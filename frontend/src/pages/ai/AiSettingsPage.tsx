import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Link } from "react-router-dom";
import { Bot, KeyRound, Save, FlaskConical, Trash2, Info } from "lucide-react";
import { PageHeader, Spinner } from "../../components/ui";
import { aiSettingsApi, type AiSettings } from "../../api/ai";
import { errText } from "../../api/platform";

/** AI settings (org admin): bring-your-own Claude key, model, effort, feature switches, monthly caps, price table. */
export default function AiSettingsPage() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["ai-settings"], queryFn: aiSettingsApi.get });
  const [form, setForm] = useState<Partial<AiSettings>>({});
  const [apiKey, setApiKey] = useState("");
  useEffect(() => { if (data) setForm({ model: data.model, effort: data.effort, features: data.features, monthly_cap_tokens: data.monthly_cap_tokens, monthly_cap_cad: data.monthly_cap_cad, cap_mode: data.cap_mode, usd_cad_rate: data.usd_cad_rate, prices: data.prices }); }, [data]);
  const refresh = () => qc.invalidateQueries({ queryKey: ["ai-settings"] });
  const save = useMutation({ mutationFn: (body: any) => aiSettingsApi.save(body), onSuccess: () => { toast.success("AI settings saved"); setApiKey(""); refresh(); }, onError: (e: any) => toast.error(errText(e, "Could not save")) });
  const test = useMutation({ mutationFn: () => aiSettingsApi.testKey(apiKey || undefined, form.model), onSuccess: (r) => (r.ok ? toast.success(r.message) : toast.error(r.message)), onError: (e: any) => toast.error(errText(e, "Test failed")) });
  if (isLoading || !data) return <div className="grid place-items-center py-24"><Spinner className="h-8 w-8" /></div>;
  const st = data.status; const f = form.features || data.features;
  const setF = (k: string, v: any) => setForm((x) => ({ ...x, [k]: v }));
  const models = Array.from(new Set([...(st.models || []), data.model]));

  return (
    <div>
      <PageHeader title="AI settings" icon={Bot} subtitle="Your own Claude key, the model, what the assistant may do, and monthly caps."
        actions={<div className="flex gap-2"><Link to="/ai/tokens" className="btn-secondary text-xs">AI access tokens</Link><Link to="/ai/usage" className="btn-secondary text-xs">AI usage</Link></div>} />

      <div className={`mb-4 flex items-start gap-3 rounded-xl p-3.5 border ${st.configured ? "bg-emerald-50 border-emerald-200 text-emerald-800" : "bg-amber-50 border-amber-200 text-amber-800"}`}>
        <Info className="w-5 h-5 shrink-0 mt-0.5" />
        <div className="text-sm"><b>{st.configured ? `Live — ${st.model} using the ${st.key_source} key.` : "AI not configured."}</b> {st.message}
          {!st.sdk && <div>The anthropic SDK is not installed on the server — ask the operator to rebuild the ai-platform image.</div>}
          {!st.configured && data.platform_key === false && <div>No platform key either (ANTHROPIC_API_KEY). Paste your company's key below.</div>}</div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <section className="card space-y-3">
          <h2 className="font-display font-semibold text-gray-800 inline-flex items-center gap-2"><KeyRound className="w-4 h-4" /> Claude API key (bring your own)</h2>
          <p className="text-xs text-gray-500">Stored encrypted (AES-256-GCM, LEXORA_DATA_KEY). Never shown again. {data.has_org_key ? <>A key ending in <b>…{data.key_last4}</b> is set.</> : "No company key — the platform key is used if the operator set one."}</p>
          <div className="flex flex-col sm:flex-row gap-2">
            <input className="input font-mono text-xs flex-1" type="password" placeholder="sk-ant-…" value={apiKey} onChange={(e) => setApiKey(e.target.value)} autoComplete="off" />
            <button className="btn-secondary text-xs inline-flex items-center gap-1" disabled={test.isPending} onClick={() => test.mutate()}><FlaskConical className="w-3.5 h-3.5" /> Test {apiKey ? "this key" : "saved key"}</button>
          </div>
          <div className="flex gap-2">
            <button className="btn-primary text-xs inline-flex items-center gap-1" disabled={!apiKey || save.isPending} onClick={() => save.mutate({ api_key: apiKey })}><Save className="w-3.5 h-3.5" /> Save key</button>
            {data.has_org_key && <button className="btn-secondary text-xs inline-flex items-center gap-1 text-red-700" onClick={() => { if (confirm("Remove the company key? The platform key (if any) is used instead.")) save.mutate({ clear_key: true }); }}><Trash2 className="w-3.5 h-3.5" /> Remove key</button>}
          </div>
        </section>

        <section className="card space-y-3">
          <h2 className="font-display font-semibold text-gray-800">Model and features</h2>
          <div className="grid grid-cols-2 gap-3">
            <label className="text-xs text-gray-600">Model<select className="input mt-1" value={form.model || ""} onChange={(e) => setF("model", e.target.value)}>{models.map((m) => <option key={m} value={m}>{m}</option>)}</select></label>
            <label className="text-xs text-gray-600">Effort (thinking depth)<select className="input mt-1" value={form.effort || "medium"} onChange={(e) => setF("effort", e.target.value)}><option value="low">low — fastest, cheapest</option><option value="medium">medium</option><option value="high">high — deepest</option></select></label>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 text-sm">
            {([["qa", "Q&A with citations"], ["actions", "Actions (leave, tickets, exit…)"], ["mcp", "MCP server (external AI clients)"]] as const).map(([k, label]) => (
              <label key={k} className="flex items-center gap-2 border border-surface-border rounded-lg px-3 py-2"><input type="checkbox" checked={!!f[k]} onChange={(e) => setF("features", { ...f, [k]: e.target.checked })} /> {label}</label>))}
          </div>
          <p className="text-[11px] text-gray-500">Identifiers and money are replaced by placeholders before any model call. Actions always need an explicit confirmation on screen (twice for high-risk ones).</p>
        </section>

        <section className="card space-y-3">
          <h2 className="font-display font-semibold text-gray-800">Monthly cap</h2>
          <div className="grid grid-cols-2 gap-3">
            <label className="text-xs text-gray-600">Tokens per month (0 = no cap)<input className="input mt-1" type="number" min={0} value={form.monthly_cap_tokens ?? 0} onChange={(e) => setF("monthly_cap_tokens", Number(e.target.value))} /></label>
            <label className="text-xs text-gray-600">$ per month (0 = no cap)<input className="input mt-1" type="number" min={0} step="0.01" value={form.monthly_cap_cad ?? 0} onChange={(e) => setF("monthly_cap_cad", Number(e.target.value))} /></label>
            <label className="text-xs text-gray-600">When the cap is reached<select className="input mt-1" value={form.cap_mode || "soft"} onChange={(e) => setF("cap_mode", e.target.value)}><option value="soft">soft — warn, keep answering</option><option value="hard">hard — rules-based answers only</option></select></label>
            <label className="text-xs text-gray-600">USD → CAD rate (for C$ estimates)<input className="input mt-1" type="number" min={0} step="0.01" placeholder="not set" value={form.usd_cad_rate ?? ""} onChange={(e) => setF("usd_cad_rate", e.target.value === "" ? null : Number(e.target.value))} /></label>
          </div>
          <p className="text-[11px] text-gray-500">This month: {data.cap.used_tokens.toLocaleString()} tokens{data.cap.pct !== null ? ` (${data.cap.pct}% of cap)` : ""}. A $ cap only works once a rate is set — there is no built-in exchange rate.</p>
        </section>

        <section className="card space-y-3">
          <h2 className="font-display font-semibold text-gray-800">Price table (USD per 1M tokens)</h2>
          <p className="text-[11px] text-gray-500">{st.price_source} Leave a row blank to use the default; a model with no price shows cost as "set on the AI settings page".</p>
          <table className="w-full text-xs">
            <thead><tr className="text-left text-gray-500"><th className="py-1">Model</th><th>Default in / out</th><th>Override in</th><th>Override out</th></tr></thead>
            <tbody>{models.map((m) => { const d = st.default_prices_usd[m]; const o = (form.prices || {})[m]; return (
              <tr key={m} className="border-t border-surface-border"><td className="py-1 font-mono">{m}</td><td className="text-gray-500">{d ? `${d.input_usd_per_mtok} / ${d.output_usd_per_mtok}` : "—"}</td>
                <td><input className="input !py-1 w-24" type="number" step="0.01" value={o?.input_usd_per_mtok ?? ""} onChange={(e) => setF("prices", { ...(form.prices || {}), [m]: { ...(o || { output_usd_per_mtok: "" }), input_usd_per_mtok: e.target.value } })} /></td>
                <td><input className="input !py-1 w-24" type="number" step="0.01" value={o?.output_usd_per_mtok ?? ""} onChange={(e) => setF("prices", { ...(form.prices || {}), [m]: { ...(o || { input_usd_per_mtok: "" }), output_usd_per_mtok: e.target.value } })} /></td></tr>); })}</tbody>
          </table>
        </section>
      </div>

      <div className="mt-4 flex items-center gap-3">
        <button className="btn-primary inline-flex items-center gap-1" disabled={save.isPending} onClick={() => save.mutate({ ...form, prices: form.prices })}><Save className="w-4 h-4" /> Save settings</button>
        {data.updated_at && <span className="text-xs text-gray-500">Last saved by {data.updated_by} · {new Date(data.updated_at).toLocaleString()}</span>}
      </div>
    </div>
  );
}
