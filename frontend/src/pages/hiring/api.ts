import axios from "axios";
import api from "../../api/client";

/* Hiring API (signed-in). Rows are typed loosely on purpose: the backend
   serialises table rows and the screens read named fields. */
export type Row = Record<string, any>;
const d = <T = any>(p: Promise<{ data: T }>) => p.then((r) => r.data);

export const hiringApi = {
  settings: () => d<Row>(api.get("/hiring/settings")),
  saveSettings: (body: Row) => d<Row>(api.put("/hiring/settings", body)),

  requisitions: () => d<Row[]>(api.get("/hiring/requisitions")),
  createRequisition: (body: Row) => d<Row>(api.post("/hiring/requisitions", body)),
  updateRequisition: (id: string, body: Row) => d<Row>(api.patch(`/hiring/requisitions/${id}`, body)),
  actRequisition: (id: string, action: string, reason?: string) =>
    d<Row>(api.post(`/hiring/requisitions/${id}/${action}`, { reason })),

  postings: () => d<Row[]>(api.get("/hiring/postings")),
  createPosting: (body: Row) => d<Row>(api.post("/hiring/postings", body)),
  updatePosting: (id: string, body: Row) => d<Row>(api.patch(`/hiring/postings/${id}`, body)),

  candidates: () => d<Row[]>(api.get("/hiring/candidates")),
  checkDuplicate: (body: Row) => d<{ possible_duplicates: Row[] }>(api.post("/hiring/candidates/check-duplicate", body)),
  createCandidate: (body: Row) => d<Row>(api.post("/hiring/candidates", body)),
  updateCandidate: (id: string, body: Row) => d<Row>(api.patch(`/hiring/candidates/${id}`, body)),
  mergeCandidate: (id: string, into: string) => d<Row>(api.post(`/hiring/candidates/${id}/merge-into/${into}`)),
  uploadDocument: (candidateId: string, file: File, kind: string, applicationId?: string) => {
    const f = new FormData();
    f.append("file", file); f.append("kind", kind);
    if (applicationId) f.append("application_id", applicationId);
    return d<Row>(api.post(`/hiring/candidates/${candidateId}/documents`, f));
  },
  documentBlob: (id: string) => api.get(`/hiring/documents/${id}`, { responseType: "blob" }).then((r) => r.data as Blob),

  applications: (params?: Row) => d<Row[]>(api.get("/hiring/applications", { params })),
  application: (id: string) => d<Row>(api.get(`/hiring/applications/${id}`)),
  createApplication: (body: Row) => d<Row>(api.post("/hiring/applications", body)),
  move: (ids: string[], to_stage: string, reason?: string) =>
    d<{ moved: string[]; failed: { id: string; reason: string }[] }>(api.post("/hiring/applications/move", { ids, to_stage, reason })),
  addNote: (appId: string, body: string) => d<Row>(api.post(`/hiring/applications/${appId}/notes`, { body })),

  scheduleInterview: (appId: string, body: Row) => d<Row>(api.post(`/hiring/applications/${appId}/interviews`, body)),
  updateInterview: (id: string, body: Row) => d<Row>(api.patch(`/hiring/interviews/${id}`, body)),
  myInterviews: () => d<{ linked: boolean; items: Row[] }>(api.get("/hiring/interviews/mine")),
  feedback: (id: string, body: Row) => d<Row>(api.post(`/hiring/interviews/${id}/feedback`, body)),

  offers: () => d<Row[]>(api.get("/hiring/offers")),
  previewCtc: (annual_ctc: number) => d<Row>(api.post("/hiring/offers/preview-ctc", { annual_ctc })),
  createOffer: (appId: string, body: Row) => d<Row>(api.post(`/hiring/applications/${appId}/offer`, body)),
  updateOffer: (id: string, body: Row) => d<Row>(api.patch(`/hiring/offers/${id}`, body)),
  actOffer: (id: string, action: string, body: Row = {}) => d<Row>(api.post(`/hiring/offers/${id}/${action}`, body)),
  syncJoined: () => d<Row>(api.post("/hiring/sync-joined")),

  reports: () => d<Row>(api.get("/hiring/reports")),

  /* ── D2: job boards ── */
  boards: (postingId: string) => d<Row>(api.get(`/hiring/postings/${postingId}/boards`)),
  setBoard: (postingId: string, board: string, enabled: boolean) => d<Row>(api.put(`/hiring/postings/${postingId}/boards/${board}`, { enabled })),
  boardsSummary: () => d<Row>(api.get("/hiring/boards")),
  exportText: (postingId: string, board: string) => api.get(`/hiring/postings/${postingId}/export/${board}`, { responseType: "text" }).then((r) => r.data as string),
  exportCsvBlob: () => api.get("/hiring/postings/export.csv", { responseType: "blob" }).then((r) => r.data as Blob),
  /* ── D2: scheduling + calendars ── */
  calendar: () => d<Row>(api.get("/hiring/calendar")),
  connectCalendar: (provider: string) => d<Row>(api.post(`/hiring/calendar/${provider}/connect`)),
  disconnectCalendar: (provider: string) => d<Row>(api.delete(`/hiring/calendar/${provider}`)),
  proposeSlots: (ivId: string, body: Row) => d<Row>(api.post(`/hiring/interviews/${ivId}/slots`, body)),
  scheduling: (ivId: string) => d<Row>(api.get(`/hiring/interviews/${ivId}/scheduling`)),
  syncCalendar: (ivId: string) => d<Row>(api.post(`/hiring/interviews/${ivId}/sync-calendar`, {})),
  reschedule: (ivId: string, body: Row) => d<Row>(api.post(`/hiring/interviews/${ivId}/reschedule`, body)),
  cancelInterview: (ivId: string, reason: string) => d<Row>(api.post(`/hiring/interviews/${ivId}/cancel`, { reason })),
  sendReminders: () => d<Row>(api.post("/hiring/interviews/send-reminders")),
  /* ── D2: scorecards ── */
  scorecards: () => d<Row>(api.get("/hiring/scorecards")),
  saveScorecard: (body: Row) => d<Row>(api.post("/hiring/scorecards", body)),
  deleteScorecard: (id: string) => d<Row>(api.delete(`/hiring/scorecards/${id}`)),
  interviewScorecard: (ivId: string) => d<Row>(api.get(`/hiring/interviews/${ivId}/scorecard`)),
  submitScorecard: (ivId: string, body: Row) => d<Row>(api.post(`/hiring/interviews/${ivId}/scorecard-feedback`, body)),
  applicationScorecard: (appId: string) => d<Row>(api.get(`/hiring/applications/${appId}/scorecard`)),
  /* ── D2: offer approval chain ── */
  submitApproval: (offerId: string) => d<Row>(api.post(`/hiring/offers/${offerId}/submit-approval`)),
  approval: (offerId: string) => d<Row>(api.get(`/hiring/offers/${offerId}/approval`)),
  decideApproval: (offerId: string, decision: "approve" | "reject", comment?: string) => d<Row>(api.post(`/hiring/offers/${offerId}/approval/${decision}`, { comment })),
  /* ── D2: referrals + bonus ── */
  referralJobs: () => d<Row>(api.get("/hiring/referrals/jobs")),
  refer: (form: FormData) => d<Row>(api.post("/hiring/referrals", form)),
  myReferrals: () => d<{ linked: boolean; items: Row[] }>(api.get("/hiring/referrals/mine")),
  referrals: () => d<Row[]>(api.get("/hiring/referrals")),
  referralRule: (reqId: string) => d<Row>(api.get(`/hiring/requisitions/${reqId}/referral-rule`)),
  saveReferralRule: (reqId: string, body: Row) => d<Row>(api.put(`/hiring/requisitions/${reqId}/referral-rule`, body)),
  referralBonuses: () => d<Row>(api.get("/hiring/referral-bonuses")),
  syncReferralBonuses: () => d<Row>(api.post("/hiring/referral-bonuses/sync")),
  bonusAction: (id: string, action: string, body: Row = {}) => d<Row>(api.post(`/hiring/referral-bonuses/${id}/${action}`, body)),
  bonusesCsvBlob: () => api.get("/hiring/referral-bonuses/export.csv", { responseType: "blob" }).then((r) => r.data as Blob),
  /* ── D2: candidate messages ── */
  commsTemplates: () => d<Row>(api.get("/hiring/comms/templates")),
  saveCommsTemplates: (items: Row[]) => d<Row>(api.put("/hiring/comms/templates", items)),
  commsPreview: (body: Row) => d<Row>(api.post("/hiring/comms/preview", body)),
  commsLog: (candidateId: string) => d<Row[]>(api.get("/hiring/comms/log", { params: { candidate_id: candidateId } })),
  setCandidateComms: (candId: string, body: Row) => d<Row>(api.put(`/hiring/candidates/${candId}/comms`, body)),
};

