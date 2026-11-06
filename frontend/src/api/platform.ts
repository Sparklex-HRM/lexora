// Notifications, approvals inbox, company settings and reports (stream E).
// Every call goes through the gateway; the server takes the organisation and the
// user from the session, never from what the browser sends.
import api from "./client";
import { formatDateTime } from "../lib/datetime";   // 2026-10-25: UTC store, org-zone today

// ── Notifications ───────────────────────────────────────────────────────────
export interface Note {
  id: string; title: string; body: string; link: string; kind: string; kind_label: string;
  created_at: string | null; read_at: string | null; read: boolean;
}
export interface SmtpStatus { configured: boolean; message: string; }
export interface Delivery {
  id: string; recipient_name: string; recipient_email: string; channel: string; kind: string;
  kind_label: string; title: string; status: string; reason: string; attempts: number;
  created_at: string | null; sent_at: string | null; can_retry: boolean;
}
export interface NoteTemplate {
  kind: string; label: string; subject: string; body: string; customised: boolean;
  updated_by: string | null; updated_at: string | null;
}

export const notificationsApi = {
  list: (params: { unread?: boolean; kind?: string; q?: string; limit?: number; offset?: number }) =>
    api.get<{ items: Note[]; total: number; unread: number; kinds: { kind: string; label: string }[] }>(
      "/notifications", { params }).then((r) => r.data),
  unreadCount: () => api.get<{ unread: number; latest: Note[] }>("/notifications/unread-count").then((r) => r.data),
  read: (id: string) => api.post(`/notifications/${id}/read`).then((r) => r.data),
  unread: (id: string) => api.post(`/notifications/${id}/unread`).then((r) => r.data),
  readAll: (kind?: string) => api.post<{ marked: number }>("/notifications/read-all", { kind: kind || null }).then((r) => r.data),
  preferences: () => api.get<{ email_available: SmtpStatus; kinds: { kind: string; label: string; email_on: boolean }[] }>(
    "/notifications/preferences").then((r) => r.data),
  savePreferences: (email: Record<string, boolean>) => api.put("/notifications/preferences", { email }).then((r) => r.data),
  status: () => api.get<SmtpStatus>("/notifications/status").then((r) => r.data),
  deliveries: (params: { status?: string; channel?: string; kind?: string; q?: string; limit?: number }) =>
    api.get<{ items: Delivery[]; total: number; by_status: Record<string, number>; smtp: SmtpStatus }>(
      "/notifications/deliveries", { params }).then((r) => r.data),
  retry: (id: string) => api.post<{ status: string }>(`/notifications/deliveries/${id}/retry`).then((r) => r.data),
  testEmail: () => api.post<{ in_app: string; email: string; reason: string }>("/notifications/test-email").then((r) => r.data),
  templates: () => api.get<{ placeholders: { name: string; meaning: string }[]; note: string;
    default: { subject: string; body: string }; templates: NoteTemplate[] }>("/notifications/templates").then((r) => r.data),
  saveTemplate: (kind: string, subject: string, body: string) =>
    api.put<{ preview: { subject: string; body: string } }>(`/notifications/templates/${kind}`, { subject, body }).then((r) => r.data),
  resetTemplate: (kind: string) => api.delete(`/notifications/templates/${kind}`).then((r) => r.data),
};

// ── Approvals ───────────────────────────────────────────────────────────────
export interface DecisionRow { decision: string; comment: string; by: string; on_behalf_of: string; at: string | null; round: number; recorded_from: string; }
export interface ApprovalRow {
  id: string; kind: string; kind_label: string; title: string; summary: string[]; requester_name: string;
  status: string; opened_at: string | null; closed_at: string | null; age_days: number; link: string;
  can_decide_here: boolean; why?: string; acting_for?: string; waiting_on?: string; i_can_decide?: boolean;
  decided_by?: string; decided_at?: string | null; comment?: string; history?: DecisionRow[];
  // D1 no-code chains: which step the request is on
  step_label?: string; step_no?: number; steps_total?: number; waiting_for?: string[]; sla_due_at?: string | null;
  overdue?: boolean; chain_name?: string; escalated?: boolean; mode?: string;
}
export interface KindCount { kind: string; label: string; count: number; }
export interface ApprovalList { items: ApprovalRow[]; total: number; by_kind: KindCount[]; as_of?: string; }
export interface DecidedRow extends DecisionRow { id: string; kind: string; kind_label: string; title: string; summary: string[]; requester_name: string; link: string; }
export interface Delegation {
  id: string; from_name: string; to_name: string; from_user_id: string; to_user_id: string;
  start_date: string; end_date: string; note: string; state: string; mine: boolean; to_me: boolean;
}

