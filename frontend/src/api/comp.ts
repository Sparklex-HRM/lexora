import api from "./client";

/** Stream G2 — incentive plans (/payroll/incentives/*) and the ESOP ledger (/payroll/esop/*). */

const I = "/payroll/incentives";
const E = "/payroll/esop";
const q = (params: Record<string, any>) =>
  Object.fromEntries(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ""));
const d = <T = any>(p: Promise<{ data: T }>) => p.then((r) => r.data);

export const incentivesApi = {
  plans: () => d<any[]>(api.get(`${I}/plans`)),
  createPlan: (body: any) => d(api.post(`${I}/plans`, body)),
  updatePlan: (id: string, body: any) => d(api.put(`${I}/plans/${id}`, body)),
  deactivatePlan: (id: string) => d(api.delete(`${I}/plans/${id}`)),
  eligible: (id: string) => d<any[]>(api.get(`${I}/plans/${id}/eligible`)),

  enrolments: (params: { plan_id?: string; employee_id?: string } = {}) => d<any[]>(api.get(`${I}/enrolments`, { params: q(params) })),
  enrol: (body: { plan_id: string; employee_ids?: string[]; all_eligible?: boolean; target_value?: number | null; target_payout?: number | null; from_date?: string; to_date?: string }) =>
    d(api.post(`${I}/enrolments`, body)),
  editEnrolment: (id: string, body: { target_value?: number | null; target_payout?: number | null; to_date?: string | null }) => d(api.put(`${I}/enrolments/${id}`, body)),
  endEnrolment: (id: string) => d(api.delete(`${I}/enrolments/${id}`)),

  metrics: (plan_id: string, period: string) => d(api.get(`${I}/metrics`, { params: { plan_id, period } })),
  saveMetrics: (body: { plan_id: string; period: string; rows: { employee_id: string; value: number | null; note?: string }[]; source?: string }) =>
    d(api.put(`${I}/metrics`, body)),
  importMetrics: (plan_id: string, period: string, file: File) => {
    const fd = new FormData(); fd.append("file", file);
    return d(api.post(`${I}/metrics/import`, fd, { params: { plan_id, period }, headers: { "Content-Type": "multipart/form-data" } }));
  },
  metricsTemplate: (plan_id: string) => api.get(`${I}/metrics/template.csv`, { params: { plan_id }, responseType: "blob" }).then((r) => r.data as Blob),
  pullMetrics: (plan_id: string, period: string) => d(api.post(`${I}/metrics/pull`, { plan_id, period })),

  previewRun: (body: { plan_id: string; period: string; pay_month?: string }) => d(api.post(`${I}/runs/preview`, body)),
  createRun: (body: { plan_id: string; period: string; pay_month?: string }) => d(api.post(`${I}/runs`, body)),
  runs: (params: { status?: string; plan_id?: string } = {}) => d<any[]>(api.get(`${I}/runs`, { params: q(params) })),
  run: (id: string) => d(api.get(`${I}/runs/${id}`)),
  override: (run_id: string, pid: string, body: { amount?: number | null; reason?: string; exclude?: boolean }) => d(api.put(`${I}/runs/${run_id}/proposals/${pid}`, body)),
  submitRun: (id: string) => d(api.post(`${I}/runs/${id}/submit`)),
  decideRun: (id: string, decision: "approve" | "reject" | "cancel", note?: string) => d(api.post(`${I}/runs/${id}/${decision}`, { note })),
  runEntries: (pay_month: string) => d(api.get(`${I}/run-entries`, { params: { pay_month } })),
  forecast: (employee_id?: string) => d(api.get(`${I}/forecast`, { params: q({ employee_id }) })),
};

export const esopApi = {
  plans: () => d<any[]>(api.get(`${E}/plans`)),
  createPlan: (body: any) => d(api.post(`${E}/plans`, body)),
  updatePlan: (id: string, body: any) => d(api.put(`${E}/plans/${id}`, body)),
  capTable: () => d(api.get(`${E}/cap-table`)),
  grants: (params: { plan_id?: string; employee_id?: string; mine?: number } = {}) => d<any[]>(api.get(`${E}/grants`, { params: q(params) })),
  grant: (id: string) => d(api.get(`${E}/grants/${id}`)),
  createGrant: (body: { plan_id: string; employee_id: string; grant_date: string; quantity: number; exercise_price?: number | null; note?: string; issue_letter?: boolean }) =>
    d(api.post(`${E}/grants`, body)),
  accelerate: (id: string, body: { quantity: number; reason: string; on_date?: string }) => d(api.post(`${E}/grants/${id}/accelerate`, body)),
  cancelGrant: (id: string, reason: string) => d(api.post(`${E}/grants/${id}/cancel`, { reason })),
  letter: (id: string, kind: "grant" | "statement") => d(api.post(`${E}/grants/${id}/letter`, { kind })),
  vestDue: () => d(api.post(`${E}/vest-due`)),
  exercises: (params: { status?: string; mine?: number } = {}) => d<any[]>(api.get(`${E}/exercises`, { params: q(params) })),
  requestExercise: (body: { grant_id: string; quantity: number; note?: string }) => d(api.post(`${E}/exercises`, body)),
  setFmv: (id: string, body: { fmv: number; fmv_source: string; fmv_date: string; pay_month?: string }) => d(api.put(`${E}/exercises/${id}/fmv`, body)),
  decideExercise: (id: string, decision: "approve" | "reject" | "cancel", note?: string) => d(api.post(`${E}/exercises/${id}/${decision}`, { note })),
  exits: () => d<any[]>(api.get(`${E}/exits`)),
  processExits: () => d(api.post(`${E}/process-exits`)),
  me: (employee_id?: string) => d(api.get(`${E}/me`, { params: q({ employee_id }) })),
};

export const PERIOD_TYPES = [{ value: "monthly", label: "Monthly" }, { value: "quarterly", label: "Quarterly" }, { value: "annual", label: "Annual" }];
export const FORMULA_TYPES = [
  { value: "fixed", label: "Fixed amount per period" },
  { value: "pct_of_base", label: "% of base (salary / basic / gross)" },
  { value: "slab", label: "Slab table on a metric" },
  { value: "target", label: "Target vs achievement %" },
];
export const METRIC_SOURCES = [
  { value: "manual", label: "Typed in by HR" }, { value: "csv", label: "CSV upload" },
  { value: "psa_utilisation", label: "Billable utilisation % (Project Services)" },
  { value: "performance_rating", label: "Published appraisal score (Performance)" },
];
export const BASES = [{ value: "annual_ctc", label: "Annual salary" }, { value: "monthly_basic", label: "Monthly Basic" }, { value: "monthly_gross", label: "Monthly gross" }];
export const TIMINGS = [{ value: "with_payroll", label: "With the period's last salary" }, { value: "next_month", label: "The month after the period" }, { value: "off_cycle", label: "Off-cycle (posted by payroll)" }];
export const PRORATION = [{ value: "days", label: "By days served" }, { value: "full_month", label: "Full months only" }, { value: "none", label: "No proration (must be employed at period end)" }];
