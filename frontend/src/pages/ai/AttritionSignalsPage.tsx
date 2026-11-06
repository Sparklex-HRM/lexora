import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Radar, Info } from "lucide-react";
import { PageHeader, Spinner, StatTile } from "../../components/ui";
import DataGrid, { WideModal, type GridColumn } from "../../components/DataGrid";
import { errText, when, day } from "../../api/platform";
import { attritionApi, type RiskRow } from "../../api/ai";

const BAND: Record<string, string> = { high: "bg-red-100 text-red-700", watch: "bg-amber-100 text-amber-700", low: "bg-green-100 text-green-700" };

export default function AttritionSignalsPage() {
  const [band, setBand] = useState("");
  const [dept, setDept] = useState("");
  const [open, setOpen] = useState<RiskRow | null>(null);
  const q = useQuery({ queryKey: ["attrition"], queryFn: () => attritionApi.signals() });
  const rows = useMemo(() => (q.data?.rows || []).filter((r) => (!band || r.band === band) && (!dept || r.department === dept)), [q.data, band, dept]);
  const depts = useMemo(() => Array.from(new Set((q.data?.rows || []).map((r) => r.department).filter(Boolean))).sort() as string[], [q.data]);
  if (q.isLoading) return <div className="grid place-items-center py-24"><Spinner className="h-8 w-8" /></div>;
  if (q.isError || !q.data) return <div className="card text-sm text-red-600">Could not load the signals. {errText(q.error)}</div>;
  const d = q.data;
  const tenure = (r: RiskRow) => r.hire_date ? ((Date.now() - new Date(r.hire_date).getTime()) / 31557600000).toFixed(1) : "";
  const cols: GridColumn<RiskRow>[] = [
    { key: "score", label: "Score", type: "number", width: "70px", className: "font-semibold text-gray-800" },
    { key: "band", label: "Band", render: (r) => <span className={`inline-block text-[11px] font-semibold px-2 py-0.5 rounded-full ${BAND[r.band]}`}>{r.band}</span>, text: (r) => r.band, width: "80px" },
    { key: "name", label: "Name", className: "text-gray-800" }, { key: "employee_number", label: "Employee no." }, { key: "department", label: "Department" },
    { key: "job_title", label: "Designation" }, { key: "manager_name", label: "Manager" },
    { key: "tenure", label: "Tenure (y)", type: "number", render: (r) => tenure(r), text: (r) => tenure(r) },
    { key: "hire_date", label: "Joined", type: "date" },
    { key: "factors", label: "Factors", type: "number", render: (r) => r.factors.length, text: (r) => String(r.factors.length) },
    { key: "top_factor", label: "Biggest factor" },
  ];
  return (
    <div>
      <PageHeader title="Flight-risk signals" icon={Radar} subtitle={`${d.scope} · ${d.rows.length} people · as of ${when(d.as_of)}`} />
      <div className="card mb-4 border-amber-200 bg-amber-50/40 text-sm text-gray-700 flex gap-3">
        <Info className="w-5 h-5 text-amber-600 shrink-0" />
        <div><b>This is a heuristic, not a prediction.</b> Each rule adds fixed points and shows the rows it came from. Use it as a prompt for a conversation, never as a decision on its own. {d.label}</div>
      </div>
      <div className="grid grid-cols-3 gap-3 mb-4">
        <StatTile label="High (50+)" value={d.bands.high} /><StatTile label="Watch (25–49)" value={d.bands.watch} /><StatTile label="Low" value={d.bands.low} />
      </div>
      <DataGrid<RiskRow> rows={rows} rowKey={(r) => r.employee_id} columns={cols} exportName="flight-risk-signals" minWidth={1000}
        initialSort={{ key: "score", dir: "desc" }} onRowClick={setOpen} empty="No active employees in your scope."
        filters={<>
          <select className="input !py-1.5 text-sm w-36" value={band} onChange={(e) => setBand(e.target.value)} aria-label="Band"><option value="">All bands</option><option value="high">High</option><option value="watch">Watch</option><option value="low">Low</option></select>
          <select className="input !py-1.5 text-sm w-44" value={dept} onChange={(e) => setDept(e.target.value)} aria-label="Department"><option value="">All departments</option>{depts.map((x) => <option key={x} value={x}>{x}</option>)}</select>
        </>} />
      {open && (
        <WideModal title={`${open.name} — score ${open.score} (${open.band})`} onClose={() => setOpen(null)}>
          <p className="text-xs text-gray-500 mb-3">{open.employee_number} · {open.department || "—"} · {open.job_title || "—"} · manager {open.manager_name || "—"} · joined {day(open.hire_date)}</p>
          {open.factors.length === 0 ? <p className="text-sm text-gray-600">No rule fired for this person. Score 0.</p> : (
            <div className="card !p-0 overflow-x-auto">
              <table className="w-full text-[13px]">
                <thead className="bg-surface text-[11px] uppercase tracking-wide text-gray-500 text-left"><tr><th className="px-3 py-2">Factor</th><th className="px-3 py-2 text-right">Points</th><th className="px-3 py-2">What the records show</th><th className="px-3 py-2">Rule</th></tr></thead>
                <tbody>{open.factors.map((f) => <tr key={f.factor} className="border-t border-surface-border"><td className="px-3 py-1.5 font-medium text-gray-800">{f.factor}</td><td className="px-3 py-1.5 text-right tabular-nums">+{f.points}</td><td className="px-3 py-1.5 text-gray-700">{f.evidence}</td><td className="px-3 py-1.5 text-gray-500">{f.rule}</td></tr>)}</tbody>
                <tfoot><tr className="border-t-2 border-surface-border bg-surface font-semibold"><td className="px-3 py-2">Total</td><td className="px-3 py-2 text-right">{open.score}</td><td colSpan={2} className="px-3 py-2 text-gray-500 font-normal">Capped at 100 · low &lt; 25 · watch 25–49 · high 50+</td></tr></tfoot>
              </table>
            </div>)}
          <details className="mt-4 text-xs text-gray-500"><summary className="cursor-pointer">How the score is built</summary><pre className="whitespace-pre-wrap mt-2 font-sans">{d.method}</pre></details>
        </WideModal>
      )}
    </div>
  );
}