export const approvalsApi = {
  inbox: () => api.get<ApprovalList>("/approvals/inbox").then((r) => r.data),
  summary: () => api.get<{ waiting_for_me: number }>("/approvals/summary").then((r) => r.data),
  all: () => api.get<ApprovalList>("/approvals/all").then((r) => r.data),
  mine: () => api.get<ApprovalList>("/approvals/mine").then((r) => r.data),
  decided: () => api.get<{ items: DecidedRow[]; total: number; by_kind: KindCount[] }>("/approvals/decided").then((r) => r.data),
  decide: (id: string, decision: "approve" | "reject", comment: string) =>
    api.post<{ status: string }>(`/approvals/items/${id}/decide`, { decision, comment }).then((r) => r.data),
  people: () => api.get<{ id: string; name: string; role: string }[]>("/approvals/people").then((r) => r.data),
  delegations: () => api.get<Delegation[]>("/approvals/delegations").then((r) => r.data),
  createDelegation: (body: { to_user_id: string; start_date: string; end_date: string; note?: string; from_user_id?: string }) =>
    api.post<Delegation>("/approvals/delegations", body).then((r) => r.data),
  cancelDelegation: (id: string) => api.delete(`/approvals/delegations/${id}`).then((r) => r.data),
};

// ── Company settings ────────────────────────────────────────────────────────
export interface SettingGroup { label: string; value: any; saved: boolean; updated_by: string | null; updated_at: string | null; }
export interface SettingsAll { groups: Record<string, SettingGroup>; can_edit: boolean; has_logo: boolean; logo_needs_reupload?: boolean; }
export interface SettingsAudit { id: string; key: string; label: string; by: string; at: string | null; changes: { field: string; before: any; after: any }[]; }

export const settingsApi = {
  all: () => api.get<SettingsAll>("/settings").then((r) => r.data),
  get: <T = any>(key: string) => api.get<{ key: string; value: T }>(`/settings/${key}`).then((r) => r.data.value),
  save: (group: string, value: any) => api.put(`/settings/${group}`, { value }).then((r) => r.data),
  audit: () => api.get<SettingsAudit[]>("/settings/audit").then((r) => r.data),
  logoBlob: () => api.get("/settings/logo", { responseType: "blob" }).then((r) => r.data as Blob),
  saveLogo: (content_type: string, data_base64: string) => api.put("/settings/logo", { content_type, data_base64 }).then((r) => r.data),
  deleteLogo: () => api.delete("/settings/logo").then((r) => r.data),
};

// ── Reports ─────────────────────────────────────────────────────────────────
export type ColType = "text" | "number" | "money" | "percent" | "date" | "hours";
export interface ReportColumn { key: string; label: string; type: ColType; }
export interface ChartSpec { type: "bar" | "line"; x: string; series: { key: string; label: string }[]; data?: any[]; }
export interface Report {
  key: string; title: string; group: string; description: string; as_of: string; scope?: string; period?: string;
  columns: ReportColumn[]; rows: Record<string, any>[]; totals?: Record<string, any> | null;
  formula?: string | null; notes?: string[]; chart?: ChartSpec | null; problems: string[]; empty: string;
  options?: { by?: { value: string; label: string }[] }; summary?: { label: string; value: any; type?: ColType }[];
}
export interface CatalogEntry { key: string; title: string; group: string; description: string; params: string[]; }
export interface DashTile { key: string; label: string; value: number | null; unit: ColType; sub: string; empty: string; }
export interface DashChart extends ChartSpec { key: string; title: string; data: any[]; }
export interface Dashboard { as_of: string; scope: string; tiles: DashTile[]; charts: DashChart[]; shows_pay: boolean; }

export const reportsApi = {
  catalog: () => api.get<{ reports: CatalogEntry[]; to_add: { key: string; title: string; group: string; reason: string }[]; scope: string }>(
    "/reports/catalog").then((r) => r.data),
  dashboard: () => api.get<Dashboard>("/reports/dashboard").then((r) => r.data),
  run: (key: string, params: Record<string, string | number | undefined>) =>
    api.get<Report>(`/reports/${key}`, { params }).then((r) => r.data),
};

export const errText = (e: any, fallback = "Something went wrong. Try again.") => {
  const d = e?.response?.data?.detail;
  return typeof d === "string" && d ? d : fallback;
};
// an instant in the viewer's zone, zone named ("25 Oct 2026, 10:30 am IST") — lib/datetime.ts
export const when = (iso?: string | null) => formatDateTime(iso);
export const day = (iso?: string | null) =>
  iso ? new Date(iso.length <= 10 ? iso + "T00:00:00" : iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "—";
