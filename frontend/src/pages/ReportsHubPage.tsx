import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, FileSpreadsheet, LayoutGrid } from "lucide-react";
import { PageHeader, Spinner } from "../components/ui";
import DataGrid, { formatCell, type GridColumn } from "../components/DataGrid";
import SimpleChart from "../components/SimpleChart";
import { errText, reportsApi, when, type CatalogEntry, type Report } from "../api/platform";
import { todayISO } from "../lib/datetime";   // 2026-10-25: UTC store, org-zone today

export default function ReportsHubPage() {
  const [sp, setSp] = useSearchParams();
  const key = sp.get("report") || "";
  const cat = useQuery({ queryKey: ["reports", "catalog"], queryFn: reportsApi.catalog });
  const [q, setQ] = useState("");
  const entry = cat.data?.reports.find((r) => r.key === key);

  const groups = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const out: Record<string, CatalogEntry[]> = {};
    for (const r of cat.data?.reports || []) {
      if (needle && !`${r.title} ${r.description} ${r.group}`.toLowerCase().includes(needle)) continue;
      (out[r.group] ||= []).push(r);
    }
    return out;
  }, [cat.data, q]);

  if (cat.isLoading) return <div className="grid place-items-center py-20"><Spinner className="h-8 w-8" /></div>;
  if (cat.isError || !cat.data) return <div className="card text-sm text-red-600">Could not load the reports. {errText(cat.error)}</div>;
  if (key && entry) return <ReportView entry={entry} onBack={() => setSp({})} />;

  return (
    <div>
      <PageHeader title="Reports Hub" icon={LayoutGrid} subtitle={`Every report is built from your own records · ${cat.data.scope}`}
        actions={<input className="input !py-1.5 text-sm w-48 sm:w-64" placeholder="Find a report…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Find a report" />} />
      {!Object.keys(groups).length && <div className="card text-sm text-gray-500">No report matches “{q}”.</div>}
      <div className="space-y-5">
        {Object.entries(groups).map(([g, list]) => (
          <div key={g}>
            <h3 className="font-display font-semibold text-gray-800 mb-2">{g} <span className="text-xs font-normal text-gray-400">· {list.length}</span></h3>
            <div className="grid sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
              {list.map((r) => (
                <button key={r.key} onClick={() => setSp({ report: r.key })} className="card text-left group hover:shadow-card-hover transition-shadow">
                  <div className="flex items-start justify-between">
                    <FileSpreadsheet className="w-5 h-5 text-primary-600" />
                    <ArrowRight className="w-4 h-4 text-primary-400 group-hover:translate-x-1 transition-transform" />
                  </div>
                  <div className="font-semibold text-gray-800 mt-2">{r.title}</div>
                  <div className="text-xs text-gray-500 mt-0.5">{r.description}</div>
                </button>
              ))}
            </div>
          </div>
        ))}
        {!q && cat.data.to_add.length > 0 && (
          <div>
            <h3 className="font-display font-semibold text-gray-800 mb-2">To add</h3>
            <div className="card !p-0">
              <table className="w-full text-sm">
                <thead className="text-left text-[11px] uppercase text-gray-500 bg-surface"><tr><th className="px-3 py-2">Report</th><th className="px-3 py-2">Area</th><th className="px-3 py-2">What it is waiting for</th></tr></thead>
                <tbody>{cat.data.to_add.map((t) => (
                  <tr key={t.key} className="border-t border-surface-border"><td className="px-3 py-1.5 text-gray-700">{t.title}</td><td className="px-3 py-1.5 text-gray-500">{t.group}</td><td className="px-3 py-1.5 text-gray-500">{t.reason}</td></tr>))}</tbody>
              </table>
            </div>
            <p className="text-xs text-gray-400 mt-2">These are not built yet. Nothing is shown for them — not even sample numbers.</p>
          </div>
        )}
      </div>
    </div>
  );
}


