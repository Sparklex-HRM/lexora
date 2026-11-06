import { useState, useEffect } from "react";
import { NavLink, Outlet, useNavigate, useLocation } from "react-router-dom";
import clsx from "clsx";
import {
  LayoutDashboard, Users, UserPlus, Wallet, CalendarClock, CalendarDays,
  CheckSquare, Gauge, Bot, Activity, LogOut, Moon, Sun, Menu, X, ChevronDown,
  BarChart3, Database, ShieldCheck, Radar, LayoutGrid, Crown, Coins, LucideIcon,
  FileText, Landmark, Award, Building2, Zap, TrendingUp, Layers, SlidersHorizontal,
  Receipt, PiggyBank, Fingerprint, ShieldAlert, FileCheck, DatabaseZap, Briefcase, Gift, History, Smartphone,
} from "lucide-react";
import Logo, { Monogram } from "./Logo";
import SubscriptionBanner from "./SubscriptionBanner";   // S4: trial / read-only strip
import { sessionApi } from "../api/saas";                 // S4: sign-out ends the server session
import Chatbot from "../pages/ai/CopilotPanel";   // G5: the HR copilot (shielded, tool-based) replaces the old AI chatbot panel
import BranchSwitcher from "./BranchSwitcher";
import EntityPicker from "./EntityPicker";       // D4: legal-entity view (group companies)
import NotificationBell from "./NotificationBell";
import CommandPalette from "./CommandPalette";   // global search (2026-10-20): pages + every tab, people, records, actions
import { useSearchHighlight } from "../search/useSearchHighlight";   // ?hl= from a search result flashes the record
import { Bell, KeyRound, Settings } from "lucide-react";
import { Workflow } from "lucide-react";   // Agentic operations add-on 2026-10-25
import { BookOpenCheck } from "lucide-react";   // HR Desk agent (phase 2, P2-C)
import { UserPlus as LcUserPlus, LogOut as LcLogOut, CalendarClock as LcCalendarClock } from "lucide-react";   // lifecycle agents 2026-10-26
import { deskApi } from "../pages/ai/desk/api";   // harassment-referral inbox shows only for designated recipients (server decides)
import { ClipboardList, Megaphone, FileSignature, MessagesSquare, ClipboardCheck, Target } from "lucide-react";   // Stream D menu icons
import { MODULES } from "../moduleCatalog";
import { ArrowUpRight, UserCheck, FileSignature as LettersIcon, TrendingUp as HikeIcon, History as HistoryIcon, Wallet as CompIcon } from "lucide-react";   // Stream B menu icons
import { BookOpen, LifeBuoy, MapPinned, Package, Timer } from "lucide-react";   // Stream G4 menu icons
import { Repeat } from "lucide-react";   // G2 2026-10-23 shift rotations
import { Newspaper, Send, ListTodo, FolderOpen, CalendarRange, ListChecks } from "lucide-react";   // 2026-10-16 portal batch 3
import { useQuery } from "@tanstack/react-query";
import { settingsApi } from "../api/platform";
import { Users as UsersIcon, Briefcase as BriefcaseIcon } from "lucide-react";   // Stream D7 menu icons
import LanguagePicker from "../i18n/LanguagePicker";
import { tx, useT } from "../i18n";
import { clearOfflineCaches } from "../pwa/register";
import { clearQueue } from "../pwa/queue";
import {
  useAuthStore, useUserRole, ROLE_LABEL,
  HR_WRITE, PAYROLL_ACCESS, APPROVER_ROLES, ALL_STAFF, ADMIN_ROLES, TEAM_APPROVER_ROLES } from "../store/authStore";
import { can, menuWithGrantedAccess, navItemAllowed } from "../config/permissions";   // 2026-10-24 custom roles
import { setOrgTimeZone } from "../lib/datetime";   // 2026-10-25: UTC store, org-zone today

/** Real ops module — links to /ops/:resource (uses catalog label/icon). */
const opsNav = (slug: string, resource: string, roles: string[]): NavItem => ({
  to: `/ops/${resource}`, label: MODULES[slug].title, icon: MODULES[slug].icon, roles,
});
/** Real page — catalog label/icon, but a real dedicated route. */
const real = (slug: string, to: string, roles: string[]): NavItem => ({
  to, label: MODULES[slug].title, icon: MODULES[slug].icon, roles,
});

interface NavItem { to: string; label: string; icon: LucideIcon; roles: string[]; exact?: boolean; ai?: boolean;
  /** HR Desk: shown only when /ai/desk/ic-referrals/access says the login is a designated harassment recipient (never by role). */
  icOnly?: boolean; }
// `module` gates the group on the org's subscription (Phase 4). Omitted = "core" = always on.
interface NavGroup { label: string; items: NavItem[]; module?: string; }

// /ops/:resource pages are editors and the route admits only these roles — the menu must not offer them to others.
const OPS_ROLES = [...HR_WRITE, "manager", "payroll_admin"];

