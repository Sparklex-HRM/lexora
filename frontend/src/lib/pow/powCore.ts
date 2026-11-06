/**
 * Proof-of-work CAPTCHA core (WASA hardening 2026-10-21, ported from
 * the Medfidelity HMS) — no DOM, no imports, so the Web Worker and the main
 * thread fallback run exactly the same code.
 *
 * Find the smallest whole number n >= start such that
 * SHA-256(salt + String(n)) begins with `difficulty` zero bits.
 * Server side: backend/services/auth/pow_captcha.py (same rule).
 *
 * A hand-rolled SHA-256 instead of crypto.subtle: subtle.digest is async and
 * costs a promise per hash, ~10x slower for millions of tiny inputs.
 */

const K = new Int32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

const W = new Int32Array(64);

/** First 64 bits of SHA-256(msg[0..len)) as [h0, h1] (all a check needs). */
function sha256Head(msg: Uint8Array, len: number, out: Int32Array): void {
  // pad in place: msg has room for it (caller allocates 128 bytes)
  const total = len + 9 <= 64 ? 64 : 128;
  msg[len] = 0x80;
  for (let i = len + 1; i < total; i++) msg[i] = 0;
  const bits = len * 8;
  msg[total - 4] = (bits >>> 24) & 0xff;
  msg[total - 3] = (bits >>> 16) & 0xff;
  msg[total - 2] = (bits >>> 8) & 0xff;
  msg[total - 1] = bits & 0xff;

  let h0 = 0x6a09e667 | 0, h1 = 0xbb67ae85 | 0, h2 = 0x3c6ef372 | 0, h3 = 0xa54ff53a | 0;
  let h4 = 0x510e527f | 0, h5 = 0x9b05688c | 0, h6 = 0x1f83d9ab | 0, h7 = 0x5be0cd19 | 0;
  for (let off = 0; off < total; off += 64) {
    for (let i = 0; i < 16; i++) {
      const j = off + i * 4;
      W[i] = (msg[j] << 24) | (msg[j + 1] << 16) | (msg[j + 2] << 8) | msg[j + 3];
    }
    for (let i = 16; i < 64; i++) {
      const x = W[i - 15], y = W[i - 2];
      const s0 = ((x >>> 7) | (x << 25)) ^ ((x >>> 18) | (x << 14)) ^ (x >>> 3);
      const s1 = ((y >>> 17) | (y << 15)) ^ ((y >>> 19) | (y << 13)) ^ (y >>> 10);
      W[i] = (W[i - 16] + s0 + W[i - 7] + s1) | 0;
    }
    let a = h0, b = h1, c = h2, d = h3, e = h4, f = h5, g = h6, h = h7;
    for (let i = 0; i < 64; i++) {
      const S1 = ((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7));
      const ch = (e & f) ^ (~e & g);
      const t1 = (h + S1 + ch + K[i] + W[i]) | 0;
      const S0 = ((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10));
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const t2 = (S0 + maj) | 0;
      h = g; g = f; f = e; e = (d + t1) | 0;
      d = c; c = b; b = a; a = (t1 + t2) | 0;
    }
    h0 = (h0 + a) | 0; h1 = (h1 + b) | 0; h2 = (h2 + c) | 0; h3 = (h3 + d) | 0;
    h4 = (h4 + e) | 0; h5 = (h5 + f) | 0; h6 = (h6 + g) | 0; h7 = (h7 + h) | 0;
  }
  out[0] = h0;
  out[1] = h1;
}

function meetsHead(h0: number, h1: number, difficulty: number): boolean {
  if (difficulty <= 0) return true;
  if (difficulty <= 32) return (h0 >>> (32 - difficulty)) === 0;
  if (h0 !== 0) return false;
  return difficulty >= 64 ? h1 === 0 : (h1 >>> (64 - difficulty)) === 0;
}

/**
 * Search n in [start, end). Returns the first solution or -1.
 * Pure and synchronous: callers chunk it (worker loop / setTimeout).
 */
