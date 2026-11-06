import api from "../../api/client";

export type Row = Record<string, any>;
const d = <T = any>(p: Promise<{ data: T }>) => p.then((r) => r.data);

export const perfApi = {
  settings: () => d<Row>(api.get("/performance/settings")),
  saveSettings: (body: Row) => d<Row>(api.put("/performance/settings", body)),

  goalCycles: () => d<Row[]>(api.get("/performance/goal-cycles")),
  createGoalCycle: (body: Row) => d<Row>(api.post("/performance/goal-cycles", body)),
  actGoalCycle: (id: string, action: "close" | "reopen") => d<Row>(api.post(`/performance/goal-cycles/${id}/${action}`)),

  goals: (cycle_id: string, scope: string) => d<Row[]>(api.get("/performance/goals", { params: { cycle_id, scope } })),
  createGoal: (body: Row) => d<Row>(api.post("/performance/goals", body)),
  updateGoal: (id: string, body: Row) => d<Row>(api.patch(`/performance/goals/${id}`, body)),
  deleteGoal: (id: string) => d<Row>(api.delete(`/performance/goals/${id}`)),
  actGoals: (action: "submit" | "approve" | "return", body: Row) => d<Row>(api.post(`/performance/goals/${action}`, body)),
  checkins: (goalId: string) => d<Row[]>(api.get(`/performance/goals/${goalId}/checkins`)),
  addCheckin: (goalId: string, body: Row) => d<Row>(api.post(`/performance/goals/${goalId}/checkins`, body)),
  library: () => d<Row[]>(api.get("/performance/goal-library")),
  addLibrary: (body: Row) => d<Row>(api.post("/performance/goal-library", body)),
  removeLibrary: (id: string) => d<Row>(api.delete(`/performance/goal-library/${id}`)),

  cycles: () => d<Row[]>(api.get("/performance/cycles")),
  createCycle: (body: Row) => d<Row>(api.post("/performance/cycles", body)),
  updateCycle: (id: string, body: Row) => d<Row>(api.patch(`/performance/cycles/${id}`, body)),
  eligibility: (id: string) => d<Row[]>(api.get(`/performance/cycles/${id}/eligibility`)),
  actCycle: (id: string, action: "launch" | "remind" | "publish") => d<Row>(api.post(`/performance/cycles/${id}/${action}`)),
  calibration: (id: string) => d<Row>(api.get(`/performance/cycles/${id}/calibration`)),
  promotions: (id: string) => d<Row[]>(api.get(`/performance/cycles/${id}/promotions`)),

  reviews: (scope: string, cycle_id?: string) => d<Row[]>(api.get("/performance/reviews", { params: { scope, cycle_id } })),
  review: (id: string) => d<Row>(api.get(`/performance/reviews/${id}`)),
  saveDraft: (id: string, payload: Row) => d<Row>(api.put(`/performance/reviews/${id}/draft`, { payload })),
  submit: (id: string, payload: Row) => d<Row>(api.post(`/performance/reviews/${id}/submit`, { payload })),
  moveStage: (id: string, to_stage: string, reason: string) => d<Row>(api.post(`/performance/reviews/${id}/move-stage`, { to_stage, reason })),
  calibrate: (id: string, final_score: number, reason: string, promotion_recommended?: boolean) =>
    d<Row>(api.post(`/performance/reviews/${id}/calibrate`, { final_score, reason, ...(promotion_recommended === undefined ? {} : { promotion_recommended }) })),
  ack: (id: string, status: string, comment: string) => d<Row>(api.post(`/performance/reviews/${id}/ack`, { status, comment })),

  proposal: (cycleId: string) => d<Row>(api.get(`/performance/cycles/${cycleId}/increment-proposal`)),
  saveProposal: (id: string, body: Row) => d<Row>(api.put(`/performance/increment-proposals/${id}`, body)),
  rebuildProposal: (id: string) => d<Row>(api.post(`/performance/increment-proposals/${id}/rebuild`)),
  sendProposal: (id: string) => d<Row>(api.post(`/performance/increment-proposals/${id}/send`)),
};

export const STAGE_LABEL: Record<string, string> = {
  self: "Self review", manager: "Manager review", reviewer: "Reviewer", calibration: "HR calibration", published: "Published",
};
