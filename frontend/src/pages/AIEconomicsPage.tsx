import { useQuery } from "@tanstack/react-query";
import { Coins, Cpu, MessageSquare, Bot, Zap, Info } from "lucide-react";
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";
import { PageHeader, StatTile, Spinner, Empty } from "../components/ui";
import { aiApi } from "../api/endpoints";

const usd = (n: number) => "$" + (Number(n) || 0).toLocaleString("en-US", { minimumFractionDigits: n < 1 ? 4 : 2, maximumFractionDigits: 4 });
const tok = (n: number) => (Number(n) || 0).toLocaleString("en-US");

export default function AIEconomicsPage() {
  const { data, isLoading } = useQuery({ queryKey: ["ai-usage"], queryFn: aiApi.usage });
  if (isLoading || !data) return <div className="grid place-items-center py-24"><Spinner className="h-8 w-8" /></div>;

  const t = data.totals;
  const avg = t.calls ? t.cost_usd / t.calls : 0;

  return (
    <div>
      <PageHeader title="AI Economics" icon={Coins}
        subtitle="Token usage & spend across the AI layer — Lexi copilot and the agent fleet" />

      {!data.key_configured && (
        <div className="mb-4 flex items-start gap-3 rounded-xl p-3.5 bg-amber-50 border border-amber-200">
          <Info className="w-5 h-5 text-amber-600 shrink-0 mt-0.5" />
          <div className="text-sm text-amber-800">
            <b>Grounded mode.</b> No <code className="font-mono text-xs bg-amber-100 px-1 rounded">ANTHROPIC_API_KEY</code> is configured, so Lexi answers from your data with a rule-based responder.
            Costs below are <b>estimated</b> at <b>{data.lexi_model}</b> rates — set the key to enable the live Claude agent and real billing.
          </div>
        </div>
      )}

      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4 mb-4">
        <StatTile label="Total spend" value={usd(t.cost_usd)} icon={Coins} accent sub={data.mode === "live" ? "billed" : "estimated"} />
        <StatTile label="Total tokens" value={tok(t.total_tokens)} icon={Cpu} sub={`${tok(t.input_tokens)} in · ${tok(t.output_tokens)} out`} />
        <StatTile label="Lexi calls" value={t.calls} icon={MessageSquare} />
        <StatTile label="Avg / call" value={usd(avg)} icon={Zap} />
        <StatTile label="Agent runs" value={data.agents.runs} icon={Bot} sub={`${data.agents.count} agents · ${data.agents.engine}`} />
      </div>

      <div className="grid lg:grid-cols-3 gap-4">
        {/* Daily trend */}
        <div className="card lg:col-span-2">
          <h3 className="font-display font-semibold text-gray-800 mb-3">Token usage over time</h3>
          {data.daily.length ? (
            <ResponsiveContainer width="100%" height={230}>
              <AreaChart data={data.daily} margin={{ top: 6, right: 10, left: -10, bottom: 0 }}>
                <defs><linearGradient id="econg" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#4f46e5" stopOpacity={0.35} /><stop offset="100%" stopColor="#4f46e5" stopOpacity={0.02} /></linearGradient></defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#eef0f5" vertical={false} />
                <XAxis dataKey="date" tick={{ fontSize: 11, fill: "#9ca3af" }} axisLine={false} tickLine={false} tickFormatter={(d) => d.slice(5)} />
                <YAxis tick={{ fontSize: 11, fill: "#9ca3af" }} axisLine={false} tickLine={false} />
                <Tooltip contentStyle={{ borderRadius: 10, border: "1px solid #e5e7ef", fontSize: 13 }} formatter={(v: any, k: any) => k === "tokens" ? [tok(v), "tokens"] : v} />
                <Area type="monotone" dataKey="tokens" stroke="#4f46e5" strokeWidth={2.5} fill="url(#econg)" />
              </AreaChart>
            </ResponsiveContainer>
          ) : <Empty>No AI usage yet — chat with Lexi to generate metered activity.</Empty>}
        </div>

        {/* Cost breakdown */}
        <div className="card">
          <h3 className="font-display font-semibold text-gray-800 mb-3">Spend breakdown</h3>
          <div className="text-[10px] font-semibold text-gray-400 uppercase tracking-wide mb-1.5">By feature</div>
          {data.by_feature.length ? data.by_feature.map((f) => (
            <div key={f.feature} className="flex items-center justify-between text-sm py-1">
              <span className="text-gray-700 capitalize">{f.feature.replace("_", " ")} <span className="text-xs text-gray-400">· {f.calls}</span></span>
              <span className="tabular-nums font-medium text-gray-800">{usd(f.cost)}</span>
            </div>
          )) : <div className="text-sm text-gray-400 py-2">No spend yet.</div>}
          <div className="flex items-center justify-between text-sm py-1 border-t border-surface-border mt-1 pt-2">
            <span className="text-gray-700">AI Workforce <span className="text-xs text-gray-400">· {data.agents.runs} runs</span></span>
            <span className="tabular-nums font-medium text-emerald-600">$0.00</span>
          </div>
          <div className="mt-3 rounded-lg p-2.5 bg-gradient-to-r from-primary-50 to-teal-50 text-[11px] text-gray-600">
            The {data.agents.count}-agent fleet runs on <b>SQL</b>, not the LLM — <b>0 tokens, $0</b>. Only Lexi consumes model tokens.
          </div>
        </div>
      </div>

      {/* Pricing + recent */}
      <div className="grid lg:grid-cols-2 gap-4 mt-4">
        <div className="card !p-0">
          <div className="px-5 py-3 border-b border-surface-border"><h3 className="font-display font-semibold text-gray-800">Model pricing ($/1M tokens)</h3></div>
          <table className="w-full text-sm">
            <thead className="bg-surface text-gray-400 text-left text-[11px] uppercase tracking-wide">
              <tr><th className="px-5 py-2 font-semibold">Model</th><th className="px-5 py-2 font-semibold text-right">Input</th><th className="px-5 py-2 font-semibold text-right">Output</th></tr>
            </thead>
            <tbody>
              {Object.entries(data.prices).map(([m, p]) => (
                <tr key={m} className={`border-t border-surface-border ${m === data.lexi_model ? "bg-primary-50/40" : ""}`}>
                  <td className="px-5 py-2 font-mono text-xs text-gray-700">{m}{m === data.lexi_model && <span className="badge-purple ml-2">Lexi</span>}</td>
                  <td className="px-5 py-2 text-right tabular-nums text-gray-700">${p.input.toFixed(2)}</td>
                  <td className="px-5 py-2 text-right tabular-nums text-gray-700">${p.output.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="text-[11px] text-gray-400 px-5 py-2">Switch models via the <code className="font-mono">LEXI_MODEL</code> env var to trade cost for capability.</p>
        </div>

        <div className="card !p-0">
          <div className="px-5 py-3 border-b border-surface-border"><h3 className="font-display font-semibold text-gray-800">Recent AI calls</h3></div>
          {data.recent.length ? (
            <div className="overflow-x-auto max-h-[260px] overflow-y-auto">
              <table className="w-full text-sm">
                <thead className="bg-surface text-gray-400 text-left text-[11px] uppercase tracking-wide sticky top-0">
                  <tr><th className="px-5 py-2 font-semibold">Feature</th><th className="px-5 py-2 font-semibold text-right">Tokens</th><th className="px-5 py-2 font-semibold text-right">Cost</th><th className="px-5 py-2 font-semibold">Mode</th></tr>
                </thead>
                <tbody>
                  {data.recent.map((r, i) => (
                    <tr key={i} className="border-t border-surface-border">
                      <td className="px-5 py-2 text-gray-700 capitalize">{r.feature.replace("_", " ")}</td>
                      <td className="px-5 py-2 text-right tabular-nums text-gray-600">{tok(r.input_tokens + r.output_tokens)}</td>
                      <td className="px-5 py-2 text-right tabular-nums text-gray-800">{usd(r.cost_usd)}</td>
                      <td className="px-5 py-2"><span className={r.mode === "live" ? "badge-green" : "badge-amber"}>{r.mode}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <Empty>No calls yet.</Empty>}
        </div>
      </div>
    </div>
  );
}
