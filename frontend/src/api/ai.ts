// G5 — AI & analytics: watchdog fleet, HR copilot, flight-risk signals, custom report builder.
import api from "./client";

// ── watchdogs ────────────────────────────────────────────────────────────────
export interface WdRun {
  id: string; agent_key: string; agent?: string; trigger: string; status: "ok" | "attention" | "skipped" | "error" | string;
  started_at: string | null; finished_at: string | null; duration_ms: number; rows_read: number; findings_count: number;
  new_findings: number; severity_max: string | null; summary: string; thresholds: Record<string, any>; notified: boolean; error: string | null;
}
export interface WdAgent {
  key: string; name: string; domain: string; checks: string; cadence_minutes: number; notify_roles: string[]; countries: string[];
  enabled: boolean; thresholds: Record<string, any>; threshold_help: Record<string, string>; last_run: WdRun | null; engine: string;
}
export interface WdFinding {
  id: string; run_id: string; agent_key: string; agent: string; severity: "info" | "warn" | "high" | string; confidence: string;
  subject_employee_id: string | null; subject_name: string | null; title: string; explanation: string;
  evidence: Record<string, any>[]; is_new: boolean; created_at: string | null;
}
export interface WdHealth {
  as_of: string; country: string; score: number; headline: string; totals: { info: number; warn: number; high: number };
  agents: { key: string; name: string; domain: string; enabled: boolean; status: string; last_run_at: string | null; summary: string;
    findings: Record<string, number>; severity_max: string | null; rows_read: number }[];
  never_run: number; errors: number; note: string;
}
export interface WdSettings { value: { enabled: boolean; disabled_agents: string[]; thresholds: Record<string, Record<string, any>>; notify: boolean }; can_edit: boolean; defaults: Record<string, Record<string, any>>; }

export const watchdogApi = {
  fleet: () => api.get<{ country: string; fleet_enabled: boolean; agents: WdAgent[]; as_of: string }>("/ai/watchdogs").then((r) => r.data),
  health: () => api.get<WdHealth>("/ai/watchdogs/health").then((r) => r.data),
  run: (key: string) => api.post<{ run: WdRun; findings: WdFinding[] }>(`/ai/watchdogs/${key}/run`).then((r) => r.data),
  runAll: () => api.post<{ runs: WdRun[] }>("/ai/watchdogs/run-all").then((r) => r.data),
  runs: (agent?: string) => api.get<{ items: WdRun[] }>("/ai/watchdogs/runs", { params: { agent: agent || undefined, limit: 200 } }).then((r) => r.data),
  findings: (p: { agent?: string; severity?: string; run_id?: string }) =>
    api.get<{ items: WdFinding[]; total: number }>("/ai/watchdogs/findings", { params: { ...p, agent: p.agent || undefined, severity: p.severity || undefined } }).then((r) => r.data),
  settings: () => api.get<WdSettings>("/ai/watchdogs/settings").then((r) => r.data),
  saveSettings: (value: WdSettings["value"]) => api.put("/ai/watchdogs/settings", { value }).then((r) => r.data),
};

// ── copilot ──────────────────────────────────────────────────────────────────
export interface CopilotBudget { enabled: boolean; org_used: number; org_limit: number; user_used: number; user_limit: number; over: boolean; resets: string; }
export interface CopilotStatus { configured: boolean; model: string | null; message: string; budget: CopilotBudget; shield: string; linked_employee: boolean; }
export interface CopilotPendingAction { id: string; action: string; summary: string; expires_at: string | null; }
export interface CopilotReply {
  reply: string; mode: "live" | "rules" | "refused" | string; note: string; pending: CopilotPendingAction | null; tools_used: string[];
  masked: number; usage: { input_tokens: number; output_tokens: number; cost_usd: number; model: string | null }; budget: CopilotBudget;
}
export interface CopilotAuditRow { id: string; user: string; role: string; mode: string; model: string | null; prompt: string; reply: string; tools: any[]; masked: number; tokens: number; cost_usd: number; note: string | null; at: string | null; }

export const copilotApi = {
  status: () => api.get<CopilotStatus>("/ai/copilot/status").then((r) => r.data),
  chat: (message: string, history: { role: string; content: string }[]) => api.post<CopilotReply>("/ai/copilot/chat", { message, history }).then((r) => r.data),
  confirm: (pending_id: string) => api.post<{ ok: boolean; message: string; status: string; link?: string }>("/ai/copilot/confirm", { pending_id }).then((r) => r.data),
  cancel: (pending_id: string) => api.post<{ ok: boolean; message: string }>("/ai/copilot/cancel", { pending_id }).then((r) => r.data),
  audit: (all_users = false) => api.get<{ items: CopilotAuditRow[]; note: string }>("/ai/copilot/audit", { params: { all_users, limit: 200 } }).then((r) => r.data),
};

