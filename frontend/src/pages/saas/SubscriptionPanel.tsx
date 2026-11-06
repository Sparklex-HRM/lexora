import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { BadgeCheck, CreditCard, AlertTriangle, History } from "lucide-react";
import { Badge } from "../../components/ui";
import { subscriptionApi, errText, type SubscriptionState } from "../../api/saas";
import { billingApi } from "../../api/privacy";
import { regionOptions } from "../../api/regions";

const fmt = (s: string | null) => (s ? new Date(s).toLocaleDateString("en-CA", { dateStyle: "medium" }) : "—");
const cad = (n: number) => new Intl.NumberFormat("en-CA", { style: "currency", currency: "CAD", maximumFractionDigits: 2 }).format(n || 0);
const TONE: Record<string, string> = { active: "green", trialing: "teal", past_due: "amber", cancelled: "red", expired: "red" };
const PAY_BY_INVOICE = "Online payment is not set up on this server. Pay the monthly invoice by EFT or cheque, or contact Lexora HRM.";

/** The subscription state as the auth service returns it (GST/HST buyer block, seller tax set-up). */
type CaSubscription = SubscriptionState & {
  seller_tax?: { configured: boolean; missing?: string[] };
  buyer: SubscriptionState["buyer"] & { gst_hst_number?: string | null; province?: string | null };
};

