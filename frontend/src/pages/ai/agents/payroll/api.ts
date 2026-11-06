// Payroll Auditor (Canada, restored 2026-10-26) — /ai/agents/payroll-auditor/*. The server owns every rule (checks,
// thresholds, tiers, shadow mode, who may act, every statutory figure from the payroll engine); this screen shows the
// review pack and records the checker's decisions.
import api from "../../../../api/client";
import type { AgentRun, AgentTask, Proposal } from "../../../../api/agentic";

export type Severity = "critical" | "warning" | "info";
export type FindingStatus = "unexplained" | "explained" | "confirmed" | "dismissed" | "info";
export type PayFrequency = "weekly" | "biweekly" | "semi_monthly" | "monthly";

export interface Finding {
  id: string; run_id: string; pay_period: string; code: string; check: string; severity: Severity; rank: number;
  employee_id: string | null; employee_ref: string | null; department: string | null; cost_centre: string | null; component: string | null;
  prev: number | null; curr: number | null; delta_cad: number | null; delta_pct: number | null; rule: string; threshold: Record<string, any>;
  linked_event: Record<string, any> | null; evidence_refs: { label?: string; link?: string }[]; fix_hint: string | null; owner_role: string | null;
  explanation: string | null; explained_by: "template" | "model" | "person" | null; verified: boolean | null; dollars_at_risk: number | null;
  status: FindingStatus; checker_note: string | null; decided_by: string | null; decided_at: string | null; value_event_id: string | null;
  task_id: string | null; created_at: string | null; title: string;
}
export interface Totals { gross: number; deductions: number; net: number; statutory: number; income_tax: number; headcount: number }
export interface CheckRow { code: string; name: string; findings: number; critical: number; warning: number; info: number; status: string; note: string | null }
export interface PackSummary {
  headline: string; pay_period: string; pay_frequency: PayFrequency | null; period_start: string | null; period_end: string | null; pay_date: string | null;
  prev_period: string | null; handled: number; needs_attention: number; critical: number; blocking?: number; warnings: number; info: number;
  unexplained: number; explained: number; explained_by_model: number; dollars_at_risk: number;
  projected: { this_period: Totals; last_period: Totals | null; delta: Record<keyof Totals, number> | null; delta_pct: Record<keyof Totals, number | null> | null };
  attendance: { tracked: boolean; locked: boolean; ready: number; total: number; locked_at: string | null };
  draft_source: string | null; sources: Record<string, string>;
  statutory: { source: string | null; year: number | null; edition_found: boolean | null; editions: number[]; plans: string[]; verified: number; tracked: number;
    roc: Record<string, Record<string, number>> | null; qc: Record<string, Record<string, number>> | null;
    vacation_min: Record<string, { source: string; tiers: [number, number][]; verified: boolean }>; findings: string[]; note: string | null };
  tieout: { register?: Record<string, number>; gl?: Record<string, any> }; checks: CheckRow[];
  timing: { lead_days: number | null; freeze_by: string | null; approve_by: string | null; attendance_locked_at?: string | null; draft_at?: string | null };
  gates?: { blocking: number; blocking_codes: string[]; critical_unexplained: number; warnings: number; attendance_tracked: boolean; attendance_locked: boolean; can_lock: boolean; can_send: boolean; can_release: boolean; why_not: string | null };
  chase?: { proposed: number; opened: number; deduplicated: number; skipped_no_owner: number }; would_open?: { owner_role: string; title: string; code: string; employee_ref: string | null }[];
  thresholds?: Record<string, number | string>; model?: { model: string | null; rephrased: number; rejected: number; fallback: string | null };
  waiting_on?: string[]; shadow_note?: string | null; grid_status?: Record<FindingStatus, number>; confirmed_dollars?: number;
  inputs_locked_at?: string | null; sent_for_approval_at?: string | null; release_ready_at?: string | null; [k: string]: any;
}
export interface Pack {
  run: AgentRun; mode: "shadow" | "live"; shadow: boolean; summary: PackSummary; grid: Finding[]; waiting_on: (AgentTask & { age_hours: number | null })[];
  would_have_done: Proposal[]; checks: [string, string][]; statutory: PackSummary["statutory"]; tieout: PackSummary["tieout"]; timing: PackSummary["timing"];
}
export interface Status {
  state: { enabled: boolean; mode: "shadow" | "live"; shadow_cycles_done: number; can_go_live: boolean; live_gate_note: string; addon_purchased: boolean; paused: boolean };
  pay_frequency: PayFrequency; pay_frequencies: PayFrequency[];
  period: string; period_start: string; period_end: string; pay_date: string; prev_period: string; today: string; freeze_by: string; approve_by: string;
  attendance: { tracked: boolean; locked: boolean; ready: number; total: number; missing: string[] };
  previous_run: { id: string; period_start: string; period_end: string; pay_date: string; status: string } | null;
  engine_run: { id: string; period_start: string; period_end: string; pay_date: string; status: string } | null;
  latest_run: AgentRun | null; thresholds: Record<string, any>; checks: { code: string; name: string }[]; can_edit: boolean; headcount: number;
}
export interface Thresholds { value: Record<string, any>; own: Record<string, any>; defaults: Record<string, any>; presets: Record<string, number>; can_edit: boolean }

