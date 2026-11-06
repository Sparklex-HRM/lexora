import api from "./client";
import type { User } from "../store/authStore";
import { PROVINCE_NAMES } from "./regions";

// ── Types (mirror the Lexora backend response builders) ────────────────────
export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  org_id: string;
  role: string;
}

export interface Employee {
  id: string;
  employee_number: string;
  first_name: string;
  last_name: string;
  email: string | null;
  date_of_birth?: string | null;
  gender?: string | null;
  preferred_name?: string | null;
  pronouns?: string | null;
  phone?: string | null;
  sin_masked?: string | null;
  work_eligibility?: string | null;
  work_permit_expiry?: string | null;
  address_line1?: string | null;
  address_line2?: string | null;
  city?: string | null;
  postal_code?: string | null;
  emergency_name?: string | null;
  emergency_relationship?: string | null;
  emergency_phone?: string | null;
  status: string;
  employment_type: string;
  province_of_employment: string;
  job_title?: string | null;
  department?: string | null;
  work_location?: string | null;
  branch_id?: string | null;
  branch_name?: string | null;
  entity_id?: string | null;          // D4: legal entity (group company)
  manager_name?: string | null;
  pay_type: string;
  annual_salary: number;
  hourly_rate: number;
  standard_hours_per_week?: number;
  pay_frequency: string;
  pay_periods_per_year: number;
  td1_federal_claim?: number | null;
  td1_provincial_claim?: number | null;
  bank_institution?: string | null;
  bank_transit?: string | null;
  bank_account_last4?: string | null;
  hire_date: string | null;
  // Stream A
  address_country?: string | null;
  country?: string | null;
  manager_id?: string | null;
  grade?: string | null;
  father_name?: string | null;
  spouse_name?: string | null;
  bank_account_masked?: string | null;
  has_bank_account?: boolean; has_sin?: boolean;
  is_self?: boolean;
}

export interface EmployeeCreate {
  employee_number: string;
  first_name: string;
  last_name: string;
  email?: string | null;
  date_of_birth?: string | null;
  gender?: string | null;
  preferred_name?: string | null;
  pronouns?: string | null;
  sin?: string | null;
  work_eligibility?: string | null;
  work_permit_expiry?: string | null;
  phone?: string | null;
  address_line1?: string | null;
  address_line2?: string | null;
  city?: string | null;
  postal_code?: string | null;
  emergency_name?: string | null;
  emergency_relationship?: string | null;
  emergency_phone?: string | null;
  hire_date?: string | null;
  employment_type?: string;
  province_of_employment?: string;
  job_title?: string | null;
  department?: string | null;
  work_location?: string | null;
  branch_id?: string | null;
  branch_name?: string | null;
  manager_name?: string | null;
  status?: string | null;
  pay_type?: string;
  annual_salary?: number | null;
  hourly_rate?: number | null;
  standard_hours_per_week?: number;
  pay_frequency?: string;
  td1_federal_claim?: number | null;
  td1_provincial_claim?: number | null;
  additional_tax_per_period?: number | null;
  bank_institution?: string | null;
  bank_transit?: string | null;
  bank_account_last4?: string | null;
  address_country?: string | null;
  // Stream A: identity fields (blank employee_number → next auto number)
  manager_id?: string | null;
  grade?: string | null;
  father_name?: string | null;
  spouse_name?: string | null;
  bank_account?: string | null;
}

export interface Deductions { cpp: number; cpp2: number; ei: number; income_tax: number; }
export interface Ytd { cpp: number; cpp2: number; ei: number; pensionable: number; insurable: number; }
export interface Paystub {
  gross: number;
  deductions: Deductions;
  employer_cost: { cpp: number; cpp2: number; ei: number };
  net_pay: number;
  ytd: Ytd;
}
export interface AnnualPreview {
  pay_frequency: string;
  periods_per_year: number;
  gross_per_period: number;
  annual_gross: number;
  year_end_ytd: Ytd;
  periods: Array<{ period: number; gross: number; cpp: number; cpp2: number; ei: number; income_tax: number; net: number }>;
}

// ── Auth ───────────────────────────────────────────────────────────────────
export const authApi = {
  login: (email: string, password: string, org_code?: string, captcha_token?: string) =>
    api.post<TokenResponse>("/auth/login", { email, password, org_code, captcha_token }).then((r) => r.data),
  /** WASA #7: the forced first-sign-in change returns fresh tokens (no pwchg). */
  changePassword: (current_password: string, new_password: string) =>
    api.post<{ ok: boolean; changed_at: string; access_token?: string; refresh_token?: string; org_id?: string; role?: string; password_change_required?: boolean }>(
      "/auth/change-password", { current_password, new_password }).then((r) => r.data),
  passwordPolicy: () => api.get<{ text: string; min_length?: number }>("/auth/password-policy").then((r) => r.data),
  me: () => api.get<User>("/auth/me").then((r) => r.data),
  registerOrg: (body: Record<string, unknown>) =>
    api.post<TokenResponse>("/auth/register-org", body).then((r) => r.data),
};

