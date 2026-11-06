import axios from "axios";
import { useAuthStore } from "../store/authStore";

// Same shape as the Medfidelity HMS client: browser hits a same-origin `/api`
// prefix, Vite proxies it to the gateway. The gateway is the only JWT verifier.
const api = axios.create({ baseURL: import.meta.env.VITE_API_BASE || "/api" });

api.interceptors.request.use((config) => {
  const { token } = useAuthStore.getState();
  if (token) config.headers.Authorization = `Bearer ${token}`;
  // Branch switcher: org-scope users request a specific branch view. The gateway
  // honours this only for org-scope logins; branch users are locked server-side.
  try {
    const b = localStorage.getItem("lx-branch");
    if (b) config.headers["X-View-Branch"] = b;
    // D4: entity picker — services narrow lists / files to this legal entity ("" = all).
    const en = localStorage.getItem("lx-entity");
    if (en) config.headers["X-View-Entity"] = en;
  } catch { /* ignore */ }
  return config;
});

// HMS "first-call 500 then OK on retry" mitigation: idempotent reads (GET/HEAD)
// silently retry once on a 5xx (cold pool / cold pod). Writes are never retried.
const RETRYABLE = new Set([500, 502, 503, 504]);

api.interceptors.response.use(
  (res) => res,
  async (err) => {
    const cfg = err.config || {};
    const method = (cfg.method || "get").toLowerCase();
    const status = err.response?.status;

    // No refresh endpoint yet — a 401 means the session is gone. Drop it.
    if (status === 401) {
      useAuthStore.getState().logout();
      if (!window.location.pathname.startsWith("/login")) {
        window.location.href = "/login";
      }
      return Promise.reject(err);
    }

    // S4: read-only subscription (402) / plan limit — show the plain sentence, not the code.
    if (status === 402 && err.response?.data) {
      const d = err.response.data;
      if (d.detail === "subscription_read_only" && d.message) d.detail = d.message;
      else if (d.detail && typeof d.detail === "object" && d.detail.message) d.detail = d.detail.message;
    }

    if ((method === "get" || method === "head") && RETRYABLE.has(status) && !cfg.__retried) {
      cfg.__retried = true;
      await new Promise((r) => setTimeout(r, 250));
      return api.request(cfg);
    }
    return Promise.reject(err);
  }
);

export default api;