function ReportView({ entry, onBack }: { entry: CatalogEntry; onBack: () => void }) {
  const today = todayISO();
  const [p, setP] = useState<{ from: string; to: string; as_of: string; by: string; year: string }>({ from: "", to: "", as_of: today, by: "", year: "" });
  const has = (k: string) => entry.params.includes(k);
  const params = useMemo(() => ({
    ...(has("from") && p.from ? { from: p.from } : {}), ...(has("to") && p.to ? { to: p.to } : {}),
    ...(has("as_of") && p.as_of ? { as_of: p.as_of } : {}), ...(has("by") && p.by ? { by: p.by } : {}),
    ...(has("year") && p.year ? { year: Number(p.year) } : {}),
  }), [p, entry]);   // eslint-disable-line react-hooks/exhaustive-deps
  const q = useQuery({ queryKey: ["reports", entry.key, params], queryFn: () => reportsApi.run(entry.key, params), placeholderData: (prev) => prev });
  const r: Report | undefined = q.data;
  const cols: GridColumn[] = (r?.columns || []).map((c) => ({ key: c.key, label: c.label, type: c.type }));
  const unit = r?.chart ? r.columns.find((c) => c.key === r.chart!.series[0]?.key)?.type || "number" : "number";

  return (
    <div>
      <PageHeader title={entry.title} icon={FileSpreadsheet} subtitle={entry.description}
        actions={<button className="btn-secondary text-xs inline-flex items-center gap-1.5" onClick={onBack}><ArrowLeft className="w-3.5 h-3.5" /> All reports</button>} />
      <div className="flex flex-wrap items-end gap-3 mb-3">
        {has("as_of") && <div><label className="block text-xs font-semibold text-gray-500 mb-1">As of</label><input type="date" className="input !py-1.5 text-sm" value={p.as_of} max={today} onChange={(e) => setP({ ...p, as_of: e.target.value })} /></div>}
        {has("from") && <div><label className="block text-xs font-semibold text-gray-500 mb-1">From</label><input type="date" className="input !py-1.5 text-sm" value={p.from} max={p.to || today} onChange={(e) => setP({ ...p, from: e.target.value })} /></div>}
        {has("to") && <div><label className="block text-xs font-semibold text-gray-500 mb-1">To</label><input type="date" className="input !py-1.5 text-sm" value={p.to} min={p.from} onChange={(e) => setP({ ...p, to: e.target.value })} /></div>}
        {has("year") && <div><label className="block text-xs font-semibold text-gray-500 mb-1">Leave year</label><input type="number" className="input !py-1.5 text-sm w-28" placeholder="Latest" value={p.year} onChange={(e) => setP({ ...p, year: e.target.value })} /></div>}
        {has("by") && r?.options?.by && <div><label className="block text-xs font-semibold text-gray-500 mb-1">Group by</label>
          <select className="input !py-1.5 text-sm" value={p.by || r.options.by[0].value} onChange={(e) => setP({ ...p, by: e.target.value })}>{r.options.by.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</select></div>}
        {r && <div className="text-xs text-gray-500 ml-auto text-right">
          <div>As of <b>{when(r.as_of)}</b>{q.isFetching ? " · refreshing…" : ""}</div>
          <div>{[r.period, r.scope].filter(Boolean).join(" · ")}</div></div>}
      </div>

      {q.isLoading ? <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div>
        : q.isError || !r ? <div className="card text-sm text-red-600">Could not run this report. {errText(q.error)}</div>
        : (
          <div className="space-y-3">
            {r.problems.map((m, i) => <div key={i} className="rounded-lg bg-amber-50 text-amber-800 text-sm px-3 py-2">{m}</div>)}
            {r.summary && r.summary.length > 0 && (
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                {r.summary.map((s) => <div key={s.label} className="card"><div className="text-[11px] font-semibold text-gray-400 uppercase">{s.label}</div>
                  <div className="text-xl font-bold text-gray-800 mt-1 tabular-nums">{s.value === null || s.value === undefined ? <span className="text-sm font-medium text-gray-400">No data yet</span> : formatCell(s.value, s.type || "number")}</div></div>)}
              </div>
            )}
            {r.chart && r.rows.length > 1 && (
              <div className="card"><div className="text-sm font-semibold text-gray-700 mb-2">{r.chart.series.map((s) => s.label).join(" and ")}</div>
                <SimpleChart spec={r.chart} data={r.chart.data || r.rows} unit={unit} /></div>
            )}
            <DataGrid columns={cols} rows={r.rows} rowKey={(_row, i) => String(i)} totals={r.totals || null}
              exportName={`${entry.key}-${today}`} empty={r.empty || "No data yet."} minWidth={Math.max(720, cols.length * 120)} pageSizeDefault={50} />
            {(r.formula || (r.notes && r.notes.length > 0)) && (
              <div className="card text-sm">
                {r.formula && <p className="text-gray-700"><span className="font-semibold">How it is worked out: </span>{r.formula}</p>}
                {r.notes && r.notes.length > 0 && <ul className="list-disc pl-5 mt-2 space-y-1 text-gray-500 text-xs">{r.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>}
              </div>
            )}
          </div>
        )}
    </div>
  );
}