// ── Lexora platform admin (superadmin) ─────────────────────────────────────
export interface CountryMeta {
  label: string; currency: string; symbol: string; locale: string;
  tax_label: string; tax_id_label: string; region_label: string; regions: string[]; timezone: string;
}
export interface PlatformOrg {
  id: string; name: string; code?: string | null; plan: string; modules: string[]; explicit_modules?: string[] | null;
  country: string; province?: string | null; is_active: boolean; user_count: number;
  currency?: string; price?: number;
  addons?: string[]; agentic?: boolean;     // Agentic operations add-on (2026-10-25) — platform toggle
  admin_email?: string | null; created_at?: string | null;
}
export interface PlatformOrgs {
  orgs: PlatformOrg[]; total: number; total_users: number;
  all_modules: string[]; plans: Record<string, string[]>;
  countries?: Record<string, CountryMeta>; plan_pricing?: Record<string, Record<string, number>>;
}
export const platformApi = {
  orgs: () => api.get<PlatformOrgs>("/auth/platform/orgs").then((r) => r.data),
  createOrg: (body: { org_name: string; country?: string; province?: string; plan?: string; admin_email: string; admin_first_name?: string; admin_last_name?: string; admin_password: string }) =>
    api.post<any>("/auth/platform/orgs", body).then((r) => r.data),
  updateOrg: (id: string, body: { plan?: string; modules?: string[] | null; is_active?: boolean; agentic?: boolean }) =>
    api.patch<any>(`/auth/platform/orgs/${id}`, body).then((r) => r.data),
};

// ── Employees ────────────────────────────────────────────────────────────
export const employeeApi = {
  list: () => api.get<Employee[]>("/employees").then((r) => r.data),
  get: (id: string) => api.get<Employee>(`/employees/${id}`).then((r) => r.data),
  create: (body: EmployeeCreate) => api.post<Employee>("/employees", body).then((r) => r.data),
  update: (id: string, body: Record<string, any>) => api.patch<Employee>(`/employees/${id}`, body).then((r) => r.data),
  /** Colleagues' work facts for every role — no pay, ids or personal contact data (S1). */
  directory: () => api.get<DirectoryPerson[]>("/employees/directory").then((r) => r.data),
};

/** One row of GET /employees/directory (server allow-list: directory_routes.DIRECTORY_FIELDS). */
export interface DirectoryPerson {
  id: string; employee_number: string | null; first_name: string; last_name: string; preferred_name: string | null;
  display_name: string; initials: string; job_title: string | null; department: string | null; work_email: string | null;
  manager_id: string | null; manager_name: string | null; work_location: string | null; branch_name: string | null; is_self: boolean;
}

// ── Payroll ──────────────────────────────────────────────────────────────
export interface RunFlag { level: "error" | "warn" | "info"; code: string; message: string; }
export interface RunLine { employee_id: string; name: string; employee_number: string; province: string; gross: number; cpp: number; cpp2: number; ei: number; income_tax: number; net: number; flags?: RunFlag[]; payable?: PayableDays | null; }
export interface RunPreview { pay_frequency: string; periods_per_year: number; employee_count: number; lines: RunLine[]; totals: Record<string, number>; anomalies?: { errors: number; warnings: number };
  salary_basis?: { basis: "current" | "as_voided"; employees_at_voided_rates: number };
  // date rules: who is NOT paid in this period (and why) + the pro-ration basis in force
  excluded?: { employee_id: string; name: string; employee_number?: string; reason: string; reason_code: string }[];
  date_rules?: { basis: string; basis_label: string; period_known: boolean; settled_count?: number }; }
export interface PayableDays { paid: boolean; reason_code: string; reason: string; basis: string; basis_label: string; days_in_period: number; employed_days: number; lop_days: number; payable_days: number; prorated: boolean; review?: string[]; }
export interface RunEntitySummary { entity_id: string | null; name: string | null; short_code?: string | null; employees: number; gross?: number; net?: number; }
export interface RunVoidInfo { reason?: string | null; by?: string | null; at?: string | null; mode?: string | null; employees?: number; feed_rows_restored?: number; ytd_rows_reversed?: number; }
export interface PayRun { id: string; pay_frequency: string; status: string; period_start: string; period_end: string; pay_date: string; employee_count: number; total_gross: number; total_net: number;
  entity_id?: string | null; entity_name?: string | null; entities?: RunEntitySummary[] | null;   // D4
  compliance_mode?: "test" | "live" | null; void?: RunVoidInfo | null; can_void?: boolean; }       // void of a TEST run (2026-10-19)
export interface RunStub { stub_id: string; employee_id: string; name: string; employee_number: string; gross: number; cpp: number; cpp2: number; ei: number; income_tax: number; net_pay: number; entity_id?: string | null; entity_name?: string | null; }
export interface RunDetail extends PayRun { stubs: RunStub[]; }
// 422 body when POST /payroll/runs refuses: error-level anomalies always, warnings until acknowledged.
export interface RunRefusal { code: "PAYROLL_RUN_HAS_ERRORS" | "PAYROLL_RUN_HAS_WARNINGS" | string; message: string;
  errors: { employee_id: string; employee_number?: string; name: string; code: string; message: string }[];
  warnings: { employee_id: string; employee_number?: string; name: string; code: string; message: string }[]; }
export interface YtdRow { employee_id: string; name: string; employee_number: string; year: number; gross: number; income_tax: number; cpp: number; cpp2: number; ei: number; pensionable: number; insurable: number; vac_accrued?: number; vac_paid?: number; vac_balance?: number; }
export interface YtdLedger { year: number; caps: Record<string, number>; employees: YtdRow[]; }
export interface PayslipRow { stub_id: string; employee_id: string; name: string; period_end: string; pay_date: string; pay_frequency: string; gross: number; net_pay: number; }
// Per-payslip statutory ledger (CPP/EI/tax source records behind an employee's YTD)
export interface LedgerRow { stub_id: string; pay_date: string; gross: number; cpp: number; cpp2: number; ei: number; income_tax: number; net_pay: number; }

