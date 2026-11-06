import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Sun, Calculator, Copy } from "lucide-react";
import toast from "react-hot-toast";
import { Link } from "react-router-dom";
import { PageHeader, Spinner } from "../../components/ui";
import { canadaApi, type StatCompute } from "../../api/canada";
import { Th, Td, Panel, Hint, GateBanner, Field, StatusPill, cad, inputCls, err } from "./_shared";

/** /payroll/canada/stat-holiday — per-province rule (org data) + line-by-line holiday pay for one employee.
 *  The result is a run entry (STAT, plus a premium line for hours worked) that carries its own calc lines,
 *  so the payslip detail keeps the working. */
export default function StatHolidayPayPage() {
  const { data: rules } = useQuery({ queryKey: ["ca-stat-rules"], queryFn: canadaApi.statRules });
  const { data: emps } = useQuery({ queryKey: ["ca-emps"], queryFn: canadaApi.employees });
  const [emp, setEmp] = useState("");
  const [holiday, setHoliday] = useState("2026-07-01");
  const [eligible, setEligible] = useState(true);
  const [hours, setHours] = useState("0");
  const [days, setDays] = useState("");
  const [manual, setManual] = useState("");
  const [res, setRes] = useState<StatCompute | null>(null);
  const run = useMutation({
    mutationFn: () => canadaApi.statCompute({ employee_id: emp, holiday, eligible, hours_worked_on_holiday: Number(hours) || 0,
      days_worked_in_lookback: days ? Number(days) : null, manual_amount: manual ? Number(manual) : null }),
    onSuccess: (r) => { setRes(r); if (!r.ok) toast.error(r.reason || "Could not compute"); }, onError: (e) => toast.error(err(e)),
  });
  if (!rules || !emps) return <div className="grid place-items-center py-20"><Spinner className="h-7 w-7" /></div>;
  return (
    <div>
      <PageHeader title="Statutory holiday pay" icon={Sun} subtitle="Province rule as your data · calculation shown line by line · paste the entry into Run Payroll → Adjust" />
      <Hint tone="warn">No formula ships with Lexora. Each province's rule (method, lookback days, divisor, worked-on-holiday premium) is entered under Canadian statutory values from the provincial employment-standards page and verified. Until then this screen says "no rule" and computes nothing.</Hint>
      <div className="grid lg:grid-cols-5 gap-3 mt-3">
        <Panel title="Province rules" sub="stat.<PROV>.rule" className="lg:col-span-2">
          <div className="overflow-x-auto max-h-[50vh]">
            <table className="w-full text-sm">
              <thead><tr><Th>Prov</Th><Th>Method</Th><Th right>Lookback</Th><Th right>Divisor</Th><Th right>Premium</Th><Th>Status</Th></tr></thead>
              <tbody>{rules.rules.map((r) => { const t = r.typed || {}; return (
                <tr key={r.key}><Td mono className="font-semibold">{r.province}</Td><Td>{t.method || <span className="text-gray-300">—</span>}</Td>
                  <Td right mono>{t.lookback_days ?? "—"}</Td><Td right mono>{t.divisor ?? "—"}</Td><Td right mono>{t.worked_premium ?? "—"}</Td><Td><StatusPill status={r.status} /></Td></tr>); })}</tbody>
            </table>
          </div>
          <div className="px-3 py-2 text-[11px] text-gray-500 border-t border-surface-border">Methods: <b>lookback_div</b> (wages in lookback ÷ divisor) · <b>lookback_per_day_worked</b> (wages ÷ days worked) · <b>daily_rate</b> (hours/day × rate) · <b>manual</b>. Edit under <Link className="underline text-primary-700" to="/payroll/canada/statutory-values">Canadian statutory values</Link>.</div>
        </Panel>
        <Panel title="Calculate for one employee" className="lg:col-span-3">
          <div className="p-3 grid md:grid-cols-3 gap-3">
            <Field label="Employee" className="md:col-span-2"><select className={inputCls} value={emp} onChange={(e) => setEmp(e.target.value)}>
              <option value="">— pick —</option>{emps.map((e: any) => <option key={e.id} value={e.id}>{e.employee_number} · {e.first_name} {e.last_name} · {e.province_of_employment}</option>)}</select></Field>
            <Field label="Holiday date"><input type="date" className={inputCls} value={holiday} onChange={(e) => setHoliday(e.target.value)} /></Field>
            <Field label="Hours worked on the holiday"><input type="number" className={inputCls} value={hours} onChange={(e) => setHours(e.target.value)} /></Field>
            <Field label="Days worked in lookback (per-day rules)"><input type="number" className={inputCls} value={days} onChange={(e) => setDays(e.target.value)} placeholder="only for lookback_per_day_worked" /></Field>
            <Field label="Manual amount (manual rule)"><input type="number" className={inputCls} value={manual} onChange={(e) => setManual(e.target.value)} /></Field>
            <label className="flex items-center gap-2 text-xs text-gray-700 md:col-span-2"><input type="checkbox" className="accent-primary-600" checked={eligible} onChange={(e) => setEligible(e.target.checked)} /> Eligible under the province's rule (you confirm the first/last-day or days-worked test)</label>
            <div className="flex items-end"><button type="button" className="btn-primary text-xs inline-flex items-center gap-1" disabled={!emp || run.isPending} onClick={() => run.mutate()}><Calculator className="w-3.5 h-3.5" /> Calculate</button></div>
          </div>
          {res && (
            <div className="border-t border-surface-border">
              <div className="p-3"><GateBanner gate={res.compliance} /></div>
              {!res.ok && <div className="px-3 pb-3"><Hint tone="bad">{res.reason}</Hint></div>}
              <table className="w-full text-sm">
                <thead><tr><Th>#</Th><Th>Step</Th><Th>How</Th><Th right>Value</Th></tr></thead>
                <tbody>{res.lines.map((l, i) => <tr key={i}><Td mono>{i + 1}</Td><Td className="font-medium text-gray-800">{l.step}</Td><Td className="!whitespace-normal text-gray-600">{l.detail}</Td><Td right mono>{typeof l.value === "number" ? cad(l.value) : String(l.value)}</Td></tr>)}</tbody>
                <tfoot><tr className="bg-surface font-semibold"><Td colSpan={3 as any}>Holiday pay {res.worked_premium_pay > 0 && <span className="text-gray-500 font-normal">+ worked premium {cad(res.worked_premium_pay)}</span>}</Td><Td right mono className="text-primary-700">{cad(res.holiday_pay)}</Td></tr></tfoot>
              </table>
              {res.entries.length > 0 && (
                <div className="p-3 flex items-start gap-2">
                  <pre className="flex-1 text-[11px] bg-surface rounded p-2 overflow-x-auto max-h-40">{JSON.stringify(res.entries.map((e) => ({ code: e.code, amount: e.amount, hours: e.hours })), null, 1)}</pre>
                  <button type="button" className="btn-secondary text-xs inline-flex items-center gap-1" onClick={() => { navigator.clipboard.writeText(JSON.stringify(res.entries)); toast.success("Entry copied — paste into Run Payroll → Adjust (STAT)"); }}><Copy className="w-3.5 h-3.5" /> Copy entry</button>
                </div>)}
            </div>)}
        </Panel>
      </div>
    </div>
  );
}
