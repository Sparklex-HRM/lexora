/**
 * obfuscateApiPaths — production-build-only Vite plugin (Lexora HRM).
 *
 * WASA "API Endpoint Disclosure in JavaScript Files" (fix 2026-10-24, port of the
 * Medfidelity HMS fix). A DevTools "search in all files" for "/payroll/",
 * "/employees/", "/auth/platform/" … used to list the whole API map from the
 * served JS. This plugin rewrites, in FIRST-PARTY source only (src/**), every
 * string literal and template-literal static part that names an API path
 * ("/<service prefix>…", prefix list below) into a call to a tiny runtime decoder:
 *
 *     api.get(`/employees/${id}/documents`)
 *  →  api.get(`${__lx$d("q8Jt…")}${id}${__lx$d("Zx0…")}`)
 *
 * Encoding = UTF-8 bytes XOR a per-build key stream, then base64. The key is
 * derived from LX_STR_SEED (default fixed) so builds are deterministic.
 *
 * HONEST SCOPE: this is obfuscation, not secrecy. The decoder and key ship in the
 * bundle, and every request is still visible in the browser Network tab. The real
 * control is server-side authorisation (gateway + per-service role checks). This
 * plugin removes the *static, greppable* endpoint map the assessor reported. The
 * pre-login bundle additionally does not contain the post-login API modules at
 * all (App.tsx lazy-loads the signed-in shell; /login uses api/publicAuth.ts).
 *
 * Not applied in `vite` dev (apply: "build"). Opt out for a debugging build with
 * LX_OBFUSCATE_API_PATHS=0. The build ends with a self-check that warns (or, with
 * LX_STR_STRICT=1, fails) if any served chunk still contains a quoted
 * "/<prefix>" literal.
 *
 * Left untouched on purpose: import / export sources, dynamic import(),
 * new URL(x, import.meta.url), tagged templates (raw text semantics), and
 * expression-statement strings (directives). Object / class keys that are API
 * paths become computed keys.
 */
import { createHash } from "node:crypto";
import path from "node:path";
import type { Plugin } from "vite";

/** First path segments of the API, derived 2026-10-24 from:
 *  - backend/api-gateway/main.py SERVICES (the gateway prefix -> service map) and
 *    its own routes (/search);
 *  - "api" (the axios baseURL, also named by bare fetch / href helpers);
 *  - the sub-resource segments that src/api/*.ts and pages/<area>/api.ts name RELATIVE
 *    to a base constant (canada.ts `${B}` + "/statutory-values", hiring public client
 *    "/careers/…") — otherwise those stay greppable.
 *  Keep in step when a new gateway prefix or base-relative module is added — the
 *  end-of-build self-check names any miss that still appears quoted, but only for
 *  prefixes in this list. */
export const API_PREFIXES = [
  // gateway SERVICES + gateway routes + axios base
  "ai", "api", "approvals", "assets", "auth", "compliance", "employees", "engage", "exit", "hiring",
  "iclock", "integrations", "leave", "letters", "masters", "notifications", "onboarding", "ops", "payroll",
  "performance", "reports", "search", "settings", "time",
  // base-relative sub-resources (`${B}${path}` in api/*.ts and pages/<area>/api.ts)
  "accounts", "ageing", "audit-log", "bank", "cost-centres", "expenses", "invoices", "journals", "notes",
  "number-series", "overview", "payments", "periods", "receipts", "setup", "statutory", "remittances",
  "stat-holiday", "accruals", "contractors", "year-end",
  // hiring public client (pages/hiring/api.ts, baseURL /api/hiring/public)
  "careers", "interview", "offer",
];

const VIRTUAL_ID = "virtual:lx-str";
const RESOLVED_ID = "\0" + VIRTUAL_ID;
const DECODER = "__lx$d";

function escapeRe(s: string) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

type Node = { type: string; start: number; end: number; [k: string]: any };