// Per-employee extra pay-components attached to a run (bonus/OT earnings, RRSP/dues deductions)
export interface RunEntryLine { code: string; amount: number; hours?: number; }
export interface RunEntry { earnings: RunEntryLine[]; deductions: RunEntryLine[]; }
export type RunEntries = Record<string, RunEntry>;

export const payrollApi = {
  calculate: (body: { gross_period: number; pay_frequency: string; province: string; ytd?: Ytd | null }) =>
    api.post<Paystub>("/payroll/calculate", body).then((r) => r.data),
  previewAnnual: (body: { annual_salary: number; pay_frequency: string; province: string }) =>
    api.post<AnnualPreview>("/payroll/preview-annual", body).then((r) => r.data),
  runPreview: (pay_frequency: string, entries?: RunEntries, periods_in_year?: number, year?: number, period_start?: string, period_end?: string, salary_basis?: "current" | "as_voided") =>
    api.post<RunPreview>("/payroll/runs/preview", { pay_frequency, entries, periods_in_year, year, period_start, period_end, salary_basis }).then((r) => r.data),
  runExecute: (body: { pay_frequency: string; period_start: string; period_end: string; pay_date: string; entries?: RunEntries; employee_ids?: string[]; acknowledge_unverified?: boolean; acknowledge_warnings?: boolean; salary_basis?: "current" | "as_voided"; periods_in_year?: number; year?: number }) =>
    api.post<PayRun>("/payroll/runs", body).then((r) => r.data),
  // Void a TEST run (never a live one): reverses the YTD ledger + feed, archives the stubs, lets the month be re-run.
  voidRun: (id: string, reason: string) => api.post<{ id: string; status: string; period_start: string; period_end: string; void: RunVoidInfo; warnings: string[] }>(`/payroll/runs/${id}/void`, { reason }).then((r) => r.data),
  runs: () => api.get<PayRun[]>("/payroll/runs").then((r) => r.data),
  runDetail: (id: string) => api.get<RunDetail>(`/payroll/runs/${id}`).then((r) => r.data),
  register: (id: string) => api.get<any>(`/payroll/runs/${id}/register`).then((r) => r.data),
  glJournal: (id: string) => api.get<any>(`/payroll/runs/${id}/gl-journal`).then((r) => r.data),
  remittance: (id: string) => api.get<any>(`/payroll/runs/${id}/remittance`).then((r) => r.data),
  t4: (year = 2026) => api.get<any>("/payroll/t4", { params: { year } }).then((r) => r.data),
  rl1: (year = 2026) => api.get<any>("/payroll/rl1", { params: { year } }).then((r) => r.data),
  roe: (employee_id: string, reason = "A", last_day?: string) => api.get<any>(`/payroll/roe/${employee_id}`, { params: { reason, last_day } }).then((r) => r.data),
  roeXml: (employee_id: string, reason = "A", last_day?: string) => api.get(`/payroll/roe/${employee_id}/xml`, { params: { reason, last_day }, responseType: "blob" }).then((r) => r.data as Blob),
  vacationOwed: (employee_id: string, year = 2026) => api.get<{ accrued: number; paid: number; balance: number }>(`/payroll/vacation-owed/${employee_id}`, { params: { year } }).then((r) => r.data),
  finalPay: (body: { employee_id: string; last_day: string; reason?: string; extra_earnings?: RunEntryLine[]; extra_deductions?: RunEntryLine[]; case_id?: string; acknowledge_unverified?: boolean }) =>
    api.post<any>("/payroll/final-pay", body).then((r) => r.data),
  remittanceSchedule: (year = 2026) => api.get<any>("/payroll/remittance-schedule", { params: { year } }).then((r) => r.data),
  compliance: () => api.get<any>("/payroll/compliance").then((r) => r.data),
  verification: () => api.get<any>("/payroll/verification").then((r) => r.data),
  eht: (year = 2026) => api.get<any>("/payroll/employer-health-tax", { params: { year } }).then((r) => r.data),
  offCycle: (body: { employee_id: string; pay_date: string; earnings: RunEntryLine[]; deductions: RunEntryLine[]; acknowledge_unverified?: boolean }) =>
    api.post<any>("/payroll/off-cycle", body).then((r) => r.data),
  offCycleList: () => api.get<any[]>("/payroll/off-cycle").then((r) => r.data),
  ytd: (year = 2026) => api.get<YtdLedger>("/payroll/ytd", { params: { year } }).then((r) => r.data),
  ytdPatch: (employee_id: string, body: Record<string, any>) =>
    api.patch(`/payroll/ytd/${employee_id}`, body).then((r) => r.data),
  payslips: (employee_id?: string) =>
    api.get<PayslipRow[]>("/payroll/payslips", { params: employee_id ? { employee_id } : {} }).then((r) => r.data),
  employeeLedger: (employee_id: string, year = 2026) =>
    api.get<LedgerRow[]>(`/payroll/employee-ledger/${employee_id}`, { params: { year } }).then((r) => r.data),
  payslipDetail: (stub_id: string) => api.get<any>(`/payroll/payslips/${stub_id}`).then((r) => r.data),
  payslipPdf: (stub_id: string, orgName?: string) =>
    api.get(`/payroll/payslips/${stub_id}/pdf`,
      { params: orgName ? { org: orgName } : {}, responseType: "blob" }).then((r) => r.data as Blob),
  payGroups: () => api.get("/payroll/pay-groups").then((r) => r.data),
  calendar: (params: { pay_frequency: string; first_pay_date?: string; year?: number }) =>
    api.get("/payroll/calendar", { params }).then((r) => r.data),
};

