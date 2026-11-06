import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Briefcase, Plus } from "lucide-react";
import DataGrid, { Tabs, WideModal, type GridColumn } from "../../components/DataGrid";
import { PageHeader, StatTile } from "../../components/ui";
import { employeeApi, money } from "../../api/endpoints";
import { positionsApi, type BudgetRow, type Position } from "../../api/analytics";
import { errText } from "../../api/platform";
import { useUrlTab } from "../../search/useUrlTab";
import { v, collect, firstError, FieldError, Req } from "../../lib/validators";
import { todayISO } from "../../lib/datetime";   // 2026-10-25: UTC store, org-zone today

const FY_RE = /^\d{4}-\d{2}$/;

const F = "input !py-1.5 text-sm w-full";
const L = "block text-[11px] font-semibold text-gray-500 mb-1 uppercase tracking-wide";
const ST: Record<string, string> = { open: "bg-amber-100 text-amber-800", filled: "bg-green-100 text-green-700", frozen: "bg-gray-200 text-gray-700" };

/** /analytics/positions — headcount planning: positions (open / filled / frozen), budget vs actual by department / entity / grade,
 *  fill from the roster, link to a hiring requisition (read-only reference). */
export default function PositionsPage() {
  const qc = useQueryClient();
  const [tab, setTab] = useUrlTab<"positions" | "budget">("positions");   // ?tab= (global search deep link)
  const [status, setStatus] = useState("");
  const [dept, setDept] = useState("");
  const q = useQuery({ queryKey: ["positions"], queryFn: () => positionsApi.list() });
  const [edit, setEdit] = useState<Partial<Position> | null>(null);
  const [fill, setFill] = useState<Position | null>(null);
  const [link, setLink] = useState<Position | null>(null);
  const inv = () => qc.invalidateQueries({ queryKey: ["positions"] });
  const vacate = useMutation({ mutationFn: (id: string) => positionsApi.vacate(id), onSuccess: () => { toast.success("Position is open again"); inv(); }, onError: (e) => toast.error(errText(e)) });
  const del = useMutation({ mutationFn: (id: string) => positionsApi.remove(id), onSuccess: inv, onError: (e) => toast.error(errText(e)) });
  const seed = useMutation({ mutationFn: positionsApi.seed, onSuccess: (r) => { toast.success(`${r.created} position(s) created from the roster`); inv(); }, onError: (e) => toast.error(errText(e)) });
  const d = q.data;
  const showMoney = !!d?.shows_money;
  const canEdit = !!d?.can_edit;
  const rows = useMemo(() => (d?.positions || []).filter((p) => (!status || p.status === status) && (!dept || p.department === dept)), [d, status, dept]);
  const depts = useMemo(() => Array.from(new Set((d?.positions || []).map((p) => p.department).filter(Boolean))).sort() as string[], [d]);
  const cols: GridColumn<Position>[] = [
    { key: "code", label: "Position", width: "95px", className: "font-medium text-gray-800" }, { key: "title", label: "Title", className: "text-gray-800" }, { key: "grade", label: "Grade", width: "70px" },
    { key: "department", label: "Department" }, { key: "entity", label: "Entity" }, { key: "location", label: "Location" }, { key: "fiscal_year", label: "FY", width: "80px" },
    { key: "status", label: "Status", width: "85px", render: (r) => <span className={`inline-block text-[11px] font-semibold px-2 py-0.5 rounded-full ${ST[r.status]}`}>{r.status}</span> },
    { key: "employee_name", label: "Holder" }, { key: "filled_on", label: "Filled on", type: "date", width: "100px" }, { key: "manager_name", label: "Reports to" }, { key: "requisition_number", label: "Requisition", width: "110px" },
    ...(showMoney ? [{ key: "budgeted_ctc", label: "Budgeted salary", type: "money" as const }, { key: "actual_ctc", label: "Actual salary", type: "money" as const },
      { key: "over_budget", label: "Over", width: "60px", text: (r: Position) => (r.over_budget ? "yes" : ""), render: (r: Position) => (r.over_budget ? <span className="badge-red">over</span> : "") }] : []),
    { key: "act", label: "", sortable: false, render: (r) => canEdit ? <div className="flex gap-2 whitespace-nowrap"><button className="text-xs text-primary-700" onClick={(e) => { e.stopPropagation(); setEdit(r); }}>Edit</button>
      {r.status === "open" && <button className="text-xs text-primary-700" onClick={(e) => { e.stopPropagation(); setFill(r); }}>Fill</button>}
      {r.status === "filled" && <button className="text-xs text-amber-700" onClick={(e) => { e.stopPropagation(); if (confirm(`Vacate ${r.code}?`)) vacate.mutate(r.id); }}>Vacate</button>}
      <button className="text-xs text-primary-700" onClick={(e) => { e.stopPropagation(); setLink(r); }}>Req.</button>
      {r.status !== "filled" && <button className="text-xs text-red-600" onClick={(e) => { e.stopPropagation(); if (confirm(`Delete ${r.code}?`)) del.mutate(r.id); }}>Delete</button>}</div> : null },
  ];
  const totals = showMoney ? { code: "Total", budgeted_ctc: rows.reduce((s, r) => s + (r.budgeted_ctc || 0), 0), actual_ctc: rows.reduce((s, r) => s + (r.actual_ctc || 0), 0) } : null;
  return (
    <div>
      <PageHeader title="Positions & headcount plan" icon={Briefcase} subtitle={`${d?.counts.open ?? 0} open · ${d?.counts.filled ?? 0} filled · ${d?.counts.frozen ?? 0} frozen${showMoney ? "" : " · budget figures hidden for your role"}`}
        actions={canEdit && <div className="flex gap-2"><button className="btn-secondary text-sm" onClick={() => { if (confirm("Create one filled position per active employee who holds none (budget = current annual salary)?")) seed.mutate(); }} disabled={seed.isPending}>Seed from roster</button>
          <button className="btn-primary text-sm inline-flex items-center gap-1" onClick={() => setEdit({ title: "", status: "open", budgeted_ctc: 0 })}><Plus className="w-4 h-4" /> Position</button></div>} />
      <Tabs value={tab} onChange={setTab} tabs={[{ key: "positions", label: "Positions", count: d?.positions.length }, { key: "budget", label: "Budget vs actual" }]} />
      {tab === "positions" && <DataGrid<Position> columns={cols} rows={rows} rowKey={(r) => r.id} minWidth={showMoney ? 1500 : 1200} exportName="positions" density="compact" totals={totals} onRowClick={canEdit ? setEdit : undefined} empty="No positions yet. Add one or seed from the roster."
        filters={<><select className="input !py-1.5 text-sm w-32" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status"><option value="">All status</option><option value="open">Open</option><option value="filled">Filled</option><option value="frozen">Frozen</option></select>
          <select className="input !py-1.5 text-sm w-44" value={dept} onChange={(e) => setDept(e.target.value)} aria-label="Department"><option value="">All departments</option>{depts.map((x) => <option key={x} value={x}>{x}</option>)}</select></>} />}
      {tab === "budget" && <Budget showMoney={showMoney} />}
      {edit && <PositionModal p={edit} onClose={() => { setEdit(null); inv(); }} />}
      {fill && <FillModal p={fill} onClose={() => { setFill(null); inv(); }} />}
      {link && <LinkModal p={link} onClose={() => { setLink(null); inv(); }} />}
    </div>
  );
}

