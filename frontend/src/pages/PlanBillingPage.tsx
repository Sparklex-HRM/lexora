import { useState } from "react";
import PrintPdfButton from "../components/PrintPdfButton";   // print wrap-up 2026-10-24
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { CreditCard, Receipt, Users, Percent, Download } from "lucide-react";
import { PageHeader, Spinner, Empty, StatTile } from "../components/ui";
import { billingApi, saveBlob, type InvoicePreview } from "../api/privacy";
import { useAuthStore } from "../store/authStore";
import { regionOptions } from "../api/regions";
import SubscriptionPanel from "./saas/SubscriptionPanel";   // S4: status, plan change, pay by invoice, GST/HST details
import { subscriptionApi } from "../api/saas";

const inputCls = "w-full border border-surface-border rounded-lg px-3 py-1.5 text-sm bg-white";
const th = "px-3 py-2 text-left text-[11px] uppercase tracking-wide text-gray-400 font-semibold";
const td = "px-3 py-1.5 text-sm text-gray-700 border-t border-surface-border/70";
const cad = (n: number, cur = "CAD") => new Intl.NumberFormat("en-CA", { style: "currency", currency: cur || "CAD", maximumFractionDigits: 2 }).format(n || 0);
/** The preview as the auth service returns it (GST/HST by billing province). */
type CaPreview = InvoicePreview & { gst_hst_number?: string | null; tax_province?: string | null; tax_rate?: number; tax_determined?: boolean };

