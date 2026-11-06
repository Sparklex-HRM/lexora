import { useState, useMemo } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Building2, Plus, X, Users, CheckCircle2, Ban, SlidersHorizontal, Search,
  Wallet, Eye, RefreshCw, Globe,
} from "lucide-react";
import toast from "react-hot-toast";
import { PageHeader, StatTile, Spinner, Empty } from "../components/ui";
import AppModal from "../components/Modal";
import { authApi, platformApi, type PlatformOrg, type CountryMeta } from "../api/endpoints";
import { PROVINCE_NAMES } from "../api/regions";

const PLAN_TONE: Record<string, string> = {
  starter: "bg-gray-100 text-gray-600", growth: "bg-teal-50 text-teal-600", enterprise: "bg-primary-100 text-primary-700",
};
const FLAG: Record<string, string> = { CA: "🇨🇦" };
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
const fmtDate = (d?: string | null) => d ? new Date(d).toLocaleDateString("en-CA", { month: "short", day: "numeric", year: "numeric" }) : "—";

function money(amount: number | undefined, country: string, countries?: Record<string, CountryMeta>) {
  const m = countries?.[country];
  const sym = m?.symbol ?? "$";
  const locale = m?.locale ?? "en-CA";
  return `${sym}${(amount ?? 0).toLocaleString(locale)}`;
}

