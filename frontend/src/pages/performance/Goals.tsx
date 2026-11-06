import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import toast from "react-hot-toast";
import { err, fmtDate, fmtDateTime } from "../hiring/api";
import { Col, empLabel, Field, FirstStep, Grid, Input, Modal, Note, Pill, ReasonDialog, Select, Textarea, useEmployees } from "../hiring/ui";
import { FormGrid, FormSection } from "../../components/Modal";
import { collect, FieldError, firstError, Req, v } from "../../lib/validators";
import { perfApi, Row } from "./api";

const SCOPES: Record<string, string> = { mine: "My goals", team: "My team's goals", all: "Everyone" };

export default function Goals({ info }: { info: Row }) {
  const qc = useQueryClient();
  const cycles = useQuery({ queryKey: ["perf", "goal-cycles"], queryFn: perfApi.goalCycles });
  const [cycleId, setCycleId] = useState("");
  const [scope, setScope] = useState("mine");
  const [edit, setEdit] = useState<Row | { employee_id?: string } | null>(null);
  const [checkin, setCheckin] = useState<Row | null>(null);
  const [sendBack, setSendBack] = useState<string | null>(null);
  const [newCycle, setNewCycle] = useState(false);
  const [library, setLibrary] = useState(false);
  useEffect(() => { if (!cycleId && cycles.data?.length) setCycleId((cycles.data.find((c) => c.status === "open") || cycles.data[0]).id); }, [cycles.data, cycleId]);
  const cycle = (cycles.data || []).find((c) => c.id === cycleId);
  const goals = useQuery({ queryKey: ["perf", "goals", cycleId, scope], queryFn: () => perfApi.goals(cycleId, scope), enabled: !!cycleId });
  const refresh = () => qc.invalidateQueries({ queryKey: ["perf"] });
  const scopes = Object.keys(SCOPES).filter((s) => s === "mine" || (s === "team" && info.has_team) || (s === "all" && info.is_hr));

  const act = useMutation({
    mutationFn: (v: { action: "submit" | "approve" | "return"; employee_id: string; reason?: string }) => perfApi.actGoals(v.action, { cycle_id: cycleId, employee_id: v.employee_id, reason: v.reason }),
    onSuccess: (r) => { toast.success(r.status === "approved" ? `${r.goals} goal(s) approved.` : r.status === "proposed" ? `${r.goals} goal(s) sent to the manager for approval.` : `${r.goals} goal(s) sent back.`); refresh(); setSendBack(null); },
    onError: (e) => toast.error(err(e), { duration: 7000 }),
  });
  const del = useMutation({ mutationFn: perfApi.deleteGoal, onSuccess: () => { toast.success("Goal deleted."); refresh(); }, onError: (e) => toast.error(err(e)) });

  const people = useMemo(() => {
    const m = new Map<string, { id: string; name: string; relation: string; goals: Row[] }>();
    (goals.data || []).forEach((g) => { if (!m.has(g.employee_id)) m.set(g.employee_id, { id: g.employee_id, name: g.employee_name, relation: g.relation, goals: [] }); m.get(g.employee_id)!.goals.push(g); });
    return [...m.values()];
  }, [goals.data]);

  const cols: Col<Row>[] = [
    { k: "employee_name", label: "Employee", hide: scope === "mine" },
    { k: "title", label: "Goal", render: (g) => <span className="font-medium text-gray-800">{g.title}</span> },
    { k: "measure", label: "Measure" }, { k: "target", label: "Target" },
    { k: "weight", label: "Weight %", align: "right" },
    { k: "due_date", label: "Due", render: (g) => fmtDate(g.due_date) },
    { k: "progress_pct", label: "Progress", align: "right", render: (g) => (g.status === "approved" ? `${g.progress_pct}%` : ""), csv: (g) => g.progress_pct },
    { k: "status", label: "Status", render: (g) => <Pill s={g.status} label={g.status === "proposed" ? "With manager" : g.status === "returned" ? "Sent back" : undefined} />, csv: (g) => g.status },
    { k: "status_reason", label: "Manager's note" },
    { k: "_a", label: "Actions", csv: () => "", render: (g) => (
      <div className="flex gap-1" onClick={(e) => e.stopPropagation()}>
        {(["draft", "returned"].includes(g.status) || (g.status === "proposed" && g.relation !== "self")) && <button className="btn-secondary !py-0.5 !px-2 text-[11px]" onClick={() => setEdit(g)}>Edit</button>}
        {["draft", "returned"].includes(g.status) && <button className="btn-secondary !py-0.5 !px-2 text-[11px]" onClick={() => { if (window.confirm("Delete this goal?")) del.mutate(g.id); }}>Delete</button>}
        {g.status === "approved" && <button className="btn-primary !py-0.5 !px-2 text-[11px]" onClick={() => setCheckin(g)}>Check in</button>}
      </div>) },
  ];

  if (!cycles.isLoading && !(cycles.data || []).length) {
    return (<>
      <FirstStep title="No goal cycle yet">
        {info.is_hr ? "A goal cycle is the period goals are set for, for example a financial year or a quarter. Create one first; then everyone can add goals."
          : "HR has not opened a goal cycle yet. Goals can be added once HR creates one."}
        {info.is_hr && <div className="mt-4"><button className="btn-primary inline-flex items-center gap-1.5" onClick={() => setNewCycle(true)}><Plus className="w-4 h-4" />Create a goal cycle</button></div>}
      </FirstStep>
      {newCycle && <GoalCycleForm onClose={() => setNewCycle(false)} />}
    </>);
  }

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <select className="input !py-1.5 text-sm !w-64" value={cycleId} onChange={(e) => setCycleId(e.target.value)}>
          {(cycles.data || []).map((c) => <option key={c.id} value={c.id}>{c.name} ({fmtDate(c.period_start)} to {fmtDate(c.period_end)}){c.status === "closed" ? " · closed" : ""}</option>)}</select>
        <div className="inline-flex rounded-lg border border-surface-border overflow-hidden">
          {scopes.map((s) => <button key={s} className={`px-3 py-1.5 text-xs ${scope === s ? "bg-primary-50 text-primary-600 font-medium" : "text-gray-500"}`} onClick={() => setScope(s)}>{SCOPES[s]}</button>)}</div>
        <span className="ml-auto" />
        {info.is_hr && <><button className="btn-secondary !py-1.5 text-xs" onClick={() => setLibrary(true)}>Goal library</button>
          <button className="btn-secondary !py-1.5 text-xs" onClick={() => setNewCycle(true)}>New goal cycle</button></>}
        {cycle?.status === "open" && (scope === "mine" ? info.linked : true) && <button className="btn-primary !py-1.5 text-xs inline-flex items-center gap-1" onClick={() => setEdit({})}><Plus className="w-3.5 h-3.5" />Add a goal</button>}
      </div>
      {scope === "mine" && !info.linked && <Note tone="warn">Your login is not linked to an employee record, so you cannot hold goals. Ask HR to link it.</Note>}

      {people.map((p) => {
        const total = Math.round(p.goals.reduce((s, g) => s + Number(g.weight || 0), 0) * 100) / 100;
        const drafts = p.goals.some((g) => ["draft", "returned"].includes(g.status)), waiting = p.goals.some((g) => g.status === "proposed");
        const canApprove = p.relation !== "self";
        return (
          <div key={p.id} className="flex flex-wrap items-center gap-2 text-xs bg-white border border-surface-border rounded-lg px-3 py-2 mb-2">
            <b className="text-gray-800">{scope === "mine" ? "Your goals" : p.name}</b>
            <span className={total === 100 ? "text-green-600" : "text-amber-700"}>Weights add up to {total} of 100</span>
            <span className="text-gray-400">{p.goals.length} goal(s)</span>
            <span className="ml-auto" />
            {drafts && <button className="btn-primary !py-0.5 !px-2 text-[11px] disabled:opacity-50" disabled={total !== 100 || act.isPending} title={total !== 100 ? "Weights must add up to 100 first" : ""} onClick={() => act.mutate({ action: "submit", employee_id: p.id })}>{p.relation === "self" ? "Send for approval" : "Set these goals"}</button>}
            {waiting && canApprove && <button className="btn-primary !py-0.5 !px-2 text-[11px] disabled:opacity-50" disabled={act.isPending} onClick={() => act.mutate({ action: "approve", employee_id: p.id })}>Approve goals</button>}
            {canApprove && p.goals.some((g) => ["proposed", "approved"].includes(g.status)) && <button className="btn-secondary !py-0.5 !px-2 text-[11px]" onClick={() => setSendBack(p.id)}>Send back</button>}
            {scope !== "mine" && cycle?.status === "open" && <button className="btn-secondary !py-0.5 !px-2 text-[11px]" onClick={() => setEdit({ employee_id: p.id })}>Add a goal for them</button>}
          </div>);
      })}

      <Grid rows={goals.data || []} cols={cols} keyOf={(g) => g.id} loading={goals.isLoading} exportName="goals.csv" title={SCOPES[scope]}
        empty={scope === "mine" ? "You have no goals in this cycle. Press Add a goal. Weights must add up to 100 before you send them for approval."
          : scope === "team" ? "Nobody in your team has goals in this cycle yet. Press Add a goal to set one for a team member." : "No goals in this cycle yet."} />

      {edit && <GoalForm goal={"id" in edit ? (edit as Row) : null} cycleId={cycleId} cycle={cycle} allGoals={goals.data || []} forEmployee={(edit as Row).employee_id} scope={scope} onClose={() => setEdit(null)} />}
      {checkin && <CheckinModal goal={checkin} onClose={() => setCheckin(null)} />}
      {newCycle && <GoalCycleForm onClose={() => setNewCycle(false)} />}
      {library && <LibraryModal onClose={() => setLibrary(false)} />}
      {sendBack && <ReasonDialog title="Send the goals back" label="What should change" hint="The employee sees this note and can edit and resend." confirm="Send back"
        onClose={() => setSendBack(null)} onConfirm={(reason) => act.mutate({ action: "return", employee_id: sendBack, reason })} />}
    </div>
  );
}

