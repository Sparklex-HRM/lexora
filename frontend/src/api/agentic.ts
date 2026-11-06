// Agentic operations add-on (2026-10-25) — /ai/agents/*: overview, approvals inbox, tasks, runs / shadow
// reports, action log + chain verify, value ledger, settings. Server owns every rule (tiers, two-person
// rule, shadow mode, approver identity from the session); the screen only shows and asks.
import api from "./client";

export type AgentKey = "payroll_auditor" | "hr_desk" | "onboarding" | "exit" | "attendance";   // + lifecycle agents 2026-10-26 · Payroll Auditor (Canada) restored 2026-10-26
export type Mode = "shadow" | "live";

export interface AgentState {
  enabled: boolean; addon_purchased: boolean; paused: boolean; mode: Mode; shadow_cycles_done: number;
  can_go_live: boolean; live_gate_note: string; live_after_shadow_cycles: number;
}
export interface AgentRun {
  id: string; agent: AgentKey; agent_role: string; agent_version: string; trigger: string; mode: Mode; status: "running" | "done" | "error" | string;
  started_at: string | null; finished_at: string | null; duration_ms: number; summary: Record<string, any>; checks_run: number; findings: number;
  proposals: number; tasks_opened: number; input_tokens: number; output_tokens: number; cost_usd: number; cost_cad: number | null; error: string | null;
}
export interface AgentCard {
  key: AgentKey; role_name: string; does: string; you_approve: string; triggers: string[]; tier_map: Record<string, { tier: number; label: string }>;
  never: string[]; version: string; executors_registered: string[]; mode: Mode; last_run: AgentRun | null; open_tasks: number; inbox: number;
  value_this_month: { proxy_minutes: number; avoided_dollars: number; runs: number };
  link?: string;   // the agent's own workspace route (phase 2), "/ai/agents" when it has none yet
}
export interface Overview { state: AgentState; agents: AgentCard[]; inbox_total: number; open_tasks_total: number; can_edit: boolean; as_of: string }

