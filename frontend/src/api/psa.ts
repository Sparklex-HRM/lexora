import api from "./client";

/** Stream G2 — Project Services (PSA): /ops/psa/* (ops service) + /time/psa/* (time service). */

const P = "/ops/psa";
const q = (params: Record<string, any>) =>
  Object.fromEntries(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ""));
const d = <T = any>(p: Promise<{ data: T }>) => p.then((r) => r.data);

export interface RateCard {
  id: string; client?: string | null; project_id?: string | null; project_name?: string | null; role?: string | null;
  employee_id?: string | null; employee_name?: string | null; rate: number; unit: "hour" | "day"; currency: string;
  effective_from: string; effective_to?: string | null; note?: string | null;
}
export interface Assignment {
  id: string; employee_id: string; employee_name?: string; employee_number?: string; department?: string;
  project_id: string; project_name?: string; client?: string; role?: string | null; allocation_pct: number;
  start_date: string; end_date?: string | null; billable: boolean; note?: string | null;
}

export const psaApi = {
  settings: () => d(api.get(`${P}/settings`)),
  rateCards: () => d<RateCard[]>(api.get(`${P}/rate-cards`)),
  createRateCard: (body: Partial<RateCard>) => d(api.post(`${P}/rate-cards`, body)),
  updateRateCard: (id: string, body: Partial<RateCard>) => d(api.put(`${P}/rate-cards/${id}`, body)),
  deleteRateCard: (id: string) => d(api.delete(`${P}/rate-cards/${id}`)),

  assignments: (params: { employee_id?: string; project_id?: string } = {}) => d<Assignment[]>(api.get(`${P}/assignments`, { params: q(params) })),
  createAssignment: (body: any) => d(api.post(`${P}/assignments`, body)),
  updateAssignment: (id: string, body: any) => d(api.put(`${P}/assignments/${id}`, body)),
  endAssignment: (id: string) => d(api.delete(`${P}/assignments/${id}`)),
  bench: (as_of?: string) => d(api.get(`${P}/bench`, { params: q({ as_of }) })),

  billableLines: (params: Record<string, any>) => d<any[]>(api.get(`${P}/billable-lines`, { params: q(params) })),
  applyRates: (body: { from_date?: string; to_date?: string; overwrite?: boolean }) => d(api.post(`${P}/billable-lines/apply-rates`, body)),
  utilisation: (month: string, department?: string) => d(api.get(`${P}/utilisation`, { params: q({ month, department }) })),

  draftInvoice: (body: any) => d(api.post(`${P}/invoices/draft`, body)),
  invoices: (params: { client?: string; status?: string } = {}) => d<any[]>(api.get(`${P}/invoices`, { params: q(params) })),
  invoice: (id: string) => d(api.get(`${P}/invoices/${id}`)),
  setInvoiceStatus: (id: string, status: string) => d(api.post(`${P}/invoices/${id}/status`, { status })),
  recordPayment: (id: string, body: { amount: number; paid_on?: string }) => d(api.post(`${P}/invoices/${id}/payment`, body)),
  discardDraft: (id: string) => d(api.delete(`${P}/invoices/${id}`)),

  leakage: (params: { from?: string; to?: string } = {}) => d(api.get(`${P}/leakage`, { params: q(params) })),
  revenue: (params: { from?: string; to?: string; group?: string } = {}) => d(api.get(`${P}/revenue`, { params: q(params) })),
  fxRates: () => d(api.get(`${P}/fx-rates`)),
  addFx: (body: { currency: string; rate_to_home: number; as_of: string; source?: string; note?: string }) => d(api.post(`${P}/fx-rates`, body)),
  deleteFx: (id: string) => d(api.delete(`${P}/fx-rates/${id}`)),
};

export const errText = (e: any, fallback = "Something went wrong"): string => {
  const det = e?.response?.data?.detail;
  if (!det) return e?.message || fallback;
  if (typeof det === "string") return det;
  if (Array.isArray(det)) return det.map((x: any) => x?.msg || String(x)).join(" ");
  return [det.message, ...(Array.isArray(det.errors) ? det.errors : [])].filter(Boolean).join(" ") || fallback;
};

export const monthKey = (dt = new Date()) => `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}`;
export const monthStart = (m: string) => `${m}-01`;
export const monthEnd = (m: string) => {
  const [y, mo] = m.split("-").map(Number);
  return `${m}-${String(new Date(y, mo, 0).getDate()).padStart(2, "0")}`;
};
