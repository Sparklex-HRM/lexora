import api from "../../api/client";
import type { Row } from "./api";

/* G4 (2026-10-23): objectives & key results, 360 feedback, improvement plans, 1:1s.
   Who sees what is decided by the server; these screens only show what comes back. */
const d = <T = any>(p: Promise<{ data: T }>) => p.then((r) => r.data);

export const okrApi = {
  list: (cycle_id?: string, scope = "all") => d<Row>(api.get("/performance/okrs", { params: { cycle_id: cycle_id || undefined, scope } })),
  create: (body: Row) => d<Row>(api.post("/performance/okrs", body)),
  update: (id: string, body: Row) => d<Row>(api.patch(`/performance/okrs/${id}`, body)),
  status: (id: string, status: string, reason?: string) => d<Row>(api.post(`/performance/okrs/${id}/status`, { status, reason: reason || undefined })),
  addKr: (id: string, body: Row) => d<Row>(api.post(`/performance/okrs/${id}/key-results`, body)),
  updateKr: (krId: string, body: Row) => d<Row>(api.patch(`/performance/okrs/key-results/${krId}`, body)),
  deleteKr: (krId: string) => d<Row>(api.delete(`/performance/okrs/key-results/${krId}`)),
  checkins: (id: string) => d<Row[]>(api.get(`/performance/okrs/${id}/checkins`)),
  checkin: (id: string, body: Row) => d<Row>(api.post(`/performance/okrs/${id}/checkins`, body)),
};

export const fbApi = {
  defaults: () => d<Row>(api.get("/performance/feedback/questions/default")),
  rounds: () => d<Row>(api.get("/performance/feedback/rounds")),
  createRound: (body: Row) => d<Row>(api.post("/performance/feedback/rounds", body)),
  updateRound: (id: string, body: Row) => d<Row>(api.patch(`/performance/feedback/rounds/${id}`, body)),
  round: (id: string) => d<Row>(api.get(`/performance/feedback/rounds/${id}`)),
  addSubjects: (id: string, body: Row) => d<Row>(api.post(`/performance/feedback/rounds/${id}/subjects`, body)),
  advance: (id: string, action: string) => d<Row>(api.post(`/performance/feedback/rounds/${id}/advance`, { action })),
  nominate: (subjectId: string, body: Row) => d<Row>(api.post(`/performance/feedback/subjects/${subjectId}/nominations`, body)),
  decide: (nomId: string, approve: boolean) => d<Row>(api.post(`/performance/feedback/nominations/${nomId}/decide`, { approve })),
  withdraw: (nomId: string) => d<Row>(api.delete(`/performance/feedback/nominations/${nomId}`)),
  myRequests: () => d<Row>(api.get("/performance/feedback/my-requests")),
  submit: (nomId: string, answers: Row) => d<Row>(api.post(`/performance/feedback/nominations/${nomId}/submit`, { answers })),
  decline: (nomId: string) => d<Row>(api.post(`/performance/feedback/nominations/${nomId}/decline`, {})),
  report: (subjectId: string) => d<Row>(api.get(`/performance/feedback/subjects/${subjectId}/report`)),
};

export const pipApi = {
  list: () => d<Row>(api.get("/performance/pips")),
  get: (id: string) => d<Row>(api.get(`/performance/pips/${id}`)),
  create: (body: Row) => d<Row>(api.post("/performance/pips", body)),
  update: (id: string, body: Row) => d<Row>(api.patch(`/performance/pips/${id}`, body)),
  addItem: (id: string, body: Row) => d<Row>(api.post(`/performance/pips/${id}/items`, body)),
  updateItem: (itemId: string, body: Row) => d<Row>(api.patch(`/performance/pips/items/${itemId}`, body)),
  deleteItem: (itemId: string) => d<Row>(api.delete(`/performance/pips/items/${itemId}`)),
  start: (id: string) => d<Row>(api.post(`/performance/pips/${id}/start`)),
  checkin: (id: string, cid: string, body: Row) => d<Row>(api.post(`/performance/pips/${id}/checkins/${cid}`, body)),
  ack: (id: string, status: string, comment: string) => d<Row>(api.post(`/performance/pips/${id}/ack`, { status, comment })),
  outcome: (id: string, body: Row) => d<Row>(api.post(`/performance/pips/${id}/outcome`, body)),
  cancel: (id: string, reason: string) => d<Row>(api.post(`/performance/pips/${id}/cancel`, { reason })),
  exitLink: (id: string, exit_case_id: string) => d<Row>(api.post(`/performance/pips/${id}/exit-link`, { exit_case_id })),
};

export const oneApi = {
  list: () => d<Row>(api.get("/performance/one-on-ones")),
  get: (id: string) => d<Row>(api.get(`/performance/one-on-ones/${id}`)),
  create: (body: Row) => d<Row>(api.post("/performance/one-on-ones", body)),
  update: (id: string, body: Row) => d<Row>(api.patch(`/performance/one-on-ones/${id}`, body)),
  complete: (id: string) => d<Row>(api.post(`/performance/one-on-ones/${id}/complete`)),
  cancel: (id: string) => d<Row>(api.post(`/performance/one-on-ones/${id}/cancel`)),
};

export const BAND_LABEL: Record<string, string> = { on_track: "On track", at_risk: "At risk", off_track: "Off track" };
export const BAND_CLASS: Record<string, string> = { on_track: "badge-green", at_risk: "badge-amber", off_track: "badge-red" };

export function Progress({ pct }: { pct?: number | null }) {
  const v = Math.max(0, Math.min(100, Number(pct || 0)));
  return (
    <span className="inline-flex items-center gap-1.5 min-w-[7rem]">
      <span className="h-1.5 w-16 rounded-full bg-gray-100 overflow-hidden"><span className="block h-full bg-primary-500" style={{ width: `${v}%` }} /></span>
      <span className="tabular-nums text-[11px] text-gray-600">{v.toFixed(0)}%</span>
    </span>
  );
}
