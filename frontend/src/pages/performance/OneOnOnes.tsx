import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Lock, Plus, Trash2 } from "lucide-react";
import toast from "react-hot-toast";
import { err, fmtDate, fmtDateTime } from "../hiring/api";
import { Col, Field, Grid, Input, Modal, Note, Select, Textarea } from "../hiring/ui";
import { FormGrid } from "../../components/Modal";
import { collect, FieldError, firstError, Req, v } from "../../lib/validators";
import type { Row } from "./api";

/** <input type="datetime-local"> value for a Date (local time, no seconds). */
const localDT = (d: Date) => toDateTimeLocal(d);
import { oneApi } from "./api_g4";
import { addDaysISO, fromDateTimeLocal, toDateTimeLocal, todayISO } from "../../lib/datetime";   // 2026-10-25: UTC store, org-zone today

/* G4: 1:1 conversations between a manager and a direct report. Shared notes are seen by both; each
   side's private notes only by that side; HR sees dates and counts, never what was said. */

export default function OneOnOnes() {
  const q = useQuery({ queryKey: ["perf", "ones"], queryFn: oneApi.list });
  const [newOne, setNewOne] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const d = q.data;
  const items: Row[] = d?.items || [];
  const canSchedule = !!(d?.team?.length || d?.my_manager);
  const cols: Col<Row>[] = [
    { k: "scheduled_at", label: "When", render: (m) => fmtDateTime(m.scheduled_at), sortVal: (m) => m.scheduled_at },
    { k: "manager_name", label: "Manager" }, { k: "employee_name", label: "Team member" },
    { k: "status", label: "Status", render: (m) => <span className={m.status === "done" ? "badge-green" : m.status === "cancelled" ? "badge-amber" : "badge-purple"}>{m.status}</span>, csv: (m) => m.status },
    { k: "repeat_days", label: "Repeats", render: (m) => (m.repeat_days ? `Every ${m.repeat_days} days` : "") },
    { k: "actions", label: "Open actions", align: "right", render: (m) => (m.counts ? m.counts.actions_open : (m.action_items || []).filter((a: Row) => !a.done).length), csv: (m) => (m.counts ? m.counts.actions_open : (m.action_items || []).filter((a: Row) => !a.done).length) },
  ];
  return (
    <div>
      {d?.is_hr && <Note>As HR you see that 1:1s happen (dates and counts). Notes, agenda and actions stay between the two people.</Note>}
      {d && !d.linked && !d.is_hr && <Note tone="warn">Your login is not linked to an employee record. Ask HR to link it.</Note>}
      <Grid rows={items} cols={cols} keyOf={(m) => m.id} loading={q.isLoading} title="1:1 conversations" exportName="one-on-ones.csv"
        onRow={(m) => (m.scope === "hr" ? undefined : setOpen(m.id))}
        empty={d?.team?.length ? "No 1:1s yet. Schedule one with a team member." : d?.my_manager ? `No 1:1s yet. You can ask for one with ${d.my_manager.name}.` : "No 1:1s."}
        actions={canSchedule && <button className="btn-primary !py-1 !px-2.5 text-xs inline-flex items-center gap-1" onClick={() => setNewOne(true)}><Plus className="w-3.5 h-3.5" />Schedule a 1:1</button>} />
      {newOne && d && <NewOne team={d.team || []} myManager={d.my_manager} onClose={() => setNewOne(false)} onCreated={(id) => { setNewOne(false); setOpen(id); }} />}
      {open && <Meeting id={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

function NewOne({ team, myManager, onClose, onCreated }: { team: Row[]; myManager: Row | null; onClose: () => void; onCreated: (id: string) => void }) {
  const qc = useQueryClient();
  const people = [...team.map((t) => ({ id: t.id, label: `${t.name} · ${t.employee_number || ""}` })), ...(myManager ? [{ id: myManager.id, label: `${myManager.name} (my manager)` }] : [])];
  const [f, setF] = useState<Row>({ employee_id: people[0]?.id || "", scheduled_at: "", repeat_days: "14", agenda: "" });
  const [tried, setTried] = useState(false);
  const errs = collect({
    employee_id: v.required(f.employee_id, "Who the 1:1 is with"),
    scheduled_at: v.required(f.scheduled_at, "Date and time") || (f.scheduled_at && new Date(f.scheduled_at).getTime() < Date.now() - 60000 ? "Pick a date and time in the future" : null),
    agenda: String(f.agenda).split("\n").some((t) => t.trim().length > 400) ? "Each agenda point can be at most 400 characters" : null,
  });
  const show = (k: string) => (tried || (k === "scheduled_at" && f.scheduled_at) || k === "agenda" ? errs[k] : null);
  const onSave = () => { setTried(true); if (Object.keys(errs).length) { toast.error(firstError(errs)); return; } save.mutate(); };
  const save = useMutation({
    mutationFn: () => oneApi.create({ employee_id: f.employee_id, scheduled_at: fromDateTimeLocal(f.scheduled_at) || "", repeat_days: f.repeat_days || null,
      agenda: String(f.agenda).split("\n").map((t) => t.trim()).filter(Boolean).map((text) => ({ text })) }),
    onSuccess: (m) => { toast.success("1:1 scheduled. The other person has been told."); qc.invalidateQueries({ queryKey: ["perf", "ones"] }); onCreated(m.id); },
    onError: (e) => toast.error(err(e)),
  });
  return (
    <Modal title="Schedule a 1:1" subtitle="Open action items from your last 1:1 together come along automatically." onClose={onClose} size="lg"
      footer={<><button className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary disabled:opacity-50" disabled={save.isPending} onClick={onSave}>Schedule</button></>}>
      <FormGrid cols={3}>
        <div><Field label={<>With<Req /></>} hint="A team member, or your own manager."><Select value={f.employee_id} onChange={(e) => setF({ ...f, employee_id: e.target.value })}>{!people.length && <option value="">Nobody to meet</option>}{people.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}</Select></Field><FieldError msg={show("employee_id")} /></div>
        <div><Field label={<>Date and time<Req /></>} hint="In the future. The other person is told."><Input type="datetime-local" min={localDT(new Date())} value={f.scheduled_at} onChange={(e) => setF({ ...f, scheduled_at: e.target.value })} /></Field><FieldError msg={show("scheduled_at")} /></div>
        <Field label="Repeat" hint="When you mark it done, the next one is set up and open actions carry over."><Select value={f.repeat_days} onChange={(e) => setF({ ...f, repeat_days: e.target.value })}><option value="">Once</option><option value="7">Every week</option><option value="14">Every 2 weeks</option><option value="30">Every month</option></Select></Field>
        <div className="sm:col-span-2 lg:col-span-3"><Field label="Agenda" hint="One discussion point per line. The other person can add theirs later."><Textarea rows={4} value={f.agenda} onChange={(e) => setF({ ...f, agenda: e.target.value })} placeholder={"Progress on the Q3 release\nTraining plan for next quarter"} /></Field><FieldError msg={show("agenda")} /></div>
      </FormGrid>
    </Modal>
  );
}

function Meeting({ id, onClose }: { id: string; onClose: () => void }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["perf", "ones", id], queryFn: () => oneApi.get(id) });
  const [draft, setDraft] = useState<Row | null>(null);
  const [newPoint, setNewPoint] = useState("");
  const [moveTo, setMoveTo] = useState<string | null>(null);
  const m: Row | undefined = q.data;
  const f: Row | null = draft || (m ? { agenda: m.agenda || [], shared_notes: m.shared_notes || "", my_private: m.my_private || "", action_items: m.action_items || [] } : null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["perf", "ones"] });
  const save = useMutation({ mutationFn: () => oneApi.update(id, f!), onSuccess: () => { toast.success("Saved."); setDraft(null); refresh(); }, onError: (e) => toast.error(err(e)) });
  const done = useMutation({ mutationFn: async () => { if (draft) await oneApi.update(id, draft); return oneApi.complete(id); },
    onSuccess: (r) => { toast.success(r.next ? `Done. Next 1:1 on ${fmtDate(r.next.scheduled_at)}, open actions carried over.` : "Marked as done."); setDraft(null); refresh(); }, onError: (e) => toast.error(err(e)) });
  const cancel = useMutation({ mutationFn: () => oneApi.cancel(id), onSuccess: () => { toast.success("Cancelled."); refresh(); }, onError: (e) => toast.error(err(e)) });
  const resched = useMutation({ mutationFn: (when: string) => oneApi.update(id, { scheduled_at: fromDateTimeLocal(when) || "" }),
    onSuccess: (r) => { toast.success(`Moved to ${fmtDateTime(r.scheduled_at)}.`); setMoveTo(null); refresh(); }, onError: (e) => toast.error(err(e)) });
  if (!m || !f) return <Modal title="1:1" onClose={onClose}><div className="text-sm text-gray-400">{q.isError ? err(q.error) : "Loading…"}</div></Modal>;
  const ro = m.status === "cancelled";
  const set = (k: string, v: any) => setDraft({ ...f, [k]: v });
  const updAct = (i: number, k: string, val: any) => set("action_items", f.action_items.map((a: Row, j: number) => (j === i ? { ...a, [k]: val } : a)));
  const todayIso = todayISO();
  const actErrs = collect(Object.fromEntries((f.action_items as Row[]).map((a, i) => [`a${i}`,
    !String(a.text || "").trim() ? `Action ${i + 1} has no text: write it or remove the row` : v.maxLen(String(a.text).trim(), 400, `Action ${i + 1}`)])));
  const guardSave = (then: () => void) => () => { if (Object.keys(actErrs).length) { toast.error(firstError(actErrs)); return; } then(); };
  const plusDays = (n: number) => addDaysISO(todayISO(), n);
  return (
    <Modal size="xl" title={`1:1 · ${m.manager_name} and ${m.employee_name}`} subtitle={`${fmtDateTime(m.scheduled_at)} · ${m.status}${m.repeat_days ? ` · every ${m.repeat_days} days` : ""}`} onClose={onClose}
      footer={<>{m.status === "scheduled" && <button className="btn-secondary" onClick={() => { if (window.confirm("Cancel this 1:1?")) cancel.mutate(); }}>Cancel 1:1</button>}
        {m.status === "scheduled" && <button className="btn-secondary" onClick={() => setMoveTo(localDT(new Date(m.scheduled_at)))}>Reschedule</button>}
        {!ro && <button className="btn-secondary disabled:opacity-50" disabled={!draft || save.isPending} onClick={guardSave(() => save.mutate())}>Save</button>}
        {m.status === "scheduled" && <button className="btn-primary disabled:opacity-50" disabled={done.isPending} onClick={guardSave(() => done.mutate())}>Mark as done</button>}
        <button className="btn-secondary" onClick={onClose}>Close</button></>}>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="space-y-3">
          <div className="card !p-3">
            <div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold mb-2">Agenda</div>
            {!f.agenda.length && <div className="text-xs text-gray-400">No points yet.</div>}
            {f.agenda.map((a: Row, i: number) => (
              <label key={a.id || i} className="flex items-center gap-2 text-xs py-0.5">
                <input type="checkbox" disabled={ro} checked={!!a.discussed} onChange={(e) => set("agenda", f.agenda.map((x: Row, j: number) => (j === i ? { ...x, discussed: e.target.checked } : x)))} />
                <span className={a.discussed ? "line-through text-gray-400" : "text-gray-700"}>{a.text}</span><span className="text-[10px] text-gray-400">({a.by})</span>
              </label>))}
            {!ro && <div className="flex gap-2 mt-2"><Input value={newPoint} onChange={(e) => setNewPoint(e.target.value)} placeholder="Add a point" />
              <button className="btn-secondary !py-1 !px-2 text-xs" disabled={!newPoint.trim()} onClick={() => { set("agenda", [...f.agenda, { text: newPoint.trim() }]); setNewPoint(""); }}>Add</button></div>}
          </div>
          <Field label="Shared notes (both of you see these)" hint="Up to 8000 characters."><Textarea rows={6} disabled={ro} maxLength={8000} value={f.shared_notes} onChange={(e) => set("shared_notes", e.target.value)} /></Field>
          <Field label="My private notes (only you see these)" hint="Never shared with the other person or with HR."><div className="relative"><Textarea rows={4} disabled={ro} maxLength={8000} value={f.my_private} onChange={(e) => set("my_private", e.target.value)} /><Lock className="w-3 h-3 text-gray-300 absolute right-2 top-2" /></div></Field>
        </div>
        <div className="card !p-3">
          <div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold mb-2">Action items</div>
          <table className="w-full text-xs"><thead className="text-[10px] uppercase text-gray-400 text-left"><tr><th className="w-6" /><th>Action</th><th>Owner</th><th>Due</th><th /></tr></thead>
            <tbody>{f.action_items.map((a: Row, i: number) => (
              <tr key={a.id || i} className="border-t border-surface-border">
                <td className="py-1"><input type="checkbox" disabled={ro} checked={!!a.done} onChange={(e) => updAct(i, "done", e.target.checked)} aria-label="Done" /></td>
                <td className="pr-1"><Input disabled={ro} maxLength={400} value={a.text} className={!String(a.text || "").trim() ? "!border-red-300" : ""} placeholder="What will be done" onChange={(e) => updAct(i, "text", e.target.value)} />{a.carried_from && <div className="text-[10px] text-gray-400">carried over</div>}</td>
                <td className="pr-1 w-28"><select className="input !py-1.5 text-xs w-full" disabled={ro} value={a.owner} onChange={(e) => updAct(i, "owner", e.target.value)}><option value="employee">Team member</option><option value="manager">Manager</option></select></td>
                <td className="pr-1 w-36"><Input type="date" disabled={ro} value={a.due || ""} onChange={(e) => updAct(i, "due", e.target.value)} />
                  {!ro && !a.due && <div className="flex gap-1 mt-0.5 text-[10px]"><button className="underline text-primary-600" onClick={() => updAct(i, "due", plusDays(7))}>+1 week</button><button className="underline text-primary-600" onClick={() => updAct(i, "due", plusDays(14))}>+2 weeks</button></div>}
                  {!a.done && a.due && a.due < todayIso && <div className="text-[10px] text-amber-700">overdue</div>}</td>
                <td>{!ro && <button className="icon-btn" title="Remove" onClick={() => set("action_items", f.action_items.filter((_: Row, j: number) => j !== i))}><Trash2 className="w-3.5 h-3.5 text-gray-400" /></button>}</td>
              </tr>))}</tbody></table>
          {!ro && <button className="btn-secondary !py-1 !px-2 text-xs inline-flex items-center gap-1 mt-2" onClick={() => set("action_items", [...f.action_items, { text: "", owner: "employee", due: "", done: false }])}><Plus className="w-3 h-3" />Add an action</button>}
          <p className="text-[11px] text-gray-400 mt-2">Set an owner and a due date. When you mark the 1:1 as done, open actions move to the next one.</p>
        </div>
      </div>
      {moveTo !== null && (
        <Modal size="lg" title="Reschedule the 1:1" subtitle={`Now ${fmtDateTime(m.scheduled_at)}. Notes, agenda and actions stay as they are.`} onClose={() => setMoveTo(null)}
          footer={<><button className="btn-secondary" onClick={() => setMoveTo(null)}>Back</button>
            <button className="btn-primary disabled:opacity-50" disabled={resched.isPending} onClick={() => {
              const e = collect({ when: v.required(moveTo, "New date and time") || (new Date(moveTo).getTime() < Date.now() ? "Pick a date and time in the future" : null) });
              if (Object.keys(e).length) { toast.error(firstError(e)); return; } resched.mutate(moveTo); }}>Move the 1:1</button></>}>
          <Field label={<>New date and time<Req /></>}><Input type="datetime-local" min={localDT(new Date())} value={moveTo} onChange={(e) => setMoveTo(e.target.value)} /></Field>
          <FieldError msg={moveTo && new Date(moveTo).getTime() < Date.now() ? "Pick a date and time in the future" : null} />
        </Modal>)}
    </Modal>
  );
}
