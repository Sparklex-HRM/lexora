import api from "./client";

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename; document.body.appendChild(a); a.click();
  setTimeout(() => { document.body.removeChild(a); URL.revokeObjectURL(url); }, 500);
}

/** Show a typed cell value the way the admin typed it (months as Apr-2026, numbers plain). */
export function cellText(v: any): string {
  if (v === null || v === undefined) return "";
  if (Array.isArray(v) && v.length === 2 && typeof v[0] === "number") {
    const names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    return `${names[v[1] - 1]}-${v[0]}`;
  }
  if (typeof v === "boolean") return v ? "Y" : "N";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(2);
  return String(v);
}

// ── Stream G6: migration kit (Canada) — /payroll/migration/* ──
export interface CaFieldDef { key: string; label: string; required: boolean; kind: string; example: string; help: string; choices: string[]; }
export interface CaCatalog { file_types: { key: string; label: string; help: string; fields: CaFieldDef[] }[]; vendors: { code: string; name: string; verified: boolean; source_url?: string; note?: string; file_types: string[] }[]; not_built: Record<string, string>; year: number; }
export interface CaBatch {
  id: string; file_type: string; file_type_label: string; vendor: string; file_name: string | null; status: "uploaded" | "validated" | "committed" | "rolled_back";
  row_count: number; error_count: number; summary: any; result: any; created_by: string | null; created_at: string | null; committed_at: string | null; rolled_back_at: string | null;
  headers?: string[]; mapping?: Record<string, string | null>; rows?: { row_no: number; data: any; errors: string[]; warnings: string[] }[]; sample?: any[]; missing_required?: string[]; preset_note?: string | null;
}
export interface ParallelRun {
  id: string; country: string; month: string; vendor: string | null; file_name: string | null; tolerance: number; status: "uploaded" | "compared" | "signed";
  signed_by: string | null; signed_at: string | null; sign_note: string | null; created_by: string | null; created_at: string | null; summary: any;
  headers?: string[]; mapping?: Record<string, string>; sample?: any[]; result?: any;
}
export interface ChecklistStep { key: string; title: string; how: string; link: string; auto: string | null; required: boolean; order: number; done: boolean; auto_value: any; manual: boolean; note: string | null; done_by: string | null; done_at: string | null; blocked: boolean; }
export interface Checklist { country: string; steps: ChecklistStep[]; done: number; total: number; percent: number; next_step: string | null; complete: boolean; }

const B = "/payroll/migration";
const fd = (file: File, extra: Record<string, any>) => { const f = new FormData(); f.append("file", file); Object.entries(extra).forEach(([k, v]) => f.append(k, String(v))); return f; };

export const migrationKitApi = {
  caCatalog: () => api.get<CaCatalog>(`${B}/canada/catalog`).then((r) => r.data),
  caTemplate: async (ft: string, format: "xlsx" | "csv" = "xlsx") => {
    const r = await api.get(`${B}/canada/templates/${ft}`, { params: { format }, responseType: "blob" });
    downloadBlob(r.data as Blob, `lexora_${ft}_template.${format}`);
  },
  caUpload: (file: File, file_type: string, vendor: string) => api.post<CaBatch>(`${B}/canada/upload`, fd(file, { file_type, vendor }), { headers: { "Content-Type": "multipart/form-data" } }).then((r) => r.data),
  caValidate: (id: string, body: { mapping?: Record<string, string | null>; overrides?: any; save_preset?: boolean }) => api.post<CaBatch>(`${B}/canada/batches/${id}/validate`, body).then((r) => r.data),
  caBatches: () => api.get<CaBatch[]>(`${B}/canada/batches`).then((r) => r.data),
  caBatch: (id: string) => api.get<CaBatch>(`${B}/canada/batches/${id}`).then((r) => r.data),
  caCommit: (id: string, allow_partial = false) => api.post<CaBatch>(`${B}/canada/batches/${id}/commit`, { allow_partial }).then((r) => r.data),
  caRollback: (id: string) => api.post<CaBatch>(`${B}/canada/batches/${id}/rollback`, {}).then((r) => r.data),
  caDiscard: (id: string) => api.delete(`${B}/canada/batches/${id}`).then((r) => r.data),
  lastBatch: () => api.get<{ last: any }>(`${B}/last-batch`).then((r) => r.data),
  undoByUrl: (url: string) => api.post(url, {}).then((r) => r.data),
  presets: () => api.get<any>(`${B}/presets`).then((r) => r.data),
  prComponents: (country: string) => api.get<{ components: { key: string; label: string }[]; presets: any }>(`${B}/parallel-run/components`, { params: { country } }).then((r) => r.data),
  prUpload: (file: File, month: string, country: string, vendor: string, tolerance: number) =>
    api.post<ParallelRun>(`${B}/parallel-run`, fd(file, { month, country, vendor, tolerance }), { headers: { "Content-Type": "multipart/form-data" } }).then((r) => r.data),
  prCompare: (id: string, body: { mapping?: Record<string, string>; tolerance?: number }) => api.post<ParallelRun>(`${B}/parallel-run/${id}/compare`, body).then((r) => r.data),
  prSign: (id: string, note = "") => api.post<ParallelRun>(`${B}/parallel-run/${id}/sign`, { note }).then((r) => r.data),
  prList: () => api.get<ParallelRun[]>(`${B}/parallel-run`).then((r) => r.data),
  prGet: (id: string) => api.get<ParallelRun>(`${B}/parallel-run/${id}`).then((r) => r.data),
  checklist: (country: string) => api.get<Checklist>(`${B}/checklist`, { params: { country } }).then((r) => r.data),
  tick: (body: { country: string; step_key: string; done: boolean; note?: string }) => api.post(`${B}/checklist/tick`, body).then((r) => r.data),
};
