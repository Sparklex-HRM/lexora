import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Landmark, Download, Save } from "lucide-react";
import toast from "react-hot-toast";
import { Link } from "react-router-dom";
import { PageHeader, Spinner } from "../../components/ui";
import { canadaApi } from "../../api/canada";
import { Th, Td, Panel, Hint, Tile, GateBanner, Field, cad, exportCsv, err } from "./_shared";

/** /payroll/canada/remittances — remitter type (org choice), PD7A-style periods by that type, AMWA
 *  reference, Revenu Québec summary. Due dates appear ONLY from rules the org entered. */
export default function RemittancesPage() {
  const qc = useQueryClient();
  const [year] = useState(2026);
  const { data, isLoading } = useQuery({ queryKey: ["ca-remit", year], queryFn: () => canadaApi.remittances(year) });
  const [rt, setRt] = useState<string | null>(null);
  const [acct, setAcct] = useState<string | null>(null);
  const [rq, setRq] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () => canadaApi.putSettings({ remitter_type: rt ?? data!.settings.remitter_type, cra_payroll_account: acct ?? data!.settings.cra_payroll_account,
      rq_remitter_type: data!.settings.rq_remitter_type, rq_account: rq ?? data!.settings.rq_account }),
    onSuccess: () => { toast.success("Settings saved"); qc.invalidateQueries({ queryKey: ["ca-remit", year] }); }, onError: (e) => toast.error(err(e)),
  });
  if (isLoading || !data) return <div className="grid place-items-center py-20"><Spinner className="h-7 w-7" /></div>;
  const s = data.schedule;
  const cur = rt ?? data.settings.remitter_type;
  return (
    <div>
      <PageHeader title="CRA / Revenu Québec remittances" icon={Landmark} subtitle="Source deductions owed per remittance period, by your remitter type. Lexora does not pay CRA — it tells you how much and by when." />
      <GateBanner gate={data.compliance} />
      <div className="grid lg:grid-cols-3 gap-3 my-3">
        <Panel title="Your remitter type (CRA assigns it)" sub="An org choice, not a formula">
          <div className="p-3 space-y-2">
            <Field label="Remitter type"><select className="input-base h-8 text-xs w-full" value={cur} onChange={(e) => setRt(e.target.value)}>
              <option value="">— not set (regular assumed) —</option>{data.settings.remitter_types.map((t) => <option key={t} value={t}>{t}</option>)}</select></Field>
            <Field label="CRA payroll account (RP)"><input className="input-base h-8 text-xs w-full" value={acct ?? data.settings.cra_payroll_account ?? ""} onChange={(e) => setAcct(e.target.value)} placeholder="123456789RP0001" /></Field>
            <Field label="Revenu Québec account"><input className="input-base h-8 text-xs w-full" value={rq ?? data.settings.rq_account ?? ""} onChange={(e) => setRq(e.target.value)} /></Field>
            <button type="button" className="btn-primary text-xs inline-flex items-center gap-1" disabled={save.isPending} onClick={() => save.mutate()}><Save className="w-3.5 h-3.5" /> Save</button>
          </div>
        </Panel>
        <Panel title="AMWA reference" sub="Average monthly withholding from Lexora's own stubs this year">
          <div className="p-3 flex flex-wrap gap-2">
            <Tile label="Months with pay" value={data.amwa.months} /><Tile label="Total withheld" value={cad(data.amwa.total)} /><Tile label="AMWA" value={cad(data.amwa.amwa)} tone="accent" />
          </div>
          <div className="px-3 pb-3 text-[12px] text-gray-600">Suggested type: <b>{data.suggestion.suggested || "—"}</b> · {data.suggestion.reason}
            <div className="text-[11px] text-gray-400 mt-1">Ceilings entered: quarterly {data.ceilings.quarterly ?? "blank"} · regular {data.ceilings.regular ?? "blank"} · threshold 1 {data.ceilings.threshold1 ?? "blank"} — <Link className="underline" to="/payroll/canada/statutory-values">enter them</Link>. CRA decides the type from a prior year's AMWA; this is a check, not the ruling.</div>
          </div>
        </Panel>
        <Panel title="Due-date rule in use" sub={`remit.${s.remitter_type}.rule`}>
          <div className="p-3 text-[12px]">
            {data.legacy_rule_in_use
              ? <Hint tone="warn">No rule entered for <b>regular</b> — using the one already in the code (15th of the following month). Enter and verify <code>remit.regular.rule</code> to confirm it.</Hint>
              : s.rule ? <pre className="text-[11px] bg-surface rounded p-2 overflow-x-auto">{JSON.stringify(s.rule, null, 1)}</pre>
              : <Hint tone="bad">No due-date rule entered for <b>{s.remitter_type}</b>. Periods below have no due date until you enter <code>remit.{s.remitter_type}.rule</code> under Canadian statutory values.</Hint>}
          </div>
        </Panel>
      </div>
      <Panel title={`PD7A-style summary · ${year} · ${s.remitter_type}`} sub={`${s.periods.length} period(s) · total ${cad(s.total_remittance)}`}
        actions={<button type="button" className="btn-secondary text-xs inline-flex items-center gap-1" onClick={() => exportCsv(`cra_remittance_${year}.csv`,
          ["Period", "Employees", "Gross payroll", "Income tax", "CPP employee", "CPP employer", "EI employee", "EI employer", "Total", "Due date"],
          s.periods.map((p) => [p.period, p.employees, p.gross_payroll, p.income_tax, p.cpp_employee, p.cpp_employer, p.ei_employee, p.ei_employer, p.total_remittance, p.due_date || p.due_reason]))}>
          <Download className="w-3.5 h-3.5" /> CSV</button>}>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr><Th>Period</Th><Th right>Employees</Th><Th>Pay dates</Th><Th right>Gross payroll</Th><Th right>Income tax</Th><Th right>CPP ee</Th><Th right>CPP er</Th><Th right>EI ee</Th><Th right>EI er</Th><Th right>Total to remit</Th><Th>Due</Th></tr></thead>
            <tbody>{s.periods.map((p) => (
              <tr key={p.period}><Td className="font-medium text-gray-800">{p.period}</Td><Td right mono>{p.employees}</Td>
                <Td className="text-[11px] text-gray-500">{p.pay_dates.join(", ")}</Td>
                <Td right mono>{cad(p.gross_payroll)}</Td><Td right mono>{cad(p.income_tax)}</Td><Td right mono>{cad(p.cpp_employee)}</Td><Td right mono>{cad(p.cpp_employer)}</Td>
                <Td right mono>{cad(p.ei_employee)}</Td><Td right mono>{cad(p.ei_employer)}</Td><Td right mono className="font-semibold text-gray-800">{cad(p.total_remittance)}</Td>
                <Td mono>{p.due_date || <span className="text-amber-600 text-[11px]">{p.due_reason}</span>}</Td></tr>))}
              {!s.periods.length && <tr><Td className="text-gray-400 text-center" colSpan={11 as any}>No Canadian pay stubs this year.</Td></tr>}
            </tbody>
          </table>
        </div>
      </Panel>
      <div className="mt-3">
        <Panel title="Revenu Québec summary (QC employees)" sub={`QPP + QPIP + Québec tax · total ${cad(data.rq.total_remittance)} · HSF is under Employer accruals`}>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr><Th>Month</Th><Th right>Employees</Th><Th right>Gross</Th><Th right>QC tax</Th><Th right>QPP ee</Th><Th right>QPP er</Th><Th right>QPIP ee</Th><Th right>QPIP er</Th><Th right>Total</Th><Th>Due</Th></tr></thead>
              <tbody>{data.rq.periods.map((p: any) => (
                <tr key={p.period}><Td className="font-medium">{p.period}</Td><Td right mono>{p.employees}</Td><Td right mono>{cad(p.gross_payroll)}</Td><Td right mono>{cad(p.qc_income_tax)}</Td>
                  <Td right mono>{cad(p.qpp_employee)}</Td><Td right mono>{cad(p.qpp_employer)}</Td><Td right mono>{cad(p.qpip_employee)}</Td><Td right mono>{cad(p.qpip_employer)}</Td>
                  <Td right mono className="font-semibold">{cad(p.total_remittance)}</Td><Td mono>{p.due_date || <span className="text-amber-600 text-[11px]">{p.due_reason}</span>}</Td></tr>))}
                {!data.rq.periods.length && <tr><Td className="text-gray-400 text-center" colSpan={10 as any}>No Québec stubs this year.</Td></tr>}
              </tbody>
            </table>
          </div>
        </Panel>
      </div>
    </div>
  );
}
