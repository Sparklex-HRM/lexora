import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Lock, Plus, Trash2 } from "lucide-react";
import toast from "react-hot-toast";
import { err, fmtDate, fmtDateTime } from "../hiring/api";
import { Col, empLabel, Field, Grid, Input, Modal, Note, Pill, Select, Textarea, useEmployees } from "../hiring/ui";
import { useAuthStore } from "../../store/authStore";
import StartExitModal from "../exit/StartExitModal";
import { FormGrid, FormSection } from "../../components/Modal";
import { collect, FieldError, firstError, Req, v } from "../../lib/validators";
import type { Row } from "./api";

const isoToday = () => todayISO();
const daysBetween = (a: string, b: string) => Math.round((Date.parse(b) - Date.parse(a)) / 86400000);
const addDays = (iso: string, n: number) => addDaysISO(iso, n);
import { pipApi } from "./api_g4";
import { addDaysISO, todayISO } from "../../lib/datetime";   // 2026-10-25: UTC store, org-zone today

/* G4: improvement plans. CONFIDENTIAL — the employee (once started), their manager and HR only;
   the server answers "not found" to anyone else. An unsuccessful outcome never ends employment:
   HR starts an exit case by hand and links it here. */

const RATING: Record<string, string> = { on_track: "On track", some_progress: "Some progress", off_track: "Off track" };
const STATUS_LABEL: Record<string, string> = { draft: "Draft (HR to start)", active: "Active", extended: "Extended", closed: "Closed", cancelled: "Cancelled" };

