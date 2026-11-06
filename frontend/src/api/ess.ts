import api from "./client";

/** 2026-10-16 employee portal — self-service API (attendance month, corrections, permission,
 *  statements, leave extras, optional holidays, comp-off, feed, requests, documents, tasks …).
 *  Every list is a dense row array for DataGrid. */

const q = (params: Record<string, any>) =>
  Object.fromEntries(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ""));

// ── "Show salary": masked by default, remembered for THIS browser session only ──
// sessionStorage (never localStorage): closing the tab/browser masks salary again.
const SALARY_KEY = "lx-show-salary";
export function salaryShown(): boolean {
  try { return sessionStorage.getItem(SALARY_KEY) === "1"; } catch { return false; }
}
export function setSalaryShown(v: boolean) {
  try { if (v) sessionStorage.setItem(SALARY_KEY, "1"); else sessionStorage.removeItem(SALARY_KEY); } catch { /* private mode */ }
  window.dispatchEvent(new Event("lx-salary-visibility"));
}
export const MASK = "$ ••••••";

// ── leave totals: ONE rule for every screen ─────────────────────────────────
/** Days the employee can actually take = usable types only (event leaves such as maternity /
 *  paternity are shown separately, never summed). Uses the API's totals when present. */
export function usableLeave(bal: any): number {
  if (!bal) return 0;
  if (bal.totals && typeof bal.totals.usable_remaining === "number") return bal.totals.usable_remaining;
  return (bal.balances || []).filter((b: any) => b.paid && (b.category || "usable") === "usable")
    .reduce((s: number, b: any) => s + (Number(b.remaining) || 0), 0);
}
export function eventLeave(bal: any): { code: string; name: string; remaining: number; entitled: number }[] {
  if (bal?.totals?.event) return bal.totals.event;
  return (bal?.balances || []).filter((b: any) => b.category === "event")
    .map((b: any) => ({ code: b.leave_type_code, name: b.leave_type_name, remaining: b.remaining, entitled: b.entitled }));
}

// ── attendance (time service) ───────────────────────────────────────────────
export interface AttDay {
  date: string; weekday: string; code: string; status: string; planned_hours: number; hours: number;
  first_in: string | null; last_out: string | null; late_minutes: number; holiday: string | null;
  leave: { type: string; code: string; status: string; half: boolean; session?: string | null } | null;
  shift: { name: string; start: string; end: string } | null; exceptions: string[];
  correction: { id: string; status: string; reason: string } | null;
  permission: { id: string; status: string; from_time: string; to_time: string; hours: number } | null; future: boolean;
}
export interface AttMonth {
  employee: { id: string; name: string; employee_number: string }; month: string; start: string; end: string; today: string;
  shift: { name: string; start: string; end: string }; days: AttDay[];
  kpis: { avg_hours: number; days_present: number; late_days: number; exception_days: number; leave_days: number; absent_days: number; holidays: number };
}
export const essTimeApi = {
  month: (month?: string, employee_id?: string) =>
    api.get<AttMonth>("/time/me/attendance", { params: q({ month, employee_id }) }).then((r) => r.data),
  requests: () => api.get<{ corrections: any[]; permissions: any[] }>("/time/me/requests").then((r) => r.data),
};

// ── permission (short hours) — time service ────────────────────────────────
export interface PermissionRow { id: string; employee_id: string; employee_name: string; work_date: string; from_time: string; to_time: string; hours: number; reason: string; status: string; review_note?: string | null; created_at?: string | null; }
export const permissionApi = {
  policy: () => api.get<{ max_per_month: number; max_hours: number; used_this_month: number; source: string }>("/time/permissions/policy").then((r) => r.data),
  setPolicy: (body: { max_per_month: number; max_hours: number }) => api.put("/time/permissions/policy", body).then((r) => r.data),
  create: (body: { work_date: string; from_time: string; to_time: string; reason: string }) => api.post<PermissionRow>("/time/permissions", body).then((r) => r.data),
  list: (status?: string) => api.get<PermissionRow[]>("/time/permissions", { params: q({ status }) }).then((r) => r.data),
  cancel: (id: string) => api.post(`/time/permissions/${id}/cancel`).then((r) => r.data),
};

// ── leave extras, optional holidays, comp-off — leave service ──────────────
export const leaveEssApi = {
  upload: (file: File) => { const f = new FormData(); f.append("file", file); return api.post<{ id: string; file_name: string }>("/leave/attachments", f).then((r) => r.data); },
  requestCancel: (id: string, reason: string) => api.post(`/leave/requests/${id}/request-cancel`, { reason }).then((r) => r.data),
  policy: () => api.get<any>("/leave/ess-policy").then((r) => r.data),
  setPolicy: (body: any) => api.put("/leave/ess-policy", body).then((r) => r.data),
  optional: (year?: number) => api.get<any>("/leave/optional-holidays", { params: q({ year }) }).then((r) => r.data),
  pick: (holiday_id: string) => api.post(`/leave/optional-holidays/${holiday_id}/pick`).then((r) => r.data),
  picks: (status?: string) => api.get<any[]>("/leave/optional-holidays/picks", { params: q({ status }) }).then((r) => r.data),
  decidePick: (id: string, verdict: "approve" | "reject" | "cancel") => api.post(`/leave/optional-holidays/picks/${id}/${verdict}`, {}).then((r) => r.data),
  compOffs: (status?: string) => api.get<any[]>("/leave/comp-off", { params: q({ status }) }).then((r) => r.data),
  claimCompOff: (body: { worked_date: string; reason: string }) => api.post("/leave/comp-off", body).then((r) => r.data),
  decideCompOff: (id: string, verdict: "approve" | "reject" | "cancel") => api.post(`/leave/comp-off/${id}/${verdict}`, {}).then((r) => r.data),
  directory: () => api.get<{ id: string; display_name: string; department?: string | null; is_self?: boolean }[]>("/employees/directory").then((r) => r.data),
};
