/**
 * Screen side of org Roles & Permissions (2026-10-24). The SERVER enforces (gateway rbac_gate + services); this
 * only decides which menu entries / screens a login is offered, from the VERIFIED ui_grant (store/authStore).
 *
 * A login with custom roles keeps its own menu and gets the screens of its custom roles' ACTING system roles —
 * but only those whose catalogue module the custom roles actually carry (any action of it).
 * SCREEN_MODULES maps a screen path to the catalogue module (backend/shared/utils/rbac_catalogue.py MODULES).
 */
import { useAuthStore, type User } from "../store/authStore";

const SCREEN_MODULES: [string, string][] = [
  ["/employees", "people"], ["/employee-history", "people"], ["/documents", "people"], ["/masters", "masters"],
  ["/career-changes", "career"], ["/probation", "career"], ["/fixed-term", "career"], ["/onboarding", "onboarding"],
  ["/letters", "letters"], ["/exits", "exit"],
  ["/hiring", "hiring"], ["/referrals", "hiring"], ["/performance", "performance"], ["/comp", "compensation"],
  ["/salary-revisions", "compensation"],
  ["/payroll", "payroll"],
  ["/migration", "migration"],
  ["/leave", "leave"], ["/absence-calendar", "leave"], ["/stat-holidays", "leave"],
  ["/attendance", "attendance"], ["/attendance-analytics", "attendance"], ["/time-clock", "attendance"],
  ["/scheduling", "attendance"], ["/timesheets", "attendance"], ["/psa", "projects"], ["/approvals", "approvals"],
  ["/command-center", "approvals"], ["/reports", "reports"], ["/analytics", "reports"], ["/ai", "ai"],
  ["/engage", "engage"], ["/learning", "learning"], ["/compliance", "compliance"],
  ["/audit", "audit"], ["/settings", "settings"],
  ["/company-settings", "settings"], ["/plan-billing", "settings"], ["/integrations", "integrations"],
  ["/users", "users"], ["/roles", "roles"],
];

/** Catalogue module of a screen path ("/payroll?tab=x" -> payroll), or null. Longest prefix wins. */
export function screenModule(to: string): string | null {
  const path = (to || "").split("?")[0];
  let best: string | null = null;
  let len = -1;
  for (const [pre, mod] of SCREEN_MODULES) {
    if ((path === pre || path.startsWith(pre + "/")) && pre.length > len) { best = mod; len = pre.length; }
  }
  return best;
}

/** Modules the login's CUSTOM roles open (any permission of the module). */
function customModules(user: User | null | undefined): Set<string> {
  const out = new Set<string>();
  for (const p of user?.custom_permissions || []) out.add(p.split(".")[0]);
  return out;
}

/** Whether a menu entry is offered: the login's own system role, or (custom roles) an acting role of one of its
 *  custom roles AND the screen's module is in its permissions. */
export function navItemAllowed(item: { to: string; roles: string[] }, user: User | null | undefined): boolean {
  if (!user?.role) return false;
  if (item.roles.includes(user.role)) return true;
  const extra = (user.ui_roles || []).filter((r) => r !== user.role);
  if (!extra.length || !item.roles.some((r) => extra.includes(r))) return false;
  const mod = screenModule(item.to);
  return !!mod && customModules(user).has(mod);
}

/** The menu for a login: its own menu, plus — for a login with custom roles — an "Extra access" group holding the
 *  entries of the full menu that its custom roles open and its own menu does not already have. */
export function menuWithGrantedAccess<G extends { label: string; items: { to: string; roles: string[] }[]; module?: string }>(
  base: G[], full: G[], user: User | null | undefined,
): G[] {
  const extra = (user?.ui_roles || []).filter((r) => r !== user?.role);
  if (!extra.length || !user) return base;
  const have = new Set(base.flatMap((g) => g.items.filter((i) => navItemAllowed(i, user)).map((i) => i.to)));
  const add: G["items"] = [];
  for (const g of full) {
    for (const i of g.items) {
      if (!have.has(i.to) && !i.roles.includes(user.role) && navItemAllowed(i, user)) { add.push(i); have.add(i.to); }
    }
  }
  if (!add.length) return base;
  const group = { label: "Extra access", items: add } as unknown as G;
  return [...base, group];
}

/** The system roles a SCREEN may act as for one permission: the login's own role, plus — when its custom roles carry
 *  `perm` — their acting roles. Pages that branch on the role (tabs, buttons) use this so a custom role shows its
 *  screens; the server still checks every call. */
export function screenRolesFor(user: User | null | undefined, perm: string): string[] {
  if (!user?.role) return [];
  const extra = (user.ui_roles || []).filter((r) => r !== user.role);
  return extra.length && (user.custom_permissions || []).includes(perm) ? [user.role, ...extra] : [user.role];
}

/** Acting system roles of the login's custom roles (from the verified grant), without its own role. */
function actingRoles(user: User | null | undefined): string[] {
  return (user?.ui_roles || []).filter((r) => r !== user?.role);
}

/** UI gate for ONE capability (go-live 2026-10-26 — pages no longer branch on the raw system role).
 *  * the login's SYSTEM role is in `roles` -> true (exactly the old `roles.includes(role)` behaviour);
 *  * otherwise a CUSTOM role opens it when the grant's custom permissions carry `perm` (any of them, for a list)
 *    AND one of its custom roles acts as a role in `roles` — the gateway re-stamps that acting role, so the
 *    service's role check passes too.
 *  The server decides every call; this only decides what is drawn. */
export function can(user: User | null | undefined, perm: string | readonly string[], roles: readonly string[]): boolean {
  if (!user?.role) return false;
  if (roles.includes(user.role)) return true;
  const mine = user.custom_permissions || [];
  const perms = typeof perm === "string" ? [perm] : perm;
  if (!perms.some((p) => mine.includes(p))) return false;
  return actingRoles(user).some((r) => roles.includes(r));
}

/** `can()` for the signed-in login (re-renders when the grant changes). */
export function useCan(perm: string | readonly string[], roles: readonly string[]): boolean {
  return useAuthStore((s) => can(s.user, perm, roles));
}

/** The role a screen should draw a role-shaped view for (e.g. the manager vs. HR layout): the system role when it is
 *  in `roles`, else the first acting role of a custom role that carries `perm` and is in `roles`, else the system
 *  role. Lets a custom role (e.g. built on employee, acting as HR) get the HR layout of a page it has `perm` for. */
export function viewRoleFor(user: User | null | undefined, perm: string | readonly string[], roles: readonly string[]): string {
  const own = user?.role || "";
  if (!user || roles.includes(own)) return own;
  const perms = typeof perm === "string" ? [perm] : perm;
  if (!perms.some((p) => (user.custom_permissions || []).includes(p))) return own;
  return actingRoles(user).find((r) => roles.includes(r)) || own;
}

export function useViewRole(perm: string | readonly string[], roles: readonly string[]): string {
  return useAuthStore((s) => viewRoleFor(s.user, perm, roles));
}