// ── Compliance (WCB/WSIB · safety register · tamper-evident audit) ──────────
export const complianceApi = {
  // Safety
  safety: (params?: Record<string, any>) => api.get<any>("/compliance/safety", { params }).then((r) => r.data),
  safetySummary: (year = 2026) => api.get<any>("/compliance/safety/summary", { params: { year } }).then((r) => r.data),
  createIncident: (body: Record<string, any>) => api.post<any>("/compliance/safety", body).then((r) => r.data),
  updateIncident: (id: string, body: Record<string, any>) => api.patch<any>(`/compliance/safety/${id}`, body).then((r) => r.data),
  // Audit
  audit: (params?: Record<string, any>) => api.get<any>("/compliance/audit", { params }).then((r) => r.data),
  auditVerify: () => api.get<any>("/compliance/audit/verify").then((r) => r.data),
  // WCB
  wcbRates: (year = 2026) => api.get<any>("/compliance/wcb/rates", { params: { year } }).then((r) => r.data),
  setWcbRate: (province: string, body: { rate_per_100: number; account_number?: string; year?: number }) =>
    api.put<any>(`/compliance/wcb/rates/${province}`, body).then((r) => r.data),
  wcbAssessment: (year = 2026) => api.get<any>("/compliance/wcb/assessment", { params: { year } }).then((r) => r.data),
};

// ── Module stubs (ping the stub services so the UI can show liveness) ──────
// ── Onboarding ─────────────────────────────────────────────────────────────
export interface OnboardingSession {
  id: string;
  status: string;                 // invited | submitted | approved | activated | cancelled
  employee_number?: string | null;
  first_name?: string | null;
  last_name?: string | null;
  email?: string | null;
  job_title?: string | null;
  current_step: string;
  steps_completed: string[];
  steps_total: number;
  progress_pct: number;
  employee_id?: string | null;
  invite_token?: string;
  data?: Record<string, any>;
  created_at?: string | null;
  invited_at?: string | null;
  invite_expires_at?: string | null;
  submitted_at?: string | null;
  activated_at?: string | null;
}

export interface SessionCreate {
  first_name: string;
  last_name: string;
  email?: string;
  employee_number?: string;
  job_title?: string;
  department?: string;
  work_location?: string;
  employment_type?: string;
  province_of_employment?: string;
  hire_date?: string;
  manager_name?: string;
  manager_id?: string;
  grade?: string;
  address_country?: string;
  pay_type?: string;
  annual_salary?: number;
  hourly_rate?: number;
  pay_frequency?: string;
  standard_hours_per_week?: number;
  invite_expiry_days?: number;
}

export const onboardingApi = {
  list: (status?: string) =>
    api.get<OnboardingSession[]>("/onboarding/sessions", { params: status ? { status } : {} }).then((r) => r.data),
  get: (id: string) => api.get<OnboardingSession>(`/onboarding/sessions/${id}`).then((r) => r.data),
  create: (body: SessionCreate) => api.post<OnboardingSession>("/onboarding/sessions", body).then((r) => r.data),
  cancel: (id: string) => api.patch(`/onboarding/sessions/${id}`, { status: "cancelled" }).then((r) => r.data),
  approve: (id: string) => api.post<OnboardingSession>(`/onboarding/sessions/${id}/approve`, {}).then((r) => r.data),
  activate: (id: string) => api.post<any>(`/onboarding/sessions/${id}/activate`).then((r) => r.data),
  consents: (id: string) => api.get<any[]>(`/onboarding/sessions/${id}/consents`).then((r) => r.data),
  documents: (id: string) => api.get<any[]>(`/onboarding/sessions/${id}/documents`).then((r) => r.data),
  // Token (self-service) flow — HR drives it via the session's invite_token.
  invite: (token: string) => api.get<OnboardingSession>(`/onboarding/invite/${token}`).then((r) => r.data),
  saveStep: (token: string, step: string, data: Record<string, any>, complete = true) =>
    api.patch<OnboardingSession>(`/onboarding/invite/${token}/step`, { step, data, complete }).then((r) => r.data),
  addConsent: (token: string, consent_type: string, signed_name?: string, version?: string) =>
    api.post(`/onboarding/invite/${token}/consents`, { consent_type, signed_name, version }).then((r) => r.data),
  addDocument: (token: string, doc_type: string, file_name?: string) =>
    api.post(`/onboarding/invite/${token}/documents`, { doc_type, file_name }).then((r) => r.data),
  submit: (token: string) => api.post<OnboardingSession>(`/onboarding/invite/${token}/submit`).then((r) => r.data),
};

