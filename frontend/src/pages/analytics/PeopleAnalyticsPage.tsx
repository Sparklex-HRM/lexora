import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import toast from "react-hot-toast";
import { BarChart3, Plus } from "lucide-react";
import DataGrid, { Tabs, WideModal, type GridColumn } from "../../components/DataGrid";
import { PageHeader, Spinner, StatTile } from "../../components/ui";
import SimpleChart from "../../components/SimpleChart";
import { employeeApi, money } from "../../api/endpoints";
import { analyticsApi, type AttrRow, type Calibration, type PeopleAnalytics, type RetentionAction } from "../../api/analytics";
import { day, errText } from "../../api/platform";
import { useUrlTab } from "../../search/useUrlTab";
import { v, collect, firstError, FieldError, Req } from "../../lib/validators";
import { todayISO } from "../../lib/datetime";   // 2026-10-25: UTC store, org-zone today
import { useViewRole } from "../../config/permissions";   // 2026-10-26: UI gates = permission + role (custom roles)

const F = "input !py-1.5 text-sm w-full";
const L = "block text-[11px] font-semibold text-gray-500 mb-1 uppercase tracking-wide";

/** /analytics/people — headcount trend, joiners / leavers, attrition by department / tenure / manager, gender mix (descriptive),
 *  span of control, overtime, leave liability (estimate), retention actions log, attrition calibration from own exits. */
export default function PeopleAnalyticsPage() {
  const role = useViewRole("reports.view", ["superadmin", "org_admin", "hr_admin", "hr_manager", "payroll_admin", "manager"]);
  const [tab, setTab] = useUrlTab<"overview" | "attrition" | "org" | "time" | "actions" | "calibration">("overview");   // ?tab= (global search deep link)
  const [months, setMonths] = useState(12);
  const q = useQuery({ queryKey: ["people-analytics", months], queryFn: () => analyticsApi.people(months) });
  const d = q.data;
  if (q.isLoading) return <div className="grid place-items-center py-24"><Spinner className="h-8 w-8" /></div>;
  if (q.isError || !d) return <div className="card text-sm text-red-600">Could not load people analytics. {errText(q.error)}</div>;
  const isMgr = role === "manager";
  return (
    <div>
      <PageHeader title="People analytics" icon={BarChart3} subtitle={`${d.headcount} active · as of ${day(d.as_of)}${isMgr ? " · your direct reports" : ""}`}
        actions={<select className="input !py-1.5 text-sm w-36" value={months} onChange={(e) => setMonths(Number(e.target.value))} aria-label="Months"><option value={6}>6 months</option><option value={12}>12 months</option><option value={24}>24 months</option><option value={36}>36 months</option></select>} />
      <Tabs value={tab as string} onChange={(k) => setTab(k as any)} tabs={[{ key: "overview", label: "Headcount" }, { key: "attrition", label: "Attrition" }, { key: "org", label: "Org & diversity" }, ...(isMgr ? [] : [{ key: "time", label: "Overtime & leave liability" }]), { key: "actions", label: "Retention actions" }, ...(isMgr ? [] : [{ key: "calibration", label: "Attrition calibration" }])]} />
      {tab === "overview" && <Overview d={d} />}
      {tab === "attrition" && <Attrition d={d} />}
      {tab === "org" && <Org d={d} />}
      {tab === "time" && <TimeTab d={d} />}
      {tab === "actions" && <Actions />}
      {tab === "calibration" && <CalibrationTab />}
    </div>
  );
}

function Overview({ d }: { d: PeopleAnalytics }) {
  const cols: GridColumn[] = [{ key: "month", label: "Month", width: "100px" }, { key: "headcount", label: "Headcount (month end)", type: "number" }, { key: "joiners", label: "Joiners", type: "number" }, { key: "leavers", label: "Leavers", type: "number" }];
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3"><StatTile label="Headcount" value={d.headcount} accent /><StatTile label="Joiners (12 m)" value={d.joiners_12m} /><StatTile label="Leavers (12 m)" value={d.leavers_12m} /><StatTile label="Attrition (12 m)" value={d.attrition_12m_pct != null ? `${d.attrition_12m_pct}%` : "—"} sub="leavers ÷ average headcount" /></div>
      {d.leavers_without_date > 0 && <div className="card text-sm text-amber-800 bg-amber-50 border-amber-200">{d.leavers_without_date} leaver(s) have no last-working-day on record — they are counted as active until a date is set.</div>}
      <div className="grid md:grid-cols-2 gap-3"><div className="card"><div className="text-sm font-semibold text-gray-800 mb-1">Headcount</div><SimpleChart spec={{ type: "line", x: "month", series: [{ key: "headcount", label: "Headcount" }] }} data={d.series} height={220} /></div>
        <div className="card"><div className="text-sm font-semibold text-gray-800 mb-1">Joiners vs leavers</div><SimpleChart spec={{ type: "bar", x: "month", series: [{ key: "joiners", label: "Joiners" }, { key: "leavers", label: "Leavers" }] }} data={d.series} height={220} /></div></div>
      <DataGrid columns={cols} rows={d.series} rowKey={(r) => r.month} minWidth={500} exportName="headcount-trend" density="compact" />
    </div>
  );
}

