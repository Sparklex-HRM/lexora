// Stream U4 — one readiness computation shared by the Settings hub (/settings)
// and the dashboard "Setup x/N complete" banner. Every item is decided from a
// real GET; when no endpoint can tell us (or the caller's role gets a 403) the
// item is "check" = "Open to check" with a link, never a guess.
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { useAuthStore, ADMIN_ROLES, HR_WRITE } from "../../store/authStore";
import { mastersApi, timeApi, type MasterRow, type WorkSchedule } from "../../api/endpoints";
import { settingsApi, type SettingsAll } from "../../api/platform";
import { lettersApi } from "../../api/lifecycle";
import { usersApi, type UserList } from "../../api/identity";
import { integrationsApi, type Overview } from "../../api/integrations";
import { billingApi, type InvoicePreview } from "../../api/privacy";
import api from "../../api/client";
import { can } from "../../config/permissions";   // 2026-10-26: UI gates = permission + role (custom roles)

export type ReadyStatus = "done" | "needs" | "optional" | "check";
export type ReadySection = "Company" | "People" | "Time & leave" | "Payroll & statutory" | "Talent" | "Engage" | "Platform" | "Compliance & privacy";

export interface ReadyItem {
  key: string;
  label: string;
  /** Plain sentence: what we found, or what is missing. */
  detail: string;
  status: ReadyStatus;
  to: string;
  section: ReadySection;
  /** "Open to check" reason when no endpoint decides it. */
  why?: string;
}

export interface SetupReadiness {
  loading: boolean;
  items: ReadyItem[];
  /** Required items (done + needs) — optional and "open to check" are not counted. */
  total: number;
  done: number;
  needs: number;
  optional: number;
  check: number;
  complete: boolean;
  pct: number;
  /** Raw counts other screens reuse (hub section cards). */
  counts: Record<string, number>;
  /** Which GETs 403'd for this role (hub shows them as "Open to check"). */
  forbidden: string[];
}

/** A query that never retries and never throws: 403 is a normal answer here. */
function useSafe<T>(key: string, fn: () => Promise<T>, enabled: boolean): UseQueryResult<T> {
  return useQuery<T>({ queryKey: ["setup", key], queryFn: fn, enabled, retry: false, staleTime: 60_000 });
}
const is403 = (q: UseQueryResult<any>) => !!q.error && (q.error as any)?.response?.status === 403;

export const STATUS_LABEL: Record<ReadyStatus, string> = { done: "Done", needs: "Needs setup", optional: "Optional", check: "Open to check" };
export const STATUS_CLASS: Record<ReadyStatus, string> = {
  done: "badge-green", needs: "badge-amber", optional: "bg-gray-100 text-gray-600 inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium", check: "badge-teal",
};

/** Mirrors backend shared/utils/employee_fixes.format_number: LX + 1 → LX-0001, LX/ + 1 → LX/0001. */
export function employeeNumberSample(prefix: string, padding: number): string {
  const p = (prefix || "").trim();
  const body = "1".padStart(Math.max(1, Math.min(padding || 4, 10)), "0");
  if (!p) return body;
  return /[A-Za-z0-9]$/.test(p) ? `${p}-${body}` : `${p}${body}`;
}

