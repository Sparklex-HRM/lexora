// Checks the build-time pinned UI-grant key set (go-live 2026-10-26): src/config/uiGrantKeySet.ts parser rules and
// KID ROTATION end to end in WebCrypto — a grant signed with the current kid AND one signed with the previous kid
// verify against the pinned set (src/lib/uiGrant.ts verifyUiGrant); any other kid / the dev key are refused.
//   node frontend/scripts/check_ui_grant_keys.mjs        (Node >= 22.18 / 23.6 runs the .ts directly)
import assert from "node:assert/strict";
import { readFileSync, mkdtempSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = join(HERE, "..", "src");

async function load(rel) {
  try {
    return await import(pathToFileURL(join(SRC, rel)).href);           // Node with type stripping
  } catch {                                                            // older Node: transpile with the project's tsc
    const ts = (await import("typescript")).default;
    const out = ts.transpileModule(readFileSync(join(SRC, rel), "utf8"),
      { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 } }).outputText;
    const f = join(mkdtempSync(join(tmpdir(), "uigk-")), rel.replace(/[\\/]/g, "_").replace(/\.ts$/, ".mjs"));
    writeFileSync(f, out);
    return import(pathToFileURL(f).href);
  }
}

const KS = await load("config/uiGrantKeySet.ts");
const UG = await load("lib/uiGrant.ts");
let n = 0;
const ok = (v, msg) => { assert.ok(v, msg); n++; };

const b64u = (buf) => Buffer.from(buf).toString("base64url");
async function keypair() {
  const k = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, true, ["sign", "verify"]);
  const j = await crypto.subtle.exportKey("jwk", k.publicKey);
  return { priv: k.privateKey, jwk: { kty: "EC", crv: "P-256", x: j.x, y: j.y } };
}
const access = (sub, sid) => `${b64u(JSON.stringify({ alg: "HS256" }))}.${b64u(JSON.stringify({ sub, sid }))}.sig`;
async function grant(priv, kid, claims) {
  const h = b64u(JSON.stringify({ alg: "ES256", kid, typ: "jethro-ui-grant" }));
  const p = b64u(JSON.stringify(claims));
  const sig = await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, priv, new TextEncoder().encode(`${h}.${p}`));
  return `${h}.${p}.${b64u(sig)}`;                                     // WebCrypto ECDSA = raw r||s (64 bytes) = JWS
}

const k1 = await keypair(), k2 = await keypair(), k0 = await keypair();
const DEV = KS.DEV_UI_GRANT_KEYS.dev;

// 1) parser rules
ok(Object.keys(KS.parseUiGrantKeySet("").keys).length === 0 && KS.parseUiGrantKeySet("").errors.length === 0, "empty = no keys, no error");
ok(KS.parseUiGrantKeySet("{nope").errors.length === 1, "bad JSON refused");
ok(KS.parseUiGrantKeySet("[]").errors.length === 1, "array refused");
const both = KS.parseUiGrantKeySet(JSON.stringify({ k2: k2.jwk, k1: k1.jwk }));
ok(both.errors.length === 0 && Object.keys(both.keys).sort().join() === "k1,k2", "current + previous accepted");
ok(KS.parseUiGrantKeySet(JSON.stringify({ k2: k2.jwk, k1: k1.jwk, k0: k0.jwk })).errors.some((e) => e.includes("at most one previous")), "3 kids refused");
ok(KS.parseUiGrantKeySet(JSON.stringify({ dev: k1.jwk })).errors.some((e) => e.includes("dev")), "kid dev refused");
ok(KS.parseUiGrantKeySet(JSON.stringify({ k9: DEV })).errors.some((e) => e.includes("DEVELOPMENT")), "dev public key under another kid refused");
for (const old of Object.values(KS.RETIRED_DEV_UI_GRANT_KEYS)) ok(KS.parseUiGrantKeySet(JSON.stringify({ k8: old })).errors.some((e) => e.includes("DEVELOPMENT")), "retired dev public key refused");
ok(KS.parseUiGrantKeySet(JSON.stringify({ k1: { ...k1.jwk, crv: "P-384" } })).errors.length === 1, "non P-256 refused");
ok(KS.parseUiGrantKeySet(JSON.stringify({ k1: { ...k1.jwk, x: "short" } })).errors.length === 1, "bad coordinate refused");
ok(KS.parseUiGrantKeySet(JSON.stringify({ "bad kid!": k1.jwk })).errors.length === 1, "bad kid refused");
// an invalid set pins NOTHING from the env (fail closed); committed keys stay
ok(Object.keys(KS.pinnedKeys({}, JSON.stringify({ k1: k1.jwk, dev: DEV }), KS.DEV_UI_GRANT_KEYS)).length === 0, "invalid set pins nothing");
ok(Object.keys(KS.pinnedKeys({ c1: k0.jwk }, "", KS.DEV_UI_GRANT_KEYS)).join() === "c1", "committed keys kept");

// 2) rotation end to end: the bundle trusts the pinned set only
const pinned = KS.pinnedKeys({}, JSON.stringify({ k2: k2.jwk, k1: k1.jwk }), KS.DEV_UI_GRANT_KEYS);
const tok = access("u-1", "s-1");
const claims = { typ: "jethro-ui-grant", sub: "u-1", sid: "s-1", role: "hr_admin", roles: ["hr_admin"], ui_roles: ["hr_admin"],
  perms: ["people.view"], custom_perms: [], exp: Math.floor(Date.now() / 1000) + 600 };
ok((await UG.verifyUiGrant(await grant(k2.priv, "k2", claims), pinned, { accessToken: tok }))?.role === "hr_admin", "current kid k2 verifies");
ok((await UG.verifyUiGrant(await grant(k1.priv, "k1", claims), pinned, { accessToken: tok }))?.role === "hr_admin", "previous kid k1 still verifies");
ok((await UG.verifyUiGrant(await grant(k0.priv, "k0", claims), pinned, { accessToken: tok })) === null, "retired / unknown kid k0 refused");
ok((await UG.verifyUiGrant(await grant(k0.priv, "k1", claims), pinned, { accessToken: tok })) === null, "k1 header signed by another key refused");
const afterDrop = KS.pinnedKeys({}, JSON.stringify({ k2: k2.jwk }), KS.DEV_UI_GRANT_KEYS);
ok((await UG.verifyUiGrant(await grant(k1.priv, "k1", claims), afterDrop, { accessToken: tok })) === null, "k1 refused once dropped from the build");
ok(!("dev" in pinned), "dev key never pinned by the env set");

console.log(`ui-grant key set: ${n} checks passed`);
