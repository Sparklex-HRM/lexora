import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Bot, BookOpenCheck, Mail, MessageSquareText, PenLine, ShieldAlert, Info, Clock3 } from "lucide-react";
import { WideModal } from "../../../components/DataGrid";
import { PageHeader, Spinner, StatTile, Empty } from "../../../components/ui";
import { errText, when } from "../../../api/platform";
import { ModeBadge } from "../agents/shared";
import { deskApi, type DeskWeeklyReport } from "./api";

/** HR Desk — the agent's dashboard (/ai/agents/hr-desk; org admin, HR admin, payroll admin).
 *  Metrics WITH their definitions (self-solved deflection, abstain, hand-off, CSAT, median time), top questions this week,
 *  abstained questions → "write a policy clause", knowledge readiness, value (query_resolved outcomes), weekly report button. */
export default function DeskDashboardPage() {
  const qc = useQueryClient();
  const [days, setDays] = useState(7);
  const [report, setReport] = useState<DeskWeeklyReport | null>(null);
  const d = useQuery({ queryKey: ["desk", "dashboard", days], queryFn: () => deskApi.dashboard(days), refetchInterval: 60000 });
  const send = useMutation({ mutationFn: () => deskApi.weeklyReport(days),
    onSuccess: (r) => { setReport(r); toast.success(r.smtp_configured ? "Weekly report sent to HR (in-app + e-mail)" : "Weekly report sent to HR in-app (e-mail not configured)"); qc.invalidateQueries({ queryKey: ["desk", "dashboard"] }); },
    onError: (e) => toast.error(errText(e)) });
  if (d.isLoading) return <div className="grid place-items-center py-24"><Spinner className="h-8 w-8" /></div>;
  if (d.isError || !d.data) return <div className="card text-sm text-red-600">Could not load the HR Desk dashboard. {errText(d.error)}</div>;
  const x = d.data; const m = x.metrics; const st = x.state;
  const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${Math.round(v * 100)}%`);
  const label = (intent: string) => intent.startsWith("policy:") ? `Policy — ${intent.slice(7).replace(/_/g, " ")}` : intent.startsWith("tools:") ? `Records — ${intent.slice(6).replace(/_/g, " ")}`
    : ({ pay_change: "Why did my pay change", pay: "Pay stub / pay", leave: "Leave", policy: "Policy (general)", sexual_harassment: "Sexual harassment → designated recipient (count only)", harassment: "Harassment (routed)", medical: "Medical (routed)",
      mental_health: "Wellbeing (routed, urgent)", grievance: "Grievance (routed)", disciplinary: "Disciplinary (routed)", whistleblowing: "Whistle-blowing (routed)", legal: "Legal (routed)",
      salary_dispute: "Salary dispute (routed)", other_person_data: "Another person's data (refused)" } as Record<string, string>)[intent] || intent.replace(/_/g, " ");
  return (
    <div>
      <PageHeader title="HR Desk" icon={Bot}
        subtitle={`The agent behind Ask Lexi — answers from approved policy clauses and the person's own records, routes sensitive topics to a person, hands off with the transcript. ${st.enabled ? (st.mode === "live" ? "Live" : "Shadow") : "Add-on off"} · last ${x.days} days.`}
        actions={<div className="flex items-center gap-2 flex-wrap">
          <ModeBadge mode={st.mode} enabled={st.enabled} />
          <select className="input !py-1.5 text-xs" value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Window"><option value={7}>Last 7 days</option><option value={30}>Last 30 days</option><option value={90}>Last 90 days</option></select>
          <Link to="/ai/desk/policies" className="btn-secondary !py-1.5 text-xs inline-flex items-center gap-1.5"><BookOpenCheck className="w-3.5 h-3.5" /> Policy clauses</Link>
          <Link to="/ai/agents" className="btn-secondary !py-1.5 text-xs">All agents</Link>
          <button className="btn-primary !py-1.5 text-xs inline-flex items-center gap-1.5" disabled={!st.enabled || send.isPending} onClick={() => send.mutate()}
            title={st.enabled ? "Generate the weekly top-questions report and send it to HR admins and org admins" : "Part of the Agentic operations add-on"}>
            <Mail className="w-3.5 h-3.5" /> {send.isPending ? "Sending…" : "Weekly report"}</button>
        </div>} />
      {!st.enabled && (
        <div className="card mb-4 border-amber-200 bg-amber-50/60 text-sm text-amber-900">
          <b>{st.addon_purchased ? "Agentic operations are paused." : "The Agentic operations add-on is not active."}</b> The desk still answers every employee from approved clauses and routes sensitive topics to a person
          (that is a safety rule, not a feature). What is off: automatic hand-off tickets, AI narration of pay changes, CSAT prompts, the value ledger and this weekly report.{" "}
          {st.addon_purchased ? <Link to="/ai/agents?tab=settings" className="underline">Switch it back on</Link> : <Link to="/plan-billing" className="underline">Add it on Plan &amp; billing</Link>}.
        </div>)}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3 mb-4">
        <StatTile label="Self-solved (deflection)" value={pct(m.deflection_rate)} accent sub={`${m.self_solved} of ${m.candidate_sessions} candidate sessions`} />
        <StatTile label="Abstain rate" value={pct(m.abstain_rate)} sub={`${m.abstained_turns} of ${m.answered_turns + m.abstained_turns} turns`} />
        <StatTile label="Hand-off rate" value={pct(m.handoff_rate)} sub={`${m.handoffs} tickets · ${m.hard_routed_sessions} routed by rule`} />
        <StatTile label="CSAT" value={pct(m.csat)} sub={`${m.csat_rated} rated · coverage ${pct(m.csat_coverage)}`} />
        <StatTile label="Median time" value={m.median_ms !== null ? `${(m.median_ms / 1000).toFixed(1)} s` : "—"} sub={`${m.turns} turns · ${m.sessions} sessions`} icon={Clock3} />
        <StatTile label="Queries resolved" value={x.value ? `${x.value.query_resolved}` : "—"} sub={x.value ? `${x.value.query_resolved_confirmed} confirmed · ${x.value.handoff_created} hand-offs · ${x.value.ic_referral} harassment referrals` : "value ledger needs the add-on"} />
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="card">
          <h3 className="font-display font-semibold text-gray-800 text-sm mb-2 inline-flex items-center gap-1.5"><MessageSquareText className="w-4 h-4 text-primary-500" /> Top questions</h3>
          {x.top_questions.length ? <ul className="text-sm text-gray-700">{x.top_questions.map((t) => <li key={t.intent} className="flex justify-between border-b border-surface-border py-1"><span>{label(t.intent)}</span><span className="tabular-nums font-medium">{t.count}</span></li>)}</ul>
            : <Empty>No questions in this window. {x.chat_hint}</Empty>}
        </div>
        <div className="card">
          <h3 className="font-display font-semibold text-gray-800 text-sm mb-2 inline-flex items-center gap-1.5"><PenLine className="w-4 h-4 text-amber-500" /> Could not answer from policy</h3>
          <p className="text-[11px] text-gray-500 mb-2">Questions the desk abstained on (identifiers masked). Each is a clause HR can write — approved clauses are the only thing the desk answers from.</p>
          {x.abstained_questions.length ? <ul className="text-sm text-gray-700 space-y-1.5">{x.abstained_questions.map((q) => (
            <li key={q.turn_id} className="border border-surface-border rounded-lg px-2.5 py-1.5">
              <div className="text-gray-800">{q.question}</div>
              <div className="text-[10px] text-gray-400 flex items-center justify-between mt-0.5"><span>{when(q.at)}{q.rating ? ` · rated ${q.rating}` : ""}</span>
                <Link to={`${x.write_clause_link}?q=${encodeURIComponent(q.question)}`} className="text-primary-600 hover:underline inline-flex items-center gap-1"><PenLine className="w-3 h-3" /> Write a policy clause</Link></div>
            </li>))}</ul> : <Empty>Nothing abstained — every policy question found an approved clause.</Empty>}
        </div>
        <div className="card">
          <h3 className="font-display font-semibold text-gray-800 text-sm mb-2 inline-flex items-center gap-1.5"><BookOpenCheck className="w-4 h-4 text-emerald-600" /> Knowledge readiness</h3>
          <div className="grid grid-cols-3 gap-2 text-center mb-2">
            <div className="rounded-lg border border-surface-border p-2"><div className="text-lg font-bold tabular-nums text-gray-800">{x.knowledge.approved}</div><div className="text-[10px] uppercase text-gray-400">approved clauses</div></div>
            <div className="rounded-lg border border-surface-border p-2"><div className="text-lg font-bold tabular-nums text-amber-700">{x.knowledge.draft}</div><div className="text-[10px] uppercase text-gray-400">drafts awaiting SME</div></div>
            <div className="rounded-lg border border-surface-border p-2"><div className="text-lg font-bold tabular-nums text-gray-800">{x.knowledge.avg_ready_approved ?? "—"}</div><div className="text-[10px] uppercase text-gray-400">avg readiness /100</div></div>
          </div>
          <p className="text-xs text-gray-600">{x.knowledge.policies_approved} of {x.knowledge.policies} policies have an approved clause.{!x.knowledge.ready && " Import a policy and approve its clauses — until then the desk can only answer from the person's own records."}</p>
          <Link to="/ai/desk/policies" className="btn-secondary !py-1.5 text-xs mt-2 inline-flex items-center gap-1.5"><BookOpenCheck className="w-3.5 h-3.5" /> Manage policy clauses</Link>
          <div className="mt-3 text-xs text-gray-500 border-t border-surface-border pt-2 space-y-1">
            <div className="inline-flex items-center gap-1.5"><ShieldAlert className="w-3.5 h-3.5 text-slate-500" /> Sexual harassment, harassment, medical, grievance and another person's data never reach the model — fixed message, sealed referral (designated recipients only: <Link to="/ai/desk/ic" className="underline">harassment referrals</Link>).</div>
            {x.last_run && <div>Last session run {when(x.last_run.started_at)} · {x.last_run.status} · {x.last_run.input_tokens + x.last_run.output_tokens} tokens</div>}
            {x.last_report && <div>Last weekly report {when(x.last_report.at)}</div>}
          </div>
        </div>
      </div>
      <details className="card mt-4 text-xs text-gray-600"><summary className="cursor-pointer font-medium text-gray-700 inline-flex items-center gap-1.5"><Info className="w-3.5 h-3.5" /> Definitions — every number above is defined here</summary>
        <ul className="list-disc ml-5 mt-2 space-y-0.5">{Object.entries(x.definitions).map(([k, v]) => <li key={k}><b>{k.replace(/_/g, " ")}</b>: {v}</li>)}</ul></details>
      {report && (
        <WideModal title="HR Desk — weekly report" subtitle={report.note} onClose={() => setReport(null)} size="wide" printable>
          <div className="text-xs text-gray-500 mb-2">Sent to roles {report.roles.join(", ")} · {when(report.at)}</div>
          <pre className="text-xs whitespace-pre-wrap font-mono bg-gray-50 border border-surface-border rounded-lg p-3 max-h-[60vh] overflow-auto">{report.report}</pre>
        </WideModal>)}
    </div>
  );
}