export default function Pips() {
  const [params, setParams] = useSearchParams();
  const q = useQuery({ queryKey: ["perf", "pips"], queryFn: pipApi.list });
  const [newPlan, setNewPlan] = useState(false);
  const open = params.get("id");
  const setOpen = (id: string | null) => { const p = new URLSearchParams(params); if (id) p.set("id", id); else p.delete("id"); setParams(p, { replace: true }); };
  const canCreate = !!(q.data?.is_hr || q.data?.is_manager);
  const cols: Col<Row>[] = [
    { k: "plan_number", label: "Plan", render: (r) => <span className="font-medium text-gray-800">{r.plan_number}</span> },
    { k: "employee_name", label: "Employee", render: (r) => (r.scope === "self" ? "You" : `${r.employee_name} · ${r.employee_number || ""}`) },
    { k: "manager_name", label: "Manager" }, { k: "hr_owner_name", label: "HR owner" },
    { k: "start_date", label: "From", render: (r) => fmtDate(r.start_date) }, { k: "end_date", label: "To", render: (r) => fmtDate(r.end_date) },
    { k: "status", label: "Status", render: (r) => <Pill s={r.status === "active" || r.status === "extended" ? "open" : r.status} label={STATUS_LABEL[r.status]} />, csv: (r) => r.status },
    { k: "outcome", label: "Outcome", render: (r) => r.outcome_label || "" },
    { k: "ack_status", label: "Employee", render: (r) => (r.ack_status === "acknowledged" ? "Acknowledged" : r.ack_status === "disagreed" ? "Disagrees" : r.status === "draft" ? "" : "Not yet") },
  ];
  return (
    <div>
      <div className="flex items-center gap-2 text-xs text-gray-600 mb-3 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2"><Lock className="w-3.5 h-3.5 text-amber-700" />
        Confidential. Only the employee, their manager and HR can see an improvement plan. A plan that does not succeed does not end employment by itself.</div>
      <Grid rows={q.data?.items || []} cols={cols} keyOf={(r) => r.id} loading={q.isLoading} title="Improvement plans" onRow={(r) => setOpen(r.id)} exportName="improvement-plans.csv"
        empty={canCreate ? "No improvement plans. A manager drafts one for a direct report; HR reviews and starts it." : "You have no improvement plan."}
        actions={canCreate && <button className="btn-primary !py-1 !px-2.5 text-xs inline-flex items-center gap-1" onClick={() => setNewPlan(true)}><Plus className="w-3.5 h-3.5" />New plan</button>} />
      {newPlan && <PlanForm isHr={!!q.data?.is_hr} onClose={() => setNewPlan(false)} onCreated={(id) => { setNewPlan(false); setOpen(id); }} />}
      {open && <PlanModal id={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

const BLANK = { kind: "goal", title: "", measure: "", owner: "", due_date: "" };

function ItemsGrid({ rows, setRows }: { rows: Row[]; setRows: (r: Row[]) => void }) {
  const upd = (i: number, k: string, v: any) => setRows(rows.map((r, j) => (j === i ? { ...r, [k]: v } : r)));
  return (<div className="overflow-x-auto">
    <table className="w-full text-xs"><thead className="text-[10px] uppercase text-gray-400 text-left"><tr><th className="py-1">Type</th><th>What *</th><th>How it is measured</th><th>Owner</th><th>Due</th><th /></tr></thead>
      <tbody>{rows.map((r, i) => (
        <tr key={i} className="border-t border-surface-border">
          <td className="py-1 pr-1 w-32"><select className="input !py-1.5 text-xs w-full" value={r.kind} onChange={(e) => upd(i, "kind", e.target.value)}><option value="goal">Goal</option><option value="support">Support action</option></select></td>
          <td className="pr-1 min-w-[16rem]"><Input maxLength={300} value={r.title} onChange={(e) => upd(i, "title", e.target.value)} placeholder={r.kind === "goal" ? "Deliver sprint items on the agreed date" : "Weekly mentoring with the team lead"} /></td>
          <td className="pr-1 min-w-[12rem]"><Input maxLength={300} value={r.measure} onChange={(e) => upd(i, "measure", e.target.value)} placeholder={r.kind === "goal" ? "9 of 10 items on time" : ""} /></td>
          <td className="pr-1 w-36"><Input maxLength={200} value={r.owner} placeholder={r.kind === "goal" ? "Employee" : "Team lead"} onChange={(e) => upd(i, "owner", e.target.value)} /></td>
          <td className="pr-1 w-36"><Input type="date" value={r.due_date} onChange={(e) => upd(i, "due_date", e.target.value)} /></td>
          <td><button className="icon-btn" title="Remove" onClick={() => setRows(rows.filter((_, j) => j !== i))}><Trash2 className="w-3.5 h-3.5 text-gray-400" /></button></td>
        </tr>))}</tbody></table>
    <div className="flex gap-2 mt-2"><button className="btn-secondary !py-1 !px-2 text-xs" onClick={() => setRows([...rows, { ...BLANK }])}>Add a goal</button>
      <button className="btn-secondary !py-1 !px-2 text-xs" onClick={() => setRows([...rows, { ...BLANK, kind: "support" }])}>Add a support action</button></div>
  </div>);
}

function PlanForm({ isHr, onClose, onCreated }: { isHr: boolean; onClose: () => void; onCreated: (id: string) => void }) {
  const qc = useQueryClient();
  const me = useAuthStore((s) => s.user?.employee_id) || "";
  const employees = useEmployees();
  const people = useMemo(() => employees.filter((e: any) => e.id !== me && (isHr || e.manager_id === me)), [employees, isHr, me]);
  const [f, setF] = useState<Row>(() => ({ employee_id: "", hr_owner_name: "", reason: "", expectations: "", start_date: isoToday(), end_date: addDays(isoToday(), 60), checkin_every_days: 14 }));
  const [items, setItems] = useState<Row[]>([{ ...BLANK }, { ...BLANK, kind: "support" }]);
  const [tried, setTried] = useState(false);
  const set = (k: string, val: any) => setF({ ...f, [k]: val });
  const span = f.start_date && f.end_date ? daysBetween(f.start_date, f.end_date) : null;
  const errs = collect({
    employee_id: v.required(f.employee_id, "Employee"),
    start_date: v.required(f.start_date, "Start date"),
    end_date: v.required(f.end_date, "End date") || (span !== null && span < 14 ? "A plan must run for at least 14 days" : span !== null && span > 180 ? "A plan cannot run for more than 180 days" : null),
    checkin_every_days: v.required(f.checkin_every_days, "Check-in every") || (!Number.isInteger(Number(f.checkin_every_days)) ? "Check-in every must be a whole number of days" : null) || v.range(f.checkin_every_days, 7, 60, "Check-in every"),
    reason: v.required(String(f.reason).trim(), "Why the plan is needed") || (String(f.reason).trim().length < 20 ? "Describe why the plan is needed in at least 20 characters: facts and examples, not a label" : null) || v.maxLen(f.reason, 4000, "Why the plan is needed"),
    expectations: v.maxLen(f.expectations, 4000, "What good looks like"),
    hr_owner_name: v.maxLen(f.hr_owner_name, 200, "HR owner"),
    items: !items.some((i) => i.kind === "goal" && String(i.title || "").trim()) ? "Add at least one goal line (what must improve)" : null,
    item_dates: items.some((i) => String(i.title || "").trim() && i.due_date && f.start_date && f.end_date && (i.due_date < f.start_date || i.due_date > f.end_date)) ? "Line due dates must fall inside the plan dates" : null,
  });
  const show = (k: string) => (tried || ["start_date", "end_date", "checkin_every_days", "item_dates"].includes(k) ? errs[k] : null);
  const onSave = () => { setTried(true); if (Object.keys(errs).length) { toast.error(firstError(errs)); return; } save.mutate(); };
  const save = useMutation({
    mutationFn: () => pipApi.create({ ...f, reason: String(f.reason).trim(), hr_owner_name: isHr ? String(f.hr_owner_name || "").trim() || undefined : undefined,
      items: items.filter((i) => i.title.trim()).map((i) => ({ ...i, due_date: i.due_date || null })) }),
    onSuccess: (r) => { toast.success(isHr ? "Draft saved. Check it, then Start." : "Draft saved and sent to HR to review and start."); qc.invalidateQueries({ queryKey: ["perf", "pips"] }); onCreated(r.id); },
    onError: (e) => toast.error(err(e), { duration: 7000 }),
  });
  return (
    <Modal title="New improvement plan" subtitle="Write facts and examples, clear goals and the support the company will give. The employee sees it once HR starts it." onClose={onClose}
      footer={<><button className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary disabled:opacity-50" disabled={save.isPending} onClick={onSave}>Save draft</button></>}>
      <FormSection title="Who and when">
        <FormGrid cols={4}>
          <div className="sm:col-span-2"><Field label={<>Employee<Req /></>} hint={isHr ? "HR can pick anyone except themselves." : "You can pick only your direct reports."}><Select value={f.employee_id} onChange={(e) => set("employee_id", e.target.value)}><option value="">Pick</option>{people.map((e: any) => <option key={e.id} value={e.id}>{empLabel(e)}</option>)}</Select></Field><FieldError msg={show("employee_id")} /></div>
          <div><Field label={<>Start<Req /></>}><Input type="date" value={f.start_date} onChange={(e) => set("start_date", e.target.value)} /></Field><FieldError msg={show("start_date")} />
            {f.start_date && f.start_date < addDays(isoToday(), -30) && <span className="block text-[11px] text-amber-700 mt-0.5">More than 30 days in the past. Usually a plan starts today or later.</span>}</div>
          <div><Field label={<>End<Req /></>} hint={span !== null && span > 0 ? `${span} days (14 to 180 allowed).` : "14 to 180 days after the start."}><Input type="date" min={f.start_date ? addDays(f.start_date, 14) : undefined} max={f.start_date ? addDays(f.start_date, 180) : undefined} value={f.end_date} onChange={(e) => set("end_date", e.target.value)} /></Field><FieldError msg={show("end_date")} /></div>
          <div><Field label={<>Check-in every (days)<Req /></>} hint="How often progress is formally reviewed, 7 to 60 days. The dates are set when HR starts the plan."><Input type="number" min={7} max={60} step={1} value={f.checkin_every_days} onChange={(e) => set("checkin_every_days", e.target.value)} /></Field><FieldError msg={show("checkin_every_days")} /></div>
          {isHr && <div className="sm:col-span-2"><Field label="HR owner" hint="The HR person who runs this plan. Leave blank to be the owner yourself."><Input maxLength={200} value={f.hr_owner_name || ""} onChange={(e) => set("hr_owner_name", e.target.value)} /></Field><FieldError msg={show("hr_owner_name")} /></div>}
        </FormGrid>
      </FormSection>
      <FormSection title="The plan" hint="The employee sees this once HR starts the plan. Write it so a third person would understand it.">
        <FormGrid>
          <div><Field label={<>Why the plan is needed<Req /></>} hint="Facts and examples (dates, work items), not labels. At least 20 characters."><Textarea rows={5} maxLength={4000} value={f.reason} onChange={(e) => set("reason", e.target.value)} /></Field><FieldError msg={show("reason")} /></div>
          <div><Field label="What good looks like" hint="The observable standard to reach: specific, measurable outcomes."><Textarea rows={5} maxLength={4000} value={f.expectations} onChange={(e) => set("expectations", e.target.value)} /></Field></div>
        </FormGrid>
      </FormSection>
      <FormSection title="Goals and support" hint="At least one goal now. HR needs at least one goal and one support action before starting the plan.">
        <ItemsGrid rows={items} setRows={setItems} />
        <FieldError msg={show("items")} /><FieldError msg={show("item_dates")} />
      </FormSection>
    </Modal>
  );
}

function PlanModal({ id, onClose }: { id: string; onClose: () => void }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["perf", "pips", id], queryFn: () => pipApi.get(id) });
  const [ck, setCk] = useState<Row | null>(null);
  const [newItems, setNewItems] = useState<Row[]>([]);
  const [ack, setAck] = useState<Row>({ status: "acknowledged", comment: "" });
  const [oc, setOc] = useState<Row>({ outcome: "successful", note: "", new_end_date: "" });
  const [exitOpen, setExitOpen] = useState(false);
  const [cancelReason, setCancelReason] = useState("");
  const refresh = () => qc.invalidateQueries({ queryKey: ["perf", "pips"] });
  const fail = (e: any) => toast.error(err(e), { duration: 7000 });
  const ok = (m: string) => () => { toast.success(m); refresh(); };
  const start = useMutation({ mutationFn: () => pipApi.start(id), onSuccess: ok("Plan started. The employee and manager have been told."), onError: fail });
  const item = useMutation({ mutationFn: (v: { id: string; body: Row }) => pipApi.updateItem(v.id, v.body), onSuccess: ok("Saved."), onError: fail });
  const delItem = useMutation({ mutationFn: pipApi.deleteItem, onSuccess: ok("Removed."), onError: fail });
  const addItems = useMutation({ mutationFn: async () => { for (const i of newItems.filter((x) => x.title.trim())) await pipApi.addItem(id, { ...i, due_date: i.due_date || null }); }, onSuccess: () => { setNewItems([]); ok("Lines added.")(); }, onError: fail });
  const checkin = useMutation({ mutationFn: () => pipApi.checkin(id, ck!.id, ck!), onSuccess: () => { setCk(null); ok("Check-in recorded.")(); }, onError: fail });
  const doAck = useMutation({ mutationFn: () => pipApi.ack(id, ack.status, ack.comment), onSuccess: ok("Thank you. Your response is on the record."), onError: fail });
  const outcome = useMutation({ mutationFn: () => pipApi.outcome(id, { ...oc, new_end_date: oc.new_end_date || null }),
    onSuccess: (r) => { toast.success(r.next_step || "Outcome recorded.", { duration: 9000 }); refresh(); }, onError: fail });
  const cancel = useMutation({ mutationFn: () => pipApi.cancel(id, cancelReason), onSuccess: ok("Plan cancelled."), onError: fail });
  const link = useMutation({ mutationFn: (caseId: string) => pipApi.exitLink(id, caseId), onSuccess: ok("Exit case linked to the plan."), onError: fail });
  const run = (e: Record<string, string>, then: () => void) => { if (Object.keys(e).length) { toast.error(firstError(e)); return; } then(); };
  if (q.isLoading) return <Modal title="Improvement plan" onClose={onClose}><div className="text-sm text-gray-400">Loading…</div></Modal>;
  if (q.isError || !q.data) return <Modal title="Improvement plan" onClose={onClose}><Note tone="warn">{err(q.error, "This plan could not be opened.")}</Note></Modal>;
  const p = q.data, can = p.can || {};
  const ackErrs = collect({ comment: (ack.status === "disagreed" ? v.required(ack.comment.trim(), "What you disagree with") : null) || v.maxLen(ack.comment, 4000, "Comment") });
  const ocErrs = collect({
    new_end_date: oc.outcome === "extended" ? v.required(oc.new_end_date, "New end date") || (oc.new_end_date && oc.new_end_date <= p.end_date ? `The new end date must be after the current end date (${fmtDate(p.end_date)})` : null) : null,
    note: v.required(oc.note.trim(), "Reason") || (oc.note.trim().length < 10 ? "Write the reason in at least 10 characters" : null) || v.maxLen(oc.note, 4000, "Reason"),
  });
  const cancelErrs = collect({ reason: v.required(cancelReason.trim(), "Reason") || (cancelReason.trim().length < 10 ? "Write the reason for cancelling in at least 10 characters" : null) || v.maxLen(cancelReason, 600, "Reason") });
  const hrActions = can.close || can.extend || can.link_exit || can.cancel;
  const goals = (p.items || []).filter((i: Row) => i.kind === "goal"), support = (p.items || []).filter((i: Row) => i.kind === "support");
  const line = (i: Row) => (
    <tr key={i.id} className="border-t border-surface-border">
      <td className="px-2.5 py-1.5 font-medium text-gray-800">{i.title}</td><td className="px-2.5">{i.measure}</td><td className="px-2.5">{i.owner}</td><td className="px-2.5">{fmtDate(i.due_date)}</td>
      <td className="px-2.5">{can.edit ? <select className="input !py-1 text-xs" value={i.status} onChange={(e) => item.mutate({ id: i.id, body: { status: e.target.value } })}>
        {(i.kind === "goal" ? [["open", "Open"], ["met", "Met"], ["not_met", "Not met"]] : [["open", "Open"], ["done", "Done"]]).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
        : <span className="capitalize">{String(i.status).replace("_", " ")}</span>}</td>
      <td className="px-2.5">{can.edit && p.status === "draft" && <button className="icon-btn" title="Remove" onClick={() => delItem.mutate(i.id)}><Trash2 className="w-3.5 h-3.5 text-gray-400" /></button>}</td>
    </tr>);
  const table = (rows: Row[], title: string) => (
    <div className="card !p-0 overflow-x-auto mb-3"><div className="px-3 py-2 border-b border-surface-border font-semibold text-gray-700 text-sm">{title}</div>
      <table className="w-full text-xs"><thead className="bg-surface text-gray-400 text-left text-[10px] uppercase tracking-wide"><tr><th className="px-2.5 py-1.5">What</th><th className="px-2.5">Measure</th><th className="px-2.5">Owner</th><th className="px-2.5">Due</th><th className="px-2.5">Status</th><th /></tr></thead>
        <tbody>{rows.length ? rows.map(line) : <tr><td colSpan={6} className="text-center py-4 text-gray-400">None yet.</td></tr>}</tbody></table></div>);
  return (
    <Modal size="screen" title={`${p.plan_number} · ${p.scope === "self" ? "Your improvement plan" : p.employee_name}`} subtitle={`${fmtDate(p.start_date)} to ${fmtDate(p.end_date)} · ${STATUS_LABEL[p.status]}${p.outcome_label ? " · " + p.outcome_label : ""} · manager ${p.manager_name || "—"} · HR ${p.hr_owner_name || "—"}`} onClose={onClose}
      footer={<>{can.start && <button className="btn-primary disabled:opacity-50" disabled={start.isPending} onClick={() => { if (window.confirm("Start the plan? The employee is told and can read it.")) start.mutate(); }}>Start the plan</button>}<button className="btn-secondary" onClick={onClose}>Close</button></>}>
      <div className="grid grid-cols-1 xl:grid-cols-[1fr_24rem] gap-4">
        <div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-3 text-xs">
            <div className="card !p-3"><div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold mb-1">Why</div><div className="whitespace-pre-wrap text-gray-700">{p.reason}</div></div>
            <div className="card !p-3"><div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold mb-1">What good looks like</div><div className="whitespace-pre-wrap text-gray-700">{p.expectations || "—"}</div></div>
          </div>
          {table(goals, "Goals")}{table(support, "Support the company gives")}
          {can.edit && <div className="mb-3">{newItems.length ? <><ItemsGrid rows={newItems} setRows={setNewItems} /><button className="btn-primary !py-1 text-xs mt-2" disabled={addItems.isPending} onClick={() => addItems.mutate()}>Save new lines</button></>
            : <button className="btn-secondary !py-1 !px-2 text-xs inline-flex items-center gap-1" onClick={() => setNewItems([{ ...BLANK }])}><Plus className="w-3 h-3" />Add lines</button>}</div>}
          <div className="card !p-0 overflow-x-auto"><div className="px-3 py-2 border-b border-surface-border font-semibold text-gray-700 text-sm">Check-ins</div>
            <table className="w-full text-xs"><thead className="bg-surface text-gray-400 text-left text-[10px] uppercase tracking-wide"><tr><th className="px-2.5 py-1.5">Due</th><th className="px-2.5">Held</th><th className="px-2.5">How it is going</th><th className="px-2.5">Notes</th><th className="px-2.5">By</th><th /></tr></thead>
              <tbody>{(p.checkins || []).map((c: Row) => (
                <tr key={c.id} className="border-t border-surface-border align-top"><td className="px-2.5 py-1.5">{fmtDate(c.due_on)}</td><td className="px-2.5">{fmtDate(c.held_on)}</td><td className="px-2.5">{RATING[c.rating] || ""}</td>
                  <td className="px-2.5 whitespace-pre-wrap max-w-[24rem]">{c.notes}</td><td className="px-2.5">{c.by_name}</td>
                  <td className="px-2.5">{can.checkin && <button className="btn-secondary !py-0.5 !px-2 text-[11px]" onClick={() => setCk({ ...c, held_on: c.held_on || todayISO(), rating: c.rating || "some_progress", notes: c.notes || "" })}>{c.held_on ? "Edit" : "Record"}</button>}</td></tr>))}
                {!(p.checkins || []).length && <tr><td colSpan={6} className="text-center py-4 text-gray-400">Check-in dates appear when HR starts the plan.</td></tr>}</tbody></table></div>
        </div>
        <div className="space-y-3">
          {p.scope === "self" && (
            <div className="card !p-3 text-xs">
              <div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold mb-2">Your response</div>
              {p.ack_status ? <div>You {p.ack_status === "acknowledged" ? "acknowledged the plan" : "said you disagree"} on {fmtDateTime(p.ack_at)}.{p.ack_comment && <div className="text-gray-600 mt-1">{p.ack_comment}</div>}</div> : can.ack ? (<>
                <Field label="Your response"><Select value={ack.status} onChange={(e) => setAck({ ...ack, status: e.target.value })}><option value="acknowledged">I have read this plan</option><option value="disagreed">I disagree with this plan</option></Select></Field>
                <p className="text-[11px] text-gray-500 mb-1">Your response is permanent and goes on the record. If you disagree, say what with; HR reads it.</p>
                <Field label={ack.status === "disagreed" ? <>What you disagree with<Req /></> : "Comment (optional)"}><Textarea rows={3} maxLength={4000} value={ack.comment} onChange={(e) => setAck({ ...ack, comment: e.target.value })} /></Field>
                <FieldError msg={ack.status === "disagreed" && ack.comment ? ackErrs.comment : null} />
                <button className="btn-primary w-full mt-2 disabled:opacity-50" disabled={doAck.isPending || !!ackErrs.comment} title={ackErrs.comment || ""} onClick={() => run(ackErrs, () => doAck.mutate())}>Send</button></>) : null}
            </div>)}
          {p.scope !== "self" && p.ack_status && <div className="card !p-3 text-xs"><b>Employee:</b> {p.ack_status === "acknowledged" ? "acknowledged" : "disagrees"} ({fmtDateTime(p.ack_at)}){p.ack_comment && <div className="text-gray-600 mt-1">{p.ack_comment}</div>}</div>}
          {hrActions && <div className="text-[11px] font-semibold uppercase tracking-wide text-primary-700 pt-1">HR actions</div>}
          {(can.close || can.extend) && (
            <div className="card !p-3 text-xs">
              <div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold mb-2">Outcome (HR)</div>
              <Field label="Outcome"><Select value={oc.outcome} onChange={(e) => setOc({ ...oc, outcome: e.target.value })}><option value="successful">Successful: close the plan</option><option value="extended">Extend the plan</option><option value="exit">Not successful: refer to the exit process</option></Select></Field>
              {oc.outcome === "extended" && <><Field label={<>New end date<Req /></>} hint={`After the current end date, ${fmtDate(p.end_date)}.`}><Input type="date" min={p.end_date ? addDays(p.end_date, 1) : undefined} value={oc.new_end_date} onChange={(e) => setOc({ ...oc, new_end_date: e.target.value })} /></Field>
                <FieldError msg={oc.new_end_date ? ocErrs.new_end_date : null} /></>}
              <Field label={<>Reason (goes on the record)<Req /></>}><Textarea rows={3} maxLength={4000} value={oc.note} onChange={(e) => setOc({ ...oc, note: e.target.value })} /></Field>
              <FieldError msg={oc.note ? ocErrs.note : null} />
              {oc.outcome === "exit" && <p className="text-[11px] text-amber-700 mt-1">This records the outcome only. Employment does not end automatically; start an exit case yourself if the company decides to separate.</p>}
              <button className="btn-primary w-full mt-2 disabled:opacity-50" disabled={outcome.isPending || !oc.note.trim()} onClick={() => run(ocErrs, () => outcome.mutate())}>Record outcome</button>
            </div>)}
          {can.link_exit && (
            <div className="card !p-3 text-xs">
              <div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold mb-2">Exit process</div>
              {p.exit_case_id ? <div>Exit case linked. <a className="underline text-primary-600" href={`/exits/${p.exit_case_id}`}>Open it</a></div> : (<>
                <p className="mb-2">The plan was not successful. If the company decides to separate, start the exit case (choose the type and the real last working day). Nothing happens until you do.</p>
                <button className="btn-secondary w-full" onClick={() => setExitOpen(true)}>Start an exit case</button></>)}
            </div>)}
          {can.cancel && (
            <div className="card !p-3 text-xs"><div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold mb-2">Cancel the plan</div>
              <Textarea rows={2} maxLength={600} value={cancelReason} onChange={(e) => setCancelReason(e.target.value)} placeholder="Reason (goes on the record)" aria-label="Reason for cancelling" />
              <FieldError msg={cancelReason ? cancelErrs.reason : null} />
              <button className="btn-secondary w-full mt-2 !text-red-600 disabled:opacity-50" disabled={!cancelReason.trim() || cancel.isPending} onClick={() => run(cancelErrs, () => { if (window.confirm("Cancel this improvement plan? This cannot be undone.")) cancel.mutate(); })}>Cancel plan</button></div>)}
          {p.audit && <div className="card !p-3 text-xs"><div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold mb-2">Record</div>
            {(p.audit as Row[]).map((a) => <div key={a.id} className="border-t border-surface-border py-1"><span className="text-gray-400">{fmtDateTime(a.at)} · {a.by_name}</span><div>{a.action}: {a.detail}</div></div>)}</div>}
        </div>
      </div>
      {ck && (() => {
        const ckErrs = collect({
          held_on: v.required(ck.held_on, "Held on") || v.notFuture(ck.held_on, "Held on") || (p.start_date && ck.held_on < p.start_date ? `Held on cannot be before the plan started (${fmtDate(p.start_date)})` : null),
          notes: v.required(String(ck.notes || "").trim(), "What was discussed") || v.maxLen(ck.notes, 4000, "Notes"),
        });
        return (
        <Modal title={`Check-in due ${fmtDate(ck.due_on)}`} onClose={() => setCk(null)} size="lg"
          footer={<><button className="btn-secondary" onClick={() => setCk(null)}>Cancel</button><button className="btn-primary disabled:opacity-50" disabled={checkin.isPending || !ck.notes.trim()} onClick={() => run(ckErrs, () => checkin.mutate())}>Save</button></>}>
          <FormGrid>
            <div><Field label={<>Held on<Req /></>} hint="The day the review actually took place."><Input type="date" min={p.start_date} max={isoToday()} value={ck.held_on} onChange={(e) => setCk({ ...ck, held_on: e.target.value })} /></Field><FieldError msg={ckErrs.held_on} /></div>
            <Field label={<>How it is going<Req /></>} hint="Your honest read of progress since the last review. It feeds the final outcome."><Select value={ck.rating} onChange={(e) => setCk({ ...ck, rating: e.target.value })}>{Object.entries(RATING).map(([k, label]) => <option key={k} value={k}>{label}</option>)}</Select></Field>
            <div className="sm:col-span-2"><Field label={<>What was discussed and agreed<Req /></>} hint="Include what the employee said. Up to 4000 characters."><Textarea rows={5} maxLength={4000} value={ck.notes} onChange={(e) => setCk({ ...ck, notes: e.target.value })} /></Field></div>
          </FormGrid>
        </Modal>);
      })()}
      {exitOpen && <StartExitModal employeeId={p.employee_id} lockEmployee onClose={() => setExitOpen(false)} onCreated={(c: any) => { setExitOpen(false); link.mutate(c.id); }} />}
    </Modal>
  );
}
