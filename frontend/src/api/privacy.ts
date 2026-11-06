// Stream E — Platform APIs: Privacy Centre (PIPEDA), access / correction requests,
// PEPM billing (CAD, GST/HST by province), launch security (sessions, export).
import api from "./client";

// ── Privacy Centre (compliance service) ──────────────────────────────────────────────
export interface ConsentPurpose { purpose: string; label: string; text: string; status: string; notice_version: number | null; stale: boolean; given_at: string | null; withdrawn_at: string | null; }
export interface DataRequestRow {
  id: string; ref: string; employee_id: string; employee_name: string | null; request_type: string; type_label: string;
  details: string | null; payload: any; status: string; submitted_at: string; due_at: string | null;
  days_left: number | null; overdue: boolean; band: string; assigned_to: string | null; resolution_note: string | null;
  hold_reason: string | null; closed_at: string | null; closed_by: string | null;
}
export interface BreachRow {
  id: string; ref: string; detected_at: string; occurred_at: string | null; description: string; data_categories: string[];
  individuals_affected: number; severity: string; status: string; commissioner_notified_at: string | null; individuals_notified_at: string | null;
  checklist: { key: string; label: string; done: boolean; at: string | null }[]; reported_by: string | null;
  hours_since_detection: number | null; keep_record_until: string | null; reported: boolean;
}
export interface RetentionRow { id: string; record_type: string; retain_years: number; legal_basis: string | null; action: string; enabled: boolean; }

export const privacyApi = {
  purposes: () => api.get<any>("/compliance/privacy/purposes").then((r) => r.data),
  settings: () => api.get<any>("/compliance/privacy/settings").then((r) => r.data),
  saveSettings: (body: Record<string, any>) => api.put<any>("/compliance/privacy/settings", body).then((r) => r.data),
  notice: () => api.get<any>("/compliance/privacy/notice").then((r) => r.data),
  noticeVersions: () => api.get<any>("/compliance/privacy/notice/versions").then((r) => r.data),
  publishNotice: (body: { title: string; body: string }) => api.post<any>("/compliance/privacy/notice", body).then((r) => r.data),
  consents: (employee_id?: string) => api.get<{ employee_id: string; current_notice_version: number; purposes: ConsentPurpose[]; history: any[] }>("/compliance/privacy/consents", { params: employee_id ? { employee_id } : {} }).then((r) => r.data),
  giveConsent: (body: { employee_id?: string; purposes: string[]; signed_name?: string; channel?: string }) => api.post<any>("/compliance/privacy/consents", body).then((r) => r.data),
  withdrawConsent: (body: { employee_id?: string; purposes: string[]; reason?: string }) => api.post<any>("/compliance/privacy/consents/withdraw", body).then((r) => r.data),
  consentSummary: () => api.get<any>("/compliance/privacy/consents/summary").then((r) => r.data),
  requests: (params?: Record<string, any>) => api.get<{ total: number; open: number; overdue: number; items: DataRequestRow[]; statuses: string[] }>("/compliance/privacy/requests", { params }).then((r) => r.data),
  createRequest: (body: { employee_id?: string; request_type: string; details?: string; payload?: any }) => api.post<DataRequestRow>("/compliance/privacy/requests", body).then((r) => r.data),
  patchRequest: (id: string, body: Record<string, any>) => api.patch<DataRequestRow>(`/compliance/privacy/requests/${id}`, body).then((r) => r.data),
  applyErasure: (id: string) => api.post<DataRequestRow>(`/compliance/privacy/requests/${id}/apply-erasure`).then((r) => r.data),
  exportMyData: (employee_id?: string) => api.get<any>("/compliance/privacy/export-my-data", { params: { employee_id, format: "json" } }).then((r) => r.data),
  exportMyDataPdf: (employee_id?: string) => api.get("/compliance/privacy/export-my-data", { params: { employee_id, format: "pdf" }, responseType: "blob" }).then((r) => r.data as Blob),
  breaches: () => api.get<{ total: number; open: number; items: BreachRow[] }>("/compliance/privacy/breaches").then((r) => r.data),
  breachTemplate: () => api.get<any>("/compliance/privacy/breaches/checklist-template").then((r) => r.data),
  createBreach: (body: Record<string, any>) => api.post<BreachRow>("/compliance/privacy/breaches", body).then((r) => r.data),
  patchBreach: (id: string, body: Record<string, any>) => api.patch<BreachRow>(`/compliance/privacy/breaches/${id}`, body).then((r) => r.data),
  retention: () => api.get<{ items: RetentionRow[] }>("/compliance/privacy/retention").then((r) => r.data),
  saveRetention: (rows: Partial<RetentionRow>[]) => api.put<any>("/compliance/privacy/retention", rows).then((r) => r.data),
  runRetention: (dry_run = true) => api.post<any>("/compliance/privacy/retention/run", null, { params: { dry_run } }).then((r) => r.data),
  retentionRuns: () => api.get<any>("/compliance/privacy/retention/runs").then((r) => r.data),
  processorContract: () => api.get<any>("/compliance/privacy/processor-contract").then((r) => r.data),
  processorContractPdf: () => api.get("/compliance/privacy/processor-contract/pdf", { responseType: "blob" }).then((r) => r.data as Blob),
  acceptContract: () => api.post<any>("/compliance/privacy/processor-contract/accept").then((r) => r.data),
  dashboard: () => api.get<any>("/compliance/privacy/dashboard").then((r) => r.data),
};