// ── Time & Attendance ──────────────────────────────────────────────────────
export interface AttendanceRow {
  employee_id: string; name: string; employee_number?: string | null;
  province?: string | null; department?: string | null; pay_type?: string | null;
  planned_hours: number; actual_hours: number; status: string; late_minutes: number;
  first_in?: string | null; last_out?: string | null;
}
export interface AttendanceSummary {
  date: string; total: number; present: number; on_shift: number; late: number;
  absent: number; on_leave: number; rest_day: number; holiday: number; scheduled: number;
}
export interface AttendanceBoard { summary: AttendanceSummary; rows: AttendanceRow[]; }
export interface TimesheetLine {
  id?: string; work_date: string; reg_hours: number; ot_hours: number; ot2_hours?: number; stat_hours: number;
  cost_center?: string | null; note?: string | null;
}
export interface Timesheet {
  id: string; employee_id: string; employee_name?: string | null; employee_number?: string | null;
  manager_id?: string | null; manager_name?: string | null; province?: string | null; pay_type?: string | null;
  hourly_rate: number; pay_frequency?: string | null; period_start: string; period_end: string;
  reg_hours: number; ot_hours: number; ot2_hours?: number; stat_hours: number; stat_pay?: number;
  total_hours: number; status: string;
  submitted_at?: string | null; reviewed_at?: string | null; review_note?: string | null;
  locked_at?: string | null; lines?: TimesheetLine[];
}
export interface TimeRules {
  province: string; province_label: string; year: number; disclaimer: string;
  overtime: { summary: string; daily_ot_after: number | null; daily_double_after: number | null; weekly_ot_after: number; combine: string; banked_allowed: boolean };
  stat_holiday: { avg_wage_method: string; lookback_days: number; worked_premium: number; eligibility: string };
  breaks: { meal_after_hours: number; meal_minutes: number; daily_rest_hours: number; weekly_rest_hours: number };
}
export interface WorkSchedule {
  id: string; name: string; code?: string | null; days_of_week: number[];
  start_time: string; end_time: string; break_minutes: number; daily_hours: number;
  timezone: string; is_default: boolean; is_active: boolean;
}
export interface Regularization {
  id: string; employee_id: string; employee_name?: string | null; work_date: string;
  reason: string; requested_in?: string | null; requested_out?: string | null;
  note?: string | null; status: string; reviewed_at?: string | null;
}
export interface PayrollReadiness {
  pay_frequency: string; period_start: string; period_end: string; total_employees: number;
  ready: number; submitted: number; missing_count: number;
  missing: Array<{ employee_id: string; name: string; status: string }>;
  all_ready: boolean; coverage_pct: number;
}
export interface PayrollFeed {
  pay_frequency: string; period_start: string; period_end: string; employee_count: number;
  entries: RunEntries; hours: Record<string, { reg: number; ot: number; stat: number; total: number }>;
  employee_ids: string[];
}

export const timeApi = {
  // schedules
  schedules: () => api.get<WorkSchedule[]>("/time/schedules").then((r) => r.data),
  createSchedule: (body: Record<string, any>) => api.post<WorkSchedule>("/time/schedules", body).then((r) => r.data),
  seedSchedule: () => api.post("/time/schedules/seed-defaults").then((r) => r.data),
  assignments: () => api.get<any[]>("/time/assignments").then((r) => r.data),
  assign: (employee_id: string, schedule_id: string, effective_from?: string) =>
    api.put(`/time/assignments/${employee_id}`, { schedule_id, effective_from }).then((r) => r.data),
  // capture + attendance
  punch: (body: { employee_id: string; direction: string; source?: string; note?: string; at?: string }) =>
    api.post("/time/punch", body).then((r) => r.data),
  punches: (employee_id: string, day?: string) =>
    api.get<any[]>("/time/punches", { params: { employee_id, ...(day ? { day } : {}) } }).then((r) => r.data),
  board: (day?: string) => api.get<AttendanceBoard>("/time/attendance", { params: day ? { day } : {} }).then((r) => r.data),
  employeeAttendance: (employee_id: string, start?: string, end?: string) =>
    api.get<any>(`/time/attendance/${employee_id}`, { params: { ...(start ? { start } : {}), ...(end ? { end } : {}) } }).then((r) => r.data),
  // timesheets
  generate: (body: { period_start: string; period_end: string; pay_frequency?: string; employee_ids?: string[] }) =>
    api.post<any>("/time/timesheets/generate", body).then((r) => r.data),
  timesheets: (params?: { status?: string; employee_id?: string; period_end?: string }) =>
    api.get<Timesheet[]>("/time/timesheets", { params: params || {} }).then((r) => r.data),
  timesheet: (id: string) => api.get<Timesheet>(`/time/timesheets/${id}`).then((r) => r.data),
  editLines: (id: string, lines: TimesheetLine[]) =>
    api.patch<Timesheet>(`/time/timesheets/${id}/lines`, { lines }).then((r) => r.data),
  submitTimesheet: (id: string) => api.post<Timesheet>(`/time/timesheets/${id}/submit`).then((r) => r.data),
  approveTimesheet: (id: string, note?: string) => api.post<Timesheet>(`/time/timesheets/${id}/approve`, { note }).then((r) => r.data),
  rejectTimesheet: (id: string, note?: string) => api.post<Timesheet>(`/time/timesheets/${id}/reject`, { note }).then((r) => r.data),
  lockTimesheet: (id: string) => api.post<Timesheet>(`/time/timesheets/${id}/lock`).then((r) => r.data),
  // regularizations + approvals
  requestReg: (body: Record<string, any>) => api.post("/time/regularizations", body).then((r) => r.data),
  regularizations: (status?: string) =>
    api.get<Regularization[]>("/time/regularizations", { params: status ? { status } : {} }).then((r) => r.data),
  approveReg: (id: string) => api.post(`/time/regularizations/${id}/approve`).then((r) => r.data),
  rejectReg: (id: string) => api.post(`/time/regularizations/${id}/reject`).then((r) => r.data),
  approvals: () => api.get<{ timesheets: Timesheet[]; regularizations: Regularization[]; pending_total: number }>("/time/approvals").then((r) => r.data),
  // the payroll feed
  readiness: (pay_frequency: string, period_start: string, period_end: string) =>
    api.get<PayrollReadiness>("/time/readiness", { params: { pay_frequency, period_start, period_end } }).then((r) => r.data),
  payrollFeed: (pay_frequency: string, period_start: string, period_end: string) =>
    api.get<PayrollFeed>("/time/payroll-feed", { params: { pay_frequency, period_start, period_end } }).then((r) => r.data),
  // statutory rules reference
  rules: (province: string, year = 2026) =>
    api.get<TimeRules>("/time/rules", { params: { province, year } }).then((r) => r.data),
  // analytics + roster (Phase 4)
  analytics: (start?: string, end?: string) =>
    api.get<TimeAnalytics>("/time/analytics", { params: { ...(start ? { start } : {}), ...(end ? { end } : {}) } }).then((r) => r.data),
  roster: (start: string, end: string) =>
    api.get<RosterData>("/time/roster", { params: { start, end } }).then((r) => r.data),
  rosterAssign: (shifts: Array<{ employee_id: string; work_date: string; start_time?: string; end_time?: string; planned_hours?: number; location?: string; note?: string }>) =>
    api.post("/time/roster/assign", { shifts }).then((r) => r.data),
  rosterPublish: (start: string, end: string) => api.post("/time/roster/publish", { start, end }).then((r) => r.data),
  deleteShift: (id: string) => api.delete(`/time/roster/${id}`).then((r) => r.data),
  // employee self-service (My Time)
  myTimesheet: (period_start: string, period_end: string) =>
    api.get<{ employee_id: string; period_start: string; period_end: string; timesheet: Timesheet | null }>("/time/my-timesheet", { params: { period_start, period_end } }).then((r) => r.data),
  submitEntry: (body: { period_start: string; period_end: string; submit?: boolean; lines: Array<{ work_date: string; project_id: string; project_name?: string; billable?: boolean; hours: number; note?: string }> }) =>
    api.post<Timesheet>("/time/timesheets/entry", body).then((r) => r.data),
};

