import api from "./client";

/** Employee portal batch 3 — "Raise a request" (HR-defined request types → D1 approval chain)
 *  and "My tasks" (own to-dos + exit clearance + onboarding, merged read-only). */

export type FieldType = "text" | "number" | "date" | "select" | "multiselect" | "boolean" | "file" | "lookup";
export const FIELD_TYPES: { value: FieldType; label: string }[] = [
  { value: "text", label: "Text" }, { value: "number", label: "Number" }, { value: "date", label: "Date" },
  { value: "select", label: "Choice (one)" }, { value: "multiselect", label: "Choices (many)" },
  { value: "boolean", label: "Yes / no" }, { value: "file", label: "File" },
];

/** Same shape as a D1 custom-field definition. */
export interface RequestField {
  key: string; label: string; type: FieldType; required: boolean;
  options?: string[] | null; validation?: { regex?: string; min?: number | string; max?: number | string; help?: string } | null;
  sort_order?: number; active?: boolean;
}
export interface RequestType {
  id: string; key: string; name: string; description?: string | null; category?: string | null;
  handler_note?: string | null; fields: RequestField[]; active: boolean; sort_order: number;
  updated_by?: string | null; updated_at?: string | null;
}
export interface RequestTypeIn {
  name: string; key?: string; description?: string; category?: string; handler_note?: string;
  active?: boolean; sort_order?: number; fields: Partial<RequestField>[];
}
export type RequestStatus = "pending" | "approved" | "rejected" | "withdrawn";
export interface EmployeeRequest {
  id: string; ref: string; type_id: string; type_key: string; type_name: string; category?: string | null;
  employee_id: string; employee_name?: string | null; employee_number?: string | null; department?: string | null;
  manager_id?: string | null; values: Record<string, any>; fields: RequestField[];
  rows: { key: string; label: string; type: FieldType; value: any; display: string }[];
  summary: string; status: RequestStatus; decided_by_name?: string | null; decided_at?: string | null;
  decision_note?: string | null; created_at?: string | null; can_decide?: boolean; can_withdraw?: boolean;
}
export interface Todo { id: string; title: string; due_date?: string | null; note?: string | null; done: boolean; done_at?: string | null; created_at?: string | null }
export interface TaskRow {
  id: string; ref_id: string; source: "todo" | "exit_clearance" | "exit_owner" | "onboarding"; source_label: string;
  title: string; detail?: string | null; due_date?: string | null; done: boolean; status: string;
  editable: boolean; link?: string | null; yours?: boolean;
}
export interface TasksOut {
  items: TaskRow[];
  counts: { total: number; open: number; done: number; overdue: number; by_source: Record<string, number> };
  sources: Record<string, string>;
}

const B = "/employees/ess";
const d = <T,>(p: Promise<{ data: T }>) => p.then((r) => r.data);
const q = (params: Record<string, any>) =>
  Object.fromEntries(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ""));

export const essRequestsApi = {
  types: (all = false) => d(api.get<RequestType[]>(`${B}/request-types`, { params: all ? { all: 1 } : {} })),
  createType: (body: RequestTypeIn) => d(api.post<RequestType>(`${B}/request-types`, body)),
  updateType: (id: string, body: Partial<RequestTypeIn>) => d(api.patch<RequestType>(`${B}/request-types/${id}`, body)),

  list: (params: { status?: string; type?: string; employee_id?: string } = {}) =>
    d(api.get<EmployeeRequest[]>(`${B}/requests`, { params: q(params) })),
  get: (id: string) => d(api.get<EmployeeRequest>(`${B}/requests/${id}`)),
  raise: (type_id: string, values: Record<string, any>) => d(api.post<EmployeeRequest>(`${B}/requests`, { type_id, values })),
  decide: (id: string, verdict: "approve" | "reject", comment?: string) =>
    d(api.post<EmployeeRequest>(`${B}/requests/${id}/${verdict}`, { decision: verdict, comment: comment || null })),
  withdraw: (id: string) => d(api.post<EmployeeRequest>(`${B}/requests/${id}/withdraw`, {})),
  file: (id: string, key: string) =>
    api.get(`${B}/requests/${id}/files/${key}`, { responseType: "blob" }).then((r) => r.data as Blob),
  /** A file answer: upload to the employee's own documents first, then send the document id. */
  uploadAttachment: (employeeId: string, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("doc_type", "request_attachment");
    return d(api.post<{ id: string; file_name: string }>(`/employees/${employeeId}/documents`, fd));
  },

  todos: () => d(api.get<Todo[]>(`${B}/todos`)),
  addTodo: (body: { title: string; due_date?: string | null; note?: string | null }) => d(api.post<Todo>(`${B}/todos`, body)),
  updateTodo: (id: string, body: Partial<Pick<Todo, "title" | "due_date" | "note" | "done">>) => d(api.patch<Todo>(`${B}/todos/${id}`, body)),
  deleteTodo: (id: string) => d(api.delete(`${B}/todos/${id}`)),
  tasks: () => d(api.get<TasksOut>(`${B}/tasks`)),
};

export const REQUEST_TONE: Record<string, string> = {
  pending: "badge-amber", approved: "badge-green", rejected: "badge-red", withdrawn: "badge-purple",
};
export const HR_ROLES = ["superadmin", "org_admin", "hr_admin", "hr_manager"];
