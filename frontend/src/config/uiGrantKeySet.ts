/**
 * Build-time pinned UI-grant public keys (go-live blocker closed 2026-10-26; docs/ops/GO_LIVE_CHECKLIST.md
 * "UI-grant signing key"). Pure functions — used by the bundle (config/uiGrantKeys.ts) AND by vite.config.ts,
 * which FAILS a production web build made with UI_GRANT_REQUIRE_KEYS=1 when this set is empty or invalid.
 *
 *   VITE_UI_GRANT_PUBKEYS='{"k2":{"kty":"EC","crv":"P-256","x":"…","y":"…"},"k1":{…}}'
 *
 * Kid rotation: the set holds the CURRENT kid and at most ONE previous kid (made by scripts/ops/gen_ui_grant_key.py
 * --previous-pubkeys …). A grant signed with either verifies; any other kid is refused. The dev kid and any known
 * development public key (under whatever kid) are never accepted here.
 * No imports on purpose: vite.config.ts loads this file before the app is built.
 */
export type PubJwk = { kty: "EC"; crv: "P-256"; x: string; y: string };

/** Local development key of Lexora HRM (scripts/security/make_ui_grant_key.py --kid dev --env, 2026-10-26). Trusted
 *  only by a dev build. Keep in step with backend/shared/utils/ui_grant_key.py DEV_PUBLIC_JWKS (production
 *  auth-service refuses to boot with any of these keys; a backend test compares the two lists). */
export const DEV_UI_GRANT_KEYS: Record<string, PubJwk> = {
  dev: { kty: "EC", crv: "P-256", x: "2edrAG1YyFVT5_a1ZEuLYljK2VJqlIJmRfvDNp_X_uw", y: "uDRPjmwYKLapQ2fJtievTh2Hnttnr5VNLMQx7kpxPuk" },
};

/** Development keys that are NO LONGER trusted anywhere but must still never be pinned in a build (the dev key
 *  inherited from the upstream fork, 2026-10-24 — its private half lives outside this repository). */
export const RETIRED_DEV_UI_GRANT_KEYS: Record<string, PubJwk> = {
  dev_2026_10_24: { kty: "EC", crv: "P-256", x: "ibt5z23uOB_ipM9wQMGwRG1BuPrdSqYoLXsm9OM_Nz4", y: "AR4QDQO1xAJC54c0ghDGnzPQLU7mUDohYHiKb66cCrk" },
};

/** Every development public key ever made (current + retired) — refused by parseUiGrantKeySet under any kid. */
export const ALL_DEV_UI_GRANT_KEYS: Record<string, PubJwk> = { ...RETIRED_DEV_UI_GRANT_KEYS, ...DEV_UI_GRANT_KEYS };

export const MAX_PINNED_KIDS = 2;                 // current + previous
const KID_RE = /^[A-Za-z0-9_-]{1,16}$/;
const B64U_32 = /^[A-Za-z0-9_-]{43}$/;              // 32-byte P-256 coordinate, base64url without padding

export type KeySetResult = { keys: Record<string, PubJwk>; errors: string[] };

/** Parse + validate the VITE_UI_GRANT_PUBKEYS JSON. `devKeys` = keys that must never be pinned. */
export function parseUiGrantKeySet(raw: string | undefined | null, devKeys: Record<string, PubJwk> = ALL_DEV_UI_GRANT_KEYS): KeySetResult {
  const keys: Record<string, PubJwk> = {};
  const errors: string[] = [];
  const text = (raw ?? "").trim();
  if (!text) return { keys, errors };
  let o: unknown;
  try {
    o = JSON.parse(text);
  } catch {
    return { keys, errors: ["VITE_UI_GRANT_PUBKEYS is not JSON"] };
  }
  if (!o || typeof o !== "object" || Array.isArray(o)) return { keys, errors: ["VITE_UI_GRANT_PUBKEYS must be {kid: JWK}"] };
  const dev = Object.values(devKeys).map((d) => `${d.x}.${d.y}`);
  for (const [kid, v] of Object.entries(o as Record<string, unknown>)) {
    const j = v as Partial<PubJwk> | null;
    if (!KID_RE.test(kid)) { errors.push(`kid "${kid.slice(0, 20)}": 1-16 letters, digits, - or _`); continue; }
    if (kid.toLowerCase() === "dev") { errors.push('kid "dev" is the local development key — never pinned in a build'); continue; }
    if (!j || j.kty !== "EC" || j.crv !== "P-256" || !B64U_32.test(String(j.x ?? "")) || !B64U_32.test(String(j.y ?? ""))) {
      errors.push(`kid "${kid}": not an EC P-256 public JWK {kty, crv, x, y}`);
      continue;
    }
    if (dev.includes(`${j.x}.${j.y}`)) { errors.push(`kid "${kid}" is a DEVELOPMENT public key`); continue; }
    keys[kid] = { kty: "EC", crv: "P-256", x: j.x as string, y: j.y as string };
  }
  if (Object.keys(keys).length > MAX_PINNED_KIDS) {
    errors.push(`${Object.keys(keys).length} kids pinned — keep the current kid + at most one previous kid`);
  }
  return { keys, errors };
}

/** What the bundle trusts: committed production keys + the build-time set (only when it is valid). */
export function pinnedKeys(
  committed: Record<string, PubJwk>, raw: string | undefined | null, devKeys: Record<string, PubJwk>,
): Record<string, PubJwk> {
  const env = parseUiGrantKeySet(raw, devKeys);
  return { ...committed, ...(env.errors.length ? {} : env.keys) };
}