export interface TimeAnalytics {
  start: string; end: string; period_days: number;
  headline: { employees_tracked: number; attendance_days: number; avg_daily_hours: number; total_hours: number };
  absenteeism: { scheduled_days: number; absent_days: number; rate_pct: number; late_days: number; late_rate_pct: number; avg_late_min: number };
  overtime: { ot_hours: number; ot2_hours: number; employees_with_ot: number; avg_ot_hours: number; top: Array<{ employee_id: string; name: string; department?: string | null; ot_hours: number; spike: boolean }> };
  anomalies: { overtime: Array<{ employee_id: string; name: string; ot_hours: number }>; count: number };
  by_department: Array<{ department: string; present_days: number; hours: number; ot_hours: number; late: number }>;
  daily: Array<{ date: string; present: number; late: number; ot: number }>;
  upcoming_stat: { count: number; active_employees: number; holidays: Array<{ date: string; name: string }> };
}
export interface RosterShift {
  id: string; employee_id: string; work_date: string; start_time: string; end_time: string;
  planned_hours: number; location?: string | null; status: string; note?: string | null;
}
export interface RosterData {
  start: string; end: string;
  employees: Array<{ employee_id: string; name: string; employee_number?: string | null; department?: string | null }>;
  shifts: RosterShift[];
}

// ── Leave ──────────────────────────────────────────────────────────────────
export interface LeaveBalance {
  id: string; employee_id: string; fiscal_year: number; leave_type_code: string;
  leave_type_name?: string | null; paid: boolean; allocated: number; carried_over: number;
  entitled: number; used: number; pending: number; remaining: number;
  /** usable | event | unpaid (2026-10-16) */
  category?: string;
}
export interface LeaveRequest {
  id: string; employee_id: string; employee_name?: string | null; employee_number?: string | null;
  manager_id?: string | null; manager_name?: string | null; fiscal_year: number;
  leave_type_code: string; leave_type_name?: string | null; start_date: string; end_date: string;
  days: number; half_day: boolean; reason?: string | null; status: string;
  reviewed_by_name?: string | null; reviewed_at?: string | null; review_note?: string | null; created_at?: string | null;
  half_session?: string | null; cc_employee_ids?: string[]; contact_phone?: string | null; contact_note?: string | null;
  attachment_id?: string | null; cancel_reason?: string | null; cancel_requested_at?: string | null;
}
export interface LeaveSummary {
  fiscal_year: number; requests: number; by_status: Record<string, number>;
  days_by_type: Record<string, number>; employees_with_balances: number;
  total_allocated_days: number; total_used_days: number; pending: number;
}

