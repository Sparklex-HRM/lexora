import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { DoorOpen, Plus, RefreshCw, Trash2 } from "lucide-react";
import { PageHeader, Spinner, StatTile } from "../../components/ui";
import DataGrid, { type GridColumn } from "../../components/DataGrid";
import { exitApi, errText, dmy, STATUS_TONE, type ExitCase, type CountRow } from "../../api/exit";
import StartExitModal from "./StartExitModal";
import { useUrlTab } from "../../search/useUrlTab";

/** Exits — every exit case in one dense grid: state buttons with counts, filters
 *  (department / type / last-working-day month / pending with), search, sort, export.
 *  scope="team" is the manager's "Team exits" view of the same grid. */

const STATE_BUTTONS = ["open", "submitted", "manager_review", "hr_review", "accepted", "clearance", "settlement", "settled", "closed", "withdrawn", "rejected", "on_hold", "all"];
const STATE_LABEL: Record<string, string> = { open: "Open", all: "All", submitted: "Submitted", manager_review: "Manager review", hr_review: "HR review", accepted: "Accepted", clearance: "Clearance", settlement: "Settlement", settled: "Settled", closed: "Closed", withdrawn: "Withdrawn", rejected: "Rejected", on_hold: "On hold" };

export default function ExitsPage({ scope = "org" }: { scope?: "org" | "team" }) {
  const nav = useNavigate();
  const meta = useQuery({ queryKey: ["exit-meta"], queryFn: exitApi.meta });
  const isHr = !!meta.data?.is_hr;
  const [tab, setTab] = useUrlTab<"cases" | "attrition" | "settings">("cases");   // ?tab= (global search deep link)
  const [status, setStatus] = useState("open");
  const [dept, setDept] = useState("");
  const [type, setType] = useState("");
  const [month, setMonth] = useState("");
  const [pending, setPending] = useState("");
  const [showNew, setShowNew] = useState(false);
  const list = useQuery({ queryKey: ["exit-cases", scope], queryFn: () => exitApi.list({ scope }) });
  const qc = useQueryClient();
  const tick = useMutation({
    mutationFn: () => exitApi.tick(),
    onSuccess: (r) => { toast.success(r.separated?.length ? `${r.separated.length} employee(s) marked as left` : "Nobody is due today"); qc.invalidateQueries({ queryKey: ["exit-cases"] }); qc.invalidateQueries({ queryKey: ["employees"] }); },
    onError: (e) => toast.error(errText(e)),
  });
  const all = list.data?.cases || [];
  const counts = list.data?.counts || {};
  const pendingOpts = useMemo(() => [...new Set(all.map((c) => c.pending_with).filter((p) => p && p !== "—"))].sort(), [all]);
  const rows = useMemo(() => all.filter((c) =>
    (status === "all" || (status === "open" ? !["closed", "withdrawn", "rejected"].includes(c.status) : c.status === status))
    && (!dept || c.department === dept) && (!type || c.exit_type === type)
    && (!month || (c.lwd || "").slice(0, 7) === month) && (!pending || c.pending_with === pending)), [all, status, dept, type, month, pending]);

  const columns: GridColumn<ExitCase>[] = [
    { key: "case_number", label: "Case", sortable: true, render: (r) => <span className="font-semibold text-primary-700">{r.case_number}</span> },
    { key: "employee_name", label: "Employee", sortable: true, render: (r) => <span><span className="font-medium text-gray-800">{r.employee_name}</span> <span className="text-gray-400">{r.employee_number}</span></span>, text: (r) => `${r.employee_name} ${r.employee_number || ""}` },
    { key: "department", label: "Department", sortable: true },
    { key: "exit_type_label", label: "Type", sortable: true },
    { key: "reason_category", label: "Reason", sortable: true },
    { key: "status_label", label: "State", sortable: true, render: (r) => <span className={STATUS_TONE[r.status] || "badge-purple"}>{r.status_label}</span>, text: (r) => r.status_label },
    { key: "pending_with", label: "Pending with", sortable: true },
    { key: "submitted_on", label: "Raised on", type: "date", sortable: true },
    { key: "lwd", label: "Last working day", type: "date", sortable: true },
    { key: "notice_days", label: "Notice", type: "number", sortable: true },
    { key: "notice_shortfall_days", label: "Short by", type: "number", sortable: true },
    { key: "settlement_status", label: "Settlement", sortable: true, render: (r) => r.settlement_status === "paid" ? <span className="badge-green">paid</span> : r.settlement_status === "started" ? <span className="badge-amber">in progress</span> : <span className="text-gray-400">—</span>, text: (r) => r.settlement_status },
    { key: "separated", label: "Left", render: (r) => r.separated ? <span className="badge-green">yes</span> : <span className="text-gray-400">no</span>, text: (r) => (r.separated ? "yes" : "no") },
  ];

  return (
    <div>
      <PageHeader title={scope === "team" ? "Team exits" : "Exits"} icon={DoorOpen}
        subtitle={scope === "team" ? "Resignations and exits of people who report to you." : "Resignation to last working day: notice, clearance, settlement and letters in one place."}
        actions={<div className="flex gap-2">
          {isHr && scope === "org" && <button className="btn-secondary inline-flex items-center gap-1.5" disabled={tick.isPending} title="Mark people whose last working day has arrived as left" onClick={() => tick.mutate()}><RefreshCw className="w-4 h-4" />Update last working days</button>}
          <button className="btn-primary inline-flex items-center gap-1.5" onClick={() => setShowNew(true)}><Plus className="w-4 h-4" />Start an exit</button>
        </div>} />
      {scope === "org" && (
        <div className="flex gap-1 mb-3 border-b border-surface-border">
          {([["cases", "Cases"], ["attrition", "Attrition summary"], ...(isHr ? [["settings", "Settings"]] : [])] as [string, string][]).map(([k, l]) => (
            <button key={k} onClick={() => setTab(k as any)} className={`px-3 py-2 text-sm font-semibold border-b-2 -mb-px ${tab === k ? "border-primary-600 text-primary-700" : "border-transparent text-gray-500 hover:text-gray-700"}`}>{l}</button>))}
        </div>)}
      {tab === "cases" && (<>
        <div className="flex flex-wrap gap-1.5 mb-3">
          {STATE_BUTTONS.map((s) => (
            <button key={s} onClick={() => setStatus(s)} className={`px-2.5 py-1 rounded-full text-xs font-semibold border ${status === s ? "bg-primary-600 text-white border-primary-600" : "bg-surface-card text-gray-600 border-surface-border hover:bg-surface"}`}>
              {STATE_LABEL[s]} <span className="opacity-70">{counts[s] ?? 0}</span>
            </button>))}
        </div>
        {list.isLoading ? <div className="grid place-items-center py-16"><Spinner /></div> : (
          <div className="card !p-0">
            <DataGrid<ExitCase> columns={columns} rows={rows} rowKey={(r) => r.id} exportName="exit-cases" minWidth={1250}
              searchPlaceholder="Search name, number, case" empty="No exit cases match." initialSort={{ key: "submitted_on", dir: "desc" }}
              onRowClick={(r) => nav(`/exits/${r.id}`)}
              filters={<>
                <select className="input !py-1 !w-auto text-sm" value={dept} onChange={(e) => setDept(e.target.value)}><option value="">All departments</option>{(list.data?.departments || []).map((d) => <option key={d}>{d}</option>)}</select>
                <select className="input !py-1 !w-auto text-sm" value={type} onChange={(e) => setType(e.target.value)}><option value="">All types</option>{(meta.data?.exit_types || []).map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}</select>
                <input type="month" className="input !py-1 !w-auto text-sm" value={month} onChange={(e) => setMonth(e.target.value)} title="Last working day month" />
                <select className="input !py-1 !w-auto text-sm" value={pending} onChange={(e) => setPending(e.target.value)}><option value="">Pending with anyone</option>{pendingOpts.map((p) => <option key={p}>{p}</option>)}</select>
              </>} />
          </div>)}
      </>)}
      {tab === "attrition" && <AttritionTab />}
      {tab === "settings" && isHr && <SettingsTab />}
      {showNew && <StartExitModal onClose={() => setShowNew(false)} onCreated={(c) => { setShowNew(false); nav(`/exits/${c.id}`); }} />}
    </div>
  );
}

