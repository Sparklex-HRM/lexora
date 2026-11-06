import api from "./client";

// Exit process (Stream C): cases, notice, clearance, assets, settlement link, letters,
// former employees + rehire, attrition, settings. Server: employee service, /exit/*.

export interface ExitAction { action: string; label: string; needs_reason: boolean; }
export interface ExitLetter { key: string; label: string; at?: string; by?: string; number?: string | null; pdf_url?: string | null; letter_id?: string | null; override_reason?: string | null; }
export interface ExitCase {
  id: string; case_number: string; employee_id: string; employee_name: string; employee_number?: string | null;
  department?: string | null; job_title?: string | null; manager_name?: string | null; country: string;
  exit_type: string; exit_type_label: string; initiated_by: string; reason_category?: string | null; reason?: string | null; note?: string | null;
  status: string; status_label: string; pending_with: string;
  submitted_on?: string | null; requested_lwd?: string | null; accepted_on?: string | null;
  notice_days?: number | null; notice_stage?: string | null; notice_source?: string | null;
  expected_lwd?: string | null; lwd?: string | null; lwd_reason?: string | null;
  notice_served_days?: number | null; notice_shortfall_days: number; notice_treatment?: string | null;
  notice_treatment_label?: string | null; notice_treatment_reason?: string | null; notice_decided_by?: string | null;
  garden_leave: boolean; leave_during_notice_days: number; leave_extends_lwd: boolean;
  clearance_override?: { by: string; at: string; reason: string } | null;
  settlement_kind?: string | null; settlement_ref?: string | null; settlement_status: string;
  settlement_paid_at?: string | null; settlement_net?: number | null; roe_reason_code?: string | null;
  separated: boolean; separated_at?: string | null; separation_result?: any;
  withdraw_requested: boolean; withdraw_reason?: string | null;
  letters: ExitLetter[]; rehire_eligible?: boolean | null; rehire_note?: string | null;
  created_by?: string | null; created_at?: string | null; closed_at?: string | null;
  actions?: ExitAction[]; my_relation?: string[];
}
export interface ClearanceItem {
  id: string; department: string; item: string; owner_role?: string | null; owner_name?: string | null;
  status: "pending" | "cleared" | "cleared_with_recovery"; recovery_amount: number; comment?: string | null;
  acted_by?: string | null; acted_at?: string | null; can_clear?: boolean;
}
export interface ClearanceSummary { total: number; pending: number; cleared: number; complete: boolean; recovery_total: number; pending_departments: string[]; }
export interface ExitEvent { id: string; at: string; actor?: string | null; actor_role?: string | null; action: string; from_label?: string | null; to_label?: string | null; note?: string | null; }
export interface Asset { id: string; employee_id: string; item: string; tag?: string | null; issued_on?: string | null; returned_on?: string | null; condition?: string | null; note?: string | null; returned: boolean; }
export interface InterviewQuestion { key: string; question: string; type: string; }
export interface CaseDetail {
  case: ExitCase; timeline: ExitEvent[]; clearance: ClearanceItem[]; clearance_summary: ClearanceSummary; assets: Asset[];
  notice: { notice_days: number; served_days: number; served_raw: number; shortfall_days: number; expected_lwd: string; lwd: string; early: boolean; days_left: number } | null;
  interview: any; interview_template: InterviewQuestion[]; can_see_interview: boolean; can_fill_interview: boolean;
  settlement: { ready: boolean; blocked_reason?: string | null; prefill: any };
  letters: { key: string; label: string; allowed: boolean; held_reason?: string | null; issued?: ExitLetter | null }[];
  letters_done: boolean; settings: Record<string, any>; helpers: Record<string, boolean>;
}
export interface ExitMeta {
  exit_types: { value: string; label: string; employer_initiated: boolean }[];
  states: { value: string; label: string }[]; notice_treatments: { value: string; label: string }[];
  letters: { key: string; label: string; held: boolean }[]; roe_codes: string[]; roe_suggested: Record<string, string>;
  is_hr: boolean; is_payroll: boolean; role: string; my_employee_id?: string | null; helpers: Record<string, boolean>;
}
export interface FormerEmployee {
  id: string; name: string; employee_number: string; label: string; department?: string | null; job_title?: string | null; country: string;
  hire_date?: string | null; exit_date?: string | null; exit_type_label?: string | null; exit_reason?: string | null;
  rehire_eligible?: boolean | null; rehire_note?: string | null; case_id?: string | null; spell_count: number;
  spells: { spell_no: number; employee_number?: string | null; hire_date?: string | null; exit_date?: string | null; exit_type_label?: string | null; exit_reason?: string | null }[];
}
export interface CountRow { label: string; count: number; }
export interface Attrition {
  total_exits: number; by_month: CountRow[]; by_type: CountRow[]; by_department: CountRow[]; by_reason: CountRow[];
  average_notice_served_days: number | null; notice_cases: number; short_notice_cases: number; open_cases: number; withdrawn: number;
  current_headcount: number; year?: number | null;
}

const d = <T,>(p: Promise<{ data: T }>) => p.then((r) => r.data);

