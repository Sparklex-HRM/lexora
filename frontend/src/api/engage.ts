import api from "./client";

// Engagement (Stream G4): helpdesk, surveys, kudos, LMS, user prefs. Server: ops service, /engage/*.

export interface HdCategory { key: string; label: string; sla_hours: number; assign_role: string; }
export interface EngSettings { helpdesk_categories: HdCategory[]; anonymity_threshold: number; kudos_values: string[]; kudos_needs_approval: boolean; can_edit?: boolean; }
export interface Ticket {
  id: string; number: string; employee_id?: string | null; employee_name?: string | null; department?: string | null; category: string; subject: string;
  body?: string | null; priority: string; status: string; assignee_user_id?: string | null; assignee_name?: string | null; sla_hours: number;
  due_at?: string | null; sla_state: string; age_hours: number; first_response_at?: string | null; resolved_at?: string | null; closed_at?: string | null;
  confidential: boolean; source: string; doc_ids: string[]; created_at?: string | null; updated_at?: string | null; comments?: number | null;
}
export interface TicketComment { id: string; author_name?: string | null; author_role?: string | null; body: string; internal: boolean; kind: string; doc_ids: string[]; at?: string | null; }
export interface SurveyQ { key: string; type: "scale" | "nps" | "text" | "choice"; text: string; options?: string[]; }
export interface Survey {
  id: string; title: string; kind: string; questions: SurveyQ[]; anonymous: boolean; status: string; opens_on?: string | null; closes_on?: string | null;
  target: Record<string, any>; threshold: number; exit_case_id?: string | null; created_by?: string | null; created_at?: string | null; closed_at?: string | null;
  responses?: number | null; is_open: boolean; answered?: boolean | null; my_answers?: Record<string, any> | null;
}
export interface Kudos { id: string; from_employee_id?: string | null; from_name?: string | null; to_employee_id: string; to_name?: string | null; value_tag?: string | null; message: string; status: string; decided_by?: string | null; created_at?: string | null; }
export interface CourseModule { key: string; title: string; kind: "pdf" | "video" | "url" | "scorm"; url?: string | null; doc_id?: string | null; pkg_id?: string | null; minutes?: number; }
export interface QuizQ { q: string; options: string[]; answer?: number; }
export interface Course { id: string; title: string; description?: string | null; modules: CourseModule[]; quiz?: QuizQ[]; quiz_questions: number; pass_mark: number; mandatory: boolean; active: boolean; created_by?: string | null; created_at?: string | null; assigned?: number | null; completed?: number | null;
  // D7
  max_attempts?: number; question_tags?: string[]; quiz_sample?: number; category?: string | null; }
export interface Assignment {
  id: string; course_id: string; course_title?: string | null; mandatory: boolean; employee_id: string; employee_name?: string | null; due_on?: string | null;
  assigned_by?: string | null; assigned_at?: string | null; completed_modules: string[]; quiz_score?: number | null; quiz_passed?: boolean | null; quiz_attempts: number;
  status: string; state: string; completed_at?: string | null; certificate_no?: string | null; progress_pct: number; modules: CourseModule[]; quiz: { q: string; options: string[] }[]; pass_mark?: number | null; message?: string;
  // D7
  from_bank?: boolean; max_attempts?: number; category?: string | null; path_id?: string | null; attempts_left?: number | null;
}

const d = <T,>(p: Promise<{ data: T }>) => p.then((r) => r.data);
const base = () => import.meta.env.VITE_API_BASE || "/api";