function Budget({ showMoney }: { showMoney: boolean }) {
  const [by, setBy] = useState("department");
  const q = useQuery({ queryKey: ["positions-summary", by], queryFn: () => positionsApi.summary(by) });
  const s = q.data;
  const cols: GridColumn<BudgetRow>[] = [{ key: "group", label: by[0].toUpperCase() + by.slice(1).replace("_", " "), className: "font-medium text-gray-800" }, { key: "open", label: "Open", type: "number" }, { key: "filled", label: "Filled", type: "number" }, { key: "frozen", label: "Frozen", type: "number" }, { key: "total", label: "Seats", type: "number" }, { key: "fill_pct", label: "Fill %", type: "percent" },
    ...(showMoney ? [{ key: "budgeted", label: "Budgeted (non-frozen)", type: "money" as const }, { key: "open_budget", label: "Open seats budget", type: "money" as const }, { key: "filled_budget", label: "Filled seats budget", type: "money" as const }, { key: "actual", label: "Actual (holders)", type: "money" as const },
      { key: "variance", label: "Budget − actual", type: "money" as const, render: (r: BudgetRow) => <span className={(r.variance || 0) < 0 ? "text-red-700" : "text-green-700"}>{money(r.variance || 0)}</span> }] : [])];
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3"><StatTile label="Seats" value={s?.total.total ?? "—"} accent /><StatTile label="Open" value={s?.total.open ?? "—"} /><StatTile label="Fill %" value={s?.total.fill_pct != null ? `${s.total.fill_pct}%` : "—"} />
        {showMoney && <><StatTile label="Budgeted" value={s?.total.budgeted != null ? money(s.total.budgeted) : "—"} /><StatTile label="Budget − actual (filled)" value={s?.total.variance != null ? money(s.total.variance) : "—"} sub="negative = holders cost more than their seats" /></>}</div>
      <DataGrid<BudgetRow> columns={cols} rows={s?.rows || []} rowKey={(r) => r.group} minWidth={showMoney ? 1200 : 700} exportName={`headcount-budget-by-${by}`} density="compact" totals={s?.total ? { ...s.total, group: "Total" } : null}
        filters={<select className="input !py-1.5 text-sm w-40" value={by} onChange={(e) => setBy(e.target.value)} aria-label="Group by"><option value="department">By department</option><option value="entity">By entity</option><option value="grade">By grade</option><option value="location">By location</option><option value="fiscal_year">By FY</option></select>} />
    </div>
  );
}

