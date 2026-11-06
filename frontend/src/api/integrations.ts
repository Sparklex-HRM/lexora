import api from "./client";

// ── Stream G3: platform & integrations (auth + notification services) ───────

export interface ProviderField { key: string; label: string; secret: boolean; required: boolean; help: string; kind: string; }
export interface ProviderInfo {
  key: string; label: string; group: string; stub: boolean; note: string;
  modes: { key: string; label: string }[]; fields: Record<string, ProviderField[]>;
  mode: string; enabled: boolean; config: Record<string, any>;
  status: "configured" | "not_configured" | "error"; status_message: string;
  last_test_at: string | null; last_test_ok: boolean | null; last_error: string | null;
  updated_at: string | null; updated_by: string | null;
  docs_url?: string; environment?: "sandbox" | "live" | null; env_vars?: string[];   // S5
}
export interface Overview {
  providers: ProviderInfo[];
  sso: { status: string; protocol: string | null; required: boolean; display_name: string };
  scim: { status: string; active_tokens: number };
  api_keys: { active: number }; webhooks: { active: number };
  cra: { status: string; message: string };
}
export interface AuditRow { id: string; at: string | null; actor: string | null; role: string | null; action: string;
  entity_type: string | null; entity_id: string | null; summary: string | null; meta: any; }

export const integrationsApi = {
  overview: () => api.get<Overview>("/auth/integrations").then((r) => r.data),
  audit: () => api.get<{ items: AuditRow[] }>("/auth/integrations/audit").then((r) => r.data),
  provider: (key: string) => api.get<ProviderInfo>(`/auth/integrations/${key}`).then((r) => r.data),
  save: (key: string, body: { mode?: string; enabled?: boolean; config?: Record<string, any> }) =>
    api.put<ProviderInfo>(`/auth/integrations/${key}`, body).then((r) => r.data),
  test: (key: string) => api.post<{ ok: boolean; message: string }>(`/auth/integrations/${key}/test`).then((r) => r.data),
};

// ── SSO ─────────────────────────────────────────────────────────────────────
export interface SsoConfig {
  protocol: "oidc" | "saml"; enabled: boolean; required: boolean; display_name: string;
  discovery_url: string; client_id: string; client_secret_set: boolean;
  idp_entity_id: string; idp_sso_url: string; idp_cert_set: boolean;
  attr_email: string; attr_employee_number: string; jit_enabled: boolean; jit_default_role: string;
  updated_at: string | null; updated_by: string | null; missing: string[]; note?: string;
  oidc_redirect_uri: string; saml_acs_url: string; saml_sp_entity_id: string; saml_metadata_url: string;
  protocols: Record<string, string>; roles: { key: string; label: string }[];
}
export const ssoApi = {
  get: () => api.get<SsoConfig>("/auth/sso/config").then((r) => r.data),
  save: (body: Partial<SsoConfig> & { client_secret?: string; idp_cert_pem?: string }) =>
    api.put<SsoConfig>("/auth/sso/config", body).then((r) => r.data),
  test: () => api.post<{ ok: boolean; message: string }>("/auth/sso/test").then((r) => r.data),
  publicInfo: (orgCode: string) =>
    api.get<{ sso: boolean; required: boolean; display_name: string; org_name: string | null }>(`/auth/sso/public/${orgCode}`).then((r) => r.data),
  exchange: (code: string) =>
    api.post<{ access_token: string; refresh_token: string; org_id: string; role: string }>("/auth/sso/exchange", { code }).then((r) => r.data),
  startUrl: (orgCode: string) => `${import.meta.env.VITE_API_BASE || "/api"}/auth/sso/start/${encodeURIComponent(orgCode)}`,
};

// ── SCIM ────────────────────────────────────────────────────────────────────
export interface ScimToken { id: string; label: string; created_at: string | null; created_by: string | null;
  last_used_at: string | null; revoked_at: string | null; status: "active" | "revoked"; }
export interface ScimLogRow { id: string; at: string | null; method: string; path: string; user_name: string | null;
  external_id: string | null; outcome: string; status_code: number; message: string | null; }
