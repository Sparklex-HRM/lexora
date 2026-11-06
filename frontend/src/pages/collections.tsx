import {
  Briefcase, Megaphone, Target, TrendingUp, GraduationCap, Scale3d, GitBranch,
  FileSignature, HardHat, ShieldPlus, ShieldAlert, LineChart, Clock, CalendarClock,
  Timer, CalendarDays, History, FileSpreadsheet, Plug, UserMinus,
} from "lucide-react";
import CollectionPage, { CollectionConfig } from "../components/CollectionPage";

const page = (cfg: CollectionConfig) => () => <CollectionPage cfg={cfg} />;

// ── People ──────────────────────────────────────────────────────────────────
export const OffboardingPage = page({
  title: "Offboarding", icon: UserMinus, storageKey: "lx-offboarding", addLabel: "Start offboarding",
  subtitle: "Structured separations, clearance and final pay",
  stages: ["Initiated", "Clearance", "Final pay", "ROE issued", "Complete"],
  fields: [
    { k: "employee", label: "Employee", required: true },
    { k: "reason", label: "Reason", type: "select", options: ["Resignation", "Retirement", "End of contract", "Dismissal", "Layoff"] },
    { k: "last_day", label: "Last day worked", type: "date" },
    { k: "notes", label: "Notes", type: "textarea" },
  ],
  seed: [
    { employee: "Jordan Lee", reason: "Resignation", last_day: "2026-08-14", stage: "ROE issued" },
    { employee: "Priya Sharma", reason: "End of contract", last_day: "2026-09-30", stage: "Clearance" },
  ],
  note: "When a separation reaches 'Final pay', issue the ROE from Payroll → Year-end → Records of Employment.",
});

// ── Time & Attendance ───────────────────────────────────────────────────────
export const TimesheetsPage = page({
  title: "Timesheets", icon: Clock, storageKey: "lx-timesheets", addLabel: "New timesheet",
  subtitle: "Captured worked hours pending approval",
  fields: [
    { k: "employee", label: "Employee", required: true },
    { k: "week_of", label: "Week of", type: "date" },
    { k: "regular_hours", label: "Regular hrs", type: "number" },
    { k: "ot_hours", label: "OT hrs", type: "number" },
    { k: "status", label: "Status", type: "select", options: ["Draft", "Submitted", "Approved", "Rejected"] },
  ],
  seed: [
    { employee: "Aisha Khan", week_of: "2026-08-10", regular_hours: 80, ot_hours: 4, status: "Submitted" },
    { employee: "Sam Nguyen", week_of: "2026-08-10", regular_hours: 75, ot_hours: 0, status: "Approved" },
  ],
  note: "Approved timesheets feed hourly pay and overtime into the pay run.",
});

export const SchedulingPage = page({
  title: "Scheduling", icon: CalendarClock, storageKey: "lx-scheduling", addLabel: "Add shift",
  subtitle: "Build and publish shift schedules",
  fields: [
    { k: "employee", label: "Employee", required: true },
    { k: "date", label: "Date", type: "date" },
    { k: "shift", label: "Shift", type: "select", options: ["Day (9–5)", "Morning (6–2)", "Evening (2–10)", "Night (10–6)"] },
    { k: "location", label: "Location" },
    { k: "hours", label: "Hours", type: "number" },
  ],
  seed: [
    { employee: "Fatima Ali", date: "2026-08-24", shift: "Day (9–5)", location: "Calgary Office", hours: 8 },
    { employee: "Aisha Khan", date: "2026-08-24", shift: "Evening (2–10)", location: "Toronto HQ", hours: 8 },
  ],
});

// ── Talent ──────────────────────────────────────────────────────────────────
// Recruiting, Job Postings, Performance, Goals and Compensation Planning were mock screens here.
// They are real, data-backed pages now: pages/hiring/* and pages/performance/*.