function PositionModal({ p, onClose }: { p: Partial<Position>; onClose: () => void }) {
  const emps = useQuery({ queryKey: ["employees"], queryFn: () => employeeApi.list() });
  const list = ((emps.data || []) as any[]).filter((e) => e.status !== "terminated");
  const [f, setF] = useState<any>({ ...p });
  const save = useMutation({ mutationFn: () => (p.id ? positionsApi.update(p.id, f) : positionsApi.create(f)), onSuccess: () => { toast.success("Saved"); onClose(); }, onError: (e) => toast.error(errText(e)) });
  const fyOk = !f.fiscal_year || (FY_RE.test(f.fiscal_year) && Number(f.fiscal_year.slice(5, 7)) === (Number(f.fiscal_year.slice(2, 4)) + 1) % 100);
  const errs = collect({
    title: v.required(String(f.title || "").trim(), "Title") || v.maxLen(f.title, 200, "Title"),
    department: !p.id ? v.required(String(f.department || "").trim(), "Department") : null,
    fiscal_year: (!p.id ? v.required(f.fiscal_year, "Fiscal year") : null) || (fyOk ? null : "Fiscal year looks like 2026-27"),
    budgeted_ctc: v.range(f.budgeted_ctc, 0, undefined, "Budgeted salary"),
    grade: v.maxLen(f.grade, 40, "Grade"), notes: v.maxLen(f.notes, 4000, "Notes"),
  });
  const submit = () => { if (Object.keys(errs).length) { toast.error(firstError(errs)); return; } save.mutate(); };
  return (
    <WideModal title={p.id ? `${p.code} · ${p.title}` : "New position"} onClose={onClose} footer={<button className="btn-primary" disabled={save.isPending} onClick={submit}>Save</button>}>
      <div className="grid sm:grid-cols-2 md:grid-cols-3 gap-3">
        <div className="sm:col-span-2"><label className={L}>Title<Req /></label><input className={F} maxLength={200} value={f.title || ""} onChange={(e) => setF({ ...f, title: e.target.value })} /><FieldError msg={errs.title} />{!p.id && <span className="block text-[11px] text-gray-400 mt-0.5">The seat code is given automatically on save</span>}</div>
        <div><label className={L}>Grade</label><input className={F} maxLength={40} value={f.grade || ""} onChange={(e) => setF({ ...f, grade: e.target.value })} /></div>
        <div><label className={L}>Department{!p.id && <Req />}</label><input className={F} maxLength={120} list="pos-depts" value={f.department || ""} onChange={(e) => setF({ ...f, department: e.target.value })} /><datalist id="pos-depts">{Array.from(new Set(list.map((e) => e.department).filter(Boolean))).map((x) => <option key={x} value={x} />)}</datalist><FieldError msg={errs.department} /></div>
        <div><label className={L}>Legal entity</label><input className={F} maxLength={200} value={f.entity || ""} onChange={(e) => setF({ ...f, entity: e.target.value })} /><span className="block text-[11px] text-gray-400 mt-0.5">The employer — decides the CRA payroll account (BN / RP) used</span></div>
        <div><label className={L}>Location</label><input className={F} value={f.location || ""} onChange={(e) => setF({ ...f, location: e.target.value })} /></div>
        <div><label className={L}>Fiscal year{!p.id && <Req />}</label><input className={F} maxLength={7} placeholder="2026-27" value={f.fiscal_year || ""} onChange={(e) => setF({ ...f, fiscal_year: e.target.value.trim() })} /><span className="block text-[11px] text-gray-400 mt-0.5">Your fiscal year label, e.g. 2026-27</span><FieldError msg={errs.fiscal_year} /></div>
        <div><label className={L}>Budgeted annual cost (CAD)</label><input type="number" className={F} min={0} step="1" value={f.budgeted_ctc ?? 0} onChange={(e) => setF({ ...f, budgeted_ctc: Number(e.target.value) })} /><span className="block text-[11px] text-gray-400 mt-0.5">Full yearly employer cost for this seat</span><FieldError msg={errs.budgeted_ctc} /></div>
        <div><label className={L}>Status</label><select className={F} value={f.status || "open"} onChange={(e) => setF({ ...f, status: e.target.value })} disabled={p.status === "filled"}><option value="open">Open</option><option value="frozen">Frozen</option>{p.status === "filled" && <option value="filled">Filled</option>}</select></div>
        <div className="sm:col-span-2"><label className={L}>Reports to</label><select className={F} value={f.manager_id || ""} onChange={(e) => setF({ ...f, manager_id: e.target.value || null })}><option value="">—</option>{list.map((e) => <option key={e.id} value={e.id}>{e.first_name} {e.last_name} · {e.department || ""}</option>)}</select></div>
        <div className="sm:col-span-2 md:col-span-3"><label className={L}>Notes</label><textarea className={F} rows={2} maxLength={4000} value={f.notes || ""} onChange={(e) => setF({ ...f, notes: e.target.value })} /></div>
      </div>
    </WideModal>
  );
}