// ── Employee sensitive identifiers (employee service) ──────────────────────
export const sensitiveApi = {
  reveal: (employee_id: string) => api.get<any>(`/employees/${employee_id}/sensitive`).then((r) => r.data),
  encryptionStatus: () => api.get<any>("/employees/encryption-status").then((r) => r.data),
  setHold: (employee_id: string, hold: boolean, reason?: string) => api.post<any>(`/employees/${employee_id}/retention-hold`, { hold, reason }).then((r) => r.data),
};

// ── Billing (auth service) ─────────────────────────────────────────────────
export interface InvoiceLine { code: string; label: string; qty: number; unit: number; amount: number; minimum_applied?: boolean; minimum?: number; free_band?: boolean; note?: string; one_time?: boolean; waived?: boolean; }
export interface InvoicePreview {
  org_id: string; org_name: string; country: string; currency: string; plan: string; plan_label: string; billing_model: string;
  period: string; period_label: string; active_employees: number; pepm?: number; billing_cycle: string; free_band?: boolean;
  annual_discount_pct?: number; lines: InvoiceLine[]; subtotal: number; tax_lines: { code: string; label: string; amount: number }[];
  gst: number; total: number; tax_rate?: number; tax_province?: string | null; tax_determined?: boolean; seller_tax_configured?: boolean; gst_hst_number: string | null; billing_email: string | null; addons: string[]; modules: string[]; custom_quote?: boolean; note?: string;
}
export const billingApi = {
  plans: (country = "CA") => api.get<any>("/auth/billing/plans", { params: { country } }).then((r) => r.data),
  preview: (org_id?: string, period?: string) => api.get<InvoicePreview>("/auth/billing/preview", { params: { org_id, period } }).then((r) => r.data),
  saveSettings: (body: Record<string, any>) => api.patch<InvoicePreview>("/auth/billing/settings", body).then((r) => r.data),
  generateInvoice: (org_id?: string, period?: string) => api.post<any>("/auth/billing/invoices/generate", null, { params: { org_id, period } }).then((r) => r.data),
  invoices: (org_id?: string) => api.get<any>("/auth/billing/invoices", { params: { org_id } }).then((r) => r.data),
  platformSummary: () => api.get<any>("/auth/billing/platform-summary").then((r) => r.data),
  saveCatalog: (body: { country: string; plans?: any[]; addons?: any[] }) => api.put<any>("/auth/billing/catalog", body).then((r) => r.data),
};

// ── Security (auth service) ────────────────────────────────────────────────
export const securityApi = {
  sessions: (params?: Record<string, any>) => api.get<any>("/auth/sessions", { params }).then((r) => r.data),
  changePassword: (current_password: string, new_password: string) => api.post<any>("/auth/change-password", { current_password, new_password }).then((r) => r.data),
  passwordPolicy: () => api.get<{ text: string }>("/auth/password-policy").then((r) => r.data),
  loginInfo: (org_code?: string) => api.get<any>("/auth/login-info", { params: org_code ? { org_code } : {} }).then((r) => r.data),
  orgExport: () => api.get("/auth/org-export", { responseType: "blob" }).then((r) => r.data as Blob),
};

// ── IT-declaration proof files (payroll service) ───────────────────────────
export interface ProofFile { id: string; employee_id: string; year: number; section: string; amount: number; remark: string | null; file_name: string; content_type: string; size_bytes: number; size_label: string; scan_status: string; uploaded_at: string | null; download_url: string; preview_url: string; }
export const proofApi = {
  limits: () => api.get<any>("/payroll/it-proofs/limits").then((r) => r.data),
  list: (employee_id: string, year = 2026) => api.get<{ items: ProofFile[]; total_bytes: number }>("/payroll/it-proofs", { params: { employee_id, year } }).then((r) => r.data),
  upload: (body: { employee_id: string; year: number; section: string; amount?: number; remark?: string; file: File }) => {
    const fd = new FormData();
    fd.append("employee_id", body.employee_id); fd.append("year", String(body.year)); fd.append("section", body.section);
    fd.append("amount", String(body.amount ?? 0)); fd.append("remark", body.remark || ""); fd.append("file", body.file);
    return api.post<ProofFile>("/payroll/it-proofs/upload", fd, { headers: { "Content-Type": "multipart/form-data" } }).then((r) => r.data);
  },
  download: (id: string) => api.get(`/payroll/it-proofs/${id}/download`, { responseType: "blob" }).then((r) => r.data as Blob),
  remove: (id: string) => api.delete<any>(`/payroll/it-proofs/${id}`).then((r) => r.data),
};

export function saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}
export function openBlob(blob: Blob) {
  const url = URL.createObjectURL(blob);
  window.open(url, "_blank", "noopener");
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}