export const LearningPage = page({
  title: "Learning (LMS)", icon: GraduationCap, storageKey: "lx-learning", addLabel: "Add course",
  subtitle: "Training courses and certifications",
  fields: [
    { k: "course", label: "Course", required: true },
    { k: "provider", label: "Provider" },
    { k: "category", label: "Category", type: "select", options: ["Compliance", "Leadership", "Technical", "Safety", "Onboarding"] },
    { k: "hours", label: "Hours", type: "number" },
    { k: "status", label: "Status", type: "select", options: ["Active", "Archived"] },
  ],
  seed: [
    { course: "AODA Accessibility Training", provider: "Internal", category: "Compliance", hours: 1, status: "Active" },
    { course: "Workplace Health & Safety", provider: "Internal", category: "Safety", hours: 2, status: "Active" },
  ],
});

export const SuccessionPage = page({
  title: "Succession", icon: GitBranch, storageKey: "lx-succession", addLabel: "Add plan",
  subtitle: "Key-role succession planning",
  stages: ["Identified", "Developing", "Ready 1–2y", "Ready now"],
  fields: [
    { k: "role", label: "Key role", required: true },
    { k: "incumbent", label: "Incumbent" },
    { k: "successor", label: "Successor" },
    { k: "readiness", label: "Notes", type: "textarea" },
  ],
  seed: [
    { role: "Director, Engineering", incumbent: "Wei Zhang", successor: "Aisha Khan", stage: "Developing" },
    { role: "Payroll Lead", incumbent: "—", successor: "Priya Sharma", stage: "Ready now" },
  ],
});

// ── Staffing & Ops ──────────────────────────────────────────────────────────
export const MsaPage = page({
  title: "Contracts & MSA", icon: FileSignature, storageKey: "lx-msa", addLabel: "Add contract",
  subtitle: "Master service agreements and SOWs",
  fields: [
    { k: "party", label: "Client / party", required: true },
    { k: "type", label: "Type", type: "select", options: ["MSA", "SOW", "NDA", "Amendment"] },
    { k: "value", label: "Value", type: "money" },
    { k: "start", label: "Start", type: "date" },
    { k: "end", label: "End", type: "date" },
    { k: "status", label: "Status", type: "select", options: ["Draft", "Active", "Expired", "Terminated"] },
  ],
  seed: [{ party: "Northwind Retail", type: "MSA", value: 240000, start: "2026-01-01", end: "2026-12-31", status: "Active" }],
});

// ── Compliance ──────────────────────────────────────────────────────────────
export const ComplianceCenterPage = page({
  title: "Compliance Center", icon: ShieldAlert, storageKey: "lx-compliance-center", addLabel: "Add requirement",
  subtitle: "Employment-standards compliance tracker",
  fields: [
    { k: "requirement", label: "Requirement", required: true },
    { k: "jurisdiction", label: "Jurisdiction", type: "select", options: ["Federal", "ON", "BC", "AB", "QC"] },
    { k: "category", label: "Category", type: "select", options: ["ESA", "Health & Safety", "Pay Equity", "Privacy", "Tax"] },
    { k: "due_date", label: "Due date", type: "date" },
    { k: "status", label: "Status", type: "select", options: ["On track", "Due soon", "Overdue", "Complete"] },
  ],
  seed: [
    { requirement: "AODA multi-year accessibility plan", jurisdiction: "ON", category: "ESA", due_date: "2026-12-31", status: "On track" },
    { requirement: "Pay Equity Act posting", jurisdiction: "Federal", category: "Pay Equity", due_date: "2026-09-03", status: "Due soon" },
  ],
});

export const WcbPage = page({
  title: "WCB / WSIB", icon: HardHat, storageKey: "lx-wcb", addLabel: "Add claim",
  subtitle: "Workers' compensation claims",
  fields: [
    { k: "claim", label: "Claim", required: true },
    { k: "employee", label: "Employee" },
    { k: "incident_date", label: "Incident date", type: "date" },
    { k: "status", label: "Status", type: "select", options: ["Reported", "Under review", "Approved", "Closed"] },
    { k: "cost", label: "Cost", type: "money" },
  ],
  seed: [{ claim: "WSIB-2026-014", employee: "Fatima Ali", incident_date: "2026-07-12", status: "Approved", cost: 3200 }],
});

