import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Crown, RefreshCw } from "lucide-react";
import { PageHeader, Spinner } from "../components/ui";
import { formatCell } from "../components/DataGrid";
import SimpleChart from "../components/SimpleChart";
import { errText, reportsApi, when, type ColType, type DashTile } from "../api/platform";

// Where a tile's number comes from, so every figure can be opened and checked.
const SOURCE: Record<string, string> = {
  headcount: "headcount", joiners: "joiners_leavers", leavers: "joiners_leavers", attrition: "attrition",
  probation: "probation_due", on_leave: "leave_usage", present: "attendance_summary",
  approvals: "approvals_ageing", payroll_cost: "payroll_cost",
};
const CHART_UNIT: Record<string, ColType> = { payroll_cost: "money" };

function Tile({ t }: { t: DashTile }) {
  const body = (
    <>
      <div className="text-[11px] font-semibold text-gray-400 uppercase tracking-wide">{t.label}</div>
      {t.value === null
        ? <div className="text-sm font-medium text-gray-400 mt-2.5 mb-1">{t.empty || "No data yet"}</div>
        : <div className="text-2xl font-bold text-gray-800 mt-1.5 tabular-nums">{formatCell(t.value, t.unit)}</div>}
      {t.sub && <div className="text-xs text-gray-500 mt-1">{t.sub}</div>}
    </>
  );
  const src = SOURCE[t.key];
  return src ? <Link to={`/reports/hub?report=${src}`} className="card block hover:shadow-card-hover transition-shadow" title="Open the report behind this number">{body}</Link>
    : <div className="card">{body}</div>;
}

export default function MDDashboardPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["reports", "dashboard"], queryFn: reportsApi.dashboard, staleTime: 30_000 });
  if (q.isLoading) return <div className="grid place-items-center py-20"><Spinner className="h-8 w-8" /></div>;
  if (q.isError || !q.data) return <div className="card text-sm text-red-600">Could not load the dashboard. {errText(q.error)}</div>;
  const d = q.data;
  return (
    <div>
      <PageHeader title="Executive Dashboard" icon={Crown} subtitle={`${d.scope} · as of ${when(d.as_of)}`}
        actions={<button className="btn-secondary text-xs inline-flex items-center gap-1.5" disabled={q.isFetching} onClick={() => qc.invalidateQueries({ queryKey: ["reports", "dashboard"] })}><RefreshCw className={`w-3.5 h-3.5 ${q.isFetching ? "animate-spin" : ""}`} /> Refresh</button>} />
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
        {d.tiles.map((t) => <Tile key={t.key} t={t} />)}
      </div>
      {d.charts.length === 0
        ? <div className="card text-center py-12 text-sm text-gray-400">No data yet. Charts appear here once employees, leave or pay runs are recorded.</div>
        : <div className="grid lg:grid-cols-2 gap-4">
            {d.charts.map((c) => (
              <div key={c.key} className="card">
                <div className="flex items-center justify-between mb-2">
                  <h3 className="text-sm font-semibold text-gray-700">{c.title}</h3>
                  <Link className="text-xs text-primary-600 hover:underline" to={`/reports/hub?report=${c.key}`}>Open report</Link>
                </div>
                <SimpleChart spec={c} data={c.data} unit={CHART_UNIT[c.key] || "number"} />
              </div>
            ))}
          </div>}
      <p className="text-xs text-gray-400 mt-4">
        Every number comes from your own records at the time shown above. A tile with nothing behind it says “No data yet”.
        {d.shows_pay ? " Payroll cost counts finalised pay runs only." : " Payroll cost is shown to org admin, payroll and finance roles only."}
      </p>
    </div>
  );
}