function Attrition({ d }: { d: PeopleAnalytics }) {
  const cols = (label: string): GridColumn<AttrRow>[] => [{ key: "group", label, className: "font-medium text-gray-800" }, { key: "headcount", label: "Active", type: "number" }, { key: "leavers", label: "Leavers (12 m)", type: "number" }, { key: "attrition_pct", label: "Attrition %", type: "percent" }];
  return (
    <div className="space-y-3">
      <div className="grid md:grid-cols-2 gap-3">
        <div><h3 className="font-display font-semibold text-gray-800 mb-1">By department</h3><DataGrid columns={cols("Department")} rows={d.attrition_by_department} rowKey={(r) => r.group} minWidth={420} exportName="attrition-by-department" density="compact" /></div>
        <div><h3 className="font-display font-semibold text-gray-800 mb-1">By tenure at exit</h3><DataGrid columns={cols("Tenure band")} rows={d.attrition_by_tenure} rowKey={(r) => r.group} minWidth={420} exportName="attrition-by-tenure" density="compact" /></div>
      </div>
      <h3 className="font-display font-semibold text-gray-800">By manager</h3>
      <DataGrid columns={cols("Manager")} rows={d.attrition_by_manager} rowKey={(r) => r.group} minWidth={520} exportName="attrition-by-manager" density="compact" />
      <p className="text-xs text-gray-500">Attrition % = leavers in the last 12 months ÷ (active + half the leavers). Individual flight-risk signals: <Link className="text-primary-700 underline" to="/ai/flight-risk">open</Link>.</p>
    </div>
  );
}

function Org({ d }: { d: PeopleAnalytics }) {
  const genders = Array.from(new Set(d.gender_by_department.flatMap((r) => Object.keys(r.by_gender)))).sort();
  const gcols: GridColumn<any>[] = [{ key: "group", label: "Department", className: "font-medium text-gray-800" }, { key: "total", label: "Active", type: "number" }, ...genders.map((g) => ({ key: g, label: g, type: "number" as const, text: (r: any) => String(r.by_gender[g] || 0), render: (r: any) => r.by_gender[g] || 0 })),
    ...genders.map((g) => ({ key: `${g}_pct`, label: `${g} %`, type: "percent" as const, text: (r: any) => String(r.total ? Math.round((r.by_gender[g] || 0) * 100 / r.total) : 0), render: (r: any) => r.total ? `${Math.round((r.by_gender[g] || 0) * 100 / r.total)}%` : "—" }))];
  const s = d.span_of_control;
  const scols: GridColumn[] = [{ key: "manager_name", label: "Manager", className: "font-medium text-gray-800" }, { key: "reports", label: "Direct reports", type: "number" }, { key: "wide", label: `Above ${s.wide_at}`, width: "90px", text: (r) => (r.wide ? "yes" : ""), render: (r) => (r.wide ? <span className="badge-amber">wide</span> : "") }];
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3"><StatTile label="Managers" value={s.managers} accent /><StatTile label="Average span" value={s.average ?? "—"} /><StatTile label="Median span" value={s.median ?? "—"} /><StatTile label={`Wide (> ${s.wide_at})`} value={s.wide} /></div>
      <div className="grid md:grid-cols-2 gap-3">
        <div><h3 className="font-display font-semibold text-gray-800 mb-1">Span of control</h3><DataGrid columns={scols} rows={s.rows} rowKey={(r) => r.manager_id} minWidth={400} exportName="span-of-control" density="compact" /></div>
        <div><h3 className="font-display font-semibold text-gray-800 mb-1">Gender mix (as recorded — descriptive only)</h3><DataGrid columns={gcols} rows={d.gender_by_department} rowKey={(r) => r.group} minWidth={500} exportName="gender-by-department" density="compact" /></div>
      </div>
    </div>
  );
}

