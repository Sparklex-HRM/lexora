// Stream U4 — Settings hub: the front door of setup. One page that says what is
// configured, what still needs someone, and where every setup screen lives.
import { Link, Navigate } from "react-router-dom";
import {
  Settings, Building2, Users, CalendarClock, Landmark, Target, Heart, Plug, ShieldCheck,
  ArrowRight, CheckCircle2, AlertCircle, Circle, Search, LucideIcon,
} from "lucide-react";
import { PageHeader, Spinner } from "../components/ui";
import { useAuthStore, ADMIN_ROLES, HR_WRITE, PAYROLL_ACCESS, APPROVER_ROLES, ALL_STAFF } from "../store/authStore";
import { can, navItemAllowed } from "../config/permissions";   // 2026-10-26: UI gates = permission + role
import { useSetupReadiness, STATUS_LABEL, STATUS_CLASS, type ReadyItem, type ReadySection, type ReadyStatus } from "./settings/useSetupReadiness";

const HUB_ROLES = [...ADMIN_ROLES, "hr_admin"];

interface HubPage {
  name: string; what: string; to: string; roles: string[];
  /** Readiness item key whose status this page shows; omitted = "Open" (no endpoint decides it). */
  ready?: string;
}
interface HubSection { key: ReadySection; icon: LucideIcon; blurb: string; pages: HubPage[]; }

