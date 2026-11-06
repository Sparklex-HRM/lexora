/**
 * Public keys that sign the `ui_grant` (HMS Entersoft finding "UI role tampering via login response", HIGH 7.1 —
 * fix 2026-10-24; backend/services/auth/ui_grant.py, docs/security/UI_GRANT.md).
 *
 * PINNED IN THE BUNDLE ON PURPOSE and never fetched from the API: someone who can rewrite API answers could
 * otherwise hand the page their own key. Public keys are not secrets.
 *
 * PRODUCTION: the key pair is made at deploy time (scripts/ops/gen_ui_grant_key.py --kid k1). Pin its public
 * half at build time (web image: --build-arg VITE_UI_GRANT_PUBKEYS=… UI_GRANT_REQUIRE_KEYS=1 — the build then
 * FAILS without a valid production key) with
 *     VITE_UI_GRANT_PUBKEYS='{"k1":{"kty":"EC","crv":"P-256","x":"…","y":"…"}}'
 * or commit it in PROD_KEYS. Rules + parser: ./uiGrantKeySet.ts (current kid + at most ONE previous kid; never dev).
 * Rotation: gen_ui_grant_key.py --kid k2 --previous-pubkeys <current set> -> deploy the web app (accepts k1 + k2)
 * -> switch auth-service LEXORA_UI_GRANT_KID / LEXORA_UI_GRANT_PRIVATE_KEY -> later release: drop k1.
 *
 * DEV: the local key (kid "dev", private half only in the git-ignored Lexora-HRM/.env + secrets/) is trusted ONLY by the
 * vite dev server, or by a build made with VITE_UI_GRANT_TRUST_DEV=1 (local `vite preview` only). auth-service
 * refuses to sign with kid "dev" when LEXORA_ENV=production.
 */
import { ALL_DEV_UI_GRANT_KEYS, DEV_UI_GRANT_KEYS, pinnedKeys, type PubJwk } from "./uiGrantKeySet";

export type { PubJwk };

/** Production key(s). None committed yet: pass VITE_UI_GRANT_PUBKEYS at build time (docs/security/UI_GRANT.md). */
const PROD_KEYS: Record<string, PubJwk> = {};

/** Local development key(s) — defined in ./uiGrantKeySet.ts (vite.config.ts needs them too). */
export const DEV_KEYS: Record<string, PubJwk> = DEV_UI_GRANT_KEYS;

const TRUST_DEV = import.meta.env.DEV || import.meta.env.VITE_UI_GRANT_TRUST_DEV === "1";

export const UI_GRANT_KEYS: Readonly<Record<string, PubJwk>> = Object.freeze({
  ...pinnedKeys(PROD_KEYS, import.meta.env.VITE_UI_GRANT_PUBKEYS as string | undefined, ALL_DEV_UI_GRANT_KEYS),
  ...(TRUST_DEV ? DEV_KEYS : {}),
});
