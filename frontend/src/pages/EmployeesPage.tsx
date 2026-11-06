import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import {
  Users, Plus, Pencil, UserPlus, UserRoundX, Columns3, Rows3, Rows4, Layers, X, Download, Copy,
  UserCog, Building2, FileSignature, Check, Info,
} from "lucide-react";
import { PageHeader, Spinner, Badge } from "../components/ui";
import DataGrid, { GridColumn, GridDensity, toCsv, downloadCsv } from "../components/DataGrid";
import { avatarClass, initialsOf } from "../components/CommandPalette";
import { employeeApi, mastersApi, Employee, money, freqLabel, EMPLOYMENT_TYPES } from "../api/endpoints";
import { COUNTRY_NAME, REGION_NAMES, regionOptions } from "../api/regions";
import { entitiesApi, entityName } from "../api/entities";   // D4
import { useUserRole, useAuthStore, HR_WRITE } from "../store/authStore";
import EmployeeForm from "./employee/EmployeeForm";
import StartExitModal from "./exit/StartExitModal";
import PeopleDirectory from "./employee/PeopleDirectory";
import { useCustomGrid, displayValue } from "../components/CustomFieldsSection";   // D1: custom-field columns
import { can, useCan, viewRoleFor } from "../config/permissions";   // 2026-10-26: UI gates = permission + role (custom roles)

const STATUS_TEXT: Record<string, string> = { onboarding: "Onboarding", active: "Active", on_leave: "On leave", terminated: "Left" };

// ── Saved views (the rule of each one is also the tooltip) ──────────────────
type ViewKey = "all" | "active" | "new" | "probation" | "nomanager" | "left";
const DAY = 86_400_000;
const daysSince = (d?: string | null) => d ? Math.floor((Date.now() - new Date(d).getTime()) / DAY) : NaN;
const VIEWS: { key: ViewKey; label: string; tip: string; test: (e: Employee) => boolean }[] = [
  { key: "all", label: "All", tip: "Everyone on the books, including people who have left.", test: () => true },
  { key: "active", label: "Active", tip: "Status is Active.", test: (e) => e.status === "active" },
  { key: "new", label: "New joiners", tip: "Joined in the last 90 days.", test: (e) => daysSince(e.hire_date) <= 90 },
  { key: "probation", label: "Probation", tip: "Status is Onboarding, or joined less than 6 months ago and has not left.",
    test: (e) => e.status === "onboarding" || (e.status !== "terminated" && daysSince(e.hire_date) < 183) },
  { key: "nomanager", label: "No manager", tip: "No linked manager — approvals have nobody to go to.", test: (e) => !e.manager_id },
  { key: "left", label: "Left", tip: "Status is Left (exit completed).", test: (e) => e.status === "terminated" },
];

// ── Extra one-click chips from the summary strip ────────────────────────────
type ChipKey = "" | "joined_month" | "left_year" | "missing_docs";
const sameMonth = (d?: string | null) => { if (!d) return false; const x = new Date(d), n = new Date(); return x.getFullYear() === n.getFullYear() && x.getMonth() === n.getMonth(); };
const thisYear = (d?: string | null) => !!d && new Date(d).getFullYear() === new Date().getFullYear();
const hasDocFields = (e: Employee) => e.has_bank_account !== undefined || e.bank_account_masked !== undefined;
const missingDocs = (e: Employee) =>
  e.has_bank_account === false || (e.has_bank_account === undefined && e.bank_account_masked === null);
const CHIP_TEST: Record<Exclude<ChipKey, "">, (e: Employee) => boolean> = {
  joined_month: (e) => sameMonth(e.hire_date),
  left_year: (e) => e.status === "terminated" && thisYear((e as any).termination_date || (e as any).last_working_day || (e as any).updated_at),
  missing_docs: missingDocs,
};

// ── Column catalogue (keys persist in localStorage — never PHI) ─────────────
const ALL_COLS: { key: string; label: string; always?: boolean }[] = [
  { key: "name", label: "Name", always: true }, { key: "employee_number", label: "Number" }, { key: "job_title", label: "Job title" },
  { key: "department", label: "Department" }, { key: "grade", label: "Grade" }, { key: "manager_name", label: "Reports to" },
  { key: "location", label: "Location" }, { key: "hire_date", label: "Joined" }, { key: "pay", label: "Pay" },
  { key: "status", label: "Status" }, { key: "actions", label: "Actions", always: true },
];
type GroupKey = "" | "department" | "location" | "manager";

