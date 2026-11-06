import api from "./client";

// ── Logins (auth service: /auth/users*) ─────────────────────────────────────
export interface LoginUser {
  id: string; email: string; name: string; first_name?: string | null; last_name?: string | null;
  role: string; role_label: string; status: "active" | "invited" | "disabled";
  employee_id: string | null; employee_name?: string | null; employee_number?: string | null;
  employee_status?: string | null; department?: string | null;
  last_login: string | null; created_at: string | null; invited_at: string | null;
  disabled_at: string | null; disabled_reason: string | null; password_set: boolean;
}
export interface RoleOption { key: string; label: string; }
export interface UserList { total: number; items: LoginUser[]; roles: RoleOption[]; }
/** `link` is present only when the e-mail could not be sent (no mail server) —
 *  HR passes it to the person by hand. */
export interface LinkResult { ok?: boolean; email: string; email_sent: boolean; link?: string; expires_in_hours?: number; }
export interface CreateLoginResult {
  user_id: string; created: boolean; linked: boolean; status: string; email: string; role: string;
  invited: boolean; email_sent: boolean; link?: string; user: LoginUser;
}
export interface UnlinkedEmployee {
  id: string; employee_name: string; employee_number: string | null; email: string | null;
  job_title?: string | null; department?: string | null;
}
export interface IdentitySettings {
  auto_login_on_onboarding: boolean; invite_expiry_hours: number; reset_expiry_minutes: number;
  password_policy: string;
}
export interface LinkInfo {
  email: string; first_name: string | null; org_name: string | null; org_code: string | null;
  purpose: "invite" | "reset"; expires_at: string; password_policy: string;
}

export const usersApi = {
  list: (params: { employee_id?: string } = {}) => api.get<UserList>("/auth/users", { params }).then((r) => r.data),
  unlinked: () => api.get<UnlinkedEmployee[]>("/auth/users/unlinked-employees").then((r) => r.data),
  create: (body: { employee_id?: string | null; email?: string; role: string; send_invite: boolean;
    first_name?: string; last_name?: string }) =>
    api.post<CreateLoginResult>("/auth/users", body).then((r) => r.data),
  resend: (id: string) => api.post<LinkResult>(`/auth/users/${id}/resend`).then((r) => r.data),
  reset: (id: string) => api.post<LinkResult>(`/auth/users/${id}/reset-password`).then((r) => r.data),
  disable: (id: string, reason: string) => api.post<LoginUser>(`/auth/users/${id}/disable`, { reason }).then((r) => r.data),
  enable: (id: string) => api.post<LoginUser>(`/auth/users/${id}/enable`).then((r) => r.data),
  setRole: (id: string, role: string) => api.post<LoginUser>(`/auth/users/${id}/role`, { role }).then((r) => r.data),
  settings: () => api.get<IdentitySettings>("/auth/settings/identity").then((r) => r.data),
  saveSettings: (body: Partial<IdentitySettings>) =>
    api.put<IdentitySettings>("/auth/settings/identity", body).then((r) => r.data),
};

// Public pages — the link token is the credential.
export const linkApi = {
  info: (kind: "invite" | "reset", token: string) =>
    api.get<LinkInfo>(kind === "invite" ? `/auth/invite/${token}` : `/auth/password/reset/${token}`).then((r) => r.data),
  setPassword: (kind: "invite" | "reset", token: string, password: string) =>
    api.post<{ ok: boolean; email: string; org_code: string | null }>(
      kind === "invite" ? `/auth/invite/${token}` : `/auth/password/reset/${token}`, { password }).then((r) => r.data),
  forgot: (email: string, org_code: string, captcha_token?: string) =>
    api.post<{ ok: boolean; message: string; dev_link?: string }>("/auth/password/forgot", { email, org_code, captcha_token }).then((r) => r.data),
};

// ── Employee form options + documents (employee service) ────────────────────
export interface ManagerOption { id: string; name: string; employee_number: string | null; job_title?: string | null; department?: string | null; }
export interface Opt { value: string; label: string; }
export interface FormOptions {
  org_country: string | null; org_region: string | null; countries: Opt[];
  regions: Record<string, string[]>; employment_types: string[];
  grades: { name: string; code?: string | null }[]; managers: ManagerOption[];
  doc_types: Record<string, Opt[]>; doc_rules: { allowed: string; max_mb: number };
  next_number: string; prefix: string; padding: number;
}
export interface StoredDoc {
  id: string; doc_type: string; file_name: string; content_type: string; size_bytes: number;
  uploaded_by_name: string | null; uploaded_at: string | null; verified: boolean;
  verified_by?: string | null; verified_at?: string | null;
}

const upload = (url: string, file: File, doc_type: string) => {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("doc_type", doc_type);
  return api.post<StoredDoc>(url, fd).then((r) => r.data);
};
const blob = (url: string) => api.get(url, { responseType: "blob" }).then((r) => r.data as Blob);