function FillModal({ p, onClose }: { p: Position; onClose: () => void }) {
  const emps = useQuery({ queryKey: ["employees"], queryFn: () => employeeApi.list() });
  const list = ((emps.data || []) as any[]).filter((e) => e.status !== "terminated");
  const [f, setF] = useState<any>({ employee_id: "", actual_ctc: "", filled_on: "" });
  const [qq, setQq] = useState("");
  const m = useMutation({ mutationFn: () => positionsApi.fill(p.id, { employee_id: f.employee_id, actual_ctc: f.actual_ctc ? Number(f.actual_ctc) : undefined, filled_on: f.filled_on || undefined }), onSuccess: () => { toast.success("Filled"); onClose(); }, onError: (e) => toast.error(errText(e)) });
  return (
    <WideModal title={`Fill ${p.code} · ${p.title}`} onClose={onClose} footer={<button className="btn-primary" disabled={m.isPending || !f.employee_id} onClick={() => { const e = collect({ actual_ctc: v.range(f.actual_ctc, 0, undefined, "Actual salary"), filled_on: v.notFuture(f.filled_on, "Filled on") }); if (Object.keys(e).length) { toast.error(firstError(e)); return; } m.mutate(); }}>Fill position</button>}>
      <div className="grid md:grid-cols-2 gap-3">
        <div><label className={L}>Person</label><input className={`${F} mb-1`} placeholder="Search…" value={qq} onChange={(e) => setQq(e.target.value)} />
          <div className="max-h-64 overflow-y-auto border border-surface-border rounded-lg divide-y divide-surface-border">{list.filter((e) => !qq || `${e.first_name} ${e.last_name}`.toLowerCase().includes(qq.toLowerCase())).map((e) => <label key={e.id} className="flex items-center gap-2 px-2 py-1 text-sm"><input type="radio" name="emp" checked={f.employee_id === e.id} onChange={() => setF({ ...f, employee_id: e.id })} /> {e.first_name} {e.last_name} <span className="text-[11px] text-gray-400">{e.job_title || ""} · {e.department || ""}</span></label>)}</div></div>
        <div className="space-y-3"><div><label className={L}>Actual annual salary (blank = from the employee record)</label><input type="number" min={0} className={F} value={f.actual_ctc} onChange={(e) => setF({ ...f, actual_ctc: e.target.value })} /><span className="block text-[11px] text-gray-400 mt-0.5">Budgeted for this seat: {money(p.budgeted_ctc || 0)}</span><FieldError msg={v.range(f.actual_ctc, 0, undefined, "Actual salary")} /></div>
          <div><label className={L}>Filled on (blank = today)</label><input type="date" max={todayISO()} className={F} value={f.filled_on} onChange={(e) => setF({ ...f, filled_on: e.target.value })} /><FieldError msg={v.notFuture(f.filled_on, "Filled on")} /></div>
          <p className="text-xs text-gray-500">The holder's salary is stored as a snapshot on the seat; later revisions do not change it until the seat is refilled.</p></div>
      </div>
    </WideModal>
  );
}