export interface Proposal {
  id: string; agent: AgentKey; agent_role: string; run_id: string | null; tool_name: string; tool_version: string; tool_args: Record<string, any>;
  plain_action: string; before: Record<string, any> | null; after: Record<string, any> | null; aggregate_impact: { headcount?: number; dollars?: number };
  reason: string; evidence_refs: { label?: string; link?: string; source?: string; hash?: string }[]; confidence: string; base_tier: number; risk_tier: number;
  tier_label: string; policy_id: string; policy_decision: string; untrusted_input: boolean; mode: Mode; status: string; needs_second_approval: boolean;
  requested_by_user_id: string | null; approver_user_id: string | null; approver_email: string | null; approver_role: string | null;
  second_approver_user_id: string | null; second_approver_email: string | null; approved_for_session: boolean; edited_input_diff: Record<string, any> | null;
  deny_message: string | null; reversal_plan: Record<string, any> | null; result: Record<string, any> | null; created_at: string | null; expires_at: string | null;
  first_decision_at: string | null; decided_at: string | null; executed_at: string | null; message?: string; tier_reasons?: string[];
}
export interface AgentTask {
  id: string; agent: AgentKey; agent_role: string; kind: string; employee_id: string | null; run_ref: string | null; filing_id: string | null; title: string;
  detail: { what?: string; why?: string; how_to_fix?: string; link?: string } & Record<string, any>; owner_user_id: string | null; owner_employee_id: string | null;
  due_at: string | null; status: "open" | "waiting" | "done" | "escalated" | "cancelled" | string; chase_count: number; last_chased_at: string | null;
  next_chase_at: string | null; escalation_to: { roles?: string[]; user_ids?: string[] } | null; escalated_at: string | null; created_by_run: string | null;
  result: Record<string, any> | null; completed_by: string | null; completed_at: string | null; created_at: string | null; overdue: boolean;
}
export interface ShadowReport {
  run: AgentRun; would_have_done: Proposal[]; tasks: AgentTask[];
  counts: { proposals: number; shadow: number; by_tier: Record<string, number>; dollars: number; headcount: number };
}
export interface LogEvent {
  event_id: string; seq: number; prev_event_hash: string; event_hash: string; event_type: string; ts_utc: string | null; agent_name: string | null;
  run_id: string | null; tool_name: string | null; tool_input: any; risk_tier: number | null; policy_decision: string | null; approval_id: string | null;
  approver_user_id: string | null; second_approver_user_id: string | null; decision: string | null; decision_latency_ms: number | null;
  deny_message: string | null; input_tokens: number; output_tokens: number; cost_usd: number | null; [k: string]: any;
}
export interface ChainVerify { ok: boolean; checked: number; first_bad_seq: number | null; reason: string | null; head_hash: string; as_of: string }
export interface ValueRow { id: string; agent: AgentKey; kind: string; units: number; dollars_avoided: number | null; basis: string | null; confirmed: boolean; confirmed_at: string | null; at: string | null; note: string | null; run_id: string | null; proposal_id: string | null }
export interface ValueSummary {
  from: string; to: string; mode: Mode; enabled: boolean;
  measured: { runs: number; runs_done: number; shadow_runs: number; avg_run_close_minutes: number | null; proposals_decided: number; accepted: number; denied: number;
    accept_without_edit_pct: number | null; avg_approval_latency_minutes: number | null; median_approval_latency_minutes: number | null; tasks_closed: number };
  proxy: { minutes: number; hours: number; rows: { kind: string; units: number; minutes_each: number; minutes: number }[]; minutes_table: Record<string, number>; note: string };
  avoided: { dollars: number; items: number; unconfirmed_dollars: number; unconfirmed_items: number; rows: ValueRow[]; note: string };
  cost: { input_tokens: number; output_tokens: number; cache_read_tokens: number; cache_write_tokens: number; cost_usd: number; cost_cad: number | null; events: number;
    reviewer_minutes: number; reviewer_minutes_per_decision: number; decisions: number; note: string };
  per_agent: { agent: AgentKey; agent_role: string; runs: number; proposals_decided: number; accepted_without_edit: number; denied: number; proxy_minutes: number;
    avoided_dollars: number; unconfirmed_dollars: number; tasks_closed: number; cost_usd: number }[];
}
export interface AgentSettings {
  value: AgentState & { approvers: { tier1_roles: string[]; tier2_roles: string[] }; minutes_table: Record<string, number>; thresholds: Record<string, number>;
    escalation_roles: string[]; live_override: { by: string; reason: string; at: string } | null; enabled: boolean | null };
  defaults: Record<string, any>; can_edit: boolean; agents: string[]; roles: string[];
}
export interface SessionGrant { id: string; agent: AgentKey; tool_name: string; approver_email: string | null; approver_role: string | null; max_tier: number; granted_at: string | null; expires_at: string | null; revoked_at: string | null; uses: number; active: boolean }