/** S4: subscription status, plan change, pay by invoice, GST/HST invoice details (top of Plan & billing). */
export default function SubscriptionPanel() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["billing", "subscription"], queryFn: subscriptionApi.state });
  const s = data as CaSubscription | undefined;
  const { data: hist } = useQuery({ queryKey: ["billing", "history"], queryFn: subscriptionApi.history });
  const [plan, setPlan] = useState<string>("");
  const [buyer, setBuyer] = useState<any>(null);
  const done = () => { qc.invalidateQueries({ queryKey: ["billing"] }); qc.invalidateQueries({ queryKey: ["me-subscription"] }); };
  const change = useMutation({ mutationFn: () => subscriptionApi.changePlan(plan), onSuccess: (r) => { toast.success(r.note || "Plan changed"); setPlan(""); done(); }, onError: (e) => toast.error(errText(e)) });
  // Online card payment is not built: the server answers 503 → the customer pays the invoice by EFT / cheque.
  const pay = useMutation({ mutationFn: () => subscriptionApi.checkout(plan || undefined),
    onSuccess: (r) => { if (r?.short_url) window.location.href = r.short_url; },
    onError: (e: any) => e?.response?.status === 503 ? toast(e?.response?.data?.message || PAY_BY_INVOICE) : toast.error(errText(e)) });
  const cancel = useMutation({ mutationFn: () => subscriptionApi.cancel(true), onSuccess: () => { toast.success("Subscription will end at the end of the period"); done(); }, onError: (e) => toast.error(errText(e)) });
  const saveBuyer = useMutation({ mutationFn: () => billingApi.saveSettings(buyer), onSuccess: () => { toast.success("Invoice details saved"); setBuyer(null); done(); }, onError: (e) => toast.error(errText(e)) });
  if (!s) return null;
  const b = buyer || { legal_name: s.buyer.name || "", billing_address: s.buyer.address || "", billing_province: s.buyer.province || "", gst_hst_number: s.buyer.gst_hst_number || "" };
  const plans = Object.entries(s.plans || {}) as [string, any][];
  const ro = s.mode === "read_only";
  const taxInvoice = s.invoice_kind === "tax_invoice";

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-4">
      <div className={`card lg:col-span-2 ${ro ? "ring-1 ring-red-300" : ""}`} data-testid="subscription-panel">
        <div className="flex items-center gap-2 mb-3">
          <BadgeCheck className="w-4 h-4 text-primary-600" /><span className="font-display font-semibold text-gray-800">Subscription</span>
          <Badge tone={TONE[s.status] || "default"}>{s.status}</Badge>
          {ro && <span className="text-xs text-red-700 inline-flex items-center gap-1"><AlertTriangle className="w-3.5 h-3.5" />read-only</span>}
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm mb-3">
          <div><div className="text-[11px] text-gray-400">Plan</div><div className="font-semibold capitalize">{s.plan}</div></div>
          <div><div className="text-[11px] text-gray-400">{s.status === "trialing" ? "Trial ends" : "Current period ends"}</div><div className="font-semibold">{fmt(s.status === "trialing" ? s.trial_ends_at : s.current_period_end)}</div></div>
          <div><div className="text-[11px] text-gray-400">Employees</div><div className="font-semibold">{s.employees}{s.employee_limit ? ` / ${s.employee_limit}` : ""}</div></div>
          <div><div className="text-[11px] text-gray-400">Seats paid</div><div className="font-semibold">{s.seats ?? "—"}</div></div>
        </div>
        {s.banner && <div className="text-xs text-gray-600 bg-gray-50 border border-surface-border rounded px-3 py-2 mb-3">{s.banner.text}</div>}
        <div className="flex flex-wrap items-end gap-2">
          <label className="text-xs text-gray-500">Plan
            <select className="block border border-surface-border rounded-lg px-3 py-1.5 text-sm bg-white" value={plan || s.plan} onChange={(e) => setPlan(e.target.value)}>
              {plans.map(([k, p]) => <option key={k} value={k}>{p.label}{p.custom ? " (quoted)" : ` — ${cad(p.pepm)}/employee/month`}{p.max_employees ? ` · up to ${p.max_employees}` : ""}</option>)}
            </select>
          </label>
          <button className="btn-secondary text-sm px-3 py-1.5" disabled={!plan || plan === s.plan || change.isPending} onClick={() => change.mutate()}>Change plan</button>
          <button className="btn-primary text-sm px-3 py-1.5 inline-flex items-center gap-1" disabled={pay.isPending || (s.status === "active" && !!s.provider.subscription_id)}
            onClick={() => pay.mutate()} title={s.provider.configured ? "" : PAY_BY_INVOICE}>
            <CreditCard className="w-4 h-4" /> Pay online
          </button>
          {s.provider.subscription_id && s.status === "active" && !s.cancel_at_period_end && (
            <button className="text-xs text-red-700 underline ml-auto" onClick={() => { if (confirm("End the subscription at the end of the current period?")) cancel.mutate(); }}>Cancel subscription</button>
          )}
        </div>
        {!s.provider.configured && <p className="mt-2 text-[11px] text-amber-700">Pay by invoice (EFT or cheque): online card payment is not set up on this server. Each month's invoice is under Invoices below.</p>}
        {s.cancel_at_period_end && <p className="mt-2 text-[11px] text-gray-500">The subscription ends at the end of the current period.</p>}
      </div>

      <div className="card">
        <div className="font-display font-semibold text-gray-800 mb-1">GST/HST invoice details</div>
        <p className="text-[11px] text-gray-400 mb-2">{taxInvoice ? "Invoices are GST/HST invoices (LX-year-number), taxed by your billing province." : "Invoices are PRO FORMA until Lexora HRM's own GST/HST registration is configured on this server."}</p>
        <div className="space-y-1.5">
          <input className="input py-1.5 text-sm" placeholder="Legal name" value={b.legal_name} onChange={(e) => setBuyer({ ...b, legal_name: e.target.value })} />
          <input className="input py-1.5 text-sm uppercase" maxLength={15} placeholder="GST/HST number (if registered), e.g. 123456789RT0001" value={b.gst_hst_number} onChange={(e) => setBuyer({ ...b, gst_hst_number: e.target.value.toUpperCase() })} />
          <select className="input py-1.5 text-sm" value={b.billing_province} onChange={(e) => setBuyer({ ...b, billing_province: e.target.value })}>
            <option value="">Billing province (decides GST or HST)…</option>{regionOptions().map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
          <textarea className="input py-1.5 text-sm" rows={2} placeholder="Billing address" value={b.billing_address} onChange={(e) => setBuyer({ ...b, billing_address: e.target.value })} />
          <button className="btn-primary w-full text-sm py-1.5" disabled={!buyer || saveBuyer.isPending} onClick={() => saveBuyer.mutate()}>Save</button>
        </div>
        {!!hist?.items?.length && (
          <div className="mt-3 border-t border-surface-border pt-2">
            <div className="text-[11px] text-gray-400 flex items-center gap-1 mb-1"><History className="w-3 h-3" /> History</div>
            {hist.items.slice(0, 5).map((h, i) => <div key={i} className="text-[11px] text-gray-600">{fmt(h.at)} · {h.from_status || "new"} → {h.to_status}{h.to_plan !== h.from_plan ? ` · ${h.from_plan || ""} → ${h.to_plan}` : ""} <span className="text-gray-400">({h.source})</span></div>)}
          </div>
        )}
      </div>
    </div>
  );
}
