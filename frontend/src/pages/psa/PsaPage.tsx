import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Briefcase, Plus, RefreshCw, Trash2 } from "lucide-react";
import { PageHeader, StatTile } from "../../components/ui";
import DataGrid, { GridColumn, Tabs } from "../../components/DataGrid";
import { Modal, Field, Input, Select, Status, useEmployees, empLabel } from "../common/_shared";
import { opsApi, money } from "../../api/endpoints";
import { psaApi, errText, monthKey, monthStart, monthEnd, RateCard, Assignment } from "../../api/psa";
import { useAuthStore, PAYROLL_ACCESS, HR_WRITE } from "../../store/authStore";
import { v, collect, firstError } from "../../lib/validators";
import { todayISO } from "../../lib/datetime";   // 2026-10-25: UTC store, org-zone today
import { can } from "../../config/permissions";   // 2026-10-26: UI gates = permission + role (custom roles)

/** Block Save with a toast of the first problem; true = fine to save. */
const okOrToast = (errs: Record<string, string>) => { const m = firstError(errs); if (m) toast.error(m); return !m; };
const CUR = /^[A-Z]{3}$/;

type Tab = "rates" | "assignments" | "hours" | "utilisation" | "invoices" | "leakage" | "revenue";
const TABS: { key: Tab; label: string }[] = [
  { key: "rates", label: "Rate cards" }, { key: "assignments", label: "Assignments & bench" }, { key: "hours", label: "Billable hours" },
  { key: "utilisation", label: "Utilisation" }, { key: "invoices", label: "Invoices" }, { key: "leakage", label: "Leakage" }, { key: "revenue", label: "Revenue & FX" },
];
const cur = (n: any, c?: string) => (c && c !== "" ? `${c} ${(Number(n) || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : money(Number(n) || 0));
const today = () => todayISO();

export default function PsaPage() {
  const { tab = "rates" } = useParams();
  const nav = useNavigate();
  const user = useAuthStore((s) => s.user);
  const canWrite = can(user, ["projects.create", "projects.edit"], [...HR_WRITE, ...PAYROLL_ACCESS]);
  const canFinance = can(user, ["projects.edit", "projects.admin"], PAYROLL_ACCESS);
  const canAssign = canWrite || can(user, ["projects.create", "projects.edit"], ["manager"]);
  const { data: settings } = useQuery({ queryKey: ["psa-settings"], queryFn: psaApi.settings });
  const t = (TABS.some((x) => x.key === tab) ? tab : "rates") as Tab;
  return (
    <div>
      <PageHeader title="Project Services" icon={Briefcase}
        subtitle={`Rate cards, assignments, billable hours, utilisation and client invoices${settings?.home_currency ? ` · home currency ${settings.home_currency}` : ""}`} />
      <Tabs tabs={TABS} value={t} onChange={(k) => nav(`/psa/${k}`)} />
      {t === "rates" && <RateCards canWrite={canWrite} home={settings?.home_currency || ""} />}
      {t === "assignments" && <Assignments canWrite={canAssign} />}
      {t === "hours" && <BillableHours canWrite={canWrite} />}
      {t === "utilisation" && <Utilisation />}
      {t === "invoices" && <Invoices canFinance={canFinance} />}
      {t === "leakage" && <Leakage />}
      {t === "revenue" && <Revenue canFinance={canFinance} />}
    </div>
  );
}

// ── Rate cards ─────────────────────────────────────────────────────────────
function RateCards({ canWrite, home }: { canWrite: boolean; home: string }) {
  const qc = useQueryClient();
  const employees = useEmployees();
  const { data: cards = [], isLoading } = useQuery({ queryKey: ["psa-rate-cards"], queryFn: psaApi.rateCards });
  const { data: clients = [] } = useQuery({ queryKey: ["ops", "clients"], queryFn: () => opsApi.list("clients") });
  const { data: projects = [] } = useQuery({ queryKey: ["ops", "projects"], queryFn: () => opsApi.list("projects") });
  const [edit, setEdit] = useState<Partial<RateCard> | null>(null);
  const [fClient, setFClient] = useState("");
  const save = useMutation({
    mutationFn: (b: Partial<RateCard>) => (b.id ? psaApi.updateRateCard(b.id, b) : psaApi.createRateCard(b)),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["psa-rate-cards"] }); setEdit(null); toast.success("Rate card saved"); },
    onError: (e) => toast.error(errText(e)),
  });
  const del = useMutation({ mutationFn: (id: string) => psaApi.deleteRateCard(id), onSuccess: () => { qc.invalidateQueries({ queryKey: ["psa-rate-cards"] }); toast.success("Rate card removed"); }, onError: (e) => toast.error(errText(e)) });
  const rows = useMemo(() => cards.filter((c) => !fClient || c.client === fClient), [cards, fClient]);
  const cols: GridColumn<RateCard>[] = [
    { key: "client", label: "Client" }, { key: "project_name", label: "Project" }, { key: "role", label: "Role" },
    { key: "employee_name", label: "Employee" }, { key: "rate", label: "Rate", type: "money", render: (r) => cur(r.rate, r.currency), text: (r) => String(r.rate) },
    { key: "unit", label: "Per" }, { key: "currency", label: "Currency" }, { key: "effective_from", label: "From", type: "date" },
    { key: "effective_to", label: "To", type: "date" }, { key: "note", label: "Note" },
    { key: "_a", label: "", sortable: false, render: (r) => canWrite ? <span className="flex gap-1"><button className="btn-secondary !py-0.5 !px-2 text-xs" onClick={(e) => { e.stopPropagation(); setEdit(r); }}>Edit</button><button className="icon-btn text-gray-300 hover:text-red-500" onClick={(e) => { e.stopPropagation(); if (confirm("Remove this rate card?")) del.mutate(r.id); }}><Trash2 className="w-4 h-4" /></button></span> : null },
  ];
  return (
    <>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
        <StatTile label="Rate cards" value={cards.length} accent />
        <StatTile label="Clients priced" value={new Set(cards.map((c) => c.client).filter(Boolean)).size} />
        <StatTile label="Employee-specific" value={cards.filter((c) => c.employee_id).length} />
        <StatTile label="Day rates" value={cards.filter((c) => c.unit === "day").length} />
      </div>
      <DataGrid columns={cols} rows={rows} rowKey={(r) => r.id} exportName="rate-cards" minWidth={1100}
        empty={isLoading ? "Loading…" : "No rate cards yet. Add one to price billable hours."}
        initialSort={{ key: "client", dir: "asc" }}
        filters={<Select value={fClient} onChange={(e) => setFClient(e.target.value)} className="!w-48"><option value="">All clients</option>{clients.map((c: any) => <option key={c.id} value={c.name}>{c.name}</option>)}</Select>}
        toolbar={canWrite && <button className="btn-primary !py-1.5 text-xs flex items-center gap-1" onClick={() => setEdit({ unit: "hour", currency: home, effective_from: today() })}><Plus className="w-3.5 h-3.5" /> New rate card</button>} />
      {edit && (
        <Modal title={edit.id ? "Edit rate card" : "New rate card"} subtitle="The most specific card wins: employee + project, then employee, project + role, project, client + role, client, role." onClose={() => setEdit(null)}
          footer={<><button className="btn-secondary" onClick={() => setEdit(null)}>Cancel</button><button className="btn-primary" disabled={save.isPending} onClick={() => okOrToast(collect({
            scope: !edit.client && !edit.project_id && !edit.role && !edit.employee_id ? "Pick at least one of client, project, role or employee" : null,
            rate: !(Number(edit.rate) > 0) ? "Rate must be more than zero" : null,
            currency: edit.currency && !CUR.test(edit.currency) ? "Currency is a 3-letter code, e.g. CAD" : null,
            effective_from: v.required(edit.effective_from, "Effective from"),
            effective_to: v.dateOrder(edit.effective_from, edit.effective_to, "Effective to cannot be before effective from"),
            note: v.maxLen(edit.note, 300, "Note") })) && save.mutate(edit)}>Save rate card</button></>}>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            <Field label="Client"><Select value={edit.client || ""} onChange={(e) => setEdit({ ...edit, client: e.target.value })}><option value="">— any —</option>{clients.map((c: any) => <option key={c.id} value={c.name}>{c.name}</option>)}</Select></Field>
            <Field label="Project"><Select value={edit.project_id || ""} onChange={(e) => { const p = projects.find((x: any) => x.id === e.target.value); setEdit({ ...edit, project_id: e.target.value || null, project_name: p?.name || null, client: p?.client || edit.client }); }}><option value="">— any —</option>{projects.map((p: any) => <option key={p.id} value={p.id}>{p.name}{p.client ? ` (${p.client})` : ""}</option>)}</Select></Field>
            <Field label="Role / designation"><Input value={edit.role || ""} placeholder="Senior Developer" onChange={(e) => setEdit({ ...edit, role: e.target.value })} /></Field>
            <Field label="Employee"><Select value={edit.employee_id || ""} onChange={(e) => { const em = employees.find((x: any) => x.id === e.target.value); setEdit({ ...edit, employee_id: e.target.value || null, employee_name: em ? `${em.first_name} ${em.last_name}` : null }); }}><option value="">— any —</option>{employees.map((e: any) => <option key={e.id} value={e.id}>{empLabel(e)}</option>)}</Select></Field>
            <Field label="Rate"><Input type="number" min={0} step="0.01" value={edit.rate ?? ""} onChange={(e) => setEdit({ ...edit, rate: parseFloat(e.target.value) })} /></Field>
            <Field label="Per"><Select value={edit.unit || "hour"} onChange={(e) => setEdit({ ...edit, unit: e.target.value as any })}><option value="hour">Hour</option><option value="day">Day</option></Select></Field>
            <Field label="Currency" hint="3-letter code"><Input maxLength={3} value={edit.currency || ""} placeholder={home || "CAD"} onChange={(e) => setEdit({ ...edit, currency: e.target.value.toUpperCase() })} /></Field>
            <Field label="Effective from"><Input type="date" value={edit.effective_from || ""} onChange={(e) => setEdit({ ...edit, effective_from: e.target.value })} /></Field>
            <Field label="Effective to" hint="blank = open"><Input type="date" value={edit.effective_to || ""} onChange={(e) => setEdit({ ...edit, effective_to: e.target.value || null })} /></Field>
            <Field label="Note" span={3}><Input value={edit.note || ""} onChange={(e) => setEdit({ ...edit, note: e.target.value })} /></Field>
          </div>
        </Modal>
      )}
    </>
  );
}

// ── Assignments & bench ────────────────────────────────────────────────────
function Assignments({ canWrite }: { canWrite: boolean }) {
  const qc = useQueryClient();
  const employees = useEmployees();
  const [asOf, setAsOf] = useState(today());
  const { data: rows = [], isLoading } = useQuery({ queryKey: ["psa-assignments"], queryFn: () => psaApi.assignments() });
  const { data: bench } = useQuery({ queryKey: ["psa-bench", asOf], queryFn: () => psaApi.bench(asOf) });
  const { data: projects = [] } = useQuery({ queryKey: ["ops", "projects"], queryFn: () => opsApi.list("projects") });
  const [edit, setEdit] = useState<any | null>(null);
  const [view, setView] = useState<"assignments" | "bench">("assignments");
  const save = useMutation({
    mutationFn: (b: any) => (b.id ? psaApi.updateAssignment(b.id, b) : psaApi.createAssignment(b)),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["psa-assignments"] }); qc.invalidateQueries({ queryKey: ["psa-bench"] }); setEdit(null); toast.success("Assignment saved"); },
    onError: (e) => toast.error(errText(e)),
  });
  const end = useMutation({ mutationFn: (id: string) => psaApi.endAssignment(id), onSuccess: () => { qc.invalidateQueries({ queryKey: ["psa-assignments"] }); qc.invalidateQueries({ queryKey: ["psa-bench"] }); toast.success("Assignment ended"); }, onError: (e) => toast.error(errText(e)) });
  const cols: GridColumn<Assignment>[] = [
    { key: "employee_name", label: "Employee" }, { key: "employee_number", label: "No." }, { key: "department", label: "Department" },
    { key: "client", label: "Client" }, { key: "project_name", label: "Project" }, { key: "role", label: "Role" },
    { key: "allocation_pct", label: "Allocation", type: "percent" }, { key: "start_date", label: "Start", type: "date" }, { key: "end_date", label: "End", type: "date" },
    { key: "billable", label: "Billable", render: (r) => r.billable ? <span className="badge-green">Yes</span> : <span className="badge-amber">No</span>, text: (r) => r.billable ? "Yes" : "No" },
    { key: "_a", label: "", sortable: false, render: (r) => canWrite ? <span className="flex gap-1"><button className="btn-secondary !py-0.5 !px-2 text-xs" onClick={() => setEdit(r)}>Edit</button><button className="btn-secondary !py-0.5 !px-2 text-xs" onClick={() => { if (confirm("End this assignment?")) end.mutate(r.id); }}>End</button></span> : null },
  ];
  const benchCols: GridColumn<any>[] = [
    { key: "employee_name", label: "Employee" }, { key: "employee_number", label: "No." }, { key: "department", label: "Department" }, { key: "designation", label: "Designation" },
    { key: "allocated_pct", label: "Allocated", type: "percent" }, { key: "free_pct", label: "Free", type: "percent" }, { key: "projects", label: "Current projects" },
    { key: "fully_on_bench", label: "Status", render: (r) => r.fully_on_bench ? <span className="badge-red">On bench</span> : <span className="badge-amber">Partly free</span>, text: (r) => r.fully_on_bench ? "On bench" : "Partly free" },
  ];
  const active = rows.filter((r) => !r.end_date || r.end_date >= today());
  return (
    <>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
        <StatTile label="Active assignments" value={active.length} accent />
        <StatTile label="People assigned" value={new Set(active.map((r) => r.employee_id)).size} />
        <StatTile label="On bench (0 %)" value={bench?.rows?.filter((r: any) => r.fully_on_bench).length ?? "—"} sub={`of ${bench?.headcount ?? "—"} people on ${asOf}`} />
        <StatTile label="Partly free" value={bench?.rows?.filter((r: any) => !r.fully_on_bench).length ?? "—"} />
      </div>
      <div className="flex items-center gap-2 mb-3">
        <button className={view === "assignments" ? "btn-primary !py-1 text-xs" : "btn-secondary !py-1 text-xs"} onClick={() => setView("assignments")}>Assignments</button>
        <button className={view === "bench" ? "btn-primary !py-1 text-xs" : "btn-secondary !py-1 text-xs"} onClick={() => setView("bench")}>Bench list</button>
        {view === "bench" && <label className="text-xs text-gray-500 ml-3 flex items-center gap-2">As of <Input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} className="!w-40" /></label>}
      </div>
      {view === "assignments" ? (
        <DataGrid columns={cols} rows={rows} rowKey={(r) => r.id} exportName="assignments" minWidth={1100} initialSort={{ key: "start_date", dir: "desc" }}
          empty={isLoading ? "Loading…" : "Nobody is assigned to a project yet."}
          toolbar={canWrite && <button className="btn-primary !py-1.5 text-xs flex items-center gap-1" onClick={() => setEdit({ allocation_pct: 100, start_date: today(), billable: true })}><Plus className="w-3.5 h-3.5" /> Assign to project</button>} />
      ) : (
        <DataGrid columns={benchCols} rows={bench?.rows || []} rowKey={(r) => r.employee_id} exportName={`bench-${asOf}`} minWidth={900} initialSort={{ key: "free_pct", dir: "desc" }} empty="Everyone is fully allocated." />
      )}
      {edit && (
        <Modal title={edit.id ? "Edit assignment" : "Assign an employee to a project"} subtitle="A person cannot be over 100 % across overlapping assignments." onClose={() => setEdit(null)}
          footer={<><button className="btn-secondary" onClick={() => setEdit(null)}>Cancel</button><button className="btn-primary" disabled={save.isPending} onClick={() => okOrToast(collect({
            employee_id: v.required(edit.employee_id, "Employee"), project_id: v.required(edit.project_id, "Project"),
            allocation_pct: !(Number(edit.allocation_pct) >= 1 && Number(edit.allocation_pct) <= 100) ? "Allocation is 1 to 100 %" : null,
            start_date: v.required(edit.start_date, "Start"), end_date: v.dateOrder(edit.start_date, edit.end_date, "End cannot be before start") })) && save.mutate(edit)}>Save</button></>}>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            <Field label="Employee" span={2}><Select value={edit.employee_id || ""} disabled={!!edit.id} onChange={(e) => setEdit({ ...edit, employee_id: e.target.value })}><option value="">— pick —</option>{employees.map((e: any) => <option key={e.id} value={e.id}>{empLabel(e)}</option>)}</Select></Field>
            <Field label="Project" span={2}><Select value={edit.project_id || ""} disabled={!!edit.id} onChange={(e) => setEdit({ ...edit, project_id: e.target.value })}><option value="">— pick —</option>{projects.map((p: any) => <option key={p.id} value={p.id}>{p.name}{p.client ? ` (${p.client})` : ""}</option>)}</Select></Field>
            <Field label="Role on project"><Input value={edit.role || ""} onChange={(e) => setEdit({ ...edit, role: e.target.value })} /></Field>
            <Field label="Allocation %" hint="Share of their time; 100 % max across overlaps"><Input type="number" min={1} max={100} value={edit.allocation_pct ?? ""} onChange={(e) => setEdit({ ...edit, allocation_pct: parseFloat(e.target.value) })} /></Field>
            <Field label="Start"><Input type="date" value={edit.start_date || ""} onChange={(e) => setEdit({ ...edit, start_date: e.target.value })} /></Field>
            <Field label="End" hint="blank = open"><Input type="date" value={edit.end_date || ""} onChange={(e) => setEdit({ ...edit, end_date: e.target.value || null })} /></Field>
            <Field label="Billable"><Select value={edit.billable ? "1" : "0"} onChange={(e) => setEdit({ ...edit, billable: e.target.value === "1" })}><option value="1">Yes</option><option value="0">No</option></Select></Field>
            <Field label="Note" span={3}><Input value={edit.note || ""} onChange={(e) => setEdit({ ...edit, note: e.target.value })} /></Field>
          </div>
        </Modal>
      )}
    </>
  );
}

// ── Billable hours ─────────────────────────────────────────────────────────
function BillableHours({ canWrite }: { canWrite: boolean }) {
  const qc = useQueryClient();
  const [month, setMonth] = useState(monthKey());
  const [status, setStatus] = useState("approved,locked");
  const [bill, setBill] = useState("");
  const [uninv, setUninv] = useState(false);
  const params = { from: monthStart(month), to: monthEnd(month), status, billable: bill, uninvoiced: uninv ? 1 : "" };
  const { data: lines = [], isLoading } = useQuery({ queryKey: ["psa-lines", params], queryFn: () => psaApi.billableLines(params) });
  const apply = useMutation({ mutationFn: () => psaApi.applyRates({ from_date: monthStart(month), to_date: monthEnd(month) }), onSuccess: (r: any) => { qc.invalidateQueries({ queryKey: ["psa-lines"] }); toast.success(`${r.stamped} line(s) stamped with a rate${r.missing_rate ? ` · ${r.missing_rate} have no rate card` : ""}`); }, onError: (e) => toast.error(errText(e)) });
  const cols: GridColumn<any>[] = [
    { key: "work_date", label: "Date", type: "date" }, { key: "employee_name", label: "Employee" }, { key: "client", label: "Client" }, { key: "project_name", label: "Project" },
    { key: "hours", label: "Hours", type: "hours" },
    { key: "billable", label: "Billable", render: (r) => r.billable ? <span className="badge-green">Yes</span> : <span className="badge-purple">No</span>, text: (r) => r.billable ? "Yes" : "No" },
    { key: "status", label: "Timesheet", render: (r) => <Status s={r.status} />, text: (r) => r.status },
    { key: "rate", label: "Rate", type: "money", render: (r) => r.missing_rate ? <span className="badge-red">No rate card</span> : cur(r.rate, r.currency) + "/" + (r.unit === "day" ? "day" : "h"), text: (r) => String(r.rate) },
    { key: "rate_source", label: "Rate from", render: (r) => ({ snapshot: "Snapshot", card: "Rate card", none: "—" } as any)[r.rate_source] },
    { key: "amount", label: "Amount", type: "money", render: (r) => cur(r.amount, r.currency), text: (r) => String(r.amount) },
    { key: "invoice_line_id", label: "Invoiced", render: (r) => r.invoice_line_id ? <span className="badge-teal">Yes</span> : <span className="text-gray-400">No</span>, text: (r) => r.invoice_line_id ? "Yes" : "No" },
    { key: "note", label: "Note" },
  ];
  const totals = { hours: lines.reduce((s, l) => s + (l.hours || 0), 0), amount: lines.reduce((s, l) => s + (l.amount || 0), 0) };
  return (
    <>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
        <StatTile label="Hours shown" value={totals.hours.toFixed(2)} accent />
        <StatTile label="Billable hours" value={lines.filter((l) => l.billable).reduce((s, l) => s + l.hours, 0).toFixed(2)} />
        <StatTile label="Amount at rate" value={money(totals.amount)} />
        <StatTile label="Lines without a rate card" value={lines.filter((l) => l.billable && l.missing_rate).length} />
      </div>
      <DataGrid columns={cols} rows={lines} rowKey={(r) => r.id} exportName={`billable-hours-${month}`} minWidth={1200} totals={{ hours: totals.hours, amount: totals.amount }}
        empty={isLoading ? "Loading…" : "No timesheet lines for this month and filter. Employees book hours under Self-Service → My Time."}
        filters={<>
          <Input type="month" value={month} onChange={(e) => setMonth(e.target.value)} className="!w-40" />
          <Select value={status} onChange={(e) => setStatus(e.target.value)} className="!w-44"><option value="approved,locked">Approved + locked</option><option value="submitted">Submitted</option><option value="draft">Draft</option><option value="all">All</option></Select>
          <Select value={bill} onChange={(e) => setBill(e.target.value)} className="!w-36"><option value="">Billable + not</option><option value="1">Billable only</option><option value="0">Non-billable</option></Select>
          <label className="text-xs text-gray-600 flex items-center gap-1"><input type="checkbox" checked={uninv} onChange={(e) => setUninv(e.target.checked)} /> Not yet invoiced</label>
        </>}
        toolbar={canWrite && <button className="btn-secondary !py-1.5 text-xs flex items-center gap-1" onClick={() => apply.mutate()} disabled={apply.isPending} title="Stamp the rate card in force on every approved billable line that has no rate yet"><RefreshCw className="w-3.5 h-3.5" /> Apply rate cards</button>} />
    </>
  );
}

// ── Utilisation ────────────────────────────────────────────────────────────
function Utilisation() {
  const [month, setMonth] = useState(monthKey());
  const [dept, setDept] = useState("");
  const { data, isLoading } = useQuery({ queryKey: ["psa-util", month, dept], queryFn: () => psaApi.utilisation(month, dept) });
  const rows = data?.rows || [];
  const depts = Array.from(new Set(rows.map((r: any) => r.department).filter(Boolean))) as string[];
  const cols: GridColumn<any>[] = [
    { key: "employee_name", label: "Employee" }, { key: "employee_number", label: "No." }, { key: "department", label: "Team" },
    { key: "available", label: "Available h", type: "hours" }, { key: "billable", label: "Billable h", type: "hours" }, { key: "non_billable", label: "Non-billable h", type: "hours" },
    { key: "utilisation_pct", label: "Utilisation", type: "percent", render: (r) => <span className={r.utilisation_pct >= 75 ? "badge-green" : r.utilisation_pct >= 50 ? "badge-amber" : "badge-red"}>{r.utilisation_pct.toFixed(1)}%</span>, text: (r) => String(r.utilisation_pct) },
    { key: "productive_pct", label: "Productive", type: "percent" },
  ];
  const teamCols: GridColumn<any>[] = [
    { key: "department", label: "Team" }, { key: "people", label: "People", type: "number" }, { key: "available", label: "Available h", type: "hours" },
    { key: "billable", label: "Billable h", type: "hours" }, { key: "non_billable", label: "Non-billable h", type: "hours" }, { key: "utilisation_pct", label: "Utilisation", type: "percent" },
  ];
  return (
    <>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
        <StatTile label="Utilisation" value={`${data?.totals?.utilisation_pct ?? 0}%`} accent sub="billable ÷ available" />
        <StatTile label="Available hours" value={data?.totals?.available ?? 0} sub={`${data?.hours_per_day ?? 8} h × working days (company settings)`} />
        <StatTile label="Billable hours" value={data?.totals?.billable ?? 0} />
        <StatTile label="Non-billable hours" value={data?.totals?.non_billable ?? 0} />
      </div>
      <div className="mb-4">
        <DataGrid columns={cols} rows={rows} rowKey={(r) => r.employee_id} exportName={`utilisation-${month}`} minWidth={1000} initialSort={{ key: "utilisation_pct", dir: "desc" }}
          empty={isLoading ? "Loading…" : "No employees."}
          filters={<><Input type="month" value={month} onChange={(e) => setMonth(e.target.value)} className="!w-40" /><Select value={dept} onChange={(e) => setDept(e.target.value)} className="!w-44"><option value="">All teams</option>{depts.map((d) => <option key={d} value={d}>{d}</option>)}</Select></>} />
      </div>
      <h3 className="text-sm font-semibold text-gray-700 mb-2">By team</h3>
      <DataGrid columns={teamCols} rows={data?.teams || []} rowKey={(r) => r.department} exportName={`utilisation-teams-${month}`} minWidth={700} empty="—" />
    </>
  );
}

// ── Invoices ───────────────────────────────────────────────────────────────
function Invoices({ canFinance }: { canFinance: boolean }) {
  const qc = useQueryClient();
  const { data: invoices = [], isLoading } = useQuery({ queryKey: ["psa-invoices"], queryFn: () => psaApi.invoices() });
  const { data: clients = [] } = useQuery({ queryKey: ["ops", "clients"], queryFn: () => opsApi.list("clients") });
  const { data: projects = [] } = useQuery({ queryKey: ["ops", "projects"], queryFn: () => opsApi.list("projects") });
  const [draft, setDraft] = useState<any | null>(null);
  const [open, setOpen] = useState<any | null>(null);
  const [pay, setPay] = useState<{ amount: number; paid_on: string } | null>(null);
  const refresh = () => { qc.invalidateQueries({ queryKey: ["psa-invoices"] }); qc.invalidateQueries({ queryKey: ["psa-lines"] }); qc.invalidateQueries({ queryKey: ["ops", "invoices"] }); };
  const create = useMutation({ mutationFn: (b: any) => psaApi.draftInvoice(b), onSuccess: (r: any) => { refresh(); setDraft(null); setOpen(r); toast.success(`Draft ${r.name} created · ${r.timesheet_lines_marked} timesheet line(s) taken`); }, onError: (e) => toast.error(errText(e)) });
  const setStatus = useMutation({ mutationFn: ({ id, status }: any) => psaApi.setInvoiceStatus(id, status), onSuccess: (r) => { refresh(); setOpen(r); toast.success(`Invoice is now ${r.status}`); }, onError: (e) => toast.error(errText(e)) });
  const payment = useMutation({ mutationFn: ({ id, body }: any) => psaApi.recordPayment(id, body), onSuccess: (r) => { refresh(); setOpen(r); setPay(null); toast.success("Payment recorded"); }, onError: (e) => toast.error(errText(e)) });
  const discard = useMutation({ mutationFn: (id: string) => psaApi.discardDraft(id), onSuccess: () => { refresh(); setOpen(null); toast.success("Draft discarded — the hours are back in the pool"); }, onError: (e) => toast.error(errText(e)) });
  const cols: GridColumn<any>[] = [
    { key: "name", label: "Invoice no." }, { key: "client", label: "Client" }, { key: "invoice_type", label: "Type" },
    { key: "period_start", label: "Period from", type: "date" }, { key: "period_end", label: "Period to", type: "date" },
    { key: "issue_date", label: "Issued", type: "date" }, { key: "due_date", label: "Due", type: "date" },
    { key: "hours", label: "Hours", type: "hours" }, { key: "amount", label: "Amount", type: "money", render: (r) => cur(r.amount, r.currency), text: (r) => String(r.amount) },
    { key: "tax", label: "Tax", type: "money", render: (r) => cur(r.tax, r.currency), text: (r) => String(r.tax) },
    { key: "total", label: "Total", type: "money", render: (r) => cur(r.total, r.currency), text: (r) => String(r.total) },
    { key: "paid_amount", label: "Paid", type: "money", render: (r) => cur(r.paid_amount, r.currency), text: (r) => String(r.paid_amount) },
    { key: "status", label: "Status", render: (r) => <Status s={r.status} />, text: (r) => r.status },
  ];
  const m = new Date(); const firstOfMonth = `${monthKey(m)}-01`;
  return (
    <>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
        <StatTile label="Invoices" value={invoices.length} accent />
        <StatTile label="Drafts" value={invoices.filter((i) => i.status === "draft").length} />
        <StatTile label="Billed (all)" value={money(invoices.filter((i) => i.status !== "draft").reduce((s, i) => s + (i.total || 0), 0))} />
        <StatTile label="Outstanding" value={money(invoices.filter((i) => i.status !== "draft").reduce((s, i) => s + (i.outstanding || 0), 0))} />
      </div>
      <DataGrid columns={cols} rows={invoices} rowKey={(r) => r.id} exportName="psa-invoices" minWidth={1300} initialSort={{ key: "issue_date", dir: "desc" }} onRowClick={(r) => setOpen(r)}
        empty={isLoading ? "Loading…" : "No invoices yet. Draft one from approved hours."}
        toolbar={canFinance && <button className="btn-primary !py-1.5 text-xs flex items-center gap-1" onClick={() => setDraft({ client: "", period_start: firstOfMonth, period_end: monthEnd(monthKey(m)), invoice_type: "tm", tax_pct: 0, include_hours: true, fixed_lines: [] })}><Plus className="w-3.5 h-3.5" /> Draft invoice</button>} />
      {draft && (
        <Modal title="Draft an invoice" subtitle="T&M: approved + locked billable hours of the client's projects not yet invoiced × the rate in force. Milestone / retainer: fixed lines you type." onClose={() => setDraft(null)}
          footer={<><button className="btn-secondary" onClick={() => setDraft(null)}>Cancel</button><button className="btn-primary" disabled={create.isPending} onClick={() => okOrToast(collect({
            client: v.required(draft.client, "Client"),
            period_end: v.required(draft.period_start, "Period from") || v.required(draft.period_end, "Period to") || v.dateOrder(draft.period_start, draft.period_end, "Period to cannot be before period from"),
            currency: draft.currency && !CUR.test(draft.currency) ? "Currency is a 3-letter code, e.g. USD" : null,
            tax_pct: v.percent(draft.tax_pct, "Tax %"),
            lines: draft.invoice_type !== "tm" && !(draft.fixed_lines || []).length ? "Add at least one fixed line" : (draft.fixed_lines || []).some((l: any) => !String(l.description || "").trim() || !(Number(l.amount) >= 0)) ? "Every fixed line needs a description and an amount of 0 or more" : null })) && create.mutate(draft)}>Create draft</button></>}>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            <Field label="Client"><Select value={draft.client} onChange={(e) => setDraft({ ...draft, client: e.target.value })}><option value="">— pick —</option>{clients.map((c: any) => <option key={c.id} value={c.name}>{c.name}</option>)}</Select></Field>
            <Field label="Type"><Select value={draft.invoice_type} onChange={(e) => setDraft({ ...draft, invoice_type: e.target.value, include_hours: e.target.value !== "milestone" && e.target.value !== "retainer" })}><option value="tm">Time & material</option><option value="milestone">Milestone</option><option value="retainer">Retainer</option><option value="mixed">Mixed (hours + fixed)</option></Select></Field>
            <Field label="Period from"><Input type="date" value={draft.period_start} onChange={(e) => setDraft({ ...draft, period_start: e.target.value })} /></Field>
            <Field label="Period to"><Input type="date" value={draft.period_end} onChange={(e) => setDraft({ ...draft, period_end: e.target.value })} /></Field>
            <Field label="Currency" hint="blank = home currency"><Input maxLength={3} value={draft.currency || ""} onChange={(e) => setDraft({ ...draft, currency: e.target.value.toUpperCase() })} /></Field>
            <Field label="Tax %" hint="Total sales tax rate (GST/HST, plus PST/QST where it applies); 0 for zero-rated exports"><Input type="number" min={0} max={100} step="0.01" value={draft.tax_pct} onChange={(e) => setDraft({ ...draft, tax_pct: parseFloat(e.target.value) || 0 })} /></Field>
            <Field label="Issue date"><Input type="date" value={draft.issue_date || today()} onChange={(e) => setDraft({ ...draft, issue_date: e.target.value })} /></Field>
            <Field label="Note"><Input value={draft.note || ""} onChange={(e) => setDraft({ ...draft, note: e.target.value })} /></Field>
          </div>
          {(draft.invoice_type !== "tm") && (
            <div className="mt-4">
              <div className="flex items-center justify-between mb-2"><h4 className="text-sm font-semibold text-gray-700">Fixed lines</h4><button className="btn-secondary !py-1 text-xs" onClick={() => setDraft({ ...draft, fixed_lines: [...draft.fixed_lines, { kind: draft.invoice_type === "retainer" ? "retainer" : "milestone", description: "", amount: 0 }] })}>+ Add line</button></div>
              <table className="w-full text-sm"><thead className="text-[11px] uppercase text-gray-400"><tr><th className="text-left py-1">Kind</th><th className="text-left">Description</th><th className="text-left">Project</th><th className="text-right">Amount</th><th /></tr></thead>
                <tbody>{draft.fixed_lines.map((l: any, i: number) => (
                  <tr key={i} className="border-t border-surface-border">
                    <td className="py-1 pr-2"><Select value={l.kind} onChange={(e) => { const fl = [...draft.fixed_lines]; fl[i] = { ...l, kind: e.target.value }; setDraft({ ...draft, fixed_lines: fl }); }}><option value="milestone">Milestone</option><option value="retainer">Retainer</option><option value="other">Other</option></Select></td>
                    <td className="pr-2"><Input value={l.description} placeholder="Milestone 2 — UAT sign-off" onChange={(e) => { const fl = [...draft.fixed_lines]; fl[i] = { ...l, description: e.target.value }; setDraft({ ...draft, fixed_lines: fl }); }} /></td>
                    <td className="pr-2"><Select value={l.project_id || ""} onChange={(e) => { const fl = [...draft.fixed_lines]; fl[i] = { ...l, project_id: e.target.value || null }; setDraft({ ...draft, fixed_lines: fl }); }}><option value="">—</option>{projects.filter((p: any) => !draft.client || p.client === draft.client).map((p: any) => <option key={p.id} value={p.id}>{p.name}</option>)}</Select></td>
                    <td className="pr-2"><Input type="number" min={0} step="0.01" className="text-right" value={l.amount} onChange={(e) => { const fl = [...draft.fixed_lines]; fl[i] = { ...l, amount: parseFloat(e.target.value) || 0 }; setDraft({ ...draft, fixed_lines: fl }); }} /></td>
                    <td><button className="icon-btn text-gray-300 hover:text-red-500" onClick={() => setDraft({ ...draft, fixed_lines: draft.fixed_lines.filter((_: any, j: number) => j !== i) })}><Trash2 className="w-4 h-4" /></button></td>
                  </tr>))}</tbody></table>
            </div>
          )}
        </Modal>
      )}
      {open && (
        <Modal title={`Invoice ${open.name} — ${open.client}`} subtitle={`${open.invoice_type || "tm"} · period ${open.period_start || "—"} to ${open.period_end || "—"} · issued ${open.issue_date} · due ${open.due_date}`} onClose={() => setOpen(null)}
          footer={<>
            {canFinance && open.status === "draft" && <button className="btn-secondary text-red-600" onClick={() => { if (confirm("Discard this draft? Its hours go back to the uninvoiced pool.")) discard.mutate(open.id); }}>Discard draft</button>}
            {canFinance && open.status === "draft" && <button className="btn-primary" onClick={() => setStatus.mutate({ id: open.id, status: "sent" })}>Mark as sent</button>}
            {canFinance && (open.status === "sent" || open.status === "overdue") && <button className="btn-secondary" onClick={() => setPay({ amount: open.outstanding, paid_on: today() })}>Record payment</button>}
            {canFinance && open.status === "sent" && <button className="btn-secondary" onClick={() => setStatus.mutate({ id: open.id, status: "overdue" })}>Mark overdue</button>}
            <button className="btn-secondary" onClick={() => setOpen(null)}>Close</button>
          </>}>
          <div className="grid grid-cols-2 lg:grid-cols-5 gap-3 mb-4">
            <StatTile label="Status" value={<Status s={open.status} />} />
            <StatTile label="Amount" value={cur(open.amount, open.currency)} />
            <StatTile label="Tax" value={cur(open.tax, open.currency)} />
            <StatTile label="Total" value={cur(open.total, open.currency)} accent />
            <StatTile label="Paid / outstanding" value={cur(open.paid_amount, open.currency)} sub={`${cur(open.outstanding, open.currency)} outstanding${open.paid_on ? ` · last paid ${open.paid_on}` : ""}`} />
          </div>
          {pay && (
            <div className="card mb-4 flex flex-wrap items-end gap-3">
              <Field label="Amount received"><Input type="number" min={0} step="0.01" value={pay.amount} onChange={(e) => setPay({ ...pay, amount: parseFloat(e.target.value) || 0 })} /></Field>
              <Field label="Paid on"><Input type="date" value={pay.paid_on} onChange={(e) => setPay({ ...pay, paid_on: e.target.value })} /></Field>
              <button className="btn-primary" onClick={() => payment.mutate({ id: open.id, body: pay })}>Save payment</button>
              <button className="btn-secondary" onClick={() => setPay(null)}>Cancel</button>
            </div>
          )}
          <DataGrid columns={[
            { key: "kind", label: "Kind" }, { key: "description", label: "Description" }, { key: "employee_name", label: "Employee" }, { key: "project_name", label: "Project" },
            { key: "hours", label: "Hours", type: "hours" }, { key: "rate", label: "Rate", type: "money", render: (r: any) => r.kind === "tm" ? cur(r.rate, r.currency) : "—", text: (r: any) => String(r.rate) },
            { key: "amount", label: "Amount", type: "money", render: (r: any) => cur(r.amount, r.currency), text: (r: any) => String(r.amount) },
            { key: "missing_rate", label: "", render: (r: any) => r.missing_rate ? <span className="badge-red">No rate card</span> : null, text: (r: any) => r.missing_rate ? "No rate" : "" },
          ]} rows={open.lines || []} rowKey={(r: any) => r.id} exportName={`invoice-${open.name}`} minWidth={900} totals={{ hours: open.hours, amount: open.amount }} empty="No lines." />
        </Modal>
      )}
    </>
  );
}

// ── Leakage ────────────────────────────────────────────────────────────────
function Leakage() {
  const [from, setFrom] = useState(""); const [to, setTo] = useState("");
  const { data, isLoading } = useQuery({ queryKey: ["psa-leakage", from, to], queryFn: () => psaApi.leakage({ from, to }) });
  const grp = (key: string, label: string) => [{ key, label }, { key: "lines", label: "Lines", type: "number" }, { key: "hours", label: "Hours", type: "hours" }, { key: "amount", label: "Amount at rate", type: "money" }, { key: "missing_rate", label: "No rate card", type: "number" }] as GridColumn<any>[];
  return (
    <>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
        <StatTile label="Approved billable hours NOT invoiced" value={data?.uninvoiced?.hours ?? 0} accent sub={`${data?.uninvoiced?.lines ?? 0} timesheet lines`} />
        <StatTile label="Revenue waiting to be billed" value={money(data?.uninvoiced?.amount ?? 0)} sub={data?.uninvoiced?.missing_rate ? `${data.uninvoiced.missing_rate} line(s) have no rate card` : "all lines priced"} />
        <StatTile label="Invoiced, not paid" value={money(data?.outstanding_total ?? 0)} sub={`${data?.invoiced_unpaid?.length ?? 0} invoices`} />
        <div className="card flex flex-col gap-1 justify-center"><span className="text-[11px] font-semibold text-gray-400 uppercase">Work dates</span><div className="flex gap-2"><Input type="date" value={from} onChange={(e) => setFrom(e.target.value)} /><Input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></div></div>
      </div>
      <div className="grid lg:grid-cols-2 gap-4 mb-4">
        <div><h3 className="text-sm font-semibold text-gray-700 mb-2">Uninvoiced by client</h3><DataGrid columns={grp("client", "Client")} rows={data?.by_client || []} rowKey={(r) => r.client} exportName="leakage-client" minWidth={500} empty={isLoading ? "Loading…" : "Nothing waiting."} /></div>
        <div><h3 className="text-sm font-semibold text-gray-700 mb-2">Uninvoiced by project</h3><DataGrid columns={grp("project", "Project")} rows={data?.by_project || []} rowKey={(r) => r.project} exportName="leakage-project" minWidth={500} empty="Nothing waiting." /></div>
      </div>
      <div className="grid lg:grid-cols-2 gap-4">
        <div><h3 className="text-sm font-semibold text-gray-700 mb-2">Uninvoiced by employee</h3><DataGrid columns={grp("employee", "Employee")} rows={data?.by_employee || []} rowKey={(r) => r.employee} exportName="leakage-employee" minWidth={500} empty="Nothing waiting." /></div>
        <div><h3 className="text-sm font-semibold text-gray-700 mb-2">Invoiced but not paid</h3><DataGrid columns={[{ key: "name", label: "Invoice" }, { key: "client", label: "Client" }, { key: "due_date", label: "Due", type: "date" }, { key: "status", label: "Status", render: (r: any) => <Status s={r.status} />, text: (r: any) => r.status }, { key: "total", label: "Total", type: "money" }, { key: "paid_amount", label: "Paid", type: "money" }, { key: "outstanding", label: "Outstanding", type: "money" }]} rows={data?.invoiced_unpaid || []} rowKey={(r) => r.id} exportName="unpaid-invoices" minWidth={600} empty="Everything sent is paid." /></div>
      </div>
    </>
  );
}

// ── Revenue & FX ───────────────────────────────────────────────────────────
function Revenue({ canFinance }: { canFinance: boolean }) {
  const qc = useQueryClient();
  const [group, setGroup] = useState("client");
  const [from, setFrom] = useState(""); const [to, setTo] = useState("");
  const { data } = useQuery({ queryKey: ["psa-revenue", group, from, to], queryFn: () => psaApi.revenue({ group, from, to }) });
  const { data: fx } = useQuery({ queryKey: ["psa-fx"], queryFn: psaApi.fxRates });
  const [nfx, setNfx] = useState<any | null>(null);
  const addFx = useMutation({ mutationFn: (b: any) => psaApi.addFx(b), onSuccess: () => { qc.invalidateQueries({ queryKey: ["psa-fx"] }); qc.invalidateQueries({ queryKey: ["psa-revenue"] }); setNfx(null); toast.success("Rate saved"); }, onError: (e) => toast.error(errText(e)) });
  const delFx = useMutation({ mutationFn: (id: string) => psaApi.deleteFx(id), onSuccess: () => { qc.invalidateQueries({ queryKey: ["psa-fx"] }); qc.invalidateQueries({ queryKey: ["psa-revenue"] }); }, onError: (e) => toast.error(errText(e)) });
  const home = data?.home_currency || fx?.home_currency || "";
  const cols: GridColumn<any>[] = [
    { key: group, label: group === "month" ? "Month" : group === "project" ? "Project" : "Client" }, { key: "invoices", label: "Invoices", type: "number" }, { key: "lines", label: "Lines", type: "number" },
    { key: "hours", label: "Hours", type: "hours" }, { key: "amount", label: `Revenue (${home || "home"})`, type: "money" }, { key: "paid", label: "Paid", type: "money" },
    { key: "unconverted_lines", label: "No FX rate", type: "number", render: (r) => r.unconverted_lines ? <span className="badge-red">{r.unconverted_lines}</span> : "—", text: (r) => String(r.unconverted_lines) },
  ];
  return (
    <>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
        <StatTile label={`Revenue invoiced (${home || "home"})`} value={money(data?.total ?? 0)} accent />
        <StatTile label="Paid" value={money(data?.paid ?? 0)} />
        <StatTile label="Lines without an FX rate" value={data?.unconverted_lines ?? 0} sub="shown at face value" />
        <div className="card flex flex-col gap-1 justify-center"><span className="text-[11px] font-semibold text-gray-400 uppercase">Issue dates</span><div className="flex gap-2"><Input type="date" value={from} onChange={(e) => setFrom(e.target.value)} /><Input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></div></div>
      </div>
      <div className="mb-4">
        <DataGrid columns={cols} rows={data?.rows || []} rowKey={(r) => r[group]} exportName={`revenue-by-${group}`} minWidth={800} empty="No invoices in this range."
          filters={<Select value={group} onChange={(e) => setGroup(e.target.value)} className="!w-40"><option value="client">By client</option><option value="project">By project</option><option value="month">By month</option></Select>} />
      </div>
      <h3 className="text-sm font-semibold text-gray-700 mb-2">Exchange rates to {home || "the home currency"} (entered by hand — no feed)</h3>
      <DataGrid columns={[{ key: "currency", label: "Currency" }, { key: "rate_to_home", label: `1 unit = … ${home}`, type: "number" }, { key: "as_of", label: "As of", type: "date" }, { key: "source", label: "Source" }, { key: "note", label: "Note" },
        { key: "_a", label: "", sortable: false, render: (r: any) => canFinance ? <button className="icon-btn text-gray-300 hover:text-red-500" onClick={() => delFx.mutate(r.id)}><Trash2 className="w-4 h-4" /></button> : null }]}
        rows={fx?.rows || []} rowKey={(r: any) => r.id} exportName="fx-rates" minWidth={700} empty="No rates. Invoices in other currencies are shown at face value until you add one."
        toolbar={canFinance && <button className="btn-primary !py-1.5 text-xs flex items-center gap-1" onClick={() => setNfx({ currency: "USD", rate_to_home: "", as_of: today(), source: "" })}><Plus className="w-3.5 h-3.5" /> Add rate</button>} />
      {nfx && (
        <Modal title="Add an exchange rate" subtitle="The newest rate per currency is used for the revenue report." onClose={() => setNfx(null)} size="lg"
          footer={<><button className="btn-secondary" onClick={() => setNfx(null)}>Cancel</button><button className="btn-primary" onClick={() => okOrToast(collect({
            currency: !CUR.test(nfx.currency || "") ? "Currency is a 3-letter code, e.g. USD" : nfx.currency === home ? `That is the home currency (${home})` : null,
            rate_to_home: !(parseFloat(nfx.rate_to_home) > 0) ? "Rate must be more than zero" : null,
            as_of: v.required(nfx.as_of, "As of") || v.notFuture(nfx.as_of, "As of"), source: v.maxLen(nfx.source, 200, "Source"), note: v.maxLen(nfx.note, 1000, "Note") }))
            && addFx.mutate({ ...nfx, rate_to_home: parseFloat(nfx.rate_to_home), note: (nfx.note || "").trim() || undefined })}>Save</button></>}>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            <Field label="Currency"><Input maxLength={3} value={nfx.currency} onChange={(e) => setNfx({ ...nfx, currency: e.target.value.toUpperCase() })} /></Field>
            <Field label={`1 unit = … ${home}`} hint={`How many ${home || "home"} one unit buys, e.g. 1 USD = 83.2`}><Input type="number" step="0.000001" value={nfx.rate_to_home} onChange={(e) => setNfx({ ...nfx, rate_to_home: e.target.value })} /></Field>
            <Field label="As of"><Input type="date" value={nfx.as_of} onChange={(e) => setNfx({ ...nfx, as_of: e.target.value })} /></Field>
            <Field label="Source" hint="Cite it for audit"><Input value={nfx.source} maxLength={200} placeholder="RBI reference rate" onChange={(e) => setNfx({ ...nfx, source: e.target.value })} /></Field>
            <Field label="Note" span={3}><Input value={nfx.note || ""} maxLength={1000} placeholder="e.g. month-end rate used for the board pack" onChange={(e) => setNfx({ ...nfx, note: e.target.value })} /></Field>
          </div>
        </Modal>
      )}
    </>
  );
}
