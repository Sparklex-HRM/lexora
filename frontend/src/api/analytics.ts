import api from "./client";

// Stream D7 — engagement depth (/engage/survey-schedules, /engage/survey-bank, heatmaps, insights, budgets),
// headcount planning (/engage/positions), people analytics (/engage/analytics), attrition extras (/ai/attrition/*).

export interface SurveySchedule {
  id: string; title: string; kind: string; cadence: string; weekday: number; day_of_month: number; open_days: number; questions: any[]; anonymous: boolean;
  target: Record<string, any>; threshold?: number | null; active: boolean; next_run_on?: string | null; last_run_on?: string | null; last_survey_id?: string | null; runs: number; created_by?: string | null; created_at?: string | null;
}
export interface SurveyBankQ { id: string; tag: string; type: string; text: string; options: string[]; active: boolean; }
export interface HeatRow { segment: string; responses: number; hidden: boolean; scores: Record<string, number | null>; }
export interface ManagerInsights {
  manager_id: string; manager_name?: string | null; threshold: number; team_size: number;
  surveys: { survey_id: string; title: string; kind: string; date?: string | null; team_responses: number; participation_pct: number | null; hidden: boolean; score: number | null; company_score: number | null }[];
  recognition: { given: number; given_points: number; received_by_team: number; team_members_recognised: number; team_members_not_recognised: number };
  budget: { month: string; points_budget: number; points_used: number; remaining: number }; team: { id: string; name?: string | null }[]; window_days: number;
}
export interface KudosBudget { id: string; manager_id: string; manager_name?: string | null; month: string; points_budget: number; points_used: number; remaining: number; note?: string | null; updated_by?: string | null; updated_at?: string | null; }
export interface Position {
  id: string; code: string; title: string; grade?: string | null; department?: string | null; entity?: string | null; location?: string | null; fiscal_year?: string | null;
  status: string; employee_id?: string | null; employee_name?: string | null; filled_on?: string | null; requisition_id?: string | null; requisition_number?: string | null;
  manager_id?: string | null; manager_name?: string | null; notes?: string | null; budgeted_ctc?: number; actual_ctc?: number | null; over_budget?: boolean; created_at?: string | null; updated_at?: string | null;
}
export interface BudgetRow { group: string; open: number; filled: number; frozen: number; total: number; budgeted?: number; actual?: number; open_budget?: number; filled_budget?: number; variance?: number; fill_pct: number | null; }
export interface RetentionAction {
  id: string; employee_id: string; employee_name?: string | null; department?: string | null; action: string; kind: string; owner_name?: string | null; due_on?: string | null;
  status: string; outcome?: string | null; risk_score_at?: number | null; risk_band_at?: string | null; created_by?: string | null; created_at?: string | null; closed_at?: string | null;
}
export interface PeopleAnalytics {
  as_of: string; months: number; headcount: number; series: { month: string; headcount: number; joiners: number; leavers: number }[]; joiners_12m: number; leavers_12m: number; attrition_12m_pct: number | null;
  attrition_by_department: AttrRow[]; attrition_by_tenure: AttrRow[]; attrition_by_manager: AttrRow[];
  gender_by_department: { group: string; total: number; by_gender: Record<string, number> }[];
  span_of_control: { managers: number; average: number | null; median: number | null; wide_at: number; wide: number; rows: { manager_id: string; manager_name: string; reports: number; wide: boolean }[] };
  leavers_without_date: number; shows_money: boolean;
  overtime_by_month?: OtRow[]; overtime_by_department?: OtRow[];
  leave_liability?: { rows: { employee_id: string; employee_name?: string | null; department?: string | null; days: number; amount?: number; types: { leave_type: string; days: number; amount?: number }[] }[]; total_days: number; total_amount?: number; divisor: number; method: string; fiscal_year: number; balances_found: number };
}
export interface AttrRow { group: string; headcount: number; leavers: number; attrition_pct: number | null; }
export interface OtRow { group: string; ot_hours: number; sheets: number; people: number; avg_per_person: number; }
export interface Calibration { as_of: string; available: boolean; exits: number; needed: number; people: number; model: any; message?: string; overall_exit_rate?: number; factors?: { factor: string; flagged: number; exit_rate_flagged: number | null; exit_rate_not_flagged: number | null; lift: number | null }[]; bands?: { band: string; people: number; left: number; exit_rate: number }[]; }

const d = <T,>(p: Promise<{ data: T }>) => p.then((r) => r.data);