export const scimApi = {
  tokens: () => api.get<{ items: ScimToken[]; base_url: string }>("/auth/scim/admin/tokens").then((r) => r.data),
  create: (label: string) => api.post<{ id: string; token: string; base_url: string; note: string }>("/auth/scim/admin/tokens", { label }).then((r) => r.data),
  revoke: (id: string) => api.post(`/auth/scim/admin/tokens/${id}/revoke`).then((r) => r.data),
  log: () => api.get<{ items: ScimLogRow[] }>("/auth/scim/admin/log").then((r) => r.data),
};

// ── API keys ────────────────────────────────────────────────────────────────
export interface ApiKeyRow { id: string; name: string; prefix: string; scopes: string[]; role: string; role_label: string;
  expires_at: string | null; last_used_at: string | null; created_at: string | null; created_by: string | null;
  revoked_at: string | null; status: "active" | "revoked" | "expired"; }
export const apiKeysApi = {
  list: () => api.get<{ items: ApiKeyRow[] }>("/auth/api-keys").then((r) => r.data),
  scopes: () => api.get<{ services: string[]; levels: string[]; roles: { key: string; label: string }[]; how: string }>("/auth/api-keys/scopes").then((r) => r.data),
  create: (body: { name: string; scopes: string[]; role: string; expires_days: number | null }) =>
    api.post<ApiKeyRow & { key: string; note: string }>("/auth/api-keys", body).then((r) => r.data),
  revoke: (id: string) => api.post<ApiKeyRow>(`/auth/api-keys/${id}/revoke`).then((r) => r.data),
};

// ── Webhooks ────────────────────────────────────────────────────────────────
export interface WebhookRow { id: string; url: string; description: string; events: string[]; enabled: boolean;
  created_at: string | null; created_by: string | null; last_status: string | null; last_delivery_at: string | null; }
export interface DeliveryRow { id: string; webhook_id: string; url?: string; event: string; event_id: string; attempt: number;
  status: string; http_status: number | null; response_excerpt: string | null; next_attempt_at: string | null;
  created_at: string | null; delivered_at: string | null; replay_of: string | null; payload: any; }
export const webhooksApi = {
  events: () => api.get<{ events: { key: string; label: string }[]; signature: string; retries: string[] }>("/auth/webhooks/events").then((r) => r.data),
  list: () => api.get<{ items: WebhookRow[] }>("/auth/webhooks").then((r) => r.data),
  create: (body: { url: string; description: string; events: string[]; enabled: boolean }) =>
    api.post<WebhookRow & { secret: string; note: string }>("/auth/webhooks", body).then((r) => r.data),
  update: (id: string, body: { url: string; description: string; events: string[]; enabled: boolean }) =>
    api.put<WebhookRow>(`/auth/webhooks/${id}`, body).then((r) => r.data),
  remove: (id: string) => api.delete(`/auth/webhooks/${id}`).then((r) => r.data),
  rotate: (id: string) => api.post<{ secret: string; note: string }>(`/auth/webhooks/${id}/rotate-secret`).then((r) => r.data),
  test: (id: string) => api.post<DeliveryRow>(`/auth/webhooks/${id}/test`).then((r) => r.data),
  deliveries: (params: { webhook_id?: string; status?: string } = {}) =>
    api.get<{ items: DeliveryRow[] }>("/auth/webhooks/deliveries", { params }).then((r) => r.data),
  replay: (id: string) => api.post<{ id: string; status: string }>(`/auth/webhooks/deliveries/${id}/replay`).then((r) => r.data),
};

// ── E-sign ──────────────────────────────────────────────────────────────────
export interface Envelope { id: string; title: string; kind: string; provider: string; employee_id: string | null;
  signer_name: string; signer_email: string | null; signer_phone: string | null; status: string;
  pdf_sha256: string; signed_sha256: string | null; otp_channel: string | null; otp_status: string | null;
  typed_name: string | null; signer_ip: string | null; signed_at: string | null; declined_reason: string | null;
  created_by: string | null; created_at: string | null; link_expires_at: string | null;
  events?: { at: string; what: string; ip?: string }[]; legal_note?: string; signer_user_agent?: string | null; }