// ── flight risk ──────────────────────────────────────────────────────────────
export interface RiskFactor { factor: string; points: number; evidence: string; rule: string; }
export interface RiskRow {
  employee_id: string; name: string; employee_number: string | null; department: string | null; job_title: string | null; manager_name: string | null;
  hire_date: string | null; score: number; band: "low" | "watch" | "high" | string; factors: RiskFactor[]; top_factor: string; label: string;
}
export interface RiskReport { as_of: string; scope: string; rows: RiskRow[]; bands: Record<string, number>; method: string; thresholds: Record<string, number>; label: string; }
export const attritionApi = {
  signals: (p: { band?: string; department?: string } = {}) => api.get<RiskReport>("/ai/attrition", { params: { band: p.band || undefined, department: p.department || undefined } }).then((r) => r.data),
};

// ── report builder ───────────────────────────────────────────────────────────
export interface RbColumn { key: string; label: string; type: "text" | "number" | "money" | "percent" | "date" | "hours" | string; money?: boolean; }
export interface RbDataset { key: string; title: string; description: string; access: string; columns: RbColumn[]; scope: string; }
export interface RbFilter { col: string; op: string; value?: any; }
export interface RbSpec { dataset: string; columns: string[]; filters: RbFilter[]; group_by: string[]; aggregates: { fn: string; col?: string }[]; sort: { col: string; dir: "asc" | "desc" }[]; limit: number; }
export interface RbResult { columns: RbColumn[]; rows: Record<string, any>[]; total: number; truncated: boolean; limit: number; grouped: boolean; as_of: string; }
export interface RbSchedule { id: string; cadence: string; hour_utc: number; local_time?: string | null; tz?: string | null; weekday: number; day_of_month: number; roles: string[]; user_ids: string[]; enabled: boolean; next_run_at: string | null; last_run_at: string | null; last_rows: number | null; last_status: string | null; }
export interface RbSaved { id: string; name: string; dataset: string; spec: RbSpec; shared: boolean; owner: string | null; mine: boolean; created_at: string | null; updated_at: string | null; schedule: RbSchedule | null; }

export const builderApi = {
  datasets: () => api.get<{ datasets: RbDataset[]; scope: string; shows_money: boolean; max_rows: number; ops: { op: string; label: string }[]; aggregates: string[] }>("/reports/builder/datasets").then((r) => r.data),
  run: (spec: RbSpec) => api.post<RbResult>("/reports/builder/run", { spec }).then((r) => r.data),
  exportFile: async (spec: RbSpec, format: "csv" | "xlsx", name: string) => {
    const r = await api.post("/reports/builder/export", { spec }, { params: { format, name }, responseType: "blob" });
    const url = URL.createObjectURL(r.data as Blob);
    const a = document.createElement("a");
    a.href = url; a.download = `${name || "report"}.${format}`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  },
  saved: () => api.get<{ items: RbSaved[]; smtp: { configured: boolean; message: string } }>("/reports/builder/saved").then((r) => r.data),
  save: (name: string, spec: RbSpec, shared: boolean) => api.post<RbSaved>("/reports/builder/saved", { name, spec, shared }).then((r) => r.data),
  update: (id: string, name: string, spec: RbSpec, shared: boolean) => api.put<RbSaved>(`/reports/builder/saved/${id}`, { name, spec, shared }).then((r) => r.data),
  remove: (id: string) => api.delete(`/reports/builder/saved/${id}`).then((r) => r.data),
  schedule: (id: string, body: Partial<RbSchedule>) => api.put<RbSchedule & { smtp: { configured: boolean }; note: string }>(`/reports/builder/saved/${id}/schedule`, body).then((r) => r.data),
  unschedule: (id: string) => api.delete(`/reports/builder/saved/${id}/schedule`).then((r) => r.data),
  sendNow: (id: string) => api.post<{ ok: boolean; status: string; next_run_at: string | null }>(`/reports/builder/saved/${id}/send-now`).then((r) => r.data),
};

