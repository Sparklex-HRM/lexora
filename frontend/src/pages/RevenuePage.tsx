import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { TrendingUp, Wallet, Building2, Layers } from "lucide-react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell, PieChart, Pie, Legend } from "recharts";
import { PageHeader, StatTile, Spinner, Empty } from "../components/ui";
import { platformApi, type CountryMeta } from "../api/endpoints";

const FLAG: Record<string, string> = { CA: "🇨🇦" };
const PLAN_COLORS: Record<string, string> = { starter: "#94a3b8", growth: "#b466fc", enterprise: "#7830ea" };
const COUNTRY_COLORS = ["#b466fc", "#7830ea", "#f59e0b", "#ec4899"];
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

function money(amount: number | undefined, country: string, countries?: Record<string, CountryMeta>) {
  const m = countries?.[country];
  const sym = m?.symbol ?? "$";
  const locale = m?.locale ?? "en-CA";
  return `${sym}${(amount ?? 0).toLocaleString(locale)}`;
}

export default function RevenuePage() {
  const { data, isLoading } = useQuery({ queryKey: ["platform", "orgs"], queryFn: () => platformApi.orgs() });
  const orgs = data?.orgs || [];
  const countries = data?.countries || {};
  const plans = Object.keys(data?.plans || {});
  const active = orgs.filter((o) => o.is_active);

  const mrrByCountry = useMemo(() => {
    const m: Record<string, number> = {};
    active.forEach((o) => { m[o.country] = (m[o.country] || 0) + (o.price || 0); });
    return m;
  }, [orgs]);

  const planData = useMemo(() =>
    plans.map((p) => ({ plan: cap(p), key: p, tenants: orgs.filter((o) => o.plan === p).length })),
  [orgs, plans]);

  const countryData = useMemo(() => Object.keys(countries).map((c) => ({
    name: countries[c].label, key: c, value: orgs.filter((o) => o.country === c).length,
  })).filter((d) => d.value > 0), [orgs, countries]);

  // New tenants by month, from created_at.
  const growth = useMemo(() => {
    const m: Record<string, number> = {};
    orgs.forEach((o) => {
      if (!o.created_at) return;
      const d = new Date(o.created_at);
      const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
      m[key] = (m[key] || 0) + 1;
    });
    return Object.keys(m).sort().map((k) => ({ month: k.slice(2), tenants: m[k] }));
  }, [orgs]);

  const countryKeys = Object.keys(mrrByCountry);

  if (isLoading) return <div className="grid place-items-center py-24"><Spinner className="h-8 w-8" /></div>;

  return (
    <div>
      <PageHeader title="Revenue" icon={TrendingUp} subtitle="Recurring revenue across every tenant — by plan, country & currency" />

      {!orgs.length ? <Empty>No tenants yet — provision one to see revenue.</Empty> : (
        <>
          {/* MRR / ARR per currency */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
            {countryKeys.map((c) => (
              <StatTile key={`mrr-${c}`} label={`MRR · ${countries[c]?.label || c}`} value={money(mrrByCountry[c], c, countries)}
                sub={`ARR ${money((mrrByCountry[c] || 0) * 12, c, countries)}`} icon={Wallet} accent={c === countryKeys[0]} />
            ))}
            <StatTile label="Active tenants" value={active.length} sub={`${orgs.length} total`} icon={Building2} />
            <StatTile label="Plans in use" value={new Set(orgs.map((o) => o.plan)).size} sub={plans.map((p) => cap(p)).join(" · ")} icon={Layers} />
          </div>

          {/* Charts */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
            <div className="card">
              <h3 className="font-display font-semibold text-gray-800 mb-3">Tenants by plan</h3>
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={planData} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
                  <XAxis dataKey="plan" tick={{ fontSize: 12 }} />
                  <YAxis allowDecimals={false} tick={{ fontSize: 12 }} />
                  <Tooltip cursor={{ fill: "rgba(0,0,0,0.04)" }} />
                  <Bar dataKey="tenants" radius={[6, 6, 0, 0]}>
                    {planData.map((d) => <Cell key={d.key} fill={PLAN_COLORS[d.key] || "#7830ea"} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
            <div className="card">
              <h3 className="font-display font-semibold text-gray-800 mb-3">Tenants by country</h3>
              <ResponsiveContainer width="100%" height={220}>
                <PieChart>
                  <Pie data={countryData} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={80} label>
                    {countryData.map((d, i) => <Cell key={d.key} fill={COUNTRY_COLORS[i % COUNTRY_COLORS.length]} />)}
                  </Pie>
                  <Legend />
                  <Tooltip />
                </PieChart>
              </ResponsiveContainer>
            </div>
          </div>

          {growth.length > 1 && (
            <div className="card mb-4">
              <h3 className="font-display font-semibold text-gray-800 mb-3">New tenants over time</h3>
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={growth} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
                  <XAxis dataKey="month" tick={{ fontSize: 12 }} />
                  <YAxis allowDecimals={false} tick={{ fontSize: 12 }} />
                  <Tooltip cursor={{ fill: "rgba(0,0,0,0.04)" }} />
                  <Bar dataKey="tenants" fill="#7830ea" radius={[6, 6, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Revenue by tenant */}
          <div className="card !p-0">
            <div className="px-5 py-3 border-b border-surface-border"><h3 className="font-display font-semibold text-gray-800">Revenue by tenant</h3></div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm min-w-[640px]">
                <thead className="bg-surface text-gray-400 text-left text-[11px] uppercase tracking-wide">
                  <tr>
                    <th className="px-5 py-2.5 font-semibold">Tenant</th>
                    <th className="px-3 py-2.5 font-semibold">Country</th>
                    <th className="px-3 py-2.5 font-semibold">Plan</th>
                    <th className="px-3 py-2.5 font-semibold text-right">MRR</th>
                    <th className="px-3 py-2.5 font-semibold text-right">ARR</th>
                    <th className="px-3 py-2.5 font-semibold">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {[...orgs].sort((a, b) => (b.price || 0) - (a.price || 0)).map((o) => (
                    <tr key={o.id} className={`border-t border-surface-border ${!o.is_active ? "opacity-50" : ""}`}>
                      <td className="px-5 py-2.5 font-medium text-gray-800">{o.name}</td>
                      <td className="px-3 py-2.5 text-gray-600">{FLAG[o.country] || ""} {countries[o.country]?.label || o.country}</td>
                      <td className="px-3 py-2.5"><span className="capitalize text-xs font-semibold" style={{ color: PLAN_COLORS[o.plan] }}>{o.plan}</span></td>
                      <td className="px-3 py-2.5 text-right tabular-nums font-medium text-gray-800">{money(o.price, o.country, countries)}</td>
                      <td className="px-3 py-2.5 text-right tabular-nums text-gray-500">{money((o.price || 0) * 12, o.country, countries)}</td>
                      <td className="px-3 py-2.5">{o.is_active ? <span className="badge-green">Active</span> : <span className="badge-red">Suspended</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          <p className="text-[11px] text-gray-400 mt-3">MRR/ARR are in Canadian dollars. Suspended tenants are excluded from MRR. Prices are illustrative monthly tiers.</p>
        </>
      )}
    </div>
  );
}
