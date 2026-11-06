// HR Desk (stream D, 2026-10-25) — policy clause manager + sealed harassment-referral inbox. Server: ai-platform /ai/desk/*.
import api from "../../../api/client";

export interface DeskPolicy { id: string; key: string; title: string; owner: string | null; status: "draft" | "approved" | "retired" | string; source_doc: string | null; created_by: string | null; created_at: string | null; updated_at: string | null; clauses: number; approved: number; draft: number; avg_ready: number | null; }
export interface DeskScope { locations?: string[]; grades?: string[]; employment_types?: string[]; roles?: string[]; }
export interface DeskClause {
  id: string; policy_id: string; clause_id: string; version: number; title: string; text: string; effective_from: string | null; effective_to: string | null;
  scope: DeskScope; status: "draft" | "approved" | "retired" | string; approved_by: string | null; approved_at: string | null; ai_ready_score: number; ai_ready_notes: string[];
  source_doc: string | null; order_no: number; created_at: string | null; policy_title?: string | null; policy_key?: string | null; note?: string;
}
export interface IcReferral { id: string; employee_id: string | null; channel: string; created_at: string | null; acknowledged_by: string | null; acknowledged_at: string | null; }
export interface DeskMetrics {
  sessions: number; candidate_sessions: number; outcomes: Record<string, number>; self_solved: number; deflection_rate: number | null; handoffs: number; handoff_rate: number | null;
  hard_routed_sessions: number; turns: number; answered_turns: number; abstained_turns: number; abstain_rate: number | null; csat: number | null; csat_rated: number; csat_coverage: number | null;
  median_ms: number | null; top_intents: { intent: string; count: number }[]; definitions: Record<string, string>; since: string; days: number;
}

// ── phase 2 (P2-C): the desk as an agent — chat block, dashboard, weekly report ──
export interface DeskCitation { tag?: string; policy?: string; policy_id?: string; clause_id?: string; version?: number; effective_from?: string | null; deep_link?: string | null; clause_pk?: string; label?: string; }
export interface DeskPayLine { component: string; label: string; kind: string; prev: number; curr: number; delta: number; rule_code: string; rule_text: string; refs: string[]; }
export interface DeskPayStructure { period: string; prev_period: string | null; lines: DeskPayLine[]; net_delta: number; gross_delta: number; lop_days: number; per_day_rate: number; regime: string | null; pf_ceiling: number | null; unexplained_delta: number; needs_payroll_review: boolean; summary_facts: string[]; run_id: string | null; links: string[]; run_meta?: Record<string, any>; }
export interface DeskTicket { id: string; number: string | null; link: string; category: string; sla_hours: number | null; }
/** The `desk` object on every /ai/assistant/chat reply (desk.dispatch.desk_block). */
export interface DeskReply {
  session_id: string | null; turn_id: string | null; run_id: string | null; kind: "answer" | "abstain" | "pay_change" | "hard_route" | "refused" | "tools" | string;
  lane?: string | null; intent?: string | null; mode: "live" | "rules" | "fixed" | string; hard_class: string | null; citations: DeskCitation[]; confidence?: number | null;
  abstained?: boolean; abstain_reason?: string | null; ticket: DeskTicket | null; ic_referral: boolean; handoff_reason?: string | null; ask_human: boolean;
  ask_hr: { link: string; text: string } | null; addon: boolean; disclosure: string | null; csat: { turn_id: string; question: string; options: { rating: "up" | "down"; label: string }[] } | null;
  structure: DeskPayStructure | null; narrated?: boolean | null; fallback_reason?: string | null; links?: string[];
}
export interface DeskDashboard {
  state: { enabled: boolean; mode: "shadow" | "live"; addon_purchased: boolean; paused: boolean; can_go_live?: boolean; live_gate_note?: string };
  days: number; metrics: DeskMetrics; definitions: Record<string, string>; top_questions: { intent: string; count: number }[];
  abstained_questions: { turn_id: string; session_id: string; question: string; intent: string; confidence: number | null; rating: string | null; at: string | null }[];
  knowledge: { policies: number; policies_approved: number; approved: number; draft: number; retired: number; avg_ready_approved: number | null; avg_ready_draft: number | null; ready: boolean };
  value: { query_resolved: number; query_resolved_confirmed: number; handoff_created: number; ic_referral: number } | null;
  last_run: { id: string; status: string; started_at: string | null; finished_at: string | null; mode: string; summary: Record<string, any>; input_tokens: number; output_tokens: number; cost_usd: number } | null;
  last_report: { at: string | null; by: string | null } | null; write_clause_link: string; chat_hint: string;
}
export interface DeskWeeklyReport { ok: boolean; notified: boolean; roles: string[]; smtp_configured: boolean; note: string; subject: string; report: string; at: string; }

