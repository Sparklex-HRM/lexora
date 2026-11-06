import { useQuery } from "@tanstack/react-query";
import { Scale, Info } from "lucide-react";
import { PageHeader, Spinner, Empty, StatTile } from "../components/ui";
import { mastersApi } from "../api/endpoints";

const isGarnish = (c: any) => c.kind === "deduction" && /garnish|support|court|seiz|levy/i.test(`${c.name} ${c.code}`);

export default function GarnishmentsPage() {
  const { data, isLoading } = useQuery({ queryKey: ["m", "pay-components"], queryFn: () => mastersApi.list("pay-components") });
  const codes = (data || []).filter((c: any) => isGarnish(c) && c.is_active !== false);

  return (
    <div>
      <PageHeader title="Garnishments" icon={Scale} subtitle="Court-ordered and support deductions applied through payroll" />

      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 mb-4">
        <StatTile label="Garnishment codes" value={codes.length} icon={Scale} />
        <StatTile label="After-tax" value={codes.filter((c: any) => c.taxable).length} sub="net-pay only" />
        <StatTile label="Applied at run time" value="Per employee" accent />
      </div>

      <div className="rounded-lg bg-primary-50 text-primary-800 px-4 py-3 mb-4 text-xs flex items-start gap-2">
        <Info className="w-4 h-4 shrink-0 mt-px" />
        <span>Garnishments are entered per employee on each pay run — open <b>Run Payroll → Adjust</b> on a row and add the garnishment component with the ordered amount. They reduce net pay only (after-tax) and are reported on the payroll register.</span>
      </div>

      <div className="card !p-0">
        <div className="px-5 py-3 border-b border-surface-border"><h3 className="font-display font-semibold text-gray-800">Configured garnishment components</h3></div>
        {isLoading ? <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div>
          : !codes.length ? <Empty>No garnishment components yet — add one under Master Data → Pay Components (kind = deduction, e.g. code GARN).</Empty>
          : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-surface text-gray-400 text-left text-[11px] uppercase tracking-wide">
                  <tr>
                    <th className="px-5 py-2.5 font-semibold">Component</th>
                    <th className="px-4 py-2.5 font-semibold">Code</th>
                    <th className="px-4 py-2.5 font-semibold">Tax treatment</th>
                    <th className="px-4 py-2.5 font-semibold">GL account</th>
                  </tr>
                </thead>
                <tbody>
                  {codes.map((c: any) => (
                    <tr key={c.id} className="border-t border-surface-border">
                      <td className="px-5 py-2.5 font-medium text-gray-800">{c.name}</td>
                      <td className="px-4 py-2.5 text-gray-600">{c.code}</td>
                      <td className="px-4 py-2.5"><span className="badge-amber">after-tax</span></td>
                      <td className="px-4 py-2.5 text-gray-500 tabular-nums">{c.gl_account || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </div>
    </div>
  );
}
