/** Global search — the NAVIGATION INDEX (2026-10-20, spec project_global_search).
 *
 *  ONE registry, keyed by the sidebar menu's `to` (components/DashboardLayout.tsx NAV / EMPLOYEE_NAV
 *  ). Every menu route MUST have an entry here — scripts/check_search_index.py
 *  fails the build check otherwise, and dev builds log the missing routes in the console.
 *
 *  An entry adds, to the menu page itself:
 *    kw    search words / synonyms for the page ("salary slip", "T4", "ROE"…)
 *    subs  every tab / section / sub-tab inside the page, each with a deep link that opens it
 *          (`?tab=` etc. — the pages read the same params) and optional extra role gate.
 *  A sub is shown only when its menu page is visible to the user (same role / SKU gate
 *  as the menu) AND its own `roles` allow it.
 *
 *  EXTRA_PAGES: screens reachable from inside pages but not in the menu (settings editors, hub
 *  cards…), gated by their own roles / module exactly like their route.
 *
 *  New screen? Add its menu item, then one entry here (plus its tabs). That is all search needs. */
import {
  ADMIN_ROLES, HR_WRITE, PAYROLL_ACCESS, APPROVER_ROLES, ALL_STAFF,
} from "../store/authStore";

export interface SearchSub { to: string; label: string; kw?: string[]; roles?: string[] }
export interface SearchPage { kw?: string[]; subs?: SearchSub[] }
export interface ExtraPage extends SearchSub { group: string; parent?: string; module?: string }

const HR = HR_WRITE;
const HRP = [...HR_WRITE, "payroll_admin"];
const FILING = [...ADMIN_ROLES, "payroll_admin"];
const HR_ONLY = ["superadmin", "org_admin", "hr_admin", "hr_manager"];
const AGENT = [...HR_ONLY, "payroll_admin"];
const OFFICE = ["superadmin", "org_admin", "hr_admin", "hr_manager"];