const d = <T,>(p: Promise<{ data: T }>) => p.then((r) => r.data);

export const deskApi = {
  policies: () => d(api.get<{ items: DeskPolicy[] }>("/ai/desk/policies")),
  createPolicy: (body: { key: string; title: string; owner?: string; source_doc?: string }) => d(api.post<DeskPolicy>("/ai/desk/policies", body)),
  policy: (id: string) => d(api.get<{ policy: DeskPolicy; clauses: DeskClause[] }>(`/ai/desk/policies/${id}`)),
  patchPolicy: (id: string, body: { title?: string; owner?: string; status?: string }) => d(api.patch<DeskPolicy>(`/ai/desk/policies/${id}`, body)),
  importText: (id: string, body: { text: string; source_doc?: string; effective_from?: string | null; scope?: DeskScope; replace_drafts?: boolean }) =>
    d(api.post<{ imported: number; clauses: DeskClause[]; avg_ready: number | null; next_step: string }>(`/ai/desk/policies/${id}/import`, body)),
  importFile: (id: string, file: File, effective_from?: string) => {
    const f = new FormData(); f.append("file", file); if (effective_from) f.append("effective_from", effective_from);
    return d(api.post<{ imported: number; clauses: DeskClause[]; avg_ready: number | null; next_step: string }>(`/ai/desk/policies/${id}/import`, f));
  },
  addClause: (pid: string, body: { clause_id: string; title?: string; text: string; effective_from?: string | null; effective_to?: string | null; scope?: DeskScope }) =>
    d(api.post<DeskClause>(`/ai/desk/policies/${pid}/clauses`, body)),
  patchClause: (cid: string, body: Partial<Pick<DeskClause, "title" | "text" | "effective_from" | "effective_to" | "scope" | "clause_id">>) => d(api.patch<DeskClause>(`/ai/desk/clauses/${cid}`, body)),
  approve: (cid: string, body: { effective_from?: string | null; note?: string }) => d(api.post<DeskClause>(`/ai/desk/clauses/${cid}/approve`, body)),
  retire: (cid: string) => d(api.post<DeskClause>(`/ai/desk/clauses/${cid}/retire`)),
  readiness: (pid: string) => d(api.get<{ items: { id: string; clause_id: string; version: number; status: string; score: number; notes: string[] }[]; avg: number | null; approved_avg: number | null }>(`/ai/desk/policies/${pid}/readiness`)),
  metrics: (days = 7) => d(api.get<DeskMetrics>("/ai/desk/metrics", { params: { days } })),
  icAccess: () => d(api.get<{ ic_member: boolean }>("/ai/desk/ic-referrals/access")),
  icReferrals: () => d(api.get<{ items: IcReferral[]; note: string }>("/ai/desk/ic-referrals")),
  icAcknowledge: (id: string) => d(api.post<{ id: string; acknowledged_by: string; acknowledged_at: string }>(`/ai/desk/ic-referrals/${id}/acknowledge`)),
  // phase 2 (P2-C)
  feedback: (body: { session_id: string; turn_id?: string | null; rating: "up" | "down"; comment?: string }) =>
    d(api.post<{ ok: boolean; unresolved: number; outcome: string; value?: Record<string, any> }>("/ai/desk/feedback", body)),
  dashboard: (days = 7) => d(api.get<DeskDashboard>("/ai/desk/dashboard", { params: { days } })),
  weeklyReport: (days = 7) => d(api.post<DeskWeeklyReport>("/ai/desk/weekly-report", { days })),
};
