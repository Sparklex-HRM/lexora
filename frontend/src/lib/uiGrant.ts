/**
 * Verify the signed UI grant (HMS Entersoft "UI role tampering via login response", HIGH 7.1 — fixed 2026-10-24; the typ string below is a wire id shared with auth-service, kept as is).
 *
 * The access token is HS256 — the browser cannot check it — so the role in a login / me answer used to be trusted
 * as-is; editing it in a proxy painted another role's menus. auth-service now adds `ui_grant`, an ES256 JWS over
 * the access token's own claims + the login's live roles. The web app takes role, roles, permissions and modules
 * ONLY from a grant that
 *   - verifies (WebCrypto ECDSA P-256 / SHA-256) under a kid pinned in config/uiGrantKeys.ts (never fetched),
 *   - has typ "jethro-ui-grant" and has not expired,
 *   - names the same sub + sid as the access token it came with.
 *
 * Honest scope: whoever rewrites this JavaScript in their own browser can still draw any menu — the server
 * (gateway + service checks, security/authz) is the control and refuses every call. This closes the
 * edit-the-response path. Pure module (no app imports).
 */

export interface UiGrant {
  typ: "jethro-ui-grant";
  sub: string;
  org_id: string | null;
  sid: string | null;
  /** the login's ONE system role */
  role: string;
  /** system role + "custom:<key>" for every active custom role */
  roles: string[];
  /** system roles the UI may draw screens for: the system role + each custom role's acting role */
  ui_roles: string[];
  /** effective permission keys ("leave.view", "data.pay", …) */
  perms: string[];
  /** permissions carried by the custom roles only (which extra screens to offer) */
  custom_perms: string[];
  reach: string;
  /** subscribed modules; null = no subscription gate */
  modules: string[] | null;
  iat: number | null;
  exp: number;
}

type Jwk = { kty: string; crv: string; x: string; y: string };

function b64uToBytes(s: string): Uint8Array<ArrayBuffer> {
  if (!/^[A-Za-z0-9_-]*$/.test(s)) throw new Error("b64u");
  const b64 = s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4);
  const bin = atob(b64);
  const out = new Uint8Array(new ArrayBuffer(bin.length));
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function jsonPart(s: string): any {
  return JSON.parse(new TextDecoder().decode(b64uToBytes(s)));
}

/** Claims of a JWT WITHOUT verifying it — used only to bind a grant to its access token (sub / sid). */
export function peekJwt(token: string | null | undefined): { sub?: string; sid?: string | null } | null {
  try {
    const p = String(token || "").split(".");
    if (p.length !== 3) return null;
    const c = jsonPart(p[1]);
    return c && typeof c === "object" ? c : null;
  } catch {
    return null;
  }
}

const keyCache = new Map<string, Promise<CryptoKey>>();

function importKey(kid: string, jwk: Jwk): Promise<CryptoKey> {
  const ck = `${kid}:${jwk.x}:${jwk.y}`;
  let p = keyCache.get(ck);
  if (!p) {
    p = crypto.subtle.importKey("jwk", { kty: "EC", crv: "P-256", x: jwk.x, y: jwk.y, ext: true },
      { name: "ECDSA", namedCurve: "P-256" }, false, ["verify"]);
    keyCache.set(ck, p);
  }
  return p;
}

const strList = (v: unknown): v is string[] => Array.isArray(v) && v.every((x) => typeof x === "string");

/** The verified grant, or null for anything missing / malformed / forged / expired / bound to another token.
 *  Never throws. */
export async function verifyUiGrant(
  jws: unknown, keys: Readonly<Record<string, Jwk>>, expect: { accessToken: string; now?: number },
): Promise<UiGrant | null> {
  try {
    if (typeof jws !== "string" || jws.length > 32768) return null;
    const parts = jws.split(".");
    if (parts.length !== 3) return null;
    const [h, p, s] = parts;
    const header = jsonPart(h);
    if (!header || header.alg !== "ES256" || typeof header.kid !== "string") return null;
    if (!Object.prototype.hasOwnProperty.call(keys, header.kid)) return null;
    const sig = b64uToBytes(s);
    if (sig.length !== 64) return null;
    const key = await importKey(header.kid, keys[header.kid]);
    const ok = await crypto.subtle.verify({ name: "ECDSA", hash: "SHA-256" }, key, sig,
      new TextEncoder().encode(`${h}.${p}`));
    if (!ok) return null;
    const c = jsonPart(p);
    if (!c || c.typ !== "jethro-ui-grant") return null;
    if (typeof c.sub !== "string" || !c.sub || typeof c.role !== "string" || !c.role) return null;
    if (typeof c.exp !== "number") return null;
    const now = expect.now ?? Math.floor(Date.now() / 1000);
    if (c.exp <= now) return null;
    if (c.modules != null && !strList(c.modules)) return null;
    if (!strList(c.perms ?? []) || !strList(c.roles ?? []) || !strList(c.ui_roles ?? [])
      || !strList(c.custom_perms ?? [])) return null;
    // Bound to THIS access token: same user, same session.
    const tok = peekJwt(expect.accessToken);
    if (!tok || String(tok.sub ?? "") !== c.sub) return null;
    if ((tok.sid ?? null) !== (c.sid ?? null)) return null;
    const uiRoles: string[] = (c.ui_roles && c.ui_roles.length ? c.ui_roles : [c.role]);
    return {
      typ: "jethro-ui-grant", sub: c.sub, role: c.role, exp: c.exp,
      org_id: c.org_id != null ? String(c.org_id) : null,
      sid: c.sid ?? null,
      roles: c.roles ?? [c.role],
      ui_roles: uiRoles.includes(c.role) ? uiRoles : [c.role, ...uiRoles],
      perms: c.perms ?? [],
      custom_perms: c.custom_perms ?? [],
      reach: typeof c.reach === "string" ? c.reach : "own",
      modules: c.modules ?? null,
      iat: typeof c.iat === "number" ? c.iat : null,
    };
  } catch {
    return null;
  }
}
