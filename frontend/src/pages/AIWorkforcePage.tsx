import { useQuery } from "@tanstack/react-query";
import { Bot, Zap } from "lucide-react";
import { PageHeader, Spinner, StatTile } from "../components/ui";
import { aiApi } from "../api/endpoints";

export default function AIWorkforcePage() {
  const { data, isLoading } = useQuery({ queryKey: ["ai-agents"], queryFn: aiApi.agents });
  if (isLoading || !data) return <div className="grid place-items-center py-24"><Spinner className="h-8 w-8" /></div>;

  const attention = data.filter((a) => a.status === "attention").length;
  const totalFindings = data.reduce((s, a) => s + a.findings, 0);

  return (
    <div>
      <PageHeader title="AI Workforce" icon={Bot}
        subtitle="A lean, task-specific watchdog fleet — each agent proposes, a human approves" />

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-4">
        <StatTile label="Agents" value={data.length} icon={Bot} accent sub="task-specific watchdogs" />
        <StatTile label="Online" value={data.length - attention} sub="clean, nothing to flag" />
        <StatTile label="Need attention" value={attention} sub="have findings to review" />
        <StatTile label="Open findings" value={totalFindings} icon={Zap} sub="across the fleet" />
      </div>

      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {data.map((a) => {
          const attn = a.status === "attention";
          return (
            <div key={a.key} className="card hover:shadow-card-hover transition-shadow">
              <div className="flex items-start justify-between gap-2 mb-2">
                <div className="flex items-center gap-2.5 min-w-0">
                  <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-primary-500 to-teal-500 flex items-center justify-center flex-shrink-0">
                    <Bot className="w-4.5 h-4.5 text-white" />
                  </div>
                  <div className="min-w-0">
                    <div className="font-semibold text-gray-800 text-sm truncate">{a.name}</div>
                    <span className="badge-purple mt-0.5">{a.domain}</span>
                  </div>
                </div>
                <span className={`inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-full shrink-0 ${
                  attn ? "bg-amber-100 text-amber-700" : "bg-green-100 text-green-700"}`}>
                  <span className={`w-1.5 h-1.5 rounded-full ${attn ? "bg-amber-500 animate-pulse" : "bg-green-500"}`} />
                  {attn ? `${a.findings}` : "online"}
                </span>
              </div>
              <p className="text-xs text-gray-500 leading-relaxed">{a.summary}</p>
              <div className="flex items-center justify-between mt-3 pt-3 border-t border-surface-border text-[11px] text-gray-400">
                <span className="capitalize">Runs: {a.cadence}</span>
                <span>{a.last_run ? `last ${rel(a.last_run)}` : "on demand"}</span>
              </div>
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