function TimeTab({ d }: { d: PeopleAnalytics }) {
  const ocols: GridColumn[] = [{ key: "group", label: "Month / department", className: "font-medium text-gray-800" }, { key: "ot_hours", label: "OT hours", type: "hours" }, { key: "people", label: "People", type: "number" }, { key: "sheets", label: "Timesheets", type: "number" }, { key: "avg_per_person", label: "Avg h / person", type: "hours" }];
  const ll = d.leave_liability;
  const lcols: GridColumn<any>[] = [{ key: "employee_name", label: "Person", className: "font-medium text-gray-800" }, { key: "department", label: "Department" }, { key: "days", label: "Balance days", type: "number" }, ...(d.shows_money ? [{ key: "amount", label: "Estimated liability", type: "money" as const }] : []),
    { key: "types", label: "By leave type", text: (r: any) => r.types.map((t: any) => `${t.leave_type} ${t.days}`).join("; "), render: (r: any) => r.types.map((t: any) => `${t.leave_type} ${t.days}`).join(" · ") }];
  return (
    <div className="space-y-3">
      <div className="grid md:grid-cols-2 gap-3">
        <div><h3 className="font-display font-semibold text-gray-800 mb-1">Overtime by month (approved timesheets, 12 m)</h3>{!!d.overtime_by_month?.length && <div className="card mb-2"><SimpleChart spec={{ type: "bar", x: "group", series: [{ key: "ot_hours", label: "OT hours" }] }} data={d.overtime_by_month} unit="hours" height={200} /></div>}<DataGrid columns={ocols} rows={d.overtime_by_month || []} rowKey={(r) => r.group} minWidth={480} exportName="overtime-by-month" density="compact" /></div>
        <div><h3 className="font-display font-semibold text-gray-800 mb-1">Overtime by department</h3><DataGrid columns={ocols} rows={d.overtime_by_department || []} rowKey={(r) => r.group} minWidth={480} exportName="overtime-by-department" density="compact" /></div>
      </div>
      {ll && <div><h3 className="font-display font-semibold text-gray-800 mb-1">Leave liability · FY {ll.fiscal_year}</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-2"><StatTile label="Balance days" value={ll.total_days} accent />{d.shows_money && <StatTile label="Estimated liability" value={ll.total_amount != null ? money(ll.total_amount) : "—"} />}<StatTile label="People" value={ll.rows.length} /><StatTile label="Balance rows" value={ll.balances_found} /></div>
        <p className="text-xs text-gray-500 mb-2">{ll.method}. Divisor = org setting leave.encashment_divisor ({ll.divisor}).</p>
        <DataGrid columns={lcols} rows={ll.rows} rowKey={(r) => r.employee_id} minWidth={800} exportName="leave-liability" density="compact" totals={{ employee_name: "Total", days: ll.total_days, ...(d.shows_money ? { amount: ll.total_amount } : {}) }} /></div>}
    </div>
  );
}

