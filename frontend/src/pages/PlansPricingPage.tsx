import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Layers, Globe, Check, DollarSign } from "lucide-react";
import { PageHeader, Spinner } from "../components/ui";
import { platformApi, type CountryMeta } from "../api/endpoints";
import { billingApi } from "../api/privacy";
import { PROVINCE_NAMES } from "../api/regions";

const cad = (n: number) => new Intl.NumberFormat("en-CA", { style: "currency", currency: "CAD", maximumFractionDigits: 2 }).format(n || 0);
const cellInput = "w-24 border border-surface-border rounded px-2 py-0.5 text-sm tabular-nums text-right";

/** Superadmin-editable Canada PEPM catalogue (CAD) + per-tenant MRR view. Numbers live
 *  in the DB (billing_plan_catalog / billing_addon_catalog), seeded from pricing.py. */
function PepmCatalogSection() {
  const qc = useQueryClient();
  const { data: cat } = useQuery({ queryKey: ["billing", "plans", "CA"], queryFn: () => billingApi.plans("CA") });
  const { data: mrr } = useQuery({ queryKey: ["billing", "platform-summary"], queryFn: billingApi.platformSummary });
  const [plans, setPlans] = useState<any>(null);
  const [addons, setAddons] = useState<any>(null);
  const P = plans || cat?.pepm_plans || {};
  const A = addons || cat?.addons || {};
  const save = useMutation({
    mutationFn: () => billingApi.saveCatalog({ country: "CA",
      plans: Object.entries(P).map(([k, v]: any) => ({ plan_key: k, ...v })),
      addons: Object.entries(A).map(([k, v]: any) => ({ addon_key: k, ...v })) }),
    onSuccess: () => { toast.success("Price catalogue saved"); setPlans(null); setAddons(null); qc.invalidateQueries({ queryKey: ["billing"] }); },
    onError: (e: any) => toast.error(e?.response?.data?.detail || "Could not save"),
  });
  const setP = (k: string, f: string, v: any) => setPlans({ ...P, [k]: { ...P[k], [f]: v } });
  const setA = (k: string, f: string, v: any) => setAddons({ ...A, [k]: { ...A[k], [f]: v } });
  const th = "px-3 py-2 text-left text-[11px] uppercase tracking-wide text-gray-400 font-semibold";
  const td = "px-3 py-1.5 text-sm text-gray-700 border-t border-surface-border/70";
  const [preview, setPreview] = useState({ plan: "growth", n: 57, annual: false, province: "ON" });
  const taxRates: Record<string, number> = cat?.sales_tax_rates || {};
  const taxPct = Number(taxRates[preview.province] ?? 0);
  const taxLabel = `${["ON", "NS", "NB", "NL", "PE"].includes(preview.province) ? "HST" : "GST"} ${taxPct}%`;
  const spec = P[preview.plan];
  const raw = spec ? preview.n * Number(spec.pepm) : 0;
  const base = spec ? Math.max(raw, Number(spec.min_monthly)) : 0;
  const disc = preview.annual && spec ? base * Number(spec.annual_discount_pct || 0) / 100 : 0;
  const sub = base - disc;
  return (
    <div className="card !p-0 mb-6">
      <div className="px-5 py-3 border-b border-surface-border flex flex-wrap items-center gap-2">
        <DollarSign className="w-4 h-4 text-primary-500" /><h3 className="font-display font-semibold text-gray-800">Per-employee-per-month plans (CAD)</h3>
        <span className="text-[11px] text-gray-400">headcount on the 1st · minimum per month · GST/HST by billing province · editable here, stored in DB</span>
        <button onClick={() => save.mutate()} disabled={(!plans && !addons) || save.isPending} className="ml-auto text-xs rounded-lg bg-primary-600 text-white font-semibold px-3 py-1.5 disabled:opacity-50">Save catalogue</button>
      </div>
      <div className="overflow-x-auto"><table className="w-full">
        <thead><tr><th className={th}>Plan</th><th className={`${th} text-right`}>$ PEPM</th><th className={`${th} text-right`}>Minimum / mo</th><th className={`${th} text-right`}>Free ≤ emp</th><th className={`${th} text-right`}>Free months</th><th className={`${th} text-right`}>Annual −%</th><th className={th}>Custom</th><th className={th}>Includes</th></tr></thead>
        <tbody>{Object.entries(P).map(([k, v]: any) => <tr key={k}>
          <td className={`${td} font-medium`}><input className="border border-surface-border rounded px-2 py-0.5 text-sm w-28" value={v.label} onChange={(e) => setP(k, "label", e.target.value)} /> <span className="text-[10px] text-gray-400">{k}</span></td>
          <td className={`${td} text-right`}><input type="number" className={cellInput} value={v.pepm} onChange={(e) => setP(k, "pepm", Number(e.target.value))} /></td>
          <td className={`${td} text-right`}><input type="number" className={cellInput} value={v.min_monthly} onChange={(e) => setP(k, "min_monthly", Number(e.target.value))} /></td>
          <td className={`${td} text-right`}><input type="number" className={cellInput} value={v.free_upto_employees} onChange={(e) => setP(k, "free_upto_employees", Number(e.target.value))} /></td>
          <td className={`${td} text-right`}><input type="number" className={cellInput} value={v.free_months} onChange={(e) => setP(k, "free_months", Number(e.target.value))} /></td>
          <td className={`${td} text-right`}><input type="number" className={cellInput} value={v.annual_discount_pct} onChange={(e) => setP(k, "annual_discount_pct", Number(e.target.value))} /></td>
          <td className={td}><input type="checkbox" checked={!!v.custom} onChange={(e) => setP(k, "custom", e.target.checked)} /></td>
          <td className={td}><input className="border border-surface-border rounded px-2 py-0.5 text-xs w-full min-w-[280px]" value={v.includes || ""} onChange={(e) => setP(k, "includes", e.target.value)} /></td>
        </tr>)}</tbody>
      </table></div>
      <div className="px-5 py-2 border-t border-surface-border text-[11px] font-semibold text-gray-400 uppercase tracking-wide">Add-ons</div>
      <div className="overflow-x-auto"><table className="w-full">
        <thead><tr><th className={th}>Add-on</th><th className={th}>Kind</th><th className={`${th} text-right`}>$ price</th><th className={th}>Waived on annual</th><th className={th}>Active</th></tr></thead>
        <tbody>{Object.entries(A).map(([k, v]: any) => <tr key={k}>
          <td className={td}><input className="border border-surface-border rounded px-2 py-0.5 text-xs w-full min-w-[320px]" value={v.label} onChange={(e) => setA(k, "label", e.target.value)} /> <span className="text-[10px] text-gray-400">{k}</span></td>
          <td className={td}><select className="border border-surface-border rounded px-2 py-0.5 text-sm" value={v.kind} onChange={(e) => setA(k, "kind", e.target.value)}><option value="pepm">per employee / month</option><option value="flat">flat / month</option><option value="one_time">one-time</option></select></td>
          <td className={`${td} text-right`}><input type="number" className={cellInput} value={v.price} onChange={(e) => setA(k, "price", Number(e.target.value))} /></td>
          <td className={td}><input type="checkbox" checked={!!v.waived_on_annual} onChange={(e) => setA(k, "waived_on_annual", e.target.checked)} /></td>
          <td className={td}><input type="checkbox" checked={v.active !== false} onChange={(e) => setA(k, "active", e.target.checked)} /></td>
        </tr>)}</tbody>
      </table></div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 p-5 border-t border-surface-border">
        <div>
          <div className="text-[11px] font-semibold text-gray-400 uppercase tracking-wide mb-1">Quick quote</div>
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <select className="border border-surface-border rounded px-2 py-1 text-sm" value={preview.plan} onChange={(e) => setPreview({ ...preview, plan: e.target.value })}>{Object.keys(P).map((k) => <option key={k} value={k}>{P[k].label}</option>)}</select>
            <input type="number" className={cellInput} value={preview.n} onChange={(e) => setPreview({ ...preview, n: Number(e.target.value) })} /> employees
            <select className="border border-surface-border rounded px-2 py-1 text-sm" value={preview.province} onChange={(e) => setPreview({ ...preview, province: e.target.value })}>{Object.entries(PROVINCE_NAMES).map(([c, n]) => <option key={c} value={c}>{n}</option>)}</select>
            <label className="text-xs flex items-center gap-1"><input type="checkbox" checked={preview.annual} onChange={(e) => setPreview({ ...preview, annual: e.target.checked })} /> annual prepay</label>
          </div>
          {spec && <table className="mt-2 text-sm"><tbody>
            <tr><td className="pr-4 text-gray-500">{preview.n} × {cad(spec.pepm)}</td><td className="text-right tabular-nums">{cad(raw)}</td></tr>
            {raw < Number(spec.min_monthly) && <tr><td className="pr-4 text-gray-500">minimum applied</td><td className="text-right tabular-nums">{cad(spec.min_monthly)}</td></tr>}
            {disc > 0 && <tr><td className="pr-4 text-gray-500">annual −{spec.annual_discount_pct}%</td><td className="text-right tabular-nums">−{cad(disc)}</td></tr>}
            <tr><td className="pr-4 text-gray-500">{taxLabel}</td><td className="text-right tabular-nums">{cad(sub * taxPct / 100)}</td></tr>
            <tr className="font-semibold"><td className="pr-4">Total / month</td><td className="text-right tabular-nums">{cad(sub * (1 + taxPct / 100))}</td></tr>
          </tbody></table>}
        </div>
        <div>
          <div className="text-[11px] font-semibold text-gray-400 uppercase tracking-wide mb-1">Tenants — this month</div>
          <table className="w-full text-sm">
            <thead><tr><th className={th}>Org</th><th className={th}>Plan</th><th className={`${th} text-right`}>Emp</th><th className={`${th} text-right`}>Before tax</th><th className={`${th} text-right`}>Total</th></tr></thead>
            <tbody>{(mrr?.orgs || []).map((o: any) => <tr key={o.org_id}><td className={td}>{o.name} <span className="text-[10px] text-gray-400">{o.country}</span></td><td className={`${td} capitalize`}>{o.plan}{o.pepm_override ? <span className="text-[10px] text-amber-600"> @{o.pepm_override}</span> : null}</td><td className={`${td} text-right tabular-nums`}>{o.active_employees}</td><td className={`${td} text-right tabular-nums`}>{cad(o.subtotal)}</td><td className={`${td} text-right tabular-nums font-semibold`}>{cad(o.total)}</td></tr>)}</tbody>
          </table>
        </div>
      </div>
      <p className="px-5 pb-3 text-[11px] text-gray-400">Negotiated per-tenant rates: Tenants page → PEPM override.</p>
    </div>
  );
}

