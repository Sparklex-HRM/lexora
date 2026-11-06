import { Link } from "react-router-dom";
import { Bot, ClipboardList, Inbox, Clock3 } from "lucide-react";
import { when } from "../../../api/platform";
import { cad, minutesLabel, type Overview } from "../../../api/agentic";
import { ModeBadge, Pill } from "./shared";

/** Agent cards (six since 2026-10-26): role name, mode badge, last run, open tasks, value this month. */
export default function OverviewTab({ o, onTab }: { o: Overview; onTab: (t: string, agent?: string) => void }) {
  const st = o.state;
  return (
    <div>
      {!st.enabled && (
        <div className="card mb-4 border-amber-200 bg-amber-50/60 text-sm text-amber-900">
          <b>{st.addon_purchased ? "Agentic operations are paused by an admin." : "The Agentic operations add-on is not active for this company."}</b>{" "}
          {st.addon_purchased ? "Switch them back on under Settings." : <>Add it on <Link to="/plan-billing" className="underline">Plan &amp; billing</Link> ($ per employee per month, any plan) — the agents start in shadow mode and show what they would have done before anything runs.</>}
        </div>
      )}
      {st.enabled && st.mode === "shadow" && (
        <div className="card mb-4 border-violet-200 bg-violet-50/60 text-sm text-violet-900">
          <b>Shadow mode.</b> The agents prepare, check and chase, and record what they <i>would</i> have done — nothing executes. {st.live_gate_note}{" "}
          Shadow reports are under <button className="underline" onClick={() => onTab("runs")}>Shadow reports</button>; the switch is under Settings.
        </div>
      )}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {o.agents.map((a) => (
          <div key={a.key} className="card flex flex-col gap-3">
            <div className="flex items-start gap-3">
              <div className="w-10 h-10 rounded-xl bg-primary-50 text-primary-600 grid place-items-center shrink-0"><Bot className="w-5 h-5" /></div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 flex-wrap"><span className="font-display font-bold text-gray-800">{a.role_name}</span><ModeBadge mode={a.mode} enabled={st.enabled} /></div>
                <div className="text-[11px] text-gray-400">v{a.version} · {a.executors_registered.length} executor(s) registered · {Object.keys(a.tier_map).length} tools in the tier map</div>
              </div>
            </div>
            <p className="text-xs text-gray-600 leading-relaxed">{a.does}</p>
            <div className="grid grid-cols-3 gap-2 text-center">
              <button className="rounded-lg border border-surface-border px-2 py-2 hover:border-primary-300" onClick={() => onTab("approvals", a.key)}>
                <div className="text-lg font-bold tabular-nums text-gray-800">{a.inbox}</div><div className="text-[10px] uppercase text-gray-400 inline-flex items-center gap-1"><Inbox className="w-3 h-3" />to approve</div></button>
              <button className="rounded-lg border border-surface-border px-2 py-2 hover:border-primary-300" onClick={() => onTab("tasks", a.key)}>
                <div className="text-lg font-bold tabular-nums text-gray-800">{a.open_tasks}</div><div className="text-[10px] uppercase text-gray-400 inline-flex items-center gap-1"><ClipboardList className="w-3 h-3" />open tasks</div></button>
              <button className="rounded-lg border border-surface-border px-2 py-2 hover:border-primary-300" onClick={() => onTab("value")}>
                <div className="text-lg font-bold tabular-nums text-gray-800">{cad(a.value_this_month.avoided_dollars)}</div><div className="text-[10px] uppercase text-gray-400">confirmed $ · {minutesLabel(a.value_this_month.proxy_minutes)} est.</div></button>
            </div>
            <div className="text-xs text-gray-500 flex items-center gap-2 flex-wrap">
              <Clock3 className="w-3.5 h-3.5" />
              {a.last_run ? <>Last run {when(a.last_run.started_at)} · <Pill v={a.last_run.status} /> · {a.last_run.checks_run} checks · {a.last_run.findings} finding(s) · {a.last_run.proposals} proposal(s)</> : <>Has not run yet — the agent runs on its triggers ({a.triggers.join(", ")}).</>}
              {a.last_run && <button className="text-primary-600 hover:underline" onClick={() => onTab("runs", a.key)}>report</button>}
              {a.key === "payroll_auditor" && <Link to="/ai/agents/payroll-auditor" className="ml-auto text-primary-600 font-medium hover:underline">Open the review pack →</Link>}
              {a.key === "hr_desk" && <Link to="/ai/agents/hr-desk" className="ml-auto text-primary-600 font-medium hover:underline">Open HR Desk dashboard →</Link>}
              {a.key === "onboarding" && <Link to="/ai/agents/onboarding" className="ml-auto text-primary-600 font-medium hover:underline">Open the joiner checklist →</Link>}
              {a.key === "exit" && <Link to="/ai/agents/exit" className="ml-auto text-primary-600 font-medium hover:underline">Open the exit cases →</Link>}
              {a.key === "attendance" && <Link to="/ai/agents/attendance" className="ml-auto text-primary-600 font-medium hover:underline">Open today's attendance check →</Link>}
            </div>
            <details className="text-xs text-gray-600">
              <summary className="cursor-pointer text-gray-500 hover:text-gray-700">What you approve · what it never does</summary>
              <p className="mt-1"><b>You approve:</b> {a.you_approve}</p>
              <ul className="mt-1 list-disc pl-4">{a.never.map((n) => <li key={n}>Never {n}</li>)}</ul>
              <div className="mt-2 flex flex-wrap gap-1">{Object.entries(a.tier_map).map(([t, v]) => <span key={t} className={`text-[10px] px-1.5 py-0.5 rounded border ${v.tier === 2 ? "border-red-200 text-red-700" : v.tier === 1 ? "border-sky-200 text-sky-700" : "border-gray-200 text-gray-500"}`}>{t.replace(/_/g, " ")} · T{v.tier}</span>)}</div>
            </details>
          </div>
        ))}
      </div>
    </div>
  );
}