// localStorage only ever holds view / filter / column / density keys — no names, no numbers.
const LS = { view: "lx-emp-view", filters: "lx-emp-filters", cols: "lx-emp-cols", density: "lx-emp-density", group: "lx-emp-group" };
function lsGet<T>(k: string, fallback: T): T { try { const v = localStorage.getItem(k); return v ? (JSON.parse(v) as T) : fallback; } catch { return fallback; } }
function lsSet(k: string, v: unknown) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* ignore */ } }

// ── Small helpers ───────────────────────────────────────────────────────────
export function tenure(hire?: string | null): string {
  if (!hire) return "";
  const h = new Date(hire), n = new Date();
  if (isNaN(h.getTime())) return "";
  let m = (n.getFullYear() - h.getFullYear()) * 12 + (n.getMonth() - h.getMonth());
  if (n.getDate() < h.getDate()) m -= 1;
  if (m < 0) return "starts soon";
  const y = Math.floor(m / 12), mm = m % 12;
  return y ? `${y} y ${mm} m` : `${mm} m`;
}
const emailOf = (e: Employee) => (e as any).work_email || e.email || "";

export default function EmployeesPage() {
  // Employee role: read-only colleague directory (work facts only) — no admin columns, actions or bulk bar.
  const role = useUserRole();
  // a custom role carrying people.view and acting as an office role / manager gets the people list (2026-10-26)
  const office = useCan("people.view", ["superadmin", "org_admin", "hr_admin", "hr_manager", "payroll_admin", "manager"]);
  // accountant gets the work-facts directory too — global search links people there (2026-10-20)
  if (!office && (role === "employee" || role === "accountant")) return <PeopleDirectory />;
  return <EmployeesAdminPage />;
}