function GoalForm({ goal, cycleId, cycle, allGoals, forEmployee, scope, onClose }: { goal: Row | null; cycleId: string; cycle?: Row; allGoals: Row[]; forEmployee?: string; scope: string; onClose: () => void }) {
  const qc = useQueryClient();
  const employees = useEmployees();
  const lib = useQuery({ queryKey: ["perf", "library"], queryFn: perfApi.library });
  const pickFor = !goal && scope !== "mine" && !forEmployee;
  // The person's other goals in this cycle, for the running weight total.
  const owner = goal?.employee_id || forEmployee || (scope === "mine" ? allGoals.find((g) => g.relation === "self")?.employee_id : "");
  const othersTotal = (emp?: string) => Math.round(allGoals.filter((g) => (emp ? g.employee_id === emp : scope === "mine" && g.relation === "self") && g.id !== goal?.id)
    .reduce((s, g) => s + Number(g.weight || 0), 0) * 100) / 100;
  const [f, setF] = useState<Row>(() => {
    if (goal) return { ...goal };
    const left = 100 - othersTotal(owner || undefined);
    return { employee_id: forEmployee || "", due_date: cycle?.period_end || "", weight: left > 0 && left < 100 ? left : "" };
  });
  const [tried, setTried] = useState(false);
  const set = (k: string, val: any) => setF((p) => ({ ...p, [k]: val }));
  const others = othersTotal((pickFor ? f.employee_id : owner) || undefined);
  const total = Math.round((others + (Number(f.weight) || 0)) * 100) / 100;
  const errs = collect({
    employee_id: pickFor ? v.required(f.employee_id, "Employee") : null,
    title: v.required(String(f.title || "").trim(), "Goal") || v.maxLen(String(f.title || "").trim(), 300, "Goal"),
    measure: v.maxLen(f.measure, 300, "Measure"),
    target: v.maxLen(f.target, 300, "Target"),
    weight: v.required(f.weight, "Weight") || v.range(f.weight, 0.01, 100, "Weight") || (total > 100 ? `This person's goal weights would add up to ${total}. Keep them at 100 or below.` : null),
    due_date: cycle && f.due_date && (f.due_date < cycle.period_start || f.due_date > cycle.period_end) ? `Due date must fall inside the cycle (${fmtDate(cycle.period_start)} to ${fmtDate(cycle.period_end)})` : null,
  });
  const show = (k: string) => (tried || !["employee_id", "title", "weight"].includes(k) || (k === "weight" && f.weight !== "") ? errs[k] : null);
  const save = useMutation({
    mutationFn: () => {
      const body = { title: String(f.title).trim(), measure: f.measure, target: f.target, weight: f.weight, due_date: f.due_date || null };
      return goal ? perfApi.updateGoal(goal.id, body) : perfApi.createGoal({ ...body, cycle_id: cycleId, employee_id: scope === "mine" ? undefined : f.employee_id });
    },
    onSuccess: () => { toast.success("Goal saved as a draft."); qc.invalidateQueries({ queryKey: ["perf"] }); onClose(); },
    onError: (e) => toast.error(err(e)),
  });
  const onSave = () => { setTried(true); if (Object.keys(errs).length) { toast.error(firstError(errs)); return; } save.mutate(); };
  return (
    <Modal title={goal ? "Edit goal" : "Add a goal"} subtitle="A person's goal weights must add up to 100 before they are sent for approval." onClose={onClose}
      footer={<><button className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary disabled:opacity-50" disabled={save.isPending} onClick={onSave}>Save</button></>}>
      {(pickFor || (!goal && !!(lib.data || []).length)) && (
        <FormSection title="Start">
          <FormGrid>
            {pickFor && <div><Field label={<>For<Req /></>}><Select value={f.employee_id} onChange={(e) => set("employee_id", e.target.value)}>
              <option value="">Pick an employee</option>{employees.map((e) => <option key={e.id} value={e.id}>{empLabel(e)}</option>)}</Select></Field><FieldError msg={show("employee_id")} /></div>}
            {!goal && !!(lib.data || []).length && <Field label="Start from the goal library" hint="Optional. Fills the fields below; you can still change them.">
              <Select value="" onChange={(e) => { const l = lib.data!.find((x) => x.id === e.target.value); if (l) setF((p) => ({ ...p, title: l.title, measure: l.measure, target: l.target, weight: l.weight ?? p.weight })); }}>
                <option value="">Pick a library goal</option>{lib.data!.map((l) => <option key={l.id} value={l.id}>{l.designation ? `${l.designation}: ` : ""}{l.title}</option>)}</Select></Field>}
          </FormGrid>
        </FormSection>)}
      <FormSection title="Goal">
        <FormGrid cols={4}>
          <div className="sm:col-span-2 lg:col-span-4"><Field label={<>Goal<Req /></>}><Input maxLength={300} value={f.title || ""} onChange={(e) => set("title", e.target.value)} placeholder="Cut invoice errors in the Bengaluru branch" /></Field><FieldError msg={show("title")} /></div>
          <div className="sm:col-span-2"><Field label="Measure" hint="How it will be measured."><Input maxLength={300} value={f.measure || ""} onChange={(e) => set("measure", e.target.value)} placeholder="Invoices sent back by customers each month" /></Field></div>
          <div className="sm:col-span-2"><Field label="Target" hint="The number or outcome that counts as done."><Input maxLength={300} value={f.target || ""} onChange={(e) => set("target", e.target.value)} placeholder="Below 2% by December, or $500K revenue" /></Field></div>
          <div><Field label={<>Weight %<Req /></>} hint="Share of this person's total review."><Input type="number" min={0.01} max={100} step="any" value={f.weight ?? ""} onChange={(e) => set("weight", e.target.value)} /></Field>
            <span className={`block text-[11px] mt-0.5 ${total === 100 ? "text-green-600" : total > 100 ? "text-red-600" : "text-amber-700"}`}>With this goal: {total} of 100{others ? ` (other goals ${others})` : ""}</span>
            <FieldError msg={show("weight")} /></div>
          <div><Field label="Due date" hint={cycle ? `Within ${fmtDate(cycle.period_start)} to ${fmtDate(cycle.period_end)}. Blank means the cycle end.` : "Blank means the cycle end."}><Input type="date" min={cycle?.period_start} max={cycle?.period_end} value={f.due_date || ""} onChange={(e) => set("due_date", e.target.value)} /></Field><FieldError msg={show("due_date")} /></div>
        </FormGrid>
      </FormSection>
    </Modal>
  );
}

function CheckinModal({ goal, onClose }: { goal: Row; onClose: () => void }) {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["perf", "checkins", goal.id], queryFn: () => perfApi.checkins(goal.id) });
  const [pct, setPct] = useState(String(goal.progress_pct ?? 0));
  const [comment, setComment] = useState("");
  const save = useMutation({
    mutationFn: () => perfApi.addCheckin(goal.id, { progress_pct: pct, comment }),
    onSuccess: () => { toast.success(`Check-in saved. Progress is now ${pct}%.`); setComment(""); qc.invalidateQueries({ queryKey: ["perf"] }); },
    onError: (e) => toast.error(err(e)),
  });
  return (
    <Modal title="Check in" subtitle={`${goal.title} · weight ${goal.weight}%${goal.target ? " · target " + goal.target : ""}`} size="lg" onClose={onClose}
      footer={<><button className="btn-secondary" onClick={onClose}>Close</button><button className="btn-primary disabled:opacity-50" disabled={save.isPending || pct === ""} onClick={() => save.mutate()}>Save check-in</button></>}>
      <div className="grid grid-cols-1 sm:grid-cols-[10rem_1fr] gap-3 mb-4">
        <Field label="Progress % *"><Input type="number" min={0} max={100} value={pct} onChange={(e) => setPct(e.target.value)} /></Field>
        <Field label="Comment"><Textarea rows={2} value={comment} onChange={(e) => setComment(e.target.value)} /></Field>
      </div>
      <div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold mb-1">Earlier check-ins</div>
      {!(list.data || []).length ? <div className="text-sm text-gray-400">None yet.</div>
        : list.data!.map((c) => <div key={c.id} className="flex gap-3 border-b border-surface-border py-1.5 text-xs"><span className="w-32 shrink-0 text-gray-400">{fmtDateTime(c.at)}</span><b className="w-12 tabular-nums">{c.progress_pct}%</b><span className="text-gray-700 flex-1">{c.comment}</span><span className="text-gray-400">{c.by_name} ({c.by_role})</span></div>)}
    </Modal>
  );
}