export function useSetupReadiness(): SetupReadiness {
  const user = useAuthStore((s) => s.user);
  const on = can(user, "settings.view", [...ADMIN_ROLES, "hr_admin"]);
  const isAdmin = can(user, "settings.admin", ADMIN_ROLES);
  const hr = can(user, "people.view", HR_WRITE);
  const year = new Date().getFullYear();

  const settings = useSafe<SettingsAll>("settings", settingsApi.all, on);
  const masters = useSafe<Record<string, number>>("masters-summary", mastersApi.summary, on && hr);
  const holidays = useSafe<MasterRow[]>("holidays", () => mastersApi.list("stat-holidays"), on);
  const schedules = useSafe<WorkSchedule[]>("schedules", timeApi.schedules, on);
  const payComponents = useSafe<MasterRow[]>("pay-components", () => mastersApi.list("pay-components"), on && hr);
  const templates = useSafe<any[]>("letter-templates", lettersApi.templates, on);
  const users = useSafe<UserList>("users", () => usersApi.list(), on);
  const integrations = useSafe<Overview>("integrations", integrationsApi.overview, on && isAdmin);
  // S1: the real next employee number (prefix + padding + counter), not a hard-coded "EMP-0001".
  const nextNumber = useSafe<{ next_number: string; prefix: string; padding: number }>("employee-next-number",
    () => api.get("/employees/next-number").then((x) => x.data), on && hr);
  const billing = useSafe<InvoicePreview>("billing", () => billingApi.preview(), on && isAdmin);

  const all = [settings, masters, holidays, schedules, payComponents, templates, users, integrations, billing];
  const loading = on && all.some((q) => q.isLoading && q.fetchStatus !== "idle");
  const forbidden: string[] = [];
  const g = settings.data?.groups || {};
  const counts: Record<string, number> = {};
  const items: ReadyItem[] = [];

  const push = (i: ReadyItem) => items.push(i);
  const checkItem = (key: string, label: string, to: string, section: ReadySection, why: string): ReadyItem =>
    ({ key, label, detail: why, status: "check", to, section, why });

  // ── Company ──────────────────────────────────────────────────────────────
  if (settings.data) {
    const c = g.company?.value || {};
    const addr = Array.isArray(c.addresses) ? c.addresses.length : 0;
    const okCompany = !!c.legal_name && addr > 0;
    push({ key: "company", label: "Company profile", section: "Company", to: "/company-settings",
      status: okCompany ? "done" : "needs",
      detail: okCompany ? `${c.legal_name} · ${addr} address${addr === 1 ? "" : "es"}` : [!c.legal_name && "legal name", addr === 0 && "registered address"].filter(Boolean).join(" and ") + " missing" });
    const l = g.locale?.value || {};
    const okLocale = !!l.country && !!l.currency;
    push({ key: "locale", label: "Country, currency and financial year", section: "Company", to: "/company-settings",
      status: okLocale ? "done" : "needs",
      detail: okLocale ? `${l.country} · ${l.currency} · FY starts month ${l.fy_start_month ?? "?"}${g.locale?.saved ? "" : " (from sign-up, not saved yet)"}` : "Country or currency not set" });
    const ww = g.work_week, wh = g.work_hours;
    const okWork = !!(ww?.saved || wh?.saved);
    const days = (ww?.value?.working_days || []).length;
    push({ key: "work_week", label: "Work week and hours", section: "Time & leave", to: "/company-settings",
      status: okWork ? "done" : "needs",
      detail: okWork ? `${days} working days · ${wh?.value?.start}–${wh?.value?.end}, ${wh?.value?.hours_per_day} h/day` : `Defaults in use (${days} days, ${wh?.value?.start}–${wh?.value?.end}) — confirm and save once` });
    const nb = g.numbering;
    push({ key: "numbering", label: "Numbering prefixes", section: "Company", to: "/company-settings",
      status: nb?.saved ? "done" : "optional",
      detail: `${nextNumber.data?.next_number ? `Next employee number ${nextNumber.data.next_number}` : `${employeeNumberSample(nb?.value?.employee_prefix ?? "EMP", nb?.value?.padding || 4)} style`}${nb?.saved ? "" : " (defaults — fine to keep)"}` });
    const ap = g.approvals;
    push({ key: "approvals", label: "Approval chains and reminders", section: "Platform", to: "/company-settings",
      status: ap?.saved ? "done" : "optional",
      detail: `Remind approvers after ${ap?.value?.escalation_days ?? 3} days${ap?.saved ? "" : " (default) · chains follow the manager → HR order"}` });
    {
      const regs: any[] = g.registrations?.value?.items || [];
      push({ key: "registrations", label: "Statutory registration numbers", section: "Payroll & statutory", to: "/company-settings",
        status: regs.length ? "done" : "needs",
        detail: regs.length ? `${regs.length} number${regs.length === 1 ? "" : "s"} on file (${regs.map((r) => r.label).filter(Boolean).slice(0, 3).join(", ")})` : "No CRA / provincial account numbers recorded" });
    }
  } else if (is403(settings)) {
    forbidden.push("settings");
    push(checkItem("company", "Company profile", "/company-settings", "Company", "Your role cannot read company settings — ask an org admin"));
  }

  // ── People masters ───────────────────────────────────────────────────────
  if (masters.data) {
    Object.assign(counts, masters.data);
    const m = masters.data;
    const need = (key: string, label: string, what: string, section: ReadySection = "People") =>
      push({ key, label, section, to: "/masters", status: (m[key] || 0) > 0 ? "done" : "needs",
        detail: (m[key] || 0) > 0 ? `${m[key]} ${what}` : `No ${what} yet — add at least one` });
    need("departments", "Departments", "departments");
    need("designations", "Job titles", "job titles");
    need("locations", "Locations", "locations");
    push({ key: "grades", label: "Grades", section: "People", to: "/masters", status: (m.grades || 0) > 0 ? "done" : "optional",
      detail: (m.grades || 0) > 0 ? `${m.grades} grades` : "No grades — only needed if pay bands or letters use them" });
    const lt = m["leave-types"] || 0;
    const hol = (holidays.data || []).filter((h: any) => String(h.holiday_date || "").startsWith(String(year))).length;
    counts.holidays_this_year = hol;
    push({ key: "leave", label: `Leave types and ${year} holiday list`, section: "Time & leave", to: lt > 0 ? "/stat-holidays" : "/masters",
      status: lt > 0 && hol > 0 ? "done" : "needs",
      detail: lt > 0 && hol > 0 ? `${lt} leave types · ${hol} holidays in ${year}` : lt === 0 ? "No leave types yet" : `${lt} leave types but no holidays entered for ${year}` });
  } else if (is403(masters)) {
    forbidden.push("masters");
    push(checkItem("departments", "Departments, job titles, locations", "/masters", "People", "Your role cannot read master data"));
  }

  // ── Work schedule ────────────────────────────────────────────────────────
  if (schedules.data) {
    const n = schedules.data.filter((s) => s.is_active !== false).length;
    counts.schedules = n;
    push({ key: "schedule", label: "Work schedule / shift", section: "Time & leave", to: "/attendance",
      status: n > 0 ? "done" : "needs",
      detail: n > 0 ? `${n} schedule${n === 1 ? "" : "s"} (${schedules.data.find((s) => s.is_default)?.name || schedules.data[0].name})` : "No work schedule defined yet — attendance cannot mark late or absent without one" });
  } else if (is403(schedules)) forbidden.push("schedules");

  // ── Pay components ───────────────────────────────────────────────────────
  if (payComponents.data) {
    const n = payComponents.data.length;
    push({ key: "pay", label: "Pay components", section: "Payroll & statutory", to: "/masters",
      status: n > 0 ? "done" : "optional", detail: n > 0 ? `${n} pay components` : "None — CPP/QPP, EI and income tax are computed; add components only for extra earnings or deductions" });
  }

  // ── Letters ──────────────────────────────────────────────────────────────
  if (templates.data) {
    const n = templates.data.filter((t: any) => t.active !== false).length;
    counts.letter_templates = n;
    push({ key: "letters", label: "Letter templates", section: "People", to: "/letters",
      status: n > 0 ? "done" : "needs", detail: n > 0 ? `${n} templates (appointment, confirmation, …)` : "No letter templates — open Letters once to seed the defaults" });
  } else if (is403(templates)) {
    forbidden.push("letters");
    push(checkItem("letters", "Letter templates", "/letters", "People", "Your role cannot read letter templates"));
  }

  // ── Users & roles ────────────────────────────────────────────────────────
  if (users.data) {
    const live = users.data.items.filter((u) => u.status === "active");
    const nonAdmin = live.filter((u) => !ADMIN_ROLES.includes(u.role)).length;
    counts.logins = live.length; counts.logins_non_admin = nonAdmin;
    push({ key: "users", label: "Users and logins", section: "People", to: "/users",
      status: nonAdmin > 0 ? "done" : "needs",
      detail: nonAdmin > 0 ? `${live.length} active logins · ${nonAdmin} besides the org admin` : "Only the org admin can sign in — invite HR, payroll, managers and staff" });
    const roles = users.data.roles?.length || 0;
    push({ key: "roles", label: "Roles reviewed", section: "People", to: "/roles", status: "optional",
      detail: `${roles || 7} built-in roles — review what each may see before inviting people` });
  } else if (is403(users)) {
    forbidden.push("users");
    push(checkItem("users", "Users and logins", "/users", "People", "Your role cannot list logins"));
  }

  // ── Integrations, plan ───────────────────────────────────────────────────
  if (integrations.data) {
    const live = integrations.data.providers.filter((p) => p.enabled && p.status === "configured").length;
    counts.integrations_live = live;
    push({ key: "integrations", label: "Integrations", section: "Platform", to: "/integrations",
      status: live > 0 ? "done" : "optional",
      detail: live > 0 ? `${live} provider${live === 1 ? "" : "s"} switched on` : "None switched on — webhooks, WhatsApp and calls live here" });
  } else if (is403(integrations) || !isAdmin) {
    if (is403(integrations)) forbidden.push("integrations");
    push(checkItem("integrations", "Integrations", "/integrations", "Platform", "Org admins manage integrations"));
  }
  if (billing.data) {
    const b = billing.data;
    const ok = !!b.billing_email;
    push({ key: "billing", label: "Plan and billing", section: "Company", to: "/plan-billing",
      status: ok ? "done" : "optional",
      detail: `${b.plan_label || b.plan} plan · ${b.active_employees} active employees${ok ? ` · invoices to ${b.billing_email}` : " · add a billing e-mail and GST/HST number for invoices"}` });
  } else if (is403(billing) || !isAdmin) {
    if (is403(billing)) forbidden.push("billing");
    push(checkItem("billing", "Plan and billing", "/plan-billing", "Company", "Org admins see the plan"));
  }

  const done = items.filter((i) => i.status === "done").length;
  const needs = items.filter((i) => i.status === "needs").length;
  const optional = items.filter((i) => i.status === "optional").length;
  const check = items.filter((i) => i.status === "check").length;
  const total = done + needs;
  return {
    loading, items, total, done, needs, optional, check, counts, forbidden,
    complete: !loading && total > 0 && needs === 0,
    pct: total ? Math.round((100 * done) / total) : 0,
  };
}