function EmployeesAdminPage() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const user = useAuthStore((s) => s.user);
  const role = viewRoleFor(user, "people.view", ["superadmin", "org_admin", "hr_admin", "hr_manager", "payroll_admin", "manager"]);
  const canWrite = can(user, ["people.create", "people.edit"], HR_WRITE);
  const canEdit = canWrite || can(user, "people.edit", ["payroll_admin"]);
  // Pay follows the same line as editing: HR / org admin / payroll see it; managers and employees never do —
  // and a custom role only with data.pay (the server blanks the fields otherwise, 2026-10-26).
  const canSeePay = can(user, "data.pay", [...HR_WRITE, "payroll_admin"]);
  const [params, setParams] = useSearchParams();
  const [editing, setEditing] = useState<Employee | null>(null);
  const [adding, setAdding] = useState(false);
  // Separation goes through the exit process (real last working day + reason), never a
  // bare status stamp with today's date.
  const [exitFor, setExitFor] = useState<Employee | null>(null);

  // ── Persisted view state ──
  const [view, setViewState] = useState<ViewKey>(() => lsGet<ViewKey>(LS.view, "all"));
  const [chip, setChip] = useState<ChipKey>("");
  const [filters, setFilters] = useState(() => lsGet(LS.filters, { dept: "", province: "", status: "", type: "", manager: "", entity: "" }));
  const [hidden, setHidden] = useState<Record<string, boolean>>(() => lsGet(LS.cols, {}));
  const [density, setDensity] = useState<GridDensity>(() => lsGet<GridDensity>(LS.density, "comfortable"));
  const [groupKey, setGroupKey] = useState<GroupKey>(() => lsGet<GroupKey>(LS.group, ""));
  useEffect(() => lsSet(LS.view, view), [view]);
  useEffect(() => lsSet(LS.filters, filters), [filters]);
  useEffect(() => lsSet(LS.cols, hidden), [hidden]);
  useEffect(() => lsSet(LS.density, density), [density]);
  useEffect(() => lsSet(LS.group, groupKey), [groupKey]);
  const setView = (v: ViewKey) => { setViewState(v); setChip(""); };
  const setF = (k: keyof typeof filters, v: string) => setFilters((f) => ({ ...f, [k]: v }));
  const [colsOpen, setColsOpen] = useState(false);
  const colsRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!colsOpen) return;
    const h = (ev: MouseEvent) => { if (colsRef.current && !colsRef.current.contains(ev.target as Node)) setColsOpen(false); };
    document.addEventListener("mousedown", h); return () => document.removeEventListener("mousedown", h);
  }, [colsOpen]);

  // ── Selection + bulk ──
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulk, setBulk] = useState<"manager" | "department" | null>(null);

  const { data: emps, isLoading } = useQuery({ queryKey: ["employees"], queryFn: employeeApi.list });
  const cf = useCustomGrid("employee", canEdit || role === "manager");
  // D4: legal entities (group companies) → entity column + filter when the org has more than one
  const { data: entData } = useQuery({ queryKey: ["entities", "headcount"], queryFn: entitiesApi.headcount, staleTime: 60_000 });
  const entityList = useMemo(() => entData?.entities || [], [entData]);
  const entityCode = (id?: string | null) => entityList.find((x) => x.id === id)?.short_code || "";
  const all = useMemo(() => emps || [], [emps]);
  const deptOpts = useMemo(() => [...new Set(all.map((e) => e.department).filter(Boolean))].sort() as string[], [all]);
  const deepLink = params.get("q") || "";        // /employees?q=<number|name>

  // ?new=1 (from the command palette) opens the add form once, then drops the flag.
  useEffect(() => {
    if (params.get("new") === "1" && canWrite) {
      setAdding(true);
      const p = new URLSearchParams(params); p.delete("new"); setParams(p, { replace: true });
    }
  }, [params, canWrite, setParams]);

  // ── Summary strip ──
  const stats = useMemo(() => ({
    total: all.length,
    active: all.filter((e) => e.status === "active").length,
    probation: all.filter(VIEWS[3].test).length,
    joinedMonth: all.filter(CHIP_TEST.joined_month).length,
    leftYear: all.filter(CHIP_TEST.left_year).length,
    noManager: all.filter((e) => !e.manager_id && e.status !== "terminated").length,
    // a record the server redacted (pay_hidden: role without data.identity) has blank bank / SIN — not "missing"
    docs: all.some(hasDocFields) && !all.some((e) => (e as any).pay_hidden) ? all.filter((e) => e.status !== "terminated" && missingDocs(e)).length : null,
  }), [all]);

  const viewDef = VIEWS.find((v) => v.key === view) || VIEWS[0];
  const rows = useMemo(() => all.filter((e) =>
    viewDef.test(e)
    && (!chip || CHIP_TEST[chip](e))
    && (!filters.dept || e.department === filters.dept)
    && (!filters.province || e.province_of_employment === filters.province)
    && (!filters.status || e.status === filters.status)
    && (!filters.type || e.employment_type === filters.type)
    && (!filters.entity || e.entity_id === filters.entity)
    && (!filters.manager || (filters.manager === "none" ? !e.manager_id : !!e.manager_id))),
  [all, viewDef, chip, filters]);
  const viewCounts = useMemo(() => Object.fromEntries(VIEWS.map((v) => [v.key, all.filter(v.test).length])), [all]);

  // Drop selections that are no longer visible (filter changed, person left the list).
  useEffect(() => {
    setSelected((s) => { const vis = new Set(rows.map((r) => r.id)); const n = new Set([...s].filter((k) => vis.has(k))); return n.size === s.size ? s : n; });
  }, [rows]);
  const selectedRows = useMemo(() => rows.filter((r) => selected.has(r.id)), [rows, selected]);

  const region = (e: Employee) =>
    REGION_NAMES.CA[e.province_of_employment] || e.province_of_employment;
  const locationText = (e: Employee) => e.address_country ? `${COUNTRY_NAME[e.address_country] || e.address_country} · ${region(e)}` : "";

  const columns: GridColumn<Employee>[] = [
    { key: "name", label: "Name", text: (e) => `${e.first_name} ${e.last_name}`,
      render: (e) => (
        <div className="flex items-center gap-2.5 min-w-0">
          <span className={`w-7 h-7 rounded-full grid place-items-center text-[11px] font-bold shrink-0 ${avatarClass(e.id || e.employee_number)}`} aria-hidden="true">{initialsOf(e)}</span>
          <div className="min-w-0">
            <div className="font-medium text-gray-800 truncate">{e.first_name} {e.last_name}{e.is_self && <span className="badge-teal ml-1.5">You</span>}</div>
            <div className="text-xs text-gray-400 truncate">{emailOf(e)}</div>
          </div>
        </div>
      ) },
    { key: "employee_number", label: "Number", className: "text-gray-700 tabular-nums" },
    ...(entityList.length > 1 ? [{ key: "entity", label: "Entity", text: (e: Employee) => entityCode(e.entity_id),
      render: (e: Employee) => e.entity_id ? <span className="badge-teal font-mono text-[11px]" title={entityName(entityList, e.entity_id)}>{entityCode(e.entity_id)}</span> : <span className="text-gray-300">—</span> } as GridColumn<Employee>] : []),
    { key: "job_title", label: "Job title" },
    { key: "department", label: "Department" },
    { key: "grade", label: "Grade" },
    { key: "manager_name", label: "Reports to", text: (e) => e.manager_name || "",
      render: (e) => e.manager_name
        ? <span title={e.manager_id ? undefined : "Typed name only — pick the person on the form so approvals reach them"}>
            {e.manager_name}{!e.manager_id && <span className="badge-amber ml-1.5">not linked</span>}</span>
        : <span className="text-gray-300">—</span> },
    { key: "location", label: "Location", text: locationText,
      render: (e) => e.address_country ? <span>{COUNTRY_NAME[e.address_country] || e.address_country}<span className="text-gray-400"> · {region(e)}</span></span>
        : <span className="badge-red">Not set</span> },
    { key: "hire_date", label: "Joined", type: "date",
      render: (e) => e.hire_date ? <span className="whitespace-nowrap">{new Date(e.hire_date).toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" })}<span className="text-gray-400 text-xs ml-1.5">{tenure(e.hire_date)}</span></span> : <span className="text-gray-300">—</span> },
    ...(canSeePay ? [{ key: "pay", label: "Pay", text: (e: Employee) => String(e.pay_type === "hourly" ? e.hourly_rate : e.annual_salary),
      className: "text-right tabular-nums",
      render: (e: Employee) => <span title={freqLabel(e.pay_frequency)}>{e.pay_type === "hourly" ? `${money(e.hourly_rate)}/hr` : money(e.annual_salary)}</span> } as GridColumn<Employee>] : []),
    // D1: custom fields (hidden until picked in the column chooser; exported when shown)
    ...cf.gridDefs.map((d) => ({ key: `cf_${d.key}`, label: d.label, text: (e: Employee) => displayValue(d, cf.valuesOf(e.id)[d.key]),
      render: (e: Employee) => displayValue(d, cf.valuesOf(e.id)[d.key]) || <span className="text-gray-300">—</span> } as GridColumn<Employee>)),
    { key: "status", label: "Status", text: (e) => STATUS_TEXT[e.status] || e.status,
      render: (e) => <Badge tone={e.status}>{STATUS_TEXT[e.status] || e.status}</Badge> },
    ...(canEdit ? [{ key: "actions", label: "Actions", sortable: false, text: () => "",
      render: (e: Employee) => (
        <div className="flex items-center justify-end gap-1">
          <button onClick={(ev) => { ev.stopPropagation(); setEditing(e); }} title="Edit" className="icon-btn text-gray-400 hover:text-primary-600"><Pencil className="w-4 h-4" /></button>
          {canWrite && (
            <button onClick={(ev) => { ev.stopPropagation(); setExitFor(e); }} disabled={e.status === "terminated"}
              title="Start the exit process" className="icon-btn text-gray-400 hover:text-red-500 disabled:opacity-30"><UserRoundX className="w-4 h-4" /></button>
          )}
        </div>
      ) } as GridColumn<Employee>] : []),
  ];
  // custom columns default to hidden (opt-in from the chooser) so the grid does not widen by surprise
  const isHidden = (k: string) => (hidden[k] === undefined ? k.startsWith("cf_") : hidden[k] === true);
  const visibleColumns = columns.filter((c) => !isHidden(c.key));
  const allCols = useMemo<{ key: string; label: string; always?: boolean }[]>(
    () => [...ALL_COLS, ...cf.gridDefs.map((d) => ({ key: `cf_${d.key}`, label: `${d.label} (custom)` }))], [cf.gridDefs]);
  // Page stores "hidden = true"; the grid expects "visible = false" — translate at the boundary.
  const visibility = useMemo(() => Object.fromEntries(columns.map((c) => [c.key, !isHidden(c.key)])), [hidden, columns]);   // eslint-disable-line react-hooks/exhaustive-deps

  const groupBy = useMemo(() => {
    if (groupKey === "department") return { key: (e: Employee) => e.department || "", label: "Department" };
    if (groupKey === "location") return { key: (e: Employee) => locationText(e), label: "Location" };
    if (groupKey === "manager") return { key: (e: Employee) => e.manager_name || "", label: "Reports to" };
    return null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [groupKey]);

  // ── Bulk actions ──
  const exportSelected = () => downloadCsv("employees-selected", toCsv(visibleColumns.filter((c) => c.key !== "actions"), selectedRows));
  const copyEmails = async () => {
    const list = selectedRows.map(emailOf).filter(Boolean);
    if (!list.length) return toast.error("No e-mail addresses on the selected rows");
    try { await navigator.clipboard.writeText(list.join("; ")); toast.success(`${list.length} e-mail${list.length === 1 ? "" : "s"} copied`); }
    catch { toast.error("Could not copy — your browser blocked the clipboard"); }
  };
  const issueLetter = () => nav(`/letters?employees=${[...selected].join(",")}`);

  const chipBtn = (label: string, value: number | string, on: boolean, click: () => void, title?: string, tone = "") => (
    <button type="button" onClick={click} title={title}
      className={`flex flex-col items-start px-3 py-1.5 rounded-lg border text-left transition-colors min-w-[7rem] ${on ? "border-primary-400 bg-primary-50" : "border-surface-border bg-white hover:border-primary-200"}`}>
      <span className={`text-lg font-bold tabular-nums leading-tight ${tone || "text-gray-800"}`}>{value}</span>
      <span className="text-[11px] text-gray-500">{label}</span>
    </button>
  );

  return (
    <div className="pb-20">
      <PageHeader title="Employees" icon={Users}
        subtitle={canEdit ? "Everyone on the books. Click a row to open the person." : role === "manager" ? "You and the people who report to you." : all.length > 1 ? "Everyone your role lets you see — pay and identifiers only where your role carries them." : "Your record."}
        actions={canWrite && (
          <div className="flex items-center gap-2">
            <Link to="/onboarding" className="btn-secondary flex items-center gap-1.5"><UserPlus className="w-4 h-4" /> Invite new hire</Link>
            <button className="btn-primary flex items-center gap-1.5" onClick={() => setAdding(true)}><Plus className="w-4 h-4" /> Add employee</button>
          </div>
        )} />

      {isLoading ? <div className="grid place-items-center py-20"><Spinner className="h-8 w-8" /></div> : (
        <>
          {/* Summary strip — every chip is a filter */}
          <div className="flex flex-wrap gap-2 mb-3" data-testid="emp-summary">
            {chipBtn("Total", stats.total, view === "all" && !chip && !filters.status, () => { setView("all"); setFilters({ dept: "", province: "", status: "", type: "", manager: "", entity: "" }); }, "Everyone — clears all filters")}
            {chipBtn("Active", stats.active, view === "active", () => setView("active"), VIEWS[1].tip, "text-emerald-600")}
            {chipBtn("Probation / onboarding", stats.probation, view === "probation", () => setView("probation"), VIEWS[3].tip, "text-teal-600")}
            {chipBtn("Joined this month", stats.joinedMonth, chip === "joined_month", () => { setViewState("all"); setChip(chip === "joined_month" ? "" : "joined_month"); }, "Hire date falls in the current calendar month")}
            {chipBtn("Left this year", stats.leftYear, chip === "left_year", () => { setViewState("left"); setChip(chip === "left_year" ? "" : "left_year"); }, "Status Left with an exit date in the current year")}
            {chipBtn("Not linked to a manager", stats.noManager, view === "nomanager", () => setView("nomanager"), VIEWS[4].tip, stats.noManager ? "text-amber-600" : "")}
            {stats.docs !== null && chipBtn("Missing bank details", stats.docs, chip === "missing_docs", () => { setViewState("all"); setChip(chip === "missing_docs" ? "" : "missing_docs"); }, "No bank account on file — payroll cannot pay them by direct deposit", stats.docs ? "text-red-600" : "")}
          </div>

          {/* Saved views */}
          <div className="flex flex-wrap items-center gap-1 mb-3" role="tablist" aria-label="Saved views">
            {VIEWS.map((v) => (
              <button key={v.key} role="tab" aria-selected={view === v.key} title={v.tip} onClick={() => setView(v.key)}
                className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium border transition-colors ${view === v.key ? "bg-primary-600 border-primary-600 text-white" : "bg-white border-surface-border text-gray-600 hover:border-primary-300"}`}>
                {v.label}<span className={`tabular-nums text-[10px] px-1.5 rounded-full ${view === v.key ? "bg-white/20" : "bg-gray-100 text-gray-500"}`}>{viewCounts[v.key]}</span>
              </button>
            ))}
            {chip && (
              <button onClick={() => setChip("")} className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs bg-amber-50 border border-amber-200 text-amber-700" title="Remove this extra filter">
                {chip === "joined_month" ? "Joined this month" : chip === "left_year" ? "Left this year" : "Missing bank details"} <X className="w-3 h-3" />
              </button>
            )}
            <span className="ml-auto inline-flex items-center gap-1 text-[11px] text-gray-400"><Info className="w-3 h-3" /> Hover a view for its rule. Views, filters and columns are remembered on this browser.</span>
          </div>

          <DataGrid<Employee> columns={columns} rows={rows} rowKey={(e) => e.id} exportName="employees"
            searchPlaceholder="Search number, name, title, department, e-mail, phone…" empty="No employees match."
            initialSort={{ key: "employee_number", dir: "asc" }} minWidth={1100}
            initialSearch={deepLink}
            searchText={(e) => `${e.employee_number} ${e.first_name} ${e.last_name} ${e.preferred_name || ""} ${emailOf(e)} ${e.email || ""} ${e.phone || ""}`}
            onRowClick={(e) => nav(`/employees/${e.id}`)}
            selectable={canEdit} selected={selected} onSelectionChange={setSelected}
            groupBy={groupBy} columnVisibility={visibility} density={density}
            filters={
              <>
                <select className="input !py-1.5 !w-auto text-sm" value={filters.dept} onChange={(e) => setF("dept", e.target.value)} aria-label="Department filter">
                  <option value="">All departments</option>{deptOpts.map((d) => <option key={d} value={d}>{d}</option>)}
                </select>
                {entityList.length > 1 && (
                  <select className="input !py-1.5 !w-auto text-sm" value={filters.entity || ""} onChange={(e) => setF("entity", e.target.value)} aria-label="Legal entity filter">
                    <option value="">All entities</option>{entityList.map((x) => <option key={x.id} value={x.id}>{x.short_code} · {x.name}</option>)}
                  </select>
                )}
                <select className="input !py-1.5 !w-auto text-sm" value={filters.province || ""} onChange={(e) => setF("province", e.target.value)} aria-label="Province filter">
                  <option value="">All provinces</option>{regionOptions().map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
                <select className="input !py-1.5 !w-auto text-sm" value={filters.status} onChange={(e) => setF("status", e.target.value)} aria-label="Status filter">
                  <option value="">All statuses</option>{Object.entries(STATUS_TEXT).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                </select>
                <select className="input !py-1.5 !w-auto text-sm capitalize" value={filters.type} onChange={(e) => setF("type", e.target.value)} aria-label="Employment type filter">
                  <option value="">All types</option>{EMPLOYMENT_TYPES.map((t) => <option key={t} value={t}>{t.replace("_", " ")}</option>)}
                </select>
                <select className="input !py-1.5 !w-auto text-sm" value={filters.manager} onChange={(e) => setF("manager", e.target.value)} aria-label="Manager link filter">
                  <option value="">Any manager</option><option value="linked">Manager linked</option><option value="none">Manager not linked</option>
                </select>
              </>}
            toolbar={
              <>
                <label className="inline-flex items-center gap-1 text-xs text-gray-500" title="Group rows under collapsible headers">
                  <Layers className="w-3.5 h-3.5" />
                  <select className="input !py-1 !w-auto text-xs" value={groupKey} onChange={(e) => setGroupKey(e.target.value as GroupKey)} aria-label="Group by">
                    <option value="">No grouping</option><option value="department">Group: Department</option>
                    <option value="location">Group: Location</option><option value="manager">Group: Manager</option>
                  </select>
                </label>
                <button type="button" title={density === "compact" ? "Comfortable rows" : "Compact rows"} aria-label="Toggle row density"
                  onClick={() => setDensity(density === "compact" ? "comfortable" : "compact")}
                  className="btn-secondary !py-1.5 !px-2 text-xs inline-flex items-center gap-1">
                  {density === "compact" ? <Rows3 className="w-3.5 h-3.5" /> : <Rows4 className="w-3.5 h-3.5" />}<span className="hidden md:inline">{density === "compact" ? "Compact" : "Comfortable"}</span>
                </button>
                <div className="relative" ref={colsRef}>
                  <button type="button" onClick={() => setColsOpen((o) => !o)} className="btn-secondary !py-1.5 !px-2 text-xs inline-flex items-center gap-1" title="Choose columns" aria-haspopup="menu" aria-expanded={colsOpen}>
                    <Columns3 className="w-3.5 h-3.5" /><span className="hidden md:inline">Columns</span>
                  </button>
                  {colsOpen && (
                    <div role="menu" className="absolute right-0 top-full mt-1 z-30 w-56 bg-white border border-surface-border rounded-xl shadow-lg p-2" data-testid="column-chooser">
                      <div className="flex items-center justify-between px-1.5 pb-1.5 mb-1 border-b border-surface-border">
                        <span className="text-[11px] font-semibold uppercase tracking-wide text-gray-400">Columns</span>
                        <button className="text-[11px] text-primary-600 hover:underline" onClick={() => setHidden({})}>Show all</button>
                      </div>
                      {allCols.filter((c) => (c.key !== "pay" || canSeePay) && (c.key !== "actions" || canEdit)).map((c) => (
                        <label key={c.key} className={`flex items-center gap-2 px-1.5 py-1 rounded-md text-sm text-gray-700 ${c.always ? "opacity-50" : "hover:bg-gray-50 cursor-pointer"}`}>
                          <input type="checkbox" className="rounded border-gray-300 text-primary-600" disabled={c.always} checked={!isHidden(c.key)}
                            onChange={() => setHidden((h) => ({ ...h, [c.key]: !isHidden(c.key) }))} />
                          {c.label}
                        </label>
                      ))}
                    </div>
                  )}
                </div>
              </>} />
        </>
      )}

      {/* Floating bulk bar */}
      {selected.size > 0 && (
        <div className="fixed bottom-4 left-1/2 -translate-x-1/2 z-40 w-[calc(100%-1.5rem)] sm:w-auto" data-testid="bulk-bar">
          <div className="flex flex-wrap items-center gap-1.5 px-3 py-2 rounded-xl bg-gray-900 text-white shadow-2xl">
            <span className="text-sm font-semibold tabular-nums mr-1">{selected.size} selected</span>
            <button onClick={exportSelected} className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs bg-white/10 hover:bg-white/20"><Download className="w-3.5 h-3.5" /> Export selected</button>
            <button onClick={copyEmails} className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs bg-white/10 hover:bg-white/20"><Copy className="w-3.5 h-3.5" /> Copy e-mails</button>
            {canWrite && (
              <>
                <button onClick={() => setBulk("manager")} className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs bg-white/10 hover:bg-white/20"><UserCog className="w-3.5 h-3.5" /> Assign manager</button>
                <button onClick={() => setBulk("department")} className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs bg-white/10 hover:bg-white/20"><Building2 className="w-3.5 h-3.5" /> Change department</button>
                <button onClick={issueLetter} className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs bg-white/10 hover:bg-white/20"><FileSignature className="w-3.5 h-3.5" /> Issue letter</button>
              </>
            )}
            <button onClick={() => setSelected(new Set())} className="ml-1 p-1 rounded-lg hover:bg-white/20" title="Clear selection" aria-label="Clear selection"><X className="w-4 h-4" /></button>
          </div>
        </div>
      )}

      {bulk && (
        <BulkEditDialog kind={bulk} people={selectedRows} everyone={all} deptOpts={deptOpts}
          onClose={() => setBulk(null)}
          onDone={(n) => { setBulk(null); setSelected(new Set()); qc.invalidateQueries({ queryKey: ["employees"] }); toast.success(`${n} record${n === 1 ? "" : "s"} updated`); }} />
      )}

      {(adding || editing) && <EmployeeForm employee={editing} onClose={() => { setAdding(false); setEditing(null); }} />}
      {exitFor && (
        <StartExitModal employeeId={exitFor.id} lockEmployee defaultImmediate={can(user, "exit.create", HR_WRITE)}
          onClose={() => setExitFor(null)}
          onCreated={(c) => { setExitFor(null); qc.invalidateQueries({ queryKey: ["employees"] }); toast.success(`Open Exits → ${c.case_number} to continue`); }} />
      )}
    </div>
  );
}

/** Assign manager / change department for N people: pick → review the list → confirm → PATCH each. */
function BulkEditDialog({ kind, people, everyone, deptOpts, onClose, onDone }: {
  kind: "manager" | "department"; people: Employee[]; everyone: Employee[]; deptOpts: string[];
  onClose: () => void; onDone: (n: number) => void;
}) {
  const [value, setValue] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const { data: depts } = useQuery({ queryKey: ["m", "departments"], queryFn: () => mastersApi.list("departments"), enabled: kind === "department" });
  const deptNames = useMemo(() => [...new Set([...(depts || []).map((d) => d.name), ...deptOpts])].sort(), [depts, deptOpts]);
  const ids = new Set(people.map((p) => p.id));
  const managers = everyone.filter((e) => e.status !== "terminated" && !ids.has(e.id));
  const chosenManager = managers.find((m) => m.id === value);
  const title = kind === "manager" ? "Assign manager" : "Change department";
  const label = kind === "manager" ? (chosenManager ? `${chosenManager.first_name} ${chosenManager.last_name}` : "") : value.trim();

  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", h); return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  const run = async () => {
    setBusy(true);
    const body = kind === "manager" ? { manager_id: value } : { department: value.trim() };
    const results = await Promise.allSettled(people.map((p) => employeeApi.update(p.id, body)));
    const ok = results.filter((r) => r.status === "fulfilled").length;
    const failed = results.length - ok;
    setBusy(false);
    if (failed) toast.error(`${failed} record${failed === 1 ? "" : "s"} could not be updated`);
    onDone(ok);
  };

  return (
    <div className="fixed inset-0 z-[60] bg-black/40 flex items-end sm:items-center justify-center p-0 sm:p-4" onMouseDown={onClose}>
      <div role="dialog" aria-modal="true" aria-label={title} onMouseDown={(e) => e.stopPropagation()}
        className="card !p-0 w-full sm:max-w-5xl max-h-[92vh] flex flex-col rounded-b-none sm:rounded-b-xl">
        <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-surface-border">
          <h2 className="font-display font-semibold text-gray-800">{title} — {people.length} {people.length === 1 ? "person" : "people"}</h2>
          <button onClick={onClose} className="icon-btn text-gray-400 hover:text-gray-700" aria-label="Close"><X className="w-4 h-4" /></button>
        </div>
        <div className="p-4 overflow-y-auto grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="block text-xs font-semibold text-gray-500 mb-1">{kind === "manager" ? "New manager (reports to)" : "New department"}</label>
            {kind === "manager" ? (
              <select className="input" value={value} onChange={(e) => { setValue(e.target.value); setConfirming(false); }} autoFocus>
                <option value="">Choose a person…</option>
                {managers.map((m) => <option key={m.id} value={m.id}>{m.first_name} {m.last_name} · {m.employee_number}{m.job_title ? ` · ${m.job_title}` : ""}</option>)}
              </select>
            ) : (
              <>
                <input className="input" list="lx-bulk-depts" value={value} onChange={(e) => { setValue(e.target.value); setConfirming(false); }} placeholder="Choose or type…" autoFocus />
                <datalist id="lx-bulk-depts">{deptNames.map((d) => <option key={d} value={d} />)}</datalist>
              </>
            )}
            <p className="text-xs text-gray-400 mt-2">
              {kind === "manager" ? "Leave and other approvals for these people will go to the new manager from now on." : "Reports, grouping and department filters use this value. Spelling must match the master list."}
            </p>
            {confirming && (
              <div className="mt-3 p-3 rounded-lg bg-amber-50 border border-amber-200 text-sm text-amber-800 flex items-start gap-2">
                <Info className="w-4 h-4 mt-0.5 shrink-0" />
                <span>Set <b>{kind === "manager" ? "Reports to" : "Department"}</b> = <b>{label}</b> for the {people.length} {people.length === 1 ? "person" : "people"} listed? This writes to each record immediately.</span>
              </div>
            )}
          </div>
          <div>
            <div className="text-xs font-semibold text-gray-500 mb-1">Who is affected</div>
            <ul className="border border-surface-border rounded-lg divide-y divide-surface-border max-h-64 overflow-y-auto text-sm">
              {people.map((p) => (
                <li key={p.id} className="flex items-center gap-2 px-3 py-1.5">
                  <span className={`w-6 h-6 rounded-full grid place-items-center text-[10px] font-bold shrink-0 ${avatarClass(p.id)}`}>{initialsOf(p)}</span>
                  <span className="truncate text-gray-800">{p.first_name} {p.last_name}</span>
                  <span className="ml-auto text-xs text-gray-400 tabular-nums">{p.employee_number}</span>
                  <span className="text-xs text-gray-400 truncate hidden sm:inline">{kind === "manager" ? (p.manager_name || "no manager") : (p.department || "no department")}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>
        <div className="px-4 py-3 border-t border-surface-border flex flex-wrap justify-end gap-2">
          <button className="btn-secondary" onClick={onClose} disabled={busy}>Cancel</button>
          {!confirming
            ? <button className="btn-primary" disabled={!value.trim()} onClick={() => setConfirming(true)}>Review…</button>
            : <button className="btn-primary inline-flex items-center gap-1.5" disabled={busy} onClick={run}><Check className="w-4 h-4" /> {busy ? "Saving…" : `Yes, update ${people.length}`}</button>}
        </div>
      </div>
    </div>
  );
}
