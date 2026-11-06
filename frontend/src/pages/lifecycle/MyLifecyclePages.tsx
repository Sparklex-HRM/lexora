import { useQuery } from "@tanstack/react-query";
import { Wallet, FileSignature } from "lucide-react";
import { PageHeader } from "../../components/ui";
import { compApi, revisionsApi, lettersApi, cur, fmtDate, errText } from "../../api/lifecycle";
import { SalaryTimeline, LetterList } from "../../components/lifecycle/EmployeeLifecyclePanel";
import { Stat } from "../common/_shared";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";
import { useSalaryVisible, SalaryToggle } from "../../components/SalaryToggle";
import { todayISO } from "../../lib/datetime";   // 2026-10-25: UTC store, org-zone today

/* Salary over time (portal batch 3): one step series from the salary history this page already loads.
   Masked ("Show salary" off): the shape stays, axis values and tooltip amounts are hidden. */
const cadAxis = (v: number) => `$${Math.round(v).toLocaleString("en-CA")}`;

function SalaryChart({ history, currency }: { history: any[]; currency?: string }) {
  const [shown] = useSalaryVisible();
  const hourly = (history[0]?.pay_type || "") === "hourly";
  const pts = [...history]
    .filter((h) => h.effective_from && Number(hourly ? h.hourly_rate : h.annual_amount) > 0)
    .sort((a, b) => String(a.effective_from).localeCompare(String(b.effective_from)))
    .map((h) => ({ date: String(h.effective_from).slice(0, 10), amount: Number(hourly ? h.hourly_rate : h.annual_amount),
                   pct: h.change_percent === undefined || h.change_percent === null ? null : Number(h.change_percent) }));
  if (pts.length < 1) return null;
  // carry the current salary to today so the last step has a visible length
  const today = todayISO();
  const data = pts[pts.length - 1].date < today ? [...pts, { date: today, amount: pts[pts.length - 1].amount, pct: null, now: true } as any] : pts;
  const ts = (d: string) => new Date(d + "T00:00:00").getTime();
  const rows = data.map((p: any) => ({ ...p, t: ts(p.date) }));
  const yearTick = (t: number) => new Date(t).toLocaleDateString("en-CA", { month: "short", year: "numeric" });
  return (
    <div className="card !p-0 mb-4" data-testid="salary-chart">
      <div className="px-4 py-2.5 border-b border-surface-border flex items-center justify-between gap-2">
        <div className="font-display font-semibold text-gray-800 text-sm">{hourly ? "Hourly rate" : "Annual salary"} over time</div>
        <SalaryToggle />
      </div>
      <div className="px-2 pt-3 pb-1 h-64 text-primary-600">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 8, right: 16, bottom: 4, left: shown ? 8 : 0 }}>
            <CartesianGrid stroke="#e5e7eb" strokeDasharray="0" vertical={false} />
            <XAxis dataKey="t" type="number" scale="time" domain={["dataMin", "dataMax"]} tickFormatter={yearTick}
              tick={{ fontSize: 11, fill: "#6b7280" }} tickLine={false} axisLine={{ stroke: "#d1d5db" }} minTickGap={32} />
            <YAxis tickFormatter={shown ? cadAxis : () => ""} tick={shown ? { fontSize: 11, fill: "#6b7280" } : false} width={shown ? 92 : 8}
              tickLine={false} axisLine={false} domain={["auto", "auto"]} />
            <Tooltip cursor={{ stroke: "#9ca3af", strokeWidth: 1 }}
              content={({ active, payload }: any) => {
                const p = active && payload?.[0]?.payload;
                if (!p) return null;
                return (
                  <div className="rounded-lg border border-surface-border bg-white px-3 py-2 shadow-sm text-xs text-gray-700">
                    <div className="font-semibold text-gray-800">{p.now ? "Today" : `Effective ${fmtDate(p.date)}`}</div>
                    <div className="tabular-nums">{shown ? cur(p.amount, currency) : "Amount hidden"}{hourly ? " / hour" : " / year"}</div>
                    {p.pct !== null && p.pct !== undefined && <div className={p.pct < 0 ? "text-red-600" : "text-emerald-700"}>{p.pct > 0 ? "+" : ""}{p.pct.toFixed(1)}% change</div>}
                  </div>);
              }} />
            <Line type="stepAfter" dataKey="amount" stroke="currentColor" strokeWidth={2} isAnimationActive={false}
              dot={(props: any) => props.payload?.now ? <g key={props.key} /> : <circle key={props.key} cx={props.cx} cy={props.cy} r={4} fill="currentColor" stroke="#fff" strokeWidth={2} />}
              activeDot={{ r: 5, stroke: "#fff", strokeWidth: 2 }} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      {!shown && <p className="px-4 pb-2 text-[11px] text-gray-500">Amounts are hidden. Click Show salary to see them.</p>}
    </div>
  );
}