export const exitApi = {
  meta: () => d(api.get<ExitMeta>("/exit/meta")),
  list: (params: Record<string, string | undefined>) =>
    d(api.get<{ scope: string; cases: ExitCase[]; counts: Record<string, number>; departments: string[] }>("/exit/cases", { params })),
  create: (body: Record<string, any>) => d(api.post<ExitCase & { separation?: any }>("/exit/cases", body)),
  detail: (id: string) => d(api.get<CaseDetail>(`/exit/cases/${id}`)),
  action: (id: string, body: Record<string, any>) => d(api.post<{ case: ExitCase; prefill?: any; separation?: any }>(`/exit/cases/${id}/action`, body)),
  notice: (id: string, body: Record<string, any>) => d(api.patch<{ case: ExitCase }>(`/exit/cases/${id}/notice`, body)),
  interview: (id: string, answers: any[], submit: boolean) => d(api.put<{ ok: boolean; submitted: boolean }>(`/exit/cases/${id}/interview`, { answers, submit })),
  clear: (id: string, itemId: string, body: { status: string; recovery_amount?: number; comment?: string }) =>
    d(api.patch<{ clearance: ClearanceItem[]; clearance_summary: ClearanceSummary }>(`/exit/cases/${id}/clearance/${itemId}`, body)),
  addLine: (id: string, body: { department: string; item: string; owner_role: string; owner_user_id?: string; owner_name?: string }) => d(api.post<any>(`/exit/cases/${id}/clearance`, body)),
  assets: (employee_id: string) => d(api.get<Asset[]>("/exit/assets", { params: { employee_id } })),
  addAsset: (body: { employee_id: string; item: string; tag?: string; issued_on?: string }) => d(api.post<Asset>("/exit/assets", body)),
  updateAsset: (id: string, body: Record<string, any>) => d(api.patch<Asset>(`/exit/assets/${id}`, body)),
  settlement: (id: string) => d(api.get<{ case: ExitCase; ready: boolean; blocked_reason?: string | null; prefill: any; pay_status: any; pay_status_available: boolean }>(`/exit/cases/${id}/settlement`)),
  settlementLink: (id: string, settlement_ref: string, kind?: string) => d(api.post<any>(`/exit/cases/${id}/settlement/link`, { settlement_ref, kind })),
  settlementSync: (id: string) => d(api.post<{ case: ExitCase; changed: boolean; pay_status: any }>(`/exit/cases/${id}/settlement/sync`)),
  letter: (id: string, key: string, override_reason?: string) => d(api.post<{ case: ExitCase; letter: ExitLetter; closed: boolean }>(`/exit/cases/${id}/letters`, { key, override_reason })),
  rehireFlag: (id: string, eligible: boolean | null, note?: string) => d(api.patch<any>(`/exit/cases/${id}/rehire-flag`, { eligible, note })),
  tick: (case_id?: string, force = false) => d(api.post<any>("/exit/lwd-tick", { case_id, force })),
  former: (q?: string) => d(api.get<{ former_employees: FormerEmployee[]; rehire_number_policy: string; can_rehire: boolean }>("/exit/former-employees", { params: q ? { q } : {} })),
  formerFlag: (employee_id: string, eligible: boolean | null, note?: string) => d(api.patch<any>(`/exit/former-employees/${employee_id}/rehire-flag`, { eligible, note })),
  rehire: (body: Record<string, any>) => d(api.post<any>("/exit/rehire", body)),
  attrition: (year?: number) => d(api.get<Attrition>("/exit/attrition", { params: year ? { year } : {} })),
  settings: () => d(api.get<any>("/exit/settings")),
  saveSettings: (body: Record<string, any>) => d(api.put<any>("/exit/settings", body)),
  addPolicy: (body: Record<string, any>) => d(api.post<any[]>("/exit/notice-policies", body)),
  deletePolicy: (id: string) => d(api.delete<any[]>(`/exit/notice-policies/${id}`)),
  noticePreview: (employee_id: string, on?: string) => d(api.get<{ days: number; stage: string; source: string; expected_lwd: string; accepted_on: string; rule: string }>("/exit/notice-preview", { params: { employee_id, on } })),
  // payroll side: pro-ration basis
  dateRules: () => d(api.get<{ proration_basis: string; basis_label: string; working_weekdays: number[]; options: { value: string; label: string }[]; rule: string }>("/payroll/date-rules")),
  saveDateRules: (proration_basis: string, working_weekdays?: number[]) => d(api.put<any>("/payroll/date-rules", { proration_basis, working_weekdays })),
};

export const errText = (e: any, fallback = "Something went wrong") => {
  const det = e?.response?.data?.detail;
  if (typeof det === "string") return det;
  if (det && typeof det === "object") return det.message || det.error || fallback;
  return fallback;
};
export const dmy = (iso?: string | null) =>
  iso ? new Date(iso.length <= 10 ? iso + "T00:00:00" : iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "—";
export const STATUS_TONE: Record<string, string> = {
  submitted: "badge-amber", manager_review: "badge-amber", hr_review: "badge-amber", accepted: "badge-teal",
  clearance: "badge-purple", settlement: "badge-purple", settled: "badge-green", closed: "badge-green",
  withdrawn: "badge-red", rejected: "badge-red", on_hold: "badge-amber",
};