function Bars({ title, rows }: { title: string; rows: CountRow[] }) {
  const max = Math.max(1, ...rows.map((r) => r.count));
  return (
    <div className="card">
      <h3 className="font-display font-semibold text-gray-800 mb-2 text-sm">{title}</h3>
      {!rows.length ? <p className="text-xs text-gray-400">No exits yet.</p> : (
        <table className="w-full text-xs"><tbody>{rows.map((r) => (
          <tr key={r.label} className="border-t border-surface-border">
            <td className="py-1 pr-2 whitespace-nowrap text-gray-700 w-40">{r.label}</td>
            <td className="py-1 w-full"><div className="h-3 rounded bg-primary-500/70" style={{ width: `${Math.max(4, (r.count / max) * 100)}%` }} /></td>
            <td className="py-1 pl-2 text-right tabular-nums font-semibold text-gray-800">{r.count}</td>
          </tr>))}</tbody></table>)}
    </div>
  );
}

function AttritionTab() {
  const [year, setYear] = useState<number>(new Date().getFullYear());
  const a = useQuery({ queryKey: ["exit-attrition", year], queryFn: () => exitApi.attrition(year) });
  if (a.isLoading || !a.data) return <div className="grid place-items-center py-16"><Spinner /></div>;
  const d = a.data;
  return (
    <div>
      <div className="flex items-center gap-2 mb-3">
        <span className="text-sm text-gray-600">Year</span>
        <select className="input !py-1 !w-auto text-sm" value={year} onChange={(e) => setYear(Number(e.target.value))}>{[0, 1, 2, 3].map((i) => new Date().getFullYear() - i).map((y) => <option key={y}>{y}</option>)}</select>
        <span className="text-xs text-gray-400">Counted from accepted exit cases. Withdrawn and rejected cases are not exits.</span>
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3 mb-4">
        <StatTile label="Exits" value={d.total_exits} accent />
        <StatTile label="People employed now" value={d.current_headcount} />
        <StatTile label="Average notice served" value={d.average_notice_served_days == null ? "—" : `${d.average_notice_served_days} days`} sub={`${d.notice_cases} case(s) with notice`} />
        <StatTile label="Left before notice ended" value={d.short_notice_cases} />
        <StatTile label="Withdrawn" value={d.withdrawn} sub={`${d.open_cases} case(s) still open`} />
      </div>
      <div className="grid md:grid-cols-2 gap-4">
        <Bars title="Exits by month (last working day)" rows={d.by_month} />
        <Bars title="Exits by reason" rows={d.by_reason} />
        <Bars title="Exits by department" rows={d.by_department} />
        <Bars title="Exits by type" rows={d.by_type} />
      </div>
    </div>
  );
}

