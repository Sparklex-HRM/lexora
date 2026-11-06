import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Coins, Cpu, MessageSquare, Gauge, Info } from "lucide-react";
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";
import DataGrid, { type GridColumn } from "../../components/DataGrid";
import { PageHeader, StatTile, Spinner, Badge } from "../../components/ui";
import { aiMeteringApi, type UsageBucket } from "../../api/ai";
import { useAuthStore } from "../../store/authStore";

const tok = (n: number | null | undefined) => (Number(n) || 0).toLocaleString("en-CA");
const usd = (n: number | null | undefined) => (n === null || n === undefined ? "—" : "$" + Number(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 }));
const cad = (n: number | null | undefined) => (n === null || n === undefined ? "set rate on AI settings" : Number(n).toLocaleString("en-CA", { style: "currency", currency: "CAD", minimumFractionDigits: 2, maximumFractionDigits: 2 }));

/** AI usage this month: tokens, calls, estimated cost per user / feature / model / channel; cap status. */
export default function AiUsagePage() {
  const { data, isLoading } = useQuery({ queryKey: ["ai-metering"], queryFn: aiMeteringApi.get });
  const role = useAuthStore((s) => s.user?.role || "");
  if (isLoading || !data) return <div className="grid place-items-center py-24"><Spinner className="h-8 w-8" /></div>;
  const org = data.org || data.me; const cap = data.cap;
  const bucketCols = (label: string, key: string): GridColumn<UsageBucket>[] => [
    { key, label, sortable: true }, { key: "calls", label: "Calls", type: "number", sortable: true },
    { key: "input_tokens", label: "Input tokens", type: "number", sortable: true }, { key: "output_tokens", label: "Output tokens", type: "number", sortable: true },
    { key: "tokens", label: "Tokens", type: "number", sortable: true },
    { key: "cost_usd", label: "Est. USD", render: (r) => <span className="tabular-nums">{usd(r.cost_usd)}</span>, text: (r) => String(r.cost_usd ?? "") },
    { key: "cost_cad", label: "Est. $", render: (r) => <span className="tabular-nums text-xs">{cad(r.cost_cad)}</span>, text: (r) => String(r.cost_cad ?? "") },
  ];
  return (
    <div>
      <PageHeader title="AI usage" icon={Coins} subtitle={`This month since ${new Date(org.since).toLocaleDateString()} · model ${data.model} · ${data.scope === "org" ? "whole company" : "your own usage"}`}
        actions={["org_admin", "superadmin"].includes(role) ? <Link to="/ai/settings" className="btn-secondary text-xs">AI settings</Link> : undefined} />
      {cap.cap_tokens > 0 || cap.cap_cad > 0 ? (
        <div className={`mb-4 flex items-start gap-3 rounded-xl p-3.5 border ${cap.over ? "bg-red-50 border-red-200 text-red-800" : cap.warn ? "bg-amber-50 border-amber-200 text-amber-800" : "bg-gray-50 border-surface-border text-gray-700"}`}>
          <Gauge className="w-5 h-5 shrink-0 mt-0.5" /><div className="text-sm"><b>Monthly cap ({cap.mode}):</b> {tok(cap.used_tokens)} of {tok(cap.cap_tokens)} tokens{cap.pct !== null ? ` (${cap.pct}%)` : ""}
            {cap.cap_cad > 0 && <> · $ cap {cap.cap_cad}{cap.cad_cap_enforceable ? "" : " (not enforceable until a USD→CAD rate is set)"}</>}{cap.blocked ? " — model calls are blocked; rules-based answers only." : cap.over ? " — over the soft cap." : ""}</div></div>
      ) : null}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <StatTile label="Calls" value={tok(org.calls)} icon={MessageSquare} sub={data.scope === "org" ? `you: ${tok(data.me.calls)}` : undefined} />
        <StatTile label="Tokens" value={tok(org.tokens)} icon={Cpu} sub={`${tok(org.input_tokens)} in · ${tok(org.output_tokens)} out`} />
        <StatTile label="Est. cost (USD)" value={usd(org.cost_usd)} icon={Coins} accent />
        <StatTile label="Est. cost ($)" value={cad(org.cost_cad)} icon={Coins} sub={data.usd_cad_rate ? `at ${data.usd_cad_rate} C$ per US$` : "no rate configured"} />
      </div>
      <p className="text-[11px] text-gray-500 mb-4 inline-flex items-start gap-1"><Info className="w-3.5 h-3.5 mt-0.5 shrink-0" /> {data.price_source} Costs are estimates from token counts, not the provider's invoice.</p>
      {data.scope === "org" && (
        <>
          {!!data.daily?.length && <div className="card mb-4"><h2 className="font-display font-semibold text-gray-800 mb-2">Tokens per day</h2>
            <div className="h-48"><ResponsiveContainer><AreaChart data={data.daily}><CartesianGrid strokeDasharray="3 3" stroke="#eee" /><XAxis dataKey="date" fontSize={10} /><YAxis fontSize={10} /><Tooltip />
              <Area type="monotone" dataKey="tokens" stroke="#4f46e5" fill="#c7d2fe" /></AreaChart></ResponsiveContainer></div></div>}
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
            <div><h2 className="font-display font-semibold text-gray-800 mb-2">By user</h2><DataGrid<UsageBucket> columns={bucketCols("User", "user")} rows={data.by_user || []} rowKey={(r) => r.user} exportName="ai-usage-by-user" empty="No usage this month." minWidth={700} /></div>
            <div><h2 className="font-display font-semibold text-gray-800 mb-2">By feature</h2><DataGrid<UsageBucket> columns={bucketCols("Feature", "feature")} rows={data.by_feature || []} rowKey={(r) => r.feature} exportName="ai-usage-by-feature" empty="No usage this month." minWidth={700} /></div>
            <div><h2 className="font-display font-semibold text-gray-800 mb-2">By model</h2><DataGrid<UsageBucket> columns={bucketCols("Model", "model")} rows={data.by_model || []} rowKey={(r) => r.model} exportName="ai-usage-by-model" empty="No usage this month." minWidth={700} /></div>
            <div><h2 className="font-display font-semibold text-gray-800 mb-2">By channel</h2><DataGrid<UsageBucket> columns={bucketCols("Channel", "channel")} rows={data.by_channel || []} rowKey={(r) => r.channel} exportName="ai-usage-by-channel" empty="No usage this month." minWidth={700} /></div>
          </div>
          <h2 className="font-display font-semibold text-gray-800 mt-4 mb-2">Recent calls</h2>
          <DataGrid columns={[
            { key: "at", label: "When", type: "date", render: (r: any) => <span className="text-xs">{r.at ? new Date(r.at).toLocaleString() : ""}</span>, text: (r: any) => r.at || "" },
            { key: "user", label: "User" }, { key: "channel", label: "Channel", render: (r: any) => <Badge>{r.channel}</Badge>, text: (r: any) => r.channel },
            { key: "feature", label: "Feature" }, { key: "model", label: "Model", className: "font-mono text-xs" }, { key: "key_source", label: "Key" },
            { key: "input_tokens", label: "In", type: "number" }, { key: "output_tokens", label: "Out", type: "number" },
            { key: "cost_usd", label: "USD", render: (r: any) => <span>{usd(r.cost_usd)}</span>, text: (r: any) => String(r.cost_usd ?? "") },
            { key: "cost_cad", label: "$", render: (r: any) => <span className="text-xs">{cad(r.cost_cad)}</span>, text: (r: any) => String(r.cost_cad ?? "") },
          ]} rows={data.recent || []} rowKey={(r: any) => `${r.at}-${r.user}-${r.feature}`} exportName="ai-usage-recent" empty="No calls yet." minWidth={1000} />
        </>
      )}
    </div>
  );
}