export default function PlatformPage() {
  const qc = useQueryClient();
  const { data, isLoading, refetch, isFetching } = useQuery({ queryKey: ["platform", "orgs"], queryFn: () => platformApi.orgs() });
  const [showNew, setShowNew] = useState(false);
  const [modOrg, setModOrg] = useState<PlatformOrg | null>(null);
  const [viewOrg, setViewOrg] = useState<PlatformOrg | null>(null);
  const [q, setQ] = useState("");
  const [fCountry, setFCountry] = useState("");
  const [fPlan, setFPlan] = useState("");
  const [fStatus, setFStatus] = useState("");

  const invalidate = () => qc.invalidateQueries({ queryKey: ["platform"] });
  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: any }) => platformApi.updateOrg(id, body),
    onSuccess: () => { invalidate(); toast.success("Tenant updated"); },
    onError: (e: any) => toast.error(e?.response?.data?.detail || "Update failed"),
  });

  const orgs = data?.orgs || [];
  const plans = data?.plans || {};
  const countries = data?.countries || {};
  const planPricing = data?.plan_pricing || {};
  const countryKeys = Object.keys(countries);

  const active = orgs.filter((o) => o.is_active).length;
  const mrr = useMemo(() => {
    const m: Record<string, number> = {};
    orgs.filter((o) => o.is_active).forEach((o) => { m[o.country] = (m[o.country] || 0) + (o.price || 0); });
    return m;
  }, [orgs]);
  const byCountry = useMemo(() => {
    const m: Record<string, number> = {};
    orgs.forEach((o) => { m[o.country] = (m[o.country] || 0) + 1; });
    return m;
  }, [orgs]);
  const byPlan = useMemo(() => {
    const m: Record<string, number> = {};
    orgs.forEach((o) => { m[o.plan] = (m[o.plan] || 0) + 1; });
    return m;
  }, [orgs]);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return orgs.filter((o) =>
      (!needle || `${o.name} ${o.code || ""} ${o.admin_email || ""}`.toLowerCase().includes(needle)) &&
      (!fCountry || o.country === fCountry) &&
      (!fPlan || o.plan === fPlan) &&
      (!fStatus || (fStatus === "active" ? o.is_active : !o.is_active)));
  }, [orgs, q, fCountry, fPlan, fStatus]);

  return (
    <div>
      <PageHeader title="Platform" icon={Building2} subtitle="Lexora HRM operator console — Canadian tenants, plans & revenue"
        actions={
          <div className="flex items-center gap-2">
            <button className="btn-secondary inline-flex items-center gap-1.5" onClick={() => refetch()} disabled={isFetching}>
              <RefreshCw className={`w-4 h-4 ${isFetching ? "animate-spin" : ""}`} />Refresh
            </button>
            <button className="btn-primary inline-flex items-center gap-1.5" onClick={() => setShowNew(true)}><Plus className="w-4 h-4" />New tenant</button>
          </div>
        } />

      {/* KPIs */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-3">
        <StatTile label="Tenants" value={data?.total ?? "—"} sub={`${active} active · ${countryKeys.length} countries`} icon={Building2} accent />
        <StatTile label="Total users" value={data?.total_users ?? "—"} icon={Users} />
        <StatTile label="MRR · Canada" value={money(mrr.CA || 0, "CA", countries)} sub={`/mo · ${byCountry.CA || 0} tenants`} icon={Wallet} />
        <StatTile label="ARR · Canada" value={money((mrr.CA || 0) * 12, "CA", countries)} sub="MRR × 12" icon={Wallet} />
      </div>

      {/* Breakdown chips */}
      <div className="flex flex-wrap items-center gap-2 mb-4 text-xs">
        <span className="text-gray-400 font-semibold uppercase tracking-wide">By plan:</span>
        {Object.keys(plans).map((p) => (
          <span key={p} className={`px-2 py-0.5 rounded-full font-medium ${PLAN_TONE[p] || "bg-gray-100"}`}>{cap(p)} · {byPlan[p] || 0}</span>
        ))}
        <span className="text-gray-300 mx-1">|</span>
        <span className="text-gray-400 font-semibold uppercase tracking-wide">By country:</span>
        {countryKeys.map((c) => (
          <span key={c} className="px-2 py-0.5 rounded-full bg-surface text-gray-600 font-medium">{FLAG[c] || ""} {countries[c].label} · {byCountry[c] || 0}</span>
        ))}
      </div>

      {/* Filter bar */}
      <div className="card !p-3 flex flex-wrap items-end gap-3 mb-4">
        <div className="flex-1 min-w-[220px]">
          <label className="block text-[10px] font-semibold text-gray-400 uppercase tracking-wide mb-0.5">Search</label>
          <div className="relative">
            <Search className="w-3.5 h-3.5 text-gray-400 absolute left-2.5 top-1/2 -translate-y-1/2" />
            <input className="input !pl-8 text-sm w-full" placeholder="Name, code, admin email…" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
        </div>
        <div>
          <label className="block text-[10px] font-semibold text-gray-400 uppercase tracking-wide mb-0.5">Country</label>
          <select className="input text-sm !py-1.5" value={fCountry} onChange={(e) => setFCountry(e.target.value)}>
            <option value="">All countries</option>
            {countryKeys.map((c) => <option key={c} value={c}>{countries[c].label}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-[10px] font-semibold text-gray-400 uppercase tracking-wide mb-0.5">Plan</label>
          <select className="input text-sm !py-1.5" value={fPlan} onChange={(e) => setFPlan(e.target.value)}>
            <option value="">All plans</option>
            {Object.keys(plans).map((p) => <option key={p} value={p}>{cap(p)}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-[10px] font-semibold text-gray-400 uppercase tracking-wide mb-0.5">Status</label>
          <select className="input text-sm !py-1.5" value={fStatus} onChange={(e) => setFStatus(e.target.value)}>
            <option value="">All</option><option value="active">Active</option><option value="suspended">Suspended</option>
          </select>
        </div>
      </div>

      {/* Tenants table */}
      <div className="card !p-0">
        {isLoading ? <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div>
          : !orgs.length ? <Empty>No tenants yet — provision the first one.</Empty>
          : !filtered.length ? <Empty>No tenants match these filters.</Empty>
          : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm min-w-[920px]">
                <thead className="bg-surface text-gray-400 text-left text-[11px] uppercase tracking-wide">
                  <tr>
                    <th className="px-5 py-2.5 font-semibold">Tenant</th>
                    <th className="px-3 py-2.5 font-semibold">Region</th>
                    <th className="px-3 py-2.5 font-semibold">Plan</th>
                    <th className="px-3 py-2.5 font-semibold">Modules</th>
                    <th className="px-3 py-2.5 font-semibold">Agents</th>
                    <th className="px-3 py-2.5 font-semibold text-right">Users</th>
                    <th className="px-3 py-2.5 font-semibold text-right">Price /mo</th>
                    <th className="px-3 py-2.5 font-semibold">Status</th>
                    <th className="px-4 py-2.5 font-semibold text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((o) => (
                    <tr key={o.id} className={`border-t border-surface-border ${!o.is_active ? "opacity-50" : ""}`}>
                      <td className="px-5 py-2.5">
                        <div className="font-medium text-gray-800 flex items-center gap-2">
                          {o.name}
                          {o.code && <span className="text-[10px] font-mono font-semibold px-1.5 py-0.5 rounded bg-primary-50 text-primary-700" title="Login code — tenants sign in with this">{o.code}</span>}
                        </div>
                        <div className="text-[11px] text-gray-400">{o.admin_email || "—"}</div>
                      </td>
                      <td className="px-3 py-2.5">
                        <div className="flex items-center gap-1.5 text-gray-700">
                          <span>{FLAG[o.country] || ""}</span>
                          <span className="text-xs">{countries[o.country]?.label || o.country}</span>
                        </div>
                        <div className="text-[11px] text-gray-400">{o.province || "—"} · {o.currency || "—"}</div>
                      </td>
                      <td className="px-3 py-2.5">
                        <select className={`text-xs font-semibold rounded-md px-2 py-1 border-0 ${PLAN_TONE[o.plan] || "bg-gray-100"}`}
                          value={o.plan} onChange={(e) => update.mutate({ id: o.id, body: { plan: e.target.value, modules: null } })}>
                          {Object.keys(plans).map((p) => <option key={p} value={p}>{cap(p)}</option>)}
                        </select>
                      </td>
                      <td className="px-3 py-2.5">
                        <button className="inline-flex items-center gap-1.5 text-xs text-gray-600 hover:text-primary-600" onClick={() => setModOrg(o)}>
                          <span className="tabular-nums font-medium">{o.modules.length}</span> modules
                          {o.explicit_modules && <span className="badge-amber text-[9px]">custom</span>}
                          <SlidersHorizontal className="w-3 h-3" />
                        </button>
                      </td>
                      <td className="px-3 py-2.5">
                        <button className={`text-[11px] font-semibold px-2 py-1 rounded-md ${o.agentic ? "bg-teal-50 text-teal-700" : "bg-gray-100 text-gray-500"}`}
                          title={o.agentic ? "Agentic operations add-on ON (starts in shadow mode) — click to switch off" : "Agentic operations add-on off — click to switch on (shadow mode)"}
                          onClick={() => update.mutate({ id: o.id, body: { agentic: !o.agentic } })}>
                          Agents {o.agentic ? "on" : "off"}
                        </button>
                      </td>
                      <td className="px-3 py-2.5 text-right tabular-nums text-gray-700">{o.user_count}</td>
                      <td className="px-3 py-2.5 text-right tabular-nums font-medium text-gray-800">{money(o.price, o.country, countries)}</td>
                      <td className="px-3 py-2.5">{o.is_active ? <span className="badge-green">Active</span> : <span className="badge-red">Suspended</span>}</td>
                      <td className="px-4 py-2.5 text-right whitespace-nowrap">
                        <span className="inline-flex gap-1.5">
                          <button className="btn-secondary !py-1 !px-2 text-xs inline-flex items-center gap-1" onClick={() => setViewOrg(o)} title="View details"><Eye className="w-3.5 h-3.5" /></button>
                          <button className="btn-secondary !py-1 !px-2.5 text-xs inline-flex items-center gap-1"
                            onClick={() => update.mutate({ id: o.id, body: { is_active: !o.is_active } })}>
                            {o.is_active ? <><Ban className="w-3.5 h-3.5" />Suspend</> : <><CheckCircle2 className="w-3.5 h-3.5" />Activate</>}
                          </button>
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </div>
      <p className="text-[11px] text-gray-400 mt-3">
        Above the org layer. Prices are monthly plan minimums in Canadian dollars. Changing a plan resets modules to the plan default;
        the module control lets you override individual flags (add-ons / trials). Suspending a tenant blocks its logins. The operator cannot see any tenant's employee or payroll data.
      </p>

      {showNew && <NewTenantModal plans={Object.keys(plans)} countries={countries} planPricing={planPricing} onClose={() => setShowNew(false)} onDone={invalidate} />}
      {viewOrg && <OrgDetailModal org={viewOrg} countries={countries} onClose={() => setViewOrg(null)} />}
      {modOrg && <ModulesModal org={modOrg} allModules={data?.all_modules || []} planDefault={plans[modOrg.plan] || []}
        onClose={() => setModOrg(null)} onSave={(mods) => { update.mutate({ id: modOrg.id, body: { modules: mods } }); setModOrg(null); }}
        onReset={() => { update.mutate({ id: modOrg.id, body: { modules: null } }); setModOrg(null); }} />}
    </div>
  );
}

function NewTenantModal({ plans, countries, planPricing, onClose, onDone }: {
  plans: string[]; countries: Record<string, CountryMeta>; planPricing: Record<string, Record<string, number>>;
  onClose: () => void; onDone: () => void;
}) {
  // Lexora HRM is Canada-only: every tenant is created in CA (first province offered by the server)
  const startCountry = "CA";
  const [f, setF] = useState({ org_name: "", country: startCountry, province: countries[startCountry]?.regions[0] || "", plan: "growth", admin_email: "", admin_first_name: "", admin_last_name: "", admin_password: "" });
  const set = (k: string, v: string) => setF((s) => ({ ...s, [k]: v }));
  const setCountry = (c: string) => setF((s) => ({ ...s, country: c, province: countries[c]?.regions[0] || "" }));
  const meta = countries[f.country];
  const price = planPricing?.[f.country]?.[f.plan];
  const create = useMutation({
    mutationFn: () => platformApi.createOrg(f),
    onSuccess: (r: any) => { toast.success(`Tenant "${r.name}" provisioned · code ${r.code}`); onDone(); onClose(); },
    onError: (e: any) => toast.error(e?.response?.data?.detail || "Could not create tenant"),
  });
  // WASA #7: the real policy (server minimum, breached list); the admin must change it at first sign-in.
  const { data: policy } = useQuery({ queryKey: ["password-policy"], queryFn: authApi.passwordPolicy, staleTime: 3_600_000 });
  const minLen = policy?.min_length ?? 10;
  const emailOk = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(f.admin_email.trim());
  const pwMix = /[A-Z]/.test(f.admin_password) && /[a-z]/.test(f.admin_password) && /[0-9]/.test(f.admin_password) && /[^A-Za-z0-9]/.test(f.admin_password);
  const ok = f.org_name.trim() && f.admin_first_name.trim() && emailOk && f.admin_password.length >= minLen && pwMix;

  return (
    <AppModal title="Provision a new tenant" subtitle="Creates the organisation, its plan and the first org admin login." onClose={onClose} size="form"
      footer={<><button className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={!ok || create.isPending} onClick={() => create.mutate()}>{create.isPending ? "Provisioning…" : "Create tenant"}</button></>}>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-start">
          {/* Organization + country */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <label className="text-xs font-semibold text-gray-500 sm:col-span-2">Organization name<span className="text-red-500"> *</span>
              <input className="input mt-1" value={f.org_name} onChange={(e) => set("org_name", e.target.value)} placeholder="e.g. Maple Leaf Logistics Inc." maxLength={120} /></label>
            <label className="text-xs font-semibold text-gray-500">Country
              <select className="input mt-1" value={f.country} onChange={(e) => setCountry(e.target.value)}>
                {Object.keys(countries).map((c) => <option key={c} value={c}>{FLAG[c] || ""} {countries[c].label}</option>)}
              </select></label>
            <label className="text-xs font-semibold text-gray-500">{meta?.region_label || "Province"}
              <select className="input mt-1" value={f.province} onChange={(e) => set("province", e.target.value)}>
                {(meta?.regions || []).map((r) => <option key={r} value={r}>{PROVINCE_NAMES[r] || r}</option>)}
              </select></label>
          </div>

          {/* Country preview */}
          {meta && (
            <div className="rounded-lg bg-surface border border-surface-border px-3 py-2 flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-gray-600">
              <span className="inline-flex items-center gap-1.5"><Globe className="w-3.5 h-3.5 text-primary-500" /><span className="font-semibold text-gray-700">{FLAG[f.country]} {meta.label}</span></span>
              <span>Currency <span className="font-medium text-gray-800">{meta.symbol} {meta.currency}</span></span>
              <span>{meta.tax_label} · {meta.tax_id_label}</span>
              <span>{meta.locale}</span>
            </div>
          )}

          {/* Plan + price */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 items-end">
            <label className="text-xs font-semibold text-gray-500">Plan
              <select className="input mt-1" value={f.plan} onChange={(e) => set("plan", e.target.value)}>{plans.map((p) => <option key={p} value={p}>{cap(p)}</option>)}</select></label>
            <div className="text-right">
              <div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">Price</div>
              <div className="text-lg font-bold text-primary-600 tabular-nums">{money(price, f.country, countries)}<span className="text-xs font-normal text-gray-400"> /mo</span></div>
            </div>
          </div>

          {/* First admin */}
          <div className="border-t border-surface-border pt-3">
            <div className="text-[11px] font-semibold text-gray-400 uppercase mb-2">First org admin</div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <label className="text-xs font-semibold text-gray-500">First name<span className="text-red-500"> *</span><input className="input mt-1" maxLength={60} value={f.admin_first_name} onChange={(e) => set("admin_first_name", e.target.value)} /></label>
              <label className="text-xs font-semibold text-gray-500">Last name<input className="input mt-1" maxLength={60} value={f.admin_last_name} onChange={(e) => set("admin_last_name", e.target.value)} /></label>
              <label className="text-xs font-semibold text-gray-500 sm:col-span-2">Email<span className="text-red-500"> *</span><input maxLength={254} className="input mt-1" type="email" value={f.admin_email} onChange={(e) => set("admin_email", e.target.value)} placeholder="admin@company.ca" />
                {f.admin_email && !emailOk && <span className="block mt-1 text-[11px] font-normal text-red-600">Enter a valid e-mail address.</span>}</label>
              <label className="text-xs font-semibold text-gray-500 sm:col-span-2">Temporary password<span className="text-red-500"> *</span><input className="input mt-1" value={f.admin_password} onChange={(e) => set("admin_password", e.target.value)} placeholder={`at least ${minLen} characters`} />
                <span className="block mt-1 text-[11px] font-normal text-gray-400">{policy?.text || `At least ${minLen} characters with a capital, a small letter, a number and a symbol.`} The admin must choose their own password at first sign-in.</span>
                {f.admin_password && (f.admin_password.length < minLen || !pwMix) && <span className="block mt-1 text-[11px] font-normal text-red-600">Needs {minLen}+ characters with a capital, a small letter, a number and a symbol.</span>}</label>
            </div>
          </div>
        </div>
    </AppModal>
  );
}

function OrgDetailModal({ org, countries, onClose }: { org: PlatformOrg; countries: Record<string, CountryMeta>; onClose: () => void }) {
  const meta = countries[org.country];
  const Row = ({ label, value }: { label: string; value: any }) => (
    <div className="flex items-center justify-between gap-3 px-3 py-2 bg-surface-card border-b border-surface-border last:border-0">
      <span className="text-[11px] font-semibold text-gray-400 uppercase tracking-wide">{label}</span>
      <span className="text-sm text-gray-800 text-right">{value ?? "—"}</span>
    </div>
  );
  return (
    <AppModal size="form" onClose={onClose} printable
      title={<span className="flex items-center gap-2">{org.name}{org.code && <span className="text-[10px] font-mono font-semibold px-1.5 py-0.5 rounded bg-primary-50 text-primary-700">{org.code}</span>}</span>}
      subtitle={`${FLAG[org.country] || ""} ${meta?.label || org.country} · ${org.is_active ? "Active" : "Suspended"}`}>
        <div className="space-y-4">
          <div className="grid grid-cols-3 gap-3">
            <StatTile label="Users" value={org.user_count} />
            <StatTile label="Plan" value={cap(org.plan)} />
            <StatTile label="Price /mo" value={money(org.price, org.country, countries)} accent />
          </div>
          <div className="rounded-lg border border-surface-border overflow-hidden">
            <Row label="Login code" value={org.code} />
            <Row label="Country" value={`${FLAG[org.country] || ""} ${meta?.label || org.country}`} />
            <Row label={meta?.region_label || "Province"} value={org.province} />
            <Row label="Currency" value={`${meta?.symbol || ""} ${org.currency || meta?.currency || "—"}`} />
            <Row label="Tax" value={meta ? `${meta.tax_label} · ${meta.tax_id_label}` : "—"} />
            <Row label="Admin" value={org.admin_email} />
            <Row label="Modules" value={`${org.modules.length}${org.explicit_modules ? " (custom)" : ""}`} />
            <Row label="Created" value={fmtDate(org.created_at)} />
          </div>
          <div>
            <div className="text-[11px] font-semibold text-gray-400 uppercase mb-1.5">Subscribed modules</div>
            <div className="flex flex-wrap gap-1.5">
              {org.modules.map((m) => <span key={m} className="text-[11px] px-2 py-0.5 rounded-full bg-primary-50 text-primary-700 capitalize">{m}</span>)}
            </div>
          </div>
        </div>
    </AppModal>
  );
}

function ModulesModal({ org, allModules, planDefault, onClose, onSave, onReset }:
  { org: PlatformOrg; allModules: string[]; planDefault: string[]; onClose: () => void; onSave: (m: string[]) => void; onReset: () => void }) {
  const [sel, setSel] = useState<Set<string>>(() => new Set(org.modules));
  const toggle = (m: string) => setSel((s) => { const n = new Set(s); n.has(m) ? n.delete(m) : n.add(m); return n; });
  return (
    <AppModal size="confirm" onClose={onClose} title={`Modules · ${org.name}`} subtitle={`${cap(org.plan)} plan default: ${planDefault.length} modules`}>
        <div>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
            {allModules.map((m) => {
              const on = sel.has(m); const isCore = m === "core"; const inPlan = planDefault.includes(m);
              return (
                <button key={m} disabled={isCore} onClick={() => toggle(m)}
                  className={`flex items-center justify-between px-3 py-2 rounded-lg border text-sm ${on ? "border-primary-300 bg-primary-50 text-primary-700" : "border-surface-border text-gray-500"} ${isCore ? "opacity-60 cursor-not-allowed" : ""}`}>
                  <span className="capitalize">{m}</span>
                  <span className="flex items-center gap-1">
                    {!inPlan && on && <span className="badge-amber text-[9px]">add-on</span>}
                    {on ? <CheckCircle2 className="w-4 h-4" /> : <span className="w-4 h-4 rounded-full border border-gray-300" />}
                  </span>
                </button>
              );
            })}
          </div>
          <div className="flex items-center justify-between pt-4">
            <button className="text-xs text-gray-500 hover:text-gray-700" onClick={onReset}>Reset to {cap(org.plan)} default</button>
            <button className="btn-primary" onClick={() => onSave([...sel])}>Save modules</button>
          </div>
        </div>
    </AppModal>
  );
}