/** The current calendar year: name and dates, as a starting suggestion. */
function nextFy() {
  const y = new Date().getFullYear();
  return { name: `${y} goals`, period_start: `${y}-01-01`, period_end: `${y}-12-31` };
}

export function GoalCycleForm({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const existing = useQuery({ queryKey: ["perf", "goal-cycles"], queryFn: perfApi.goalCycles });
  const [f, setF] = useState<Row>(nextFy);
  const [tried, setTried] = useState(false);
  const name = String(f.name || "").trim();
  const errs = collect({
    name: v.required(name, "Name") || v.maxLen(name, 120, "Name")
      || ((existing.data || []).some((c) => String(c.name).trim().toLowerCase() === name.toLowerCase()) ? "A goal cycle with this name already exists" : null),
    period_start: v.required(f.period_start, "Start date"),
    period_end: v.required(f.period_end, "End date") || (f.period_start && f.period_end && f.period_end <= f.period_start ? "The cycle must end after it starts" : null),
  });
  const show = (k: string) => (tried || f[k] ? errs[k] : null);
  const save = useMutation({ mutationFn: () => perfApi.createGoalCycle({ ...f, name }), onSuccess: (c) => { toast.success(`Goal cycle ${c.name} is open.`); qc.invalidateQueries({ queryKey: ["perf"] }); onClose(); }, onError: (e) => toast.error(err(e)) });
  const onSave = () => { setTried(true); if (Object.keys(errs).length) { toast.error(firstError(errs)); return; } save.mutate(); };
  return (
    <Modal title="Create a goal cycle" subtitle="The period goals are set for: a financial year, a half or a quarter." size="lg" onClose={onClose}
      footer={<><button className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary disabled:opacity-50" disabled={save.isPending} onClick={onSave}>Create</button></>}>
      <FormGrid cols={3}>
        <div><Field label={<>Name<Req /></>} hint="For example FY 2026-27 (April to March), or Q1 FY 2026-27."><Input maxLength={120} placeholder="FY 2026-27" value={f.name || ""} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field><FieldError msg={show("name")} /></div>
        <div><Field label={<>Starts<Req /></>}><Input type="date" value={f.period_start || ""} onChange={(e) => setF({ ...f, period_start: e.target.value })} /></Field><FieldError msg={show("period_start")} /></div>
        <div><Field label={<>Ends<Req /></>}><Input type="date" min={f.period_start || undefined} value={f.period_end || ""} onChange={(e) => setF({ ...f, period_end: e.target.value })} /></Field><FieldError msg={show("period_end")} /></div>
      </FormGrid>
    </Modal>
  );
}

function LibraryModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["perf", "library"], queryFn: perfApi.library });
  const [f, setF] = useState<Row>({});
  const [tried, setTried] = useState(false);
  const refresh = () => qc.invalidateQueries({ queryKey: ["perf", "library"] });
  const errs = collect({
    title: v.required(String(f.title || "").trim(), "Goal") || v.maxLen(String(f.title || "").trim(), 300, "Goal"),
    designation: v.maxLen(f.designation, 120, "Designation"),
    weight: v.range(f.weight, 0.01, 100, "Weight"),
  });
  const show = (k: string) => (tried || k !== "title" ? errs[k] : null);
  const add = useMutation({ mutationFn: () => perfApi.addLibrary({ ...f, title: String(f.title).trim(), weight: f.weight === "" ? null : f.weight }), onSuccess: () => { toast.success("Added to the library."); setF({}); setTried(false); refresh(); }, onError: (e) => toast.error(err(e)) });
  const onAdd = () => { setTried(true); if (Object.keys(errs).length) { toast.error(firstError(errs)); return; } add.mutate(); };
  const rm = useMutation({ mutationFn: perfApi.removeLibrary, onSuccess: refresh, onError: (e) => toast.error(err(e)) });
  return (
    <Modal title="Goal library" subtitle="Ready-made goals people can start from. Leave the designation empty to offer a goal to everyone." onClose={onClose} footer={<button className="btn-primary" onClick={onClose}>Close</button>}>
      <FormSection title="Add a library goal">
        <FormGrid cols={4}>
          <div className="sm:col-span-2"><Field label={<>Goal<Req /></>}><Input maxLength={300} value={f.title || ""} onChange={(e) => setF({ ...f, title: e.target.value })} /></Field><FieldError msg={show("title")} /></div>
          <div><Field label="Designation" hint="Leave blank to offer it to everyone."><Input maxLength={120} value={f.designation || ""} onChange={(e) => setF({ ...f, designation: e.target.value })} /></Field></div>
          <div><Field label="Suggested weight %" hint="People can change it when they add the goal."><Input type="number" min={0.01} max={100} step="any" value={f.weight ?? ""} onChange={(e) => setF({ ...f, weight: e.target.value })} /></Field><FieldError msg={show("weight")} /></div>
          <div className="sm:col-span-2"><Field label="Measure" hint="How it is measured, in words every team reads the same way."><Input maxLength={300} value={f.measure || ""} onChange={(e) => setF({ ...f, measure: e.target.value })} /></Field></div>
          <div className="sm:col-span-2"><Field label="Target"><Input maxLength={300} value={f.target || ""} onChange={(e) => setF({ ...f, target: e.target.value })} /></Field></div>
        </FormGrid>
        <div className="flex justify-end mt-3"><button className="btn-primary disabled:opacity-50" disabled={add.isPending} onClick={onAdd}>Add to the library</button></div>
      </FormSection>
      <Grid rows={data || []} keyOf={(r) => r.id} empty="The library is empty. Add the first goal above." exportName="goal-library.csv"
        cols={[{ k: "designation", label: "Designation", render: (r) => r.designation || "Everyone" }, { k: "title", label: "Goal" }, { k: "measure", label: "Measure" }, { k: "target", label: "Target" },
          { k: "weight", label: "Weight %", align: "right" }, { k: "_a", label: "", csv: () => "", render: (r) => <button className="btn-secondary !py-0.5 !px-2 text-[11px]" onClick={() => rm.mutate(r.id)}>Remove</button> }]} />
    </Modal>
  );
}