const SECTIONS: HubSection[] = [
  { key: "Company", icon: Building2, blurb: "Who you are on paper and on every letter, pay stub and invoice.", pages: [
    { name: "Company profile", what: "Legal name, addresses, phone, website, logo.", to: "/company-settings", roles: ADMIN_ROLES, ready: "company" },
    { name: "Country, currency, financial year", what: "Canada, CAD and the tax year (calendar year for T4 / RL-1).", to: "/company-settings", roles: ADMIN_ROLES, ready: "locale" },
    { name: "Numbering prefixes", what: "EMP-0001, LTR-, REQ-, OFR- and friends.", to: "/company-settings", roles: ADMIN_ROLES, ready: "numbering" },
    { name: "Legal entities", what: "Group companies: CRA Business Number, payroll account (RP), Revenu Québec number, WCB accounts, bank accounts, signatory, letterhead.", to: "/settings/entities", roles: ADMIN_ROLES },
    { name: "Branches", what: "Sites or offices people are attached to.", to: "/ops/branches", roles: HR_WRITE },
    { name: "Plan and billing", what: "Your Lexora plan, headcount billed, GST/HST invoices.", to: "/plan-billing", roles: ADMIN_ROLES, ready: "billing" },
  ]},
  { key: "People", icon: Users, blurb: "Master lists, who can sign in, and the paperwork people receive.", pages: [
    { name: "Master data", what: "Departments, job titles, grades, locations, leave types, cost centres.", to: "/masters", roles: HR_WRITE, ready: "departments" },
    { name: "Users and logins", what: "Invite people, set roles, disable leavers.", to: "/users", roles: [...ADMIN_ROLES, "hr_admin"], ready: "users" },
    { name: "Roles", what: "What each role may see and do.", to: "/roles", roles: HR_WRITE, ready: "roles" },
    { name: "Onboarding checklist", what: "Joiner tasks and the intake steps new hires fill in.", to: "/onboarding", roles: HR_WRITE },
    { name: "Probation rules", what: "Default probation months and reminders.", to: "/probation", roles: HR_WRITE },
    { name: "Letter templates", what: "Offer, confirmation, pay change and employment verification letters.", to: "/letters", roles: HR_WRITE, ready: "letters" },
    { name: "Exit settings", what: "Notice policies, clearance departments, rehire rule.", to: "/exits", roles: [...HR_WRITE, "payroll_admin"] },
    { name: "Documents", what: "Document types and the employee file vault.", to: "/ops/documents", roles: HR_WRITE },
  ]},
  { key: "Time & leave", icon: CalendarClock, blurb: "Working days, shifts, holidays and leave policy.", pages: [
    { name: "Work week and hours", what: "Working days, weekly offs, default start and end times.", to: "/company-settings", roles: ADMIN_ROLES, ready: "work_week" },
    { name: "Work schedules / shifts", what: "Named schedules people are assigned to; needed for late and absent marking.", to: "/attendance", roles: ALL_STAFF, ready: "schedule" },
    { name: "Scheduling and roster", what: "Plan and publish shifts by week.", to: "/scheduling", roles: [...HR_WRITE, "manager"] },
    { name: "Holiday list", what: "Statutory holidays by province for the year.", to: "/stat-holidays", roles: ALL_STAFF, ready: "leave" },
    { name: "Leave types", what: "Vacation, sick, personal and other leave with days per year and carry-over.", to: "/masters", roles: HR_WRITE, ready: "leave" },
    { name: "Work sites and phones", what: "Geo-fenced sites and registered devices for mobile punch.", to: "/mobile/sites", roles: [...HR_WRITE, "manager"] },
    { name: "Translations", what: "Which languages the mobile app offers.", to: "/mobile/translations", roles: HR_WRITE },
    { name: "Biometric import", what: "Device punch files to attendance.", to: "/biometric-import", roles: HR_WRITE },
  ]},
  { key: "Payroll & statutory", icon: Landmark, blurb: "Pay structure, statutory numbers and the rules payroll runs on.", pages: [
    { name: "Pay components", what: "Extra earnings and deductions used in runs.", to: "/masters", roles: HR_WRITE, ready: "pay" },
    { name: "Registration numbers", what: "CRA payroll account and provincial numbers.", to: "/company-settings", roles: ADMIN_ROLES, ready: "registrations" },
    { name: "Canadian statutory values", what: "CPP, EI, remittance shapes and year-end due dates.", to: "/payroll/canada/statutory-values", roles: PAYROLL_ACCESS },
    { name: "CRA remittance schedule", what: "Source-deduction due dates by remitter type, plus EHT and WCB.", to: "/payroll/canada/remittances", roles: PAYROLL_ACCESS },
    { name: "Year-end (T4 / RL-1)", what: "Slips, summaries and the year-end checklist.", to: "/payroll/canada/year-end", roles: PAYROLL_ACCESS },
    { name: "Salary revision settings", what: "Two-step approval thresholds for hikes.", to: "/salary-revisions", roles: [...PAYROLL_ACCESS, "hr_manager"] },
    { name: "Payroll date rules", what: "Pro-ration basis and working weekdays.", to: "/payroll", roles: PAYROLL_ACCESS },
    { name: "Data migration (Canada)", what: "Import employees, YTD and history.", to: "/migration/canada", roles: [...HR_WRITE, "payroll_admin"] },
    { name: "Migration checklist", what: "Tick off every go-live step.", to: "/migration/checklist", roles: [...HR_WRITE, "payroll_admin"] },
    { name: "Parallel run (proof)", what: "Compare a Lexora run with the old provider.", to: "/migration/parallel-run", roles: [...HR_WRITE, "payroll_admin"] },
    { name: "GL export mapping", what: "Ledger accounts for the payroll journal your accountant imports.", to: "/gl-export", roles: PAYROLL_ACCESS },
  ]},
  { key: "Talent", icon: Target, blurb: "Hiring, appraisal and benefit rules.", pages: [
    { name: "Hiring settings", what: "Requisition approval chain, offer validity, careers page.", to: "/hiring/settings", roles: HR_WRITE },
    { name: "Performance settings", what: "Rating scale, competencies, goal weights, increment matrix.", to: "/performance/settings", roles: HR_WRITE },
    { name: "Benefit plans", what: "Plans people can enrol in.", to: "/benefits", roles: HR_WRITE },
    { name: "Carriers", what: "Insurers and providers behind each plan.", to: "/carriers", roles: HR_WRITE },
  ]},
  { key: "Engage", icon: Heart, blurb: "Helpdesk categories, surveys, kudos and learning.", pages: [
    { name: "Helpdesk", what: "Categories, assignees and SLAs for staff tickets.", to: "/engage/helpdesk", roles: ALL_STAFF },
    { name: "Surveys", what: "Pulse and exit survey templates.", to: "/engage/surveys", roles: ALL_STAFF },
    { name: "Kudos", what: "Recognition badges and values.", to: "/engage/kudos", roles: ALL_STAFF },
    { name: "Learning", what: "Courses and mandatory training.", to: "/engage/learning", roles: ALL_STAFF },
    { name: "Assets", what: "Laptops, phones and ID cards issued to people.", to: "/engage/assets", roles: [...HR_WRITE, "manager", "payroll_admin"] },
  ]},
  { key: "Platform", icon: Plug, blurb: "Connections, e-mail, approvals and the AI watchdogs.", pages: [
    { name: "Integrations", what: "SSO, SCIM, API keys, webhooks, WhatsApp and calls.", to: "/integrations", roles: ADMIN_ROLES, ready: "integrations" },
    { name: "Notifications and e-mail", what: "Sender name, delivery log, templates.", to: "/notifications", roles: ALL_STAFF },
    { name: "Approvals and reminders", what: "Escalation days and delegation.", to: "/company-settings", roles: ADMIN_ROLES, ready: "approvals" },
    { name: "Approval workflows", what: "Who approves what: steps by role, manager level or person; thresholds; parallel; escalation; auto-approve; test run.", to: "/settings/approval-workflows", roles: [...HR_WRITE, "payroll_admin"] },
    { name: "Custom fields", what: "Your own fields on employees, candidates, assets and expense claims — forms, grids, exports, letters, reports.", to: "/settings/custom-fields", roles: [...ADMIN_ROLES, "hr_admin"] },
    { name: "Letter template editor", what: "Rich-text editor, merge fields, live preview, draft / publish / rollback.", to: "/settings/letter-templates", roles: HR_WRITE },
    { name: "AI watchdogs", what: "Which agents run and their thresholds.", to: "/ai/workforce", roles: ADMIN_ROLES },
    { name: "AI settings", what: "Your Claude key, model, what the assistant may do, monthly caps.", to: "/ai/settings", roles: ADMIN_ROLES },
    { name: "AI access tokens", what: "Tokens for Claude Desktop / ChatGPT / Copilot to reach the MCP server.", to: "/ai/tokens", roles: ADMIN_ROLES },
    { name: "AI usage", what: "Calls, tokens and estimated cost per user and feature.", to: "/ai/usage", roles: ALL_STAFF },
    { name: "Audit log", what: "Who changed what, tamper-evident.", to: "/audit-log", roles: ADMIN_ROLES },
    { name: "Session audit", what: "Active sign-ins and devices.", to: "/security/sessions", roles: ALL_STAFF },
  ]},
  { key: "Compliance & privacy", icon: ShieldCheck, blurb: "Policies, compliance overview, safety and workers' compensation.", pages: [
    { name: "Compliance centre", what: "Filing and register overview.", to: "/compliance-center", roles: APPROVER_ROLES },
    { name: "Policies", what: "Company policies staff acknowledge.", to: "/ops/policies", roles: [...HR_WRITE, "manager", "payroll_admin"] },
    { name: "Safety register", what: "Incidents and investigations.", to: "/safety", roles: HR_WRITE },
    { name: "WCB / WSIB", what: "Rates and assessments by province.", to: "/wcb", roles: HR_WRITE },
    { name: "Privacy Centre", what: "PIPEDA consents, notice, Privacy Officer, breach register, retention.", to: "/privacy", roles: HR_WRITE },
    { name: "Privacy requests", what: "Access, correction and deletion requests (30-day limit).", to: "/data-requests", roles: HR_WRITE },
  ]},
];

