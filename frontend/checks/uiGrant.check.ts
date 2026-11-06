/**
 * Node check of the UI-grant verifier (2026-10-24). Fixture = a grant signed by auth-service's ui_grant.sign
 * (python), as {jwk, kid, token, grant}: made by scripts/security/make_ui_grant_fixture.py (run from Q-HRM).
 *   npx esbuild checks/uiGrant.check.ts --bundle --platform=node --outfile=node_modules/.cache/uiGrant.check.cjs
 *   node node_modules/.cache/uiGrant.check.cjs fixture.json
 */
import { readFileSync } from "node:fs";
import { verifyUiGrant } from "../src/lib/uiGrant";

const fx = JSON.parse(readFileSync(process.argv[2], "utf8"));
const keys = { [fx.kid]: fx.jwk };
let failed = 0;
const check = async (name: string, ok: Promise<boolean> | boolean) => {
  const v = await ok;
  console.log(`${v ? "PASS" : "FAIL"}  ${name}`);
  if (!v) failed++;
};
(async () => {
  const g = await verifyUiGrant(fx.grant, keys, { accessToken: fx.token });
  await check("python-signed grant verifies in WebCrypto", !!g && g.role === fx.role && g.perms.length > 0);
  const [h, p, s] = fx.grant.split(".");
  const body = JSON.parse(Buffer.from(p, "base64url").toString());
  body.role = "org_admin"; body.ui_roles = ["org_admin"];
  const forged = `${h}.${Buffer.from(JSON.stringify(body)).toString("base64url")}.${s}`;
  await check("edited role refused", (await verifyUiGrant(forged, keys, { accessToken: fx.token })) === null);
  await check("unknown kid refused", (await verifyUiGrant(fx.grant, { other: fx.jwk }, { accessToken: fx.token })) === null);
  await check("other token (sub) refused", (await verifyUiGrant(fx.grant, keys, { accessToken: fx.other_token })) === null);
  await check("expired refused", (await verifyUiGrant(fx.grant, keys, { accessToken: fx.token, now: body.exp + 1 })) === null);
  await check("garbage refused", (await verifyUiGrant("a.b.c", keys, { accessToken: fx.token })) === null);
  await check("missing refused", (await verifyUiGrant(undefined, keys, { accessToken: fx.token })) === null);
  process.exit(failed ? 1 : 0);
})();
