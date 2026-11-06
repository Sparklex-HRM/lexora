import { useMemo, useState, type ComponentType, type ReactNode } from "react";
import { Link, Navigate, useParams, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import {
  ArrowLeft, ChevronLeft, ChevronRight, Pencil, TrendingUp, ArrowUpRight, FileSignature, UserRoundX, KeyRound,
  Briefcase, Mail, Phone, MapPin, CalendarDays, Building2, UserCircle2, Lock,
} from "lucide-react";
import { Badge, Spinner } from "../../components/ui";
import { profileApi, type Employee, type EmployeeProfile } from "../../api/endpoints";
import { COUNTRY_NAME, REGION_NAMES } from "../../api/regions";
import { useUserRole, useAuthStore, HR_WRITE, PAYROLL_ACCESS, ADMIN_ROLES } from "../../store/authStore";
import EmployeeForm from "./EmployeeForm";
import StartExitModal from "../exit/StartExitModal";
import EntityTransferModal from "../../components/EntityTransferModal";   // D4: legal-entity transfer
import { entitiesApi, entityName } from "../../api/entities";
import { Avatar, STATUS_TEXT, fmtDate, titleCase } from "./profile/_bits";
import OverviewTab from "./profile/OverviewTab";
import JobTab from "./profile/JobTab";
import PayTab from "./profile/PayTab";
import StatutoryTab from "./profile/StatutoryTab";
import TimeLeaveTab from "./profile/TimeLeaveTab";
import DocumentsTab from "./profile/DocumentsTab";
import AssetsTab from "./profile/AssetsTab";
import LettersTab from "./profile/LettersTab";
import TimelineTab from "./profile/TimelineTab";
import { CustomFieldsSection, useCustomFieldDefs } from "../../components/CustomFieldsSection";   // D1
import { can } from "../../config/permissions";   // 2026-10-26: UI gates = permission + role (custom roles)

function CustomTab({ profile }: TabProps) {
  const defs = useCustomFieldDefs("employee");
  if (defs.data && !defs.data.items.some((d) => d.active)) {
    return <div className="card text-sm text-gray-500">No custom fields to show here — either none are defined yet (an org admin or HR head adds them under Settings → Custom fields) or the ones that exist are not visible to your role.</div>;
  }
  return <div className="card"><CustomFieldsSection entity="employee" id={profile.employee.id} mode={profile.permissions.can_edit || profile.permissions.scope === "self" ? "edit" : "view"} title={null} /></div>;
}

/** Employee 360 — /employees/:id (UUID or employee number, e.g. /employees/EMP-0004).
 *  One aggregated read (profileApi.get) feeds the header and most tabs; the tabs that
 *  live in other services (time, leave, payroll) fetch on their own and show a plain
 *  sentence when the API says no. Tabs are URL state: ?tab=pay */

export interface TabProps { profile: EmployeeProfile; refresh: () => void; }
type TabDef = { key: string; label: string; show: (p: EmployeeProfile) => boolean; Comp: ComponentType<TabProps> };

const TABS: TabDef[] = [
  { key: "overview", label: "Overview", show: () => true, Comp: OverviewTab },
  { key: "job", label: "Job", show: () => true, Comp: JobTab },
  { key: "pay", label: "Pay", show: (p) => p.permissions.can_see_pay, Comp: PayTab },
  { key: "statutory", label: "Statutory", show: (p) => p.permissions.can_see_pay, Comp: StatutoryTab },
  { key: "time", label: "Time & Leave", show: () => true, Comp: TimeLeaveTab },
  { key: "documents", label: "Documents", show: (p) => p.permissions.can_see_documents, Comp: DocumentsTab },
  { key: "assets", label: "Assets", show: (p) => p.permissions.can_see_assets, Comp: AssetsTab },
  { key: "letters", label: "Letters", show: (p) => p.letters !== null && p.letters !== undefined, Comp: LettersTab },   // HR + self only (2026-10-21)
  { key: "custom", label: "Custom fields", show: () => true, Comp: CustomTab },
  { key: "timeline", label: "Timeline", show: () => true, Comp: TimelineTab },
];

export default function EmployeeProfilePage() {
  const { id = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const qc = useQueryClient();
  const role = useUserRole() || "";
  const user = useAuthStore((s) => s.user);
  const [editing, setEditing] = useState(false);
  const [exiting, setExiting] = useState(false);
  const [entityTransfer, setEntityTransfer] = useState(false);   // D4
  const { data: entData } = useQuery({ queryKey: ["entities", "headcount"], queryFn: entitiesApi.headcount, staleTime: 60_000 });

  const key = ["employee-profile", id];
  const { data: profile, isLoading, error } = useQuery({ queryKey: key, queryFn: () => profileApi.get(id), enabled: !!id, retry: false });
  const refresh = () => { qc.invalidateQueries({ queryKey: ["employee-profile"] }); qc.invalidateQueries({ queryKey: ["employees"] }); };

  const tabs = useMemo(() => (profile ? TABS.filter((t) => t.show(profile)) : []), [profile]);
  const tabKey = params.get("tab") || "overview";
  const active = tabs.find((t) => t.key === tabKey) || tabs[0];
  const setTab = (k: string) => { const p = new URLSearchParams(params); p.set("tab", k); setParams(p, { replace: true }); };

  if (!id) return <Navigate to="/employees" replace />;
  if (isLoading) return <div className="grid place-items-center py-24"><Spinner className="h-8 w-8" /></div>;

  const status = (error as any)?.response?.status;
  if (error || !profile) {
    const noAccess = status === 404 || status === 403;
    return (
      <div className="max-w-xl mx-auto mt-10 card text-center">
        <Lock className="w-8 h-8 text-gray-300 mx-auto mb-2" />
        <h2 className="font-display font-semibold text-gray-800">{noAccess ? "This employee record is not available to you" : "Could not load the employee"}</h2>
        <p className="text-sm text-gray-500 mt-1">
          {noAccess ? "Either the number is wrong, or you may only see your own record and the people who report to you." : "Please try again in a moment."}
        </p>
        <div className="mt-4 flex justify-center gap-2">
          <Link to={role === "employee" || role === "payroll_admin" ? "/my-profile" : "/employees"} className="btn-secondary text-xs flex items-center gap-1.5"><ArrowLeft className="w-3.5 h-3.5" /> Back</Link>
        </div>
      </div>
    );
  }

  const e = profile.employee;
  const perm = profile.permissions;
  const country = (e.address_country || e.country || "").toUpperCase();
  const region = REGION_NAMES.CA[e.province_of_employment] || e.province_of_employment;
  const canWrite = can(user, "people.edit", HR_WRITE);
  const canEdit = perm.can_edit;
  const canRevise = can(user, ["compensation.create", "compensation.edit"], [...PAYROLL_ACCESS, "hr_manager"]);
  const canCareer = can(user, ["career.create", "career.edit"], HR_WRITE);          // career changes are HR's, not the pay office's (2026-10-21)
  const canLogin = can(user, "users.view", [...ADMIN_ROLES, "hr_admin"]);
  const left = e.status === "terminated";
  const login = profile.logins?.[0];
  const Comp = active?.Comp;
  const neighbour = (n: { employee_number: string } | null) => (n ? `/employees/${encodeURIComponent(n.employee_number)}?tab=${active?.key || "overview"}` : "");

  return (
    <div className="min-w-0">
      {/* top line: back + prev/next */}
      <div className="flex items-center justify-between gap-3 mb-3">
        <Link to={perm.scope === "self" ? "/my-profile" : "/employees"} className="text-xs text-gray-500 hover:text-primary-600 flex items-center gap-1">
          <ArrowLeft className="w-3.5 h-3.5" /> {perm.scope === "self" ? "My profile" : "All employees"}
        </Link>
        {perm.scope !== "self" && (
          <div className="flex items-center gap-1 text-xs text-gray-500">
            {profile.prev ? <Link to={neighbour(profile.prev)} className="btn-secondary !py-1 !px-2 flex items-center gap-1" title="Previous employee"><ChevronLeft className="w-3.5 h-3.5" />{profile.prev.employee_number}</Link>
              : <span className="btn-secondary !py-1 !px-2 opacity-40 flex items-center gap-1"><ChevronLeft className="w-3.5 h-3.5" />First</span>}
            {profile.next ? <Link to={neighbour(profile.next)} className="btn-secondary !py-1 !px-2 flex items-center gap-1" title="Next employee">{profile.next.employee_number}<ChevronRight className="w-3.5 h-3.5" /></Link>
              : <span className="btn-secondary !py-1 !px-2 opacity-40 flex items-center gap-1">Last<ChevronRight className="w-3.5 h-3.5" /></span>}
          </div>
        )}
      </div>

      {/* header card */}
      <div className="card mb-4">
        <div className="flex flex-col lg:flex-row lg:items-start gap-4">
          <div className="flex items-start gap-4 min-w-0 flex-1">
            <Avatar first={e.first_name} last={e.last_name} seed={e.employee_number || e.id} />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <h1 className="font-display text-xl font-bold text-gray-800 truncate">{e.first_name} {e.middle_name ? `${e.middle_name} ` : ""}{e.last_name}</h1>
                {e.preferred_name && <span className="text-sm text-gray-500">“{e.preferred_name}”</span>}
                <Badge tone={e.status}>{STATUS_TEXT[e.status] || e.status}</Badge>
                {e.is_self && <span className="badge-teal">You</span>}
                {profile.career?.employment_status === "probation" && <span className="badge-amber">On probation</span>}
              </div>
              <div className="text-sm text-gray-600 mt-0.5 flex flex-wrap items-center gap-x-1.5">
                <span className="font-mono text-[13px] text-gray-800">{e.employee_number}</span>
                <span className="text-gray-300">·</span>
                <span className="flex items-center gap-1"><Briefcase className="w-3.5 h-3.5 text-gray-400" />{e.job_title || "No designation"}</span>
                <span className="text-gray-300">·</span><span>{e.department || "No department"}</span>
                {(profile.career?.grade || e.grade) && <><span className="text-gray-300">·</span><span>Grade {profile.career?.grade || e.grade}</span></>}
              </div>
              <div className="mt-2 grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-x-6 gap-y-1 text-xs text-gray-600">
                <Line icon={UserCircle2} label="Reports to">
                  {profile.manager ? <Link to={`/employees/${encodeURIComponent(profile.manager.employee_number)}`} className="text-primary-600 hover:underline font-medium">{profile.manager.name}</Link>
                    : e.manager_name ? <span>{e.manager_name} <span className="badge-amber ml-1">not linked</span></span> : <span className="text-gray-400">No manager</span>}
                </Line>
                <Line icon={Building2} label="Branch / location">{[e.branch_name, e.work_location].filter(Boolean).join(" · ") || <span className="text-gray-400">Not set</span>}</Line>
                {(entData?.entities?.length || 0) > 1 && <Line icon={Building2} label="Legal entity">{entityName(entData?.entities, e.entity_id) || <span className="text-gray-400">Default entity</span>}</Line>}
                <Line icon={MapPin} label="Country">{country ? `${COUNTRY_NAME[country] || country} · ${region || "—"}` : <span className="badge-red">Not set</span>}</Line>
                <Line icon={CalendarDays} label={left ? "Joined → left" : "Joined"}>
                  {fmtDate(e.hire_date)}{left && ` → ${fmtDate(e.termination_date)}`}{profile.tenure.text && <span className="text-gray-400"> · {profile.tenure.text}</span>}
                  <span className="text-gray-400"> · {titleCase(e.employment_type)}</span>
                </Line>
                <Line icon={Mail} label="Work e-mail">{e.work_email || e.email ? <a className="hover:underline" href={`mailto:${e.work_email || e.email}`}>{e.work_email || e.email}</a> : <span className="text-gray-400">None</span>}</Line>
                <Line icon={Phone} label="Mobile">{e.phone || <span className="text-gray-400">None</span>}</Line>
              </div>
            </div>
          </div>

          {/* action bar (role-gated like Employees / EmployeeForm) */}
          <div className="flex flex-wrap lg:grid lg:grid-cols-2 gap-1.5 lg:w-80 shrink-0 self-start">
            {canEdit && <button onClick={() => setEditing(true)} className="btn-primary text-xs flex items-center justify-center gap-1.5"><Pencil className="w-3.5 h-3.5" /> Edit</button>}
            {canRevise && !left && <Link to={`/salary-revisions?employee=${encodeURIComponent(e.id)}`} className="btn-secondary text-xs flex items-center justify-center gap-1.5"><TrendingUp className="w-3.5 h-3.5" /> Revise salary</Link>}
            {canCareer && !left && <Link to={`/career-changes?employee=${encodeURIComponent(e.id)}`} className="btn-secondary text-xs flex items-center justify-center gap-1.5"><ArrowUpRight className="w-3.5 h-3.5" /> Promote / Transfer</Link>}
            {canCareer && <Link to={`/letters?employee=${encodeURIComponent(e.id)}`} className="btn-secondary text-xs flex items-center justify-center gap-1.5"><FileSignature className="w-3.5 h-3.5" /> Issue letter</Link>}
            {can(user, "people.edit", HR_WRITE) && !left && (entData?.entities?.length || 0) > 1 && <button onClick={() => setEntityTransfer(true)} className="btn-secondary text-xs flex items-center justify-center gap-1.5"><Building2 className="w-3.5 h-3.5" /> Change entity</button>}
            {perm.can_start_exit && !left && <button onClick={() => setExiting(true)} className="btn-secondary text-xs flex items-center justify-center gap-1.5 !text-red-600"><UserRoundX className="w-3.5 h-3.5" /> Start exit</button>}
            {canLogin && (
              <Link to={`/users?q=${encodeURIComponent(e.employee_number)}`} className="btn-secondary text-xs flex items-center justify-center gap-1.5" title={login ? `${login.email} · ${login.role_label} · ${login.status}` : "No login yet"}>
                <KeyRound className="w-3.5 h-3.5" /> Login{login ? <span className={login.status === "active" ? "badge-green" : login.status === "disabled" ? "badge-red" : "badge-amber"}>{login.status}</span> : <span className="badge-amber">none</span>}
              </Link>
            )}
          </div>
        </div>
      </div>

      {/* tabs (scroll sideways on a phone) */}
      <div className="border-b border-surface-border mb-4 -mx-1 px-1 overflow-x-auto">
        <div className="flex gap-1 min-w-max">
          {tabs.map((t) => (
            <button key={t.key} onClick={() => setTab(t.key)} data-tab={t.key}
              className={`px-3 py-2 text-sm font-medium whitespace-nowrap border-b-2 -mb-px transition-colors ${active?.key === t.key ? "border-primary-600 text-primary-600" : "border-transparent text-gray-500 hover:text-gray-800"}`}>
              {t.label}
              {t.key === "documents" && profile.documents_count ? <span className="ml-1.5 text-[10px] bg-gray-100 text-gray-600 rounded-full px-1.5 py-0.5">{profile.documents_count}</span> : null}
              {t.key === "letters" && profile.letters_count ? <span className="ml-1.5 text-[10px] bg-gray-100 text-gray-600 rounded-full px-1.5 py-0.5">{profile.letters_count}</span> : null}
              {t.key === "assets" && profile.assets?.length ? <span className="ml-1.5 text-[10px] bg-gray-100 text-gray-600 rounded-full px-1.5 py-0.5">{profile.assets.length}</span> : null}
            </button>
          ))}
        </div>
      </div>

      <div data-tab-panel={active?.key}>{Comp && <Comp profile={profile} refresh={refresh} />}</div>

      {editing && <EmployeeForm employee={e as Employee} onClose={() => { setEditing(false); refresh(); }} />}
      {entityTransfer && <EntityTransferModal employeeId={e.id} employeeName={`${e.first_name} ${e.last_name}`} onClose={() => setEntityTransfer(false)} />}
      {exiting && (
        <StartExitModal employeeId={e.id} lockEmployee defaultImmediate={canWrite}
          onClose={() => setExiting(false)}
          onCreated={(c) => { setExiting(false); refresh(); toast.success(`Open Exits → ${c.case_number} to continue`); }} />
      )}
    </div>
  );
}

function Line({ icon: Icon, label, children }: { icon: ComponentType<{ className?: string }>; label: string; children: ReactNode }) {
  return (
    <div className="flex items-start gap-1.5 min-w-0">
      <Icon className="w-3.5 h-3.5 text-gray-400 mt-0.5 shrink-0" />
      <span className="text-gray-400 shrink-0">{label}:</span>
      <span className="text-gray-700 min-w-0 truncate">{children}</span>
    </div>
  );
}