/* Self-service (read-only): My compensation · My letters. */

export default function MyCompensationPage() {
  // history comes from the employee service; the break-up from payroll (it may be off for the org)
  const hist = useQuery({ queryKey: ["my-comp"], queryFn: () => compApi.mine(), retry: false });
  const brk = useQuery({ queryKey: ["my-breakup"], queryFn: () => revisionsApi.myBreakup(), retry: false });
  const c = hist.data?.current;
  const comps: Record<string, number> = brk.data?.components || c?.components || {};
  const names: Record<string, string> = brk.data?.component_names || {};
  const total = Object.values(comps).reduce((s, v) => s + (Number(v) || 0), 0);
  const hourly = c?.pay_type === "hourly";
  return (
    <div>
      <PageHeader title="My compensation" icon={Wallet} subtitle="Your current salary, its break-up and every change. This page is read-only." />
      {hist.isError ? <div className="card text-sm text-gray-500 text-center py-10">{errText(hist.error, "Your compensation is not available.")}</div> : !c ? <div className="card text-sm text-gray-400 text-center py-10">Loading…</div> : <>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
          <Stat label={hourly ? "Hourly rate" : "Annual salary"} value={cur(hourly ? c.hourly_rate : c.annual_amount, c.currency)} accent />
          {!hourly && <Stat label="Per month" value={cur(c.annual_amount / 12, c.currency)} />}
          <Stat label="Effective from" value={<span className="text-base">{fmtDate(c.effective_from)}</span>} />
          <Stat label="Changes on record" value={Math.max((hist.data?.history || []).length - 1, 0)} />
        </div>
        {Object.keys(comps).length > 0 && <div className="card !p-0 mb-4">
          <div className="px-4 py-2.5 border-b border-surface-border font-display font-semibold text-gray-800 text-sm">Salary break-up</div>
          <table className="w-full text-sm">
            <thead className="bg-surface text-gray-400 text-[10px] uppercase"><tr><th className="px-4 py-2 text-left">Component</th><th className="px-4 py-2 text-right">Per month</th><th className="px-4 py-2 text-right">Per year</th></tr></thead>
            <tbody>{Object.entries(comps).map(([k, v]) => (
              <tr key={k} className="border-t border-surface-border"><td className="px-4 py-1.5 text-gray-700">{names[k] || k}</td><td className="px-4 py-1.5 text-right tabular-nums">{cur(v, c.currency)}</td><td className="px-4 py-1.5 text-right tabular-nums">{cur(Number(v) * 12, c.currency)}</td></tr>))}
              <tr className="border-t border-surface-border font-semibold"><td className="px-4 py-2">Total</td><td className="px-4 py-2 text-right tabular-nums">{cur(total, c.currency)}</td><td className="px-4 py-2 text-right tabular-nums">{cur(total * 12, c.currency)}</td></tr></tbody>
          </table>
          <p className="px-4 py-2 text-[11px] text-gray-500 border-t border-surface-border">Deductions and taxes are on your pay stub, not here.</p>
        </div>}
        <SalaryChart history={hist.data?.history || []} currency={c.currency} />
        <SalaryTimeline history={hist.data?.history || []} currency={c.currency} />
      </>}
    </div>
  );
}

export function MyLettersPage() {
  const { data: letters = [], isLoading, isError, error } = useQuery({ queryKey: ["my-letters"], queryFn: () => lettersApi.mine(), retry: false });
  return (
    <div>
      <PageHeader title="My letters" icon={FileSignature} subtitle="Letters issued to you by the company. Open one to view or download the PDF." />
      {isError ? <div className="card text-sm text-gray-500 text-center py-10">{errText(error, "Your letters are not available.")}</div>
        : isLoading ? <div className="card text-sm text-gray-400 text-center py-10">Loading…</div> : <LetterList letters={letters} />}
    </div>
  );
}
