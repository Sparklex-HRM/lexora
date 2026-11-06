import api from "./client";

/** Stream B — salary history, salary revisions (hikes), career changes, probation
 *  and the letter engine. Paths go through the gateway:
 *    /payroll/revisions/*   payroll service
 *    /employees/*           employee service (compensation, career, probation)
 *    /letters/*             employee service (letter engine)                     */

const q = (params: Record<string, any>) =>
  Object.fromEntries(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ""));
const d = <T = any>(p: Promise<{ data: T }>) => p.then((r) => r.data);

/** Error text from the API. `detail` may be a string or {message, errors[], missing_fields[]}. */
export function errText(e: any, fallback = "Something went wrong"): string {
  const det = e?.response?.data?.detail;
  if (!det) return e?.message || fallback;
  if (typeof det === "string") return det;
  if (Array.isArray(det)) return det.map((x: any) => x?.msg || String(x)).join(" ");
  const parts = [det.message, ...(Array.isArray(det.errors) ? det.errors : [])].filter(Boolean);
  return parts.join(" ") || fallback;
}

/** Money in the row's currency (CAD, en-CA: $1,234.56). */
export function cur(n: any, currency?: string): string {
  const v = Number(n) || 0;
  if ((currency || "").toUpperCase() === "CAD")
    return "$" + v.toLocaleString("en-CA", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
export const fmtDate = (s?: string | null) => {
  if (!s) return "—";
  const dt = new Date(s.length <= 10 ? `${s}T00:00:00` : s);
  return isNaN(dt.getTime()) ? s : dt.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
};
export const pct = (n: any) => (n === null || n === undefined ? "—" : `${Number(n).toFixed(2)}%`);

export const REASONS: Record<string, string> = {
  annual_increment: "Annual increment", promotion: "Promotion", market_correction: "Market correction",
  correction: "Correction", other: "Other",
};

export interface RevisionInput {
  employee_id: string; effective_from: string; mode: "new_annual" | "percent" | "amount"; value: number;
  reason: string; note?: string; submit?: boolean; corrects_revision_id?: string;
}

export const revisionsApi = {
  list: (params: { status?: string; employee_id?: string; batch_id?: string } = {}) =>
    d<any[]>(api.get("/payroll/revisions", { params: q(params) })),
  preview: (body: RevisionInput) => d(api.post("/payroll/revisions/preview", body)),
  create: (body: RevisionInput) => d(api.post("/payroll/revisions", body)),
  update: (id: string, body: RevisionInput) => d(api.put(`/payroll/revisions/${id}`, body)),
  act: (id: string, action: "submit" | "approve" | "reject" | "cancel", note?: string) =>
    d(api.post(`/payroll/revisions/${id}/${action}`, { note })),
  letter: (id: string) => d(api.post(`/payroll/revisions/${id}/letter`)),
  bulkPreview: (body: any) => d(api.post("/payroll/revisions/bulk-preview", body)),
  bulkUpload: (file: File, effective_from?: string) => {
    const fd = new FormData();
    fd.append("file", file);
    return d(api.post("/payroll/revisions/bulk-upload", fd, { params: q({ effective_from }) }));
  },
  bulkDraft: (body: { source: string; items: any[]; name?: string; note?: string }) =>
    d(api.post("/payroll/revisions/bulk-draft", body)),
  sheetTemplate: () => api.get("/payroll/revisions/sheet-template", { responseType: "blob" }).then((r) => r.data as Blob),
  batches: () => d<any[]>(api.get("/payroll/revisions/batches")),
  batch: (id: string) => d(api.get(`/payroll/revisions/batches/${id}`)),
  batchAct: (id: string, action: "submit" | "approve" | "reject" | "cancel", note?: string) =>
    d(api.post(`/payroll/revisions/batches/${id}/${action}`, { note })),
  settings: () => d(api.get("/payroll/revisions/settings")),
  saveSettings: (body: { two_step_enabled: boolean; two_step_percent: number | null; two_step_amount: number | null }) =>
    d(api.put("/payroll/revisions/settings", body)),
  applyDue: () => d(api.post("/payroll/revisions/apply-due")),
  breakup: (employee_id: string) => d(api.get("/payroll/revisions/breakup", { params: { employee_id } })),
  myBreakup: () => d(api.get("/payroll/revisions/breakup/mine")),
  retro: (status?: string) => d<any[]>(api.get("/payroll/revisions/retro-proposals", { params: q({ status }) })),
  retroAct: (id: string, action: "accept" | "dismiss") => d(api.post(`/payroll/revisions/retro-proposals/${id}/${action}`)),
};

export const compApi = {
  employee: (id: string) => d(api.get(`/employees/${id}/compensation`)),
  mine: () => d(api.get("/employees/compensation/mine")),
};

export const careerApi = {
  fields: () => d(api.get("/employees/career/fields")),
  list: (status?: string) => d<any[]>(api.get("/employees/career/changes", { params: q({ status }) })),
  forEmployee: (id: string) => d<any[]>(api.get(`/employees/${id}/career-changes`)),
  summary: (id: string) => d(api.get(`/employees/${id}/career`)),
  create: (id: string, body: { change_type: string; effective_date: string; new_values: Record<string, any>;
    reason?: string; note?: string; linked_revision_id?: string; submit?: boolean }) =>
    d(api.post(`/employees/${id}/career-changes`, body)),
  act: (id: string, action: "submit" | "approve" | "reject" | "cancel", note?: string) =>
    d(api.post(`/employees/career/changes/${id}/${action}`, { note })),
  letter: (id: string, template_key?: string) => d(api.post(`/employees/career/changes/${id}/letter`, { template_key })),
  applyDue: () => d(api.post("/employees/career/apply-due")),
};

export const probationApi = {
  due: (params: { within?: number; all?: number } = {}) => d(api.get("/employees/career/probation-due", { params: q(params) })),
  assignMissing: () => d(api.post("/employees/career/probation/assign-missing")),
  settings: () => d(api.get("/employees/career/settings")),
  saveSettings: (body: { probation_months: number; probation_rules: any[] }) => d(api.put("/employees/career/settings", body)),
  recommend: (id: string, body: { recommendation: string; reason?: string; new_end_date?: string }) =>
    d(api.post(`/employees/${id}/probation/recommend`, body)),
  confirm: (id: string, body: { confirmation_date?: string; reason?: string; issue_letter: boolean }) =>
    d(api.post(`/employees/${id}/probation/confirm`, body)),
  extend: (id: string, body: { new_end_date: string; reason: string; issue_letter: boolean }) =>
    d(api.post(`/employees/${id}/probation/extend`, body)),
  doNotConfirm: (id: string, body: { reason: string; last_working_date?: string }) =>
    d(api.post(`/employees/${id}/probation/do-not-confirm`, body)),
};

export const lettersApi = {
  all: (params: { status?: string; template_key?: string } = {}) => d<any[]>(api.get("/letters", { params: q(params) })),
  mine: () => d<any[]>(api.get("/letters/mine")),
  forEmployee: (id: string) => d<any[]>(api.get(`/employees/${id}/letters`)),
  create: (id: string, body: { template_key: string; data?: any; effective_date?: string; issue: boolean; allow_blank?: boolean }) =>
    d(api.post(`/employees/${id}/letters`, body)),
  pdf: (id: string) => api.get(`/letters/${id}/pdf`, { responseType: "blob" }).then((r) => r.data as Blob),
  preview: (body: { employee_id: string; template_key?: string; body?: string; data?: any; effective_date?: string }) =>
    api.post("/letters/preview", body, { responseType: "blob" }).then((r) => r.data as Blob),
  issueDraft: (id: string, allow_blank = false) => d(api.post(`/letters/${id}/issue`, null, { params: { allow_blank } })),
  reissue: (id: string) => d(api.post(`/letters/${id}/reissue`)),
  void: (id: string, reason: string) => d(api.post(`/letters/${id}/void`, { reason })),
  discard: (id: string) => d(api.delete(`/letters/${id}`)),
  fields: () => d(api.get("/letters/fields")),
  templates: () => d<any[]>(api.get("/letters/templates")),
  createTemplate: (body: any) => d(api.post("/letters/templates", body)),
  saveTemplate: (id: string, body: any) => d(api.put(`/letters/templates/${id}`, body)),
  resetTemplate: (id: string) => d(api.post(`/letters/templates/${id}/reset`)),
  settings: () => d(api.get("/letters/settings")),
  saveSettings: (body: any) => d(api.put("/letters/settings", body)),
};

/** Open a PDF blob in a new tab (falls back to a download when pop-ups are blocked). */
export function openPdf(blob: Blob, name = "letter.pdf") {
  const url = URL.createObjectURL(new Blob([blob], { type: "application/pdf" }));
  const w = window.open(url, "_blank");
  if (!w) {
    const a = document.createElement("a");
    a.href = url; a.download = name; a.click();
  }
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

/** A blob error body (preview / pdf endpoints) → the API's message. */
export async function blobErrText(e: any, fallback = "Could not open the letter"): Promise<string> {
  const data = e?.response?.data;
  if (data instanceof Blob) {
    try { return errText({ response: { data: JSON.parse(await data.text()) } }, fallback); } catch { return fallback; }
  }
  return errText(e, fallback);
}