export const BOARD_LABELS: Record<string, string> = { indeed: "Indeed", google_jobs: "Google for Jobs", linkedin: "LinkedIn", naukri: "Naukri" };

/* Public pages (careers, offer link): no token, no redirect to /login on an error. */
const pub = axios.create({ baseURL: (import.meta.env.VITE_API_BASE || "/api") + "/hiring/public" });
export const publicApi = {
  careers: (org: string, params?: Row) => d<Row>(pub.get(`/careers/${encodeURIComponent(org)}`, { params })),
  interview: (token: string) => d<Row>(pub.get(`/interview/${encodeURIComponent(token)}`)),
  pickSlot: (token: string, body: Row) => d<Row>(pub.post(`/interview/${encodeURIComponent(token)}/pick`, body)),
  job: (org: string, id: string) => d<Row>(pub.get(`/careers/${encodeURIComponent(org)}/${id}`)),
  apply: (org: string, id: string, form: FormData) => d<Row>(pub.post(`/careers/${encodeURIComponent(org)}/${id}/apply`, form)),
  offer: (token: string) => d<Row>(pub.get(`/offer/${encodeURIComponent(token)}`)),
  answer: (token: string, accept: boolean, body: Row) =>
    d<Row>(pub.post(`/offer/${encodeURIComponent(token)}/${accept ? "accept" : "decline"}`, body)),
};

