import { useQuery } from "@tanstack/react-query";
import { Activity, CheckCircle2, AlertTriangle } from "lucide-react";
import { PageHeader, Spinner, Empty } from "../components/ui";
import { aiApi } from "../api/endpoints";

export default function AIActivityPage() {
  const { data, isLoading } = useQuery({ queryKey: ["ai-activity"], queryFn: () => aiApi.activity(60) });
  if (isLoading || !data) return <div className="grid place-items-center py-24"><Spinner className="h-8 w-8" /></div>;

  return (
    <div>
      <PageHeader title="AI Activity" icon={Activity}
        subtitle="Every agent run, newest first — an audit trail of what the fleet checked" />

      <div className="card !p-0">
        <div className="flex items-center justify-between px-5 py-3 border-b border-surface-border">
          <h3 className="font-display font-semibold text-gray-800">Run log</h3>
          <span className="text-xs text-gray-400">{data.total} runs</span>
        </div>
        {data.items.length === 0 ? <Empty>No agent runs yet.</Empty> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-surface text-gray-400 text-left text-[11px] uppercase tracking-wide">
                <tr>
                  <th className="px-5 py-2.5 font-semibold">Agent</th>
                  <th className="px-5 py-2.5 font-semibold">Domain</th>
                  <th className="px-5 py-2.5 font-semibold">Result</th>
                  <th className="px-5 py-2.5 font-semibold">Summary</th>
                  <th className="px-5 py-2.5 font-semibold text-right">When</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((r) => {
                  const attn = r.status === "attention";
                  return (
                    <tr key={r.id} className="border-t border-surface-border hover:bg-gray-50">
                      <td className="px-5 py-3 font-medium text-gray-800 whitespace-nowrap">{r.agent}</td>
                      <td className="px-5 py-3"><span className="badge-purple">{r.domain}</span></td>
                      <td className="px-5 py-3">
                        <span className={`inline-flex items-center gap-1 text-[11px] font-semibold ${
                          attn ? "text-amber-600" : "text-green-600"}`}>
                          {attn ? <AlertTriangle className="w-3.5 h-3.5" /> : <CheckCircle2 className="w-3.5 h-3.5" />}
                          {attn ? `${r.findings} flag${r.findings === 1 ? "" : "s"}` : "clean"}
                        </span>
                      </td>
                      <td className="px-5 py-3 text-gray-600">{r.summary}</td>
                      <td className="px-5 py-3 text-right text-gray-400 whitespace-nowrap">{rel(r.ran_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
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
