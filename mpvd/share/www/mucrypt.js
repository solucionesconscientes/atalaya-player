// F4 · SHA-256 / HMAC-SHA256 / HKDF-SHA256 in plain JS. The page uses it to prove it holds the invitation token
// (challenge-response) and to derive the relay's AES-128 key, which never travels. WebCrypto would be shorter,
// but `crypto.subtle` only exists in secure contexts and the LAN room is served over plain http.
// Tested against Python's hashlib/hmac (tools: tests/test_share_hls.py, RFC 5869 vectors in test_share_rooms.py).
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.MuCrypt = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';
  var K = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
  ];

  function rotr(x, n) { return (x >>> n) | (x << (32 - n)); }

  function sha256(bytes) {
    var l = bytes.length;
    var total = (((l + 8) >>> 6) + 1) << 6;
    var m = new Uint8Array(total);
    m.set(bytes);
    m[l] = 0x80;
    var bitsHi = Math.floor(l / 0x20000000), bitsLo = (l << 3) >>> 0;
    m[total - 8] = bitsHi >>> 24; m[total - 7] = (bitsHi >>> 16) & 255;
    m[total - 6] = (bitsHi >>> 8) & 255; m[total - 5] = bitsHi & 255;
    m[total - 4] = bitsLo >>> 24; m[total - 3] = (bitsLo >>> 16) & 255;
    m[total - 2] = (bitsLo >>> 8) & 255; m[total - 1] = bitsLo & 255;
    var H = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];
    var w = new Uint32Array(64);
    for (var off = 0; off < total; off += 64) {
      var i;
      for (i = 0; i < 16; i++) {
        w[i] = (m[off + 4 * i] << 24) | (m[off + 4 * i + 1] << 16) | (m[off + 4 * i + 2] << 8) | m[off + 4 * i + 3];
      }
      for (i = 16; i < 64; i++) {
        var x = w[i - 15], y = w[i - 2];
        var s0 = rotr(x, 7) ^ rotr(x, 18) ^ (x >>> 3);
        var s1 = rotr(y, 17) ^ rotr(y, 19) ^ (y >>> 10);
        w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
      }
      var a = H[0], b = H[1], c = H[2], d = H[3], e = H[4], f = H[5], g = H[6], h = H[7];
      for (i = 0; i < 64; i++) {
        var S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
        var ch = (e & f) ^ (~e & g);
        var t1 = (h + S1 + ch + K[i] + w[i]) >>> 0;
        var S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
        var maj = (a & b) ^ (a & c) ^ (b & c);
        var t2 = (S0 + maj) >>> 0;
        h = g; g = f; f = e; e = (d + t1) >>> 0; d = c; c = b; b = a; a = (t1 + t2) >>> 0;
      }
      H[0] = (H[0] + a) >>> 0; H[1] = (H[1] + b) >>> 0; H[2] = (H[2] + c) >>> 0; H[3] = (H[3] + d) >>> 0;
      H[4] = (H[4] + e) >>> 0; H[5] = (H[5] + f) >>> 0; H[6] = (H[6] + g) >>> 0; H[7] = (H[7] + h) >>> 0;
    }
    var out = new Uint8Array(32);
    for (var j = 0; j < 8; j++) {
      out[4 * j] = H[j] >>> 24; out[4 * j + 1] = (H[j] >>> 16) & 255;
      out[4 * j + 2] = (H[j] >>> 8) & 255; out[4 * j + 3] = H[j] & 255;
    }
    return out;
  }

  function concat(parts) {
    var n = 0, i;
    for (i = 0; i < parts.length; i++) n += parts[i].length;
    var out = new Uint8Array(n), at = 0;
    for (i = 0; i < parts.length; i++) { out.set(parts[i], at); at += parts[i].length; }
    return out;
  }

  function hmacSha256(key, msg) {
    if (key.length > 64) key = sha256(key);
    var ipad = new Uint8Array(64), opad = new Uint8Array(64), i;
    for (i = 0; i < key.length; i++) { ipad[i] = key[i] ^ 0x36; opad[i] = key[i] ^ 0x5c; }
    for (i = key.length; i < 64; i++) { ipad[i] = 0x36; opad[i] = 0x5c; }
    return sha256(concat([opad, sha256(concat([ipad, msg]))]));
  }

  function hkdfSha256(ikm, salt, info, length) {
    var prk = hmacSha256(salt, ikm), out = [], block = new Uint8Array(0), counter = 1;
    while (out.length < length) {
      block = hmacSha256(prk, concat([block, info, new Uint8Array([counter])]));
      for (var i = 0; i < block.length; i++) out.push(block[i]);
      counter += 1;
    }
    return new Uint8Array(out.slice(0, length));
  }

  var B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';

  function base64url(bytes) {
    var out = '', i;
    for (i = 0; i + 2 < bytes.length; i += 3) {
      var n = (bytes[i] << 16) | (bytes[i + 1] << 8) | bytes[i + 2];
      out += B64[(n >>> 18) & 63] + B64[(n >>> 12) & 63] + B64[(n >>> 6) & 63] + B64[n & 63];
    }
    var left = bytes.length - i;
    if (left === 1) {
      var a = bytes[i] << 16;
      out += B64[(a >>> 18) & 63] + B64[(a >>> 12) & 63];
    } else if (left === 2) {
      var b = (bytes[i] << 16) | (bytes[i + 1] << 8);
      out += B64[(b >>> 18) & 63] + B64[(b >>> 12) & 63] + B64[(b >>> 6) & 63];
    }
    return out;
  }

  // UTF-8 without TextEncoder (old browsers): the token and the proof text are ASCII in practice, but keep it exact.
  function utf8(text) {
    if (typeof TextEncoder !== 'undefined') return new TextEncoder().encode(text);
    var out = [], i, c;
    for (i = 0; i < text.length; i++) {
      c = text.charCodeAt(i);
      if (c < 0x80) out.push(c);
      else if (c < 0x800) out.push(0xc0 | (c >> 6), 0x80 | (c & 63));
      else out.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63));
    }
    return new Uint8Array(out);
  }

  function hex(bytes) {
    var out = '';
    for (var i = 0; i < bytes.length; i++) out += (bytes[i] < 16 ? '0' : '') + bytes[i].toString(16);
    return out;
  }

  return {
    sha256: sha256,
    hmacSha256: hmacSha256,
    hkdfSha256: hkdfSha256,
    base64url: base64url,
    utf8: utf8,
    hex: hex,
    // Same derivation as mpvd/share/rooms.py: HKDF(token, salt=room id, info="mu-share-hls-v1") → 16 characters.
    hlsKey: function (token, roomId) {
      return base64url(hkdfSha256(utf8(token), utf8(roomId), utf8('mu-share-hls-v1'), 12));
    },
    proof: function (token, roomId, clientNonce, serverNonce) {
      return hex(hmacSha256(utf8(token), utf8('mu-share-v1|' + roomId + '|' + clientNonce + '|' + serverNonce)));
    }
  };
});
