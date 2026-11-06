// Stream D4 — legal entities (group companies under one organisation).
// Auth service owns the entity master (/auth/entities); the employee service answers which
// entity each person belongs to (/employees/entities, transfers). Canadian registrations:
// CRA Business Number (9 digits), CRA payroll program account (BN + RP + 4 digits), Revenu
// Québec identification number (RL-1), WCB / WSIB / CNESST account per province.
import api from "./client";

export interface Licence { kind: string; number: string; province?: string | null; valid_to?: string | null; label?: string | null; }
export interface BankAccount {
  id: string; label?: string | null; bank_name?: string | null;
  institution: string;             // 3-digit financial institution number
  transit: string;                 // 5-digit branch transit number
  account_last4?: string | null; account_masked?: string | null; account_type?: string | null; is_primary?: boolean;
  account_no?: string | null;      // only on the audited reveal call
}
export interface WcbAccount { board?: string; account_no?: string; rate_group?: string; industry_code?: string; rate?: number | null; }
export interface Signatory { name?: string; designation?: string; email?: string; phone?: string; }
export interface Letterhead { header_text?: string; footer_text?: string; logo_data_url?: string; logo_filename?: string; }

export interface LegalEntity {
  id: string; org_id?: string; name: string; short_code: string; is_default: boolean; is_active: boolean;
  entity_type?: string;
  business_number?: string | null;      // CRA BN, 9 digits
  payroll_account?: string | null;      // 123456789RP0001
  rq_identification?: string | null;    // Revenu Québec identification number
  address_line1?: string | null; address_line2?: string | null; city?: string | null;
  province?: string | null; postal_code?: string | null; country?: string; email?: string | null; phone?: string | null;
  website?: string | null;
  wcb_accounts: Record<string, WcbAccount>;   // keyed by province
  licences: Licence[]; bank_accounts: BankAccount[]; signatory: Signatory; letterhead: Letterhead;
  numbering_prefix?: string | null; registration_gaps?: string[]; has_logo?: boolean; logo_needs_reupload?: boolean; employee_count?: number;
  created_at?: string | null; updated_at?: string | null; updated_by?: string | null;
}
/** What a non-admin sees (name + code only). */
export interface EntityBrief { id: string; name: string; short_code: string; is_default: boolean; is_active: boolean; province?: string | null; country?: string; has_logo?: boolean; employee_count?: number; }

export interface EntityHistoryRow {
  id: string; employee_id: string; entity_id: string; entity_name: string | null; from_date: string | null; to_date: string | null;
  status: "current" | "closed" | "scheduled"; reason?: string | null;
  career_change_id?: string | null; created_by?: string | null; created_at?: string | null;
  employee?: string; employee_number?: string;
}

export const ENTITY_TYPE_LABEL: Record<string, string> = {
  corporation: "Corporation", federal_corporation: "Federal corporation (CBCA)", provincial_corporation: "Provincial corporation",
  partnership: "Partnership", sole_proprietorship: "Sole proprietorship", non_profit: "Not-for-profit", charity: "Registered charity",
  cooperative: "Co-operative", trust: "Trust", branch: "Branch / extra-provincial registration", other: "Other",
};
export const LICENCE_KIND_LABEL: Record<string, string> = {
  business_licence: "Municipal business licence", extra_provincial: "Extra-provincial registration",
  employment_agency: "Employment / temp agency licence", other: "Other",
};
/** Workers' compensation board per province (display default; the entity may type its own). */
export const WCB_BOARD: Record<string, string> = {
  AB: "WCB Alberta", BC: "WorkSafeBC", MB: "WCB Manitoba", NB: "WorkSafeNB", NL: "WorkplaceNL", NS: "WCB Nova Scotia",
  NT: "WSCC", NU: "WSCC", ON: "WSIB", PE: "WCB PEI", QC: "CNESST", SK: "WCB Saskatchewan", YT: "Yukon WCHSB",
};