function Actions() {
  const qc = useQueryClient();
  const [status, setStatus] = useState("");
  const q = useQuery({ queryKey: ["retention-actions", status], queryFn: () => analyticsApi.actions({ status: status || undefined }) });
  const [add, setAdd] = useState(false);
  const [edit, setEdit] = useState<RetentionAction | null>(null);
  const inv = () => qc.invalidateQueries({ queryKey: ["retention-actions"] });
  const ST: Record<string, string> = { open: "bg-amber-100 text-amber-800", done: "bg-green-100 text-green-700", dropped: "bg-gray-200 text-gray-700" };
  const cols: GridColumn<RetentionAction>[] = [{ key: "employee_name", label: "Person", className: "font-medium text-gray-800" }, { key: "department", label: "Department" }, { key: "kind", label: "Kind", width: "110px" }, { key: "action", label: "Action" }, { key: "owner_name", label: "Owner" }, { key: "due_on", label: "Due", type: "date", width: "100px" },
    { key: "status", label: "Status", width: "85px", render: (r) => <span className={`inline-block text-[11px] font-semibold px-2 py-0.5 rounded-full ${ST[r.status]}`}>{r.status}</span> }, { key: "risk_score_at", label: "Risk when logged", type: "number", width: "110px" }, { key: "outcome", label: "Outcome" }, { key: "created_at", label: "Logged", type: "date", width: "100px" }];
  return (
    <div className="space-y-2">
      <div className="flex gap-2"><button className="btn-primary text-sm inline-flex items-center gap-1" onClick={() => setAdd(true)}><Plus className="w-4 h-4" /> Log an action</button><span className="text-xs text-gray-500 self-center">Follow-ups from the <Link className="text-primary-700 underline" to="/ai/flight-risk">flight-risk signals</Link>: a conversation, a pay review, a role change, learning…</span></div>
      <DataGrid<RetentionAction> columns={cols} rows={q.data?.actions || []} rowKey={(r) => r.id} minWidth={1100} exportName="retention-actions" density="compact" onRowClick={setEdit} empty="No retention actions logged yet."
        filters={<select className="input !py-1.5 text-sm w-32" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status"><option value="">All</option><option value="open">Open</option><option value="done">Done</option><option value="dropped">Dropped</option></select>} />
      {add && <ActionModal kinds={q.data?.kinds || []} onClose={() => { setAdd(false); inv(); }} />}
      {edit && <ActionModal kinds={q.data?.kinds || []} a={edit} onClose={() => { setEdit(null); inv(); }} />}
    </div>
  );
}

function ActionModal({ a, kinds, onClose }: { a?: RetentionAction; kinds: string[]; onClose: () => void }) {
  const emps = useQuery({ queryKey: ["employees"], queryFn: () => employeeApi.list(), enabled: !a });
  const list = ((emps.data || []) as any[]).filter((e) => e.status !== "terminated");
  const [f, setF] = useState<any>(a ? { ...a } : { employee_id: "", action: "", kind: "conversation", owner_name: "", due_on: "" });
  const risk = useQuery({ queryKey: ["risk-one", f.employee_id], queryFn: () => analyticsApi.one(f.employee_id), enabled: !a && !!f.employee_id, retry: false });
  const save = useMutation({ mutationFn: () => a ? analyticsApi.updateAction(a.id, { action: f.action, kind: f.kind, owner_name: f.owner_name, due_on: f.due_on || null, outcome: f.outcome, status: f.status })
    : analyticsApi.addAction({ employee_id: f.employee_id, action: f.action, kind: f.kind, owner_name: f.owner_name || undefined, due_on: f.due_on || undefined, risk_score_at: risk.data?.score, risk_band_at: risk.data?.band }), onSuccess: () => { toast.success("Saved"); onClose(); }, onError: (e) => toast.error(errText(e)) });
  const today = todayISO();
  const errs = collect({
    employee_id: !a ? v.required(f.employee_id, "Person") : null,
    action: v.required(String(f.action || "").trim(), "Action") || v.maxLen(f.action, 400, "Action"),
    owner_name: v.maxLen(f.owner_name, 120, "Owner"),
    due_on: !a && f.due_on && f.due_on < today ? "Due date cannot be in the past for a new action" : null,
    outcome: a && (f.status === "done" || f.status === "dropped") && !String(f.outcome || "").trim() ? "Record the outcome when the action is done or dropped" : v.maxLen(f.outcome, 4000, "Outcome"),
  });
  const submit = () => { if (Object.keys(errs).length) { toast.error(firstError(errs)); return; } save.mutate(); };
  return (
    <WideModal title={a ? `${a.employee_name} · retention action` : "Log a retention action"} onClose={onClose} footer={<button className="btn-primary" disabled={save.isPending || !f.action || (!a && !f.employee_id)} onClick={submit}>Save</button>}>
      <div className="grid sm:grid-cols-2 md:grid-cols-3 gap-3">
        {!a && <div className="sm:col-span-2 md:col-span-3"><label className={L}>Person<Req /></label><select className={F} value={f.employee_id} onChange={(e) => setF({ ...f, employee_id: e.target.value })}><option value="">Pick…</option>{list.map((e) => <option key={e.id} value={e.id}>{e.first_name} {e.last_name} · {e.department || ""}</option>)}</select><FieldError msg={errs.employee_id} /></div>}
        {risk.data && <div className="md:col-span-3 card !py-2 text-sm"><b>Flight-risk {risk.data.score} ({risk.data.band})</b>{risk.data.why?.length ? <ul className="list-disc pl-5 text-xs text-gray-600 mt-1">{risk.data.why.map((w: string, i: number) => <li key={i}>{w}</li>)}</ul> : <span className="text-xs text-gray-500"> — no rule fired</span>}</div>}
        <div><label className={L}>Kind<Req /></label><select className={F} value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>{(kinds.length ? kinds : ["conversation", "pay_review", "role_change", "learning", "manager_change", "other"]).map((k) => <option key={k} value={k}>{k.replace("_", " ")}</option>)}</select><span className="block text-[11px] text-gray-400 mt-0.5">A 1:1 conversation, pay review, role change…</span></div>
        <div><label className={L}>Owner</label><input className={F} maxLength={120} value={f.owner_name || ""} placeholder="blank = you" onChange={(e) => setF({ ...f, owner_name: e.target.value })} /><span className="block text-[11px] text-gray-400 mt-0.5">Leave blank to own it yourself</span><FieldError msg={errs.owner_name} /></div>
        <div><label className={L}>Due</label><input type="date" className={F} min={!a ? today : undefined} value={f.due_on || ""} onChange={(e) => setF({ ...f, due_on: e.target.value })} /><FieldError msg={errs.due_on} /></div>
        <div className="sm:col-span-2 md:col-span-3"><label className={L}>Action<Req /></label><input className={F} maxLength={400} value={f.action} placeholder="e.g. Career conversation with manager this week" onChange={(e) => setF({ ...f, action: e.target.value })} /><FieldError msg={errs.action} /></div>
        {a && <><div><label className={L}>Status</label><select className={F} value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}><option value="open">Open</option><option value="done">Done</option><option value="dropped">Dropped</option></select></div>
          <div className="sm:col-span-2 md:col-span-2"><label className={L}>Outcome{(f.status === "done" || f.status === "dropped") && <Req />}</label><textarea className={F} rows={2} maxLength={4000} value={f.outcome || ""} placeholder="What happened — e.g. stayed after the role change" onChange={(e) => setF({ ...f, outcome: e.target.value })} /><FieldError msg={errs.outcome} /></div></>}
      </div>
    </WideModal>
  );
}

