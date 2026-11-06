import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Link } from "react-router-dom";
import { BellRing, CheckCircle2, ArrowUpRight } from "lucide-react";
import DataGrid from "../../../components/DataGrid";
import { errText, when } from "../../../api/platform";
import { agenticApi, type AgentCard, type AgentTask } from "../../../api/agentic";
import { Pill } from "./shared";

/** Tasks the agents opened for people: who is being chased, due, chase count; chase now / escalate now / mark done. */
export default function TasksTab({ agents, agent, setAgent, canAct }: { agents: AgentCard[]; agent: string; setAgent: (a: string) => void; canAct: boolean }) {
  const qc = useQueryClient();
  const [status, setStatus] = useState("open,waiting,escalated");
  const q = useQuery({ queryKey: ["agentic", "tasks", status, agent], queryFn: () => agenticApi.tasks({ status, agent }) });
  const refresh = () => qc.invalidateQueries({ queryKey: ["agentic"] });
  const chase = useMutation({ mutationFn: (id: string) => agenticApi.chaseTask(id), onSuccess: (t) => { toast.success(`Reminder ${t.chase_count} sent${t.status === "escalated" ? " — escalated" : ""}`); refresh(); }, onError: (e) => toast.error(errText(e)) });
  const escalate = useMutation({ mutationFn: (id: string) => agenticApi.escalateTask(id, "Escalated by hand from the Agents screen"), onSuccess: () => { toast.success("Escalated to HR / org admin"); refresh(); }, onError: (e) => toast.error(errText(e)) });
  const done = useMutation({ mutationFn: (id: string) => agenticApi.completeTask(id, { closed_from: "agents_screen" }), onSuccess: () => { toast.success("Marked done"); refresh(); }, onError: (e) => toast.error(errText(e)) });
  const busy = chase.isPending || escalate.isPending || done.isPending;
  return (
    <DataGrid<AgentTask> rows={q.data?.items || []} rowKey={(r) => r.id} minWidth={1100} exportName="agent-tasks" density="compact"
      empty={q.isLoading ? "Loading…" : "No open items — nobody is being chased right now."}
      rowClassName={(r) => (r.overdue ? "bg-red-50/40" : "")}
      filters={<>
        <select className="input !py-1.5 text-sm w-44" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
          <option value="open,waiting,escalated">Open (all)</option><option value="escalated">Escalated</option><option value="waiting">Waiting after a reminder</option>
          <option value="open">Not yet reminded</option><option value="done">Done</option><option value="cancelled">Cancelled</option></select>
        <select className="input !py-1.5 text-sm w-44" value={agent} onChange={(e) => setAgent(e.target.value)} aria-label="Agent">
          <option value="">All agents</option>{agents.map((a) => <option key={a.key} value={a.key}>{a.role_name}</option>)}</select>
      </>}
      columns={[
        { key: "status", label: "Status", width: "100px", render: (r) => <Pill v={r.status} />, text: (r) => r.status },
        { key: "agent_role", label: "Agent", width: "130px" },
        { key: "title", label: "What is needed", className: "text-gray-800", render: (r) => <div className="whitespace-normal max-w-lg"><div>{r.title}</div>{r.detail?.how_to_fix && <div className="text-[11px] text-gray-500">{r.detail.how_to_fix}</div>}{r.detail?.link && <Link to={r.detail.link} className="text-[11px] text-primary-600 hover:underline inline-flex items-center gap-0.5" onClick={(e) => e.stopPropagation()}>open <ArrowUpRight className="w-3 h-3" /></Link>}</div> },
        { key: "kind", label: "Kind", width: "120px", render: (r) => r.kind.replace(/_/g, " ") },
        { key: "owner_user_id", label: "Who is chased", render: (r) => <span className="font-mono text-xs">{r.owner_user_id || r.owner_employee_id || "—"}</span>, text: (r) => r.owner_user_id || r.owner_employee_id || "" },
        { key: "due_at", label: "Due", render: (r) => <span className={r.overdue ? "text-red-700 font-semibold" : ""}>{r.due_at ? when(r.due_at) : "—"}</span>, text: (r) => r.due_at || "" },
        { key: "chase_count", label: "Reminders", type: "number", render: (r) => <span className="tabular-nums">{r.chase_count}{r.last_chased_at && <span className="text-[10px] text-gray-400 block">last {when(r.last_chased_at)}</span>}</span> },
        { key: "next_chase_at", label: "Next reminder", render: (r) => r.next_chase_at && ["open", "waiting"].includes(r.status) ? when(r.next_chase_at) : "—", text: (r) => r.next_chase_at || "" },
        { key: "escalation_to", label: "Escalates to", render: (r) => r.escalation_to ? [...(r.escalation_to.roles || []), ...(r.escalation_to.user_ids || [])].join(", ") : "HR / org admin (default)", text: (r) => JSON.stringify(r.escalation_to || "") },
        { key: "actions", label: "", sortable: false, render: (r) => ["open", "waiting", "escalated"].includes(r.status) && canAct ? (
          <span className="inline-flex gap-1">
            <button className="btn-secondary !py-1 !px-2 text-xs inline-flex items-center gap-1" disabled={busy} title="Send a reminder now" onClick={(e) => { e.stopPropagation(); chase.mutate(r.id); }}><BellRing className="w-3 h-3" /> Chase</button>
            {r.status !== "escalated" && <button className="btn-secondary !py-1 !px-2 text-xs" disabled={busy} onClick={(e) => { e.stopPropagation(); escalate.mutate(r.id); }}>Escalate now</button>}
            <button className="btn-secondary !py-1 !px-2 text-xs inline-flex items-center gap-1" disabled={busy} onClick={(e) => { e.stopPropagation(); done.mutate(r.id); }}><CheckCircle2 className="w-3 h-3" /> Done</button>
          </span>) : null, text: () => "" },
      ]} />
  );
}