// ── the entity VIEW (header picker) — "" = all entities. Only an id is stored, never PHI. ──
const KEY = "lx-entity";
export function getViewEntity(): string { try { return localStorage.getItem(KEY) || ""; } catch { return ""; } }
export function setViewEntity(id: string) { try { if (id) localStorage.setItem(KEY, id); else localStorage.removeItem(KEY); } catch { /* ignore */ } }

export type TransferType = "permanent" | "secondment" | "business_transfer";

export const entitiesApi = {
  // auth service — master
  list: (includeInactive = false) =>
    api.get<{ entities: LegalEntity[]; total: number; entity_types: string[] }>("/auth/entities", { params: includeInactive ? { include_inactive: 1 } : {} }).then((r) => r.data),
  get: (id: string) => api.get<LegalEntity>(`/auth/entities/${id}`).then((r) => r.data),
  create: (body: Partial<LegalEntity>) => api.post<LegalEntity>("/auth/entities", body).then((r) => r.data),
  update: (id: string, body: Partial<LegalEntity>) => api.patch<LegalEntity>(`/auth/entities/${id}`, body).then((r) => r.data),
  deactivate: (id: string) => api.delete(`/auth/entities/${id}`).then((r) => r.data),
  makeDefault: (id: string) => api.post<LegalEntity>(`/auth/entities/${id}/default`).then((r) => r.data),
  addBank: (id: string, body: { label?: string; bank_name?: string; institution: string; transit: string; account_no: string; account_type?: string; is_primary?: boolean }) =>
    api.post<{ bank_accounts: BankAccount[] }>(`/auth/entities/${id}/bank-accounts`, body).then((r) => r.data),
  removeBank: (id: string, accountId: string) => api.delete<{ bank_accounts: BankAccount[] }>(`/auth/entities/${id}/bank-accounts/${accountId}`).then((r) => r.data),
  revealBank: (id: string) => api.get<{ bank_accounts: BankAccount[] }>(`/auth/entities/${id}/bank-accounts/reveal`).then((r) => r.data),
  uploadLogo: (id: string, file: File) => { const fd = new FormData(); fd.append("file", file); return api.post<{ has_logo: boolean }>(`/auth/entities/${id}/letterhead`, fd).then((r) => r.data); },
  logo: (id: string) => api.get<Letterhead>(`/auth/entities/${id}/letterhead/logo`).then((r) => r.data),
  removeLogo: (id: string) => api.delete(`/auth/entities/${id}/letterhead`).then((r) => r.data),

  // employee service — who belongs where
  headcount: () => api.get<{ entities: EntityBrief[]; default_id: string | null; unassigned: number; view: string }>("/employees/entities").then((r) => r.data),
  transfers: (status?: string) => api.get<{ transfers: EntityHistoryRow[] }>("/employees/entities/transfers", { params: status ? { status } : {} }).then((r) => r.data),
  applyDue: () => api.post<{ applied: number }>("/employees/entities/apply-due").then((r) => r.data),
  history: (employeeId: string) => api.get<{ employee_id: string; entity_id: string | null; entity_name: string; history: EntityHistoryRow[] }>(`/employees/${employeeId}/entity-history`).then((r) => r.data),
  transfer: (employeeId: string, body: {
    entity_id: string; effective_date: string; reason?: string; note?: string;
    // recorded transfer terms (career-change record) — recorded only, nothing here changes payroll by itself
    transfer_type?: TransferType; service_continuity?: boolean; carry_leave?: boolean; final_pay_under_old_entity?: boolean;
  }) =>
    api.post<{ ok: boolean; applied: boolean; entity_id: string | null; career_change_id?: string; history: EntityHistoryRow }>(`/employees/${employeeId}/entity-transfer`, body).then((r) => r.data),
};

export const entityName = (list: { id: string; name: string }[] | undefined, id?: string | null) =>
  (id && list?.find((e) => e.id === id)?.name) || "";
