import api from "./client";

// Stream D7 — LMS depth (ops service, /engage/lms/*): paths, auto-assignment rules, question bank,
// SCORM-lite packages, quiz sets, team / report / my-learning views, reminders.

export interface LearningPath { id: string; title: string; description?: string | null; course_ids: string[]; courses: { id: string; title: string }[]; mandatory: boolean; active: boolean; created_by?: string | null; created_at?: string | null; people?: number | null; completed?: number | null; }
export interface LmsRule {
  id: string; name: string; course_id?: string | null; course_title?: string | null; path_id?: string | null; path_title?: string | null;
  roles: string[]; departments: string[]; entities: string[]; locations: string[]; joined_from?: string | null; joined_to?: string | null;
  due_days: number; new_joiners_only: boolean; active: boolean; last_run_at?: string | null; last_assigned: number; created_by?: string | null; created_at?: string | null;
}
export interface LmsQuestion { id: string; tag: string; q: string; options: string[]; answer?: number; active: boolean; created_by?: string | null; created_at?: string | null; }
export interface PathStep { n: number; course_id: string; title?: string | null; status: string; progress_pct: number; assignment_id?: string | null; }
export interface PathProgress { path_id?: string; title?: string; description?: string | null; mandatory?: boolean; due_on?: string | null; steps: PathStep[]; completed: number; total: number; pct: number; status: string; }
export interface AsgRow {
  id: string; course_id: string; course_title?: string | null; category?: string | null; mandatory: boolean; employee_id: string; employee_name?: string | null;
  department?: string | null; manager_name?: string | null; due_on?: string | null; status: string; state: string; completed_at?: string | null;
  quiz_score?: number | null; quiz_attempts: number; certificate_no?: string | null; assigned_at?: string | null; progress_pct: number; path_id?: string | null; rule_id?: string | null;
}
export interface TeamPerson { employee_id: string; employee_name?: string | null; assigned: number; completed: number; overdue: number; in_progress: number; mandatory_pending: number; completion_pct: number | null; }
export interface QuizSet { questions: { q: string; options: string[] }[]; pass_mark: number; attempts: number; attempts_left: number | null; max_attempts: number; from_bank: boolean; }

const d = <T,>(p: Promise<{ data: T }>) => p.then((r) => r.data);
const base = () => import.meta.env.VITE_API_BASE || "/api";

