import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { CheckSquare, Plus, Trash2 } from "lucide-react";
import DataGrid, { GridColumn } from "../../components/DataGrid";
import { PageHeader, StatTile } from "../../components/ui";
import { err } from "../canada/_shared";
import { essRequestsApi, TaskRow } from "../../api/essRequests";

const localISO = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
const nice = (iso?: string | null) => (iso ? new Date(iso.slice(0, 10) + "T00:00:00").toLocaleDateString("en-CA", { day: "numeric", month: "short", year: "numeric" }) : "");
const SOURCE_TONE: Record<string, string> = { todo: "badge-teal", exit_clearance: "badge-amber", exit_owner: "badge-red", onboarding: "badge-purple" };
const STATUS_LABEL: Record<string, string> = { pending: "pending", cleared: "cleared", cleared_with_recovery: "cleared (recovery)", open: "open", done: "done", overdue: "overdue" };

/** My tasks: my own to-dos (add / tick / delete) + checklist items assigned to me elsewhere (read-only). */
export default function TasksPage() {
  const qc = useQueryClient();
  const today = localISO();
  const { data, isLoading } = useQuery({ queryKey: ["ess-tasks"], queryFn: essRequestsApi.tasks });
  const [title, setTitle] = useState("");
  const [due, setDue] = useState("");
  const [showDone, setShowDone] = useState(false);
  const [src, setSrc] = useState("");
  const refresh = () => qc.invalidateQueries({ queryKey: ["ess-tasks"] });
  const add = useMutation({ mutationFn: () => essRequestsApi.addTodo({ title: title.trim(), due_date: due || null }),
    onSuccess: () => { setTitle(""); setDue(""); refresh(); }, onError: (e) => toast.error(err(e, "Could not add")) });
  const tick = useMutation({ mutationFn: (r: TaskRow) => essRequestsApi.updateTodo(r.ref_id, { done: !r.done }),
    onSuccess: refresh, onError: (e) => toast.error(err(e)) });
  const del = useMutation({ mutationFn: (id: string) => essRequestsApi.deleteTodo(id),
    onSuccess: () => { toast.success("To-do deleted"); refresh(); }, onError: (e) => toast.error(err(e)) });

  const items = data?.items || [];
  const todos = items.filter((r) => r.source === "todo" && (showDone || !r.done));
  const assigned = items.filter((r) => r.source !== "todo" && (!src || r.source === src) && (showDone || !r.done));
  const c = data?.counts;
  const late = (r: TaskRow) => !r.done && !!r.due_date && r.due_date < today;

  const cols: GridColumn<TaskRow>[] = [
    { key: "source_label", label: "From", text: (r) => r.source_label, render: (r) => <span className={SOURCE_TONE[r.source] || "badge-teal"}>{r.source_label}</span> },
    { key: "title", label: "Task", render: (r) => <span className={r.done ? "line-through text-gray-400" : "text-gray-800"}>{r.title}</span>, text: (r) => r.title },
    { key: "detail", label: "Detail" },
    { key: "due_date", label: "Due", type: "date", render: (r) => <span className={late(r) ? "text-red-600 font-semibold" : ""}>{nice(r.due_date) || "—"}</span>, text: (r) => r.due_date || "" },
    { key: "status", label: "Status", text: (r) => STATUS_LABEL[r.status] || r.status,
      render: (r) => <span className={r.done ? "badge-green" : late(r) ? "badge-red" : "badge-amber"}>{late(r) ? "overdue" : STATUS_LABEL[r.status] || r.status}</span> },
    { key: "_l", label: "", sortable: false, render: (r) => (r.link ? <Link to={r.link} className="text-xs text-primary-600 hover:underline">Open</Link> : null) },
  ];

  return (
    <div className="max-w-[1400px] mx-auto">
      <PageHeader title="My tasks" icon={CheckSquare} subtitle="Your own to-dos, plus checklist items others are waiting on you for." />
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
        <StatTile label="Open" value={c?.open ?? "—"} accent />
        <StatTile label="Overdue" value={c?.overdue ?? "—"} />
        <StatTile label="My to-dos open" value={c?.by_source?.todo ?? 0} />
        <StatTile label="Assigned to me" value={(c?.open ?? 0) - (c?.by_source?.todo ?? 0)} />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,26rem)_minmax(0,1fr)] gap-4">
        <section className="card !p-0 min-w-0">
          <div className="px-4 py-3 border-b border-surface-border flex items-center justify-between gap-2">
            <span className="font-display font-semibold text-gray-800 text-sm">My to-dos</span>
            <label className="text-xs text-gray-500 flex items-center gap-1.5"><input type="checkbox" checked={showDone} onChange={(e) => setShowDone(e.target.checked)} />Show done</label>
          </div>
          <form className="px-4 py-3 flex flex-wrap gap-2 border-b border-surface-border"
            onSubmit={(e) => { e.preventDefault(); if (title.trim()) add.mutate(); }}>
            <input className="input-base h-8 text-xs flex-1 min-w-[10rem]" placeholder="Add a to-do…" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} />
            <input type="date" className="input-base h-8 text-xs w-36" value={due} onChange={(e) => setDue(e.target.value)} />
            <button className="btn-primary !py-1 text-xs inline-flex items-center gap-1" disabled={!title.trim() || add.isPending}><Plus className="w-3.5 h-3.5" />Add</button>
          </form>
          {isLoading ? <p className="px-4 py-4 text-sm text-gray-400">Loading…</p> : !todos.length ? <p className="px-4 py-6 text-sm text-gray-400 text-center">Nothing on your list.</p> : (
            <ul className="divide-y divide-surface-border max-h-[60vh] overflow-y-auto">{todos.map((r) => (
              <li key={r.id} className="px-4 py-2 flex items-center gap-3 text-sm">
                <input type="checkbox" checked={r.done} onChange={() => tick.mutate(r)} aria-label="Done" />
                <span className={`flex-1 min-w-0 break-words ${r.done ? "line-through text-gray-400" : "text-gray-800"}`}>{r.title}</span>
                {r.due_date && <span className={`text-xs whitespace-nowrap ${late(r) ? "text-red-600 font-semibold" : "text-gray-500"}`}>{nice(r.due_date)}</span>}
                <button className="text-gray-400 hover:text-red-600" title="Delete" onClick={() => del.mutate(r.ref_id)}><Trash2 className="w-3.5 h-3.5" /></button>
              </li>))}
            </ul>)}
        </section>

        <section className="min-w-0">
          <DataGrid columns={cols} rows={assigned} rowKey={(r) => r.id} exportName="my-assigned-tasks" minWidth={820}
            empty="Nothing is assigned to you right now."
            filters={<select className="input-base h-8 text-xs" value={src} onChange={(e) => setSrc(e.target.value)}>
              <option value="">All sources</option>
              <option value="exit_owner">Exit clearance to sign off</option>
              <option value="exit_clearance">My exit clearance</option>
              <option value="onboarding">Onboarding</option>
            </select>} />
          {data?.sources?.onboarding === "unavailable" && <p className="text-[11px] text-amber-600 mt-2">Onboarding checklists could not be loaded just now — try again later.</p>}
        </section>
      </div>
    </div>
  );
}