/** Org-admin plan & billing screen (CAD per employee per month, GST/HST by billing province). */
export default function PlanBillingPage() {
  const qc = useQueryClient();
  const role = useAuthStore((s) => s.user?.role) || "";
  const { data: pv, isLoading } = useQuery({ queryKey: ["billing", "preview"], queryFn: () => billingApi.preview() });
  const p = pv as CaPreview | undefined;
  const { data: cat } = useQuery({ queryKey: ["billing", "plans", "CA"], queryFn: () => billingApi.plans("CA") });
  const { data: inv } = useQuery({ queryKey: ["billing", "invoices"], queryFn: () => billingApi.invoices() });
  const [form, setForm] = useState<any>(null);
  const save = useMutation({ mutationFn: (body: any) => billingApi.saveSettings(body), onSuccess: () => { toast.success("Billing settings saved"); setForm(null); qc.invalidateQueries({ queryKey: ["billing"] }); }, onError: (e: any) => toast.error(e?.response?.data?.detail || "Could not save") });
  const gen = useMutation({ mutationFn: () => billingApi.generateInvoice(), onSuccess: (r) => { toast.success(r.created ? `Invoice ${r.number} generated` : `Invoice ${r.number} already exists for this month`); qc.invalidateQueries({ queryKey: ["billing"] }); } });
  if (isLoading || !p) return <div className="grid place-items-center py-16"><Spinner /></div>;
  const f = form || { addons: p.addons, gst_hst_number: p.gst_hst_number || "", billing_province: p.tax_province || "", billing_email: p.billing_email || "", billing_cycle: p.billing_cycle };
  const pepm = p.billing_model === "pepm";
  const plans = cat?.pepm_plans || {};
  const addons = cat?.addons || {};
  return (
    <div>
      <PageHeader title="Plan & billing" icon={CreditCard} subtitle={pepm ? "Per-employee-per-month plan in CAD · headcount counted on the 1st · GST/HST by billing province" : "Flat monthly plan"} />
      {role !== "superadmin" && <SubscriptionPanel />}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3 mb-4">
        <StatTile label="Plan" value={p.plan_label} icon={CreditCard} accent sub={p.custom_quote ? "custom quote" : pepm ? `${cad(p.pepm || 0)} per employee / month` : "flat"} />
        <StatTile label="Active employees" value={p.active_employees} icon={Users} sub={`as of ${p.period}`} />
        <StatTile label="This month (before tax)" value={cad(p.subtotal, p.currency)} />
        <StatTile label="GST/HST" value={cad(p.gst, p.currency)} icon={Percent} sub={p.tax_determined === false ? "set the billing province" : `${p.tax_province || ""}${p.tax_rate != null ? ` · ${p.tax_rate}%` : ""}`} />
        <StatTile label="Total payable" value={cad(p.total, p.currency)} icon={Receipt} accent />
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2 card !p-0">
          <div className="px-4 py-2.5 border-b border-surface-border flex items-center gap-2">
            <span className="font-display font-semibold text-gray-800">Invoice preview — {p.period_label}</span>
            {p.free_band && <span className="badge badge-green">free band</span>}
            {p.billing_cycle === "annual" && <span className="badge badge-teal">annual prepay −{p.annual_discount_pct}%</span>}
            <button onClick={() => gen.mutate()} className="ml-auto text-xs rounded-lg bg-primary-600 text-white font-semibold px-3 py-1.5">Generate invoice</button>
          </div>
          <table className="w-full">
            <thead><tr><th className={th}>Line</th><th className={`${th} text-right`}>Qty</th><th className={`${th} text-right`}>Rate</th><th className={`${th} text-right`}>Amount</th></tr></thead>
            <tbody>
              {p.lines.map((l) => <tr key={l.code}><td className={td}>{l.label}{l.minimum_applied && <span className="ml-2 text-[10px] text-amber-700 bg-amber-50 border border-amber-200 rounded px-1">minimum {cad(l.minimum!)} applied</span>}{l.note && <div className="text-[11px] text-gray-400">{l.note}</div>}</td><td className={`${td} text-right tabular-nums`}>{l.qty}</td><td className={`${td} text-right tabular-nums`}>{cad(l.unit, p.currency)}</td><td className={`${td} text-right tabular-nums`}>{cad(l.amount, p.currency)}</td></tr>)}
              <tr><td className={`${td} font-semibold`} colSpan={3}>Subtotal</td><td className={`${td} text-right font-semibold tabular-nums`}>{cad(p.subtotal, p.currency)}</td></tr>
              {p.tax_lines.map((t) => <tr key={t.code}><td className={td} colSpan={3}>{t.label}</td><td className={`${td} text-right tabular-nums`}>{cad(t.amount, p.currency)}</td></tr>)}
              <tr className="bg-primary-50/60"><td className={`${td} font-bold text-gray-900`} colSpan={3}>Total</td><td className={`${td} text-right font-bold text-gray-900 tabular-nums`}>{cad(p.total, p.currency)}</td></tr>
            </tbody>
          </table>
          {p.note && <div className="px-4 py-2 text-[11px] text-gray-400">{p.note}</div>}
        </div>
        <div className="card">
          <div className="font-display font-semibold text-gray-800 mb-2">Billing settings</div>
          {pepm && <>
            <label className="text-[11px] text-gray-500">Billing cycle</label>
            <select className={`${inputCls} mb-2`} value={f.billing_cycle} onChange={(e) => setForm({ ...f, billing_cycle: e.target.value })}>
              <option value="monthly">Monthly</option><option value="annual">Annual prepay (−{plans[p.plan]?.annual_discount_pct ?? 15}%)</option></select>
            <label className="text-[11px] text-gray-500">Add-ons</label>
            <div className="space-y-1 mb-2">
              {Object.entries(addons).map(([k, a]: any) => <label key={k} className="flex items-start gap-2 text-xs text-gray-700">
                <input type="checkbox" checked={(f.addons || []).includes(k)} onChange={(e) => setForm({ ...f, addons: e.target.checked ? [...(f.addons || []), k] : (f.addons || []).filter((x: string) => x !== k) })} />
                <span>{a.label} <span className="text-gray-400">· {a.kind === "pepm" ? `${cad(a.price)} PEPM` : a.kind === "one_time" ? `${cad(a.price)} one-time${a.waived_on_annual ? " (waived on annual)" : ""}` : `${cad(a.price)}/month`}</span></span></label>)}
            </div>
            <label className="text-[11px] text-gray-500">Billing province (decides GST or HST)</label>
            <select className={`${inputCls} mb-2`} value={f.billing_province} onChange={(e) => setForm({ ...f, billing_province: e.target.value })}>
              <option value="">— pick —</option>{regionOptions().map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</select>
            <label className="text-[11px] text-gray-500">Your GST/HST number (optional)</label>
            <input className={`${inputCls} mb-2 uppercase`} maxLength={15} value={f.gst_hst_number} onChange={(e) => setForm({ ...f, gst_hst_number: e.target.value.toUpperCase() })} placeholder="123456789RT0001" />
          </>}
          <label className="text-[11px] text-gray-500">Billing email</label>
          <input className={`${inputCls} mb-2`} value={f.billing_email} onChange={(e) => setForm({ ...f, billing_email: e.target.value })} />
          <button onClick={() => save.mutate(f)} disabled={!form || save.isPending} className="w-full rounded-lg bg-primary-600 text-white text-sm font-semibold py-2 disabled:opacity-50">Save</button>
          {pepm && <div className="mt-3 text-[11px] text-gray-400">Plan changes are made by Lexora HRM (your account manager). Modules included: {p.modules.join(", ")}.</div>}
          {role === "superadmin" && <div className="mt-2 text-[11px] text-amber-700">You are the platform operator — use Platform → Pricing to edit plans and negotiated rates.</div>}
        </div>
      </div>
      {pepm && <div className="card !p-0 mt-4">
        <div className="px-4 py-2.5 border-b border-surface-border font-display font-semibold text-gray-800">Plans available</div>
        <table className="w-full">
          <thead><tr><th className={th}>Plan</th><th className={`${th} text-right`}>Per employee / month</th><th className={`${th} text-right`}>Minimum / month</th><th className={th}>Free band</th><th className={th}>Includes</th></tr></thead>
          <tbody>{Object.entries(plans).map(([k, pl]: any) => <tr key={k} className={k === p.plan ? "bg-primary-50/50" : ""}>
            <td className={`${td} font-medium`}>{pl.label}{k === p.plan && <span className="badge badge-teal ml-2">current</span>}</td>
            <td className={`${td} text-right tabular-nums`}>{pl.custom ? "quoted" : cad(pl.pepm)}</td>
            <td className={`${td} text-right tabular-nums`}>{cad(pl.min_monthly)}</td>
            <td className={`${td} text-xs`}>{pl.free_upto_employees ? `≤${pl.free_upto_employees} employees free for ${pl.free_months} months` : "—"}</td>
            <td className={`${td} text-xs text-gray-500`}>{pl.includes}</td></tr>)}</tbody>
        </table>
      </div>}
      <div className="card !p-0 mt-4">
        <div className="px-4 py-2.5 border-b border-surface-border font-display font-semibold text-gray-800">Invoices</div>
        <table className="w-full">
          <thead><tr><th className={th}>Number</th><th className={th}>Period</th><th className={`${th} text-right`}>Employees</th><th className={`${th} text-right`}>Subtotal</th><th className={`${th} text-right`}>GST/HST</th><th className={`${th} text-right`}>Total</th><th className={th}>Status</th><th className={th}></th></tr></thead>
          <tbody>{(inv?.items || []).map((r: any) => <tr key={r.id}>
            <td className={`${td} font-mono text-xs`}>{r.number}</td><td className={td}>{r.period}</td><td className={`${td} text-right tabular-nums`}>{r.active_employees}</td>
            <td className={`${td} text-right tabular-nums`}>{cad(r.subtotal, r.currency)}</td><td className={`${td} text-right tabular-nums`}>{cad(r.gst, r.currency)}</td><td className={`${td} text-right tabular-nums font-semibold`}>{cad(r.total, r.currency)}</td>
            <td className={td}><span className={`badge ${r.status === "paid" ? "badge-green" : "badge-teal"}`}>{r.status}</span></td>
            <td className={`${td} text-right whitespace-nowrap`}><button className="text-xs text-primary-700 hover:underline inline-flex items-center gap-1 mr-3" onClick={() => subscriptionApi.invoicePdf(r.id).then((b) => saveBlob(b, `${String(r.number).replace(/\//g, "-")}.pdf`)).catch(() => toast.error("Could not download the PDF"))}><Download className="w-3 h-3" /> {r.kind === "tax_invoice" ? "Invoice" : "Pro forma"} PDF</button><PrintPdfButton className="text-xs text-primary-700 hover:underline inline-flex items-center gap-1 mr-3" name={`${String(r.number).replace(/\//g, "-")}.pdf`} title="Print this invoice (A4)" fetchPdf={() => subscriptionApi.invoicePdf(r.id)} /><button className="text-xs text-gray-500 hover:underline inline-flex items-center gap-1" onClick={() => saveBlob(new Blob([JSON.stringify(r.detail, null, 1)], { type: "application/json" }), `${r.number}.json`)}><Download className="w-3 h-3" /> JSON</button></td></tr>)}
            {!inv?.items?.length && <tr><td colSpan={8}><Empty>No invoices generated yet.</Empty></td></tr>}</tbody>
        </table>
      </div>
    </div>
  );
}

export type { InvoicePreview };