export const PAGE_INDEX: Record<string, SearchPage> = {
  // ── Platform (superadmin) ──
  "/platform": { kw: ["tenants", "organisations", "orgs", "provision tenant", "modules"] },
  "/platform/revenue": { kw: ["mrr", "tenants by plan", "revenue by tenant"] },
  "/platform/pricing": { kw: ["plans", "per employee per month", "module availability"] },

  // ── Overview ──
  "/": { kw: ["home", "dashboard", "needs your attention", "my week", "quick access"] },
  "/command-center": { kw: ["needs action", "live activity", "headcount", "monthly cost"] },

  // ── People ──
  "/employees": { kw: ["people", "staff", "directory", "employee list", "add employee", "new joiners", "probation", "left"],
    subs: [{ to: "/employees?new=1", label: "Add employee", kw: ["new employee", "hire", "create employee"], roles: HR }] },
  "/onboarding": { kw: ["new hire", "joiner", "invite", "joining checklist", "onboard"] },
  "/career-changes": { kw: ["promotion", "promote", "transfer", "designation change", "job change"] },
  "/probation": { kw: ["confirmation", "probation due", "probation rules", "extend probation"] },
  "/fixed-term": { kw: ["fixed term", "fixed-term employee", "contract employee", "contract end date", "renew contract", "contract renewal", "convert to permanent", "make permanent", "contract expiry"] },
  "/letters": { kw: ["offer letter", "experience letter", "relieving letter", "issued letters", "letter templates"],
    subs: [
      { to: "/letters", label: "Issued letters", kw: ["letters issued"] },
      { to: "/letters?tab=templates", label: "Templates", kw: ["letter templates"] },
      { to: "/letters?tab=letterhead", label: "Letterhead and signatory", kw: ["letterhead", "signatory", "signature", "letter head"] },
    ] },
  "/employee-history": { kw: ["timeline", "salary history", "lifecycle history"] },
  "/offboarding": { kw: ["final pay", "record of employment", "roe", "final cheque", "separation"] },
  "/exits": { kw: ["resignation", "exit case", "separation", "notice period", "last working day", "lwd", "attrition"],
    subs: [
      { to: "/exits", label: "Exit cases", kw: ["cases"] },
      { to: "/exits?tab=attrition", label: "Attrition summary", kw: ["exits by month", "exits by reason", "attrition"] },
      { to: "/exits?tab=settings", label: "Exit settings", kw: ["notice policy", "rehire", "notice period rules"], roles: HR },
    ] },
  "/exits/team": { kw: ["team resignations", "my team exits"] },
  "/exits/former": { kw: ["ex employees", "alumni", "rehire eligible", "former staff"] },
  "/org-chart": { kw: ["reporting structure", "hierarchy", "org structure", "reporting line"] },
  "/ops/documents": { kw: ["document vault", "employee documents", "files"] },

  // ── Payroll ──
  "/payroll": { kw: ["run payroll", "salary run", "pay run", "process payroll", "monthly payroll", "salary processing"],
    subs: [
      { to: "/payroll?tab=runs", label: "Pay runs", kw: ["payroll history", "past runs", "pay register", "salary register"] },
      { to: "/payroll?tab=ytd", label: "Year-to-date (YTD)", kw: ["ytd ledger", "adjust ytd"] },
      { to: "/payroll?tab=payslips", label: "Pay stubs", kw: ["salary slip", "pay slip", "payslip", "pay stub"] },
      { to: "/payroll?tab=yearend", label: "Year-end", kw: ["year end", "annual"] },
      { to: "/payroll?tab=calc", label: "Calculator", kw: ["salary calculator", "per period paystub", "net pay calculator"] },
      { to: "/payroll?tab=yearend&ye=eht", label: "Employer Health Tax", kw: ["eht"] },
      { to: "/payroll?tab=yearend&ye=compliance", label: "Verification Center", kw: ["pdoc", "golden cases", "statutory constants"] },
    ] },
  "/payroll?tab=yearend&ye=t4": { kw: ["t4 slips", "statement of remuneration"] },
  "/payroll?tab=yearend&ye=roe": { kw: ["roe", "record of employment"] },
  "/payroll?tab=yearend&ye=remit": { kw: ["cra remittance", "remittance schedule", "source deductions"] },
  "/salary-revisions": { kw: ["increment", "hike", "raise", "appraisal hike", "salary increase", "revision"],
    subs: [
      { to: "/salary-revisions", label: "Revisions" },
      { to: "/salary-revisions?tab=batches", label: "Bulk batches", kw: ["bulk revision", "mass increment"] },
      { to: "/salary-revisions?tab=retro", label: "Retro pay", kw: ["retroactive pay", "back pay"] },
      { to: "/salary-revisions?tab=settings", label: "Approval settings", kw: ["revision approval"] },
    ] },
  "/garnishments": { kw: ["wage garnishment", "court order", "deduction order"] },
  "/payroll?mode=offcycle": { kw: ["off cycle", "bonus run", "one time payment", "manual cheque", "correction run"] },
  "/gl-export": { kw: ["general ledger", "gl", "journal export", "accounting export"] },
  "/payroll/canada/statutory-values": { kw: ["cpp", "ei", "ympe", "canadian rates"] },
  "/payroll/canada/remittances": { kw: ["pd7a", "rq", "revenu quebec", "remitter type"] },
  "/payroll/canada/stat-holiday": { kw: ["stat holiday pay", "public holiday pay"] },
  "/payroll/canada/accruals": { kw: ["wcb", "wsib", "eht", "employer accruals"],
    subs: [
      { to: "/payroll/canada/accruals", label: "Remittance summary" },
      { to: "/payroll/canada/accruals?tab=groups", label: "Rate groups & assignments" },
      { to: "/payroll/canada/accruals?tab=run", label: "Accruals per pay run" },
    ] },
  "/payroll/canada/contractors": { kw: ["t4a", "contractors canada"],
    subs: [
      { to: "/payroll/canada/contractors", label: "Contractors (Canada)" },
      { to: "/payroll/canada/contractors?tab=payments", label: "Contractor payments (Canada)" },
      { to: "/payroll/canada/contractors?tab=t4a", label: "T4A slips", kw: ["t4a"] },
    ] },
  "/payroll/canada/year-end": { kw: ["year end checklist", "roes outstanding"] },
  "/migration/canada": { kw: ["canada import", "migration canada"] },
  "/migration/parallel-run": { kw: ["parallel run", "proof", "old system register", "reconciliation"] },
  "/migration/checklist": { kw: ["load order", "migration steps"] },

  // ── Time & Attendance ──
  "/attendance": { kw: ["attendance", "who is in", "late", "absent", "punches", "my attendance", "regularization", "attendance correction"] },
  "/timesheets": { kw: ["timesheet", "hours", "approve timesheets"] },
  "/scheduling": { kw: ["shift roster", "shifts", "rota", "schedule"] },
  // G2 2026-10-23
  "/scheduling/rotations": { kw: ["rotation pattern", "shift rotation", "3 shift", "a b c shift", "4 on 2 off", "weekly off", "rotating weekly off", "crew", "generate roster", "shift swap approval"],
    subs: [
      { to: "/scheduling/rotations?tab=patterns", label: "Rotation patterns", kw: ["templates", "cycle", "preview calendar"] },
      { to: "/scheduling/rotations?tab=assign", label: "Assign people to a pattern", kw: ["crew", "stagger", "team rotation"] },
      { to: "/scheduling/rotations?tab=generate", label: "Generate roster weeks ahead", kw: ["auto roster", "minimum rest"] },
      { to: "/scheduling/rotations?tab=swaps", label: "Shift swap requests", kw: ["swap approval"] },
      { to: "/scheduling/rotations?tab=settings", label: "Rotation rules", kw: ["rest between shifts", "weeks ahead", "auto generate"], roles: HR_ONLY },
    ] },
  "/my-shifts": { kw: ["my roster", "my shift", "swap shift", "shift swap", "exchange shift"] },
  "/attendance-analytics": { kw: ["attendance report", "overtime trend", "hours by department"] },
  "/time-clock": { kw: ["punch in", "punch out", "clock in", "clock out"] },
  "/biometric-import": { kw: ["biometric", "device", "essl", "zkteco", "attendance machine"],
    subs: [
      { to: "/biometric-import", label: "Biometric import log" },
      { to: "/biometric-import?tab=devices", label: "Biometric devices & webhook" },
      { to: "/biometric-import?tab=map", label: "Biometric code map" },
      { to: "/biometric-import?tab=health", label: "Biometric device health & live push", kw: ["device offline", "adms", "iclock", "matrix cosec", "push", "clock drift", "device health"] },
    ] },

  // ── Leave ──
  "/leave": { kw: ["leave", "time off", "vacation", "holiday request", "apply leave", "leave balance", "pto"],
    subs: [
      { to: "/leave?tab=mine", label: "My leave & balances", kw: ["my balance", "leave balance"] },
      { to: "/leave?tab=approvals", label: "Leave approvals", kw: ["approve leave", "pending leave"], roles: APPROVER_ROLES },
      { to: "/leave?tab=overview", label: "All leave requests", roles: APPROVER_ROLES },
      { to: "/leave?apply=1", label: "Apply for leave", kw: ["request leave", "apply leave", "take leave"] },
    ] },
  "/absence-calendar": { kw: ["team calendar", "who is on leave", "absence"] },
  "/stat-holidays": { kw: ["holidays", "holiday list", "public holidays", "optional holidays", "statutory holidays"] },

  // ── Benefits ──
  "/benefits": { kw: ["benefit plans", "insurance plans", "benefits admin"] },
  "/enrollment": { kw: ["benefits enrollment", "enrolment"] },
  "/retirement": { kw: ["rrsp", "pension", "retirement plan"] },
  "/carriers": { kw: ["insurance carrier", "insurer"] },

  // ── Talent ──
  "/hiring/requisitions": { kw: ["requisition", "headcount request", "new position", "job opening", "mrf"],
    subs: [{ to: "/hiring/requisitions", label: "Raise requisition", kw: ["raise requisition", "new requisition"] }] },
  "/hiring/postings": { kw: ["job postings", "job ads", "careers page", "job boards", "linkedin", "naukri", "indeed", "job feed", "job description", "jd generator"] },
  "/hiring/candidates": { kw: ["candidates", "applicants", "pipeline", "resume", "cv", "recruitment", "ats", "resume parsing", "fit score", "match score", "candidate ranking"] },
  "/hiring/offers": { kw: ["offer", "offer letter", "salary offer"] },
  "/hiring/reports": { kw: ["hiring report", "time in stage", "source mix", "pipeline report"],
    subs: [
      { to: "/hiring/referrals", label: "Hiring — Referrals", kw: ["referral bonus", "employee referrals"], roles: HR },
      { to: "/hiring/scorecards", label: "Hiring — Scorecards", kw: ["interview scorecard"], roles: HR },
      { to: "/hiring/messages", label: "Hiring — Candidate messages", kw: ["candidate email"], roles: HR },
      { to: "/hiring/settings", label: "Hiring settings", kw: ["careers page settings", "interview calendars"], roles: HR },
    ] },
  "/hiring/my-interviews": { kw: ["interviews", "interview feedback", "scorecard"] },
  "/referrals": { kw: ["refer a friend", "referral", "open jobs"],
    subs: [{ to: "/referrals?tab=mine", label: "My referrals" }] },
  "/performance/goals": { kw: ["goals", "okr", "kra", "objectives", "targets"] },
  "/performance/reviews": { kw: ["review", "appraisal", "performance review", "self review", "feedback"] },
  // G4 2026-10-23
  "/performance/okrs": { kw: ["okr", "okrs", "objectives", "key results", "alignment", "company objectives", "confidence", "check-in"] },
  "/performance/feedback": { kw: ["360", "360 feedback", "peer feedback", "upward feedback", "multi-rater", "nominate reviewers"] },
  "/performance/one-on-ones": { kw: ["1:1", "one on one", "one-on-one", "1 on 1", "check-in meeting", "action items", "agenda"] },
  "/performance/improvement-plans": { kw: ["pip", "performance improvement plan", "improvement plan", "underperformance"] },
  "/performance/cycles": { kw: ["appraisal cycle", "increments", "calibration", "promotion recommendations", "review cycle"],
    subs: [{ to: "/performance/settings", label: "Performance settings", kw: ["rating scale", "competencies", "increment matrix"], roles: HR }] },
  "/comp/cycles": { kw: ["compensation cycle", "merit cycle", "salary review cycle", "merit matrix"] },
  "/comp/worksheets": { kw: ["comp worksheet", "manager worksheet", "merit worksheet"] },
  "/learning": { kw: ["learning", "courses", "training"] },
  "/succession": { kw: ["succession planning", "successor", "key roles"] },


  // ── Staffing & Ops ──
  "/ops/roster": { kw: ["roster", "staffing roster"] },
  "/ops/clients": { kw: ["clients", "client management"] },
  "/ops/projects": { kw: ["projects", "project management"] },
  "/ops/vendors": { kw: ["vendor management", "staffing vendors"] },
  "/ops/invoices": { kw: ["invoicing", "client invoices"] },
  "/psa": { kw: ["professional services", "billable hours", "utilisation", "bench"],
    subs: [
      { to: "/psa/rates", label: "PSA — Rate cards" },
      { to: "/psa/assignments", label: "PSA — Assignments & bench", kw: ["bench"] },
      { to: "/psa/hours", label: "PSA — Billable hours" },
      { to: "/psa/utilisation", label: "PSA — Utilisation", kw: ["utilization"] },
      { to: "/psa/invoices", label: "PSA — Invoices" },
      { to: "/psa/leakage", label: "PSA — Revenue leakage" },
      { to: "/psa/revenue", label: "PSA — Revenue & FX" },
    ] },
  "/msa": { kw: ["contracts", "msa", "sow", "nda"] },

  // ── Workflow ──
  "/approvals": { kw: ["approve", "pending approvals", "waiting for me", "inbox", "requests to approve"],
    subs: [
      { to: "/approvals?tab=mine", label: "My requests (approvals)", kw: ["my requests status"] },
      { to: "/approvals?tab=decided", label: "Decided by me" },
      { to: "/approvals?tab=delegation", label: "Out of office — delegate approvals", kw: ["delegate", "out of office", "vacation delegate"] },
      { to: "/approvals?tab=all", label: "All pending approvals", roles: OFFICE },
    ] },
  // G7 2026-10-23 (menu route — its roles come from the menu item)
  "/approvals/whatsapp": { kw: ["whatsapp approvals", "approve on whatsapp", "approve / reject from whatsapp", "whatsapp buttons", "mobile approval"] },
  "/notifications": { kw: ["notifications", "alerts", "inbox"],
    subs: [
      { to: "/notifications?tab=preferences", label: "Notification preferences", kw: ["email preferences", "mute", "whatsapp reminders", "agent reminders", "whatsapp opt-in"] },
      { to: "/notifications?tab=log", label: "E-mail delivery log", kw: ["email log", "delivery"], roles: ["superadmin", "org_admin"] },
      { to: "/notifications?tab=templates", label: "E-mail templates", kw: ["email templates"], roles: OFFICE },
    ] },
  "/raise-request": { kw: ["raise a request", "service request", "hr request", "work from home", "id card", "letter request"],
    subs: [
      { to: "/raise-request?tab=mine", label: "My raised requests" },
      { to: "/raise-request?tab=all", label: "All / team requests", roles: [...HR_ONLY, "manager"] },
    ] },
  "/request-types": { kw: ["request types", "request forms", "form fields"] },
  "/tasks": { kw: ["my tasks", "to do", "todo", "clearance"] },
  "/ops/tasks": { kw: ["ops tasks", "task board"] },

  // ── Compliance ──
  "/compliance-center": { kw: ["compliance requirements", "due dates", "compliance calendar"] },
  "/wcb": { kw: ["wcb", "wsib", "workers compensation"] },
  "/safety": { kw: ["ohs", "incident", "health and safety", "accident"] },
  "/ops/policies": { kw: ["policies", "policy register", "hr policy"] },
  "/audit-log": { kw: ["audit log", "audit trail", "hash chain", "who changed"] },

  // ── Privacy & security ──
  "/privacy": { kw: ["pipeda", "privacy", "consent", "personal information", "privacy officer"],
    subs: [
      { to: "/privacy?tab=consents", label: "My consents" },
      { to: "/privacy?tab=notice", label: "Privacy notice" },
      { to: "/privacy?tab=contacts", label: "Privacy Officer", kw: ["privacy officer", "privacy complaint"] },
      { to: "/privacy?tab=coverage", label: "Consent coverage", roles: OFFICE },
      { to: "/privacy?tab=breaches", label: "Breach register", kw: ["privacy breach", "breach of security safeguards"], roles: OFFICE },
      { to: "/privacy?tab=retention", label: "Retention & purge", kw: ["data retention"], roles: OFFICE },
      { to: "/privacy?tab=contract", label: "Service-provider terms", kw: ["data processing agreement"], roles: OFFICE },
      { to: "/privacy?tab=security", label: "Security & export", kw: ["org export", "encryption key", "sign in audit"], roles: OFFICE },
    ] },
  "/data-requests": { kw: ["access my information", "correct my information", "delete my information", "representative", "privacy request"] },
  "/security/sessions": { kw: ["sign in history", "login history", "sessions"] },
  "/security/account": { kw: ["password", "change password", "two step", "2fa", "mfa", "authenticator"] },

  // ── Insights ──
  "/reports/hub": { kw: ["reports", "report hub", "all reports"],
    subs: [
      { to: "/reports/hub?report=headcount", label: "Headcount report", kw: ["headcount by department"] },
      { to: "/reports/hub?report=headcount_trend", label: "Headcount trend" },
      { to: "/reports/hub?report=joiners_leavers", label: "Joiners and leavers", kw: ["new joiners", "leavers"] },
      { to: "/reports/hub?report=attrition", label: "Attrition report", kw: ["turnover"] },
      { to: "/reports/hub?report=probation_due", label: "Probation due report" },
      { to: "/reports/hub?report=birthdays_anniversaries", label: "Birthdays and anniversaries", kw: ["birthday"] },
      { to: "/reports/hub?report=attendance_summary", label: "Attendance summary report" },
      { to: "/reports/hub?report=overtime", label: "Overtime report" },
      { to: "/reports/hub?report=leave_balances", label: "Leave balances report" },
      { to: "/reports/hub?report=leave_usage", label: "Leave usage report" },
      { to: "/reports/hub?report=payroll_cost", label: "Payroll cost report", kw: ["salary cost", "cost to company"], roles: [...ADMIN_ROLES, "payroll_admin"] },
      { to: "/reports/hub?report=statutory_liability", label: "Statutory liability report", roles: [...ADMIN_ROLES, "payroll_admin"] },
      { to: "/reports/hub?report=salary_revisions", label: "Salary revisions report", roles: [...ADMIN_ROLES, "payroll_admin"] },
      { to: "/reports/hub?report=approvals_ageing", label: "Pending approvals ageing" },
      { to: "/reports/hub?report=exit_cases", label: "Exit cases report" },
      { to: "/reports/hub?report=hiring_pipeline", label: "Hiring pipeline report" },
      { to: "/reports/hub?report=performance_cycle", label: "Performance and goals report" },
      { to: "/reports/hub?report=career_changes", label: "Career changes report" },
    ] },
  "/reports/md-dashboard": { kw: ["executive dashboard", "md dashboard", "ceo dashboard", "board"] },
  "/reports": { kw: ["analytics", "charts"] },
  "/reports/builder": { kw: ["report builder", "custom report", "ad hoc report", "scheduled report"],
    subs: [{ to: "/reports/builder?tab=saved", label: "Saved reports", kw: ["scheduled reports"] }] },
  "/analytics/people": { kw: ["people analytics", "diversity", "span of control", "gender mix"],
    subs: [
      { to: "/analytics/people?tab=attrition", label: "Attrition analytics", kw: ["tenure at exit"] },
      { to: "/analytics/people?tab=org", label: "Org & diversity", kw: ["gender", "span of control"] },
      { to: "/analytics/people?tab=time", label: "Overtime & leave liability", kw: ["leave liability"], roles: [...HR, "payroll_admin"] },
      { to: "/analytics/people?tab=actions", label: "Retention actions" },
      { to: "/analytics/people?tab=calibration", label: "Attrition calibration", roles: [...HR, "payroll_admin"] },
    ] },
  "/analytics/positions": { kw: ["positions", "headcount plan", "vacancies"],
    subs: [{ to: "/analytics/positions?tab=budget", label: "Headcount budget vs actual" }] },
  "/benchmarking": { kw: ["salary benchmark", "market median"] },

  // ── AI ──
  "/ai/command-center": { kw: ["ai briefing", "domain layers"] },
  "/ai/workforce": { kw: ["ai agents", "watchdogs", "digital workers"] },
  "/ai/activity": { kw: ["ai run log", "agent activity"] },
  "/ai/economics": { kw: ["token usage", "ai spend", "ai cost"] },
  "/ai/compliance-health": { kw: ["watchdog findings", "compliance health"],
    subs: [
      { to: "/ai/compliance-health?tab=findings", label: "Watchdog findings" },
      { to: "/ai/compliance-health?tab=runs", label: "Watchdog run log" },
      { to: "/ai/compliance-health?tab=settings", label: "Watchdog settings", kw: ["thresholds"] },
    ] },
  "/ai/flight-risk": { kw: ["attrition risk", "flight risk", "retention risk"] },
  "/ai/usage": { kw: ["ai usage", "tokens per day"] },
  "/ai/agents": { kw: ["agents", "agentic", "hr desk", "onboarding agent", "exit agent", "attendance agent", "approval inbox", "shadow mode", "agent value"],
    subs: [
      { to: "/ai/agents?tab=approvals", label: "Agent approvals inbox", kw: ["approve", "deny", "two approvers", "tier"] },
      { to: "/ai/agents?tab=tasks", label: "Agent tasks (who is chased)", kw: ["chase", "reminder", "escalate"] },
      { to: "/ai/agents?tab=runs", label: "Shadow reports & agent runs", kw: ["what I would have done", "action log", "verify chain"] },
      { to: "/ai/agents?tab=value", label: "Agent value (measured · proxy · avoided)", kw: ["hours saved", "minutes table", "roi"] },
      { to: "/ai/agents?tab=settings", label: "Agent settings (mode, approvers, thresholds)", kw: ["shadow", "live", "approver roles"], roles: ADMIN_ROLES },
    ] },
  // Payroll Auditor (Canada, restored 2026-10-26)
  "/ai/agents/payroll-auditor": { kw: ["payroll auditor", "review pack", "exception grid", "pay period checks", "unpaid leave vs attendance", "variance explanation", "waiting on", "tie-out", "gl journal tie-out", "cpp check", "qpp check", "ei check", "qpip", "ympe", "yampe", "ytd maximum", "td1 change", "vacation pay accrual", "final pay", "roe due", "17 checks", "ready to release"] },
  // Lifecycle agents (2026-10-26)
  "/ai/agents/onboarding": { kw: ["onboarding agent", "joiner checklist", "joining checklist", "new joiner", "offer accepted", "appointment letter draft", "sin check", "direct deposit", "td1 forms", "documents vault", "induction", "laptop issued"] },
  "/ai/agents/exit": { kw: ["exit agent", "resignation", "notice period", "last working day", "clearance", "no dues", "full and final", "final pay", "roe", "record of employment", "relieving letter", "experience letter", "final pay ready"] },
  "/ai/agents/attendance": { kw: ["attendance agent", "missing punch", "single punch", "late marks", "overtime limit", "ot breach", "regularisation pending", "unapproved leave", "payroll cut-off", "whatsapp reminders", "agent reminders whatsapp"] },
  // HR Desk agent (phase 2, P2-C)
  "/ai/agents/hr-desk": { kw: ["hr desk", "ask lexi", "deflection", "self-solved", "csat", "top questions", "weekly report", "abstained questions", "knowledge readiness"] },
  "/ai/desk/policies": { kw: ["hr desk policies", "policy clauses", "policy import", "approve clause", "sme sign-off", "ai readiness", "effective date", "clause version"] },
  "/ai/desk/ic": { kw: ["harassment referrals", "designated recipient", "harassment investigator", "workplace harassment", "sealed referrals"] },
  "/ai/settings": { kw: ["claude api key", "ai model", "monthly cap"] },
  "/ai/tokens": { kw: ["mcp", "ai access token"] },

  // ── Mobile ──
  "/mobile/home": { kw: ["mobile", "phone app"] },
  "/mobile/punch": { kw: ["mobile punch", "geo punch", "selfie punch"] },
  "/mobile/team-punches": { kw: ["team punches", "attendance fixes"] },
  "/mobile/sites": { kw: ["geo fence", "work sites", "phones", "devices"],
    subs: [
      { to: "/mobile/sites?tab=devices", label: "Registered phones" },
      { to: "/mobile/sites?tab=rules", label: "Punch rules" },
    ] },
  "/mobile/translations": { kw: ["translations", "french", "francais", "language"] },

  // ── Engage ──
  "/engage/helpdesk": { kw: ["helpdesk", "ticket", "ask hr", "support", "grievance", "query"],
    subs: [
      { to: "/engage/helpdesk?new=1", label: "Ask HR — new ticket", kw: ["raise ticket", "ask hr", "new ticket"] },
      { to: "/engage/helpdesk?tab=mine", label: "My tickets" },
      { to: "/engage/helpdesk?tab=assigned", label: "Tickets assigned to me", roles: AGENT },
      { to: "/engage/helpdesk?tab=report", label: "Helpdesk ageing & SLA report", kw: ["sla"], roles: AGENT },
      { to: "/engage/helpdesk?tab=settings", label: "Helpdesk categories & SLA", roles: AGENT },
    ] },
  "/engage/surveys": { kw: ["survey", "pulse", "enps", "feedback survey"],
    subs: [{ to: "/engage/surveys?mode=trend", label: "Survey trend", roles: HR }] },
  "/engage/kudos": { kw: ["kudos", "recognition", "appreciation", "shout out"],
    subs: [
      { to: "/engage/kudos?tab=monthly", label: "Kudos this month" },
      { to: "/engage/kudos?tab=pending", label: "Kudos waiting for approval", roles: HR },
    ] },
  "/engage/feed": { kw: ["company feed", "announcements", "posts", "polls", "birthdays", "anniversaries", "new joiners"] },
  "/engage/learning": { kw: ["courses", "training", "lms", "e-learning"],
    subs: [
      { to: "/engage/learning?tab=courses", label: "All courses (manage)", roles: HR_ONLY },
      { to: "/engage/learning?tab=compliance", label: "Training compliance", kw: ["mandatory training"], roles: [...HR_ONLY, "manager"] },
    ] },
  "/learning/my": { kw: ["my learning", "my courses", "certificates", "learning paths"] },
  "/learning/admin": { kw: ["learning admin", "completion report"],
    subs: [
      { to: "/learning/admin?tab=paths", label: "Learning paths", roles: HR_ONLY },
      { to: "/learning/admin?tab=rules", label: "Course assignment rules", roles: HR_ONLY },
      { to: "/learning/admin?tab=bank", label: "Quiz question bank", roles: HR_ONLY },
      { to: "/learning/admin?tab=team", label: "Team learning view" },
    ] },
  "/engage/surveys/admin": { kw: ["survey schedules", "heatmap", "question bank", "recognition budget"],
    subs: [
      { to: "/engage/surveys/admin?tab=heatmap", label: "Engagement heatmaps" },
      { to: "/engage/surveys/admin?tab=trend", label: "eNPS trend", kw: ["enps"] },
      { to: "/engage/surveys/admin?tab=bank", label: "Survey question bank" },
      { to: "/engage/surveys/admin?tab=budgets", label: "Recognition budgets", kw: ["kudos budget"] },
    ] },
  "/engage/insights": { kw: ["manager insights", "team score"] },
  "/engage/assets": { kw: ["asset register", "laptop", "assets", "it assets", "inventory"] },
  "/engage/my-assets": { kw: ["my laptop", "assets issued to me"] },

  // ── Self-service ──
  "/my-profile": { kw: ["my details", "profile", "personal details", "bank details", "address"] },
  "/my-pay": { kw: ["payslip", "my payslips", "salary slip", "pay slip", "net pay", "my salary"] },
  "/my-compensation": { kw: ["my salary", "salary history", "salary break up"] },
  "/my-letters": { kw: ["my letters", "offer letter", "appointment letter", "experience letter"] },
  "/documents": { kw: ["document centre", "my documents", "upload document"],
    subs: [
      { to: "/documents?tab=letters", label: "Documents — Letters" },
      { to: "/documents?tab=pay", label: "Documents — Pay stubs & tax", kw: ["t4 download", "tax slips"] },
      { to: "/documents?tab=policies", label: "Company policies (to accept)", kw: ["policy", "handbook", "acknowledge policy"] },
      { to: "/documents?tab=manage", label: "Publish & track policies", roles: HR_ONLY },
    ] },
  "/my-resignation": { kw: ["resign", "resignation", "notice period", "quit"] },
  "/my-time": { kw: ["my timesheet", "hours by project"] },
  "/my-benefits": { kw: ["insurance", "my benefits", "health insurance"] },
  "/my-requests": { kw: ["my requests", "request status"] },
  "/me/chat-link": { kw: ["slack", "teams", "chat link"] },

  // ── Setup ──
  "/settings": { kw: ["settings", "setup", "configuration", "setup checklist"] },
  "/masters": { kw: ["master data", "masters", "dropdowns"],
    subs: [
      { to: "/masters", label: "Departments", kw: ["department master"] },
      { to: "/masters?tab=designations", label: "Job titles", kw: ["designations", "designation master"] },
      { to: "/masters?tab=grades", label: "Grades", kw: ["bands", "levels"] },
      { to: "/masters?tab=locations", label: "Locations", kw: ["offices", "work locations"] },
      { to: "/masters?tab=leave-types", label: "Leave types", kw: ["leave policy", "leave master"] },
      { to: "/masters?tab=pay-components", label: "Pay components", kw: ["earnings", "deductions"] },
      { to: "/masters?tab=fiscal-years", label: "Fiscal years" },
      { to: "/masters?tab=cost-centers", label: "Cost centres", kw: ["cost center"] },
      { to: "/masters?tab=benefit-plans", label: "Benefit plans (master)" },
      { to: "/masters?tab=stat-holidays", label: "Stat holidays (master)" },
    ] },
  "/roles": { kw: ["permissions", "access", "rbac", "role permissions", "custom role", "privileges", "access control"],
    subs: [
      { to: "/roles?tab=roles", label: "System & custom roles", kw: ["create role", "clone role", "retire role", "permission matrix"] },
      { to: "/roles?tab=people", label: "People & their roles", kw: ["assign role", "give access", "effective permissions"] },
      { to: "/roles?tab=history", label: "Role change history", kw: ["role audit", "who changed access"] },
    ] },
  "/users": { kw: ["logins", "user accounts", "invite user", "reset password", "create login"] },
  "/company-settings": { kw: ["company profile", "organisation details", "company info"],
    subs: [
      { to: "/company-settings", label: "Company profile & logo", kw: ["logo", "legal name", "address", "registration numbers", "currency", "financial year"] },
      { to: "/company-settings?tab=work", label: "Work week and hours", kw: ["working days", "weekly off", "work hours"] },
      { to: "/company-settings?tab=numbering", label: "Numbering prefixes", kw: ["employee number format", "prefix"] },
      { to: "/company-settings?tab=notifications", label: "E-mail sender & approval reminders", kw: ["smtp", "sender", "reminders"] },
      { to: "/company-settings?tab=history", label: "Settings change history" },
    ] },
  "/plan-billing": { kw: ["subscription", "plan", "billing", "invoice", "upgrade"] },
  "/ops/branches": { kw: ["branches", "locations", "offices"] },
  "/integrations": { kw: ["integrations", "connectors", "api"],
    subs: [
      { to: "/integrations?tab=sso", label: "Single sign-on (SSO)", kw: ["saml", "google login", "azure ad", "okta"] },
      { to: "/integrations?tab=scim", label: "SCIM provisioning" },
      { to: "/integrations?tab=keys", label: "API keys", kw: ["api key"] },
      { to: "/integrations?tab=webhooks", label: "Webhooks" },
      { to: "/integrations?tab=chat", label: "Slack · Teams · WhatsApp", kw: ["whatsapp", "slack", "teams"] },
      { to: "/integrations?tab=esign", label: "E-sign", kw: ["esign", "digital signature", "e-signature"] },
      { to: "/integrations?tab=calls", label: "Provider calls log" },
      { to: "/integrations?tab=audit", label: "Integrations audit" },
    ] },
};

