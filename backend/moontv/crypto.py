"""`src/lib/crypto.ts` — the AES backup format, kept byte-compatible.

The `.dat` backups MoonTV wrote are not an arbitrary blob: they are

    JSON  ->  pako.deflate (raw DEFLATE)  ->  base64
          ->  CryptoJS.AES.encrypt(text, passphrase)  ->  base64 text

and `crypto.ts` is CryptoJS's default path, which is OpenSSL's: an 8-byte random
salt, `EVP_BytesToKey` with MD5 and one iteration to get a 32-byte key plus a
16-byte IV, AES-256-CBC over PKCS#7-padded UTF-8, and a `Salted__` header ahead
of the ciphertext. `crypto-js@4.2.0` is in `frontend/package.json` for the
client-side half of that (the download manager), so the two implementations have
to agree exactly — an old backup has to import into the new backend, and a
backup the new backend exports has to import back into an old one.

The helpers below are the whole pipeline, not just the cipher, because the
pipeline is where the format's three layers meet:

    encode_backup(payload, password) -> str   export side
    decode_backup(text, password)    -> str   import side (caller does json.loads)

`SimpleCrypto`'s three methods are `encrypt`, `decrypt` and `can_decrypt`.
"""

from __future__ import annotations

import base64
import hashlib
import os
import zlib
from base64 import b64decode
from cryptography.hazmat.primitives.ciphers import (
    Cipher,
    algorithms,
    modes,
)

# CryptoJS's OpenSSLKdf: EvpKDF(keySize + ivSize) with MD5, one iteration.
KEY_SIZE = 32
IV_SIZE = 16

# `WordArray.create([0x53616c74, 0x65645f5f])` — the OpenSSL header.
SALTED_HEADER = b"Salted__"

# `WordArray.random(64/8)`
SALT_SIZE = 8

DECRYPT_FAILED = "解密失败，请检查密码是否正确"

# `enc-base64.js`'s `_map`, minus the padding character.
B64_MAP = {
    char: index
    for index, char in enumerate(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    )
}


class DecryptFailed(Exception):
    """`throw new Error('解密失败，请检查密码是否正确')`."""


def evp_bytes_to_key(password: bytes, salt: bytes) -> tuple[bytes, bytes]:
    """`OpenSSLKdf.execute(password, 256/32, 128/32, salt)`.

    `EvpKDF.compute` is `MD5(previous_block || password || salt)` repeated until
    it has `keySize + ivSize` words; the first 32 bytes are the key and the next
    16 are the IV. One iteration, MD5, no stretching — the same derivation
    `openssl enc -aes-256-cbc` uses, which is why the format is called
    "OpenSSL-compatible".
    """
    derived = b""
    block = b""
    while len(derived) < KEY_SIZE + IV_SIZE:
        if block:
            block = hashlib.md5(block + password + salt).digest()
        else:
            block = hashlib.md5(password + salt).digest()
        derived += block

    return derived[:KEY_SIZE], derived[KEY_SIZE : KEY_SIZE + IV_SIZE]


def _b64_parse(text: str) -> bytes:
    """`CryptoJS.enc.Base64.parse`, which is more forgiving than `b64decode`.

    It stops at the first `=` and, for a character outside the alphabet, adds
    nothing but still advances its position (`reverseMap[code]` is `undefined`,
    and `undefined << 2` is `0`). A `.dat` file written by a browser is clean
    base64, but a file edited or wrapped by hand reaches this path too, and the
    old server did not reject it here — it failed later, at inflate.
    """
    padding = text.find("=")
    if padding != -1:
        text = text[:padding]

    out = bytearray()
    for i in range(1, len(text)):
        if i % 4 == 0:
            continue
        bits1 = B64_MAP.get(text[i - 1], 0) << ((i % 4) * 2)
        bits2 = B64_MAP.get(text[i], 0) >> (6 - (i % 4) * 2)
        out.append((bits1 | bits2) & 0xFF)
    return bytes(out)


