// Lifecycle agents (2026-10-26) — Onboarding / Exit / Attendance share one workspace shape: /ai/agents/<slug>/*
// (backend agents/lifecycle_router.py). The server owns every rule (checks, clocks, tiers, shadow mode, who may act).
import api from "../../../../api/client";
import type { AgentRun, Proposal } from "../../../../api/agentic";

export type Slug = "onboarding" | "exit" | "attendance";
export type Severity = "blocking" | "warning" | "info";

export interface Clock {
  kind: string; label: string; due: string | null; days_left: number | null; status: "ok" | "due_soon" | "overdue" | "undefined" | "met" | "late";
  verified: boolean; source: string | null; rule_key: string | null; note: string | null; badge: string | null; working_days?: number; code?: string;
}
export interface Item {
  id: string; agent: string; run_id: string | null; case_key: string; case_label: string | null; employee_id: string | null; employee_ref: string | null;
  code: string; ref: string; severity: Severity; title: string; what: string | null; how_to_fix: string | null; owner_role: string | null; link: string | null;
  due_on: string | null; clock: Clock | null; detail: Record<string, any>; status: "open" | "resolved" | "dismissed"; task_id: string | null;
  resolved_at: string | null; created_at: string | null; updated_at: string | null;
}
export interface CaseRow {
  case_key: string; case_label: string | null; employee_id: string | null; employee_ref: string | null; items: Item[];
  blocking: number; warning: number; info: number; next_due: string | null; clocks: Clock[];
}
export interface Overview {
  agent: string; role_name: string; does: string; you_approve: string; never: string[]; tier_map: Record<string, number>;
  state: { enabled: boolean; addon_purchased: boolean; paused: boolean; mode: "shadow" | "live"; can_go_live: boolean; live_gate_note: string };
  settings: Record<string, any>; can_edit: boolean; checks: { code: string; name: string }[]; latest_run: AgentRun | null; runs: AgentRun[];
  counts: { open: number; blocking: number; warning: number; info: number; cases: number; by_owner: Record<string, number> };
  cases: CaseRow[]; inbox: Proposal[]; would_have_done: Proposal[]; shadow_note: string | null;
}
export interface WhatsApp {
  value: { enabled: boolean; provider: string; phone_number_id: string; api_version: string; token_env: string; language: string; after_reminders: number; templates: { agent_chase: string } };
  readiness: { can_send: boolean; missing: string[] }; can_edit: boolean; consent_event: string; order: string[]; note: string;
}

const d = <T,>(p: Promise<{ data: T }>) => p.then((r) => r.data);

export const lifecycleApi = (slug: Slug) => {
  const P = `/ai/agents/${slug}`;
  return {
    overview: () => d(api.get<Overview>(`${P}/overview`)),
    run: () => d(api.post<AgentRun & { chase: any; items: number }>(`${P}/run`, {})),
    dismiss: (id: string, reason: string) => d(api.post<Item>(`${P}/items/${id}/dismiss`, { reason })),
    saveSettings: (value: Record<string, any>) => d(api.put<{ saved: boolean; value: Record<string, any> }>(`${P}/settings`, { value })),
  };
};

export const channelsApi = {
  whatsapp: () => d(api.get<WhatsApp>("/ai/agents/channels/whatsapp")),
  saveWhatsapp: (value: Partial<WhatsApp["value"]>) => d(api.put<{ saved: boolean; value: WhatsApp["value"]; readiness: WhatsApp["readiness"] }>("/ai/agents/channels/whatsapp", { value })),
  preview: (employee_id: string, reminder_no = 2) => d(api.post<{ plan: string[]; consent: { ok: boolean; reason: string | null; to: string }; readiness: WhatsApp["readiness"]; would_send: any; sent: false }>(
    "/ai/agents/channels/whatsapp/preview", { employee_id, reminder_no })),
};

export const OWNER_LABEL: Record<string, string> = { employee: "Employee", manager: "Manager", hr: "HR", payroll: "Payroll", it: "IT", admin: "Admin", finance: "Finance" };
