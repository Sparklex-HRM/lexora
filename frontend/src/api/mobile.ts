import api from "./client";

// Mobile punch (Stream G4): geo-fence, selfie, device binding, offline sync. Server: time service /time/mobile/*.

export interface MobileSettings {
  outside_fence: "flag" | "refuse"; selfie: "off" | "optional" | "required"; device_binding: boolean; refuse_mock: boolean;
  max_accuracy_m: number; refuse_poor_accuracy: boolean; late_sync_minutes: number; selfie_max_kb: number;
}
export interface Fence { id: string; name: string; location_id?: string | null; lat: number; lng: number; radius_m: number; active: boolean; }
export interface Device { id: string; employee_id: string; employee_name?: string | null; device_id: string; label?: string | null; status: string; registered_at?: string | null; decided_by?: string | null; decided_at?: string | null; }
export interface PunchMeta {
  id: string; punch_id?: string | null; employee_id: string; employee_name?: string | null; employee_number?: string | null; department?: string | null;
  client_id: string; direction: string; work_date?: string | null; device_time?: string | null; server_time?: string | null;
  synced_late: boolean; late_by_minutes: number; lat?: number | null; lng?: number | null; accuracy_m?: number | null; mock_location: boolean;
  fence_name?: string | null; distance_m?: number | null; inside_fence?: boolean | null; has_selfie: boolean; selfie_url?: string | null;
  flags: string[]; flag_labels: string[]; refused: boolean; refused_reason?: string | null; source: string;
  duplicate?: boolean; accepted?: boolean; status?: string; at?: string;
}
export interface MobilePunchIn {
  client_id: string; direction: "in" | "out" | "break_start" | "break_end"; device_time: string;
  lat?: number | null; lng?: number | null; accuracy_m?: number | null; mock_location?: boolean;
  device_id?: string | null; device_label?: string | null; selfie_base64?: string | null; note?: string | null;
}
export interface SyncResult { client_id: string; ok: boolean; result?: PunchMeta; error?: string; status?: number; }
export interface PunchContext {
  settings: MobileSettings; sites: { name: string; radius_m: number }[]; my_devices: Device[]; today: PunchMeta[]; server_time: string; employee_id?: string | null;
}

const d = <T,>(p: Promise<{ data: T }>) => p.then((r) => r.data);

export const mobileApi = {
  context: () => d(api.get<PunchContext>("/time/mobile/context")),
  punch: (body: MobilePunchIn) => d(api.post<PunchMeta>("/time/mobile/punch", body)),
  sync: (punches: MobilePunchIn[]) => d(api.post<{ results: SyncResult[]; server_time: string }>("/time/mobile/sync", { punches })),
  punches: (employee_id: string, day?: string) => d(api.get<PunchMeta[]>("/time/mobile/punches", { params: { employee_id, ...(day ? { day } : {}) } })),
  today: (day?: string) => d(api.get<{ date: string; rows: PunchMeta[]; summary: { punches: number; outside: number; late_sync: number; refused: number; people: number } }>("/time/mobile/today", { params: day ? { day } : {} })),
  selfieUrl: (metaId: string) => `${import.meta.env.VITE_API_BASE || "/api"}/time/mobile/selfie/${metaId}`,
  selfieBlob: (metaId: string) => api.get(`/time/mobile/selfie/${metaId}`, { responseType: "blob" }).then((r) => URL.createObjectURL(r.data)),
  sites: () => d(api.get<{ fences: Fence[]; locations_without_fence: { id: string; name: string; city?: string | null }[]; can_edit: boolean }>("/time/mobile/sites")),
  createSite: (body: Partial<Fence>) => d(api.post<Fence>("/time/mobile/sites", body)),
  updateSite: (id: string, body: Partial<Fence>) => d(api.patch<Fence>(`/time/mobile/sites/${id}`, body)),
  settings: () => d(api.get<{ value: MobileSettings; can_edit: boolean }>("/time/mobile/settings")),
  saveSettings: (value: Partial<MobileSettings>) => d(api.put<{ value: MobileSettings }>("/time/mobile/settings", { value })),
  devices: (status?: string) => d(api.get<Device[]>("/time/mobile/devices", { params: status ? { status } : {} })),
  decideDevice: (id: string, decision: "approve" | "reject", comment?: string) => d(api.post<Device>(`/time/mobile/devices/${id}/${decision}`, { comment })),
  revokeDevice: (id: string) => d(api.post<Device>(`/time/mobile/devices/${id}/revoke`)),
};

/** A stable, non-identifying source for the device fingerprint (hashed on the server). */
export function deviceSource(): { id: string; label: string } {
  let salt = "";
  try { salt = localStorage.getItem("lx-device-salt") || ""; if (!salt) { salt = Math.random().toString(36).slice(2) + Date.now().toString(36); localStorage.setItem("lx-device-salt", salt); } } catch { /* ignore */ }
  const ua = navigator.userAgent || "";
  const label = /Android/i.test(ua) ? "Android phone" : /iPhone|iPad/i.test(ua) ? "iPhone / iPad" : /Windows/i.test(ua) ? "Windows browser" : /Mac/i.test(ua) ? "Mac browser" : "Browser";
  return { id: `${salt}|${ua}|${screen.width}x${screen.height}`, label: `${label} · ${screen.width}x${screen.height}` };
}
