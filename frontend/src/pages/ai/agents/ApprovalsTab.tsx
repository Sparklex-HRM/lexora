import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Link } from "react-router-dom";
import { Check, CheckCheck, Pencil, X, ShieldAlert, Users } from "lucide-react";
import DataGrid, { WideModal } from "../../../components/DataGrid";
import { Spinner } from "../../../components/ui";
import { errText, when } from "../../../api/platform";
import { agenticApi, cad, type AgentCard, type Proposal } from "../../../api/agentic";
import { Diff, KV, Pill, TierBadge } from "./shared";

/** Approvals inbox: one card per proposal — plain action, tool + args, before/after, $ impact, reason,
 *  evidence, tier; approve / approve for session / deny with message / edit & approve. */
export default function ApprovalsTab({ agents, agent, setAgent, canAct, me }: { agents: AgentCard[]; agent: string; setAgent: (a: string) => void; canAct: boolean; me: string }) {
  const qc = useQueryClient();
  const [status, setStatus] = useState("inbox");
  const [open, setOpen] = useState<Proposal | null>(null);
  const q = useQuery({ queryKey: ["agentic", "proposals", status, agent], queryFn: () => agenticApi.proposals({ status, agent }) });
  const refresh = () => { qc.invalidateQueries({ queryKey: ["agentic"] }); setOpen(null); };
  const approve = useMutation({ mutationFn: (p: { id: string; for_session?: boolean; note?: string }) => agenticApi.approve(p.id, p),
    onSuccess: (r) => { toast.success(r.message || (r.status === "executed" ? `Done: ${r.plain_action}` : r.status === "second_approval" ? "First approval recorded — a different approver must confirm." : `Recorded: ${r.status}`)); refresh(); },
    onError: (e) => toast.error(errText(e)) });
  const deny = useMutation({ mutationFn: (p: { id: string; message: string }) => agenticApi.deny(p.id, p.message), onSuccess: () => { toast.success("Denied — the agent is told why"); refresh(); }, onError: (e) => toast.error(errText(e)) });
  const edit = useMutation({ mutationFn: (p: { id: string; args: Record<string, any>; note: string }) => agenticApi.editApprove(p.id, p.args, p.note), onSuccess: (r) => { toast.success(r.status === "executed" ? `Done with your edit: ${r.plain_action}` : `Recorded: ${r.status}`); refresh(); }, onError: (e) => toast.error(errText(e)) });
  const rows = q.data?.items || [];
  return (
    <div>
      <DataGrid<Proposal> rows={rows} rowKey={(r) => r.id} minWidth={1100} exportName="agent-proposals" onRowClick={setOpen} density="compact"
        empty={q.isLoading ? "Loading…" : status === "inbox" ? "Nothing waiting for approval." : "No proposals in this state."}
        filters={<>
          <select className="input !py-1.5 text-sm w-44" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
            <option value="inbox">Waiting for approval</option><option value="second_approval">Needs second approval</option><option value="executed">Executed</option>
            <option value="approved">Approved (no executor yet)</option><option value="denied">Denied</option><option value="shadow">Shadow (would have done)</option>
            <option value="expired">Expired</option><option value="failed">Failed</option></select>
          <select className="input !py-1.5 text-sm w-44" value={agent} onChange={(e) => setAgent(e.target.value)} aria-label="Agent">
            <option value="">All agents</option>{agents.map((a) => <option key={a.key} value={a.key}>{a.role_name}</option>)}</select>
        </>}
        columns={[
          { key: "risk_tier", label: "Tier", width: "150px", render: (r) => <TierBadge tier={r.risk_tier} untrusted={r.untrusted_input} />, text: (r) => String(r.risk_tier) },
          { key: "agent_role", label: "Agent", width: "130px" },
          { key: "plain_action", label: "Action (plain words)", className: "text-gray-800", render: (r) => <div className="whitespace-normal max-w-xl"><div>{r.plain_action}</div><div className="text-[11px] text-gray-400 font-mono">{r.tool_name}</div></div> },
          { key: "impact", label: "$ impact", type: "money", render: (r) => <span className="tabular-nums">{r.aggregate_impact?.dollars ? cad(r.aggregate_impact.dollars) : "—"}{r.aggregate_impact?.headcount ? <span className="text-gray-400"> · {r.aggregate_impact.headcount} ppl</span> : null}</span>, text: (r) => String(r.aggregate_impact?.dollars || "") },
          { key: "confidence", label: "Confidence", width: "90px" },
          { key: "status", label: "Status", width: "120px", render: (r) => <span className="inline-flex items-center gap-1"><Pill v={r.status} />{r.needs_second_approval && r.status === "second_approval" && <Users className="w-3.5 h-3.5 text-orange-600" />}</span>, text: (r) => r.status },
          { key: "approver_email", label: "Approver(s)", render: (r) => <span className="text-xs">{[r.approver_email, r.second_approver_email].filter(Boolean).join(" + ") || "—"}</span>, text: (r) => r.approver_email || "" },
          { key: "created_at", label: "Proposed", render: (r) => when(r.created_at), text: (r) => r.created_at || "" },
          { key: "expires_at", label: "Expires", render: (r) => r.expires_at ? when(r.expires_at) : "—", text: (r) => r.expires_at || "" },
        ]} />
      {open && <ProposalModal p={open} me={me} canAct={canAct} onClose={() => setOpen(null)}
        busy={approve.isPending || deny.isPending || edit.isPending}
        onApprove={(forSession, note) => approve.mutate({ id: open.id, for_session: forSession, note })}
        onDeny={(m) => deny.mutate({ id: open.id, message: m })}
        onEdit={(args, note) => edit.mutate({ id: open.id, args, note })} />}
    </div>
  );
}

