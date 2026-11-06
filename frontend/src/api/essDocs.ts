import api from "./client";

/** 2026-10-16 employee portal batch 3 — Document centre.
 *  My documents reuse /employees/{id}/documents; letters reuse /letters/mine; policies are new. */

export interface MyDoc {
  id: string; doc_type: string; file_name: string; content_type: string; size_bytes: number;
  uploaded_by_name: string | null; uploaded_at: string | null; verified: boolean;
  verified_by?: string | null; verified_at?: string | null;
}
export type PolicyStatus = "acknowledged" | "pending" | "not_required" | "archived";
export interface Policy {
  id: string; title: string; category: string | null; version_no: number; version_label: string;
  effective_date: string | null; has_body: boolean; has_file: boolean; file_name: string | null;
  ack_required: boolean; status: "published" | "archived"; published_at: string | null; published_by: string | null;
  archived_at: string | null; last_reminded_at: string | null; my_status: PolicyStatus; my_acknowledged_at: string | null;
  previous_versions: { version_no: number; version_label: string; effective_date: string | null; published_at: string | null }[];
  body?: string | null;
  acks?: { total: number; acknowledged: number; pending: number; percent: number } | null;
}
export interface AckRow {
  employee_id: string; employee_number: string | null; name: string; department: string | null; job_title: string | null;
  status: "acknowledged" | "pending"; acknowledged_at: string | null; version_label: string | null; user_agent: string | null;
}
export interface AckRegister { policy: Policy; total: number; acknowledged: number; pending: number; percent: number; rows: AckRow[]; }
export interface DocSummary {
  my_documents: number; letters: number; policies: number; policies_to_acknowledge: number;
  hr?: { policies_needing_ack: number; staff: number; pending_acknowledgements: number };
}
export interface PolicyIn {
  title: string; category?: string | null; version_label: string; effective_date?: string | null;
  body?: string | null; document_id?: string | null; ack_required: boolean;
}
export interface VersionIn {
  version_label: string; effective_date?: string | null; body?: string | null; document_id?: string | null;
  ack_required?: boolean; keep_file?: boolean;
}

const d = <T>(p: Promise<{ data: T }>) => p.then((r) => r.data);
const blob = (url: string, params?: Record<string, any>) =>
  api.get(url, { responseType: "blob", params }).then((r) => r.data as Blob);
const P = "/employees/ess/policies";

/** The document types HR and employees use in this org (same list as the employee form). */
export const DOC_TYPES: { value: string; label: string }[] = [
  { value: "sin_letter", label: "SIN confirmation letter" }, { value: "photo_id", label: "Government photo ID" },
  { value: "work_permit", label: "Work permit / PR card" }, { value: "td1", label: "TD1 forms (federal + provincial)" },
  { value: "bank_proof", label: "Void cheque / direct-deposit form" }, { value: "photo", label: "Photo" },
  { value: "education", label: "Education certificate" }, { value: "experience", label: "Previous employer letter" },
  { value: "address_proof", label: "Address proof" }, { value: "other", label: "Other" },
];

export const essDocsApi = {
  summary: () => d(api.get<DocSummary>("/employees/ess/documents/summary")),
  // my documents (existing endpoints)
  myDocs: (employeeId: string) => d(api.get<MyDoc[]>(`/employees/${employeeId}/documents`)),
  uploadMyDoc: (employeeId: string, file: File, doc_type: string) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("doc_type", doc_type);
    return d(api.post<MyDoc>(`/employees/${employeeId}/documents`, fd));
  },
  downloadMyDoc: (employeeId: string, docId: string) => blob(`/employees/${employeeId}/documents/${docId}/download`),
  // policies
  policies: () => d(api.get<Policy[]>(P)),
  manage: () => d(api.get<Policy[]>(P, { params: { manage: 1 } })),
  policy: (id: string) => d(api.get<Policy>(`${P}/${id}`)),
  file: (id: string) => blob(`${P}/${id}/file`),
  uploadFile: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return d(api.post<{ id: string; file_name: string; size_bytes: number }>(`${P}/files`, fd));
  },
  publish: (body: PolicyIn) => d(api.post<Policy>(P, body)),
  newVersion: (id: string, body: VersionIn) => d(api.post<Policy>(`${P}/${id}/versions`, body)),
  edit: (id: string, body: { title?: string; category?: string | null }) => d(api.patch<Policy>(`${P}/${id}`, body)),
  archive: (id: string) => d(api.post<Policy>(`${P}/${id}/archive`)),
  restore: (id: string) => d(api.post<Policy>(`${P}/${id}/restore`)),
  acknowledge: (id: string, version_no: number) => d(api.post<Policy>(`${P}/${id}/acknowledge`, { version_no })),
  register: (id: string) => d(api.get<AckRegister>(`${P}/${id}/acks`)),
  registerCsv: (id: string, status?: string) => blob(`${P}/${id}/acks.csv`, status ? { status } : undefined),
  remind: (id: string) => d(api.post<{ reminded: number; last_reminded_at: string | null }>(`${P}/${id}/remind`)),
};