export const SafetyPage = page({
  title: "Health & Safety", icon: ShieldPlus, storageKey: "lx-safety", addLabel: "Log incident",
  subtitle: "OHS incidents and training",
  fields: [
    { k: "incident", label: "Incident", required: true },
    { k: "location", label: "Location" },
    { k: "severity", label: "Severity", type: "select", options: ["Near miss", "Minor", "Serious", "Critical"] },
    { k: "date", label: "Date", type: "date" },
    { k: "status", label: "Status", type: "select", options: ["Open", "Investigating", "Closed"] },
  ],
  seed: [{ incident: "Slip in corridor", location: "Toronto HQ", severity: "Minor", date: "2026-08-05", status: "Closed" }],
});

export const AuditLogPage = page({
  title: "Audit Log", icon: History, storageKey: "lx-audit", addLabel: "Add entry",
  subtitle: "Activity trail across the HRM",
  fields: [
    { k: "event", label: "Event", required: true },
    { k: "actor", label: "Actor" },
    { k: "entity", label: "Entity" },
    { k: "timestamp", label: "When", type: "date" },
    { k: "detail", label: "Detail", type: "textarea" },
  ],
  seed: [
    { event: "Pay run committed", actor: "payroll_admin", entity: "PayRun", timestamp: "2026-08-21", detail: "Biweekly, 4 employees" },
    { event: "Employee updated", actor: "hr_admin", entity: "Employee", timestamp: "2026-08-20", detail: "Priya Sharma — TD1 claim" },
  ],
  note: "Tamper-evident, server-side audit is a backend follow-up; this view is a functional placeholder.",
});

// ── Insights ────────────────────────────────────────────────────────────────
export const BenchmarkingPage = page({
  title: "Benchmarking", icon: LineChart, storageKey: "lx-benchmarking", addLabel: "Add benchmark",
  subtitle: "Market pay and turnover benchmarks",
  fields: [
    { k: "role", label: "Role", required: true },
    { k: "market", label: "Market" },
    { k: "market_median", label: "Market median", type: "money" },
    { k: "our_median", label: "Our median", type: "money" },
    { k: "source", label: "Source" },
  ],
  seed: [
    { role: "Senior Specialist", market: "Toronto", market_median: 102000, our_median: 100000, source: "Mercer 2026" },
    { role: "Manager", market: "Vancouver", market_median: 135000, our_median: 130000, source: "Radford 2026" },
  ],
});

// ── Payroll ─────────────────────────────────────────────────────────────────
export const GlExportPage = page({
  title: "GL Export", icon: FileSpreadsheet, storageKey: "lx-glexport", addLabel: "New export",
  subtitle: "Post payroll to your accounting system",
  fields: [
    { k: "period", label: "Pay period", required: true },
    { k: "gl_date", label: "GL date", type: "date" },
    { k: "debit_total", label: "Debit total", type: "money" },
    { k: "credit_total", label: "Credit total", type: "money" },
    { k: "status", label: "Status", type: "select", options: ["Pending", "Exported", "Posted"] },
  ],
  seed: [{ period: "2026-08-01 → 08-14", gl_date: "2026-08-21", debit_total: 15078.83, credit_total: 15078.83, status: "Exported" }],
  note: "Amounts roll up from the payroll register's by-department GL breakdown (Pay run → Register).",
});

// ── Setup ───────────────────────────────────────────────────────────────────
export const IntegrationsPage = page({
  title: "Integrations", icon: Plug, storageKey: "lx-integrations", addLabel: "Add integration",
  subtitle: "Connect your accounting, benefits and identity stack",
  fields: [
    { k: "integration", label: "Integration", required: true },
    { k: "category", label: "Category", type: "select", options: ["Accounting", "Benefits", "Identity/SSO", "Banking", "Time"] },
    { k: "status", label: "Status", type: "select", options: ["Connected", "Available", "Error"] },
    { k: "last_sync", label: "Last sync", type: "date" },
  ],
  seed: [
    { integration: "QuickBooks Online", category: "Accounting", status: "Available" },
    { integration: "Sun Life", category: "Benefits", status: "Available" },
    { integration: "Okta SSO", category: "Identity/SSO", status: "Available" },
  ],
});
