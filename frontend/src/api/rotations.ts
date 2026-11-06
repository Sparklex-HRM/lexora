import api from "./client";

// ── G2 (2026-10-23): shift rotation patterns, generated roster, shift swaps (/time/rotations) ──
const B = "/time/rotations";

export interface RotaShiftDef { name: string; start: string; end: string; break_minutes: number; hours?: number; color?: string }
export interface RotaTemplate { key: string; name: string; description: string; cycle_days: number; cycle: string[]; shifts: Record<string, RotaShiftDef> }
export interface RotaPattern { id: string; name: string; code: string | null; template: string | null; cycle: string[]; cycle_days: number;
  shifts: Record<string, RotaShiftDef>; description: string | null; active: boolean; work_days: number; night: boolean; updated_at: string | null }
export interface RotaDay { date: string; weekday: string; code: string | null; name: string; start: string | null; end: string | null; hours: number; night: boolean; color?: string | null }
export interface RotaAssignment { id: string; pattern_id: string; pattern: string; employee_id: string | null; employee: string | null;
  employee_number: string | null; department: string | null; team: boolean; crew: string | null; start_date: string; end_date: string | null; offset_days: number }
export interface RotaSettings { min_rest_hours: number; weeks_ahead: number; auto_generate: boolean; swaps_enabled: boolean;
  last_auto_run: string | null; updated_by: string | null; rest_note: string }
export interface RotaIssue { employee_id: string; name: string; date: string; reason: string; code?: string; shift?: string }
export interface RotaGenerate { start: string; end: string; dry_run: boolean; employees: number; work_shifts: number; off_days: number;
  created: number; updated: number; deleted: number; min_rest_hours: number;
  rows: { employee_id: string; name: string; work_date: string; kind: string; shift_code: string; start_time: string; end_time: string; planned_hours: number; note: string }[];
  kept: RotaIssue[]; skipped: RotaIssue[]; conflicts: RotaIssue[]; counts: { kept: number; skipped: number; conflicts: number } }
export interface MyShift { id: string; employee_id: string; work_date: string; start_time: string; end_time: string; planned_hours: number;
  status: string; kind: string; source: string; shift_code: string | null; note: string | null; swap_pending?: boolean }
export interface SwapOption extends MyShift { employee: string; employee_number: string | null; problems: string[] }
export interface Swap { id: string; status: string; reason: string | null; requester_id: string; requester: string; partner_id: string; partner: string;
  requester_shift: MyShift | null; partner_shift: MyShift | null; partner_note: string | null; decided_by: string | null; decided_at: string | null;
  decision_note: string | null; created_at: string | null; for_me?: boolean }

const d = <T,>(p: Promise<{ data: T }>) => p.then((r) => r.data);

export const rotaApi = {
  templates: () => d(api.get<RotaTemplate[]>(`${B}/templates`)),
  patterns: () => d(api.get<RotaPattern[]>(`${B}/patterns`)),
  createPattern: (b: Partial<RotaPattern>) => d(api.post<RotaPattern>(`${B}/patterns`, b)),
  updatePattern: (id: string, b: Partial<RotaPattern>) => d(api.put<RotaPattern>(`${B}/patterns/${id}`, b)),
  deletePattern: (id: string) => d(api.delete(`${B}/patterns/${id}`)),
  previewSaved: (id: string, p: { start?: string; days?: number; offset?: number; crews?: number; crew_offset?: number }) =>
    d(api.get<{ days: RotaDay[]; coverage: any }>(`${B}/patterns/${id}/preview`, { params: p })),
  preview: (b: { cycle: string[]; shifts: Record<string, RotaShiftDef>; start?: string; days?: number; offset?: number; crews?: number; crew_offset?: number }) =>
    d(api.post<{ days: RotaDay[]; coverage: any }>(`${B}/preview`, b)),
  assignments: () => d(api.get<RotaAssignment[]>(`${B}/assignments`)),
  assign: (b: { pattern_id: string; employee_ids?: string[]; department?: string; crew?: string; start_date: string; end_date?: string; offset_days?: number; stagger_days?: number }) =>
    d(api.post<{ created: number }>(`${B}/assignments`, b)),
  endAssignment: (id: string) => d(api.delete<{ ok: boolean; removed_planned_shifts: number }>(`${B}/assignments/${id}`)),
  generate: (b: { start?: string; weeks?: number; employee_ids?: string[]; dry_run: boolean; overwrite_manual?: boolean; overwrite_published?: boolean }) =>
    d(api.post<RotaGenerate>(`${B}/generate`, b)),
  settings: () => d(api.get<RotaSettings>(`${B}/settings`)),
  saveSettings: (b: { min_rest_hours: number; weeks_ahead: number; auto_generate: boolean; swaps_enabled: boolean }) => d(api.put<RotaSettings>(`${B}/settings`, b)),
  myShifts: (start?: string, end?: string) => d(api.get<{ start: string; end: string; swaps_enabled: boolean; shifts: MyShift[] }>(`${B}/my-shifts`, { params: { start, end } })),
  swapOptions: (shift_id: string) => d(api.get<{ shift: MyShift; options: SwapOption[] }>(`${B}/swap-options`, { params: { shift_id } })),
  swaps: (scope: "mine" | "team" | "all" = "mine") => d(api.get<Swap[]>(`${B}/swaps`, { params: { scope } })),
  requestSwap: (b: { my_shift_id: string; partner_shift_id: string; reason?: string }) => d(api.post<Swap>(`${B}/swaps`, b)),
  swapAct: (id: string, act: "accept" | "decline" | "approve" | "reject" | "cancel", note?: string) =>
    d(api.post<Swap>(`${B}/swaps/${id}/${act}`, act === "cancel" ? {} : { note })),
};

export function rotaErr(e: any, fallback = "Something went wrong"): string {
  const det = e?.response?.data?.detail;
  if (!det) return e?.message || fallback;
  if (typeof det === "string") return det;
  if (Array.isArray(det)) return det.map((x: any) => x?.msg || String(x)).join(" ");
  return det.message || fallback;
}

export const SWAP_LABEL: Record<string, string> = {
  pending_partner: "Waiting for colleague", pending_manager: "Waiting for manager", approved: "Approved",
  declined: "Declined by colleague", rejected: "Rejected by manager", cancelled: "Withdrawn",
};
