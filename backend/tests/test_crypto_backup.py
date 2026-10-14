"""`src/lib/crypto.ts` and the `.dat` backup pipeline.

The interesting claim is not "encrypt then decrypt returns the input" but that
the bytes we write are readable by `openssl enc -aes-256-cbc`, i.e. the salt
header, the EVP_BytesToKey derivation and the PKCS#7 padding are the OpenSSL
ones upstream's CryptoJS produced.
"""

from __future__ import annotations

import base64
import subprocess
import zlib

import pytest

from moontv.crypto import (
    DecryptFailed,
    decode_backup,
    encrypt,
    evp_bytes_to_key,
    pako_deflate,
    pako_inflate,
    encode_backup,
    decrypt,
    can_decrypt,
)

PAYLOADS = [
    "hello",
    "héllo 世界",
    '{"a":1,"b":[2,3]}',
    "x" * 16,  # exactly one block -> a full 16-byte pad
    "",
]


@pytest.mark.parametrize("data", PAYLOADS)
def test_round_trip(data):
    assert decrypt(encrypt(data, "s3cret"), "s3cret") == data


def test_wrong_password_raises():
    text = encrypt("hello", "s3cret")
    with pytest.raises(DecryptFailed):
        decrypt(text, "wrong")
    assert can_decrypt(text, "s3cret") is True
    assert can_decrypt(text, "wrong") is False


def test_salt_is_fresh_per_call():
    assert encrypt("same", "pw") != encrypt("same", "pw")


def test_openssl_reads_what_we_write():
    """The `.dat` a user downloads must open with a stock OpenSSL.

    `-md md5` is explicit because OpenSSL 3 defaults its deprecated `EVP_BytesToKey`
    to SHA-256, while CryptoJS's `OpenSSLKdf` is the MD5/one-iteration path.
    """
    plaintext = "MoonTVPlus backup payload 世界"
    text = encrypt(plaintext, "s3cret")
    proc = subprocess.run(
        [
            "openssl",
            "enc",
            "-d",
            "-base64",
            "-aes-256-cbc",
            "-md",
            "md5",
            "-pass",
            "pass:s3cret",
        ],
        input=(text + "\n").encode("ascii"),
        capture_output=True,
        check=True,
    )
    assert proc.stdout.decode("utf-8") == plaintext


def test_we_read_what_openssl_writes():
    plaintext = "MoonTVPlus backup payload 世界"
    proc = subprocess.run(
        [
            "openssl",
            "enc",
            "-base64",
            "-aes-256-cbc",
            "-md",
            "md5",
            "-pass",
            "pass:s3cret",
        ],
        input=plaintext.encode("utf-8"),
        capture_output=True,
        check=True,
    )
    # CryptoJS's base64 parser is not whitespace-tolerant, so the wrapped output
    # is flattened before it reaches the port — the same thing the browser does
    # when it reads a downloaded file into a string.
    assert decrypt(proc.stdout.decode("ascii").replace("\n", ""), "s3cret") == plaintext


def test_key_derivation_matches_openssl():
    """EVP_BytesToKey, MD5, one iteration: key = first 32 bytes, IV = next 16."""
    import hashlib

    password, salt = b"s3cret", bytes(range(8))
    first = hashlib.md5(password + salt).digest()
    second = hashlib.md5(first + password + salt).digest()
    third = hashlib.md5(second + password + salt).digest()
    assert evp_bytes_to_key(password, salt) == (first + second, third)


def test_pako_deflate_is_raw_deflate():
    payload = b"the quick brown fox" * 50
    compressor = zlib.compressobj(6, zlib.DEFLATED, -15)
    assert pako_deflate(payload) == compressor.compress(payload) + compressor.flush()
    assert pako_inflate(pako_deflate(payload)) == payload
    # A zlib-wrapped stream is not what pako produced, and pako would not accept it.
    with pytest.raises(zlib.error):
        pako_inflate(zlib.compress(payload))


def test_backup_pipeline_is_three_layers():
    """`encode_backup` deflates, base64s, then AES — so the middle layer is
    base64(raw DEFLATE), not the DEFLATE bytes themselves."""
    payload = '{"users":[{"username":"a"}]}'
    text = encode_backup(payload, "pw")
    middle = decrypt(text, "pw")
    assert base64.b64decode(middle) == pako_deflate(payload.encode("utf-8"))
    assert decode_backup(text, "pw") == payload


def test_backup_with_a_wrong_password_is_rejected_not_garbled():
    text = encode_backup('{"a":1}', "pw")
    with pytest.raises(DecryptFailed):
        decode_backup(text, "nope")