const STATUS_ICON: Record<ReadyStatus, LucideIcon> = { done: CheckCircle2, needs: AlertCircle, optional: Circle, check: Search };
const STATUS_ICON_CLASS: Record<ReadyStatus, string> = { done: "text-green-500", needs: "text-amber-500", optional: "text-gray-300", check: "text-teal-500" };

function Pill({ status }: { status: ReadyStatus }) {
  return <span className={`${STATUS_CLASS[status]} whitespace-nowrap`}>{STATUS_LABEL[status]}</span>;
}

function ChecklistRow({ item }: { item: ReadyItem }) {
  const Icon = STATUS_ICON[item.status];
  return (
    <div className="grid grid-cols-[20px_1fr_auto] sm:grid-cols-[20px_minmax(180px,1fr)_minmax(0,2fr)_110px_60px] items-center gap-x-3 gap-y-0.5 px-4 py-2 border-b border-surface-border/70 last:border-b-0 hover:bg-surface/60">
      <Icon className={`h-4 w-4 ${STATUS_ICON_CLASS[item.status]}`} />
      <div className="text-sm font-medium text-gray-800 truncate">{item.label}</div>
      <div className="col-span-3 sm:col-span-1 col-start-2 sm:col-start-auto text-xs text-gray-500 truncate" title={item.detail}>{item.detail}</div>
      <div className="col-start-3 row-start-1 sm:col-start-auto sm:row-start-auto justify-self-end sm:justify-self-start"><Pill status={item.status} /></div>
      <Link to={item.to} className="hidden sm:inline-flex items-center gap-0.5 text-xs font-medium text-primary-600 hover:text-primary-700 justify-self-end">
        Go <ArrowRight className="h-3 w-3" />
      </Link>
    </div>
  );
}