/** A backend error as one plain sentence (detail may be a string or an object). */
export const err = (e: any, fallback = "Something went wrong. Try again."): string => {
  const det = e?.response?.data?.detail;
  if (typeof det === "string") return det;
  if (det && typeof det === "object" && typeof det.message === "string") return det.message;
  if (e?.response?.data?.detail === "module_not_subscribed") return "Your plan does not include this module.";
  return fallback;
};

export const STAGES: { key: string; label: string }[] = [
  { key: "applied", label: "Applied" }, { key: "screening", label: "Screening" }, { key: "interview", label: "Interview" },
  { key: "selected", label: "Selected" }, { key: "offer", label: "Offer" }, { key: "joined", label: "Joined" },
  { key: "on_hold", label: "On hold" }, { key: "rejected", label: "Rejected" }, { key: "withdrawn", label: "Withdrawn" },
];
export const stageLabel = (k?: string) => STAGES.find((s) => s.key === k)?.label || k || "";
export const SOURCES: Record<string, string> = {
  careers_page: "Careers page", referral: "Employee referral", agency: "Agency", walk_in: "Walk-in", job_board: "Job board",
};
export const EMP_TYPES: Record<string, string> = { full_time: "Full time", part_time: "Part time", contract: "Contract", seasonal: "Seasonal" };
export const fmtDate = (iso?: string | null) => (iso ? new Date(iso.length === 10 ? iso + "T00:00:00" : iso).toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" }) : "");
export const fmtDateTime = (iso?: string | null) => (iso ? new Date(iso).toLocaleString(undefined, { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "");

const TONE: Record<string, string> = {
  draft: "badge-purple", pending_approval: "badge-amber", open: "badge-green", on_hold: "badge-amber", filled: "badge-teal",
  cancelled: "badge-red", applied: "badge-purple", screening: "badge-purple", interview: "badge-amber", selected: "badge-teal",
  offer: "badge-teal", joined: "badge-green", rejected: "badge-red", withdrawn: "badge-red", approved: "badge-teal",
  sent: "badge-amber", accepted: "badge-green", declined: "badge-red", expired: "badge-red", scheduled: "badge-amber",
  submitted: "badge-green", pending: "badge-amber", proposed: "badge-amber", returned: "badge-red", active: "badge-green",
  published: "badge-green", closed: "badge-purple", self: "badge-purple", manager: "badge-amber", reviewer: "badge-amber",
  calibration: "badge-teal", acknowledged: "badge-green", disagreed: "badge-red", sending: "badge-amber",
};
export const tone = (s?: string) => TONE[s || ""] || "badge-purple";