def _cbc_decrypt_blockwise(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    return decryptor.update(ciphertext) + decryptor.finalize()


def _pkcs7_unpad(padded: bytes) -> bytes:
    """`CryptoJS.pad.Pkcs7.unpad`, with the check CryptoJS does not do.

    CryptoJS subtracts the last byte from `sigBytes` without validating it, so a
    wrong password usually leaves a negative length and an empty string, which
    `crypto.ts` turns into the same error this raises. Validating here is
    strictly better: it turns "wrong password that happens to end in a
    plausible pad" into an error instead of a payload of garbage that fails
    later, at inflate.
    """
    if not padded:
        raise DecryptFailed(DECRYPT_FAILED)

    pad = padded[-1]
    if not 1 <= pad <= 16 or pad > len(padded):
        raise DecryptFailed(DECRYPT_FAILED)
    if padded[-pad:] != bytes([pad]) * pad:
        raise DecryptFailed(DECRYPT_FAILED)

    return padded[:-pad]


def encrypt(data: str, password: str) -> str:
    """`SimpleCrypto.encrypt` — CryptoJS's `AES.encrypt(data, password).toString()`.

    The message is UTF-8 encoded (`CryptoJS` parses the JS string with
    `enc.Utf8`), padded to the AES block size, and the salt is fresh per call,
    so two exports of the same data are different ciphertexts — as they were in
    MoonTV.
    """
    salt = os.urandom(SALT_SIZE)
    key, iv = evp_bytes_to_key(password.encode("utf-8"), salt)

    plaintext = data.encode("utf-8")
    pad = 16 - (len(plaintext) % 16)  # PKCS#7 always adds 1..16 bytes
    padded = plaintext + bytes([pad]) * pad

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()

    blob = SALTED_HEADER + salt + ciphertext
    return base64.b64encode(blob).decode("ascii")


def decrypt(encrypted_data: str, password: str) -> str:
    """`SimpleCrypto.decrypt`.

    Raises `DecryptFailed` for anything CryptoJS would have returned empty for:
    no `Salted__` header (CryptoJS derives with a *random* salt in that case, so
    it never recovers the plaintext either), a wrong key, or a bad pad.
    """
    blob = _b64_parse(encrypted_data)
    if blob[:8] != SALTED_HEADER:
        raise DecryptFailed(DECRYPT_FAILED)

    salt = blob[8 : 8 + SALT_SIZE]
    ciphertext = blob[8 + SALT_SIZE :]
    if not ciphertext or len(ciphertext) % 16:
        # CryptoJS's CBC `decrypt` throws on a ciphertext that is not a whole
        # number of blocks; `crypto.ts` catches it and rethrows the same error.
        raise DecryptFailed(DECRYPT_FAILED)

    key, iv = evp_bytes_to_key(password.encode("utf-8"), salt)
    padded = _cbc_decrypt_blockwise(ciphertext, key, iv)
    plaintext = _pkcs7_unpad(padded)

    try:
        return plaintext.decode("utf-8")
    except UnicodeDecodeError:
        # `bytes.toString(CryptoJS.enc.Utf8)` produced replacement characters
        # instead of failing, and the caller then failed at base64/inflate; the
        # message the import route returns is the same either way.
        raise DecryptFailed(DECRYPT_FAILED) from None


def can_decrypt(encrypted_data: str, password: str) -> bool:
    """`SimpleCrypto.canDecrypt`."""
    try:
        return len(decrypt(encrypted_data, password)) > 0
    except DecryptFailed:
        return False


# ---------------------------------------------------------------------------
# the `.dat` pipeline (`src/app/api/admin/data_migration/{export,import}`)
# ---------------------------------------------------------------------------


def pako_deflate(data: bytes) -> bytes:
    """`pako.deflate` — raw DEFLATE, no zlib header, default level 6."""
    compressor = zlib.compressobj(6, zlib.DEFLATED, -15)
    return compressor.compress(data) + compressor.flush()


def pako_inflate(data: bytes) -> bytes:
    """`pako.inflate` — raw DEFLATE. Raises on anything that is not."""
    return zlib.decompress(data, -15)


def encode_backup(payload: str, password: str) -> str:
    """The export side: JSON -> deflate -> base64 -> AES -> base64."""
    return encrypt(base64.b64encode(pako_deflate(payload.encode("utf-8"))).decode("ascii"), password)


def decode_backup(text: str, password: str) -> str:
    """The import side, up to but not including `json.loads`.

    The base64 step is `Buffer.from(decryptedData, 'base64')`, which is
    lenient the same way CryptoJS's parser is; a payload that decrypts to
    something that is not base64-of-DEFLATE raises here, which is the old
    route's `备份文件格式错误`.
    """
    compressed = _b64_parse(decrypt(text, password))
    return pako_inflate(compressed).decode("utf-8")
