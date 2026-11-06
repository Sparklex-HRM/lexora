/** Human message from an axios error (FastAPI detail may be a string or an object).
 *  Lives here (not in api/saas.ts, which re-exports it) so the pre-login code can use it
 *  without pulling an API module into the /login entry chunk (WASA, 2026-10-24). */
export function errText(e: any, fallback = "Something went wrong"): string {
  const d = e?.response?.data;
  if (!d) return fallback;
  if (typeof d.detail === "string") return d.message && d.detail.match(/^[a-z_]+$/) ? d.message : d.ref ? `${d.detail} (ref ${d.ref})` : d.detail;
  if (d.detail?.message) return d.detail.message;
  return d.message || fallback;
}
