// Stream S4 — self-serve sign-up, subscription billing, two-step sign-in (MFA), sessions.
import api from "./client";

export interface SignupPlan { key: string; label: string; pepm: number; min_monthly: number; custom: boolean; includes: string | null; max_employees: number | null; modules: string[]; trial_days: number; }
/** Add-ons offered on the sign-up form ("agentic" — HR Desk, Onboarding, Exit and Attendance agents; PEPM on any plan). */
export interface SignupAddon { key: string; label: string; kind: string; price: number; agents: string[]; note: string; }
export interface SignupConfig {
  enabled?: boolean; message?: string;          // decision 2026-10-21: false = self sign-up is off (only enabled + message are sent)
  country: string; currency: string; plans: SignupPlan[]; addons?: SignupAddon[]; provinces: { code: string; name: string }[]; trial_days: number;
  password_policy: string; captcha: { provider: string | null; configured: boolean; site_key: string | null };
  terms_version: string; email_configured: boolean;
}
export interface SignupBody {
  org_name: string; admin_email: string; admin_password: string; admin_first_name: string; admin_last_name?: string;
  phone?: string; state: string; plan: string; addons?: string[]; employees_estimate?: number; accept_terms: boolean;
  captcha_token?: string; website?: string;
}
/** /auth/login and sign-up verify answer with tokens OR the MFA step the login still owes. */
export interface SignInResult {
  access_token?: string; refresh_token?: string; org_id: string; role: string;
  mfa_required?: boolean; mfa_setup_required?: boolean; mfa_token?: string; reason?: string;
  password_change_required?: boolean;   // WASA #7: operator-chosen password → forced change first
  recovery_codes?: string[]; org_code?: string; org_name?: string; trial_ends_at?: string | null;
  canada_defaults?: { ok: boolean; error?: string };
}
export interface MfaSetup { secret: string; secret_grouped: string; otpauth_uri: string; qr_svg: string | null; digits: number; period: number; }
export interface Banner { level: "info" | "warning" | "error"; mode: string; status: string; text: string; days_left: number | null; }

export const signupApi = {
  config: () => api.get<SignupConfig>("/auth/signup/config").then((r) => r.data),
  start: (body: SignupBody) => api.post<{ ok: boolean; email: string; message: string; email_sent: boolean; dev_link?: string }>("/auth/signup", body).then((r) => r.data),
  peek: (token: string) => api.get<{ org_name: string; email: string; state: string; plan: string; expires_at: string; trial_days: number }>(`/auth/signup/verify/${token}`).then((r) => r.data),
  verify: (token: string) => api.post<SignInResult>(`/auth/signup/verify/${token}`).then((r) => r.data),
  checklist: () => api.get<{ items: { key: string; label: string; done: boolean; link: string; action?: string }[]; done: number; total: number; pct: number; trial_ends_at: string | null; status: string }>("/auth/setup-checklist").then((r) => r.data),
  seedDefaults: () => api.post<{ ok: boolean; error?: string }>("/auth/setup-checklist/seed-defaults").then((r) => r.data),
};

export const mfaApi = {
  // second sign-in step (public, credential = mfa_token)
  verify: (mfa_token: string, code: string, captcha_token?: string) => api.post<SignInResult>("/auth/mfa/challenge/verify", { mfa_token, code, captcha_token }).then((r) => r.data),
  enrollStart: (mfa_token: string) => api.post<MfaSetup>("/auth/mfa/challenge/enroll/start", { mfa_token }).then((r) => r.data),
  enrollFinish: (mfa_token: string, code: string, captcha_token?: string) => api.post<SignInResult>("/auth/mfa/challenge/enroll/finish", { mfa_token, code, captcha_token }).then((r) => r.data),
  // signed in
  status: () => api.get<{ enabled: boolean; required: boolean; required_roles: string[]; enforced: boolean; enrolled_at: string | null; last_used_at: string | null; recovery_codes_left: number; qr_available: boolean }>("/auth/mfa").then((r) => r.data),
  setup: () => api.post<MfaSetup>("/auth/mfa/setup").then((r) => r.data),
  enable: (code: string) => api.post<{ enabled: boolean; recovery_codes: string[] }>("/auth/mfa/enable", { code }).then((r) => r.data),
  disable: (code: string, password: string) => api.post<{ enabled: boolean }>("/auth/mfa/disable", { code, password }).then((r) => r.data),
  newCodes: (code: string) => api.post<{ recovery_codes: string[] }>("/auth/mfa/recovery-codes", { code }).then((r) => r.data),
  resetFor: (user_id: string) => api.post<{ ok: boolean; sessions_ended: number }>(`/auth/mfa/reset/${user_id}`).then((r) => r.data),
};

export interface ActiveSession { id: string; method: string; ip: string | null; device: string | null; user_agent: string | null; created_at: string | null; expires_at: string | null; revoked_at: string | null; revoked_reason: string | null; current: boolean; }
export const sessionApi = {
  active: (user_id?: string) => api.get<{ user_id: string; email: string; items: ActiveSession[] }>("/auth/sessions/active", { params: user_id ? { user_id } : {} }).then((r) => r.data),
  revoke: (id: string) => api.post(`/auth/sessions/${id}/revoke`).then((r) => r.data),
  revokeOthers: () => api.post<{ ended: number }>("/auth/sessions/revoke-others").then((r) => r.data),
  logout: () => api.post("/auth/logout").then((r) => r.data),
};

export interface SubscriptionState {
  org_id: string; plan: string; plans: Record<string, any>; status: string; stored_status: string | null; mode: "full" | "read_only";
  banner: Banner | null; trial_ends_at: string | null; access_until: string | null; current_period_end: string | null;
  cancel_at_period_end: boolean; employees: number; employee_limit: number | null; seats: number | null; modules: string[];
  provider: { provider: string; configured: boolean; mode: string; subscription_id: string | null; key_id?: string };
  seller_tax: { configured: boolean; missing?: string[] }; invoice_kind: "tax_invoice" | "proforma";
  buyer: { name: string; gst_hst_number: string | null; province: string | null; address: string | null; email: string | null };
}
export const subscriptionApi = {
  state: () => api.get<SubscriptionState>("/auth/billing/subscription").then((r) => r.data),
  changePlan: (plan: string) => api.post<{ ok: boolean; plan: string; changed: boolean; note?: string }>("/auth/billing/change-plan", { plan }).then((r) => r.data),
  checkout: (plan?: string) => api.post<{ subscription_id: string; short_url: string; seats: number; plan: string; mode: string }>("/auth/billing/checkout", { plan }).then((r) => r.data),
  cancel: (at_period_end = true) => api.post("/auth/billing/cancel", { at_period_end }).then((r) => r.data),
  history: () => api.get<{ items: any[] }>("/auth/billing/history").then((r) => r.data),
  invoicePdf: (id: string) => api.get(`/auth/billing/invoices/${id}/pdf`, { responseType: "blob" }).then((r) => r.data as Blob),
};

/** Human message from an axios error — moved to lib/errText (pre-login code uses it). */
export { errText } from "../lib/errText";