function LinkModal({ p, onClose }: { p: Position; onClose: () => void }) {
  const q = useQuery({ queryKey: ["positions-reqs"], queryFn: positionsApi.requisitions });
  const m = useMutation({ mutationFn: (id: string | null) => positionsApi.link(p.id, id), onSuccess: () => { toast.success("Saved"); onClose(); }, onError: (e) => toast.error(errText(e)) });
  const cols: GridColumn<any>[] = [{ key: "req_number", label: "Requisition", width: "110px", className: "font-medium text-gray-800" }, { key: "designation", label: "Designation" }, { key: "department", label: "Department" }, { key: "grade", label: "Grade", width: "70px" }, { key: "location", label: "Location" }, { key: "openings", label: "Openings", type: "number", width: "85px" }, { key: "status", label: "Status", width: "90px" }, { key: "hiring_manager_name", label: "Hiring manager" }, { key: "target_date", label: "Target", type: "date", width: "100px" },
    { key: "act", label: "", sortable: false, render: (r) => <button className="text-xs text-primary-700" onClick={() => m.mutate(r.id)}>Link</button> }];
  return (
    <WideModal title={`Requisition for ${p.code}`} onClose={onClose} footer={p.requisition_id && <button className="btn-secondary" onClick={() => m.mutate(null)}>Unlink {p.requisition_number}</button>}>
      <p className="text-xs text-gray-500 mb-2">{q.data?.note || "Read-only reference — hiring is managed on the Hiring screens."}{p.requisition_number ? ` Currently linked: ${p.requisition_number}.` : ""}</p>
      <DataGrid columns={cols} rows={q.data?.requisitions || []} rowKey={(r) => r.id} minWidth={900} density="compact" empty="No requisitions on record." />
    </WideModal>
  );
}
