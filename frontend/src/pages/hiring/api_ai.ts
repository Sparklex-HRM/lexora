import api from "../../api/client";
import type { Row } from "./api";

/* G6 (2026-10-23): hiring assist — résumé parsing, JD drafts, advisory fit. Nothing here is cached in
   browser storage: résumé fields are personal data and live only in React state while the modal is open. */
const d = <T = any>(p: Promise<{ data: T }>) => p.then((r) => r.data);

export const aiApi = {
  status: () => d<Row>(api.get("/hiring/ai/status")),
  parseFile: (file: File) => { const f = new FormData(); f.append("file", file); return d<Row>(api.post("/hiring/ai/parse-file", f)); },
  parseCandidate: (candidateId: string) => d<Row>(api.post(`/hiring/ai/candidates/${candidateId}/parse`, {})),
  profile: (candidateId: string) => d<Row>(api.get(`/hiring/ai/candidates/${candidateId}/profile`)),
  saveProfile: (candidateId: string, body: Row) => d<Row>(api.put(`/hiring/ai/candidates/${candidateId}/profile`, body)),
  jd: (body: Row) => d<Row>(api.post("/hiring/ai/jd", body)),
  match: (appId: string) => d<Row>(api.post(`/hiring/ai/applications/${appId}/match`)),
  latestMatch: (appId: string) => d<Row | null>(api.get(`/hiring/ai/applications/${appId}/match`)),
  ranking: (reqId: string, refresh = false) => d<Row>(api.get(`/hiring/ai/requisitions/${reqId}/ranking`, { params: { refresh } })),
};

export const cad = (v?: number | null) => (v == null ? "" : `$${Number(v).toLocaleString("en-CA")}`);
export const BAND_TONE: Record<string, string> = { strong: "badge-green", possible: "badge-purple", weak: "badge-amber" };