const P = "/ai/agents/payroll-auditor";
const d = <T,>(p: Promise<{ data: T }>) => p.then((r) => r.data);

export const payrollAuditorApi = {
  status: (period?: string, pay_frequency?: string) => d(api.get<Status>(`${P}/status`, { params: { period: period || undefined, pay_frequency: pay_frequency || undefined } })),
  run: (pay_period?: string, pay_frequency?: string) => d(api.post<AgentRun & { chase: any; gates: any; proposals_made: string[] }>(`${P}/run`, { pay_period: pay_period || null, pay_frequency: pay_frequency || null })),
  periods: () => d(api.get<{ items: { pay_period: string; run_id: string; mode: string; started_at: string | null; headline: string | null }[] }>(`${P}/periods`)),
  // a period with no auditor run yet answers {empty: true} (200) → null here
  pack: (p: { period?: string; run_id?: string }) => d(api.get<Pack | { empty: true; message: string }>(`${P}/pack`, { params: { period: p.period || undefined, run_id: p.run_id || undefined } }))
    .then((x) => ("empty" in x ? null : x)),
  confirm: (id: string, note = "") => d(api.post<Finding>(`${P}/findings/${id}/confirm`, { note })),
  dismiss: (id: string, reason: string) => d(api.post<Finding>(`${P}/findings/${id}/dismiss`, { reason })),
  note: (id: string, text: string) => d(api.post<Finding>(`${P}/findings/${id}/note`, { text })),
  explain: (id: string, text: string) => d(api.post<Finding>(`${P}/findings/${id}/explain`, { text })),
  thresholds: () => d(api.get<Thresholds>(`${P}/thresholds`)),
  saveThresholds: (value: Record<string, any>) => d(api.put<{ saved: boolean; value: Record<string, any>; own: Record<string, any> }>(`${P}/thresholds`, { value })),
};

export const CHECK_LABEL: Record<string, string> = {
  "PA-01": "Headcount", "PA-02": "Joiner data", "PA-03": "Final pay / ROE", "PA-04": "Unpaid leave", "PA-05": "Variance", "PA-06": "Cost centre",
  "PA-07": "Net pay", "PA-08": "Minimum wage", "PA-09": "Duplicates", "PA-10": "Retro pay", "PA-11": "Tie-out", "PA-12": "Timing",
  "PA-13": "CPP / QPP · EI · QPIP", "PA-14": "YTD maximums", "PA-15": "YMPE / YAMPE", "PA-16": "Income tax · TD1", "PA-17": "Vacation pay",
};
export const VARIANCE_CODES = new Set(["PA-05", "PA-06", "PA-16"]);
export const FREQ_LABEL: Record<string, string> = { weekly: "Weekly", biweekly: "Bi-weekly", semi_monthly: "Semi-monthly", monthly: "Monthly" };

/** "biweekly:2026-10-04..2026-10-17" → "Bi-weekly · 2026-10-04 → 2026-10-17" */
export const periodLabel = (key: string | null | undefined) => {
  if (!key) return "—";
  const [f, rng] = key.split(":");
  const [a, b] = (rng || "").split("..");
  return `${FREQ_LABEL[f] || f} · ${a} → ${b}`;
};

/** CAD in en-CA with cents: $1,234.56 (−$1,234.56). */
export const money = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : (v < 0 ? "−" : "") + Math.abs(v).toLocaleString("en-CA", { style: "currency", currency: "CAD", minimumFractionDigits: 2, maximumFractionDigits: 2 });