function SettingsTab() {
  const qc = useQueryClient();
  const s = useQuery({ queryKey: ["exit-settings"], queryFn: exitApi.settings });
  const rules = useQuery({ queryKey: ["payroll-date-rules"], queryFn: exitApi.dateRules, retry: false });
  const save = useMutation({ mutationFn: (b: Record<string, any>) => exitApi.saveSettings(b), onSuccess: () => { toast.success("Saved"); qc.invalidateQueries({ queryKey: ["exit-settings"] }); }, onError: (e) => toast.error(errText(e)) });
  const saveRules = useMutation({ mutationFn: (b: string) => exitApi.saveDateRules(b), onSuccess: () => { toast.success("Pay-day rule saved"); qc.invalidateQueries({ queryKey: ["payroll-date-rules"] }); }, onError: (e) => toast.error(errText(e)) });
  const addPol = useMutation({ mutationFn: (b: Record<string, any>) => exitApi.addPolicy(b), onSuccess: () => { toast.success("Row added"); qc.invalidateQueries({ queryKey: ["exit-settings"] }); }, onError: (e) => toast.error(errText(e)) });
  const delPol = useMutation({ mutationFn: (id: string) => exitApi.deletePolicy(id), onSuccess: () => qc.invalidateQueries({ queryKey: ["exit-settings"] }), onError: (e) => toast.error(errText(e)) });
  const [pol, setPol] = useState({ grade: "", employment_type: "", stage: "any", notice_days: 30 });
  const [draft, setDraft] = useState<any>(null);
  if (s.isLoading || !s.data) return <div className="grid place-items-center py-16"><Spinner /></div>;
  const v = draft || s.data;
  const set = (k: string, val: any) => setDraft({ ...v, [k]: val });
  const L = "block text-xs font-semibold text-gray-500";
  return (
    <div className="grid lg:grid-cols-2 gap-4">
      <div className="card space-y-3">
        <h3 className="font-display font-semibold text-gray-800 text-sm">Notice and rehire</h3>
        <p className="text-xs text-gray-500">{s.data.note}</p>
        <div className="grid grid-cols-2 gap-3">
          <label className={L}>Notice days (confirmed)<input type="number" className="input mt-1" value={v.default_notice_days} onChange={(e) => set("default_notice_days", Number(e.target.value))} /></label>
          <label className={L}>Notice days (in probation)<input type="number" className="input mt-1" value={v.probation_notice_days} onChange={(e) => set("probation_notice_days", Number(e.target.value))} /></label>
        </div>
        <label className="flex items-center gap-2 text-sm text-gray-700"><input type="checkbox" checked={!!v.leave_extends_lwd} onChange={(e) => set("leave_extends_lwd", e.target.checked)} />Leave taken during notice pushes the last working day out</label>
        <label className="flex items-center gap-2 text-sm text-gray-700"><input type="checkbox" checked={!!v.hold_letters_until_settled} onChange={(e) => set("hold_letters_until_settled", e.target.checked)} />Hold relieving and experience letters until clearance and settlement are done</label>
        <label className={L}>Employee number on rehire
          <select className="input mt-1" value={v.rehire_number_policy} onChange={(e) => set("rehire_number_policy", e.target.value)}><option value="keep">Keep the old number</option><option value="new">Give a new number</option></select></label>
        <div className="flex justify-end"><button className="btn-primary" disabled={!draft || save.isPending} onClick={() => save.mutate({ default_notice_days: v.default_notice_days, probation_notice_days: v.probation_notice_days, leave_extends_lwd: v.leave_extends_lwd, hold_letters_until_settled: v.hold_letters_until_settled, rehire_number_policy: v.rehire_number_policy })}>Save</button></div>
        <hr className="border-surface-border" />
        <h3 className="font-display font-semibold text-gray-800 text-sm">Pay for part of a month (payroll)</h3>
        {rules.data ? (<>
          <p className="text-xs text-gray-500">{rules.data.rule}</p>
          <label className={L}>Pay a part month by
            <select className="input mt-1" value={rules.data.proration_basis} onChange={(e) => saveRules.mutate(e.target.value)}>{rules.data.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</select></label>
          <p className="text-[11px] text-gray-400">The basis is printed on the payslip.</p>
        </>) : <p className="text-xs text-gray-400">Payroll is not reachable – the pay-day rule cannot be shown.</p>}
      </div>
      <div className="card">
        <h3 className="font-display font-semibold text-gray-800 text-sm mb-2">Notice policy table</h3>
        <p className="text-xs text-gray-500 mb-2">The most specific matching row wins. With no matching row the two defaults on the left apply.</p>
        <div className="overflow-x-auto"><table className="w-full text-xs min-w-[520px]">
          <thead className="bg-surface text-gray-400 text-left text-[11px] uppercase"><tr><th className="px-2 py-1.5">Grade / designation</th><th className="px-2 py-1.5">Employment type</th><th className="px-2 py-1.5">Stage</th><th className="px-2 py-1.5 text-right">Notice days</th><th /></tr></thead>
          <tbody>
            {(s.data.notice_policies || []).map((p: any) => (
              <tr key={p.id} className="border-t border-surface-border"><td className="px-2 py-1.5">{p.grade || "Any"}</td><td className="px-2 py-1.5">{(p.employment_type || "Any").replace("_", " ")}</td><td className="px-2 py-1.5">{p.stage}</td><td className="px-2 py-1.5 text-right tabular-nums">{p.notice_days}</td>
                <td className="px-2 py-1.5 text-right"><button className="icon-btn text-gray-400 hover:text-red-500" title="Remove" onClick={() => delPol.mutate(p.id)}><Trash2 className="w-3.5 h-3.5" /></button></td></tr>))}
            <tr className="border-t border-surface-border bg-surface/50">
              <td className="px-2 py-1.5"><input className="input !py-1 text-xs" placeholder="e.g. Manager (blank = any)" value={pol.grade} onChange={(e) => setPol({ ...pol, grade: e.target.value })} /></td>
              <td className="px-2 py-1.5"><select className="input !py-1 text-xs" value={pol.employment_type} onChange={(e) => setPol({ ...pol, employment_type: e.target.value })}><option value="">Any</option><option value="full_time">Full time</option><option value="part_time">Part time</option><option value="contract">Contract</option><option value="seasonal">Seasonal</option></select></td>
              <td className="px-2 py-1.5"><select className="input !py-1 text-xs" value={pol.stage} onChange={(e) => setPol({ ...pol, stage: e.target.value })}><option value="any">Any</option><option value="probation">Probation</option><option value="confirmed">Confirmed</option></select></td>
              <td className="px-2 py-1.5"><input type="number" className="input !py-1 text-xs text-right" value={pol.notice_days} onChange={(e) => setPol({ ...pol, notice_days: Number(e.target.value) })} /></td>
              <td className="px-2 py-1.5 text-right"><button className="btn-secondary !py-1 text-xs" onClick={() => addPol.mutate(pol)}>Add</button></td>
            </tr>
          </tbody></table></div>
        <TemplateEditor title="Clearance checklist" hint="One line per department. Used when clearance starts on a case."
          rows={v.clearance_template} cols={[["department", "Department"], ["item", "What must be cleared"], ["owner_role", "Owner"]]}
          blank={{ department: "", item: "", owner_role: "hr_admin" }} onSave={(rows) => save.mutate({ clearance_template: rows })} />
        <TemplateEditor title="Exit interview questions" hint="Filled by HR or by the employee. Answers are visible to HR only."
          rows={v.interview_template} cols={[["question", "Question"], ["type", "Answer type"]]}
          blank={{ question: "", type: "text" }} onSave={(rows) => save.mutate({ interview_template: rows })} />
      </div>
    </div>
  );
}

const OWNER_OPTS: [string, string][] = [["manager", "Reporting manager"], ["hr_admin", "HR"], ["payroll_admin", "Payroll / finance"], ["org_admin", "Admin"]];
const TYPE_OPTS: [string, string][] = [["text", "Text"], ["rating", "Rating 1–5"], ["yes_no", "Yes / No"]];

function TemplateEditor({ title, hint, rows, cols, blank, onSave }: { title: string; hint: string; rows: any[]; cols: [string, string][]; blank: any; onSave: (rows: any[]) => void }) {
  const [draft, setDraft] = useState<any[] | null>(null);
  const v = draft || rows || [];
  const upd = (i: number, k: string, val: string) => setDraft(v.map((r, j) => (j === i ? { ...r, [k]: val } : r)));
  return (
    <div className="mt-5">
      <h3 className="font-display font-semibold text-gray-800 text-sm">{title}</h3>
      <p className="text-xs text-gray-500 mb-2">{hint}</p>
      <table className="w-full text-xs"><thead className="text-left text-[11px] uppercase text-gray-400"><tr>{cols.map(([k, l]) => <th key={k} className="px-1 py-1">{l}</th>)}<th /></tr></thead>
        <tbody>{v.map((r, i) => (
          <tr key={i} className="border-t border-surface-border">{cols.map(([k]) => (
            <td key={k} className="px-1 py-1">{k === "owner_role" ? <select className="input !py-1 text-xs" value={r[k]} onChange={(e) => upd(i, k, e.target.value)}>{OWNER_OPTS.map(([a, b]) => <option key={a} value={a}>{b}</option>)}</select>
              : k === "type" ? <select className="input !py-1 text-xs" value={r[k]} onChange={(e) => upd(i, k, e.target.value)}>{TYPE_OPTS.map(([a, b]) => <option key={a} value={a}>{b}</option>)}</select>
              : <input className="input !py-1 text-xs" value={r[k] || ""} onChange={(e) => upd(i, k, e.target.value)} />}</td>))}
            <td className="px-1 py-1 text-right"><button className="icon-btn text-gray-400 hover:text-red-500" title="Remove" onClick={() => setDraft(v.filter((_, j) => j !== i))}><Trash2 className="w-3.5 h-3.5" /></button></td></tr>))}</tbody></table>
      <div className="flex justify-between mt-2">
        <button className="btn-secondary !py-1 text-xs" onClick={() => setDraft([...v, { ...blank }])}>Add a line</button>
        <button className="btn-primary !py-1 text-xs" disabled={!draft} onClick={() => { onSave(v); setDraft(null); }}>Save {title.toLowerCase()}</button>
      </div>
    </div>
  );
}
