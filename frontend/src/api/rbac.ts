/** Org Roles & Permissions (2026-10-24) — backend/services/auth/rbac_routes.py. */
import api from "./client";

export interface CatModule { key: string; label: string; group: string; desc: string; actions: string[]; platform_only: boolean }
export interface SystemRole {
  key: string; label: string; reach: string; reach_label: string; desc: string; system: true;
  permissions: string[]; can_clone: boolean; platform_only: boolean; members?: number;
}
export interface Catalogue {
  modules: CatModule[];
  action_labels: Record<string, string>;
  data_scopes: { key: string; label: string; desc: string }[];
  reaches: { key: string; label: string; narrow: boolean }[];
  narrow_reach_permissions: string[];
  non_delegable: string[];
  custom_bases: string[];
  reach_by_base: Record<string, string[]>;
  system_roles: SystemRole[];
  route_count: number;
}
export interface CustomRole {
  id: string; key: string; name: string; description: string | null; base_role: string; reach: string;
  reach_label?: string; acting_role?: string; permissions: string[]; status: "active" | "retired"; version: number;
  cloned_from: string | null; members?: number; updated_at?: string | null; updated_by?: string | null; created_by?: string | null;
}
export interface RoleMember { user_id: string; email: string; name: string; system_role: string; is_active: boolean; assigned_at: string | null; assigned_by: string | null }
export interface RoleUser {
  user_id: string; email: string; name: string; system_role: string; system_role_label: string; is_active: boolean;
  employee_number: string | null; department: string | null; custom_roles: { id: string; name: string; status: string }[];
}
export interface Preview {
  user_id?: string; email?: string; name?: string; system_role: string; system_role_label?: string;
  role: string; roles: string[]; ui_roles: string[]; perms: string[]; reach: string; reach_label: string;
  sources: Record<string, string[]>; custom_roles?: CustomRole[];
}
export interface Change {
  id: string; at: string | null; actor_email: string | null; actor_role: string | null; action: string;
  role_id: string | null; role_key: string | null; user_id: string | null; summary: string | null; before: any; after: any;
}
export interface RoleInput { name: string; description?: string | null; base_role: string; reach: string; permissions: string[]; cloned_from?: string | null }

const B = "/auth/rbac";
export const rbacApi = {
  catalogue: () => api.get<Catalogue>(`${B}/catalogue`).then((r) => r.data),
  roles: () => api.get<{ system: SystemRole[]; custom: CustomRole[] }>(`${B}/roles`).then((r) => r.data),
  role: (id: string) => api.get<CustomRole & { members: RoleMember[] }>(`${B}/roles/${id}`).then((r) => r.data),
  create: (b: RoleInput) => api.post<CustomRole>(`${B}/roles`, b).then((r) => r.data),
  update: (id: string, b: RoleInput) => api.put<CustomRole>(`${B}/roles/${id}`, b).then((r) => r.data),
  retire: (id: string) => api.post<CustomRole>(`${B}/roles/${id}/retire`).then((r) => r.data),
  restore: (id: string) => api.post<CustomRole>(`${B}/roles/${id}/restore`).then((r) => r.data),
  remove: (id: string) => api.delete(`${B}/roles/${id}`).then((r) => r.data),
  addMembers: (id: string, user_ids: string[]) => api.post(`${B}/roles/${id}/members`, { user_ids }).then((r) => r.data),
  removeMember: (id: string, uid: string) => api.delete(`${B}/roles/${id}/members/${uid}`).then((r) => r.data),
  users: (q?: string) => api.get<{ items: RoleUser[]; total: number }>(`${B}/users`, { params: q ? { q } : {} }).then((r) => r.data),
  preview: (uid: string) => api.get<Preview>(`${B}/users/${uid}`).then((r) => r.data),
  setUserRoles: (uid: string, role_ids: string[]) => api.put<Preview & { changed: number }>(`${B}/users/${uid}/roles`, { role_ids }).then((r) => r.data),
  history: (p: { role_id?: string; user_id?: string } = {}) => api.get<Change[]>(`${B}/history`, { params: p }).then((r) => r.data),
  me: () => api.get<Preview>(`${B}/me`).then((r) => r.data),
};

/** "message (CODE)" from an API refusal — every RBAC refusal carries a code. */
export function rbacError(e: any, fallback = "That did not work"): string {
  const d = e?.response?.data;
  const msg = typeof d?.detail === "string" ? d.detail
    : Array.isArray(d?.detail) ? d.detail.map((x: any) => x?.msg).filter(Boolean).join("; ") : fallback;
  return d?.code ? `${msg} (${d.code})` : d?.ref ? `${msg} (ref ${d.ref})` : msg || fallback;
}