// ── D3: assistant with citations + gated actions, AI settings, access tokens, metering ──
export interface AiCap { cap_tokens: number; cap_cad: number; mode: "soft" | "hard"; used_tokens: number; used_cad: number | null; pct: number | null; warn: boolean; over: boolean; blocked: boolean; cad_cap_enforceable: boolean; }
export interface AiCitation { id: string; tool: string; label: string; link: string; system: string; records: string[]; }
export interface AiActionCard { action: string; what: string; for_whom: string; effect: string; risk: "normal" | "high"; requires_confirmations: number; link?: string; }
export interface AiAction { id: string; action: string; status: "pending" | "done" | "cancelled" | "expired" | "failed" | string; card: AiActionCard; confirmations: number; requires_confirmations: number; result: any; channel: string; created_at: string | null; expires_at: string | null; decided_at: string | null; }
export interface AiActionResult extends AiAction { executed: boolean; message: string; link?: string; }
export interface AssistantStatus { configured: boolean; key_source: string | null; model: string | null; effort: string; features: Record<string, boolean>; message: string; cap: AiCap; tools: { read: string[]; act: string[] }; shield: string; linked_employee: boolean; note: string | null; }
export interface AssistantReply {
  reply: string; mode: "live" | "rules" | "fixed" | string; note: string; citations: AiCitation[]; cited_ids: string[]; actions: AiAction[]; tools_used: string[]; masked: number;
  refusal: { category: string | null; explanation: string | null } | null;
  usage: { input_tokens: number; output_tokens: number; model: string | null; cost_usd: number | null; cost_cad: number | null; note?: string }; cap: AiCap;
  /** HR Desk block (phase 2, P2-C): every question goes through the desk first — citations, disclosure, hand-off, CSAT, pay-change table. */
  desk?: import("../pages/ai/desk/api").DeskReply | null;
}
export interface AiSettings {
  org_id: string; has_org_key: boolean; key_last4: string | null; model: string; effort: "low" | "medium" | "high"; features: { qa: boolean; actions: boolean; mcp: boolean };
  monthly_cap_tokens: number; monthly_cap_cad: number; cap_mode: "soft" | "hard"; usd_cad_rate: number | null;
  prices: Record<string, { input_usd_per_mtok: number; output_usd_per_mtok: number }>; updated_by: string | null; updated_at: string | null;
  status: { configured: boolean; key_source: string | null; model: string | null; message: string; sdk: boolean; models: string[]; price_source: string; default_prices_usd: Record<string, { input_usd_per_mtok: number; output_usd_per_mtok: number }> };
  platform_key: boolean; cap: AiCap;
}
export interface AiToken { id: string; name: string; user_id: string; user_email: string | null; role: string; employee_id: string | null; scopes: string[]; prefix: string; rate_per_min: number; calls: number; expires_at: string | null; revoked_at: string | null; last_used_at: string | null; created_by: string | null; created_at: string | null; status: "active" | "revoked" | "expired" | string; }
export interface AiToolCallRow { id: string; tool: string; args: any; ok: boolean; error: string | null; duration_ms: number; at: string | null; }
export interface UsageBucket { calls: number; input_tokens: number; output_tokens: number; tokens: number; cost_usd: number | null; cost_cad: number | null; [k: string]: any; }
export interface UsageTotals { input_tokens: number; output_tokens: number; tokens: number; calls: number; cost_usd: number | null; cost_cad: number | null; since: string; }
export interface AiMetering {
  scope: "me" | "org"; me: UsageTotals; org?: UsageTotals; cap: AiCap; price_source: string; model: string; usd_cad_rate?: number | null;
  prices?: Record<string, [number, number] | null>; since?: string; by_user?: UsageBucket[]; by_feature?: UsageBucket[]; by_model?: UsageBucket[]; by_channel?: UsageBucket[];
  daily?: UsageBucket[]; recent?: { at: string | null; user: string; channel: string; feature: string; model: string | null; key_source: string | null; input_tokens: number; output_tokens: number; cost_usd: number | null; cost_cad: number | null }[];
}

export const assistantApi = {
  status: () => api.get<AssistantStatus>("/ai/assistant/status").then((r) => r.data),
  chat: (message: string, history: { role: string; content: string }[], session_id?: string | null) =>
    api.post<AssistantReply>("/ai/assistant/chat", { message, history, session_id: session_id || undefined }).then((r) => r.data),
  tools: () => api.get<{ tools: any[]; confirmations: Record<string, number> }>("/ai/assistant/tools").then((r) => r.data),
  actions: () => api.get<{ items: AiAction[] }>("/ai/assistant/actions").then((r) => r.data),
  confirm: (id: string) => api.post<AiActionResult>(`/ai/assistant/actions/${id}/confirm`).then((r) => r.data),
  cancel: (id: string) => api.post<AiActionResult>(`/ai/assistant/actions/${id}/cancel`).then((r) => r.data),
};
export const aiSettingsApi = {
  get: () => api.get<AiSettings>("/ai/settings").then((r) => r.data),
  save: (body: Partial<AiSettings> & { api_key?: string; clear_key?: boolean }) => api.put<AiSettings>("/ai/settings", body).then((r) => r.data),
  testKey: (api_key?: string, model?: string) => api.post<{ ok: boolean; message: string; model?: string; cost_usd?: number | null; cost_cad?: number | null }>("/ai/settings/test-key", { api_key, model }).then((r) => r.data),
};
export const aiTokensApi = {
  list: () => api.get<{ items: AiToken[]; scopes: string[]; default_days: number }>("/ai/tokens").then((r) => r.data),
  create: (body: { name: string; user_id: string; user_email?: string | null; role: string; employee_id?: string | null; scopes: string[]; days?: number; rate_per_min?: number }) =>
    api.post<{ token: string; item: AiToken; note: string }>("/ai/tokens", body).then((r) => r.data),
  revoke: (id: string) => api.delete<{ ok: boolean }>(`/ai/tokens/${id}`).then((r) => r.data),
  calls: (id: string) => api.get<{ items: AiToolCallRow[]; note: string }>(`/ai/tokens/${id}/calls`).then((r) => r.data),
};
export const aiMeteringApi = { get: () => api.get<AiMetering>("/ai/metering").then((r) => r.data) };
