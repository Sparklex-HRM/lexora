import { useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { BookOpen, Download, CheckCircle2, AlertTriangle } from "lucide-react";
import { PageHeader, StatTile, Spinner, Empty } from "../components/ui";
import { payrollApi, money, freqLabel } from "../api/endpoints";

const fmt = (d?: string) => d ? new Date(d).toLocaleDateString("en-CA", { month: "short", day: "numeric", year: "numeric" }) : "—";

export default function GlExportPage() {
  const { data: runs } = useQuery({ queryKey: ["runs"], queryFn: () => payrollApi.runs() });
  const [runId, setRunId] = useState("");
  useEffect(() => { if (!runId && runs && runs.length) setRunId(runs[0].id); }, [runs, runId]);
  const { data: gl, isLoading } = useQuery({ queryKey: ["gl-journal", runId], queryFn: () => payrollApi.glJournal(runId), enabled: !!runId });

  const csv = () => {
    if (!gl) return;
    const rows = [["Account", "Name", "Memo", "Debit", "Credit"],
      ...gl.journal.map((j: any) => [j.account, j.name, j.memo || "", j.debit.toFixed(2), j.credit.toFixed(2)]),
      ["", "TOTALS", "", gl.total_debit.toFixed(2), gl.total_credit.toFixed(2)]];
    const blob = new Blob([rows.map((r) => r.map((c: any) => `"${String(c).replace(/"/g, '""')}"`).join(",")).join("\n")], { type: "text/csv" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
    a.download = `gl-journal-${gl.pay_date}.csv`; a.click(); URL.revokeObjectURL(a.href);
  };

  return (
    <div>
      <PageHeader title="GL Export" icon={BookOpen} subtitle="Payroll → general-ledger journal (double-entry) for a pay run"
        actions={
          <div className="flex items-center gap-2">
            <select className="input !py-1.5 !w-auto text-sm" value={runId} onChange={(e) => setRunId(e.target.value)}>
              {(runs || []).map((r) => <option key={r.id} value={r.id}>{freqLabel(r.pay_frequency)} · {fmt(r.pay_date)} · {money(r.total_net)}</option>)}
            </select>
            <button className="btn-secondary text-sm inline-flex items-center gap-1.5" onClick={csv} disabled={!gl}><Download className="w-4 h-4" />CSV</button>
          </div>
        } />

      {!runs?.length ? <div className="card"><Empty>No pay runs yet — run payroll first.</Empty></div>
        : isLoading || !gl ? <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div>
        : (
          <>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
              <StatTile label="Total debits" value={money(gl.total_debit)} accent />
              <StatTile label="Total credits" value={money(gl.total_credit)} />
              <StatTile label="CRA remittance" value={money(gl.cra_remittance)} sub="tax + CPP + EI" />
              <StatTile label="Balanced" value={gl.balanced ? "Yes" : "No"} sub={`${gl.employee_count} employees`} />
            </div>

            {gl.balanced
              ? <div className="mb-4 rounded-xl bg-green-50 border border-green-200 px-4 py-2.5 flex items-center gap-2 text-sm text-green-800"><CheckCircle2 className="w-4 h-4" />Journal balances — debits equal credits to the cent.</div>
              : <div className="mb-4 rounded-xl bg-red-50 border-2 border-red-300 px-4 py-2.5 flex items-center gap-2 text-sm text-red-800"><AlertTriangle className="w-4 h-4" />Journal does NOT balance — do not post. Debits {money(gl.total_debit)} vs credits {money(gl.total_credit)}.</div>}

            <div className="card !p-0">
              <div className="px-5 py-3 border-b border-surface-border"><h3 className="font-display font-semibold text-gray-800">Journal entry · pay date {fmt(gl.pay_date)}</h3></div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="bg-surface text-gray-400 text-left text-[11px] uppercase tracking-wide">
                    <tr><th className="px-5 py-2.5 font-semibold">Account</th><th className="px-3 py-2.5 font-semibold">Description</th><th className="px-3 py-2.5 font-semibold">Memo</th><th className="px-3 py-2.5 font-semibold text-right">Debit</th><th className="px-4 py-2.5 font-semibold text-right">Credit</th></tr>
                  </thead>
                  <tbody>
                    {gl.journal.map((j: any, i: number) => (
                      <tr key={i} className="border-t border-surface-border">
                        <td className="px-5 py-2 font-mono text-gray-700">{j.account}</td>
                        <td className="px-3 py-2 text-gray-800">{j.name}</td>
                        <td className="px-3 py-2 text-gray-400 text-xs">{j.memo || ""}</td>
                        <td className="px-3 py-2 text-right tabular-nums text-gray-800">{j.debit ? money(j.debit) : ""}</td>
                        <td className="px-4 py-2 text-right tabular-nums text-gray-800">{j.credit ? money(j.credit) : ""}</td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr className="border-t-2 border-surface-border bg-surface font-semibold">
                      <td className="px-5 py-2.5" colSpan={3}>Totals</td>
                      <td className="px-3 py-2.5 text-right tabular-nums text-primary-700">{money(gl.total_debit)}</td>
                      <td className="px-4 py-2.5 text-right tabular-nums text-primary-700">{money(gl.total_credit)}</td>
                    </tr>
                  </tfoot>
                </table>
              </div>
            </div>
            <p className="text-[11px] text-gray-400 mt-3">Salaries debited by department; net pay, CRA (CPP/EI/tax) and employee deductions credited to their payables.</p>
          </>
        )}
    </div>
  );
}
