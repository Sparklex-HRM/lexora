import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Receipt, Plus, Trash2, Send } from "lucide-react";
import { PageHeader, Spinner, Empty, StatTile } from "../components/ui";
import PayslipModal from "../components/PayslipModal";
import { payrollApi, mastersApi, employeeApi, money } from "../api/endpoints";
import { todayISO } from "../lib/datetime";   // 2026-10-25: UTC store, org-zone today

type Row = { code: string; amount: number };
const today = () => todayISO();

export default function OffCyclePage() {
  const qc = useQueryClient();
  const [emp, setEmp] = useState("");
  const [payDate, setPayDate] = useState(today());
  const [earnings, setEarnings] = useState<Row[]>([{ code: "BON", amount: 0 }]);
  const [deductions, setDeductions] = useState<Row[]>([]);
  const [result, setResult] = useState<any>(null);
  const [stubId, setStubId] = useState<string | null>(null);

  const { data: emps } = useQuery({ queryKey: ["employees"], queryFn: () => employeeApi.list() });
  const { data: comps } = useQuery({ queryKey: ["m", "pay-components"], queryFn: () => mastersApi.list("pay-components") });
  const { data: history } = useQuery({ queryKey: ["off-cycle"], queryFn: () => payrollApi.offCycleList() });
  const earnComps = (comps || []).filter((c: any) => c.kind === "earning" && c.is_active !== false);
  const dedComps = (comps || []).filter((c: any) => c.kind === "deduction" && c.is_active !== false);

  const run = useMutation({
    mutationFn: () => payrollApi.offCycle({
      employee_id: emp, pay_date: payDate,
      earnings: earnings.filter((r) => r.code && r.amount > 0),
      deductions: deductions.filter((r) => r.code && r.amount > 0),
    }),
    onSuccess: (r) => {
      setResult(r);
      qc.invalidateQueries({ queryKey: ["off-cycle"] }); qc.invalidateQueries({ queryKey: ["ytd"] });
      qc.invalidateQueries({ queryKey: ["payslips"] }); qc.invalidateQueries({ queryKey: ["runs"] });
      toast.success(`Off-cycle cheque — net ${money(r.net_pay)}`);
    },
    onError: (e: any) => toast.error(e?.response?.data?.detail || "Off-cycle run failed"),
  });

  const rowList = (rows: Row[], setRows: (r: Row[]) => void, opts: any[], label: string) => (
    <div className="rounded-lg border border-surface-border p-3">
      <div className="flex items-center justify-between mb-2">
        <div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">{label}</div>
        <button onClick={() => setRows([...rows, { code: opts[0]?.code || "", amount: 0 }])} className="inline-flex items-center gap-1 text-[11px] font-medium text-primary-600"><Plus className="w-3 h-3" /> Add</button>
      </div>
      {rows.length === 0 ? <p className="text-xs text-gray-400">None.</p> : (
        <div className="space-y-2">
          {rows.map((r, i) => (
            <div key={i} className="flex items-center gap-2">
              <select className="input !py-1.5 text-sm flex-1" value={r.code} onChange={(e) => setRows(rows.map((x, j) => j === i ? { ...x, code: e.target.value } : x))}>
                <option value="">Select…</option>
                {opts.map((o) => <option key={o.id} value={o.code || ""}>{o.name}{o.non_periodic ? " · bonus method" : ""}</option>)}
              </select>
              <div className="relative w-32">
                <span className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-400 text-sm">$</span>
                <input className="input !py-1.5 text-sm !pl-5 text-right" type="number" step="0.01" min="0" value={r.amount || ""} onChange={(e) => setRows(rows.map((x, j) => j === i ? { ...x, amount: parseFloat(e.target.value) || 0 } : x))} />
              </div>
              <button onClick={() => setRows(rows.filter((_, j) => j !== i))} className="icon-btn text-gray-300 hover:text-red-500"><Trash2 className="w-4 h-4" /></button>
            </div>
          ))}
        </div>
      )}
    </div>
  );

  return (
    <div>
      <PageHeader title="Off-Cycle Pay" icon={Receipt} subtitle="One-off cheque outside the regular calendar — bonus, correction or final pay" />

      <div className="grid lg:grid-cols-3 gap-4">
        <div className="card h-fit lg:col-span-1">
          <h3 className="font-display font-semibold text-gray-800 mb-3">New off-cycle cheque</h3>
          <label className="block text-xs font-semibold text-gray-500 mb-1">Employee</label>
          <select className="input" value={emp} onChange={(e) => { setEmp(e.target.value); setResult(null); }}>
            <option value="">Select…</option>
            {(emps || []).map((e: any) => <option key={e.id} value={e.id}>{e.first_name} {e.last_name}</option>)}
          </select>
          <label className="block text-xs font-semibold text-gray-500 mb-1 mt-3">Pay date</label>
          <input className="input" type="date" value={payDate} onChange={(e) => setPayDate(e.target.value)} />
          <div className="mt-3 space-y-3">
            {rowList(earnings, setEarnings, earnComps, "Earnings")}
            {rowList(deductions, setDeductions, dedComps, "Deductions")}
          </div>
          <button className="btn-primary w-full mt-4 flex items-center justify-center gap-1.5 disabled:opacity-60"
            onClick={() => run.mutate()} disabled={!emp || run.isPending}>
            <Send className="w-3.5 h-3.5" /> {run.isPending ? "Processing…" : "Run off-cycle cheque"}
          </button>
          <p className="text-[11px] text-gray-400 mt-2">No base salary is included — only the components above. Bonus/commission use the CRA bonus method. YTD rolls forward.</p>
        </div>

        <div className="lg:col-span-2 space-y-4">
          {result && (
            <div className="card">
              <div className="flex items-center justify-between mb-3">
                <h3 className="font-display font-semibold text-gray-800">Result · {result.employee}</h3>
                <span className="badge-green">processed</span>
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <StatTile label="Gross" value={money(result.gross)} />
                <StatTile label="CPP + EI" value={money((result.deductions.cpp || 0) + (result.deductions.cpp2 || 0) + (result.deductions.ei || 0))} />
                <StatTile label="Income tax" value={money(result.deductions.income_tax)} />
                <StatTile label="Net pay" value={money(result.net_pay)} accent />
              </div>
              {result.tax_detail?.bonus_method_tax > 0 && (
                <p className="text-[11px] text-gray-500 mt-2">Bonus-method tax on {money(result.tax_detail.bonus_taxable)}: {money(result.tax_detail.bonus_method_tax)} (incremental, not annualized).</p>
              )}
            </div>
          )}

          <div className="card !p-0">
            <div className="px-5 py-3 border-b border-surface-border"><h3 className="font-display font-semibold text-gray-800">Off-cycle history</h3></div>
            {!history ? <div className="grid place-items-center py-12"><Spinner className="h-6 w-6" /></div>
              : !history.length ? <Empty>No off-cycle cheques yet.</Empty>
              : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="bg-surface text-gray-400 text-left text-[11px] uppercase tracking-wide">
                      <tr><th className="px-5 py-2.5 font-semibold">Pay date</th><th className="px-4 py-2.5 font-semibold">Employee</th><th className="px-4 py-2.5 font-semibold text-right">Gross</th><th className="px-4 py-2.5 font-semibold text-right">Net</th><th /></tr>
                    </thead>
                    <tbody>
                      {history.map((h: any) => (
                        <tr key={h.id} onClick={() => h.stub_id && setStubId(h.stub_id)} className="border-t border-surface-border hover:bg-gray-50 cursor-pointer">
                          <td className="px-5 py-2.5 font-medium text-gray-800">{h.pay_date}</td>
                          <td className="px-4 py-2.5 text-gray-600">{h.employee}<span className="text-xs text-gray-400"> · {h.employee_number}</span></td>
                          <td className="px-4 py-2.5 text-right tabular-nums text-gray-700">{money(h.gross)}</td>
                          <td className="px-4 py-2.5 text-right tabular-nums font-semibold text-gray-800">{money(h.net_pay)}</td>
                          <td className="px-3 text-primary-500 text-xs">pay stub →</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
          </div>
        </div>
      </div>
      {stubId && <PayslipModal stubId={stubId} onClose={() => setStubId(null)} />}
    </div>
  );
}