export const engageApi = {
  settings: () => d(api.get<EngSettings>("/engage/settings")),
  saveSettings: (body: Partial<EngSettings>) => d(api.put<EngSettings>("/engage/settings", body)),
  // helpdesk
  tickets: (params: { scope?: string; status?: string; category?: string; q?: string }) => d(api.get<{ tickets: Ticket[]; counts: Record<string, number> }>("/engage/helpdesk/tickets", { params })),
  createTicket: (body: { category: string; subject: string; body?: string; priority?: string; source?: string; employee_id?: string }) => d(api.post<Ticket>("/engage/helpdesk/tickets", body)),
  ticket: (id: string) => d(api.get<{ ticket: Ticket; comments: TicketComment[]; can_work: boolean; states: string[] }>(`/engage/helpdesk/tickets/${id}`)),
  comment: (id: string, body: string, internal = false) => d(api.post<TicketComment>(`/engage/helpdesk/tickets/${id}/comments`, { body, internal })),
  attach: (id: string, file: File) => { const f = new FormData(); f.append("file", file); return d(api.post<{ doc_id: string; file_name: string }>(`/engage/helpdesk/tickets/${id}/attachments`, f)); },
  fileUrl: (docId: string) => `${base()}/engage/helpdesk/files/${docId}`,
  fileBlob: (docId: string) => api.get(`/engage/helpdesk/files/${docId}`, { responseType: "blob" }).then((r) => URL.createObjectURL(r.data)),
  updateTicket: (id: string, body: { status?: string; assignee_user_id?: string | null; priority?: string; category?: string; note?: string }) => d(api.patch<Ticket>(`/engage/helpdesk/tickets/${id}`, body)),
  agents: () => d(api.get<{ user_id: string; name: string; role: string }[]>("/engage/helpdesk/agents")),
  report: (days = 90) => d(api.get<any>("/engage/helpdesk/report", { params: { days } })),
  // surveys
  surveys: () => d(api.get<{ surveys: Survey[]; can_manage: boolean }>("/engage/surveys")),
  surveyTemplates: () => d(api.get<any[]>("/engage/surveys/templates")),
  createSurvey: (body: Record<string, any>) => d(api.post<Survey>("/engage/surveys", body)),
  updateSurvey: (id: string, body: Record<string, any>) => d(api.patch<Survey>(`/engage/surveys/${id}`, body)),
  survey: (id: string) => d(api.get<Survey>(`/engage/surveys/${id}`)),
  respond: (id: string, answers: Record<string, any>) => d(api.post<{ ok: boolean }>(`/engage/surveys/${id}/respond`, { answers })),
  results: (id: string) => d(api.get<{ survey: Survey; results: any; named: any[] | null }>(`/engage/surveys/${id}/results`)),
  trend: (kind = "enps") => d(api.get<{ kind: string; points: { survey_id: string; title: string; date: string; responses: number; visible: boolean; score: number | null }[] }>("/engage/surveys-trend", { params: { kind } })),
  // kudos
  kudos: (status?: string) => d(api.get<{ kudos: Kudos[]; values: string[]; needs_approval: boolean; can_moderate: boolean }>("/engage/kudos", { params: status ? { status } : {} })),
  giveKudos: (body: { to_employee_id: string; value_tag?: string; message: string }) => d(api.post<Kudos>("/engage/kudos", body)),
  /** Every active colleague (names + department only) — the kudos picker; GET /employees is scoped to the caller. */
  people: () => d(api.get<{ id: string; first_name: string; last_name: string; department?: string | null; employee_number?: string | null }[]>("/engage/people")),
  moderateKudos: (id: string, decision: "approve" | "reject") => d(api.post<Kudos>(`/engage/kudos/${id}/${decision}`)),
  kudosSummary: (month?: string) => d(api.get<any>("/engage/kudos/summary", { params: month ? { month } : {} })),
  // prefs
  prefs: () => d(api.get<{ language: string; voice: boolean; languages: string[] }>("/engage/me/prefs")),
  savePrefs: (body: { language?: string; voice?: boolean }) => d(api.put<{ language: string; voice: boolean }>("/engage/me/prefs", body)),
  // LMS
  courses: () => d(api.get<{ courses: Course[]; can_manage: boolean }>("/engage/lms/courses")),
  createCourse: (body: Record<string, any>) => d(api.post<Course>("/engage/lms/courses", body)),
  updateCourse: (id: string, body: Record<string, any>) => d(api.patch<Course>(`/engage/lms/courses/${id}`, body)),
  moduleFile: (id: string, file: File, title?: string) => { const f = new FormData(); f.append("file", file); return d(api.post<Course>(`/engage/lms/courses/${id}/module-file`, f, { params: title ? { title } : {} })); },
  lmsFileUrl: (docId: string) => `${base()}/engage/lms/files/${docId}`,
  lmsFileBlob: (docId: string) => api.get(`/engage/lms/files/${docId}`, { responseType: "blob" }).then((r) => URL.createObjectURL(r.data)),
  assign: (body: { course_id: string; employee_ids?: string[]; departments?: string[]; due_on?: string }) => d(api.post<{ assigned: number; already: number }>("/engage/lms/assign", body)),
  assignments: (scope = "mine", course_id?: string) => d(api.get<{ assignments: Assignment[] }>("/engage/lms/assignments", { params: { scope, ...(course_id ? { course_id } : {}) } })),
  progress: (id: string, module_key: string) => d(api.post<Assignment>(`/engage/lms/assignments/${id}/progress`, { module_key })),
  quiz: (id: string, answers: number[]) => d(api.post<Assignment & { correct: number; total: number }>(`/engage/lms/assignments/${id}/quiz`, { answers })),
  certificateBlob: (id: string) => api.get(`/engage/lms/assignments/${id}/certificate.pdf`, { responseType: "blob" }).then((r) => URL.createObjectURL(r.data)),
  certificatePdf: (id: string) => api.get(`/engage/lms/assignments/${id}/certificate.pdf`, { responseType: "blob" }).then((r) => r.data as Blob),   // Print
  compliance: () => d(api.get<any>("/engage/lms/compliance")),
};