export const learningApi = {
  // paths
  paths: () => d(api.get<{ paths: LearningPath[]; can_manage: boolean }>("/engage/lms/paths")),
  createPath: (body: Record<string, any>) => d(api.post<LearningPath>("/engage/lms/paths", body)),
  updatePath: (id: string, body: Record<string, any>) => d(api.patch<LearningPath>(`/engage/lms/paths/${id}`, body)),
  assignPath: (id: string, body: { employee_ids?: string[]; departments?: string[]; due_on?: string }) => d(api.post<{ assigned: number; already: number }>(`/engage/lms/paths/${id}/assign`, body)),
  pathProgress: (id: string) => d(api.get<{ path: LearningPath; people: (PathProgress & { employee_id: string; employee_name?: string | null })[] }>(`/engage/lms/paths/${id}/progress`)),
  // rules
  rules: () => d(api.get<{ rules: LmsRule[]; note: string }>("/engage/lms/rules")),
  createRule: (body: Record<string, any>) => d(api.post<LmsRule>("/engage/lms/rules", body)),
  updateRule: (id: string, body: Record<string, any>) => d(api.patch<LmsRule>(`/engage/lms/rules/${id}`, body)),
  deleteRule: (id: string) => d(api.delete(`/engage/lms/rules/${id}`)),
  runRule: (id: string) => d(api.post<{ assigned: number }>(`/engage/lms/rules/${id}/run`)),
  runAllRules: () => d(api.post<{ assigned: number; by_rule: Record<string, number> }>("/engage/lms/rules/run-all")),
  // question bank
  questions: (tag?: string) => d(api.get<{ questions: LmsQuestion[]; tags: string[] }>("/engage/lms/questions", { params: tag ? { tag } : {} })),
  addQuestion: (body: { tag: string; q: string; options: string[]; answer: number; active?: boolean }) => d(api.post<LmsQuestion>("/engage/lms/questions", body)),
  updateQuestion: (id: string, body: Record<string, any>) => d(api.patch<LmsQuestion>(`/engage/lms/questions/${id}`, body)),
  deleteQuestion: (id: string) => d(api.delete(`/engage/lms/questions/${id}`)),
  // packages
  uploadPackage: (courseId: string, file: File, title?: string) => { const f = new FormData(); f.append("file", file); return d(api.post<{ package_id: string; entries: number; entry_point: string; modules: any[] }>(`/engage/lms/courses/${courseId}/package`, f, { params: title ? { title } : {} })); },
  packageUrl: (pkgId: string) => `${base()}/engage/lms/packages/${pkgId}/`,
  packageManifest: (pkgId: string) => d(api.get<{ package_id: string; entry_point: string; entries: string[]; size_bytes: number; file_name: string }>(`/engage/lms/packages/${pkgId}/manifest`)),
  packageFile: (pkgId: string, path: string) => api.get(`/engage/lms/packages/${pkgId}/${path}`, { responseType: "blob" }).then((r) => r.data as Blob),
  /** Build a self-contained document for the sandboxed player: index.html with relative asset links swapped for blob URLs. */
  packageDocument: async (pkgId: string): Promise<{ html: string; revoke: () => void }> => {
    const man = await learningApi.packageManifest(pkgId);
    const root = man.entry_point.includes("/") ? man.entry_point.slice(0, man.entry_point.lastIndexOf("/") + 1) : "";
    const index = await learningApi.packageFile(pkgId, man.entry_point);
    let html = await index.text();
    const urls: string[] = [];
    const assets = man.entries.filter((e) => e !== man.entry_point && e.startsWith(root)).slice(0, 400);
    const blobs = await Promise.all(assets.map((e) => learningApi.packageFile(pkgId, e).then((b) => [e, URL.createObjectURL(b)] as const).catch(() => null)));
    for (const hit of blobs) {
      if (!hit) continue;
      const [path, url] = hit;
      urls.push(url);
      const rel = path.slice(root.length);
      for (const cand of [rel, "./" + rel]) {
        html = html.split(`"${cand}"`).join(`"${url}"`).split(`'${cand}'`).join(`'${url}'`).split(`(${cand})`).join(`(${url})`);
      }
    }
    return { html, revoke: () => urls.forEach((u) => URL.revokeObjectURL(u)) };
  },
  // quiz
  quizSet: (aid: string) => d(api.get<QuizSet>(`/engage/lms/assignments/${aid}/quiz-set`)),
  resetQuiz: (aid: string) => d(api.post<{ ok: boolean }>(`/engage/lms/assignments/${aid}/reset`)),
  // views
  team: (managerId?: string) => d(api.get<{ manager_id: string; team_size: number; people: TeamPerson[]; assigned: number; completed: number; overdue: number; completion_pct: number | null; assignments: AsgRow[] }>("/engage/lms/team", { params: managerId ? { manager_id: managerId } : {} })),
  report: (p: { by?: string; course_id?: string; department?: string; state?: string }) => d(api.get<{ by: string; groups: { group: string; assigned: number; completed: number; overdue: number; completion_pct: number | null }[]; rows: AsgRow[]; mandatory: any; as_of: string }>("/engage/lms/report", { params: p })),
  my: () => d(api.get<{ assigned: number; completed: number; overdue: number; due_soon: number; paths: PathProgress[]; certificates: { assignment_id: string; course_title?: string | null; certificate_no: string; completed_at?: string | null }[] }>("/engage/lms/my")),
  runReminders: () => d(api.post<{ sent: number }>("/engage/lms/reminders/run")),
};