export const engage2Api = {
  schedules: () => d(api.get<{ schedules: SurveySchedule[]; cadences: string[] }>("/engage/survey-schedules")),
  createSchedule: (body: Record<string, any>) => d(api.post<SurveySchedule>("/engage/survey-schedules", body)),
  updateSchedule: (id: string, body: Record<string, any>) => d(api.patch<SurveySchedule>(`/engage/survey-schedules/${id}`, body)),
  deleteSchedule: (id: string) => d(api.delete(`/engage/survey-schedules/${id}`)),
  runDue: () => d(api.post<{ opened: number }>("/engage/survey-schedules/run-due")),
  bank: (tag?: string) => d(api.get<{ questions: SurveyBankQ[]; tags: string[]; types: string[] }>("/engage/survey-bank", { params: tag ? { tag } : {} })),
  addBank: (body: { tag: string; type: string; text: string; options?: string[] }) => d(api.post<SurveyBankQ>("/engage/survey-bank", body)),
  updateBank: (id: string, body: Record<string, any>) => d(api.patch<SurveyBankQ>(`/engage/survey-bank/${id}`, body)),
  deleteBank: (id: string) => d(api.delete(`/engage/survey-bank/${id}`)),
  heatmap: (surveyId: string, by: "department" | "manager") => d(api.get<{ survey: any; by: string; questions: { key: string; text: string; type: string }[]; rows: HeatRow[]; unstamped: number; note: string }>(`/engage/surveys/${surveyId}/heatmap`, { params: { by } })),
  managerInsights: (managerId?: string) => d(api.get<ManagerInsights>("/engage/insights/manager", { params: managerId ? { manager_id: managerId } : {} })),
  enpsTrend: () => d(api.get<{ points: { survey_id: string; title: string; date: string; responses: number; score: number | null; visible: boolean; participation_pct: number | null; delta: number | null }[]; headcount: number }>("/engage/insights/enps")),
  budgets: (month?: string) => d(api.get<{ month: string; budgets: KudosBudget[]; default_points: number; points_per_kudos: number; can_edit: boolean }>("/engage/kudos-budgets", { params: month ? { month } : {} })),
  putBudget: (body: { manager_id: string; month: string; points_budget: number; note?: string }) => d(api.put<KudosBudget>("/engage/kudos-budgets", body)),
  leaderboard: (month?: string) => d(api.get<{ month: string; top: { employee_id: string; name?: string | null; points: number; count: number }[] }>("/engage/kudos-leaderboard", { params: month ? { month } : {} })),
  runOnboarding: () => d(api.post<{ sent: number }>("/engage/onboarding-surveys/run")),
};

export const positionsApi = {
  list: (p: { status?: string; department?: string } = {}) => d(api.get<{ positions: Position[]; counts: Record<string, number>; shows_money: boolean; can_edit: boolean }>("/engage/positions", { params: p })),
  create: (body: Record<string, any>) => d(api.post<Position>("/engage/positions", body)),
  update: (id: string, body: Record<string, any>) => d(api.patch<Position>(`/engage/positions/${id}`, body)),
  remove: (id: string) => d(api.delete(`/engage/positions/${id}`)),
  fill: (id: string, body: { employee_id: string; actual_ctc?: number; filled_on?: string }) => d(api.post<Position>(`/engage/positions/${id}/fill`, body)),
  vacate: (id: string) => d(api.post<Position>(`/engage/positions/${id}/vacate`)),
  link: (id: string, requisition_id: string | null) => d(api.post<Position>(`/engage/positions/${id}/link-requisition`, { requisition_id })),
  requisitions: () => d(api.get<{ requisitions: { id: string; req_number: string; designation: string; department?: string | null; grade?: string | null; location?: string | null; openings: number; status: string; hiring_manager_name?: string | null; target_date?: string | null }[]; note: string }>("/engage/positions/requisitions")),
  summary: (by = "department") => d(api.get<{ by: string; rows: BudgetRow[]; total: BudgetRow; shows_money: boolean; currency: string }>("/engage/positions/summary", { params: { by } })),
  seed: () => d(api.post<{ created: number }>("/engage/positions/seed-from-employees")),
};

export const analyticsApi = {
  people: (months = 12) => d(api.get<PeopleAnalytics>("/engage/analytics/people", { params: { months } })),
  actions: (p: { status?: string; employee_id?: string } = {}) => d(api.get<{ actions: RetentionAction[]; kinds: string[] }>("/engage/analytics/retention-actions", { params: p })),
  addAction: (body: { employee_id: string; action: string; kind?: string; owner_name?: string; due_on?: string; risk_score_at?: number; risk_band_at?: string }) => d(api.post<RetentionAction>("/engage/analytics/retention-actions", body)),
  updateAction: (id: string, body: Record<string, any>) => d(api.patch<RetentionAction>(`/engage/analytics/retention-actions/${id}`, body)),
  calibration: () => d(api.get<Calibration>("/ai/attrition/calibration")),
  one: (employeeId: string) => d(api.get<any>(`/ai/attrition/${employeeId}`)),
};