const FLAG: Record<string, string> = { CA: "🇨🇦" };
const PLAN_ACCENT: Record<string, string> = {
  starter: "border-gray-200", growth: "border-teal-300", enterprise: "border-primary-300",
};
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

function money(amount: number | undefined, _country: string, m?: CountryMeta) {
  const sym = m?.symbol ?? "$";
  const locale = m?.locale ?? "en-CA";
  return `${sym}${(amount ?? 0).toLocaleString(locale)}`;
}

export default function PlansPricingPage() {
  const { data, isLoading } = useQuery({ queryKey: ["platform", "orgs"], queryFn: () => platformApi.orgs() });
  const plans = data?.plans || {};             // plan -> modules[]
  const pricing = data?.plan_pricing || {};    // country -> plan -> price
  const countries = data?.countries || {};
  const allModules = data?.all_modules || [];
  const countryKeys = Object.keys(countries);
  const planKeys = Object.keys(plans);

  if (isLoading) return <div className="grid place-items-center py-24"><Spinner className="h-8 w-8" /></div>;

  return (
    <div>
      <PageHeader title="Plans & Pricing" icon={Layers} subtitle="Subscription tiers, the modules each unlocks, and pricing in Canadian dollars" />

      {/* Plan tier cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
        {planKeys.map((p) => {
          const mods: string[] = plans[p] || [];
          return (
            <div key={p} className={`card border-2 ${PLAN_ACCENT[p] || "border-surface-border"}`}>
              <div className="flex items-center justify-between mb-2">
                <h3 className="font-display text-lg font-bold text-gray-800">{cap(p)}</h3>
                {p === "enterprise" && <span className="badge-purple text-[9px]">Full suite</span>}
              </div>
              {/* Price per country */}
              <div className="space-y-1 mb-3">
                {countryKeys.map((c) => (
                  <div key={c} className="flex items-center justify-between text-sm">
                    <span className="text-gray-500">{FLAG[c]} {countries[c].label}</span>
                    <span className="font-semibold text-gray-800 tabular-nums">{money(pricing[c]?.[p], c, countries[c])}<span className="text-xs font-normal text-gray-400">/mo</span></span>
                  </div>
                ))}
              </div>
              <div className="text-[11px] font-semibold text-gray-400 uppercase tracking-wide mb-1.5">{mods.length} modules included</div>
              <div className="flex flex-wrap gap-1.5">
                {mods.map((m) => <span key={m} className="text-[11px] px-2 py-0.5 rounded-full bg-primary-50 text-primary-700 capitalize">{m}</span>)}
              </div>
            </div>
          );
        })}
      </div>

      {/* Per-employee-per-month plans (CAD) */}
      <PepmCatalogSection />

      {/* Module → plan matrix */}
      <div className="card !p-0 mb-6">
        <div className="px-5 py-3 border-b border-surface-border"><h3 className="font-display font-semibold text-gray-800">Module availability by plan</h3></div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-surface text-gray-400 text-left text-[11px] uppercase tracking-wide">
              <tr>
                <th className="px-5 py-2.5 font-semibold">Module</th>
                {planKeys.map((p) => <th key={p} className="px-3 py-2.5 font-semibold text-center">{cap(p)}</th>)}
              </tr>
            </thead>
            <tbody>
              {allModules.map((m) => (
                <tr key={m} className="border-t border-surface-border">
                  <td className="px-5 py-2 capitalize text-gray-700">{m}{m === "core" && <span className="ml-1 text-[10px] text-gray-400">(always on)</span>}</td>
                  {planKeys.map((p) => (
                    <td key={p} className="px-3 py-2 text-center">
                      {(plans[p] || []).includes(m)
                        ? <Check className="w-4 h-4 text-primary-600 inline" />
                        : <span className="text-gray-300">—</span>}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Country presets */}
      <div className="card !p-0">
        <div className="px-5 py-3 border-b border-surface-border flex items-center gap-2">
          <Globe className="w-4 h-4 text-primary-500" /><h3 className="font-display font-semibold text-gray-800">Country preset</h3>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-surface text-gray-400 text-left text-[11px] uppercase tracking-wide">
              <tr>
                <th className="px-5 py-2.5 font-semibold">Country</th>
                <th className="px-3 py-2.5 font-semibold">Currency</th>
                <th className="px-3 py-2.5 font-semibold">Tax</th>
                <th className="px-3 py-2.5 font-semibold">{"Region"}</th>
                <th className="px-3 py-2.5 font-semibold">Regions available</th>
              </tr>
            </thead>
            <tbody>
              {countryKeys.map((c) => {
                const m = countries[c];
                return (
                  <tr key={c} className="border-t border-surface-border align-top">
                    <td className="px-5 py-2.5 font-medium text-gray-800">{FLAG[c]} {m.label}</td>
                    <td className="px-3 py-2.5 text-gray-700">{m.symbol} {m.currency}</td>
                    <td className="px-3 py-2.5 text-gray-600">{m.tax_label} · {m.tax_id_label}</td>
                    <td className="px-3 py-2.5 text-gray-600">{m.region_label}</td>
                    <td className="px-3 py-2.5 text-gray-500 text-xs">{m.regions.join(", ")}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
      <p className="text-[11px] text-gray-400 mt-3">Lexora HRM onboards Canadian tenants only (CRA payroll engine, all 13 provinces and territories). Prices are the monthly plan minimums in CAD; a plan sets the default module bundle, overridable per tenant on the Tenants page.</p>
    </div>
  );
}
