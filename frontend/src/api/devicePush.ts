import api from "./client";

/** G3 (2026-10-23) — biometric device push settings + live device health (time service /time/biometric/*). */
export interface DevicePaths { base: string; adms_plain: string; adms_token: string; cosec_server_url: string; http: string; }
export interface DeviceHealth {
  device_id: string; serial: string; name: string; vendor: string; location: string | null; timezone: string; active: boolean;
  status: "online" | "offline" | "never" | "disabled";
  push_configured: boolean; enabled: boolean; protocol: "adms" | "cosec" | "http" | null;
  has_token: boolean; token_hint: string | null; allowed_ips: string[]; direction_mode: string;
  bounce_sec: number; max_backlog_days: number; offline_after_min: number; alert_user_ids: string[];
  last_seen_at: string | null; last_punch_at: string | null; punches_today: number;
  clock_drift_sec: number | null; drift_source: "exact" | "estimate" | null; drift_level: "ok" | "warn" | "bad" | null;
  last_ip: string | null; firmware: string | null; last_batch: Record<string, any>;
  offline_alerted_at: string | null; rejects_today: number; last_reject: string | null; last_reject_at: string | null;
  paths: DevicePaths; token?: string; paths_with_token?: DevicePaths;
}
export interface HealthPanel {
  as_of: string; devices: DeviceHealth[];
  summary: { online: number; offline: number; never: number; disabled: number; punches_today: number; drift_warnings: number };
}
export interface PushSettings {
  enabled: boolean; protocol: string; allowed_ips: string[]; direction_mode: string;
  bounce_sec: number; max_backlog_days: number; offline_after_min: number; alert_user_ids: string[];
}

const T = "/time/biometric";
export const devicePushApi = {
  health: () => api.get<HealthPanel>(`${T}/health`).then((r) => r.data),
  get: (id: string) => api.get<DeviceHealth>(`${T}/devices/${id}/push`).then((r) => r.data),
  save: (id: string, body: PushSettings) => api.put<DeviceHealth>(`${T}/devices/${id}/push`, body).then((r) => r.data),
  newToken: (id: string) => api.post<DeviceHealth>(`${T}/devices/${id}/push/token`).then((r) => r.data),
  checkNow: () => api.post<{ alerts_sent: number }>(`${T}/health/check`).then((r) => r.data),
};

/** Error text from a {code, message} or string detail (masked 500s carry a ref). */
export const apiErr = (e: any, fallback = "Something went wrong") => {
  const d = e?.response?.data?.detail;
  if (d && typeof d === "object") return d.code ? `${d.message} (${d.code})` : d.message || fallback;
  const ref = e?.response?.data?.ref;
  return (d ? String(d) : e?.message || fallback) + (ref ? ` (ref ${ref})` : "");
};