function ProposalModal({ p, me, canAct, busy, onClose, onApprove, onDeny, onEdit }: {
  p: Proposal; me: string; canAct: boolean; busy: boolean; onClose: () => void;
  onApprove: (forSession: boolean, note: string) => void; onDeny: (message: string) => void; onEdit: (args: Record<string, any>, note: string) => void;
}) {
  const detail = useQuery({ queryKey: ["agentic", "proposal", p.id], queryFn: () => agenticApi.proposal(p.id) });
  const [mode, setMode] = useState<"view" | "deny" | "edit">("view");
  const [msg, setMsg] = useState("");
  const [note, setNote] = useState("");
  const [argsText, setArgsText] = useState(JSON.stringify(p.tool_args, null, 2));
  const inInbox = ["proposed", "second_approval", "edited"].includes(p.status);
  const iAmFirst = p.approver_user_id && p.approver_user_id === me;
  const parsed = (() => { try { return JSON.parse(argsText); } catch { return null; } })();
  return (
    <WideModal size="xwide" title={p.plain_action} onClose={onClose}
      subtitle={<span className="inline-flex items-center gap-2 flex-wrap"><TierBadge tier={p.risk_tier} untrusted={p.untrusted_input} /><Pill v={p.status} /><span>{p.agent_role} · {p.tool_name} v{p.tool_version} · policy {p.policy_id}</span></span>}
      footer={inInbox && canAct ? (
        <div className="w-full flex flex-col gap-2">
          {p.risk_tier === 2 && <div className="text-xs text-orange-800 bg-orange-50 border border-orange-200 rounded-lg px-3 py-2 inline-flex items-center gap-2"><Users className="w-4 h-4" />
            {p.status === "second_approval" ? (iAmFirst ? "You gave the first approval. A DIFFERENT person must give the second one." : `First approval by ${p.approver_email || p.approver_user_id}. Your approval is the second and final one.`) : "Second approval needed: this action is irreversible or money-class, so two different people must approve before it runs."}</div>}
          {mode === "deny" && <div className="flex gap-2"><input className="input flex-1" autoFocus placeholder="Why not? (the agent and the next reviewer read this)" value={msg} onChange={(e) => setMsg(e.target.value)} />
            <button className="btn-primary !bg-red-600 text-xs" disabled={busy || msg.trim().length < 3} onClick={() => onDeny(msg)}>Deny with message</button><button className="btn-secondary text-xs" onClick={() => setMode("view")}>Back</button></div>}
          {mode === "edit" && <div className="flex flex-col gap-2">
            <textarea className="input font-mono text-xs h-40" value={argsText} onChange={(e) => setArgsText(e.target.value)} />
            <div className="flex gap-2 items-center"><input className="input flex-1" placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
              <button className="btn-primary text-xs" disabled={busy || !parsed} onClick={() => onEdit(parsed, note)}>Approve with my edit</button><button className="btn-secondary text-xs" onClick={() => setMode("view")}>Back</button></div>
            {!parsed && <div className="text-[11px] text-red-600">Not valid JSON yet.</div>}
            <div className="text-[11px] text-gray-500">Values shown as [[ID_n]] / [[MONEY_n]] are tokenised for your screen; keep the token to leave that value unchanged.</div></div>}
          {mode === "view" && <div className="flex flex-wrap gap-2 justify-end">
            <button className="btn-secondary text-xs inline-flex items-center gap-1 text-red-700" disabled={busy} onClick={() => setMode("deny")}><X className="w-3.5 h-3.5" /> Deny with message</button>
            <button className="btn-secondary text-xs inline-flex items-center gap-1" disabled={busy || iAmFirst === true} onClick={() => setMode("edit")}><Pencil className="w-3.5 h-3.5" /> Edit &amp; approve</button>
            {p.risk_tier <= 1 && <button className="btn-secondary text-xs inline-flex items-center gap-1" disabled={busy} title="Approve this one and let the same tool from the same agent run without a card for the session window" onClick={() => onApprove(true, note)}><CheckCheck className="w-3.5 h-3.5" /> Approve for session</button>}
            <button className="btn-primary text-xs inline-flex items-center gap-1" disabled={busy || iAmFirst === true} onClick={() => onApprove(false, note)}><Check className="w-3.5 h-3.5" /> {p.risk_tier === 2 && p.status !== "second_approval" ? "Approve (1 of 2)" : "Approve"}</button>
          </div>}
        </div>) : <div className="text-xs text-gray-500">{p.status === "shadow" ? "Shadow record — the agent would have proposed this; nothing ran and nothing can be approved here." : `This proposal is ${p.status.replace(/_/g, " ")}.`}</div>}>
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <section className="xl:col-span-2 space-y-4">
          <div><h4 className="text-xs uppercase tracking-wide text-gray-500 font-semibold mb-1">Why the agent proposes this</h4><p className="text-sm text-gray-700 whitespace-pre-wrap">{p.reason || "—"}</p></div>
          <div><h4 className="text-xs uppercase tracking-wide text-gray-500 font-semibold mb-1">Before → after</h4><Diff before={p.before} after={p.after} /></div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div><h4 className="text-xs uppercase tracking-wide text-gray-500 font-semibold mb-1">Exact tool + arguments (tokenised)</h4><div className="card !p-3"><div className="font-mono text-xs text-primary-700 mb-1">{p.tool_name}()</div><KV obj={p.tool_args} /></div></div>
            <div><h4 className="text-xs uppercase tracking-wide text-gray-500 font-semibold mb-1">Aggregate impact</h4>
              <div className="card !p-3 text-sm"><div className="text-2xl font-bold tabular-nums text-gray-800">{p.aggregate_impact?.dollars ? cad(p.aggregate_impact.dollars) : "$0"}</div>
                <div className="text-xs text-gray-500">{p.aggregate_impact?.headcount || 0} employee(s) affected · confidence <b>{p.confidence}</b></div>
                {p.tier_reasons?.length ? <ul className="mt-2 text-[11px] text-gray-600 list-disc pl-4">{p.tier_reasons.map((r) => <li key={r}>{r}</li>)}</ul> : null}
                {p.reversal_plan && <div className="mt-2 text-[11px] text-gray-500"><b>Reversal plan:</b> <span className="font-mono">{JSON.stringify(p.reversal_plan)}</span></div>}
                {p.edited_input_diff && <div className="mt-2 text-[11px] text-amber-700"><b>Edited by the approver:</b> <span className="font-mono">{JSON.stringify(p.edited_input_diff)}</span></div>}
                {p.deny_message && <div className="mt-2 text-[11px] text-red-700"><b>Denied:</b> {p.deny_message}</div>}
                {p.result && <div className="mt-2 text-[11px] text-gray-600"><b>Result:</b> <span className="font-mono">{JSON.stringify(p.result)}</span></div>}
              </div></div>
          </div>
        </section>
        <aside className="space-y-4">
          <div><h4 className="text-xs uppercase tracking-wide text-gray-500 font-semibold mb-1">Evidence it relied on</h4>
            {p.evidence_refs.length ? <ul className="text-sm space-y-1">{p.evidence_refs.map((e, i) => <li key={i} className="flex items-start gap-2"><ShieldAlert className="w-3.5 h-3.5 text-gray-400 mt-0.5 shrink-0" />
              <span>{e.link ? <Link to={e.link} className="text-primary-600 hover:underline">{e.label || e.link}</Link> : (e.label || "—")}{e.source && <span className="text-gray-400"> · {e.source}</span>}{e.hash && <span className="block text-[10px] font-mono text-gray-400 break-all">{e.hash}</span>}</span></li>)}</ul> : <p className="text-xs text-gray-400">No evidence references were attached.</p>}</div>
          <div><h4 className="text-xs uppercase tracking-wide text-gray-500 font-semibold mb-1">Timeline (action log)</h4>
            {detail.isLoading ? <Spinner className="h-4 w-4" /> : <ul className="text-xs space-y-1">{(detail.data?.log || []).slice().reverse().map((e) => <li key={e.event_id} className="flex gap-2"><span className="text-gray-400 whitespace-nowrap">{when(e.ts_utc)}</span><span className="text-gray-700">{e.event_type}{e.decision ? ` · ${e.decision}` : ""}{e.approver_user_id ? ` · by ${e.approver_user_id}` : ""}{e.decision_latency_ms != null ? ` · ${Math.round(e.decision_latency_ms / 60000)} min` : ""}</span></li>)}</ul>}
            <div className="text-[10px] text-gray-400 mt-1">Proposed {when(p.created_at)}{p.expires_at && ` · expires ${when(p.expires_at)}`}{p.requested_by_user_id && ` · on behalf of ${p.requested_by_user_id}`}</div></div>
        </aside>
      </div>
    </WideModal>
  );
}