const P = "/ai/agents";
export const agenticApi = {
  overview: () => api.get<Overview>(`${P}/overview`).then((r) => r.data),
  proposals: (p: { status?: string; agent?: string; run_id?: string }) =>
    api.get<{ items: Proposal[]; total: number }>(`${P}/proposals`, { params: { status: p.status || "inbox", agent: p.agent || undefined, run_id: p.run_id || undefined } }).then((r) => r.data),
  proposal: (id: string) => api.get<Proposal & { log: LogEvent[] }>(`${P}/proposals/${id}`).then((r) => r.data),
  approve: (id: string, body: { for_session?: boolean; note?: string }) => api.post<Proposal>(`${P}/proposals/${id}/approve`, body).then((r) => r.data),
  editApprove: (id: string, tool_args: Record<string, any>, note = "") => api.post<Proposal>(`${P}/proposals/${id}/edit-approve`, { tool_args, note }).then((r) => r.data),
  deny: (id: string, message: string) => api.post<Proposal>(`${P}/proposals/${id}/deny`, { message }).then((r) => r.data),
  grants: () => api.get<{ items: SessionGrant[] }>(`${P}/grants`).then((r) => r.data),
  revokeGrant: (id: string) => api.post<SessionGrant>(`${P}/grants/${id}/revoke`).then((r) => r.data),
  tasks: (p: { status?: string; agent?: string }) =>
    api.get<{ items: AgentTask[]; total: number }>(`${P}/tasks`, { params: { status: p.status || undefined, agent: p.agent || undefined } }).then((r) => r.data),
  completeTask: (id: string, result: Record<string, any> = {}, status = "done") => api.post<AgentTask>(`${P}/tasks/${id}/complete`, { result, status }).then((r) => r.data),
  escalateTask: (id: string, reason = "") => api.post<AgentTask>(`${P}/tasks/${id}/escalate`, { reason }).then((r) => r.data),
  chaseTask: (id: string) => api.post<AgentTask>(`${P}/tasks/${id}/chase`).then((r) => r.data),
  runs: (p: { agent?: string; mode?: string }) => api.get<{ items: AgentRun[] }>(`${P}/runs`, { params: { agent: p.agent || undefined, mode: p.mode || undefined, limit: 200 } }).then((r) => r.data),
  run: (id: string) => api.get<ShadowReport>(`${P}/runs/${id}`).then((r) => r.data),
  log: (p: { run_id?: string; event_type?: string; limit?: number }) => api.get<{ items: LogEvent[] }>(`${P}/log`, { params: { ...p, run_id: p.run_id || undefined, event_type: p.event_type || undefined } }).then((r) => r.data),
  verify: () => api.get<ChainVerify>(`${P}/log/verify`).then((r) => r.data),
  value: (from?: string, to?: string) => api.get<ValueSummary>(`${P}/value`, { params: { from: from || undefined, to: to || undefined } }).then((r) => r.data),
  confirmValue: (id: string) => api.post<ValueRow>(`${P}/value/${id}/confirm`).then((r) => r.data),
  settings: () => api.get<AgentSettings>(`${P}/settings`).then((r) => r.data),
  saveSettings: (value: Record<string, any>) => api.put<{ saved: boolean; value: AgentSettings["value"] }>(`${P}/settings`, { value }).then((r) => r.data),
  setMode: (mode: Mode, reason = "") => api.post<{ saved: boolean; value: AgentSettings["value"] }>(`${P}/settings/mode`, { mode, reason }).then((r) => r.data),
};

export const cad = (n: number | null | undefined) => (Number(n) || 0).toLocaleString("en-CA", { style: "currency", currency: "CAD", maximumFractionDigits: 0 });
export const minutesLabel = (m: number) => (m >= 60 ? `${(m / 60).toLocaleString("en-CA", { maximumFractionDigits: 1 })} h` : `${Math.round(m)} min`);
export const KIND_LABEL: Record<string, string> = {
  query_resolved: "Employee question resolved", exception_confirmed: "Payroll exception confirmed", filing_prepared: "Statutory filing prepared",
  input_chased: "Missing input chased and closed", variance_explained: "Pay variance explained", reconciliation_done: "Reconciliation completed",
  review_minutes_per_decision: "Reviewer minutes per approval decision", interest_avoided: "Interest / fee avoided",
};
export const THRESHOLD_LABEL: Record<string, string> = {
  net_delta_pct: "Net-pay change that needs a look (%)", net_delta_floor_cad: "…and at least this many $", bulk_employees: "Bulk limit: employees in one action → two approvers",
  chase_every_hours: "Remind the owner every (hours)", escalate_after_chases: "Escalate after this many reminders", proposal_expiry_hours: "A proposal expires after (hours)",
  session_grant_hours: "'Approve for session' lasts (hours)",
};
