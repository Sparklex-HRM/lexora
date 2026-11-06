import api from "./client";

/** Biometric / attendance-device punch import (time/biometric/*). */

const T = "/time/biometric";
export const biometricApi = {
  devices: () => api.get<any[]>(`${T}/devices`).then((r) => r.data),
  createDevice: (body: any) => api.post<any>(`${T}/devices`, body).then((r) => r.data),
  updateDevice: (id: string, body: any) => api.patch<any>(`${T}/devices/${id}`, body).then((r) => r.data),
  map: () => api.get<any[]>(`${T}/map`).then((r) => r.data),
  saveMap: (rows: any[]) => api.put<any>(`${T}/map`, { rows }).then((r) => r.data),
  preview: (file: File, device_id?: string, vendor?: string) => {
    const fd = new FormData(); fd.append("file", file); if (device_id) fd.append("device_id", device_id); if (vendor) fd.append("vendor", vendor);
    return api.post<any>(`${T}/import/preview`, fd, { headers: { "Content-Type": "multipart/form-data" } }).then((r) => r.data);
  },
  import: (file: File, device_id?: string, timezone_name?: string, vendor?: string) => {
    const fd = new FormData(); fd.append("file", file); if (vendor) fd.append("vendor", vendor);
    if (device_id) fd.append("device_id", device_id); if (timezone_name) fd.append("timezone_name", timezone_name);
    return api.post<any>(`${T}/import`, fd, { headers: { "Content-Type": "multipart/form-data" } }).then((r) => r.data);
  },
  imports: () => api.get<any[]>(`${T}/imports`).then((r) => r.data),
};
