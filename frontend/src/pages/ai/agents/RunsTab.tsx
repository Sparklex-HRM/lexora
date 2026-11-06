import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ShieldCheck, ShieldX } from "lucide-react";
import DataGrid, { WideModal } from "../../../components/DataGrid";
import { Spinner, StatTile } from "../../../components/ui";
import { when } from "../../../api/platform";
import { agenticApi, cad, type AgentCard, type AgentRun, type Proposal } from "../../../api/agentic";
import { Diff, KV, ModeBadge, Pill, TierBadge } from "./shared";

/** Shadow reports: per run, "what I would have done" — plus the live run log and the action-log chain check. */
export default function RunsTab({ agents, agent, setAgent }: { agents: AgentCard[]; agent: string; setAgent: (a: string) => void }) {
  const [mode, setMode] = useState("");
  const [open, setOpen] = useState<AgentRun | null>(null);
  const q = useQuery({ queryKey: ["agentic", "runs", agent, mode], queryFn: () => agenticApi.runs({ agent, mode }) });
  const verify = useQuery({ queryKey: ["agentic", "verify"], queryFn: agenticApi.verify });
  return (
    <div>
      <div className={`card mb-4 flex items-center gap-3 text-sm ${verify.data ? (verify.data.ok ? "border-green-200" : "border-red-300 bg-red-50/40") : ""}`}>
        {verify.data?.ok ? <ShieldCheck className="w-5 h-5 text-emerald-600 shrink-0" /> : <ShieldX className="w-5 h-5 text-red-600 shrink-0" />}
        <div className="min-w-0">
          <b>Action log chain:</b> {verify.isLoading ? "checking…" : verify.data?.ok ? `intact — ${verify.data.checked} event(s) verified, head ${verify.data.head_hash.slice(0, 12)}…` : `BROKEN at event ${verify.data?.first_bad_seq}: ${verify.data?.reason}`}
          <div className="text-[11px] text-gray-500">Every tool call and decision is one append-only, hash-chained row (kept at least one year). The database refuses updates and deletes; this check recomputes every hash.</div>
        </div>
        <button className="btn-secondary !py-1 text-xs ml-auto" onClick={() => verify.refetch()}>Verify now</button>
      </div>
      <DataGrid<AgentRun> rows={q.data?.items || []} rowKey={(r) => r.id} minWidth={1000} exportName="agent-runs" density="compact" onRowClick={setOpen}
        empty={q.isLoading ? "Loading…" : "No runs yet. The agents run on their triggers (attendance lock, joiners, exits, tickets) once the add-on is on."}
        filters={<>
          <select className="input !py-1.5 text-sm w-44" value={agent} onChange={(e) => setAgent(e.target.value)} aria-label="Agent"><option value="">All agents</option>{agents.map((a) => <option key={a.key} value={a.key}>{a.role_name}</option>)}</select>
          <select className="input !py-1.5 text-sm w-36" value={mode} onChange={(e) => setMode(e.target.value)} aria-label="Mode"><option value="">Shadow + live</option><option value="shadow">Shadow only</option><option value="live">Live only</option></select>
        </>}
        columns={[
          { key: "started_at", label: "Ran", render: (r) => when(r.started_at), text: (r) => r.started_at || "" },
          { key: "agent_role", label: "Agent", width: "130px" }, { key: "trigger", label: "Trigger", render: (r) => r.trigger.replace(/_/g, " ") },
          { key: "mode", label: "Mode", width: "90px", render: (r) => <ModeBadge mode={r.mode} />, text: (r) => r.mode },
          { key: "status", label: "Status", width: "90px", render: (r) => <Pill v={r.status} />, text: (r) => r.status },
          { key: "headline", label: "Headline", render: (r) => <span className="whitespace-normal">{r.error || r.summary?.headline || "—"}</span>, text: (r) => r.error || r.summary?.headline || "" },
          { key: "checks_run", label: "Checks", type: "number" }, { key: "findings", label: "Findings", type: "number" }, { key: "proposals", label: "Proposals", type: "number" }, { key: "tasks_opened", label: "Tasks", type: "number" },
          { key: "duration_ms", label: "Took", render: (r) => r.duration_ms >= 60000 ? `${(r.duration_ms / 60000).toFixed(1)} min` : `${(r.duration_ms / 1000).toFixed(1)} s`, text: (r) => String(r.duration_ms) },
          { key: "cost_usd", label: "Cost $", type: "number", render: (r) => r.cost_usd ? r.cost_usd.toFixed(4) : "0" },
        ]} />
      {open && <ReportModal run={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

function ReportModal({ run, onClose }: { run: AgentRun; onClose: () => void }) {
  const q = useQuery({ queryKey: ["agentic", "run", run.id], queryFn: () => agenticApi.run(run.id) });
  const [sel, setSel] = useState<Proposal | null>(null);
  const r = q.data;
  const s = run.summary || {};
  return (
    <WideModal size="xwide" title={`${run.agent_role} — ${run.mode === "shadow" ? "what I would have done" : "run report"}`} onClose={onClose} printable
      subtitle={<span className="inline-flex items-center gap-2"><ModeBadge mode={run.mode} /><Pill v={run.status} /> {when(run.started_at)} · trigger {run.trigger.replace(/_/g, " ")}</span>}>
      {q.isLoading || !r ? <Spinner /> : (
        <div className="space-y-4">
          {s.headline && <div className="text-lg font-display font-semibold text-gray-800">{s.headline}</div>}
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            <StatTile label="Checks run" value={run.checks_run} /><StatTile label="Findings" value={run.findings} />
            <StatTile label={run.mode === "shadow" ? "Would have proposed" : "Proposals"} value={r.counts.proposals} sub={`T0 ${r.counts.by_tier["0"] || 0} · T1 ${r.counts.by_tier["1"] || 0} · T2 ${r.counts.by_tier["2"] || 0}`} />
            <StatTile label="$ touched" value={cad(r.counts.dollars)} sub={`${r.counts.headcount} employee(s)`} /><StatTile label="Tasks opened" value={r.tasks.length} />
          </div>
          {Array.isArray(s.waiting_on) && s.waiting_on.length > 0 && <div><h4 className="text-xs uppercase tracking-wide text-gray-500 font-semibold mb-1">Waiting on</h4><ul className="text-sm list-disc pl-5">{s.waiting_on.map((w: any, i: number) => <li key={i}>{typeof w === "string" ? w : JSON.stringify(w)}</li>)}</ul></div>}
          <div>
            <h4 className="text-xs uppercase tracking-wide text-gray-500 font-semibold mb-1">{run.mode === "shadow" ? "Actions it would have taken (nothing ran)" : "Actions proposed in this run"}</h4>
            {r.would_have_done.length === 0 ? <p className="text-sm text-gray-500">None — the run read, checked and reported only.</p> : (
              <div className="overflow-x-auto card !p-0"><table className="w-full text-[13px]">
                <thead className="bg-surface text-[11px] uppercase tracking-wide text-gray-500 text-left"><tr><th className="px-3 py-2">Tier</th><th className="px-3 py-2">Action</th><th className="px-3 py-2">Tool</th><th className="px-3 py-2 text-right">$</th><th className="px-3 py-2">Confidence</th><th className="px-3 py-2">Status</th></tr></thead>
                <tbody>{r.would_have_done.map((p) => <tr key={p.id} className="border-t border-surface-border hover:bg-primary-50/30 cursor-pointer" onClick={() => setSel(p)}>
                  <td className="px-3 py-1.5"><TierBadge tier={p.risk_tier} untrusted={p.untrusted_input} /></td><td className="px-3 py-1.5 text-gray-800">{p.plain_action}</td>
                  <td className="px-3 py-1.5 font-mono text-xs text-gray-500">{p.tool_name}</td><td className="px-3 py-1.5 text-right tabular-nums">{p.aggregate_impact?.dollars ? cad(p.aggregate_impact.dollars) : "—"}</td>
                  <td className="px-3 py-1.5">{p.confidence}</td><td className="px-3 py-1.5"><Pill v={p.status} /></td></tr>)}</tbody></table></div>)}
          </div>
          {r.tasks.length > 0 && <div><h4 className="text-xs uppercase tracking-wide text-gray-500 font-semibold mb-1">People it chased</h4>
            <ul className="text-sm space-y-0.5">{r.tasks.map((t) => <li key={t.id} className="flex gap-2 items-center"><Pill v={t.status} /><span>{t.title}</span><span className="text-xs text-gray-400">→ {t.owner_user_id || t.owner_employee_id} · {t.chase_count} reminder(s)</span></li>)}</ul></div>}
          {sel && <div className="card border-primary-200">
            <div className="flex items-center justify-between mb-2"><div className="font-semibold text-gray-800">{sel.plain_action}</div><button className="text-xs text-gray-500" onClick={() => setSel(null)}>close</button></div>
            <p className="text-sm text-gray-700 mb-3">{sel.reason || "—"}</p>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4"><div><div className="text-[11px] uppercase text-gray-500 mb-1">Before → after</div><Diff before={sel.before} after={sel.after} /></div>
              <div><div className="text-[11px] uppercase text-gray-500 mb-1">Tool + arguments</div><div className="card !p-3"><div className="font-mono text-xs text-primary-700 mb-1">{sel.tool_name}()</div><KV obj={sel.tool_args} /></div>
                {sel.evidence_refs.length > 0 && <div className="text-xs mt-2"><b>Evidence:</b> {sel.evidence_refs.map((e, i) => <span key={i} className="mr-2">{e.label || e.link}</span>)}</div>}</div></div>
          </div>}
        </div>)}
    </WideModal>
  );
}
