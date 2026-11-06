import api from "./client";

// ── Stream G6: Canada completeness — /payroll/ca/* ──
export interface Gate { verified: boolean; mode: "live" | "test"; constants_verified: number; constants_total: number; plan: string; reasons: string[]; }
export interface StatValueRow {
  key: string; label: string; group: string; kind: "number" | "percent" | "date" | "json" | "text"; help: string; province: string | null;
  shape: any; unit: string; value: string | null; typed: any; parse_ok: boolean; source_url: string | null; effective_from: string | null;
  note: string | null; updated_by: string | null; updated_at: string | null; status: "blank" | "entered" | "verified" | "stale";
  verified_by: string | null; verified_on: string | null; signed_source: string | null;
}
export interface StatValues { rows: StatValueRow[]; total: number; verified: number; entered: number; stale: number; blank: number; shapes: any; stat_rule_shape: any; }
export interface CaSettings { remitter_type: string; rq_remitter_type: string; cra_payroll_account: string | null; rq_account: string | null; remitter_types: string[]; }
export interface RemitPeriod { period: string; employees: number; pay_dates: string[]; gross_payroll: number; income_tax: number; cpp: number; cpp_employee: number; cpp_employer: number; ei: number; ei_employee: number; ei_employer: number; total_remittance: number; due_date: string | null; due_reason: string | null; }
export interface Remittances {
  year: number; settings: CaSettings; schedule: { remitter_type: string; rule: any; rule_source: string | null; periods: RemitPeriod[]; total_remittance: number };
  amwa: { months: number; total: number; amwa: number }; ceilings: Record<string, number | null>; suggestion: { suggested: string | null; reason: string };
  rq: { periods: any[]; total_remittance: number }; compliance: Gate; legacy_rule_in_use: boolean;
}
export interface StatLine { step: string; detail: string; value: any; }
export interface StatCompute { ok: boolean; reason: string | null; holiday: string; province: string; lines: StatLine[]; holiday_pay: number; worked_premium_pay: number; entries: any[]; employee: any; rule: any; compliance: Gate; }
export interface RateGroup { id: string; province: string; group_code: string; description: string | null; rate_per_100: number; year: number; account_number: string | null; }
export interface Contractor { id: string; code: string; name: string; business_number: string | null; email: string | null; address: string | null; province: string | null; default_box: string; active: boolean; ytd_paid?: number; ytd_tax?: number; payments?: number; }
export interface ContractorPayment { id: string; contractor_id: string; pay_date: string; year: number; invoice_no: string | null; description: string | null; amount: number; gst_hst: number; tax_withheld: number; box: string; status: string; }

const B = "/payroll/ca";

export const canadaApi = {
  employees: () => api.get<any[]>("/employees").then((r) => r.data),
  statutoryValues: () => api.get<StatValues>(`${B}/statutory-values`).then((r) => r.data),
  putValue: (body: { key: string; value: string | null; source_url: string; effective_from: string; note?: string }) => api.put(`${B}/statutory-values`, body).then((r) => r.data),
  verifyValue: (body: { key: string; status?: "verified" | "estimated"; note?: string }) => api.post(`${B}/statutory-values/verify`, body).then((r) => r.data),
  settings: () => api.get<CaSettings>(`${B}/settings`).then((r) => r.data),
  putSettings: (body: Partial<CaSettings>) => api.put(`${B}/settings`, body).then((r) => r.data),
  remittances: (year = 2026) => api.get<Remittances>(`${B}/remittances`, { params: { year } }).then((r) => r.data),
  statRules: () => api.get<{ rules: StatValueRow[]; methods: string[]; shape: any }>(`${B}/stat-holiday/rules`).then((r) => r.data),
  statCompute: (body: { employee_id: string; holiday: string; eligible?: boolean; hours_worked_on_holiday?: number; days_worked_in_lookback?: number | null; manual_amount?: number | null }) =>
    api.post<StatCompute>(`${B}/stat-holiday/compute`, body).then((r) => r.data),
  rateGroups: (year = 2026) => api.get<{ year: number; groups: RateGroup[]; employees: any[] }>(`${B}/accruals/rate-groups`, { params: { year } }).then((r) => r.data),
  putRateGroup: (body: Partial<RateGroup> & { province: string; group_code: string; rate_per_100: number }) => api.put(`${B}/accruals/rate-groups`, body).then((r) => r.data),
  deleteRateGroup: (id: string) => api.delete(`${B}/accruals/rate-groups/${id}`).then((r) => r.data),
  assign: (body: { employee_id: string; rate_group_id: string | null }) => api.put(`${B}/accruals/assignments`, body).then((r) => r.data),
  accrualSummary: (year = 2026) => api.get<any>(`${B}/accruals/summary`, { params: { year } }).then((r) => r.data),
  accrualRun: (runId: string) => api.get<any>(`${B}/accruals/run/${runId}`).then((r) => r.data),
  contractors: (year = 2026, include_inactive = 0) => api.get<Contractor[]>(`${B}/contractors`, { params: { year, include_inactive } }).then((r) => r.data),
  putContractor: (body: Partial<Contractor> & { code: string; name: string }) => api.put(`${B}/contractors`, body).then((r) => r.data),
  payments: (year = 2026, contractor_id?: string) => api.get<ContractorPayment[]>(`${B}/contractors/payments`, { params: { year, contractor_id } }).then((r) => r.data),
  addPayment: (body: { contractor_id: string; pay_date: string; amount: number; gst_hst?: number; tax_withheld?: number; invoice_no?: string; description?: string; box?: string }) =>
    api.post(`${B}/contractors/payments`, body).then((r) => r.data),
  cancelPayment: (id: string) => api.post(`${B}/contractors/payments/${id}/cancel`, {}).then((r) => r.data),
  t4a: (year = 2026) => api.get<any>(`${B}/t4a`, { params: { year } }).then((r) => r.data),
  yearEnd: (year = 2026) => api.get<any>(`${B}/year-end`, { params: { year } }).then((r) => r.data),
  roeValidate: (employee_id: string, reason = "A") => api.get<any>(`${B}/roe/${employee_id}/validate`, { params: { reason } }).then((r) => r.data),
  runs: () => api.get<any[]>("/payroll/runs").then((r) => r.data),
};
