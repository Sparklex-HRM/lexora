import { useQuery } from "@tanstack/react-query";
import { Gauge, Bot, Activity, CheckCircle2, AlertTriangle } from "lucide-react";
import { PageHeader, Spinner, StatTile } from "../components/ui";
import { aiApi } from "../api/endpoints";

export default function AICommandCenterPage() {
  const { data, isLoading } = useQuery({ queryKey: ["ai-command"], queryFn: aiApi.commandCenter });
  if (isLoading || !data) return <div className="grid place-items-center py-24"><Spinner className="h-8 w-8" /></div>;

  const clear = data.attention_score === 0;
  return (
    <div>
      <PageHeader title="AI Command Center" icon={Gauge}
        subtitle="One AI briefing across your HR domains — what needs your attention now" />

      {/* Hero briefing */}
      <div className="card mb-4 bg-gradient-to-r from-primary-50 to-teal-50 border-primary-100">
        <div className="flex items-center gap-5">
          <div className={`w-20 h-20 rounded-2xl flex flex-col items-center justify-center text-white flex-shrink-0 ${
            clear ? "bg-gradient-to-br from-emerald-500 to-teal-500" : "bg-gradient-to-br from-amber-500 to-orange-500"}`}>
            <span className="text-2xl font-bold leading-none">{data.attention_score}</span>
            <span className="text-[9px] uppercase tracking-wide mt-0.5">score</span>
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              {clear ? <CheckCircle2 className="w-5 h-5 text-emerald-600" /> : <AlertTriangle className="w-5 h-5 text-amber-600" />}
              <h2 className="font-display text-lg font-bold text-gray-800">{data.headline}</h2>
            </div>
            <p className="text-sm text-gray-500 mt-1">
              Generated {rel(data.generated_at)} · {data.agents_online} agents online · {data.runs_today} runs today
            </p>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-4">
        <StatTile label="Agents online" value={data.agents_online} icon={Bot} accent />
        <StatTile label="Runs today" value={data.runs_today} icon={Activity} />
        <StatTile label="Attention areas" value={data.layers.filter((l) => l.status === "attention").length} icon={AlertTriangle} />
        <StatTile label="All-clear areas" value={data.layers.filter((l) => l.status === "ok").length} icon={CheckCircle2} />
      </div>

      {/* Domain layers */}
      <h3 className="font-display font-semibold text-gray-800 mb-3">Domain layers</h3>
      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {data.layers.map((l) => {
          const attn = l.status === "attention";
          return (
            <div key={l.domain} className="card">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">{l.domain}</span>
                <span className={`inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-full ${
                  attn ? "bg-amber-100 text-amber-700" : "bg-green-100 text-green-700"}`}>
                  <span className={`w-1.5 h-1.5 rounded-full ${attn ? "bg-amber-500" : "bg-green-500"}`} />
                  {attn ? `${l.count} to review` : "All clear"}
                </span>
              </div>
              <p className="text-sm font-medium text-gray-800">{l.headline}</p>
              <p className="text-xs text-gray-500 mt-1">{l.detail}</p>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function rel(iso: string): string {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
