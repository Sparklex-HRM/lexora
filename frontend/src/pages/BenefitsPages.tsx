import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { HeartPulse, Building2, PiggyBank, ShieldPlus, Users } from "lucide-react";
import { PageHeader, Spinner, Empty, StatTile } from "../components/ui";
import { mastersApi, money } from "../api/endpoints";

function usePlans() {
  return useQuery({ queryKey: ["m", "benefit-plans"], queryFn: () => mastersApi.list("benefit-plans") });
}
const active = (p: any) => p.is_active !== false;

// ── Benefits Admin ──────────────────────────────────────────────────────────
export default function BenefitsPage() {
  const { data, isLoading } = usePlans();
  const plans = (data || []).filter(active);
  const eeMo = plans.reduce((s: number, p: any) => s + (+p.ee_cost || 0), 0);
  const erMo = plans.reduce((s: number, p: any) => s + (+p.er_cost || 0), 0);
  return (
    <div>
      <PageHeader title="Benefits Admin" icon={HeartPulse} subtitle="Group benefit plans, carriers and cost sharing — from your Master Data" />
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
        <StatTile label="Active plans" value={plans.length} icon={HeartPulse} />
        <StatTile label="Employee cost / pay" value={money(eeMo)} />
        <StatTile label="Employer cost / pay" value={money(erMo)} accent />
        <StatTile label="Taxable benefits" value={plans.filter((p: any) => p.taxable_benefit).length} />
      </div>
      <PlanTable data={data} isLoading={isLoading} />
    </div>
  );
}

function PlanTable({ data, isLoading, filter }: { data: any; isLoading: boolean; filter?: (p: any) => boolean }) {
  const rows = (data || []).filter((p: any) => active(p) && (!filter || filter(p)));
  if (isLoading) return <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div>;
  if (!rows.length) return <div className="card"><Empty>No plans yet — add them under Master Data → Benefit Plans.</Empty></div>;
  return (
    <div className="card !p-0">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-surface text-gray-400 text-left text-[11px] uppercase tracking-wide">
            <tr>
              <th className="px-5 py-2.5 font-semibold">Plan</th>
              <th className="px-4 py-2.5 font-semibold">Carrier</th>
              <th className="px-4 py-2.5 font-semibold text-right">EE / pay</th>
              <th className="px-4 py-2.5 font-semibold text-right">ER / pay</th>
              <th className="px-4 py-2.5 font-semibold">Taxable</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((p: any) => (
              <tr key={p.id} className="border-t border-surface-border">
                <td className="px-5 py-2.5 font-medium text-gray-800">{p.name}<span className="text-xs text-gray-400"> · {p.code}</span></td>
                <td className="px-4 py-2.5 text-gray-600">{p.carrier || "—"}</td>
                <td className="px-4 py-2.5 text-right tabular-nums text-gray-700">{money(+p.ee_cost || 0)}</td>
                <td className="px-4 py-2.5 text-right tabular-nums text-gray-700">{money(+p.er_cost || 0)}</td>
                <td className="px-4 py-2.5">{p.taxable_benefit ? <span className="badge-amber">taxable</span> : <span className="badge-green">non-taxable</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Benefits Enrollment ─────────────────────────────────────────────────────
export function EnrollmentPage() {
  const { data, isLoading } = usePlans();
  const plans = (data || []).filter(active);
  return (
    <div>
      <PageHeader title="Benefits Enrollment" icon={ShieldPlus} subtitle="Plans available to employees during open enrollment & life events" />
      {isLoading ? <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div>
        : !plans.length ? <div className="card"><Empty>No plans configured yet.</Empty></div>
        : (
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {plans.map((p: any) => (
              <div key={p.id} className="card">
                <div className="flex items-start justify-between">
                  <div>
                    <div className="font-display font-semibold text-gray-800">{p.name}</div>
                    <div className="text-xs text-gray-400">{p.carrier || "—"}</div>
                  </div>
                  {p.taxable_benefit ? <span className="badge-amber">taxable</span> : <span className="badge-green">non-taxable</span>}
                </div>
                <div className="mt-3 flex items-end justify-between">
                  <div><div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">Your cost / pay</div><div className="text-xl font-bold text-gray-800 tabular-nums">{money(+p.ee_cost || 0)}</div></div>
                  <div className="text-right text-xs text-gray-400">employer adds<div className="text-gray-600 font-medium tabular-nums">{money(+p.er_cost || 0)}</div></div>
                </div>
                <button className="btn-secondary w-full mt-3 text-xs">Request enrollment</button>
              </div>
            ))}
          </div>
        )}
      <p className="text-[11px] text-gray-400 mt-3">Employee selections route to HR for approval. Life-event windows (marriage, birth, status change) reopen enrollment outside the annual period.</p>
    </div>
  );
}

// ── Carriers ────────────────────────────────────────────────────────────────
export function CarriersPage() {
  const { data, isLoading } = usePlans();
  const carriers = useMemo(() => {
    const map: Record<string, { name: string; plans: number; ee: number; er: number }> = {};
    (data || []).filter(active).forEach((p: any) => {
      const key = p.carrier || "—";
      const c = map[key] || (map[key] = { name: key, plans: 0, ee: 0, er: 0 });
      c.plans += 1; c.ee += +p.ee_cost || 0; c.er += +p.er_cost || 0;
    });
    return Object.values(map).sort((a, b) => b.plans - a.plans);
  }, [data]);
  return (
    <div>
      <PageHeader title="Carriers" icon={Building2} subtitle="Insurance carriers behind your benefit plans" />
      {isLoading ? <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div>
        : !carriers.length ? <div className="card"><Empty>No carriers yet — set a carrier on each plan in Master Data → Benefit Plans.</Empty></div>
        : (
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {carriers.map((c) => (
              <div key={c.name} className="card">
                <div className="flex items-center gap-2.5 mb-2">
                  <div className="w-9 h-9 rounded-lg bg-primary-50 grid place-items-center"><Building2 className="w-4 h-4 text-primary-600" /></div>
                  <div className="font-display font-semibold text-gray-800">{c.name}</div>
                </div>
                <div className="text-xs text-gray-500">{c.plans} plan{c.plans > 1 ? "s" : ""}</div>
                <div className="mt-2 text-xs text-gray-500 tabular-nums">EE {money(c.ee)} / pay · ER {money(c.er)} / pay</div>
              </div>
            ))}
          </div>
        )}
    </div>
  );
}

// ── Retirement / RRSP ───────────────────────────────────────────────────────
export function RetirementPage() {
  const { data, isLoading } = usePlans();
  const isRetire = (p: any) => /rrsp|pension|retire/i.test(`${p.name} ${p.code}`);
  const plans = (data || []).filter((p: any) => active(p) && isRetire(p));
  return (
    <div>
      <PageHeader title="Retirement / RRSP" icon={PiggyBank} subtitle="Group RRSP and pension plans" />
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 mb-4">
        <StatTile label="Retirement plans" value={plans.length} icon={PiggyBank} />
        <StatTile label="Employer match / pay" value={money(plans.reduce((s: number, p: any) => s + (+p.er_cost || 0), 0))} accent />
        <StatTile label="Enrolled employees" value="—" sub="wire to enrollment" icon={Users} />
      </div>
      <PlanTable data={data} isLoading={isLoading} filter={isRetire} />
      <p className="text-[11px] text-gray-400 mt-3">Employee RRSP contributions flow through payroll as a pre-tax deduction (reduces income tax, stays CPP/EI-pensionable). Configure the deduction under Master Data → Pay Components.</p>
    </div>
  );
}
