import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
// WASA "API endpoint disclosure in JS" (2026-10-24): production builds only — encodes API
// path literals in first-party code (the plugin is also apply: "build", never in `vite` dev).
import obfuscateApiPaths from "./vite-plugins/obfuscateApiPaths";
// Go-live 2026-10-26: production UI-grant public key(s) pinned at BUILD time (current + previous kid).
import { parseUiGrantKeySet } from "./src/config/uiGrantKeySet";

/** Production builds: an invalid VITE_UI_GRANT_PUBKEYS always fails the build; UI_GRANT_REQUIRE_KEYS=1 (the web
 *  image for customers) also fails it when no production key is pinned — such a bundle could sign nobody in. */
function checkUiGrantKeys(env: Record<string, string>): void {
  const { keys, errors } = parseUiGrantKeySet(env.VITE_UI_GRANT_PUBKEYS);
  const kids = Object.keys(keys);
  if (errors.length) throw new Error("VITE_UI_GRANT_PUBKEYS refused: " + errors.join("; "));
  if (!kids.length && env.UI_GRANT_REQUIRE_KEYS === "1") {
    throw new Error("UI_GRANT_REQUIRE_KEYS=1 but VITE_UI_GRANT_PUBKEYS pins no production key (scripts/ops/gen_ui_grant_key.py)");
  }
  if (env.UI_GRANT_REQUIRE_KEYS === "1" && env.VITE_UI_GRANT_TRUST_DEV === "1") {
    throw new Error("VITE_UI_GRANT_TRUST_DEV=1 is refused in a customer build (UI_GRANT_REQUIRE_KEYS=1)");
  }
  console.log(kids.length ? `[ui-grant] pinned kid(s): ${kids.join(", ")}`
    : "[ui-grant] no production key pinned (dev / CI build) — set VITE_UI_GRANT_PUBKEYS for a customer build");
}

// Same pattern as the Medfidelity HMS frontend: the browser calls a same-origin
// `/api` prefix, and Vite proxies it to the Lexora HRM API gateway (:8200). No CORS in dev.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  if (mode === "production") checkUiGrantKeys(env);
  return {
    plugins: [react(), ...(mode === "production" ? [obfuscateApiPaths()] : [])],
    server: {
      host: true,          // expose on the LAN (0.0.0.0) so 192.168.x works
      port: Number(env.LEXORA_WEB_PORT) || 3200,   // 3000 and 3100 are taken by other local apps on this machine
      proxy: {
        "/api": {
          target: env.VITE_API_URL || "http://localhost:8200",
          rewrite: (p) => p.replace(/^\/api/, ""),
          changeOrigin: true,
        },
      },
    },
    // `vite preview` (the production build on :4173) proxies /api the same way, so the installed-app
    // shell + service worker can be checked against real hashed assets (dev mode has none to cache).
    preview: {
      port: 4173,
      proxy: {
        "/api": {
          target: env.VITE_API_URL || "http://localhost:8200",
          rewrite: (p) => p.replace(/^\/api/, ""),
          changeOrigin: true,
        },
      },
    },
    build: {
      sourcemap: mode !== "production",
      rollupOptions: {
        output: {
          manualChunks: {
            vendor: ["react", "react-dom", "react-router-dom"],
            query: ["@tanstack/react-query"],
            charts: ["recharts"],
          },
        },
      },
    },
  };
});