export function searchPow(salt: string, difficulty: number, start: number, end: number): number {
  if (salt.length > 100) throw new Error("salt too long");
  const buf = new Uint8Array(128 + 32);
  let p = 0;
  for (let i = 0; i < salt.length; i++) buf[p++] = salt.charCodeAt(i) & 0xff;
  const base = p;
  const out = new Int32Array(2);
  for (let n = start; n < end; n++) {
    const s = String(n);
    let q = base;
    for (let i = 0; i < s.length; i++) buf[q++] = s.charCodeAt(i);
    sha256Head(buf, q, out);
    if (meetsHead(out[0], out[1], difficulty)) return n;
  }
  return -1;
}

/** Full SHA-256 hex, for tests that compare with the server. */
export function sha256HexForTest(text: string): string {
  // Only used in tests: tiny inputs, so a straightforward implementation.
  const bytes = new TextEncoder().encode(text);
  const l = bytes.length;
  const total = Math.ceil((l + 9) / 64) * 64;
  const m = new Uint8Array(total);
  m.set(bytes);
  m[l] = 0x80;
  const bits = l * 8;
  m[total - 4] = (bits >>> 24) & 0xff; m[total - 3] = (bits >>> 16) & 0xff;
  m[total - 2] = (bits >>> 8) & 0xff; m[total - 1] = bits & 0xff;
  const H = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19].map((x) => x | 0);
  const w = new Int32Array(64);
  for (let off = 0; off < total; off += 64) {
    for (let i = 0; i < 16; i++) { const j = off + i * 4; w[i] = (m[j] << 24) | (m[j + 1] << 16) | (m[j + 2] << 8) | m[j + 3]; }
    for (let i = 16; i < 64; i++) {
      const x = w[i - 15], y = w[i - 2];
      w[i] = (w[i - 16] + (((x >>> 7) | (x << 25)) ^ ((x >>> 18) | (x << 14)) ^ (x >>> 3)) + w[i - 7]
        + (((y >>> 17) | (y << 15)) ^ ((y >>> 19) | (y << 13)) ^ (y >>> 10))) | 0;
    }
    let [a, b, c, d, e, f, g, h] = H;
    for (let i = 0; i < 64; i++) {
      const t1 = (h + (((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7)))
        + ((e & f) ^ (~e & g)) + K[i] + w[i]) | 0;
      const t2 = ((((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10)))
        + ((a & b) ^ (a & c) ^ (b & c))) | 0;
      h = g; g = f; f = e; e = (d + t1) | 0; d = c; c = b; b = a; a = (t1 + t2) | 0;
    }
    H[0] = (H[0] + a) | 0; H[1] = (H[1] + b) | 0; H[2] = (H[2] + c) | 0; H[3] = (H[3] + d) | 0;
    H[4] = (H[4] + e) | 0; H[5] = (H[5] + f) | 0; H[6] = (H[6] + g) | 0; H[7] = (H[7] + h) | 0;
  }
  return H.map((x) => (x >>> 0).toString(16).padStart(8, "0")).join("");
}

export interface PowChallenge {
  algorithm: string;
  salt: string;
  difficulty: number;
  expires: number;
  purpose: string;
  signature: string;
  /** Issued for the visible "I'm not a robot" box (inside the HMAC). */
  interactive?: boolean;
}

/** base64url(JSON(signed challenge fields + number)) — what the server reads as
 *  captcha_token. Only the signed fields travel (the /auth/challenge reply also
 *  says enabled / mode); `interactive` is kept when the challenge had it. */
export function encodePowToken(ch: PowChallenge, number: number): string {
  const body: Record<string, unknown> = {
    algorithm: ch.algorithm, salt: ch.salt, difficulty: ch.difficulty,
    expires: ch.expires, purpose: ch.purpose, signature: ch.signature,
  };
  if (ch.interactive === true) body.interactive = true;
  body.number = number;
  const json = JSON.stringify(body);
  const b64 = btoa(json);            // every field is ASCII
  return b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