export const leaveApi = {
  types: () => api.get<MasterRow[]>("/leave/types").then((r) => r.data),
  balances: (employee_id?: string, fiscal_year?: number) =>
    api.get<{ employee_id: string; fiscal_year: number; balances: LeaveBalance[]; totals?: { usable_remaining: number; usable_entitled: number; usable_used: number; usable_pending: number; event: { code: string; name: string; remaining: number; entitled: number }[] } }>("/leave/balances",
      { params: { ...(employee_id ? { employee_id } : {}), ...(fiscal_year ? { fiscal_year } : {}) } }).then((r) => r.data),
  allocate: (body: { fiscal_year?: number; employee_ids?: string[] }) =>
    api.post<any>("/leave/balances/allocate", body).then((r) => r.data),
  requests: (params?: { status?: string; employee_id?: string; scope?: string }) =>
    api.get<LeaveRequest[]>("/leave/requests", { params: params || {} }).then((r) => r.data),
  createRequest: (body: { employee_id?: string; leave_type_code: string; start_date: string; end_date: string; half_day?: boolean; reason?: string;
    half_session?: string; cc_employee_ids?: string[]; contact_phone?: string; contact_note?: string; attachment_id?: string }) =>
    api.post<LeaveRequest>("/leave/requests", body).then((r) => r.data),
  approveRequest: (id: string, note?: string) => api.post<LeaveRequest>(`/leave/requests/${id}/approve`, { note }).then((r) => r.data),
  rejectRequest: (id: string, note?: string) => api.post<LeaveRequest>(`/leave/requests/${id}/reject`, { note }).then((r) => r.data),
  cancelRequest: (id: string) => api.post<LeaveRequest>(`/leave/requests/${id}/cancel`).then((r) => r.data),
  approvals: () => api.get<{ pending: LeaveRequest[]; pending_total: number; days_total: number }>("/leave/approvals").then((r) => r.data),
  summary: (fiscal_year?: number) => api.get<LeaveSummary>("/leave/summary", { params: fiscal_year ? { fiscal_year } : {} }).then((r) => r.data),
};

export const pingApi = {
  onboarding: () => api.get("/onboarding/ping").then((r) => r.data).catch(() => null),
  time: () => api.get("/time/ping").then((r) => r.data).catch(() => null),
  leave: () => api.get("/leave/ping").then((r) => r.data).catch(() => null),
  approvals: () => api.get("/approvals/ping").then((r) => r.data).catch(() => null),
  ai: () => api.get("/ai/ping").then((r) => r.data).catch(() => null),
};

// ── Master data + roles ──────────────────────────────────────────────────
export interface MasterRow {
  id: string; name: string; code?: string | null; is_active: boolean;
  [k: string]: any;
}
export interface RoleDef {
  key: string; label: string; scope: string; description: string; permissions: string[];
}
export type MasterKind =
  | "departments" | "designations" | "grades" | "locations" | "leave-types"
  | "pay-components" | "fiscal-years" | "cost-centers" | "benefit-plans" | "stat-holidays";

export const mastersApi = {
  list: (kind: MasterKind, includeInactive = false) =>
    api.get<MasterRow[]>(`/masters/${kind}`, { params: includeInactive ? { include_inactive: true } : {} }).then((r) => r.data),
  create: (kind: MasterKind, body: Record<string, any>) =>
    api.post<MasterRow>(`/masters/${kind}`, body).then((r) => r.data),
  update: (kind: MasterKind, id: string, body: Record<string, any>) =>
    api.patch<MasterRow>(`/masters/${kind}/${id}`, body).then((r) => r.data),
  remove: (kind: MasterKind, id: string) => api.delete(`/masters/${kind}/${id}`).then((r) => r.data),
  roles: () => api.get<RoleDef[]>("/masters/roles").then((r) => r.data),
  summary: () => api.get<Record<string, number>>("/masters/summary").then((r) => r.data),
  seedDefaults: () => api.post("/masters/seed-defaults").then((r) => r.data),
};

// ── Operations / Staffing (branches, clients, projects, vendors, roster, invoices) ──
export type OpsKind = "branches" | "clients" | "projects" | "vendors" | "roster" | "invoices";
export const opsApi = {
  list: (kind: OpsKind) => api.get<any[]>(`/ops/${kind}`).then((r) => r.data),
  create: (kind: OpsKind, body: Record<string, any>) => api.post<any>(`/ops/${kind}`, body).then((r) => r.data),
  remove: (kind: OpsKind, id: string) => api.delete(`/ops/${kind}/${id}`).then((r) => r.data),
  summary: () => api.get<Record<string, number>>("/ops/summary").then((r) => r.data),
};

// ── AI platform ────────────────────────────────────────────────────────────
export interface ChatMessage { role: "user" | "assistant"; content: string; }
export interface Agent {
  key: string; name: string; domain: string; status: string;
  last_run: string | null; summary: string; findings: number; cadence: string;
}
export interface AgentRun {
  id: string; agent: string; agent_key: string; domain: string;
  ran_at: string; status: string; summary: string; findings: number;
}
export interface CommandLayer {
  domain: string; status: string; headline: string; detail: string; count: number;
}
export interface CommandBrief {
  generated_at: string; attention_score: number; headline: string;
  layers: CommandLayer[]; agents_online: number; runs_today: number;
}

