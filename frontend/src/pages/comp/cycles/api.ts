import api from "../../../api/client";

/* Compensation planning cycles (D5) — the employee service at /performance/comp/*. */
export type Row = Record<string, any>;
const d = <T = any>(p: Promise<{ data: T }>) => p.then((r) => r.data);
const B = "/performance/comp";

export const ccApi = {
  settings: () => d<Row>(api.get(`${B}/settings`)),
  saveSettings: (body: Row) => d<Row>(api.put(`${B}/settings`, body)),

  cycles: () => d<Row[]>(api.get(`${B}/cycles`)),
  cycle: (id: string) => d<Row>(api.get(`${B}/cycles/${id}`)),
  create: (body: Row) => d<Row>(api.post(`${B}/cycles`, body)),
  update: (id: string, body: Row) => d<Row>(api.patch(`${B}/cycles/${id}`, body)),
  eligibility: (id: string) => d<Row>(api.get(`${B}/cycles/${id}/eligibility`)),
  populate: (id: string, reset = false) => d<Row>(api.post(`${B}/cycles/${id}/populate`, null, { params: { reset: reset ? 1 : 0 } })),
  move: (id: string, what: "open" | "calibrate" | "reopen" | "withdraw" | "close") => d<Row>(api.post(`${B}/cycles/${id}/move/${what}`)),

  worksheet: (id: string, params?: Row) => d<Row>(api.get(`${B}/cycles/${id}/worksheet`, { params })),
  saveLine: (id: string, lineId: string, body: Row) => d<Row>(api.put(`${B}/cycles/${id}/lines/${lineId}`, body)),
  bulk: (id: string, body: Row) => d<Row>(api.post(`${B}/cycles/${id}/lines/bulk`, body)),
  lock: (id: string, lineIds: string[], lock: boolean) => d<Row>(api.post(`${B}/cycles/${id}/${lock ? "lock" : "unlock"}`, { line_ids: lineIds })),
  exportCsv: (id: string) => api.get(`${B}/cycles/${id}/export.csv`, { responseType: "blob" }).then((r) => r.data as Blob),
  importCsv: (id: string, csv: string) => d<Row>(api.post(`${B}/cycles/${id}/import`, { csv })),

  calibration: (id: string) => d<Row>(api.get(`${B}/cycles/${id}/calibration`)),
  submit: (id: string) => d<Row>(api.post(`${B}/cycles/${id}/submit`)),
  approval: (id: string) => d<Row>(api.get(`${B}/cycles/${id}/approval`)),
  decide: (id: string, decision: "approve" | "reject", comment: string) => d<Row>(api.post(`${B}/cycles/${id}/approval/${decision}`, { comment })),
  apply: (id: string) => d<Row>(api.post(`${B}/cycles/${id}/apply`)),
  progress: (id: string) => d<Row>(api.get(`${B}/cycles/${id}/apply-progress`)),
  analytics: (id: string) => d<Row>(api.get(`${B}/cycles/${id}/analytics`)),
  myWorksheets: () => d<Row[]>(api.get(`${B}/my-worksheets`)),
};

export const STATUS_LABEL: Record<string, string> = {
  draft: "Draft", open: "Open to managers", calibration: "HR calibration", approval: "Awaiting approval",
  applying: "Applying", applied: "Applied", closed: "Closed",
};
export const pct = (v: any) => (v == null || v === "" ? "—" : `${Number(v).toFixed(Number(v) % 1 ? 2 : 0)}%`);
export const cr = (v: any) => (v == null ? "—" : Number(v).toFixed(2));
export const num0 = (v: any) => (v == null ? "—" : Number(v).toLocaleString("en-CA", { maximumFractionDigits: 0 }));