/** Screens inside pages (settings editors, hub cards, detail routes) that are not in the menu. */
export const EXTRA_PAGES: ExtraPage[] = [
  { to: "/settings/entities", label: "Legal entities", group: "Setup", parent: "Settings hub", roles: ADMIN_ROLES,
    kw: ["group companies", "entity", "letterhead per entity", "authorised signatory", "business number", "cra program account"] },
  { to: "/settings/custom-fields", label: "Custom fields", group: "Setup", parent: "Settings hub", roles: [...ADMIN_ROLES, "hr_admin"],
    kw: ["extra fields", "custom data"] },
  { to: "/settings/custom-fields?entity=candidate", label: "Custom fields — Candidate", group: "Setup", parent: "Custom fields", roles: [...ADMIN_ROLES, "hr_admin"] },
  { to: "/settings/custom-fields?entity=asset", label: "Custom fields — Asset", group: "Setup", parent: "Custom fields", roles: [...ADMIN_ROLES, "hr_admin"] },
  { to: "/settings/custom-fields?entity=expense", label: "Custom fields — Expense claim", group: "Setup", parent: "Custom fields", roles: [...ADMIN_ROLES, "hr_admin"] },
  { to: "/settings/approval-workflows", label: "Approval workflows", group: "Setup", parent: "Settings hub", roles: HRP,
    kw: ["approval chain", "workflow", "multi level approval", "escalation", "auto approve"] },
  { to: "/settings/letter-templates", label: "Letter template editor", group: "Setup", parent: "Settings hub", roles: HR_ONLY,
    kw: ["edit letter template", "letter versions", "placeholders"] },
  { to: "/comp/settings", label: "Compensation settings", group: "Talent", parent: "Compensation Cycles", roles: HR,
    kw: ["compa ratio", "grade midpoints", "merit matrix", "budget rules"], module: "talent" },
  { to: "/hiring/candidates", label: "Hiring — Candidate board", group: "Talent", parent: "Candidates", roles: [...HR, "manager"], kw: ["kanban", "board view"], module: "talent" },
  { to: "/mobile/leave", label: "Apply leave (mobile)", group: "Mobile", roles: ALL_STAFF, kw: ["mobile leave"] },
  { to: "/mobile/payslips", label: "Pay stubs (mobile)", group: "Mobile", roles: ALL_STAFF, kw: ["mobile payslip", "mobile pay stub"] },
  { to: "/off-cycle", label: "Off-cycle cheque", group: "Payroll", roles: PAYROLL_ACCESS, kw: ["manual cheque", "off cycle history"], module: "payroll" },
];

