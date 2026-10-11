// Node side of the .dat format oracle: crypto-js@4.2.0 (the library upstream's
// crypto.ts used) plus node's zlib, which is pako's raw-DEFLATE at level 6.
//
// Usage: node <this-file> <spec.json> <out.json>
//
// The spec is ASCII JSON (\uXXXX escapes for anything non-ASCII), so the CJK
// never crosses this machine's transcoding I/O path; node's JSON.parse turns it
// back into the same codepoints python generated with chr().
//
// Spec keys: encrypt [{plain, pass}], decrypt [{cipher, pass}],
//            deflate [str], inflate [b64 of raw DEFLATE], b64parse [str]

const zlib = require('zlib');
const CryptoJS = require('crypto-js');
const fs = require('fs');

const spec = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));

const wordArrayHex = (wa) => {
  let out = '';
  for (let i = 0; i < wa.sigBytes; i += 1) {
    const byte = (wa.words[i >>> 2] >>> (24 - (i % 4) * 8)) & 0xff;
    out += byte.toString(16).padStart(2, '0');
  }
  return out;
};

const out = {
  encrypt: spec.encrypt.map((c) => CryptoJS.AES.encrypt(c.plain, c.pass).toString()),
  decrypt: spec.decrypt.map((c) => {
    try {
      const pt = CryptoJS.AES.decrypt(c.cipher, c.pass).toString(CryptoJS.enc.Utf8);
      return { ok: true, plain: pt };
    } catch (e) {
      return { ok: false, error: String(e.message || e) };
    }
  }),
  deflate: spec.deflate.map((s) => Buffer.from(zlib.deflateRawSync(Buffer.from(s, 'utf8'), { level: 6 })).toString('base64')),
  inflate: spec.inflate.map((b64) => {
    try {
      return zlib.inflateRawSync(Buffer.from(b64, 'base64')).toString('utf8');
    } catch (e) {
      return { error: String(e.code || e.message || e) };
    }
  }),
  b64parse: spec.b64parse.map((s) => wordArrayHex(CryptoJS.enc.Base64.parse(s))),
};

fs.writeFileSync(process.argv[3], JSON.stringify(out));
