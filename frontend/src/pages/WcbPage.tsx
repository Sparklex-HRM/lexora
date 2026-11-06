import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { HardHat, AlertTriangle, Save, Building2 } from "lucide-react";
import toast from "react-hot-toast";
import { PageHeader, StatTile, Spinner, Empty, Card } from "../components/ui";
import { complianceApi, money } from "../api/endpoints";

function RateRow({ p, year, onSaved }: { p: any; year: number; onSaved: () => void }) {
  const [rate, setRate] = useState(p.rate_per_100 ?? "");
  const [acct, setAcct] = useState(p.account_number ?? "");
  const save = useMutation({
    mutationFn: () => complianceApi.setWcbRate(p.province, { rate_per_100: Number(rate), account_number: acct || undefined, year }),
    onSuccess: () => { toast.success(`${p.province} rate saved`); onSaved(); },
    onError: () => toast.error("Could not save rate (check the value is 0–30)"),
  });
  return (
    <tr className="border-t border-surface-border">
      <td className="px-5 py-2.5"><span className="font-semibold text-gray-800">{p.province}</span> <span className="text-xs text-gray-400">{p.board}</span></td>
      <td className="px-3 py-2.5 text-right tabular-nums text-gray-600">{money(p.max_assessable)}{!p.max_verified && <span className="text-amber-500 ml-1" title="Unverified reference">*</span>}</td>
      <td className="px-3 py-2.5"><input className="input !py-1 !w-24 text-sm text-right" type="number" step="0.01" placeholder="—" value={rate} onChange={(e) => setRate(e.target.value)} /></td>
      <td className="px-3 py-2.5"><input className="input !py-1 !w-36 text-sm" placeholder="account #" value={acct} onChange={(e) => setAcct(e.target.value)} /></td>
      <td className="px-4 py-2.5 text-right">
        <button className="btn-secondary text-xs inline-flex items-center gap-1 !py-1" disabled={rate === "" || save.isPending} onClick={() => save.mutate()}><Save className="w-3.5 h-3.5" />Save</button>
      </td>
    </tr>
  );
}

export default function WcbPage() {
  const [year] = useState(2026);
  const qc = useQueryClient();
  const { data: rates, isLoading: rl } = useQuery({ queryKey: ["wcb-rates", year], queryFn: () => complianceApi.wcbRates(year) });
  const { data: ass, isLoading: al } = useQuery({ queryKey: ["wcb-assessment", year], queryFn: () => complianceApi.wcbAssessment(year) });
  const refresh = () => { qc.invalidateQueries({ queryKey: ["wcb-rates", year] }); qc.invalidateQueries({ queryKey: ["wcb-assessment", year] }); };

  return (
    <div>
      <PageHeader title="WCB / WSIB" icon={HardHat} subtitle="Workers' compensation premium assessment · planning estimate" />

      <div className="mb-4 rounded-xl bg-amber-50 border border-amber-200 px-4 py-2.5 flex items-start gap-2 text-sm text-amber-800">
        <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
        <span>Planning estimate only. Rates are your board-assigned inputs; maximum-assessable ceilings (marked <span className="font-semibold">*</span>) are <span className="font-semibold">unverified references</span> — confirm each province's published maximum before relying on any premium figure.</span>
      </div>

      {al || !ass ? <div className="grid place-items-center py-10"><Spinner className="h-7 w-7" /></div> : (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-5">
          <StatTile label="Total assessable" value={money(ass.total_assessable)} icon={Building2} />
          <StatTile label="Est. annual premium" value={money(ass.total_premium)} accent sub={ass.rates_complete ? "all provinces rated" : "some provinces need a rate"} />
          <StatTile label="Provinces" value={ass.provinces.length} />
          <StatTile label="Status" value="Estimate" sub="unverified" />
        </div>
      )}

      <div className="grid lg:grid-cols-2 gap-4">
        <Card padded={false}>
          <div className="px-5 py-3 border-b border-surface-border"><h3 className="font-display font-semibold text-gray-800">Assessment rates · {year}</h3></div>
          {rl || !rates ? <div className="grid place-items-center py-10"><Spinner className="h-6 w-6" /></div> : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-surface text-gray-400 text-left text-[11px] uppercase tracking-wide">
                  <tr><th className="px-5 py-2 font-semibold">Province</th><th className="px-3 py-2 font-semibold text-right">Max assessable</th><th className="px-3 py-2 font-semibold">Rate /$100</th><th className="px-3 py-2 font-semibold">Account</th><th /></tr>
                </thead>
                <tbody>
                  {rates.provinces.map((p: any) => <RateRow key={p.province} p={p} year={year} onSaved={refresh} />)}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card padded={false}>
          <div className="px-5 py-3 border-b border-surface-border"><h3 className="font-display font-semibold text-gray-800">Premium by province</h3></div>
          {al || !ass ? <div className="grid place-items-center py-10"><Spinner className="h-6 w-6" /></div>
            : !ass.provinces.length ? <Empty>No assessable earnings — run payroll to build the T4 ledger.</Empty> : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-surface text-gray-400 text-left text-[11px] uppercase tracking-wide">
                  <tr><th className="px-5 py-2 font-semibold">Province</th><th className="px-3 py-2 font-semibold text-right">Employees</th><th className="px-3 py-2 font-semibold text-right">Assessable</th><th className="px-4 py-2 font-semibold text-right">Est. premium</th></tr>
                </thead>
                <tbody>
                  {ass.provinces.map((p: any) => (
                    <tr key={p.province} className="border-t border-surface-border">
                      <td className="px-5 py-2.5"><span className="font-semibold text-gray-800">{p.province}</span> <span className="text-xs text-gray-400">{p.board || (p.unsupported ? "unsupported" : "")}</span></td>
                      <td className="px-3 py-2.5 text-right tabular-nums text-gray-600">{p.employees}</td>
                      <td className="px-3 py-2.5 text-right tabular-nums text-gray-700">{money(p.assessable)}</td>
                      <td className="px-4 py-2.5 text-right tabular-nums font-semibold text-gray-800">{p.premium != null ? money(p.premium) : <span className="text-amber-500 text-xs">set rate</span>}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr className="border-t-2 border-surface-border bg-surface font-semibold">
                    <td className="px-5 py-2.5" colSpan={2}>Total</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{money(ass.total_assessable)}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums text-primary-600">{money(ass.total_premium)}</td>
                  </tr>
                </tfoot>
              </table>
            </div>
          )}
        </Card>
      </div>
      <p className="text-[11px] text-gray-400 mt-3">Source: {ass?.source || "payroll T4 ledger"}. Assessable earnings = min(box-14 income, provincial maximum) per worker.</p>
    </div>
  );
}
