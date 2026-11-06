import { create } from "zustand";
import { persist } from "zustand/middleware";
import { verifyUiGrant, type UiGrant } from "../lib/uiGrant";
import { UI_GRANT_KEYS } from "../config/uiGrantKeys";

// Lexora HRM user — shaped from the auth service's /auth/me answer. Since 2026-10-24 (HMS Entersoft HIGH "UI role
// tampering"): id, org_id, role, roles, ui_roles, permissions and modules come ONLY from the verified, ES256-signed
// `ui_grant` (lib/uiGrant.ts) — never from the JSON fields of a login / me answer, which a proxy can edit.
export interface User {
  id: string;
  org_id: string;
  email: string;
  role: string;
  /** system role + "custom:<key>" per active custom role (from the grant) */
  roles?: string[];
  /** system roles whose screens the UI may draw: the system role + each custom role's acting role (from the grant) */
  ui_roles?: string[];
  first_name?: string | null;
  last_name?: string | null;
  employee_id?: string | null;
  branch_scope?: string | null;
  branch_id?: string | null;
  org_name?: string | null;
  org_code?: string | null;
  province?: string | null;
  country?: string | null;
  plan?: string | null;
  modules?: string[];
  /** effective permission keys of the catalogue ("leave.view", "data.pay", …) — from the grant */
  permissions?: string[];
  /** permissions of the custom roles only (from the grant) */
  custom_permissions?: string[];
  reach?: string;
  custom_roles?: { key: string; name: string; base_role: string; reach: string; acting_role: string }[];
  /** WASA #7: someone else chose this login's password — change it first. */
  password_change_required?: boolean;
  ui_grant?: string | null;
}

interface AuthState {
  token: string | null;
  refreshToken: string | null;
  /** null until a /auth/me answer's ui_grant has verified for the current token */
  user: User | null;
  /** the verified grant (memory only — never persisted) */
  grant: UiGrant | null;
  /** a /auth/me answer is being verified */
  verifying: boolean;
  setAuth: (token: string, refresh: string | null, user: User) => void;
  /** Verify `me.ui_grant` against the current token; on success the user is set (role etc. from the grant), on
   *  failure the session ends with "Session could not be verified". Resolves to the verdict. */
  setUser: (me: User) => Promise<boolean>;
  setToken: (token: string) => void;
  setVerifying: (v: boolean) => void;
  logout: () => void;
  splash: boolean;          // true briefly after an interactive login (not on reload)
  endSplash: () => void;
}

export const UNVERIFIED_REASON = "unverified";

/** End the session and go to sign-in with the "could not be verified" message. */
export function signOutUnverified() {
  useAuthStore.getState().logout();
  if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
    window.location.replace(`/login?reason=${UNVERIFIED_REASON}`);
  }
}

/** The screen-ready user: profile fields from /auth/me, every access field from the verified grant. */
function fromGrant(me: User, g: UiGrant): User {
  return {
    ...me,
    id: g.sub,
    org_id: g.org_id || "",
    role: g.role,
    roles: g.roles,
    ui_roles: g.ui_roles,
    permissions: g.perms,
    custom_permissions: g.custom_perms,
    reach: g.reach,
    modules: g.modules ?? undefined,
    ui_grant: undefined,
  };
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      token: null,
      refreshToken: null,
      user: null,
      grant: null,
      verifying: false,
      splash: false,
      // The login answer's role is NOT trusted: the user stays empty until /auth/me's grant verifies.
      setAuth: (token, refreshToken) => set({ token, refreshToken, user: null, grant: null, splash: true }),
      setUser: async (me) => {
        const token = get().token;
        if (!token) return false;
        set({ verifying: true });
        const g = await verifyUiGrant(me?.ui_grant, UI_GRANT_KEYS, { accessToken: token });
        if (get().token !== token) { set({ verifying: false }); return false; }   // signed out / swapped meanwhile
        if (!g) {
          set({ verifying: false });
          signOutUnverified();
          return false;
        }
        set({ user: fromGrant(me, g), grant: g, verifying: false });
        return true;
      },
      // A new access token invalidates the grant bound to the old one: the next /auth/me re-verifies.
      setToken: (token) => set({ token, user: null, grant: null }),
      setVerifying: (v) => set({ verifying: v }),
      endSplash: () => set({ splash: false }),
      logout: () => set({ token: null, refreshToken: null, user: null, grant: null, verifying: false, splash: false }),
    }),
    // Persisted: the tokens only — no name / e-mail / role / permissions in localStorage (PII rule; the role is
    // re-derived from a fresh, verified grant on every page load).
    {
      name: "lx-auth",
      version: 2,
      partialize: (s) => ({ token: s.token, refreshToken: s.refreshToken }),
      // Read back ONLY the tokens — a `user` left in storage by an older build (or typed in by hand) is ignored.
      merge: (persisted, current) => {
        const p = (persisted || {}) as Partial<AuthState>;
        return { ...current, token: p.token ?? null, refreshToken: p.refreshToken ?? null };
      },
      migrate: (persisted) => {
        const p = (persisted || {}) as Partial<AuthState>;
        return { token: p.token ?? null, refreshToken: p.refreshToken ?? null } as AuthState;
      },
    }
  )
);

