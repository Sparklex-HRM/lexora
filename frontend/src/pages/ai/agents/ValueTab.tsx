import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { BadgeCheck, Coins, Gauge, Hourglass, Save } from "lucide-react";
import { StatTile, Spinner } from "../../../components/ui";
import { errText, when } from "../../../api/platform";
import { thisMonthISO, monthEndISO } from "../../../lib/datetime";
import { agenticApi, cad, minutesLabel, KIND_LABEL, type AgentSettings } from "../../../api/agentic";

/** Three labelled layers (measured · proxy · avoided) next to agent cost and reviewer minutes; admins edit the minutes table. */
export default function ValueTab({ canEdit }: { canEdit: boolean }) {
  const qc = useQueryClient();
  const [month, setMonth] = useState(thisMonthISO());
  const from = `${month}-01`, to = monthEndISO(`${month}-01`);
  const q = useQuery({ queryKey: ["agentic", "value", month], queryFn: () => agenticApi.value(from, `${to}T23:59:59Z`) });
  const settings = useQuery({ queryKey: ["agentic", "settings"], queryFn: agenticApi.settings, enabled: canEdit });
  const [mt, setMt] = useState<Record<string, number> | null>(null);
  const save = useMutation({ mutationFn: (minutes_table: Record<string, number>) => agenticApi.saveSettings({ minutes_table }),
    onSuccess: () => { toast.success("Minutes table saved — the estimate is re-priced"); setMt(null); qc.invalidateQueries({ queryKey: ["agentic"] }); }, onError: (e) => toast.error(errText(e)) });
  const confirm = useMutation({ mutationFn: (id: string) => agenticApi.confirmValue(id), onSuccess: () => { toast.success("Confirmed — it now counts"); qc.invalidateQueries({ queryKey: ["agentic", "value"] }); }, onError: (e) => toast.error(errText(e)) });
  if (q.isLoading || !q.data) return <div className="grid place-items-center py-16"><Spinner /></div>;
  const v = q.data;
  const table: Record<string, number> = mt || (settings.data as AgentSettings | undefined)?.value.minutes_table || v.proxy.minutes_table;
  const usd = v.cost.cost_usd || 0;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <label className="text-xs text-gray-600">Month <input type="month" className="input !py-1.5 text-sm ml-1" value={month} onChange={(e) => setMonth(e.target.value)} /></label>
        <span className="text-xs text-gray-400">{v.enabled ? `Mode: ${v.mode}` : "Add-on off"} · {from} → {to}</span>
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <div className="card border-sky-200"><div className="flex items-center justify-between"><span className="text-[11px] font-semibold text-sky-700 uppercase">1 · Measured</span><Gauge className="w-4 h-4 text-sky-400" /></div>
          <div className="text-2xl font-bold tabular-nums text-gray-800 mt-1">{v.measured.avg_approval_latency_minutes != null ? `${v.measured.avg_approval_latency_minutes} min` : "—"}</div>
          <div className="text-xs text-gray-500">avg approval latency · median {v.measured.median_approval_latency_minutes ?? "—"} min</div>
          <div className="text-xs text-gray-600 mt-2 space-y-0.5"><div>{v.measured.runs} run(s) ({v.measured.shadow_runs} shadow) · close time {v.measured.avg_run_close_minutes ?? "—"} min</div>
            <div>{v.measured.proposals_decided} decided · {v.measured.accepted} accepted · {v.measured.denied} denied</div>
            <div>accepted without edit <b>{v.measured.accept_without_edit_pct ?? "—"}{v.measured.accept_without_edit_pct != null && "%"}</b> · {v.measured.tasks_closed} task(s) closed</div></div></div>
        <div className="card border-violet-200"><div className="flex items-center justify-between"><span className="text-[11px] font-semibold text-violet-700 uppercase">2 · Proxy (estimate)</span><Hourglass className="w-4 h-4 text-violet-400" /></div>
          <div className="text-2xl font-bold tabular-nums text-gray-800 mt-1">{v.proxy.hours} h</div><div className="text-xs text-gray-500">{v.proxy.note}</div>
          <ul className="text-xs text-gray-600 mt-2 space-y-0.5">{v.proxy.rows.slice(0, 5).map((r) => <li key={r.kind} className="flex justify-between gap-2"><span>{KIND_LABEL[r.kind] || r.kind} × {r.units}</span><span className="tabular-nums">{minutesLabel(r.minutes)}</span></li>)}{!v.proxy.rows.length && <li className="text-gray-400">No outcomes recorded yet.</li>}</ul></div>
        <div className="card border-emerald-200"><div className="flex items-center justify-between"><span className="text-[11px] font-semibold text-emerald-700 uppercase">3 · Avoided (confirmed)</span><BadgeCheck className="w-4 h-4 text-emerald-400" /></div>
          <div className="text-2xl font-bold tabular-nums text-gray-800 mt-1">{cad(v.avoided.dollars)}</div><div className="text-xs text-gray-500">{v.avoided.items} human-confirmed item(s). {v.avoided.unconfirmed_items > 0 && <span className="text-amber-700">{cad(v.avoided.unconfirmed_dollars)} across {v.avoided.unconfirmed_items} item(s) await confirmation and do NOT count.</span>}</div>
          <div className="text-[11px] text-gray-400 mt-2">{v.avoided.note}</div></div>
        <div className="card border-gray-200"><div className="flex items-center justify-between"><span className="text-[11px] font-semibold text-gray-600 uppercase">Cost</span><Coins className="w-4 h-4 text-gray-400" /></div>
          <div className="text-2xl font-bold tabular-nums text-gray-800 mt-1">{v.cost.cost_cad != null ? cad(v.cost.cost_cad) : `$${usd.toFixed(2)}`}</div>
          <div className="text-xs text-gray-500">{(v.cost.input_tokens + v.cost.output_tokens).toLocaleString("en-CA")} tokens ({v.cost.cache_read_tokens.toLocaleString("en-CA")} cache reads) · {v.cost.events} log event(s)</div>
          <div className="text-xs text-gray-600 mt-2">Reviewer time <b>{minutesLabel(v.cost.reviewer_minutes)}</b> ({v.cost.decisions} decision(s) × {v.cost.reviewer_minutes_per_decision} min)</div>
          <div className="text-[11px] text-gray-400 mt-1">{v.cost.note}</div></div>
      </div>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <div className="card !p-0">
          <div className="px-4 py-2.5 border-b border-surface-border font-display font-semibold text-gray-800">By agent</div>
          <table className="w-full text-[13px]"><thead className="bg-surface text-[11px] uppercase tracking-wide text-gray-500 text-left"><tr><th className="px-3 py-2">Agent</th><th className="px-3 py-2 text-right">Runs</th><th className="px-3 py-2 text-right">Decided</th><th className="px-3 py-2 text-right">No-edit</th><th className="px-3 py-2 text-right">Denied</th><th className="px-3 py-2 text-right">Est. time</th><th className="px-3 py-2 text-right">Confirmed $</th><th className="px-3 py-2 text-right">Cost $</th></tr></thead>
            <tbody>{v.per_agent.map((a) => <tr key={a.agent} className="border-t border-surface-border"><td className="px-3 py-1.5 font-medium text-gray-800">{a.agent_role}</td><td className="px-3 py-1.5 text-right tabular-nums">{a.runs}</td><td className="px-3 py-1.5 text-right tabular-nums">{a.proposals_decided}</td><td className="px-3 py-1.5 text-right tabular-nums">{a.accepted_without_edit}</td><td className="px-3 py-1.5 text-right tabular-nums">{a.denied}</td><td className="px-3 py-1.5 text-right tabular-nums">{minutesLabel(a.proxy_minutes)}</td><td className="px-3 py-1.5 text-right tabular-nums">{cad(a.avoided_dollars)}{a.unconfirmed_dollars > 0 && <span className="text-[10px] text-amber-700 block">+{cad(a.unconfirmed_dollars)} unconfirmed</span>}</td><td className="px-3 py-1.5 text-right tabular-nums">{a.cost_usd.toFixed(4)}</td></tr>)}</tbody></table>
        </div>
        <div className="card">
          <div className="flex items-center justify-between mb-2"><div className="font-display font-semibold text-gray-800">Minutes table (proxy layer)</div>
            {canEdit && <button className="btn-primary !py-1 text-xs inline-flex items-center gap-1" disabled={!mt || save.isPending} onClick={() => mt && save.mutate(mt)}><Save className="w-3.5 h-3.5" /> Save</button>}</div>
          <p className="text-[11px] text-gray-500 mb-2">Conservative defaults. Minutes a person would have spent per outcome; change them to your team's reality — the estimate re-prices the month.</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">{Object.entries(table).map(([k, m]) => <label key={k} className="text-xs text-gray-600 flex items-center justify-between gap-2 border border-surface-border rounded-lg px-2 py-1.5"><span>{KIND_LABEL[k] || k.replace(/_/g, " ")}</span>
            <input type="number" min={0} max={600} className="input !py-1 w-20 text-right" value={m} disabled={!canEdit} onChange={(e) => setMt({ ...table, [k]: Number(e.target.value) })} /></label>)}</div>
        </div>
      </div>
      <div className="card !p-0">
        <div className="px-4 py-2.5 border-b border-surface-border font-display font-semibold text-gray-800">Avoided-$ rows this month</div>
        {v.avoided.rows.length === 0 ? <div className="px-4 py-3 text-sm text-gray-500">No $ rows yet. Agents add one when a human confirms an exception or when interest / fees are avoided on the company's own amounts.</div> : (
          <table className="w-full text-[13px]"><thead className="bg-surface text-[11px] uppercase tracking-wide text-gray-500 text-left"><tr><th className="px-3 py-2">When</th><th className="px-3 py-2">Agent</th><th className="px-3 py-2">What</th><th className="px-3 py-2">Basis</th><th className="px-3 py-2 text-right">$</th><th className="px-3 py-2">Counts?</th></tr></thead>
            <tbody>{v.avoided.rows.map((r) => <tr key={r.id} className="border-t border-surface-border"><td className="px-3 py-1.5 text-gray-500 whitespace-nowrap">{when(r.at)}</td><td className="px-3 py-1.5">{r.agent.replace(/_/g, " ")}</td><td className="px-3 py-1.5">{KIND_LABEL[r.kind] || r.kind}</td><td className="px-3 py-1.5 text-gray-600">{r.basis || "—"}</td><td className="px-3 py-1.5 text-right tabular-nums">{cad(r.dollars_avoided)}</td>
              <td className="px-3 py-1.5">{r.confirmed ? <span className="badge-green">confirmed {when(r.confirmed_at)}</span> : <button className="btn-secondary !py-0.5 !px-2 text-xs" disabled={confirm.isPending} onClick={() => confirm.mutate(r.id)}>I confirm this</button>}</td></tr>)}</tbody></table>)}
      </div>
    </div>
  );
}