export default function obfuscateApiPaths(): Plugin {
  const longestFirst = [...API_PREFIXES].sort((a, b) => b.length - a.length).map(escapeRe).join("|");
  // "/<prefix>" ANYWHERE in the string / quasi, with no boundary check: a
  // DevTools search is a plain substring search, so "/payroll/ca/…",
  // "/employees360" and 'a[href="/employees"]' must not stay readable either.
  // Over-matching is harmless — the decoder returns the identical string.
  const PATH_RE = new RegExp(`/(?:${longestFirst})`);
  const QUOTED_RE = new RegExp(`["'\`]/(?:${longestFirst})`, "g");

  const seed = process.env.LX_STR_SEED || "lexora-hrm-str-v1";
  const key = createHash("sha256").update(seed).digest(); // 32 bytes
  const enabled = process.env.LX_OBFUSCATE_API_PATHS !== "0";
  let root = process.cwd();
  let srcDir = "";
  let encoded = 0;

  const encode = (s: string) => {
    const b = Buffer.from(s, "utf8");
    for (let i = 0; i < b.length; i++) b[i] ^= key[i % key.length] ^ ((i * 73) & 255);
    encoded++;
    return `${DECODER}(${JSON.stringify(b.toString("base64"))})`;
  };

  return {
    name: "lx-obfuscate-api-paths",
    apply: "build",
    enforce: "post",

    configResolved(c) {
      root = c.root;
      srcDir = path.resolve(root, "src").split(path.sep).join("/") + "/";
    },

    resolveId(id) {
      return id === VIRTUAL_ID ? RESOLVED_ID : null;
    },

    load(id) {
      if (id !== RESOLVED_ID) return null;
      return [
        `const K = new Uint8Array(${JSON.stringify(Array.from(key))});`,
        `const C = new Map();`,
        `const D = new TextDecoder();`,
        `export function ${DECODER}(s) {`,
        `  let v = C.get(s);`,
        `  if (v === undefined) {`,
        `    const b = atob(s), n = b.length, u = new Uint8Array(n);`,
        `    for (let i = 0; i < n; i++) u[i] = b.charCodeAt(i) ^ K[i % K.length] ^ ((i * 73) & 255);`,
        `    v = D.decode(u);`,
        `    C.set(s, v);`,
        `  }`,
        `  return v;`,
        `}`,
      ].join("\n");
    },

    transform(code, id) {
      if (!enabled) return null;
      const file = id.split("?")[0].split(path.sep).join("/");
      if (!file.startsWith(srcDir) || file.includes("/node_modules/") || !/\.[cm]?[jt]sx?$/.test(file)) return null;
      if (!PATH_RE.test(code)) return null; // fast path: nothing to do

      const ast = this.parse(code) as unknown as Node;
      const edits: { start: number; end: number; text: string }[] = [];
      const skip = new Set<Node>();

      const visit = (node: Node, parent: Node | null) => {
        if (!node || typeof node.type !== "string" || skip.has(node)) return;
        switch (node.type) {
          case "ImportDeclaration":
          case "ExportAllDeclaration":
          case "ImportExpression":
            if (node.source) skip.add(node.source);
            break;
          case "ExportNamedDeclaration":
            if (node.source) skip.add(node.source);
            break;
          case "TaggedTemplateExpression":
            skip.add(node.quasi);
            break;
          case "ExpressionStatement":
            if (node.expression?.type === "Literal") skip.add(node.expression);
            break;
          case "NewExpression":
            // new URL("./x", import.meta.url) — Vite's asset handling reads the literal.
            if (node.callee?.type === "Identifier" && node.callee.name === "URL" && node.arguments?.[0]) {
              skip.add(node.arguments[0]);
            }
            break;
          case "Literal":
            if (typeof node.value === "string" && PATH_RE.test(node.value)) {
              const isKey = parent && (parent.type === "Property" || parent.type === "MethodDefinition"
                || parent.type === "PropertyDefinition") && parent.key === node && !parent.computed;
              const call = encode(node.value);
              edits.push({ start: node.start, end: node.end, text: isKey ? `[${call}]` : call });
            }
            return;
          case "TemplateLiteral": {
            const quasis: Node[] = node.quasis;
            if (quasis.some((q) => typeof q.value?.cooked === "string" && PATH_RE.test(q.value.cooked))) {
              // Encode every static part of this template, so the segments after
              // the first ${} ("/visits") do not stay readable either.
              for (const q of quasis) {
                const cooked = q.value?.cooked;
                if (typeof cooked === "string" && cooked.length > 0) {
                  edits.push({ start: q.start, end: q.end, text: "${" + encode(cooked) + "}" });
                }
              }
            }
            break; // still visit ${expressions}
          }
        }
        for (const k in node) {
          if (k === "type" || k === "start" || k === "end") continue;
          const v = node[k];
          if (Array.isArray(v)) {
            for (const c of v) if (c && typeof c.type === "string") visit(c, node);
          } else if (v && typeof v.type === "string") {
            visit(v, node);
          }
        }
      };
      visit(ast, null);
      if (!edits.length) return null;

      edits.sort((a, b) => b.start - a.start);
      let out = code;
      for (const e of edits) out = out.slice(0, e.start) + e.text + out.slice(e.end);
      out = `import { ${DECODER} } from "${VIRTUAL_ID}";\n` + out;
      return { code: out, map: null };
    },

    generateBundle(_opts, bundle) {
      if (!enabled) return;
      const hits: string[] = [];
      for (const [name, f] of Object.entries(bundle)) {
        if (f.type !== "chunk") continue;
        const m = f.code.match(QUOTED_RE);
        if (m) hits.push(`${name}: ${m.length} (${[...new Set(m)].slice(0, 5).join(" ")})`);
      }
      this.info?.(`lx-obfuscate-api-paths: ${encoded} API path literals encoded`);
      if (hits.length) {
        const msg = `lx-obfuscate-api-paths: quoted API paths still in the bundle:\n  ${hits.join("\n  ")}`;
        if (process.env.LX_STR_STRICT === "1") this.error(msg);
        else this.warn(msg);
      }
    },
  };
}