// ── Role groups (mirror backend services/auth/constants.py ROLES) ──────────
export const ADMIN_ROLES   = ["superadmin", "org_admin"];
export const HR_ROLES      = ["hr_admin", "hr_manager"];
export const PAYROLL_ROLES = ["payroll_admin"];
export const MANAGER_ROLES = ["manager"];

export const HR_WRITE      = [...ADMIN_ROLES, ...HR_ROLES];               // manage people
export const PAYROLL_ACCESS = [...ADMIN_ROLES, "hr_admin", "payroll_admin"]; // run payroll
export const APPROVER_ROLES = [...ADMIN_ROLES, ...HR_ROLES, ...MANAGER_ROLES, ...PAYROLL_ROLES];
/** Who approves leave / timesheets / attendance fixes and sees team punches — matches the time and
 *  leave services (payroll admins are NOT approvers there; calling those APIs as payroll gives 403). */
export const TEAM_APPROVER_ROLES = [...ADMIN_ROLES, ...HR_ROLES, ...MANAGER_ROLES];
export const ALL_STAFF     = [...ADMIN_ROLES, ...HR_ROLES, ...PAYROLL_ROLES, ...MANAGER_ROLES, "accountant", "employee"];
/** 2026-10-24 Roles & Permissions: who may open Settings → Roles & Permissions (writes: org_admin + hr_admin). */
export const ROLE_ADMIN_ROLES = ["org_admin", "hr_admin"];

export const ROLE_LABEL: Record<string, string> = {
  superadmin: "Platform Admin",
  org_admin: "Org Admin",
  hr_admin: "HR Admin",
  hr_manager: "HR Manager",
  payroll_admin: "Payroll Admin",
  accountant: "Accountant",
  manager: "Manager",
  employee: "Employee",
};

export function useUserRole(): string | undefined {
  return useAuthStore((s) => s.user?.role);
}
export function useIsAdmin(): boolean {
  const role = useUserRole();
  return !!role && ADMIN_ROLES.includes(role);
}
export function hasRole(role: string | undefined, allowed: string[]): boolean {
  return !!role && allowed.includes(role);
}
/** The system roles whose screens this login may open (its own + the acting roles of its custom roles). */
export function uiRolesOf(user: User | null | undefined): string[] {
  if (!user?.role) return [];
  return user.ui_roles && user.ui_roles.length ? user.ui_roles : [user.role];
}
export function hasAnyRole(user: User | null | undefined, allowed: string[]): boolean {
  return uiRolesOf(user).some((r) => allowed.includes(r));
}
/** A catalogue permission from the verified grant (screens only — the server enforces). */
export function hasPerm(user: User | null | undefined, perm: string): boolean {
  return !!user?.permissions?.includes(perm) || user?.role === "superadmin";
}
export function useHasPerm(perm: string): boolean {
  return useAuthStore((s) => hasPerm(s.user, perm));
}
