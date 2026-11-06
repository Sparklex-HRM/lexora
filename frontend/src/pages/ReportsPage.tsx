import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQueries, useQuery } from "@tanstack/react-query";
import { BarChart3 } from "lucide-react";
import { PageHeader, Spinner } from "../components/ui";
import SimpleChart from "../components/SimpleChart";
import { errText, reportsApi, when, type Report } from "../api/platform";

// Analytics = the charts of the real reports, side by side, for one period.
// Which charts appear depends on what the signed-in role may open (the catalog).
const WANTED: { key: string; by?: string }[] = [
  { key: "headcount_trend" }, { key: "joiners_leavers" }, { key: "attrition" },
  { key: "headcount", by: "department" }, { key: "leave_usage" }, { key: "payroll_cost", by: "month" },
];
const RANGES = [{ v: 3, label: "Last 3 months" }, { v: 6, label: "Last 6 months" }, { v: 12, label: "Last 12 months" }, { v: 24, label: "Last 24 months" }];

export default function ReportsPage() {
  const [months, setMonths] = useState(12);
  const cat = useQuery({ queryKey: ["reports", "catalog"], queryFn: reportsApi.catalog });
  const range = useMemo(() => {
    const to = new Date();
    const from = new Date(to.getFullYear(), to.getMonth() - (months - 1), 1);
    const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    return { from: iso(from), to: iso(to) };
  }, [months]);
  const allowed = useMemo(() => WANTED.filter((w) => cat.data?.reports.some((r) => r.key === w.key)), [cat.data]);
  const results = useQueries({
    queries: allowed.map((w) => ({
      queryKey: ["reports", w.key, "analytics", range, w.by],
      queryFn: () => reportsApi.run(w.key, { ...(w.key === "headcount" ? {} : range), ...(w.by ? { by: w.by } : {}) }),
    })),
  });

  if (cat.isLoading) return <div className="grid place-items-center py-20"><Spinner className="h-8 w-8" /></div>;
  if (cat.isError) return <div className="card text-sm text-red-600">Could not load analytics. {errText(cat.error)}</div>;
  const asOf = results.find((r) => r.data)?.data?.as_of;
  return (
    <div>
      <PageHeader title="Analytics" icon={BarChart3} subtitle={`${cat.data?.scope || ""}${asOf ? ` · as of ${when(asOf)}` : ""}`}
        actions={<select className="input !py-1.5 text-sm !w-auto" value={months} onChange={(e) => setMonths(Number(e.target.value))} aria-label="Period">
          {RANGES.map((r) => <option key={r.v} value={r.v}>{r.label}</option>)}</select>} />
      {!allowed.length && <div className="card text-sm text-gray-500">There is nothing here for your role.</div>}
      <div className="grid lg:grid-cols-2 gap-4">
        {allowed.map((w, i) => {
          const q = results[i];
          const r: Report | undefined = q.data;
          const unit = r?.chart ? r.columns.find((c) => c.key === r.chart!.series[0]?.key)?.type || "number" : "number";
          return (
            <div key={w.key} className="card">
              <div className="flex items-center justify-between mb-2">
                <h3 className="text-sm font-semibold text-gray-700">{r?.title || cat.data?.reports.find((x) => x.key === w.key)?.title}{r?.period ? <span className="font-normal text-gray-400"> · {r.period}</span> : null}</h3>
                <Link className="text-xs text-primary-600 hover:underline" to={`/reports/hub?report=${w.key}`}>Open report</Link>
              </div>
              {q.isLoading ? <div className="grid place-items-center h-[240px]"><Spinner /></div>
                : q.isError || !r ? <div className="grid place-items-center h-[240px] text-sm text-red-600">Could not load. {errText(q.error)}</div>
                : !r.chart || !r.rows.length ? <div className="grid place-items-center h-[240px] text-sm text-gray-400">{r.empty || "No data yet"}</div>
                : <SimpleChart spec={r.chart} data={r.chart.data || r.rows} unit={unit} />}
            </div>
          );
        })}
      </div>
      <p className="text-xs text-gray-400 mt-4">Each chart is drawn from the report behind it — open the report to see the rows, the formula and to export them.</p>
    </div>
  );
}
