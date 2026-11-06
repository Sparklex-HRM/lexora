// Build-time: compute sha256 CSP hashes of every inline <script> in the built index.html and
// substitute them for __CSP_SCRIPT_HASHES__ in the security-headers template (stdout).
//   node nginx/csp-hash.mjs dist/index.html nginx/security-headers.conf > out.conf
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

const [html, tpl] = process.argv.slice(2);
const src = readFileSync(html, "utf8");
const hashes = [];
for (const m of src.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi)) {
  if (!m[1].trim()) continue;
  hashes.push(`'sha256-${createHash("sha256").update(m[1], "utf8").digest("base64")}'`);
}
process.stderr.write(`csp-hash: ${hashes.length} inline script(s) hashed\n`);
const out = readFileSync(tpl, "utf8")
  .split("\n").filter((l) => !l.startsWith("#")).join("\n")          // comments mention the placeholder
  .replaceAll("__CSP_SCRIPT_HASHES__", () => hashes.join(" "));
if (out.includes("__CSP_SCRIPT_HASHES__")) throw new Error("placeholder not replaced");
process.stdout.write(out);
