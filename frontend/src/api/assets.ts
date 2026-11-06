import api from "./client";

// Asset register (Stream G4). Server: employee service, /assets/*.
export interface AssetRow {
  id: string; employee_id?: string | null; employee_name?: string | null; item: string; tag?: string | null; category?: string | null; serial?: string | null;
  purchase_date?: string | null; purchase_cost?: number | null; warranty_until?: string | null; warranty_state: string; location?: string | null;
  status: string; issued_on?: string | null; returned_on?: string | null; condition?: string | null; note?: string | null; assigned_by?: string | null;
  ack_at?: string | null; ack_by?: string | null; acknowledged: boolean; return_condition?: string | null; return_note?: string | null; returned_to?: string | null;
  photo_doc_ids: string[]; returned: boolean; created_by?: string | null; created_at?: string | null; updated_at?: string | null;
}
const d = <T,>(p: Promise<{ data: T }>) => p.then((r) => r.data);

export const assetsApi = {
  list: (params: { status?: string; category?: string; employee_id?: string; q?: string }) => d(api.get<{ assets: AssetRow[]; categories: string[]; states: string[]; conditions: string[] }>("/assets", { params })),
  summary: () => d(api.get<any>("/assets/summary")),
  mine: () => d(api.get<{ assets: AssetRow[]; pending_ack: number }>("/assets/mine")),
  outstanding: (employee_id: string) => d(api.get<any>("/assets/outstanding", { params: { employee_id } })),
  create: (body: Record<string, any>) => d(api.post<AssetRow>("/assets", body)),
  update: (id: string, body: Record<string, any>) => d(api.patch<AssetRow>(`/assets/${id}`, body)),
  detail: (id: string) => d(api.get<{ asset: AssetRow; events: { id: string; at: string; actor?: string | null; action: string; note?: string | null }[] }>(`/assets/${id}`)),
  assign: (id: string, body: { employee_id: string; issued_on?: string; note?: string }) => d(api.post<AssetRow>(`/assets/${id}/assign`, body)),
  acknowledge: (id: string) => d(api.post<AssetRow>(`/assets/${id}/acknowledge`)),
  doReturn: (id: string, body: { condition?: string; note?: string; returned_on?: string; lost?: boolean }) => d(api.post<AssetRow>(`/assets/${id}/return`, body)),
  addPhoto: (id: string, file: File) => { const f = new FormData(); f.append("file", file); return d(api.post<{ doc_id: string; photo_doc_ids: string[] }>(`/assets/${id}/photos`, f)); },
  photoBlob: (docId: string) => api.get(`/assets/photos/${docId}`, { responseType: "blob" }).then((r) => URL.createObjectURL(r.data)),
};