/** Query phrase → what the app calls it. Applied BOTH ways at query time (word-boundary), so
 *  "paystub" finds payslip screens and "record of employment" finds ROE. */
export const SYNONYMS: [string, string][] = [
  ["cpp", "canada pension plan"], ["qpp", "quebec pension plan"], ["ei", "employment insurance"], ["roe", "record of employment"],
  ["cra", "canada revenue agency"], ["rl-1", "releve 1"], ["wsib", "wcb"], ["source deductions", "remittance"],
  ["salary slip", "payslip"], ["pay slip", "payslip"], ["payslips", "payslip"], ["paystub", "payslip"], ["pay stub", "payslip"],
  ["appraisal", "review"], ["appraisal", "performance"], 
  ["hike", "increment"], ["increment", "salary revision"], ["raise", "salary revision"],
  ["final pay", "offboarding"], ["final cheque", "offboarding"],
  ["resign", "resignation"], ["quit", "resignation"], ["leaver", "exit"], ["termination", "exit"],
  ["vacation", "leave"], ["time off", "leave"], ["pto", "leave"], ["holiday", "holidays"],
  ["jv", "journal"],
  ["gl", "general ledger"],
  ["staff", "employees"], ["people", "employees"], ["employee", "employees"], ["team", "employees"],
  ["candidate", "candidates"], ["applicant", "candidates"], ["recruitment", "hiring"], ["job opening", "requisition"],
  ["letter head", "letterhead"], ["logo", "company profile"],
  ["2fa", "two-step"], ["mfa", "two-step"], ["password", "account security"],
  ["regularization", "attendance correction"], ["regularisation", "attendance correction"], ["missed punch", "attendance correction"],
  ["ticket", "helpdesk"], ["grievance", "helpdesk"], ["policy", "policies"], ["training", "learning"], ["course", "learning"],
  ["okr", "goals"], ["kra", "goals"], ["org chart", "org-chart"], ["hierarchy", "org chart"],
];
