// D1 — configurability: custom fields, no-code approval workflows, letter-template versions.
import api from "./client";

const d = <T,>(p: Promise<{ data: T }>) => p.then((r) => r.data);

// ── custom fields ─────────────────────────────────────────────────────────────
export type CfEntity = "employee" | "candidate" | "asset" | "expense";
export type CfType = "text" | "number" | "date" | "select" | "multiselect" | "boolean" | "file" | "lookup";
export interface CfDef {
  id: string; entity: CfEntity; key: string; label: string; type: CfType; type_label?: string; required: boolean;
  options: string[] | null; validation: { regex?: string; min?: number | string; max?: number | string; help?: string };
  visible_to: string[]; editable_by: string[]; section: string; sort_order: number; show_in_grid: boolean; active: boolean;
  updated_by?: string | null; updated_at?: string | null; can_edit?: boolean;
}
export type CfValues = Record<string, any>;
export interface CfValuesOut { entity: CfEntity; id: string; values: CfValues; fields: CfDef[]; missing_required: string[]; can_edit: boolean; changed?: string[]; }

export const customFieldsApi = {
  meta: () => d<{ entities: { key: CfEntity; label: string }[]; types: { key: CfType; label: string }[]; roles: { key: string; label: string }[]; lookups: string[]; can_setup: boolean }>(api.get("/employees/custom-fields/meta")),
  definitions: (entity?: CfEntity) => d<{ items: CfDef[]; can_setup: boolean }>(api.get("/employees/custom-fields/definitions", { params: entity ? { entity } : {} })),
  create: (body: Partial<CfDef>) => d<CfDef>(api.post("/employees/custom-fields/definitions", body)),
  update: (id: string, body: Partial<CfDef>) => d<CfDef>(api.put(`/employees/custom-fields/definitions/${id}`, body)),
  deactivate: (id: string) => d<{ ok: boolean }>(api.delete(`/employees/custom-fields/definitions/${id}`)),
  values: (entity: CfEntity, id: string) => d<CfValuesOut>(api.get(`/employees/custom-fields/values/${entity}/${id}`)),
  save: (entity: CfEntity, id: string, values: CfValues) => d<CfValuesOut>(api.put(`/employees/custom-fields/values/${entity}/${id}`, { values })),
  bulk: (entity: CfEntity, ids?: string[]) => d<{ entity: CfEntity; fields: CfDef[]; values: Record<string, CfValues> }>(
    api.get(`/employees/custom-fields/values/${entity}`, { params: ids && ids.length ? { ids: ids.join(",") } : {} })),
};

// ── approval workflows ────────────────────────────────────────────────────────
export type ApproverSpec =
  | { type: "caller" } | { type: "role"; role: string } | { type: "manager"; level: number }
  | { type: "user"; user_id: string } | { type: "any_of"; approvers: ApproverSpec[] };
export interface Condition { field: string; op: string; value?: any; }
export interface ChainStep {
  id?: string; name: string; mode: "any" | "all"; approvers: ApproverSpec[]; when: Condition[];
  escalate_after_hours: number; escalate_to: ApproverSpec | null; sla_hours: number;
}
export interface ChainDefinition { steps: ChainStep[]; auto_approve: { name: string; when: Condition[] }[]; sla_hours: number; }
export interface Chain {
  id: string; request_type: string; label: string; name: string; description: string; definition: ChainDefinition;
  version: number; is_default: boolean; active: boolean; updated_by: string | null; updated_at: string | null;
  steps: number; auto_rules: number; context_fields: string[];
}
export interface WorkflowMeta {
  request_types: { key: string; label: string; fields: string[] }[]; context_fields: { key: string; label: string }[];
  operators: { op: string; label: string }[]; roles: { key: string; label: string }[];
  people: { id: string; name: string; role: string }[]; can_edit: boolean;
}
export interface SimResult {
  request_type: string; context: Record<string, any>; auto_approved: boolean; auto_rule: string | null; skipped: string[];
  steps: { name: string; mode: string; approvers: string[]; escalate_after_hours: number; sla_hours: number }[]; lines: string[]; note: string;
}

export const workflowsApi = {
  meta: () => d<WorkflowMeta>(api.get("/approvals/workflows/meta")),
  list: () => d<{ items: Chain[]; can_edit: boolean }>(api.get("/approvals/workflows")),
  get: (key: string) => d<Chain>(api.get(`/approvals/workflows/${key}`)),
  save: (key: string, body: { name?: string; description?: string; definition: ChainDefinition; active: boolean }) => d<Chain>(api.put(`/approvals/workflows/${key}`, body)),
  reset: (key: string) => d<Chain>(api.post(`/approvals/workflows/${key}/reset`)),
  simulate: (key: string, body: { context: Record<string, any>; definition?: ChainDefinition; requester_employee_id?: string }) => d<SimResult>(api.post(`/approvals/workflows/${key}/simulate`, body)),
  runs: (key: string) => d<{ items: any[] }>(api.get(`/approvals/workflows/${key}/runs`)),
};

// ── letter template versions / live preview ───────────────────────────────────
export interface TemplateVersion { id: string | null; version: number; state: "draft" | "published"; name: string; body: string; note: string; saved_by: string | null; saved_at: string | null; current: boolean; }
export interface PreviewRun { text: string; bold: boolean; }
export interface PreviewBlock { t: "p" | "h1" | "h2" | "li" | "hr" | "table"; mark?: string | null; runs?: PreviewRun[]; rows?: PreviewRun[][][]; money?: boolean; }
export interface PreviewText {
  ok: boolean; errors: string[]; blocks: PreviewBlock[]; text: string; missing: { field: string; label: string }[]; refused?: string[];
  employee: string; employee_id?: string; name?: string; date?: string;
  letterhead?: { company_name: string; address: string; signatory_name: string; signatory_designation: string; footer_text: string };
}

export const letterTemplatesApi = {
  versions: (id: string) => d<{ template_id: string; published_version: number; versions: TemplateVersion[] }>(api.get(`/letters/templates/${id}/versions`)),
  saveDraft: (id: string, body: { body: string; name?: string; note?: string }) => d<{ template_id: string; draft: { body: string; name: string; note?: string; saved_by: string; saved_at: string }; published_version: number }>(api.put(`/letters/templates/${id}/draft`, body)),
  publish: (id: string) => d<any>(api.post(`/letters/templates/${id}/publish`)),
  rollback: (id: string, version: number) => d<any>(api.post(`/letters/templates/${id}/rollback/${version}`)),
  previewText: (body: { employee_id?: string; template_key?: string; body?: string; data?: any; effective_date?: string }) => d<PreviewText>(api.post("/letters/preview-text", body)),
};

export const errDetail = (e: any, fallback = "Something went wrong") => {
  const det = e?.response?.data?.detail;
  if (typeof det === "string") return det;
  if (det?.message) return `${det.message} ${(det.errors || []).join(" ")}`.trim();
  return e?.message || fallback;
};