const NAV: NavGroup[] = [
  { label: "Lexora Platform", items: [
    { to: "/platform", label: "Tenants", icon: Building2, roles: ["superadmin"], exact: true },
    { to: "/platform/revenue", label: "Revenue", icon: TrendingUp, roles: ["superadmin"] },
    { to: "/platform/pricing", label: "Plans & Pricing", icon: Layers, roles: ["superadmin"] },
  ]},
  { label: "Overview", items: [
    { to: "/", label: "Dashboard", icon: LayoutDashboard, roles: ALL_STAFF, exact: true },
    { to: "/command-center", label: "Command Center", icon: Radar, roles: APPROVER_ROLES },
  ]},
  { label: "People", items: [
    { to: "/employees", label: "Employees", icon: Users, roles: [...HR_WRITE, "manager"] },
    // S1: the employee role gets a read-only people directory on the same route (work facts only).
    { to: "/employees", label: "People", icon: Users, roles: ["employee", "accountant"] },   // 2026-10-20: accountant gets the directory too
    { to: "/onboarding", label: "Onboarding", icon: UserPlus, roles: HR_WRITE },
    // Stream B (lifecycle): job changes, probation, letters, per-employee history
    { to: "/career-changes", label: "Career Changes", icon: ArrowUpRight, roles: HR_WRITE },   // HR only (2026-10-21)
    { to: "/probation", label: "Probation Due", icon: UserCheck, roles: [...HR_WRITE, "manager"] },
    // 2026-10-22: fixed-term employees
    { to: "/fixed-term", label: "Fixed-term Contracts", icon: CalendarClock, roles: [...HR_WRITE, "manager"] },
    { to: "/letters", label: "Letters", icon: LettersIcon, roles: HR_WRITE },
    { to: "/employee-history", label: "Employee History", icon: HistoryIcon, roles: [...HR_WRITE, "payroll_admin"] },
    real("offboarding", "/offboarding", HR_WRITE),
    { to: "/exits", label: "Exits", icon: LogOut, roles: [...HR_WRITE, "payroll_admin"], exact: true },
    { to: "/exits/team", label: "Team exits", icon: Users, roles: ["manager"] },
    { to: "/exits/former", label: "Former employees", icon: History, roles: [...HR_WRITE, "payroll_admin"] },
    real("org-chart", "/org-chart", [...HR_WRITE, "manager"]),
    opsNav("documents", "documents", HR_WRITE),
  ]},
  { label: "Payroll", module: "payroll", items: [
    { to: "/payroll", label: "Run Payroll", icon: Wallet, roles: PAYROLL_ACCESS, exact: true },
    { to: "/payroll?tab=yearend&ye=t4", label: "Tax Forms (T4)", icon: FileText, roles: PAYROLL_ACCESS },
    { to: "/payroll?tab=yearend&ye=roe", label: "Records of Employment", icon: Award, roles: PAYROLL_ACCESS },
    { to: "/payroll?tab=yearend&ye=remit", label: "CRA Remittances", icon: Landmark, roles: PAYROLL_ACCESS },
    { to: "/salary-revisions", label: "Salary Revisions", icon: HikeIcon, roles: [...PAYROLL_ACCESS, "hr_manager"] },   // Stream B
    real("garnishments", "/garnishments", PAYROLL_ACCESS),
    { to: "/payroll?mode=offcycle", label: "Off-Cycle Pay", icon: Zap, roles: PAYROLL_ACCESS },
    real("gl-export", "/gl-export", PAYROLL_ACCESS),
    // Stream G6: Canada completeness + migration kit
    { to: "/payroll/canada/statutory-values", label: "Canadian statutory values", icon: FileCheck, roles: PAYROLL_ACCESS },
    { to: "/payroll/canada/remittances", label: "Remittances (PD7A / RQ)", icon: Landmark, roles: PAYROLL_ACCESS },
    { to: "/payroll/canada/stat-holiday", label: "Stat-holiday pay", icon: CalendarDays, roles: PAYROLL_ACCESS },
    { to: "/payroll/canada/accruals", label: "WCB / EHT accruals", icon: ShieldCheck, roles: PAYROLL_ACCESS },
    { to: "/payroll/canada/contractors", label: "Contractors (T4A)", icon: Briefcase, roles: PAYROLL_ACCESS },
    { to: "/payroll/canada/year-end", label: "Year-end checklist", icon: ClipboardCheck, roles: PAYROLL_ACCESS },
    { to: "/migration/canada", label: "Data Migration", icon: DatabaseZap, roles: [...HR_WRITE, "payroll_admin"] },
    { to: "/migration/parallel-run", label: "Parallel run (proof)", icon: FileCheck, roles: [...HR_WRITE, "payroll_admin"] },
    { to: "/migration/checklist", label: "Migration checklist", icon: ClipboardCheck, roles: [...HR_WRITE, "payroll_admin"] },
  ]},
  { label: "Time & Attendance", module: "time", items: [
    { to: "/attendance", label: "Attendance", icon: CalendarClock, roles: ALL_STAFF },
    real("timesheets", "/timesheets", ALL_STAFF),
    real("scheduling", "/scheduling", [...HR_WRITE, "manager"]),
    { to: "/scheduling/rotations", label: "Shift rotations", icon: Repeat, roles: [...HR_WRITE, "manager"] },   // G2
    { to: "/my-shifts", label: "My shifts & swaps", icon: CalendarRange, roles: ALL_STAFF },                  // G2
    { to: "/attendance-analytics", label: "Analytics", icon: BarChart3, roles: APPROVER_ROLES },
    real("time-clock", "/time-clock", ALL_STAFF),
    { to: "/biometric-import", label: "Biometric Import", icon: Fingerprint, roles: HR_WRITE },
  ]},
  { label: "Leave & Absence", module: "leave", items: [
    { to: "/leave", label: "Leave", icon: CalendarDays, roles: ALL_STAFF },
    real("absence-calendar", "/absence-calendar", ALL_STAFF),
    real("stat-holidays", "/stat-holidays", ALL_STAFF),
  ]},
  { label: "Benefits", module: "benefits", items: [
    real("benefits", "/benefits", HR_WRITE),
    real("enrollment", "/enrollment", ALL_STAFF),
    real("retirement", "/retirement", HR_WRITE),
    real("carriers", "/carriers", HR_WRITE),
  ]},
  { label: "Talent", module: "talent", items: [
    // Stream D: real hiring + performance pages (were localStorage mocks)
    { to: "/hiring/requisitions", label: "Requisitions", icon: ClipboardList, roles: [...HR_WRITE, "manager"] },
    { to: "/hiring/postings", label: "Job Postings", icon: Megaphone, roles: HR_WRITE },
    { to: "/hiring/candidates", label: "Candidates", icon: Briefcase, roles: [...HR_WRITE, "manager"] },
    { to: "/hiring/offers", label: "Offers", icon: FileSignature, roles: HR_WRITE },
    { to: "/hiring/reports", label: "Hiring Reports", icon: BarChart3, roles: HR_WRITE },
    { to: "/hiring/my-interviews", label: "My Interviews", icon: MessagesSquare, roles: ALL_STAFF },
    { to: "/referrals", label: "Refer a Friend", icon: Briefcase, roles: ALL_STAFF },   // D2: employee referral portal
    { to: "/performance/goals", label: "Goals", icon: TrendingUp, roles: ALL_STAFF },
    { to: "/performance/okrs", label: "Objectives & Key Results", icon: Target, roles: ALL_STAFF },                 // G4 2026-10-23
    { to: "/performance/reviews", label: "Reviews", icon: ClipboardCheck, roles: ALL_STAFF },
    { to: "/performance/feedback", label: "360 Feedback", icon: UsersIcon, roles: ALL_STAFF },                      // G4
    { to: "/performance/one-on-ones", label: "1:1s", icon: MessagesSquare, roles: ALL_STAFF },                       // G4
    { to: "/performance/improvement-plans", label: "Improvement Plans", icon: LifeBuoy, roles: TEAM_APPROVER_ROLES }, // G4 (confidential)
    { to: "/performance/cycles", label: "Appraisals & Increments", icon: Target, roles: HR_WRITE },
    { to: "/comp/cycles", label: "Compensation Cycles", icon: Coins, roles: HR_WRITE },                      // D5
    { to: "/comp/worksheets", label: "Comp Worksheets", icon: ClipboardList, roles: [...HR_WRITE, "manager"] },   // D5
    real("learning", "/learning", ALL_STAFF),
    real("succession", "/succession", HR_WRITE),
  ]},
  { label: "Staffing & Ops", module: "ops", items: [
    opsNav("roster", "roster", [...HR_WRITE, "manager"]),
    opsNav("clients", "clients", [...HR_WRITE, "manager"]),
    opsNav("projects", "projects", [...HR_WRITE, "manager"]),
    opsNav("vendors", "vendors", HR_WRITE),
    opsNav("invoicing", "invoices", [...ADMIN_ROLES, "payroll_admin"]),
    { to: "/psa", label: "Project Services (PSA)", icon: Briefcase, roles: [...HR_WRITE, "manager", "payroll_admin"] },   // Stream G2
    real("msa", "/msa", HR_WRITE),
  ]},
  { label: "Workflow", items: [
    { to: "/approvals", label: "Approvals", icon: CheckSquare, roles: ALL_STAFF },
    { to: "/approvals/whatsapp", label: "WhatsApp approvals", icon: MessagesSquare, roles: APPROVER_ROLES },   // G7 2026-10-23
    { to: "/notifications", label: "Notifications", icon: Bell, roles: ALL_STAFF },
    { to: "/raise-request", label: "Raise a request", icon: Send, roles: ALL_STAFF },                 // portal batch 3
    { to: "/request-types", label: "Request types", icon: ListChecks, roles: HR_WRITE },             // portal batch 3
    { to: "/tasks", label: "My tasks", icon: ListTodo, roles: ALL_STAFF },                           // portal batch 3
    opsNav("tasks", "tasks", OPS_ROLES),
  ]},
  { label: "Compliance", module: "compliance", items: [
    real("compliance-center", "/compliance-center", APPROVER_ROLES),
    real("wcb", "/wcb", HR_WRITE),
    real("safety", "/safety", HR_WRITE),
    opsNav("policies", "policies", OPS_ROLES),
    real("audit-log", "/audit-log", ADMIN_ROLES),
  ]},
  // PIPEDA privacy rights + own sign-in security are on EVERY plan (not the paid "compliance" module).
  { label: "Privacy & Security", items: [
    { to: "/privacy", label: "Privacy Centre", icon: ShieldAlert, roles: ALL_STAFF },
    { to: "/data-requests", label: "Privacy Requests", icon: FileText, roles: ALL_STAFF },
    { to: "/security/sessions", label: "Session Audit", icon: History, roles: ALL_STAFF },
    { to: "/security/account", label: "Account Security", icon: Fingerprint, roles: ALL_STAFF },
  ]},
  { label: "Insights", module: "analytics", items: [
    { to: "/reports/hub", label: "Reports Hub", icon: LayoutGrid, roles: APPROVER_ROLES },
    { to: "/reports/md-dashboard", label: "Executive Dashboard", icon: Crown, roles: APPROVER_ROLES },
    { to: "/reports", label: "Analytics", icon: BarChart3, roles: APPROVER_ROLES, exact: true },
    { to: "/reports/builder", label: "Report Builder", icon: Database, roles: APPROVER_ROLES },   // G5
    { to: "/analytics/people", label: "People Analytics", icon: BarChart3, roles: [...HR_WRITE, "manager", "payroll_admin"] },   // D7
    { to: "/analytics/positions", label: "Positions & Headcount", icon: BriefcaseIcon, roles: [...HR_WRITE, "manager", "payroll_admin"] },   // D7
    real("benchmarking", "/benchmarking", APPROVER_ROLES),
  ]},
  { label: "AI", module: "ai", items: [
    { to: "/ai/command-center", label: "Command Center", icon: Gauge, roles: ADMIN_ROLES, ai: true },
    { to: "/ai/agents", label: "Agents", icon: Workflow, roles: [...ADMIN_ROLES, "hr_admin", "payroll_admin"], ai: true },   // Agentic operations add-on 2026-10-25
    { to: "/ai/agents/payroll-auditor", label: "Payroll Auditor", icon: Workflow, roles: [...ADMIN_ROLES, "hr_admin", "payroll_admin"], ai: true },   // Payroll Auditor (Canada, restored 2026-10-26)
    { to: "/ai/agents/onboarding", label: "Onboarding Agent", icon: LcUserPlus, roles: [...ADMIN_ROLES, "hr_admin", "hr_manager", "payroll_admin"], ai: true },   // lifecycle agents 2026-10-26
    { to: "/ai/agents/exit", label: "Exit Agent", icon: LcLogOut, roles: [...ADMIN_ROLES, "hr_admin", "hr_manager", "payroll_admin"], ai: true },
    { to: "/ai/agents/attendance", label: "Attendance Agent", icon: LcCalendarClock, roles: [...ADMIN_ROLES, "hr_admin", "hr_manager", "payroll_admin"], ai: true },
    { to: "/ai/desk/policies", label: "HR Desk policies", icon: BookOpenCheck, roles: HR_WRITE, ai: true },                  // HR Desk agent (phase 2, P2-C)
    { to: "/ai/desk/ic", label: "Harassment referrals", icon: ShieldAlert, roles: ALL_STAFF, icOnly: true },                 // designated recipients / harassment investigators only (server-decided)
    { to: "/ai/workforce", label: "AI Workforce", icon: Bot, roles: ADMIN_ROLES, ai: true },
    { to: "/ai/activity", label: "AI Activity", icon: Activity, roles: ADMIN_ROLES, ai: true },
    { to: "/ai/economics", label: "AI Economics", icon: Coins, roles: ADMIN_ROLES, ai: true },
    { to: "/ai/compliance-health", label: "Compliance Health", icon: ShieldCheck, roles: [...ADMIN_ROLES, "hr_admin", "hr_manager", "payroll_admin"], ai: true },   // G5
    { to: "/ai/flight-risk", label: "Flight-Risk Signals", icon: Radar, roles: [...HR_WRITE, "manager"], ai: true },   // G5
    { to: "/ai/usage", label: "AI Usage", icon: Coins, roles: ALL_STAFF, ai: true },                                   // D3
    { to: "/ai/settings", label: "AI Settings", icon: Settings, roles: ADMIN_ROLES, ai: true },                       // D3
    { to: "/ai/tokens", label: "AI Access Tokens", icon: KeyRound, roles: ADMIN_ROLES, ai: true },                    // D3
  ]},
  { label: "Mobile", items: [   // Stream G4: phone-first self-service (installable, works offline)
    { to: "/mobile/home", label: "Mobile Home", icon: Smartphone, roles: ALL_STAFF },
    { to: "/mobile/punch", label: "Punch", icon: Timer, roles: ALL_STAFF },
    { to: "/mobile/team-punches", label: "Team punches", icon: MapPinned, roles: TEAM_APPROVER_ROLES },
    { to: "/mobile/sites", label: "Work sites & phones", icon: MapPinned, roles: [...HR_WRITE, "manager"] },
    { to: "/mobile/translations", label: "Translations", icon: FileText, roles: HR_WRITE },
  ]},
  { label: "Engage", items: [   // Stream G4: helpdesk, surveys, kudos, learning, assets
    { to: "/engage/helpdesk", label: "Helpdesk", icon: LifeBuoy, roles: ALL_STAFF },
    { to: "/engage/surveys", label: "Surveys", icon: ClipboardList, roles: ALL_STAFF },
    { to: "/engage/kudos", label: "Kudos", icon: Award, roles: ALL_STAFF },
    { to: "/engage/feed", label: "Company feed", icon: Newspaper, roles: ALL_STAFF },                // portal batch 3
    { to: "/engage/learning", label: "Learning", icon: BookOpen, roles: ALL_STAFF },
    { to: "/learning/my", label: "My learning", icon: BookOpen, roles: ALL_STAFF },                                   // D7
    { to: "/learning/admin", label: "Learning admin", icon: BookOpen, roles: [...HR_WRITE, "manager"] },             // D7
    { to: "/engage/surveys/admin", label: "Surveys admin", icon: ClipboardList, roles: HR_WRITE },                   // D7
    { to: "/engage/insights", label: "Manager insights", icon: UsersIcon, roles: [...HR_WRITE, "manager"] },             // D7
    { to: "/engage/assets", label: "Assets", icon: Package, roles: [...HR_WRITE, "manager", "payroll_admin"] },
    { to: "/engage/my-assets", label: "My assets", icon: Package, roles: ALL_STAFF },
  ]},
  { label: "Self-Service", items: [
    real("my-profile", "/my-profile", ALL_STAFF),
    real("my-pay", "/my-pay", ALL_STAFF),
    { to: "/my-compensation", label: "My Compensation", icon: CompIcon, roles: ALL_STAFF },   // Stream B
    { to: "/my-letters", label: "My Letters", icon: LettersIcon, roles: ALL_STAFF },
    { to: "/documents", label: "Document centre", icon: FolderOpen, roles: ALL_STAFF },              // portal batch 3
    { to: "/my-resignation", label: "My resignation", icon: LogOut, roles: ALL_STAFF },
    real("my-time", "/my-time", ALL_STAFF),
    real("my-benefits", "/my-benefits", ALL_STAFF),
    real("my-requests", "/my-requests", ALL_STAFF),
    { to: "/me/chat-link", label: "Chat link (Slack / Teams)", icon: MessagesSquare, roles: ALL_STAFF },   // Stream G3
  ]},
  { label: "Setup", items: [
    { to: "/settings", label: "Settings hub", icon: Settings, roles: ADMIN_ROLES },
    { to: "/masters", label: "Master Data", icon: Database, roles: HR_WRITE },
    { to: "/roles", label: "Roles & Permissions", icon: ShieldCheck, roles: ["org_admin", "hr_admin"] },   // 2026-10-24 custom roles
    { to: "/users", label: "Users & Logins", icon: KeyRound, roles: [...ADMIN_ROLES, "hr_admin"] },
    real("company-settings", "/company-settings", ADMIN_ROLES),
    { to: "/plan-billing", label: "Plan & Billing", icon: Wallet, roles: ADMIN_ROLES },
    opsNav("branches", "branches", HR_WRITE),
    real("integrations", "/integrations", ADMIN_ROLES),
  ]},
];

