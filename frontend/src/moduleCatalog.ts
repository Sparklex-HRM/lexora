import {
  UserMinus, Network, FolderOpen, Receipt, FileText, ClipboardList, Landmark, Scale, FileSpreadsheet,
  Clock, CalendarClock, CalendarCheck, Timer, CalendarDays, Sun, HeartPulse, ShieldPlus, PiggyBank, Building,
  Briefcase, Megaphone, Target, TrendingUp, GraduationCap, Scale3d, GitBranch, CheckSquare, ListTodo,
  ShieldAlert, HardHat, BookOpen, ScrollText, History, LineChart, UserCircle, Wallet, CalendarRange,
  Inbox, Settings, MapPin, Plug, Building2, Users2, Handshake, FolderKanban, Store, ReceiptText,
  FileSignature, LucideIcon,
} from "lucide-react";

export interface ModuleDef { title: string; icon: LucideIcon; phase: string; blurb: string; points: string[]; }

// ADP-class HRM/payroll feature map. Every entry is reachable in the nav today
// as a roadmap page; each becomes a real module in the phase shown.
export const MODULES: Record<string, ModuleDef> = {
  // People / HR
  offboarding: { title: "Offboarding", icon: UserMinus, phase: "Phase 1", blurb: "Structured separations & final pay.",
    points: ["Separation checklist (equipment, access revocation, exit interview)", "Final pay + accrued vacation payout into payroll", "Auto-generate the ROE within the 5-day CRA window"] },
  "org-chart": { title: "Org Chart", icon: Network, phase: "Phase 2", blurb: "Reporting structure, live from the directory.",
    points: ["Interactive tree from manager relationships", "Span-of-control & vacancy highlighting", "Drag-to-reorg with change requests"] },
  documents: { title: "Documents", icon: FolderOpen, phase: "Phase 1", blurb: "Employee document vault + e-signature.",
    points: ["Per-employee vault (contracts, TD1s, void cheques)", "E-sign offer letters & policy acknowledgements", "Retention rules + expiry reminders (permits, certifications)"] },

  // Payroll extras
  "off-cycle": { title: "Off-Cycle Pay", icon: Receipt, phase: "Phase 3", blurb: "Bonuses, corrections & manual cheques.",
    points: ["One-off runs outside the regular calendar", "Gross-up for net-target bonuses", "Void & reissue with YTD reversal"] },
  "tax-forms": { title: "Tax Forms (T4 / T4A / RL-1)", icon: FileText, phase: "Phase 3", blurb: "Year-end slips from the YTD ledger.",
    points: ["T4 / T4A generation from the YTD accumulators", "Quebec RL-1 for QC employees", "CRA XML e-filing + employee copies"] },
  roe: { title: "Records of Employment", icon: ClipboardList, phase: "Phase 3", blurb: "ROE Web-ready separations.",
    points: ["Auto-drafted on termination with insurable hours/earnings", "ROE Web XML export", "5-day deadline tracking (see AI: ROE Deadline agent)"] },
  remittances: { title: "CRA Remittances", icon: Landmark, phase: "Phase 3", blurb: "Source-deduction remittance schedule.",
    points: ["Per-run remittance (tax + CPP ee+er + EI ee+1.4×er)", "Remitter-frequency due-date calendar", "PD7A-ready summaries"] },
  garnishments: { title: "Garnishments", icon: Scale, phase: "Phase 3", blurb: "Court-ordered & support deductions.",
    points: ["Family-support & CRA requirements-to-pay", "Provincial exemption rules & priority ordering", "Remit-to third-party tracking"] },
  "gl-export": { title: "GL Export", icon: FileSpreadsheet, phase: "Phase 3", blurb: "Post payroll to your accounting system.",
    points: ["Configurable GL mapping by cost centre", "QuickBooks / Xero / CSV export", "Accrual vs cash journal entries"] },

  // Time & attendance
  timesheets: { title: "Timesheets", icon: Clock, phase: "Live", blurb: "Generate, approve & lock worked hours.",
    points: ["Generated from attendance → REG/OT/STAT split", "Manager approval → feeds the pay run as entries", "Provincial overtime (interim rule; Phase 3 = full ESA)"] },
  scheduling: { title: "Scheduling", icon: CalendarClock, phase: "Live", blurb: "Build & publish the weekly shift roster.",
    points: ["Weekly roster grid — assign & edit shifts per employee/day", "Publish to employees; a shift overrides the default pattern", "Planned vs published states + scheduled-hours totals"] },
  "time-clock": { title: "Time Clock", icon: Timer, phase: "Phase 2", blurb: "Clock in/out (web, kiosk, geo).",
    points: ["Web / kiosk / mobile punch with geofencing", "Break & meal-rule enforcement", "Real-time who's-in board"] },

  // Leave & absence
  "absence-calendar": { title: "Absence Calendar", icon: CalendarDays, phase: "Phase 2", blurb: "Team-wide time-off visibility.",
    points: ["Company & team calendars", "Conflict detection on approvals", "iCal / Outlook sync"] },
  "stat-holidays": { title: "Statutory Holidays", icon: Sun, phase: "Phase 2", blurb: "Provincial stat-holiday engine.",
    points: ["Per-province stat calendar", "Stat-pay eligibility & averaging", "Auto stat-pay into payroll"] },

  // Benefits
  benefits: { title: "Benefits Admin", icon: HeartPulse, phase: "Phase 4", blurb: "Group benefits administration.",
    points: ["Plan design (health/dental/life/LTD)", "Eligibility & waiting periods", "Payroll deduction sync + carrier feeds"] },
  enrollment: { title: "Benefits Enrollment", icon: ShieldPlus, phase: "Phase 4", blurb: "Open enrollment & life events.",
    points: ["Guided enrollment wizard", "Qualifying life-event changes", "Dependent & beneficiary management"] },
  retirement: { title: "Retirement / RRSP", icon: PiggyBank, phase: "Phase 4", blurb: "Group RRSP / pension.",
    points: ["Employee & employer match rules", "Contribution limits (RRSP room)", "Provider remittance files"] },
  carriers: { title: "Carriers", icon: Building, phase: "Phase 4", blurb: "Insurance carrier connections.",
    points: ["Carrier directory & policy numbers", "EDI 834 enrollment feeds", "Premium reconciliation"] },

  // Talent
  recruiting: { title: "Recruiting (ATS)", icon: Briefcase, phase: "Phase 4", blurb: "Applicant tracking.",
    points: ["Requisitions & pipelines", "Candidate scorecards & interviews", "Offer → auto-onboarding handoff"] },
  "job-postings": { title: "Job Postings", icon: Megaphone, phase: "Phase 4", blurb: "Careers site & job boards.",
    points: ["Branded careers page", "Indeed / LinkedIn syndication", "Application forms & screening"] },
  performance: { title: "Performance", icon: Target, phase: "Phase 4", blurb: "Reviews & 1:1s.",
    points: ["Review cycles & templates", "360 feedback", "Calibration & ratings"] },
  goals: { title: "Goals (OKRs)", icon: TrendingUp, phase: "Phase 4", blurb: "Goal setting & tracking.",
    points: ["Cascading company → team → individual goals", "Progress check-ins", "Ties into performance reviews"] },
  learning: { title: "Learning (LMS)", icon: GraduationCap, phase: "Phase 4", blurb: "Training & certifications.",
    points: ["Course catalog & assignments", "Compliance training tracking", "Certification expiry reminders"] },
  "comp-planning": { title: "Compensation Planning", icon: Scale3d, phase: "Phase 4", blurb: "Merit & pay-band planning.",
    points: ["Pay bands & ranges", "Merit / bonus cycle worksheets", "Budget approval workflow"] },
  succession: { title: "Succession", icon: GitBranch, phase: "Phase 5", blurb: "Talent & succession planning.",
    points: ["9-box talent grids", "Successor readiness", "Flight-risk indicators"] },

  // Workflow
  tasks: { title: "Tasks", icon: ListTodo, phase: "Phase 1", blurb: "Assigned work across HR.",
    points: ["Onboarding/offboarding task lists", "Due dates & reminders", "Cross-module task inbox"] },

  // Compliance
  "compliance-center": { title: "Compliance Center", icon: ShieldAlert, phase: "Phase 3", blurb: "Employment-standards compliance.",
    points: ["Per-province ESA rules (min wage, OT, breaks)", "Pay-equity & reporting", "Compliance alerts"] },
  wcb: { title: "WCB / WSIB", icon: HardHat, phase: "Phase 3", blurb: "Workers' comp administration.",
    points: ["Provincial board rates & classifications", "Assessable-earnings reporting", "Claims tracking"] },
  safety: { title: "Health & Safety", icon: ShieldPlus, phase: "Phase 5", blurb: "OHS incidents & training.",
    points: ["Incident reporting", "JHSC minutes", "Safety training compliance"] },
  policies: { title: "Policies", icon: BookOpen, phase: "Phase 2", blurb: "Policy library & acknowledgements.",
    points: ["Versioned policy library", "E-sign acknowledgements", "Renewal reminders"] },
  "audit-log": { title: "Audit Log", icon: History, phase: "Phase 2", blurb: "Tamper-evident activity trail.",
    points: ["Who changed what, when", "PHI/PII access logging", "Exportable for auditors"] },

  // Insights
  benchmarking: { title: "Benchmarking", icon: LineChart, phase: "Phase 5", blurb: "Market pay & turnover benchmarks.",
    points: ["Salary benchmarks by role/region", "Turnover vs industry", "Cost-per-hire trends"] },

  // Self-service (ESS)
  "my-profile": { title: "My Profile", icon: UserCircle, phase: "Phase 1", blurb: "Your details & documents.",
    points: ["Update contact / banking / TD1", "View documents & policies", "Manage dependents"] },
  "my-pay": { title: "My Pay", icon: Wallet, phase: "Phase 1", blurb: "Your pay stubs & tax slips (T4 / RL-1).",
    points: ["Pay stub history & YTD", "T4 / RL-1 download", "Direct-deposit management"] },
  "my-time": { title: "My Time", icon: CalendarRange, phase: "Phase 2", blurb: "Your time & schedule.",
    points: ["Clock in/out & timesheet", "View schedule & swap shifts", "See balances"] },
  "my-benefits": { title: "My Benefits", icon: HeartPulse, phase: "Phase 4", blurb: "Your coverage & enrollment.",
    points: ["Current elections & dependents", "Open-enrollment changes", "Digital benefit cards"] },
  "my-requests": { title: "My Requests", icon: Inbox, phase: "Phase 1", blurb: "Time off & change requests.",
    points: ["Request leave & track approval", "Update-info requests", "Notifications"] },

  // Setup
  "company-settings": { title: "Company Settings", icon: Settings, phase: "Phase 1", blurb: "Org profile & payroll config.",
    points: ["Legal entity, CRA/RQ accounts", "Pay calendars & remitter frequency", "Branding & letterhead"] },
  branches: { title: "Locations & Branches", icon: MapPin, phase: "Phase 1", blurb: "Multi-branch org structure.",
    points: ["Org → branch hierarchy with addresses & provinces", "Branch-scoped employees, payroll & reporting", "All-branches roll-up + a header branch switcher"] },
  integrations: { title: "Integrations", icon: Plug, phase: "Phase 3", blurb: "Connect your stack.",
    points: ["Accounting (QuickBooks/Xero), SSO, HRIS", "Benefit-carrier & bank feeds", "Public API & webhooks"] },

  // Staffing / PSA (agency & project-based workforce)
  roster: { title: "Roster", icon: Users2, phase: "Phase 2", blurb: "Deploy the workforce to clients & sites.",
    points: ["Assign employees to clients / projects / sites for a date range", "Bill-rate vs pay-rate margin per placement", "Roster → timesheet → invoice + payroll flow"] },
  clients: { title: "Client Management", icon: Handshake, phase: "Phase 2", blurb: "Customers you place workers with.",
    points: ["Client records, sites, billing contacts & rate cards", "Per-client PO / credit limit / payment terms", "Client-scoped roster, timesheets & invoices"] },
  projects: { title: "Project Management", icon: FolderKanban, phase: "Phase 2", blurb: "Projects, budgets & staffing.",
    points: ["Projects under a client with budget & milestones", "Staff allocation & utilization", "Cost (payroll) vs billed (invoice) profitability"] },
  vendors: { title: "Vendor Management", icon: Store, phase: "Phase 3", blurb: "Subcontractors & suppliers.",
    points: ["Vendor / subcontractor records & compliance docs (WSIB, insurance)", "Subcontractor timesheets & payables", "1099/T4A-NR contractor tracking"] },
  invoicing: { title: "Invoicing", icon: ReceiptText, phase: "Phase 3", blurb: "Bill clients from approved time.",
    points: ["Auto-draft invoices from approved roster timesheets × bill rate", "GST/HST/PST by province + payment terms", "AR aging, statements & GL export"] },
  msa: { title: "Contracts & MSA", icon: FileSignature, phase: "Phase 3", blurb: "Master service agreements & SOWs.",
    points: ["MSA / SOW library with e-sign & versions", "Rate schedules & renewal reminders", "Ties clients ↔ rate cards ↔ invoicing"] },
};

export const moduleGroupIcon = Building2; // fallback