export const esignApi = {
  list: () => api.get<{ items: Envelope[]; legal_note: string }>("/auth/esign/envelopes").then((r) => r.data),
  create: (fd: FormData) => api.post<Envelope & { email_sent: boolean; link?: string }>("/auth/esign/envelopes", fd).then((r) => r.data),
  get: (id: string) => api.get<Envelope>(`/auth/esign/envelopes/${id}`).then((r) => r.data),
  pdf: (id: string, signed: boolean) => api.get(`/auth/esign/envelopes/${id}/pdf`, { params: { signed }, responseType: "blob" }).then((r) => r.data as Blob),
  resend: (id: string) => api.post<{ ok: boolean; email_sent: boolean; link?: string }>(`/auth/esign/envelopes/${id}/resend`).then((r) => r.data),
  cancel: (id: string, reason: string) => api.post<Envelope>(`/auth/esign/envelopes/${id}/cancel`, { reason }).then((r) => r.data),
  // signer (public link)
  pub: (token: string) => api.get<{ title: string; kind: string; signer_name: string; status: string; contact: string;
    otp_channel: string; otp_status: string | null; pdf_sha256: string; signed_at: string | null; legal_note: string }>(`/auth/esign/public/${token}`).then((r) => r.data),
  pubPdfUrl: (token: string) => `${import.meta.env.VITE_API_BASE || "/api"}/auth/esign/public/${token}/pdf`,
  sendCode: (token: string) => api.post<{ otp_status: string; message: string; dev_code?: string }>(`/auth/esign/public/${token}/send-code`).then((r) => r.data),
  sign: (token: string, body: { code: string; typed_name: string; agree: boolean }) =>
    api.post<{ ok: boolean; status: string; signed_sha256: string; message: string }>(`/auth/esign/public/${token}/sign`, body).then((r) => r.data),
  decline: (token: string, reason: string) => api.post(`/auth/esign/public/${token}/decline`, { reason }).then((r) => r.data),
};

// ── KYC / BGV ───────────────────────────────────────────────────────────────
export interface KycRow { id: string; employee_id: string | null; employee_name: string | null; kind: string; provider: string;
  input_masked: string | null; status: string; message: string | null; requested_by: string | null; at: string | null; }
export interface BgvRow { id: string; employee_id: string | null; employee_name: string; check_type: string; check_type_label: string;
  mode: string; vendor: string; reference: string; status: string; status_label: string; result_note: string;
  document_id: string | null; requested_at: string | null; completed_at: string | null; recorded_by: string | null; updated_at: string | null; }
export interface BgvMeta { check_types: { key: string; label: string }[]; statuses: { key: string; label: string }[];
  employees: { id: string; name: string; employee_number: string | null }[]; provider: ProviderInfo; }
export const kycApi = {
  list: () => api.get<{ items: KycRow[]; kinds: { key: string; label: string }[] }>("/auth/kyc/checks").then((r) => r.data),
  run: (body: { employee_id: string; kind: string; value: string }) => api.post<KycRow>("/auth/kyc/checks", body).then((r) => r.data),
};
export const bgvApi = {
  meta: () => api.get<BgvMeta>("/auth/bgv/meta").then((r) => r.data),
  list: () => api.get<{ items: BgvRow[] }>("/auth/bgv/checks").then((r) => r.data),
  create: (body: { employee_id: string; check_type: string; vendor: string; reference: string; result_note: string; status: string }) =>
    api.post<BgvRow>("/auth/bgv/checks", body).then((r) => r.data),
  update: (id: string, body: Partial<{ status: string; vendor: string; reference: string; result_note: string; document_id: string }>) =>
    api.patch<BgvRow>(`/auth/bgv/checks/${id}`, body).then((r) => r.data),
};

// ── Chat-ops + WhatsApp (notification service) ──────────────────────────────
export interface WaMessage { id: string; to: string; template: string; kind: string; provider: string; status: string;
  error: string | null; message_id: string | null; created_at: string | null; updated_at: string | null; }
export const chatApi = {
  commands: () => api.get<{ commands: { command: string; what: string }[]; link: string }>("/notifications/chatops/commands").then((r) => r.data),
  linkCode: () => api.post<{ code: string; expires_in_seconds: number; how: string }>("/notifications/chatops/link-code").then((r) => r.data),
  waMessages: () => api.get<{ items: WaMessage[] }>("/notifications/whatsapp/messages").then((r) => r.data),
  waTest: (to: string) => api.post<{ id: string; status: string; error: string | null }>("/notifications/whatsapp/test", { to }).then((r) => r.data),
};

export const errText = (e: any, fallback: string): string => {
  const d = e?.response?.data?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d) && d[0]?.msg) return String(d[0].msg);
  return fallback;
};
export const when = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleString(undefined, {
  day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "—");