/** 2026-10-16 employee portal: the EMPLOYEE role gets its own menu — seven groups by intent,
 *  duplicates merged (Mobile Home is the Home page, My time / Timesheets are one
 *  timesheet, the two learning pages are one, Session audit lives inside Account security), AI
 *  Usage hidden. Every other role keeps NAV above, unchanged. */
const EMP = ["employee"];
const EMPLOYEE_NAV: NavGroup[] = [
  { label: "Home", items: [
    { to: "/", label: "Home", icon: LayoutDashboard, roles: EMP, exact: true },
    { to: "/my-requests", label: "My requests", icon: ClipboardList, roles: EMP },
    { to: "/raise-request", label: "Raise a request", icon: Send, roles: EMP },
    { to: "/tasks", label: "My tasks", icon: ListTodo, roles: EMP },
    { to: "/approvals", label: "Waiting on me", icon: CheckSquare, roles: EMP },
    { to: "/notifications", label: "Notifications", icon: Bell, roles: EMP },
  ]},
  { label: "Pay & benefits", items: [
    { to: "/my-pay", label: "My pay & pay stubs", icon: Wallet, roles: EMP },
    { to: "/my-compensation", label: "My compensation", icon: CompIcon, roles: EMP },
    { to: "/my-benefits", label: "Insurance & benefits", icon: ShieldCheck, roles: EMP },
  ]},
  { label: "Leave & attendance", items: [
    { to: "/attendance", label: "My attendance", icon: CalendarClock, roles: EMP },
    { to: "/leave", label: "Leave", icon: CalendarDays, roles: EMP },
    { to: "/stat-holidays", label: "Holidays", icon: CalendarDays, roles: EMP },
    { to: "/time-clock", label: "Punch in / out", icon: Timer, roles: EMP },
    { to: "/my-shifts", label: "My shifts & swaps", icon: CalendarRange, roles: EMP },   // G2 2026-10-23
    { to: "/my-time", label: "My timesheet", icon: History, roles: EMP },
    { to: "/absence-calendar", label: "Team calendar", icon: UsersIcon, roles: EMP },
  ]},
  { label: "Work", items: [
    { to: "/performance/goals", label: "Goals", icon: Target, roles: EMP },
    { to: "/performance/okrs", label: "Objectives", icon: Target, roles: EMP },                // G4
    { to: "/performance/reviews", label: "Reviews", icon: ClipboardCheck, roles: EMP },
    { to: "/performance/feedback", label: "360 feedback", icon: UsersIcon, roles: EMP },       // G4
    { to: "/performance/one-on-ones", label: "1:1s", icon: MessagesSquare, roles: EMP },       // G4
    { to: "/learning/my", label: "Learning", icon: BookOpen, roles: EMP },
    { to: "/hiring/my-interviews", label: "My interviews", icon: MessagesSquare, roles: EMP },
    { to: "/referrals", label: "Refer a friend", icon: Briefcase, roles: EMP },
    { to: "/employees", label: "People", icon: Users, roles: EMP },
  ]},
  { label: "Engage", items: [
    { to: "/engage/kudos", label: "Kudos", icon: Award, roles: EMP },
    { to: "/engage/feed", label: "Company feed", icon: Newspaper, roles: EMP },
    { to: "/engage/surveys", label: "Surveys", icon: ClipboardList, roles: EMP },
    { to: "/engage/helpdesk", label: "Helpdesk", icon: LifeBuoy, roles: EMP },
  ]},
  { label: "My records", items: [
    { to: "/my-profile", label: "My profile", icon: UserCheck, roles: EMP },
    { to: "/documents", label: "Document centre", icon: FolderOpen, roles: EMP },
    { to: "/my-letters", label: "My letters", icon: LettersIcon, roles: EMP },
    { to: "/engage/my-assets", label: "My assets", icon: Package, roles: EMP },
    { to: "/my-resignation", label: "Resignation", icon: LogOut, roles: EMP },
  ]},
  { label: "Account", items: [
    { to: "/security/account", label: "Account security", icon: Fingerprint, roles: EMP },
    { to: "/privacy", label: "Privacy & my information", icon: ShieldAlert, roles: EMP },
    { to: "/me/chat-link", label: "Chat link (Slack / Teams)", icon: MessagesSquare, roles: EMP },
  ]},
];