export interface AiUsage {
  lexi_model: string; key_configured: boolean; mode: string;
  prices: Record<string, { input: number; output: number }>;
  totals: { input_tokens: number; output_tokens: number; total_tokens: number; cost_usd: number; calls: number };
  by_model: Array<{ model: string; tokens: number; cost: number; calls: number }>;
  by_feature: Array<{ feature: string; tokens: number; cost: number; calls: number }>;
  daily: Array<{ date: string; tokens: number; cost: number }>;
  recent: Array<{ feature: string; model: string; mode: string; input_tokens: number; output_tokens: number; cost_usd: number; created_at: string }>;
  agents: { count: number; runs: number; engine: string; tokens: number; cost_usd: number };
}

export const aiApi = {
  chat: (message: string, history: ChatMessage[]) =>
    api.post<{ reply: string; usage?: any }>("/ai/chat", { message, history }).then((r) => r.data),
  agents: () => api.get<Agent[]>("/ai/agents").then((r) => r.data),
  activity: (limit = 50) =>
    api.get<{ items: AgentRun[]; total: number }>("/ai/activity", { params: { limit } }).then((r) => r.data),
  commandCenter: () => api.get<CommandBrief>("/ai/command-center").then((r) => r.data),
  usage: () => api.get<AiUsage>("/ai/usage").then((r) => r.data),
};

// ── Domain constants (mirror shared/utils/enums.py) ────────────────────────
export const PAY_FREQUENCIES = [
  { value: "weekly", label: "Weekly", periods: 52 },
  { value: "biweekly", label: "Bi-weekly", periods: 26 },
  { value: "semi_monthly", label: "Semi-monthly", periods: 24 },
  { value: "monthly", label: "Monthly", periods: 12 },
];
// All 13 provinces / territories the payroll engine calculates (ON / BC / AB / QC first, then the rest).
export const PROVINCES = Object.entries(PROVINCE_NAMES).map(([value, label]) => ({ value, label }));
export const EMPLOYMENT_TYPES = ["full_time", "part_time", "contract", "seasonal"];
export const PAY_TYPES = ["salary", "hourly"];

// Currency: Canadian dollars, en-CA grouping ($1,234.56).
const _moneyLocale = { sym: "$", locale: "en-CA" };
export const money = (n: number) =>
  _moneyLocale.sym + (Number(n) || 0).toLocaleString(_moneyLocale.locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
// Current currency symbol / locale (for compact formatters like the dashboard KPIs).
export const moneySym = () => _moneyLocale.sym;
export const moneyLocaleName = () => _moneyLocale.locale;
export const periodsFor = (freq: string) =>
  PAY_FREQUENCIES.find((f) => f.value === freq)?.periods ?? 26;
export const freqLabel = (freq: string) =>
  PAY_FREQUENCIES.find((f) => f.value === freq)?.label ?? freq;

// ═══════════════════════════════════════════════════════════════════════════
// Stream U1 — Employee 360 profile page (/employees/:id). ONE aggregated read:
//   GET /employees/{uuid-or-number}/profile   (employee service, profile_routers.py)
// Sections the viewer may not read come back as null, never as a 403.
// ═══════════════════════════════════════════════════════════════════════════
/** The employee record as /employees/{id} returns it (every field, identifiers MASKED). */
export interface ProfileEmployee extends Employee {
  middle_name?: string | null; marital_status?: string | null; work_email?: string | null;
  home_phone?: string | null; province_of_residence?: string | null;
  work_permit_type?: string | null; work_permit_number?: string | null;
  probation_end_date?: string | null; termination_date?: string | null;
  fte_percent?: number | null; overtime_eligible?: boolean | null; vacation_percent?: number | null;
  division?: string | null; cost_center?: string | null; is_union?: boolean | null; union_local?: string | null;
  cpp_exempt?: boolean | null; ei_exempt?: boolean | null; additional_tax_per_period?: number | null;
  nominee_name?: string | null; nominee_relationship?: string | null; nominee_phone?: string | null;
  erasure_hold?: boolean; erased_at?: string | null; identifiers_encrypted?: boolean; masked?: boolean;
}
export interface ProfileBrief {
  id: string; employee_number: string; name: string; job_title?: string | null;
  department?: string | null; status?: string | null; work_email?: string | null;
}
export interface ProfilePermissions {
  scope: "all" | "team" | "self"; is_self: boolean; role: string;
  can_edit: boolean; can_hr_write: boolean; can_see_pay: boolean; can_see_assets: boolean;
  can_see_documents: boolean; can_manage_logins: boolean; can_start_exit: boolean;
}
export interface ProfileLogin { id: string; email: string; role: string; role_label: string; status: string; last_login: string | null; }
export interface EmployeeProfile {
  employee: ProfileEmployee;
  tenure: { years: number; months: number; days: number; text: string | null };
  permissions: ProfilePermissions;
  manager: ProfileBrief | null; manager_chain: ProfileBrief[]; reports: ProfileBrief[];
  compensation: any | null;            // same shape as GET /employees/{id}/compensation
  career: any | null;                  // same shape as GET /employees/{id}/career
  career_changes: any[] | null;        // same rows as GET /employees/{id}/career-changes
  letters: any[] | null; letters_count: number;
  documents_count: number | null;
  assets: any[] | null;                // same rows as GET /assets?employee_id=
  logins: ProfileLogin[] | null;
  prev: { id: string; employee_number: string } | null;
  next: { id: string; employee_number: string } | null;
}
export const profileApi = {
  get: (idOrNumber: string) =>
    api.get<EmployeeProfile>(`/employees/${encodeURIComponent(idOrNumber)}/profile`).then((r) => r.data),
};