export const employeeFormApi = {
  options: () => api.get<FormOptions>("/employees/form-options").then((r) => r.data),
  docs: (employeeId: string) => api.get<StoredDoc[]>(`/employees/${employeeId}/documents`).then((r) => r.data),
  uploadDoc: (employeeId: string, file: File, doc_type: string) => upload(`/employees/${employeeId}/documents`, file, doc_type),
  downloadDoc: (employeeId: string, docId: string) => blob(`/employees/${employeeId}/documents/${docId}/download`),
  verifyDoc: (employeeId: string, docId: string, verified: boolean) =>
    api.post<StoredDoc>(`/employees/${employeeId}/documents/${docId}/verify`, { verified }).then((r) => r.data),
  removeDoc: (employeeId: string, docId: string) => api.delete(`/employees/${employeeId}/documents/${docId}`).then((r) => r.data),
};

/** Open a downloaded file in a new tab (PDF / image) — nothing is kept on disk by the app. */
export function openBlob(b: Blob) {
  const url = URL.createObjectURL(b);
  window.open(url, "_blank", "noopener");
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

// ── Onboarding: HR side ─────────────────────────────────────────────────────
export interface JoinerTask {
  id: string; title: string; owner: string | null; due_date: string | null; done: boolean;
  done_at: string | null; done_by: string | null; note: string | null; overdue: boolean;
}
export interface TemplateItem { id?: string; title: string; owner: string | null; due_offset_days: number; }
export interface ApproveResult {
  status: string; employee?: { id: string; employee_number: string; first_name: string; last_name: string };
  login?: { created?: boolean; linked?: boolean; invited?: boolean; email_sent?: boolean; email?: string; link?: string; reason?: string };
}

export const onboardingHrApi = {
  patch: (id: string, data: Record<string, any>) =>
    api.patch<any>(`/onboarding/sessions/${id}`, { data }).then((r) => r.data),
  resend: (id: string) => api.post<any>(`/onboarding/sessions/${id}/resend`).then((r) => r.data),
  sendBack: (id: string, note: string) => api.post<any>(`/onboarding/sessions/${id}/return`, { note }).then((r) => r.data),
  approve: (id: string) => api.post<ApproveResult>(`/onboarding/sessions/${id}/approve`, {}).then((r) => r.data),
  docs: (id: string) => api.get<StoredDoc[]>(`/onboarding/sessions/${id}/documents`).then((r) => r.data),
  downloadDoc: (id: string, docId: string) => blob(`/onboarding/sessions/${id}/documents/${docId}/download`),
  verifyDoc: (id: string, docId: string, verified: boolean) =>
    api.post<StoredDoc>(`/onboarding/sessions/${id}/documents/${docId}/verify`, { verified }).then((r) => r.data),
  tasks: (id: string) => api.get<JoinerTask[]>(`/onboarding/sessions/${id}/tasks`).then((r) => r.data),
  addTask: (id: string, body: { title: string; owner?: string; due_date?: string }) =>
    api.post<JoinerTask>(`/onboarding/sessions/${id}/tasks`, body).then((r) => r.data),
  updateTask: (taskId: string, body: Partial<JoinerTask>) =>
    api.patch<JoinerTask>(`/onboarding/tasks/${taskId}`, body).then((r) => r.data),
  removeTask: (taskId: string) => api.delete(`/onboarding/tasks/${taskId}`).then((r) => r.data),
  template: () => api.get<TemplateItem[]>("/onboarding/checklist-template").then((r) => r.data),
  saveTemplate: (items: TemplateItem[]) =>
    api.put<TemplateItem[]>("/onboarding/checklist-template", { items }).then((r) => r.data),
};

// ── Onboarding: the joiner's public page ────────────────────────────────────
export interface ConsentType { key: string; label: string; required: boolean; text: string; }
export interface InvitePage {
  status: string; locked: boolean; company: string | null; country: "CA";
  first_name: string | null; last_name: string | null; email: string | null; job_title: string | null;
  hire_date?: string | null; review_note?: string | null; invite_expires_at?: string | null;
  steps: string[]; steps_completed: string[]; progress_pct: number;
  data: Record<string, any>; documents: StoredDoc[]; doc_types: Opt[];
  doc_rules: { allowed: string; max_mb: number };
  consent_types: ConsentType[]; consents_given: string[]; notice_version: string; missing: string[];
}

export const inviteApi = {
  get: (token: string) => api.get<InvitePage>(`/onboarding/invite/${token}`).then((r) => r.data),
  saveStep: (token: string, step: string, data: Record<string, any>) =>
    api.patch<any>(`/onboarding/invite/${token}/step`, { step, data, complete: true }).then((r) => r.data),
  consent: (token: string, consent_type: string, signed_name: string, version: string) =>
    api.post(`/onboarding/invite/${token}/consents`, { consent_type, signed_name, version }).then((r) => r.data),
  upload: (token: string, file: File, doc_type: string) => upload(`/onboarding/invite/${token}/documents`, file, doc_type),
  removeDoc: (token: string, docId: string) => api.delete(`/onboarding/invite/${token}/documents/${docId}`).then((r) => r.data),
  submit: (token: string) => api.post<any>(`/onboarding/invite/${token}/submit`).then((r) => r.data),
};

export const errText = (e: any, fallback: string): string => {
  const d = e?.response?.data?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d) && d[0]?.msg) return String(d[0].msg);
  return fallback;
};