function useDarkMode(): [boolean, () => void] {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));
  const toggle = () => {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
    try { localStorage.setItem("lx-theme", next ? "dark" : "light"); } catch { /* ignore */ }
  };
  return [dark, toggle];
}

// Query-aware active check: items sharing a path (/payroll?tab=…&ye=…, ?mode=…)
// must match the distinguishing query params, not just the pathname — NavLink alone
// ignores the query, so every /payroll item would light up at once.
const NAV_DEFAULTS: Record<string, string> = { tab: "run", mode: "regular", ye: "" };
function navActive(to: string, pathname: string, search: string, exact?: boolean): boolean {
  const [path, qs = ""] = to.split("?");
  const pathOk = exact ? pathname === path : (pathname === path || pathname.startsWith(path + "/"));
  if (!pathOk) return false;
  const iP = new URLSearchParams(qs), cP = new URLSearchParams(search);
  for (const k of Object.keys(NAV_DEFAULTS)) {
    if ((iP.get(k) ?? NAV_DEFAULTS[k]) !== (cP.get(k) ?? NAV_DEFAULTS[k])) return false;
  }
  for (const [k, v] of iP) { if (!(k in NAV_DEFAULTS) && cP.get(k) !== v) return false; }
  return true;
}

export default function DashboardLayout() {
  const nav = useNavigate();
  const location = useLocation();
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const role = useUserRole() || "";
  const [dark, toggleDark] = useDarkMode();
  // 2026-10-25: "today" on every screen = the org's calendar (company setting locale.timezone, default America/Toronto).
  const orgLocale = useQuery({ queryKey: ["org-locale-tz"], queryFn: () => settingsApi.get<{ timezone?: string }>("locale"), staleTime: 300_000, retry: false, enabled: !!user });
  useEffect(() => { setOrgTimeZone(orgLocale.data?.timezone); }, [orgLocale.data?.timezone]);
  // HR Desk (phase 2): the sealed harassment-referral inbox appears only for designated recipients — the server decides, never the role.
  const icAccess = useQuery({ queryKey: ["desk", "ic", "access"], queryFn: deskApi.icAccess, staleTime: 300_000, retry: false, enabled: !!user });

  const [sidebarOpen, setSidebarOpen] = useState(() => window.innerWidth >= 1024);
  const [chatOpen, setChatOpen] = useState(false);
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(() => {
    try { return new Set(JSON.parse(localStorage.getItem("lx-nav-collapsed") || "[]")); }
    catch { return new Set(); }
  });

  useEffect(() => {
    try { localStorage.setItem("lx-nav-collapsed", JSON.stringify([...collapsedGroups])); }
    catch { /* ignore */ }
  }, [collapsedGroups]);

  const toggleGroup = (label: string) =>
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      next.has(label) ? next.delete(label) : next.add(label);
      return next;
    });

  useT();   // Stream G4: re-render the menu labels when the language changes
  useSearchHighlight();
  const doLogout = async () => {
    try { await Promise.race([sessionApi.logout(), new Promise((r) => setTimeout(r, 1500))]); } catch { /* best effort */ }
    clearOfflineCaches(); clearQueue(); logout(); nav("/login");
  };
  const baseMenu = role === "superadmin" ? NAV.filter((g) => g.label === "Lexora Platform") : role === "employee" ? EMPLOYEE_NAV : NAV;
  // 2026-10-24 Roles & Permissions: + the screens a custom role opens (verified grant; the server enforces)
  const menu = role === "superadmin" ? baseMenu : menuWithGrantedAccess(baseMenu, NAV, user);
  const initials = ((user?.first_name?.[0] || "") + (user?.last_name?.[0] || "")).toUpperCase()
    || user?.email?.[0]?.toUpperCase() || "U";

  return (
    <div className="flex h-screen overflow-hidden bg-surface print:block print:h-auto">
      {/* Drawer scrim (below lg) */}
      {sidebarOpen && (
        <div className="fixed inset-0 z-[44] bg-black/40 backdrop-blur-[1px] lg:hidden"
          onClick={() => setSidebarOpen(false)} aria-hidden="true" />
      )}

      <aside className={clsx(
        "lx-app-chrome flex flex-col bg-white border-r border-surface-border flex-shrink-0",
        "fixed inset-y-0 left-0 z-[45] w-[17rem] max-w-[85vw] shadow-2xl transition-transform duration-200",
        sidebarOpen ? "translate-x-0" : "-translate-x-full",
        "lg:static lg:z-20 lg:shadow-none lg:translate-x-0 lg:max-w-none lg:transition-all",
        sidebarOpen ? "lg:w-60" : "lg:w-16",
      )}>
        {/* Brand */}
        <div className={clsx("flex items-center gap-2.5 border-b border-surface-border shrink-0",
          sidebarOpen ? "px-3 py-2.5 h-16" : "px-2 py-3 h-16")}>
          {sidebarOpen ? <Logo className="h-9 w-auto" /> : <Monogram className="w-9 h-9 mx-auto" />}
          <button onClick={() => setSidebarOpen(false)}
            className="lg:hidden ml-auto -mr-1 p-2 rounded-lg text-gray-400 hover:text-gray-700 hover:bg-gray-50 shrink-0">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Nav */}
        <nav className="flex-1 min-h-0 overflow-y-auto py-3 px-2 space-y-4 scrollbar-thin">
          {/* The platform operator (superadmin) is above the org layer — it sees
              ONLY the Lexora Platform console, not any tenant-operational nav. */}
          {menu.map((group) => {
            // Subscription gate: hide a group whose module the org didn't buy.
            const modules = user?.modules;
            if (group.module && modules && !modules.includes(group.module)) return null;
            const visible = group.items.filter((i) => navItemAllowed(i, user) && !(i.icOnly && !icAccess.data?.ic_member));
            if (!visible.length) return null;
            const isCollapsed = sidebarOpen && collapsedGroups.has(group.label);
            return (
              <div key={group.label}>
                {sidebarOpen && (
                  <button type="button" onClick={() => toggleGroup(group.label)}
                    className="w-full flex items-center justify-between px-2 mb-1 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-gray-400 hover:text-gray-600 transition-colors">
                    <span>{tx(`nav.${group.label}`, group.label)}</span>
                    <ChevronDown className={clsx("w-3 h-3 transition-transform duration-150", isCollapsed && "-rotate-90")} />
                  </button>
                )}
                <div className={clsx("space-y-0.5 overflow-hidden transition-[max-height] duration-150",
                  isCollapsed ? "max-h-0" : "max-h-[2000px]")}>
                  {visible.map((item) => {
                    const active = navActive(item.to, location.pathname, location.search, item.exact);
                    return (
                    <NavLink key={item.to} to={item.to} end={item.exact}
                      onClick={() => { if (window.innerWidth < 1024) setSidebarOpen(false); }}
                      title={!sidebarOpen ? item.label : undefined}
                      className={clsx(
                        "flex items-center gap-2.5 px-2.5 py-1.5 rounded-lg text-xs font-medium transition-all duration-100",
                        !sidebarOpen && "justify-center",
                        active ? "bg-primary-50 text-primary-600" : "text-gray-500 hover:bg-gray-50 hover:text-gray-700",
                      )}>
                      <item.icon className="w-3.5 h-3.5 flex-shrink-0" />
                      {sidebarOpen && (
                        <span className="flex-1 flex items-center gap-1.5 min-w-0">
                          <span className="truncate">{tx(`nav.${item.label}`, item.label)}</span>
                          {item.ai && (
                            <span className="text-[8.5px] font-bold uppercase tracking-widest px-1 py-0.5 rounded bg-gradient-to-r from-primary-500 to-teal-500 text-white shadow-sm shrink-0">AI</span>
                          )}
                        </span>
                      )}
                    </NavLink>
                  ); })}
                </div>
              </div>
            );
          })}
        </nav>

        {/* Footer — user */}
        <div className="p-3 border-t border-surface-border">
          <div className={clsx("flex items-center gap-2.5", !sidebarOpen && "justify-center")}>
            <div className="w-8 h-8 rounded-full bg-gradient-to-br from-primary-400 to-teal-400 flex items-center justify-center text-white text-xs font-semibold flex-shrink-0">
              {initials}
            </div>
            {sidebarOpen && (
              <>
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-medium text-gray-700 truncate">{user?.first_name} {user?.last_name}</p>
                  <p className="text-[10px] text-gray-400">{ROLE_LABEL[role] || role}</p>
                </div>
                <button onClick={doLogout} title="Log out" className="text-gray-400 hover:text-red-500 transition-colors">
                  <LogOut className="w-3.5 h-3.5" />
                </button>
              </>
            )}
          </div>
        </div>
      </aside>

      {/* Main column */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden print:block">
        <header className="lx-app-chrome min-h-11 sm:h-11 bg-white border-b border-surface-border flex items-center gap-1.5 sm:gap-3 px-2 sm:px-4 flex-shrink-0">
          <button onClick={() => setSidebarOpen(!sidebarOpen)} aria-label="Toggle menu"
            className="p-1.5 -ml-1 rounded-lg shrink-0 text-gray-400 hover:text-gray-600 hover:bg-gray-50">
            {sidebarOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
          </button>
          <div className="min-w-0 shrink md:max-w-[14rem]">
            <div className="text-sm font-semibold text-gray-800 truncate leading-tight">{user?.org_name || "Lexora HRM"}</div>
            <div className="text-[11px] text-gray-400 leading-tight">{user?.province ? `${user.province} · ` : ""}Canada</div>
          </div>

          {/* Global search: a full-width box on desktop, an icon (full-screen sheet) on phones. It reads the
              same menu arrays as the sidebar, so it only offers pages + tabs this role / plan can open. */}
          <div className="flex-1 min-w-0 flex justify-end md:justify-center md:px-2">
            <CommandPalette nav={role === "employee" ? EMPLOYEE_NAV : NAV} />
          </div>
          <div className="flex items-center gap-1.5 sm:gap-3 shrink-0">
            {role !== "superadmin" && <EntityPicker />}
            <BranchSwitcher />
            {role !== "superadmin" && <LanguagePicker compact />}
            {role !== "superadmin" && <NotificationBell />}
            {/* MEFI-style AI copilot toggle */}
            <button onClick={() => setChatOpen((o) => !o)} title="Ask Lexi — your HR copilot"
              className={clsx(
                "flex items-center gap-2 pl-1.5 pr-2 xs:pr-3 py-1 rounded-full text-xs font-semibold transition-all border shrink-0",
                chatOpen
                  ? "bg-primary-600 border-primary-600 text-white shadow-sm"
                  : "bg-gradient-to-r from-primary-50 to-teal-50 border-primary-200 text-primary-700 hover:from-primary-100 hover:to-teal-100",
              )}>
              <span className="w-5 h-5 rounded-full bg-primary-950 flex items-center justify-center shrink-0 overflow-hidden"><Monogram className="w-[18px] h-[18px]" /></span>
              <span className="hidden xs:inline">Ask Lexi</span>
            </button>
            <button onClick={toggleDark} title="Toggle theme"
              className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-50 shrink-0">
              {dark ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
            </button>
            <div className="hidden sm:flex items-center gap-2 shrink-0">
              <div className="text-right leading-tight">
                <div className="text-xs font-semibold text-gray-700">{user?.first_name} {user?.last_name}</div>
                <div className="text-[10px] text-gray-400">{ROLE_LABEL[role] || role}</div>
              </div>
              <div className="w-7 h-7 rounded-full bg-gradient-to-br from-primary-400 to-teal-400 flex items-center justify-center text-white text-[11px] font-semibold">
                {initials}
              </div>
            </div>
          </div>
        </header>

        <div className="lx-app-chrome"><SubscriptionBanner /></div>
        <main className="lx-app-main flex-1 overflow-y-auto p-4 md:p-6 print:p-0 print:overflow-visible">
          <Outlet />
        </main>
      </div>

      <div className="lx-app-chrome"><Chatbot open={chatOpen} onClose={() => setChatOpen(false)} /></div>
    </div>
  );
}
