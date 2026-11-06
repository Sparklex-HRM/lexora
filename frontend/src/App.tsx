import { Suspense, lazy, JSX } from "react";
import { BrowserRouter, Routes, Route, Navigate, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "react-hot-toast";
import { useAuthStore } from "./store/authStore";
import {
  HR_WRITE, PAYROLL_ACCESS, APPROVER_ROLES, ADMIN_ROLES, ALL_STAFF, ROLE_LABEL,
} from "./store/authStore";
import { Spinner } from "./components/ui";
import SessionGate from "./components/SessionGate";   // 2026-10-24 signed UI grant
import { navItemAllowed } from "./config/permissions";   // 2026-10-26: route guard = role + custom-role module

// WASA "API endpoint disclosure in JS" (2026-10-24): only what /login and the other public
// routes need is static. The signed-in shell (DashboardLayout, DashboardHome) is lazy — it
// pulls api/endpoints.ts (the full API map), which used to sit in the entry chunk served on
// /login. Do not import a src/api/* module (other than client / publicAuth) or the shell
// statically here.
import LoginPage from "./pages/LoginPage";
import { SplashScreen } from "./components/SplashScreen";
const DashboardLayout = lazy(() => import("./components/DashboardLayout"));
const DashboardHome = lazy(() => import("./pages/DashboardHome"));

function SplashGate() {
  const splash = useAuthStore((s) => s.splash);
  const user = useAuthStore((s) => s.user);
  const endSplash = useAuthStore((s) => s.endSplash);
  if (!splash || !user) return null;
  return <SplashScreen onDone={endSplash} />;
}

// Stream S4: self-serve sign-up, legal pages, account security
const SignupPage = lazy(() => import("./pages/saas/SignupPage"));
const SignupVerifyPage = lazy(() => import("./pages/saas/SignupPage").then((m) => ({ default: m.SignupVerifyPage })));
const PrivacyPolicyPage = lazy(() => import("./pages/saas/LegalPages").then((m) => ({ default: m.PrivacyPolicyPage })));
const TermsPage = lazy(() => import("./pages/saas/LegalPages").then((m) => ({ default: m.TermsPage })));
const AccountSecurityPage = lazy(() => import("./pages/saas/AccountSecurityPage"));
const EmployeesPage = lazy(() => import("./pages/EmployeesPage"));
const EmployeeProfilePage = lazy(() => import("./pages/employee/EmployeeProfilePage"));   // U1: Employee 360
const PayrollPage = lazy(() => import("./pages/PayrollPage"));
const OnboardingPage = lazy(() => import("./pages/OnboardingPage"));
const AttendancePage = lazy(() => import("./pages/AttendancePage"));
const LeavePage = lazy(() => import("./pages/LeavePage"));
const ApprovalsPage = lazy(() => import("./pages/ApprovalsPage"));
const WhatsAppApprovalsPage = lazy(() => import("./pages/approvals/WhatsAppApprovalsPage"));   // G7 2026-10-23
const AICommandCenterPage = lazy(() => import("./pages/AICommandCenterPage"));
const AIWorkforcePage = lazy(() => import("./pages/AIWorkforcePage"));
const AIActivityPage = lazy(() => import("./pages/AIActivityPage"));
const AIEconomicsPage = lazy(() => import("./pages/AIEconomicsPage"));
// G5: watchdog compliance health, flight-risk signals, custom report builder
const ComplianceHealthPage = lazy(() => import("./pages/ai/ComplianceHealthPage"));
const AttritionSignalsPage = lazy(() => import("./pages/ai/AttritionSignalsPage"));
// D3: AI that acts — settings (BYO key, caps), access tokens (MCP), usage metering
const AiSettingsPage = lazy(() => import("./pages/ai/AiSettingsPage"));
const AgentsPage = lazy(() => import("./pages/ai/AgentsPage"));   // Agentic operations add-on 2026-10-25
const PayrollAuditorPage = lazy(() => import("./pages/ai/agents/payroll/PayrollAuditorPage"));   // Payroll Auditor (Canada, restored 2026-10-26)
// Lifecycle agents (2026-10-26): Onboarding / Exit / Attendance workspaces (office roles)
const OnboardingAgentPage = lazy(() => import("./pages/ai/agents/lifecycle/OnboardingAgentPage"));
const ExitAgentPage = lazy(() => import("./pages/ai/agents/lifecycle/ExitAgentPage"));
const AttendanceAgentPage = lazy(() => import("./pages/ai/agents/lifecycle/AttendanceAgentPage"));
// HR Desk agent (phase 2, P2-C): policy clauses (HR), sealed IC inbox (self-gated), the agent's dashboard (office roles)
const PolicyClausesPage = lazy(() => import("./pages/ai/desk/PolicyClausesPage"));
const IcReferralsPage = lazy(() => import("./pages/ai/desk/IcReferralsPage"));
const DeskDashboardPage = lazy(() => import("./pages/ai/desk/DeskDashboardPage"));
const AiTokensPage = lazy(() => import("./pages/ai/AiTokensPage"));
const AiUsagePage = lazy(() => import("./pages/ai/AiUsagePage"));
const ReportBuilderPage = lazy(() => import("./pages/reports/builder/ReportBuilderPage"));
const ReportsPage = lazy(() => import("./pages/ReportsPage"));
const ReportsHubPage = lazy(() => import("./pages/ReportsHubPage"));
const MDDashboardPage = lazy(() => import("./pages/MDDashboardPage"));
const CommandCenterPage = lazy(() => import("./pages/CommandCenterPage"));
const MasterDataPage = lazy(() => import("./pages/MasterDataPage"));
const PlatformPage = lazy(() => import("./pages/PlatformPage"));
const RevenuePage = lazy(() => import("./pages/RevenuePage"));
const PlansPricingPage = lazy(() => import("./pages/PlansPricingPage"));
const RolesPage = lazy(() => import("./pages/RolesPage"));
// Stream A: logins + the public link pages (no sign-in needed for the last four)
const UsersPage = lazy(() => import("./pages/UsersPage"));
const OnboardingInvitePage = lazy(() => import("./pages/OnboardingInvitePage"));
const SetPasswordPage = lazy(() => import("./pages/SetPasswordPage"));
const ResetPasswordPage = lazy(() => import("./pages/SetPasswordPage").then((m) => ({ default: m.ResetPasswordPage })));
const ForgotPasswordPage = lazy(() => import("./pages/SetPasswordPage").then((m) => ({ default: m.ForgotPasswordPage })));
const ModulePage = lazy(() => import("./pages/ModulePage"));
const OpsResourcePage = lazy(() => import("./pages/OpsResourcePage"));
// Phase-1 real screens (data-backed)
const HolidaysPage = lazy(() => import("./pages/HolidaysPage"));
const OrgChartPage = lazy(() => import("./pages/OrgChartPage"));
const GarnishmentsPage = lazy(() => import("./pages/GarnishmentsPage"));
const CompanySettingsPage = lazy(() => import("./pages/CompanySettingsPage"));
const LegalEntitiesPage = lazy(() => import("./pages/settings/LegalEntitiesPage"));   // D4: group companies
const SettingsHubPage = lazy(() => import("./pages/SettingsHubPage"));   // U4
// D1: configurability — custom fields, no-code approval workflows, letter-template editor
const CustomFieldsPage = lazy(() => import("./pages/settings/CustomFieldsPage"));
const ApprovalWorkflowsPage = lazy(() => import("./pages/settings/ApprovalWorkflowsPage"));
const LetterTemplateEditorPage = lazy(() => import("./pages/settings/LetterTemplateEditorPage"));
const BenefitsPage = lazy(() => import("./pages/BenefitsPages"));
const EnrollmentPage = lazy(() => import("./pages/BenefitsPages").then((m) => ({ default: m.EnrollmentPage })));
const CarriersPage = lazy(() => import("./pages/BenefitsPages").then((m) => ({ default: m.CarriersPage })));
const RetirementPage = lazy(() => import("./pages/BenefitsPages").then((m) => ({ default: m.RetirementPage })));
const MyProfilePage = lazy(() => import("./pages/SelfServicePages"));
const PlanBillingPage = lazy(() => import("./pages/PlanBillingPage"));
const SessionAuditPage = lazy(() => import("./pages/SessionAuditPage"));
const BiometricImportPage = lazy(() => import("./pages/time/BiometricImportPage"));
// Stream B (lifecycle): salary revisions, career changes, probation, letters, self-service
const SalaryRevisionsPage = lazy(() => import("./pages/lifecycle/SalaryRevisionsPage"));
const CareerChangesPage = lazy(() => import("./pages/lifecycle/CareerChangesPage"));
const ProbationPage = lazy(() => import("./pages/lifecycle/ProbationPage"));
const FixedTermPage = lazy(() => import("./pages/lifecycle/FixedTermPage"));                   // 2026-10-22
const LettersPage = lazy(() => import("./pages/lifecycle/LettersPage"));
const EmployeeHistoryPage = lazy(() => import("./pages/lifecycle/EmployeeHistoryPage"));
const MyCompensationPage = lazy(() => import("./pages/lifecycle/MyLifecyclePages"));
const MyLettersPage = lazy(() => import("./pages/lifecycle/MyLifecyclePages").then((m) => ({ default: m.MyLettersPage })));
const MyPayPage = lazy(() => import("./pages/SelfServicePages").then((m) => ({ default: m.MyPayPage })));
const MyBenefitsPage = lazy(() => import("./pages/SelfServicePages").then((m) => ({ default: m.MyBenefitsPage })));
const MyTimePage = lazy(() => import("./pages/MyTimePage"));
const MyRequestsPage = lazy(() => import("./pages/ApprovalsPage").then((m) => ({ default: m.MyRequestsPage })));
const NotificationsPage = lazy(() => import("./pages/NotificationsPage"));
const OffCyclePage = lazy(() => import("./pages/OffCyclePage"));
// Config-driven functional screens (localStorage-persisted CRUD)
const col = (name: string) => lazy(() => import("./pages/collections").then((m) => ({ default: (m as any)[name] })));
const OffboardingPage = lazy(() => import("./pages/OffboardingPage"));
// Stream C (lifecycle): exit process
const ExitsPage = lazy(() => import("./pages/exit/ExitsPage"));
const ExitCasePage = lazy(() => import("./pages/exit/ExitCasePage"));
const MyResignationPage = lazy(() => import("./pages/exit/MyResignationPage"));
const FormerEmployeesPage = lazy(() => import("./pages/exit/FormerEmployeesPage"));
const TimesheetsPage = lazy(() => import("./pages/TimesheetsPage"));
const SchedulingPage = lazy(() => import("./pages/SchedulingPage"));
const ShiftRotationsPage = lazy(() => import("./pages/time/ShiftRotationsPage"));   // G2 2026-10-23
const MyShiftsPage = lazy(() => import("./pages/time/MyShiftsPage"));               // G2 2026-10-23
const AttendanceAnalyticsPage = lazy(() => import("./pages/AttendanceAnalyticsPage"));
const TimeClockPage = lazy(() => import("./pages/TimeClockPage"));
const AbsenceCalendarPage = lazy(() => import("./pages/AbsenceCalendarPage"));
// Stream D: hiring + performance are real, data-backed pages (the mock entries are gone from collections.tsx)
const HiringPage = lazy(() => import("./pages/hiring/HiringPage"));
const MyInterviewsPage = lazy(() => import("./pages/hiring/MyInterviewsPage"));
const PerformancePage = lazy(() => import("./pages/performance/PerformancePage"));
const CareersPage = lazy(() => import("./pages/hiring/PublicPages").then((m) => ({ default: m.CareersPage })));
const CareerJobPage = lazy(() => import("./pages/hiring/PublicPages").then((m) => ({ default: m.CareerJobPage })));
const OfferAnswerPage = lazy(() => import("./pages/hiring/PublicPages").then((m) => ({ default: m.OfferAnswerPage })));
const InterviewPickPage = lazy(() => import("./pages/hiring/PublicPages").then((m) => ({ default: m.InterviewPickPage })));   // D2
const ReferralsPage = lazy(() => import("./pages/hiring/ReferralsPage"));   // D2: ESS refer-a-friend
const LearningPage = col("LearningPage");
const SuccessionPage = col("SuccessionPage");
const MsaPage = col("MsaPage");
const ComplianceCenterPage = col("ComplianceCenterPage");
const WcbPage = lazy(() => import("./pages/WcbPage"));
const SafetyPage = lazy(() => import("./pages/SafetyPage"));
const AuditLogPage = lazy(() => import("./pages/AuditLogPage"));
const PrivacyCenterPage = lazy(() => import("./pages/PrivacyCenterPage"));
const DataRequestsPage = lazy(() => import("./pages/DataRequestsPage"));
const BenchmarkingPage = col("BenchmarkingPage");
const GlExportPage = lazy(() => import("./pages/GlExportPage"));
// Stream G6: Canada completeness + migration kit
const CaStatutoryValuesPage = lazy(() => import("./pages/canada/StatutoryValuesPage"));
const CaRemittancesPage = lazy(() => import("./pages/canada/RemittancesPage"));
const CaStatHolidayPayPage = lazy(() => import("./pages/canada/StatHolidayPayPage"));
const CaEmployerAccrualsPage = lazy(() => import("./pages/canada/EmployerAccrualsPage"));
const CaContractorsT4APage = lazy(() => import("./pages/canada/ContractorsT4APage"));
const CaYearEndChecklistPage = lazy(() => import("./pages/canada/YearEndChecklistPage"));
const ParallelRunPage = lazy(() => import("./pages/migration/ParallelRunPage"));
const MigrationChecklistPage = lazy(() => import("./pages/migration/MigrationChecklistPage"));
const CanadaImportPage = lazy(() => import("./pages/migration/CanadaImportPage"));
// Stream G3: real Integrations screen (SSO, SCIM, API keys, webhooks, chat, e-sign) + public pages
const IntegrationsPage = lazy(() => import("./pages/integrations/IntegrationsPage"));
const SignPage = lazy(() => import("./pages/integrations/PublicPages").then((m) => ({ default: m.SignPage })));
const SsoDonePage = lazy(() => import("./pages/integrations/PublicPages").then((m) => ({ default: m.SsoDonePage })));
const ChatLinkPage = lazy(() => import("./pages/integrations/PublicPages").then((m) => ({ default: m.ChatLinkPage })));
// Stream G2: project services (PSA)
const PsaPage = lazy(() => import("./pages/psa/PsaPage"));
// D5: compensation planning cycles (matrix, worksheets, calibration, approval chain, apply via salary revisions)
const CompCyclesPage = lazy(() => import("./pages/comp/cycles/CompCyclesPage"));
const CompCyclePage = lazy(() => import("./pages/comp/cycles/CompCyclePage"));
const CompSettingsPage = lazy(() => import("./pages/comp/cycles/CompSettingsPage"));
const MyWorksheetsPage = lazy(() => import("./pages/comp/cycles/MyWorksheetsPage"));
const MyWorksheetPage = lazy(() => import("./pages/comp/cycles/MyWorksheetsPage").then((m) => ({ default: m.MyWorksheetPage })));
// Stream G4: mobile self-service (PWA) + engagement (helpdesk, surveys, kudos, LMS, assets)
const MobileHomePage = lazy(() => import("./pages/mobile/MobileHomePage"));
const MobilePunchPage = lazy(() => import("./pages/mobile/MobilePunchPage"));
const MobileLeavePage = lazy(() => import("./pages/mobile/MobileLeavePage"));
const MobilePayslipsPage = lazy(() => import("./pages/mobile/MobilePayslipsPage"));
const TeamPunchesPage = lazy(() => import("./pages/mobile/TeamPunchesPage"));
const WorkSitesPage = lazy(() => import("./pages/mobile/WorkSitesPage"));
const I18nReportPage = lazy(() => import("./pages/mobile/I18nReportPage"));
const HelpdeskPage = lazy(() => import("./pages/engage/HelpdeskPage"));
const SurveysPage = lazy(() => import("./pages/engage/SurveysPage"));
// Stream D7: LMS depth, engagement depth, headcount planning, people analytics
const MyLearningPage = lazy(() => import("./pages/learning/MyLearningPage"));
const LearningAdminPage = lazy(() => import("./pages/learning/LearningAdminPage"));
const SurveysAdminPage = lazy(() => import("./pages/engage/SurveysAdminPage"));
const ManagerInsightsPage = lazy(() => import("./pages/engage/ManagerInsightsPage"));
const PositionsPage = lazy(() => import("./pages/analytics/PositionsPage"));
const PeopleAnalyticsPage = lazy(() => import("./pages/analytics/PeopleAnalyticsPage"));
const KudosPage = lazy(() => import("./pages/engage/KudosPage"));
// 2026-10-16 employee portal batch 3
const FeedPage = lazy(() => import("./pages/employee/FeedPage"));
const RaiseRequestPage = lazy(() => import("./pages/employee/RaiseRequestPage"));
const RequestTypesAdminPage = lazy(() => import("./pages/employee/RequestTypesAdminPage"));
const TasksPage = lazy(() => import("./pages/employee/TasksPage"));
const DocumentCentrePage = lazy(() => import("./pages/employee/DocumentCentrePage"));
const EngLearningPage = lazy(() => import("./pages/engage/LearningPage"));
const AssetsPage = lazy(() => import("./pages/engage/AssetsPage"));
const MyAssetsPage = lazy(() => import("./pages/engage/MyAssetsPage"));

// HMS perf defaults: longer staleTime, no refetch-on-focus, one retry.
const qc = new QueryClient({ defaultOptions: { queries: {
  staleTime: 60_000, gcTime: 300_000, refetchOnWindowFocus: false, retry: 1,
} } });

function LoadingFallback() {
  return <div className="min-h-[50vh] grid place-items-center"><Spinner className="h-10 w-10" /></div>;
}

function RequireAuth({ children }: { children: JSX.Element }) {
  const token = useAuthStore((s) => s.token);
  // 2026-10-24: the shell renders only after a VERIFIED ui_grant (components/SessionGate.tsx)
  return token ? <SessionGate>{children}</SessionGate> : <Navigate to="/login" replace />;
}

/** Role-gated route wrapper (mirrors the HMS RequireRole): renders a 403 panel
 *  instead of redirecting so a user who types a URL understands why. */
function RequireRole({ allowed, children }: { allowed: string[]; children: JSX.Element }) {
  const user = useAuthStore((s) => s.user);
  const { pathname } = useLocation();
  const role = user?.role || "";
  if (!role) return <Navigate to="/login" replace />;
  // The system role (unchanged) OR — 2026-10-26 — a custom role whose acting role is allowed AND whose permissions
  // carry this screen's catalogue module (same rule as the menu; was: any acting role opened every page of that
  // role). Both from the verified grant; the server enforces every call.
  if (!navItemAllowed({ to: pathname, roles: allowed }, user)) {
    return (
      <div className="p-8 text-center">
        <div className="inline-block p-6 rounded-xl" style={{ background: "#fee2e2", border: "1px solid #fecaca" }}>
          <h2 className="text-lg font-semibold" style={{ color: "#b91c1c" }}>403 — Access denied</h2>
          <p className="text-sm mt-2" style={{ color: "#dc2626" }}>
            Your role <code className="px-1.5 py-0.5 rounded font-mono" style={{ background: "#fecaca" }}>
              {ROLE_LABEL[role] || role}</code> isn't authorised for this page.
          </p>
          <p className="text-xs mt-3" style={{ color: "var(--muted)" }}>Contact your org admin if you need access.</p>
        </div>
      </div>
    );
  }
  return children;
}

/** Index route: the platform operator (superadmin) has no tenant dashboard —
 *  send it straight to the Lexora console. */
function IndexHome() {
  const role = useAuthStore((s) => s.user?.role) || "";
  return role === "superadmin" ? <Navigate to="/platform" replace /> : <DashboardHome />;
}

export default function App() {
  return (
    <QueryClientProvider client={qc}>
      <BrowserRouter>
        <Toaster position="top-right" toastOptions={{ style: { fontSize: 14 } }} />
        <Suspense fallback={<LoadingFallback />}>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            {/* S4 public pages: self-serve sign-up + e-mail confirmation, legal drafts */}
            <Route path="/signup" element={<SignupPage />} />
            <Route path="/signup/verify/:token" element={<SignupVerifyPage />} />
            <Route path="/legal/privacy" element={<PrivacyPolicyPage />} />
            <Route path="/legal/terms" element={<TermsPage />} />
            {/* Stream D public pages (no login): careers list, apply form, offer answer link */}
            <Route path="/careers/:orgCode" element={<CareersPage />} />
            <Route path="/careers/:orgCode/:jobId" element={<CareerJobPage />} />
            <Route path="/offer/:token" element={<OfferAnswerPage />} />
            <Route path="/interview/:token" element={<InterviewPickPage />} />   {/* D2: candidate picks an interview slot */}
            <Route path="/onboarding/invite/:token" element={<OnboardingInvitePage />} />
            <Route path="/set-password/:token" element={<SetPasswordPage />} />
            <Route path="/reset-password/:token" element={<ResetPasswordPage />} />
            <Route path="/forgot-password" element={<ForgotPasswordPage />} />
            {/* Stream G3 public pages: e-sign link, SSO return */}
            <Route path="/sign/:token" element={<SignPage />} />
            <Route path="/sso/done" element={<SsoDonePage />} />
            <Route path="/" element={<RequireAuth><DashboardLayout /></RequireAuth>}>
              <Route index element={<IndexHome />} />
              <Route path="employees" element={<RequireRole allowed={[...HR_WRITE, "manager", "employee", "accountant"]}><EmployeesPage /></RequireRole>} />
              <Route path="employees/:id" element={<RequireRole allowed={[...HR_WRITE, "manager", "payroll_admin", "employee"]}><EmployeeProfilePage /></RequireRole>} />
              <Route path="onboarding" element={<RequireRole allowed={HR_WRITE}><OnboardingPage /></RequireRole>} />
              <Route path="payroll" element={<RequireRole allowed={PAYROLL_ACCESS}><PayrollPage /></RequireRole>} />
              <Route path="attendance" element={<RequireRole allowed={ALL_STAFF}><AttendancePage /></RequireRole>} />
              <Route path="attendance-analytics" element={<RequireRole allowed={APPROVER_ROLES}><AttendanceAnalyticsPage /></RequireRole>} />
              <Route path="leave" element={<RequireRole allowed={ALL_STAFF}><LeavePage /></RequireRole>} />
              <Route path="approvals" element={<RequireRole allowed={ALL_STAFF}><ApprovalsPage /></RequireRole>} />
              <Route path="approvals/whatsapp" element={<RequireRole allowed={APPROVER_ROLES}><WhatsAppApprovalsPage /></RequireRole>} />
              <Route path="notifications" element={<RequireRole allowed={ALL_STAFF}><NotificationsPage /></RequireRole>} />
              <Route path="ai/command-center" element={<RequireRole allowed={ADMIN_ROLES}><AICommandCenterPage /></RequireRole>} />
              <Route path="ai/workforce" element={<RequireRole allowed={ADMIN_ROLES}><AIWorkforcePage /></RequireRole>} />
              <Route path="ai/activity" element={<RequireRole allowed={ADMIN_ROLES}><AIActivityPage /></RequireRole>} />
              <Route path="ai/economics" element={<RequireRole allowed={ADMIN_ROLES}><AIEconomicsPage /></RequireRole>} />
              <Route path="ai/compliance-health" element={<RequireRole allowed={[...ADMIN_ROLES, "hr_admin", "hr_manager", "payroll_admin"]}><ComplianceHealthPage /></RequireRole>} />
              <Route path="ai/flight-risk" element={<RequireRole allowed={[...HR_WRITE, "manager"]}><AttritionSignalsPage /></RequireRole>} />
              <Route path="ai/agents" element={<RequireRole allowed={[...ADMIN_ROLES, "hr_admin", "payroll_admin"]}><AgentsPage /></RequireRole>} />
              <Route path="ai/agents/payroll-auditor" element={<RequireRole allowed={[...ADMIN_ROLES, "hr_admin", "payroll_admin"]}><PayrollAuditorPage /></RequireRole>} />
              <Route path="ai/agents/hr-desk" element={<RequireRole allowed={[...ADMIN_ROLES, "hr_admin", "payroll_admin"]}><DeskDashboardPage /></RequireRole>} />
              <Route path="ai/agents/onboarding" element={<RequireRole allowed={[...ADMIN_ROLES, "hr_admin", "hr_manager", "payroll_admin"]}><OnboardingAgentPage /></RequireRole>} />
              <Route path="ai/agents/exit" element={<RequireRole allowed={[...ADMIN_ROLES, "hr_admin", "hr_manager", "payroll_admin"]}><ExitAgentPage /></RequireRole>} />
              <Route path="ai/agents/attendance" element={<RequireRole allowed={[...ADMIN_ROLES, "hr_admin", "hr_manager", "payroll_admin"]}><AttendanceAgentPage /></RequireRole>} />
              <Route path="ai/desk/policies" element={<RequireRole allowed={HR_WRITE}><PolicyClausesPage /></RequireRole>} />
              <Route path="ai/desk/ic" element={<RequireRole allowed={ALL_STAFF}><IcReferralsPage /></RequireRole>} />
              <Route path="ai/settings" element={<RequireRole allowed={ADMIN_ROLES}><AiSettingsPage /></RequireRole>} />
              <Route path="ai/tokens" element={<RequireRole allowed={ADMIN_ROLES}><AiTokensPage /></RequireRole>} />
              <Route path="ai/usage" element={<RequireRole allowed={ALL_STAFF}><AiUsagePage /></RequireRole>} />
              <Route path="reports/builder" element={<RequireRole allowed={APPROVER_ROLES}><ReportBuilderPage /></RequireRole>} />
              <Route path="command-center" element={<RequireRole allowed={APPROVER_ROLES}><CommandCenterPage /></RequireRole>} />
              <Route path="reports" element={<RequireRole allowed={APPROVER_ROLES}><ReportsPage /></RequireRole>} />
              <Route path="reports/hub" element={<RequireRole allowed={APPROVER_ROLES}><ReportsHubPage /></RequireRole>} />
              <Route path="reports/md-dashboard" element={<RequireRole allowed={APPROVER_ROLES}><MDDashboardPage /></RequireRole>} />
              <Route path="masters" element={<RequireRole allowed={HR_WRITE}><MasterDataPage /></RequireRole>} />
              <Route path="platform" element={<RequireRole allowed={["superadmin"]}><PlatformPage /></RequireRole>} />
              <Route path="platform/revenue" element={<RequireRole allowed={["superadmin"]}><RevenuePage /></RequireRole>} />
              <Route path="platform/pricing" element={<RequireRole allowed={["superadmin"]}><PlansPricingPage /></RequireRole>} />
              <Route path="roles" element={<RequireRole allowed={["org_admin", "hr_admin"]}><RolesPage /></RequireRole>} />
              <Route path="users" element={<RequireRole allowed={[...ADMIN_ROLES, "hr_admin"]}><UsersPage /></RequireRole>} />
              <Route path="settings/entities" element={<RequireRole allowed={ADMIN_ROLES}><LegalEntitiesPage /></RequireRole>} />
              {/* D1 configurability */}
              <Route path="settings/custom-fields" element={<RequireRole allowed={[...ADMIN_ROLES, "hr_admin"]}><CustomFieldsPage /></RequireRole>} />
              <Route path="settings/approval-workflows" element={<RequireRole allowed={[...HR_WRITE, "payroll_admin"]}><ApprovalWorkflowsPage /></RequireRole>} />
              <Route path="settings/letter-templates" element={<RequireRole allowed={HR_WRITE}><LetterTemplateEditorPage /></RequireRole>} />
              {/* People / Payroll / Benefits / Setup — real screens */}
              <Route path="org-chart" element={<RequireRole allowed={[...HR_WRITE, "manager"]}><OrgChartPage /></RequireRole>} />
              <Route path="garnishments" element={<RequireRole allowed={PAYROLL_ACCESS}><GarnishmentsPage /></RequireRole>} />
              <Route path="stat-holidays" element={<RequireRole allowed={ALL_STAFF}><HolidaysPage /></RequireRole>} />
              <Route path="benefits" element={<RequireRole allowed={HR_WRITE}><BenefitsPage /></RequireRole>} />
              <Route path="enrollment" element={<RequireRole allowed={ALL_STAFF}><EnrollmentPage /></RequireRole>} />
              <Route path="carriers" element={<RequireRole allowed={HR_WRITE}><CarriersPage /></RequireRole>} />
              <Route path="retirement" element={<RequireRole allowed={HR_WRITE}><RetirementPage /></RequireRole>} />
              <Route path="company-settings" element={<RequireRole allowed={ADMIN_ROLES}><CompanySettingsPage /></RequireRole>} />
              <Route path="settings" element={<SettingsHubPage />} />{/* U4: page redirects non-admin/HR roles to / */}
              {/* Self-service */}
              <Route path="my-profile" element={<RequireRole allowed={ALL_STAFF}><MyProfilePage /></RequireRole>} />
              <Route path="my-pay" element={<RequireRole allowed={ALL_STAFF}><MyPayPage /></RequireRole>} />
              <Route path="my-benefits" element={<RequireRole allowed={ALL_STAFF}><MyBenefitsPage /></RequireRole>} />
              <Route path="my-time" element={<RequireRole allowed={ALL_STAFF}><MyTimePage /></RequireRole>} />
              <Route path="my-requests" element={<RequireRole allowed={ALL_STAFF}><MyRequestsPage /></RequireRole>} />
              {/* Stream B (lifecycle) */}
              <Route path="salary-revisions" element={<RequireRole allowed={[...PAYROLL_ACCESS, "hr_manager"]}><SalaryRevisionsPage /></RequireRole>} />
              <Route path="career-changes" element={<RequireRole allowed={HR_WRITE}><CareerChangesPage /></RequireRole>} />
              <Route path="probation" element={<RequireRole allowed={[...HR_WRITE, "manager"]}><ProbationPage /></RequireRole>} />
              <Route path="fixed-term" element={<RequireRole allowed={[...HR_WRITE, "manager"]}><FixedTermPage /></RequireRole>} />
              <Route path="letters" element={<RequireRole allowed={HR_WRITE}><LettersPage /></RequireRole>} />
              <Route path="employee-history" element={<RequireRole allowed={[...HR_WRITE, "payroll_admin"]}><EmployeeHistoryPage /></RequireRole>} />
              <Route path="employee-history/:id" element={<RequireRole allowed={[...HR_WRITE, "payroll_admin"]}><EmployeeHistoryPage /></RequireRole>} />
              <Route path="my-compensation" element={<RequireRole allowed={ALL_STAFF}><MyCompensationPage /></RequireRole>} />
              <Route path="my-letters" element={<RequireRole allowed={ALL_STAFF}><MyLettersPage /></RequireRole>} />
              <Route path="biometric-import" element={<RequireRole allowed={HR_WRITE}><BiometricImportPage /></RequireRole>} />
              <Route path="plan-billing" element={<RequireRole allowed={ADMIN_ROLES}><PlanBillingPage /></RequireRole>} />
              <Route path="security/account" element={<RequireRole allowed={ALL_STAFF}><AccountSecurityPage /></RequireRole>} />
              <Route path="security/sessions" element={<RequireRole allowed={ALL_STAFF}><SessionAuditPage /></RequireRole>} />
              {/* Off-cycle payroll (real backend) */}
              <Route path="off-cycle" element={<RequireRole allowed={PAYROLL_ACCESS}><OffCyclePage /></RequireRole>} />
              <Route path="gl-export" element={<RequireRole allowed={PAYROLL_ACCESS}><GlExportPage /></RequireRole>} />
              {/* Stream G6: Canada completeness + migration kit */}
              <Route path="payroll/canada/statutory-values" element={<RequireRole allowed={PAYROLL_ACCESS}><CaStatutoryValuesPage /></RequireRole>} />
              <Route path="payroll/canada/remittances" element={<RequireRole allowed={PAYROLL_ACCESS}><CaRemittancesPage /></RequireRole>} />
              <Route path="payroll/canada/stat-holiday" element={<RequireRole allowed={PAYROLL_ACCESS}><CaStatHolidayPayPage /></RequireRole>} />
              <Route path="payroll/canada/accruals" element={<RequireRole allowed={PAYROLL_ACCESS}><CaEmployerAccrualsPage /></RequireRole>} />
              <Route path="payroll/canada/contractors" element={<RequireRole allowed={PAYROLL_ACCESS}><CaContractorsT4APage /></RequireRole>} />
              <Route path="payroll/canada/year-end" element={<RequireRole allowed={PAYROLL_ACCESS}><CaYearEndChecklistPage /></RequireRole>} />
              <Route path="migration/parallel-run" element={<RequireRole allowed={[...HR_WRITE, "payroll_admin"]}><ParallelRunPage /></RequireRole>} />
              <Route path="migration/checklist" element={<RequireRole allowed={[...HR_WRITE, "payroll_admin"]}><MigrationChecklistPage /></RequireRole>} />
              <Route path="migration" element={<Navigate to="/migration/canada" replace />} />
              <Route path="migration/canada" element={<RequireRole allowed={[...HR_WRITE, "payroll_admin"]}><CanadaImportPage /></RequireRole>} />
              {/* Functional workflow screens */}
              <Route path="offboarding" element={<RequireRole allowed={HR_WRITE}><OffboardingPage /></RequireRole>} />
              <Route path="exits" element={<RequireRole allowed={[...HR_WRITE, "payroll_admin"]}><ExitsPage /></RequireRole>} />
              <Route path="exits/team" element={<RequireRole allowed={[...HR_WRITE, "manager"]}><ExitsPage scope="team" /></RequireRole>} />
              <Route path="exits/former" element={<RequireRole allowed={[...HR_WRITE, "payroll_admin"]}><FormerEmployeesPage /></RequireRole>} />
              <Route path="exits/:id" element={<RequireRole allowed={ALL_STAFF}><ExitCasePage /></RequireRole>} />
              <Route path="my-resignation" element={<RequireRole allowed={ALL_STAFF}><MyResignationPage /></RequireRole>} />
              <Route path="timesheets" element={<RequireRole allowed={ALL_STAFF}><TimesheetsPage /></RequireRole>} />
              <Route path="scheduling" element={<RequireRole allowed={[...HR_WRITE, "manager"]}><SchedulingPage /></RequireRole>} />
              <Route path="scheduling/rotations" element={<RequireRole allowed={[...HR_WRITE, "manager"]}><ShiftRotationsPage /></RequireRole>} />
              <Route path="my-shifts" element={<RequireRole allowed={ALL_STAFF}><MyShiftsPage /></RequireRole>} />
              <Route path="time-clock" element={<RequireRole allowed={ALL_STAFF}><TimeClockPage /></RequireRole>} />
              <Route path="absence-calendar" element={<RequireRole allowed={ALL_STAFF}><AbsenceCalendarPage /></RequireRole>} />
              {/* Stream D: hiring + performance (old mock URLs redirect to the real pages) */}
              <Route path="hiring/my-interviews" element={<RequireRole allowed={ALL_STAFF}><MyInterviewsPage /></RequireRole>} />
              <Route path="referrals" element={<RequireRole allowed={ALL_STAFF}><ReferralsPage /></RequireRole>} />   {/* D2 */}
              <Route path="hiring/:tab?" element={<RequireRole allowed={[...HR_WRITE, "manager"]}><HiringPage /></RequireRole>} />
              <Route path="performance/:tab?" element={<RequireRole allowed={ALL_STAFF}><PerformancePage /></RequireRole>} />
              {/* D5: compensation planning cycles */}
              <Route path="comp/cycles" element={<RequireRole allowed={HR_WRITE}><CompCyclesPage /></RequireRole>} />
              <Route path="comp/cycles/:id/:tab?" element={<RequireRole allowed={[...HR_WRITE, "manager"]}><CompCyclePage /></RequireRole>} />
              <Route path="comp/settings" element={<RequireRole allowed={HR_WRITE}><CompSettingsPage /></RequireRole>} />
              <Route path="comp/worksheets" element={<RequireRole allowed={[...HR_WRITE, "manager"]}><MyWorksheetsPage /></RequireRole>} />
              <Route path="comp/worksheets/:id" element={<RequireRole allowed={[...HR_WRITE, "manager"]}><MyWorksheetPage /></RequireRole>} />
              <Route path="recruiting" element={<Navigate to="/hiring/candidates" replace />} />
              <Route path="job-postings" element={<Navigate to="/hiring/postings" replace />} />
              <Route path="goals" element={<Navigate to="/performance/goals" replace />} />
              <Route path="comp-planning" element={<Navigate to="/performance/cycles" replace />} />
              <Route path="learning" element={<Navigate to="/engage/learning" replace />} />
              {/* Stream G4: phone-first self-service + engagement */}
              <Route path="mobile/home" element={<RequireRole allowed={ALL_STAFF}><MobileHomePage /></RequireRole>} />
              <Route path="mobile/punch" element={<RequireRole allowed={ALL_STAFF}><MobilePunchPage /></RequireRole>} />
              <Route path="mobile/leave" element={<RequireRole allowed={ALL_STAFF}><MobileLeavePage /></RequireRole>} />
              <Route path="mobile/payslips" element={<RequireRole allowed={ALL_STAFF}><MobilePayslipsPage /></RequireRole>} />
              <Route path="mobile/team-punches" element={<RequireRole allowed={APPROVER_ROLES}><TeamPunchesPage /></RequireRole>} />
              <Route path="mobile/sites" element={<RequireRole allowed={[...HR_WRITE, "manager"]}><WorkSitesPage /></RequireRole>} />
              <Route path="mobile/translations" element={<RequireRole allowed={ALL_STAFF}><I18nReportPage /></RequireRole>} />
              <Route path="engage/helpdesk" element={<RequireRole allowed={ALL_STAFF}><HelpdeskPage /></RequireRole>} />
              <Route path="engage/surveys" element={<RequireRole allowed={ALL_STAFF}><SurveysPage /></RequireRole>} />
              <Route path="engage/kudos" element={<RequireRole allowed={ALL_STAFF}><KudosPage /></RequireRole>} />
              {/* 2026-10-16 employee portal batch 3 */}
              <Route path="engage/feed" element={<RequireRole allowed={ALL_STAFF}><FeedPage /></RequireRole>} />
              <Route path="raise-request" element={<RequireRole allowed={ALL_STAFF}><RaiseRequestPage /></RequireRole>} />
              <Route path="request-types" element={<RequireRole allowed={HR_WRITE}><RequestTypesAdminPage /></RequireRole>} />
              <Route path="tasks" element={<RequireRole allowed={ALL_STAFF}><TasksPage /></RequireRole>} />
              <Route path="documents" element={<RequireRole allowed={ALL_STAFF}><DocumentCentrePage /></RequireRole>} />
              <Route path="engage/learning" element={<RequireRole allowed={ALL_STAFF}><EngLearningPage /></RequireRole>} />
              <Route path="engage/assets" element={<RequireRole allowed={[...HR_WRITE, "manager", "payroll_admin"]}><AssetsPage /></RequireRole>} />
              <Route path="engage/my-assets" element={<RequireRole allowed={ALL_STAFF}><MyAssetsPage /></RequireRole>} />
              {/* Stream D7: LMS depth · engagement depth · headcount planning · people analytics */}
              <Route path="learning/my" element={<RequireRole allowed={ALL_STAFF}><MyLearningPage /></RequireRole>} />
              <Route path="learning/admin" element={<RequireRole allowed={[...HR_WRITE, "manager"]}><LearningAdminPage /></RequireRole>} />
              <Route path="engage/surveys/admin" element={<RequireRole allowed={HR_WRITE}><SurveysAdminPage /></RequireRole>} />
              <Route path="engage/insights" element={<RequireRole allowed={[...HR_WRITE, "manager"]}><ManagerInsightsPage /></RequireRole>} />
              <Route path="analytics/positions" element={<RequireRole allowed={[...HR_WRITE, "manager", "payroll_admin"]}><PositionsPage /></RequireRole>} />
              <Route path="analytics/people" element={<RequireRole allowed={[...HR_WRITE, "manager", "payroll_admin"]}><PeopleAnalyticsPage /></RequireRole>} />
              <Route path="succession" element={<RequireRole allowed={HR_WRITE}><SuccessionPage /></RequireRole>} />
              <Route path="msa" element={<RequireRole allowed={HR_WRITE}><MsaPage /></RequireRole>} />
              <Route path="compliance-center" element={<RequireRole allowed={APPROVER_ROLES}><ComplianceCenterPage /></RequireRole>} />
              <Route path="wcb" element={<RequireRole allowed={HR_WRITE}><WcbPage /></RequireRole>} />
              <Route path="safety" element={<RequireRole allowed={HR_WRITE}><SafetyPage /></RequireRole>} />
              <Route path="audit-log" element={<RequireRole allowed={ADMIN_ROLES}><AuditLogPage /></RequireRole>} />
              <Route path="privacy" element={<RequireRole allowed={ALL_STAFF}><PrivacyCenterPage /></RequireRole>} />
              <Route path="data-requests" element={<RequireRole allowed={ALL_STAFF}><DataRequestsPage /></RequireRole>} />
              <Route path="benchmarking" element={<RequireRole allowed={APPROVER_ROLES}><BenchmarkingPage /></RequireRole>} />
              <Route path="integrations" element={<RequireRole allowed={ADMIN_ROLES}><IntegrationsPage /></RequireRole>} />
              <Route path="me/chat-link" element={<RequireRole allowed={ALL_STAFF}><ChatLinkPage /></RequireRole>} />
              <Route path="m/:slug" element={<ModulePage />} />
              <Route path="ops/:resource" element={<RequireRole allowed={[...HR_WRITE, "manager", "payroll_admin"]}><OpsResourcePage /></RequireRole>} />
              <Route path="psa/:tab?" element={<RequireRole allowed={[...HR_WRITE, "manager", "payroll_admin"]}><PsaPage /></RequireRole>} />
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Suspense>
        <SplashGate />
      </BrowserRouter>
    </QueryClientProvider>
  );
}