function CalibrationTab() {
  const q = useQuery({ queryKey: ["attrition-calibration"], queryFn: analyticsApi.calibration });
  const c: Calibration | undefined = q.data;
  if (q.isLoading) return <div className="grid place-items-center py-12"><Spinner /></div>;
  if (q.isError || !c) return <div className="card text-sm text-red-600">{errText(q.error)}</div>;
  const fcols: GridColumn[] = [{ key: "factor", label: "Factor", className: "font-medium text-gray-800" }, { key: "flagged", label: "People flagged", type: "number" }, { key: "exit_rate_flagged", label: "Exit rate · flagged %", type: "percent" }, { key: "exit_rate_not_flagged", label: "Exit rate · not flagged %", type: "percent" }, { key: "lift", label: "Lift", type: "number" }];
  const bcols: GridColumn[] = [{ key: "band", label: "Band", className: "font-medium text-gray-800" }, { key: "people", label: "People", type: "number" }, { key: "left", label: "Left", type: "number" }, { key: "exit_rate", label: "Exit rate %", type: "percent" }];
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3"><StatTile label="Exits on record" value={c.exits} accent sub={`needs ${c.needed}`} /><StatTile label="People scored" value={c.people} /><StatTile label="Overall exit rate" value={c.overall_exit_rate != null ? `${c.overall_exit_rate}%` : "—"} /><StatTile label="Model" value={typeof c.model === "string" ? c.model.split(" (")[0] : c.model?.kind || "—"} sub={typeof c.model === "string" ? c.model : ""} /></div>
      <div className="card text-sm text-gray-700">{c.message}</div>
      {c.available && <div className="grid md:grid-cols-[1fr_22rem] gap-3"><DataGrid columns={fcols} rows={c.factors || []} rowKey={(r) => r.factor} minWidth={600} exportName="attrition-calibration" density="compact" /><DataGrid columns={bcols} rows={c.bands || []} rowKey={(r) => r.band} minWidth={300} exportName="attrition-bands" density="compact" /></div>}
      {c.available && typeof c.model === "object" && c.model?.odds_ratios && <div className="card text-xs text-gray-600">Logistic model odds ratios: {Object.entries(c.model.odds_ratios).map(([k, v]) => `${k} ${v}`).join(" · ")}</div>}
    </div>
  );
}