export default function SettingsHubPage() {
  const user = useAuthStore((s) => s.user);
  const role = user?.role || "";
  const r = useSetupReadiness();
  if (!role) return <Navigate to="/login" replace />;
  if (!can(user, "settings.view", HUB_ROLES)) return <Navigate to="/" replace />;

  const byKey = Object.fromEntries(r.items.map((i) => [i.key, i]));
  const order: ReadyStatus[] = ["needs", "check", "done", "optional"];
  const rows = [...r.items].sort((a, b) => order.indexOf(a.status) - order.indexOf(b.status));
  // 2026-10-26: a custom role sees the hub pages its permissions open (same rule as the menu)
  const visible = (p: HubPage) => navItemAllowed({ to: p.to, roles: p.roles }, user);

  return (
    <div className="space-y-4">
      <PageHeader title="Settings hub" icon={Settings}
        subtitle="Everything that sets Lexora up for your company, in one place — what is done, what still needs someone, and where each screen lives."
        actions={<Link to="/company-settings" className="btn-secondary text-xs whitespace-nowrap">Company settings</Link>} />

      {/* Setup checklist */}
      <div className="card !p-0">
        <div className="px-4 pt-4 pb-3 flex flex-wrap items-end justify-between gap-3">
          <div>
            <p className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider">Setup checklist</p>
            <h2 className="font-display font-semibold text-gray-800 text-base leading-tight">
              {r.loading ? "Checking what is set up…" : r.total === 0 ? "Nothing to check for your role" : `${r.done} of ${r.total} done`}
            </h2>
            <p className="text-xs text-gray-500 mt-0.5">
              {r.needs > 0 ? `${r.needs} item${r.needs === 1 ? "" : "s"} still need${r.needs === 1 ? "s" : ""} setup` : r.loading ? "" : "Required items are complete"}
              {r.optional > 0 && ` · ${r.optional} optional`}
              {r.check > 0 && ` · ${r.check} open to check`}
            </p>
          </div>
          <div className="flex items-center gap-3 min-w-[200px] flex-1 sm:flex-none sm:w-72">
            <div className="h-2 flex-1 rounded-full bg-primary-50 overflow-hidden">
              <div className="h-full rounded-full bg-primary-500 transition-all" style={{ width: `${r.pct}%` }} />
            </div>
            <span className="text-sm font-bold text-gray-800 tabular-nums w-10 text-right">{r.pct}%</span>
          </div>
        </div>
        <div className="hidden sm:grid grid-cols-[20px_minmax(180px,1fr)_minmax(0,2fr)_110px_60px] gap-x-3 px-4 py-1.5 border-y border-surface-border/70 bg-surface/50 text-[10px] font-semibold text-gray-400 uppercase tracking-wider">
          <span /><span>Item</span><span>What we found</span><span>Status</span><span className="text-right">Open</span>
        </div>
        {r.loading && rows.length === 0 ? (
          <div className="grid place-items-center py-10"><Spinner /></div>
        ) : rows.length === 0 ? (
          <p className="px-4 py-6 text-sm text-gray-400">Your role cannot read any of the setup endpoints. Ask an org admin.</p>
        ) : rows.map((i) => <ChecklistRow key={i.key} item={i} />)}
        <p className="px-4 py-2 text-[11px] text-gray-400 border-t border-surface-border/70">
          Done / Needs setup come from live data. “Open to check” means no endpoint can tell us — open the page and look. Optional items are not counted in the total.
        </p>
      </div>

      {/* Sections */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {SECTIONS.map((s) => {
          const pages = s.pages.filter(visible);
          if (pages.length === 0) return null;
          const needs = pages.filter((p) => p.ready && byKey[p.ready]?.status === "needs").length;
          return (
            <div key={s.key} className="card !p-0 flex flex-col">
              <div className="px-4 pt-4 pb-2 flex items-start gap-3">
                <span className="h-9 w-9 shrink-0 rounded-xl bg-primary-50 text-primary-600 grid place-items-center"><s.icon className="h-4 w-4" /></span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center justify-between gap-2">
                    <h3 className="font-display font-semibold text-gray-800 text-[13px]">{s.key}</h3>
                    {needs > 0 && <span className="badge-amber">{needs} to set up</span>}
                  </div>
                  <p className="text-[11px] text-gray-400 leading-snug">{s.blurb}</p>
                </div>
              </div>
              <div className="px-1 pb-2 flex-1">
                {pages.map((p) => {
                  const it = p.ready ? byKey[p.ready] : undefined;
                  return (
                    <Link key={p.to + p.name} to={p.to} className="group flex items-center gap-3 px-3 py-1.5 rounded-lg hover:bg-surface transition-colors">
                      <div className="min-w-0 flex-1">
                        <div className="text-sm text-gray-800 font-medium group-hover:text-primary-700 truncate">{p.name}</div>
                        <div className="text-[11px] text-gray-400 truncate" title={it?.detail || p.what}>{it ? it.detail : p.what}</div>
                      </div>
                      {it ? <Pill status={it.status} /> : <span className="text-[11px] text-gray-400 whitespace-nowrap">Open</span>}
                      <ArrowRight className="h-3.5 w-3.5 text-gray-300 group-hover:text-primary-500 shrink-0" />
                    </Link>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
