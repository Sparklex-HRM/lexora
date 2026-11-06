import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, ListTree, Plus, Rows3, Trash2 } from "lucide-react";
import toast from "react-hot-toast";
import { err, fmtDateTime } from "../hiring/api";
import { Col, empLabel, Field, FirstStep, Grid, Input, Modal, Note, Select, Stat, Textarea, useEmployees } from "../hiring/ui";
import { useAuthStore } from "../../store/authStore";
import { FormGrid, FormSection } from "../../components/Modal";
import { collect, FieldError, firstError, Req, v } from "../../lib/validators";
import type { Row } from "./api";
import { BAND_CLASS, BAND_LABEL, okrApi, Progress } from "./api_g4";

/* G4: objectives & key results. Progress is never typed — it rolls up from key results (and up the
   alignment tree). Company / department objectives are open to everyone; individual ones to the
   owner, their manager and HR (decided by the server). */

const SCOPES: Record<string, string> = { all: "Everything I can see", mine: "Mine", team: "My team" };
const LEVEL_TONE: Record<string, string> = { company: "badge-purple", department: "badge-teal", team: "badge-amber", individual: "badge-green" };

const krValue = (k: Row) => (k.metric_type === "milestone"
  ? `${(k.milestones || []).filter((m: Row) => m.done).length} of ${(k.milestones || []).length} milestones`
  : `${k.current_value ?? k.start_value}${k.unit === "%" ? "%" : ""} (from ${k.start_value} to ${k.target_value}${k.unit && k.unit !== "%" ? " " + k.unit : k.unit === "%" ? "%" : ""})`);

export default function Okrs() {
  const [cycleId, setCycleId] = useState("");
  const [scope, setScope] = useState("all");
  const [view, setView] = useState<"tree" | "grid">("tree");
  const [newObj, setNewObj] = useState<Row | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["perf", "okrs", cycleId, scope], queryFn: () => okrApi.list(cycleId, scope) });
  const data = q.data;
  if (q.isLoading) return <div className="text-sm text-gray-400">Loading…</div>;
  if (q.isError || !data) return <Note tone="warn">{err(q.error, "Objectives could not be loaded.")}</Note>;
  if (!data.cycles.length) {
    return <FirstStep title="No cycle yet">Objectives belong to a cycle (a quarter, a half or a year). {data.is_hr ? "Create one under Goals, New goal cycle, then come back." : "HR has not opened a cycle yet."}</FirstStep>;
  }
  const cyc = data.cycles.find((c: Row) => c.id === data.cycle_id);
  const objs: Row[] = data.objectives || [];
  const s = data.summary || {};
  const cols: Col<Row>[] = [
    { k: "title", label: "Objective", render: (o) => <span className="font-medium text-gray-800">{o.title}</span> },
    { k: "level", label: "Level", render: (o) => <span className={LEVEL_TONE[o.level]}>{o.level_label}</span>, csv: (o) => o.level_label },
    { k: "owner_name", label: "Owner", render: (o) => o.owner_name || o.department || "Company" },
    { k: "key_results", label: "Key results", align: "right", render: (o) => (o.key_results || []).length, csv: (o) => (o.key_results || []).length },
    { k: "weight", label: "Weight", align: "right" },
    { k: "progress_pct", label: "Progress", render: (o) => <Progress pct={o.progress_pct} />, csv: (o) => o.progress_pct },
    { k: "confidence", label: "Confidence", render: (o) => (o.confidence_band ? <span className={BAND_CLASS[o.confidence_band]}>{BAND_LABEL[o.confidence_band]} ({o.confidence}/10)</span> : <span className="text-gray-400">No check-in</span>), csv: (o) => o.confidence ?? "" },
    { k: "status", label: "Status" },
    { k: "include_in_review", label: "In review", render: (o) => (o.level === "individual" ? (o.include_in_review ? "Yes" : "No") : ""), csv: (o) => (o.include_in_review ? "yes" : "no") },
  ];
  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <select className="input !py-1.5 text-sm !w-64" value={data.cycle_id || ""} onChange={(e) => setCycleId(e.target.value)}>
          {data.cycles.map((c: Row) => <option key={c.id} value={c.id}>{c.name}{c.status === "closed" ? " · closed" : ""}</option>)}</select>
        <div className="inline-flex rounded-lg border border-surface-border overflow-hidden">
          {Object.entries(SCOPES).map(([k, v]) => <button key={k} className={`px-3 py-1.5 text-xs ${scope === k ? "bg-primary-50 text-primary-600 font-medium" : "text-gray-500"}`} onClick={() => setScope(k)}>{v}</button>)}</div>
        <div className="inline-flex rounded-lg border border-surface-border overflow-hidden">
          <button className={`px-2 py-1.5 text-xs inline-flex items-center gap-1 ${view === "tree" ? "bg-primary-50 text-primary-600" : "text-gray-500"}`} onClick={() => setView("tree")}><ListTree className="w-3.5 h-3.5" />Alignment</button>
          <button className={`px-2 py-1.5 text-xs inline-flex items-center gap-1 ${view === "grid" ? "bg-primary-50 text-primary-600" : "text-gray-500"}`} onClick={() => setView("grid")}><Rows3 className="w-3.5 h-3.5" />Grid</button></div>
        <span className="ml-auto" />
        {cyc?.status === "open" && <button className="btn-primary !py-1.5 text-xs inline-flex items-center gap-1" onClick={() => setNewObj({ level: data.is_hr_head ? "company" : "individual" })}><Plus className="w-3.5 h-3.5" />New objective</button>}
      </div>
      {!data.linked && !data.is_hr && <Note tone="warn">Your login is not linked to an employee record, so you cannot own objectives. Ask HR to link it.</Note>}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 mb-3">
        <Stat label="Objectives" value={s.objectives ?? 0} />
        <Stat label="Average progress" value={`${Math.round(s.avg_progress || 0)}%`} accent />
        <Stat label="On track" value={s.on_track ?? 0} />
        <Stat label="At risk / off track" value={`${s.at_risk ?? 0} / ${s.off_track ?? 0}`} />
        <Stat label="No check-in yet" value={s.no_checkin ?? 0} />
      </div>
      {view === "grid" ? (
        <Grid rows={objs} cols={cols} keyOf={(o) => o.id} exportName="objectives.csv" title="Objectives" onRow={(o) => setOpen(o.id)}
          empty="No objectives in this cycle yet. Press New objective." />
      ) : (
        <div className="card !p-0 overflow-x-auto">
          {!objs.length ? <div className="text-center py-10 text-sm text-gray-400">No objectives in this cycle yet. Start with the company objectives, then align team and individual ones under them.</div> : (
            <table className="w-full text-xs">
              <thead className="bg-surface text-gray-400 text-left text-[10px] uppercase tracking-wide"><tr>
                <th className="px-2.5 py-2">Objective</th><th className="px-2.5">Owner</th><th className="px-2.5">Progress</th><th className="px-2.5">Confidence</th><th className="px-2.5 text-right">KRs</th><th className="px-2.5">Status</th></tr></thead>
              <tbody>{(data.tree as Row[]).map((n) => <TreeRow key={n.id} node={n} depth={0} onOpen={setOpen} />)}</tbody>
            </table>)}
        </div>)}
      {newObj && <ObjectiveForm init={newObj} cycleId={data.cycle_id} objectives={objs} info={data} onClose={() => setNewObj(null)} />}
      {open && <ObjectiveModal id={open} cycleId={data.cycle_id} objectives={objs} onClose={() => setOpen(null)} />}
    </div>
  );
}

function TreeRow({ node, depth, onOpen }: { node: Row; depth: number; onOpen: (id: string) => void }) {
  const [shut, setShut] = useState(false);
  const kids: Row[] = node.children || [];
  return (<>
    <tr className="border-t border-surface-border hover:bg-primary-50/40 cursor-pointer" onClick={() => onOpen(node.id)}>
      <td className="px-2.5 py-1.5" style={{ paddingLeft: `${0.6 + depth * 1.4}rem` }}>
        <span className="inline-flex items-center gap-1">
          {kids.length ? <button className="text-gray-400" onClick={(e) => { e.stopPropagation(); setShut(!shut); }} aria-label={shut ? "Expand" : "Collapse"}>{shut ? <ChevronRight className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}</button> : <span className="w-3.5" />}
          <span className={`${LEVEL_TONE[node.level]} !text-[10px]`}>{node.level_label}</span>
          <span className="font-medium text-gray-800">{node.title}</span>
        </span>
      </td>
      <td className="px-2.5 whitespace-nowrap">{node.owner_name || node.department || "Company"}</td>
      <td className="px-2.5"><Progress pct={node.progress_pct} /></td>
      <td className="px-2.5">{node.confidence_band ? <span className={BAND_CLASS[node.confidence_band]}>{BAND_LABEL[node.confidence_band]}</span> : <span className="text-gray-400">—</span>}</td>
      <td className="px-2.5 text-right tabular-nums">{(node.key_results || []).length}</td>
      <td className="px-2.5 capitalize">{node.status}</td>
    </tr>
    {!shut && kids.map((k) => <TreeRow key={k.id} node={k} depth={depth + 1} onOpen={onOpen} />)}
  </>);
}

const BLANK_KR = { title: "", metric_type: "number", start_value: "", target_value: "", unit: "", weight: 1, milestones_text: "" };
const krBody = (k: Row) => ({ title: k.title, metric_type: k.metric_type, start_value: k.start_value, target_value: k.target_value, unit: k.unit, weight: k.weight || 1,
  milestones: k.metric_type === "milestone" ? String(k.milestones_text || "").split("\n").map((l) => l.trim()).filter(Boolean).map((label) => ({ label, done: false })) : undefined });

/** The server's key-result rules, checked before Save so the person sees which row is wrong. */
function krErrors(rows: Row[]): Record<string, string> {
  const out: Record<string, string> = {};
  rows.forEach((k, i) => {
    const n = `Key result ${i + 1}`;
    const used = String(k.title || "").trim() || String(k.start_value ?? "") !== "" || String(k.target_value ?? "") !== "" || String(k.milestones_text || "").trim();
    if (!used) return;                                   // an empty row is simply skipped
    const e = v.required(String(k.title || "").trim(), `${n}: title`) || v.maxLen(String(k.title).trim(), 300, `${n}: title`)
      || v.range(k.weight === "" ? 1 : k.weight, 0.01, 100, `${n}: weight`)
      || (k.metric_type === "milestone"
        ? (!String(k.milestones_text || "").split("\n").some((l) => l.trim()) ? `${n}: add at least one milestone (one per line)` : null)
        : v.required(k.start_value, `${n}: start value`) || v.required(k.target_value, `${n}: target value`)
          || (Number.isNaN(Number(k.start_value)) || Number.isNaN(Number(k.target_value)) ? `${n}: start and target must be numbers` : null)
          || (Number(k.start_value) === Number(k.target_value) ? `${n}: the target must differ from the start` : null)
          || (k.metric_type === "percent" ? v.range(k.start_value, 0, 100, `${n}: start %`) || v.range(k.target_value, 0, 100, `${n}: target %`) : null)
          || (k.metric_type === "currency" ? v.range(k.start_value, 0, undefined, `${n}: start amount`) || v.range(k.target_value, 0, undefined, `${n}: target amount`) : null));
    if (e) out[`kr${i}`] = e;
  });
  return out;
}

function KrGrid({ rows, setRows }: { rows: Row[]; setRows: (r: Row[]) => void }) {
  const upd = (i: number, k: string, v: any) => setRows(rows.map((r, j) => (j === i ? { ...r, [k]: v } : r)));
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead className="text-[10px] uppercase text-gray-400 text-left"><tr><th className="py-1">Key result *</th><th>Measure</th><th>Start</th><th>Target</th><th>Unit</th><th>Weight</th><th /></tr></thead>
        <tbody>{rows.map((r, i) => (
          <tr key={i} className="border-t border-surface-border align-top">
            <td className="py-1 pr-1 min-w-[14rem]"><Input value={r.title} onChange={(e) => upd(i, "title", e.target.value)} placeholder="Close 10 enterprise deals" /></td>
            <td className="pr-1 w-36"><select className="input !py-1.5 text-xs w-full" value={r.metric_type} onChange={(e) => upd(i, "metric_type", e.target.value)}>
              <option value="number">Number</option><option value="percent">Percentage</option><option value="currency">Amount ($)</option><option value="milestone">Milestones</option></select></td>
            {r.metric_type === "milestone" ? (
              <td colSpan={3} className="pr-1"><Textarea rows={2} value={r.milestones_text} onChange={(e) => upd(i, "milestones_text", e.target.value)} placeholder="One milestone per line" /></td>
            ) : (<>
              <td className="pr-1 w-24"><Input type="number" value={r.start_value} onChange={(e) => upd(i, "start_value", e.target.value)} /></td>
              <td className="pr-1 w-24"><Input type="number" value={r.target_value} onChange={(e) => upd(i, "target_value", e.target.value)} /></td>
              <td className="pr-1 w-20"><Input value={r.unit} onChange={(e) => upd(i, "unit", e.target.value)} placeholder={r.metric_type === "percent" ? "%" : r.metric_type === "currency" ? "$" : "deals"} /></td></>)}
            <td className="pr-1 w-16"><Input type="number" min={0.1} step="0.5" value={r.weight} onChange={(e) => upd(i, "weight", e.target.value)} /></td>
            <td><button className="icon-btn" title="Remove" onClick={() => setRows(rows.filter((_, j) => j !== i))}><Trash2 className="w-3.5 h-3.5 text-gray-400" /></button></td>
          </tr>))}</tbody>
      </table>
      <button className="btn-secondary !py-1 !px-2 text-xs inline-flex items-center gap-1 mt-2" onClick={() => setRows([...rows, { ...BLANK_KR }])}><Plus className="w-3 h-3" />Add a key result</button>
      <p className="text-[11px] text-gray-400 mt-1">A target below the start is fine (for example cut defects from 40 to 10). Weights share out the objective's progress between its key results. Rows left completely empty are skipped.</p>
      {Object.values(krErrors(rows)).map((m) => <FieldError key={m} msg={m} />)}
    </div>
  );
}

function ObjectiveForm({ init, cycleId, objectives, info, onClose }: { init: Row; cycleId: string; objectives: Row[]; info: Row; onClose: () => void }) {
  const qc = useQueryClient();
  const employees = useEmployees();
  const me = useAuthStore((s) => s.user?.employee_id) || "";
  const [f, setF] = useState<Row>({ weight: 100, include_in_review: true, status: "active", ...init });
  const [krs, setKrs] = useState<Row[]>([{ ...BLANK_KR }]);
  const [tried, setTried] = useState(false);
  const set = (k: string, val: any) => setF((p) => ({ ...p, [k]: val }));
  const siblings = objectives.filter((o) => (o.parent_id || "") === (f.parent_id || "") && o.status !== "cancelled" && (f.parent_id || o.level === f.level));
  const siblingTotal = Math.round((siblings.reduce((s, o) => s + Number(o.weight || 0), 0) + (Number(f.weight) || 0)) * 100) / 100;
  const errs = collect({
    title: v.required(String(f.title || "").trim(), "Objective") || v.maxLen(String(f.title || "").trim(), 300, "Objective"),
    department: f.level === "department" ? v.required(String(f.department || "").trim(), "Department") || v.maxLen(f.department, 120, "Department") : null,
    weight: v.required(f.weight, "Weight") || v.range(f.weight, 0.01, 100, "Weight"),
    description: v.maxLen(f.description, 4000, "Why it matters"),
    ...krErrors(krs),
  });
  const show = (k: string) => (tried || k !== "title" ? errs[k] : null);
  const onSave = () => { setTried(true); if (Object.keys(errs).length) { toast.error(firstError(errs)); return; } save.mutate(); };
  const owners = useMemo(() => (info.is_hr ? employees : employees.filter((e: any) => e.id === me || e.manager_id === me)), [employees, info.is_hr, me]);
  const levels = info.is_hr_head ? ["company", "department", "team", "individual"] : ["team", "individual"];
  const save = useMutation({
    mutationFn: () => okrApi.create({ cycle_id: cycleId, level: f.level, status: f.status, title: String(f.title).trim(), description: f.description, parent_id: f.parent_id || null, weight: f.weight,
      include_in_review: f.include_in_review, owner_employee_id: ["individual", "team"].includes(f.level) ? (f.owner_employee_id || undefined) : undefined,
      department: f.level === "department" ? f.department : undefined, key_results: krs.filter((k) => k.title.trim()).map(krBody) }),
    onSuccess: () => { toast.success(f.status === "draft" ? "Objective saved as a draft. Make it active when it is ready." : "Objective saved."); qc.invalidateQueries({ queryKey: ["perf", "okrs"] }); onClose(); },
    onError: (e) => toast.error(err(e), { duration: 7000 }),
  });
  return (
    <Modal title="New objective" subtitle="What you want to achieve this cycle, and the key results that prove it." onClose={onClose}
      footer={<><button className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary disabled:opacity-50" disabled={save.isPending} onClick={onSave}>Save objective</button></>}>
      <FormSection title="Objective">
        <FormGrid cols={4}>
          <div className="sm:col-span-2"><Field label={<>Objective<Req /></>}><Input maxLength={300} value={f.title || ""} onChange={(e) => set("title", e.target.value)} placeholder="Make onboarding effortless for new customers" /></Field><FieldError msg={show("title")} /></div>
          <Field label={<>Level<Req /></>} hint={info.is_hr_head ? undefined : "Company and department objectives are set by HR heads."}><Select value={f.level} onChange={(e) => set("level", e.target.value)}>{levels.map((l) => <option key={l} value={l}>{l[0].toUpperCase() + l.slice(1)}</option>)}</Select></Field>
          {["individual", "team"].includes(f.level) ? (
            <Field label="Owner" hint="Leave as Me to own it yourself."><Select value={f.owner_employee_id || ""} onChange={(e) => set("owner_employee_id", e.target.value)}><option value="">Me</option>{owners.map((e: any) => <option key={e.id} value={e.id}>{empLabel(e)}</option>)}</Select></Field>
          ) : f.level === "department" ? (
            <div><Field label={<>Department<Req /></>}><Input maxLength={120} value={f.department || ""} onChange={(e) => set("department", e.target.value)} /></Field><FieldError msg={show("department")} /></div>
          ) : <div />}
          <div className="sm:col-span-2"><Field label="Aligned to (parent objective)" hint="Its progress counts toward the parent's when the parent has no key results of its own.">
            <Select value={f.parent_id || ""} onChange={(e) => set("parent_id", e.target.value)}><option value="">Not aligned</option>{objectives.filter((o) => o.status !== "cancelled").map((o) => <option key={o.id} value={o.id}>{o.level_label}: {o.title}</option>)}</Select></Field></div>
          <div className="sm:col-span-2"><Field label="Why it matters (optional)"><Textarea rows={2} maxLength={4000} value={f.description || ""} onChange={(e) => set("description", e.target.value)} /></Field></div>
        </FormGrid>
      </FormSection>
      <FormSection title="Scoring and status">
        <FormGrid cols={4}>
          <div><Field label={<>Weight<Req /></>} hint="How much it counts toward its parent and the review score. Keep objectives under one parent adding up to 100."><Input type="number" min={0.01} max={100} step="any" value={f.weight} onChange={(e) => set("weight", e.target.value)} /></Field>
            <FieldError msg={show("weight")} />
            {!errs.weight && f.parent_id && siblingTotal > 100 && <span className="block text-[11px] text-amber-700 mt-0.5">Objectives under this parent would add up to {siblingTotal}.</span>}</div>
          {f.level === "individual" && <Field label="Use in the appraisal review" hint="Yes rates it like a goal; No keeps it for development only."><Select value={f.include_in_review ? "1" : "0"} onChange={(e) => set("include_in_review", e.target.value === "1")}><option value="1">Yes: rated like a goal</option><option value="0">No: development only</option></Select></Field>}
          <Field label="Save as" hint="A draft does not count in progress or scores until it is made active."><Select value={f.status} onChange={(e) => set("status", e.target.value)}><option value="active">Active</option><option value="draft">Draft</option></Select></Field>
        </FormGrid>
      </FormSection>
      <FormSection title="Key results" hint="Each key result is a measurable step that proves the objective is met.">
        <KrGrid rows={krs} setRows={setKrs} />
      </FormSection>
    </Modal>
  );
}

function ObjectiveModal({ id, cycleId, objectives, onClose }: { id: string; cycleId: string; objectives: Row[]; onClose: () => void }) {
  const qc = useQueryClient();
  const o = objectives.find((x) => x.id === id);
  const hist = useQuery({ queryKey: ["perf", "okrs", "checkins", id], queryFn: () => okrApi.checkins(id), enabled: !!o });
  const [ck, setCk] = useState<Row>({ key_result_id: "", value: "", done: [] as string[], confidence: "", comment: "" });
  const [newKrs, setNewKrs] = useState<Row[]>([]);
  const [closing, setClosing] = useState<{ to: "closed" | "cancelled"; reason: string } | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["perf", "okrs"] });
  const kr = (o?.key_results || []).find((k: Row) => k.id === ck.key_result_id);
  const checkin = useMutation({
    mutationFn: () => okrApi.checkin(id, { key_result_id: ck.key_result_id || undefined, value: kr && kr.metric_type !== "milestone" ? ck.value : undefined,
      done_milestones: kr?.metric_type === "milestone" ? ck.done : undefined, confidence: ck.confidence || undefined, comment: ck.comment }),
    onSuccess: (r) => { toast.success(`Check-in saved. Objective now ${Math.round(r.objective_progress)}%.`); setCk({ key_result_id: "", value: "", done: [], confidence: "", comment: "" }); refresh(); },
    onError: (e) => toast.error(err(e), { duration: 7000 }),
  });
  const addKrs = useMutation({ mutationFn: async () => { for (const k of newKrs.filter((x) => x.title.trim())) await okrApi.addKr(id, krBody(k)); },
    onSuccess: () => { toast.success("Key results added."); setNewKrs([]); refresh(); }, onError: (e) => toast.error(err(e)) });
  const delKr = useMutation({ mutationFn: okrApi.deleteKr, onSuccess: () => { toast.success("Key result removed."); refresh(); }, onError: (e) => toast.error(err(e)) });
  const status = useMutation({ mutationFn: (s: { to: string; reason?: string }) => okrApi.status(id, s.to, s.reason), onSuccess: () => { toast.success("Status changed."); setClosing(null); refresh(); }, onError: (e) => toast.error(err(e)) });
  if (!o) return null;
  const ckErrs = collect({
    value: kr && kr.metric_type !== "milestone" ? v.required(ck.value, "New value") || (Number.isNaN(Number(ck.value)) ? "New value must be a number" : null)
      || (kr.metric_type === "percent" ? v.range(ck.value, 0, 100, "New value") : kr.metric_type === "currency" ? v.range(ck.value, 0, undefined, "New value") : null) : null,
    confidence: v.range(ck.confidence, 1, 10, "Confidence"),
    comment: v.maxLen(ck.comment, 2000, "Comment"),
  });
  const onCheckin = () => { if (Object.keys(ckErrs).length) { toast.error(firstError(ckErrs)); return; } checkin.mutate(); };
  const onAddKrs = () => { const e = krErrors(newKrs); if (Object.keys(e).length) { toast.error(firstError(e)); return; } addKrs.mutate(); };
  return (
    <Modal size="screen" title={o.title} subtitle={`${o.level_label} objective · ${o.owner_name || o.department || "Company"} · weight ${o.weight}`} onClose={onClose}
      footer={<>{o.can_edit && o.status === "active" && <><button className="btn-secondary" onClick={() => setClosing({ to: "closed", reason: "" })}>Close objective</button><button className="btn-secondary !text-red-600" onClick={() => setClosing({ to: "cancelled", reason: "" })}>Cancel objective</button></>}
        {o.can_edit && ["closed", "cancelled", "draft"].includes(o.status) && <button className="btn-secondary" onClick={() => status.mutate({ to: "active" })}>Make active</button>}
        <button className="btn-primary" onClick={onClose}>Close</button></>}>
      <div className="grid grid-cols-1 xl:grid-cols-[1fr_24rem] gap-4">
        <div>
          <div className="flex items-center gap-3 mb-2 text-sm"><Progress pct={o.progress_pct} />{o.confidence_band && <span className={BAND_CLASS[o.confidence_band]}>{BAND_LABEL[o.confidence_band]} ({o.confidence}/10)</span>}<span className="text-xs text-gray-500 capitalize">{o.status}</span></div>
          {o.description && <p className="text-xs text-gray-600 mb-3">{o.description}</p>}
          <div className="card !p-0 overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="bg-surface text-gray-400 text-left text-[10px] uppercase tracking-wide"><tr><th className="px-2.5 py-2">Key result</th><th className="px-2.5">Measure</th><th className="px-2.5">Where it stands</th><th className="px-2.5">Progress</th><th className="px-2.5">Confidence</th><th className="px-2.5 text-right">Weight</th><th /></tr></thead>
              <tbody>{(o.key_results || []).map((k: Row) => (
                <tr key={k.id} className="border-t border-surface-border">
                  <td className="px-2.5 py-1.5 font-medium text-gray-800">{k.title}</td><td className="px-2.5 capitalize">{k.metric_type}</td>
                  <td className="px-2.5">{krValue(k)}</td><td className="px-2.5"><Progress pct={k.progress_pct} /></td>
                  <td className="px-2.5">{k.confidence_band ? <span className={BAND_CLASS[k.confidence_band]}>{BAND_LABEL[k.confidence_band]}</span> : "—"}</td>
                  <td className="px-2.5 text-right">{k.weight}</td>
                  <td className="px-2.5">{o.can_edit && <button className="icon-btn" title="Remove" onClick={() => { if (window.confirm("Remove this key result?")) delKr.mutate(k.id); }}><Trash2 className="w-3.5 h-3.5 text-gray-400" /></button>}</td>
                </tr>))}
                {!(o.key_results || []).length && <tr><td colSpan={7} className="text-center py-6 text-gray-400">No key results. Progress comes from the objectives aligned under this one.</td></tr>}
              </tbody>
            </table>
          </div>
          {o.can_edit && o.status === "active" && <div className="mt-3">{newKrs.length ? <><KrGrid rows={newKrs} setRows={setNewKrs} /><button className="btn-primary !py-1 text-xs mt-2 disabled:opacity-50" disabled={addKrs.isPending} onClick={onAddKrs}>Save new key results</button></>
            : <button className="btn-secondary !py-1 !px-2 text-xs inline-flex items-center gap-1" onClick={() => setNewKrs([{ ...BLANK_KR }])}><Plus className="w-3 h-3" />Add key results</button>}</div>}
        </div>
        <div className="space-y-3">
          {o.can_checkin && o.status === "active" && (
            <div className="card !p-3">
              <div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold mb-2">Check in</div>
              <Field label="Key result to update"><Select value={ck.key_result_id} onChange={(e) => setCk({ ...ck, key_result_id: e.target.value, value: "", done: ((o.key_results || []).find((k: Row) => k.id === e.target.value)?.milestones || []).filter((m: Row) => m.done).map((m: Row) => m.label) })}>
                <option value="">None: confidence / comment only</option>{(o.key_results || []).map((k: Row) => <option key={k.id} value={k.id}>{k.title}</option>)}</Select></Field>
              <p className="text-[11px] text-gray-400 -mt-1 mb-1">Pick the key result you moved, or None to log only confidence and a comment.</p>
              {kr && kr.metric_type !== "milestone" && <div><Field label={<>New value (now {kr.current_value ?? kr.start_value}{kr.unit ? ` ${kr.unit}` : ""})<Req /></>} hint={`The latest total, not the change since last time. Target ${kr.target_value}.`}><Input type="number" step="any" value={ck.value} onChange={(e) => setCk({ ...ck, value: e.target.value })} /></Field><FieldError msg={ck.value !== "" ? ckErrs.value : null} /></div>}
              {kr && kr.metric_type === "milestone" && <div className="text-xs space-y-1 my-1">{(kr.milestones || []).map((m: Row) => <label key={m.label} className="flex items-center gap-2"><input type="checkbox" checked={ck.done.includes(m.label)} onChange={(e) => setCk({ ...ck, done: e.target.checked ? [...ck.done, m.label] : ck.done.filter((x: string) => x !== m.label) })} />{m.label}</label>)}</div>}
              <Field label={`Confidence ${ck.confidence ? `${ck.confidence}/10` : "(1 = will miss, 10 = sure)"}`}><input type="range" min={1} max={10} value={ck.confidence || 5} onChange={(e) => setCk({ ...ck, confidence: e.target.value })} className="w-full" /></Field>
              <Field label="Comment" hint="What moved, what is blocked, what help you need. Up to 2000 characters."><Textarea rows={3} maxLength={2000} value={ck.comment} onChange={(e) => setCk({ ...ck, comment: e.target.value })} placeholder="What moved, what is blocked" /></Field>
              <button className="btn-primary w-full mt-2 disabled:opacity-50" disabled={checkin.isPending || (!ck.key_result_id && !ck.confidence && !ck.comment)} onClick={onCheckin}>Save check-in</button>
            </div>)}
          <div className="card !p-3">
            <div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold mb-2">Check-in history</div>
            {!(hist.data || []).length ? <div className="text-xs text-gray-400">No check-ins yet.</div> : (hist.data || []).map((h) => (
              <div key={h.id} className="text-xs border-t border-surface-border py-1.5">
                <div className="flex justify-between text-gray-500"><span>{h.by_name}</span><span>{fmtDateTime(h.at)}</span></div>
                {h.new_value != null && <div>Value {h.old_value ?? "—"} → <b>{h.new_value}</b></div>}
                {h.milestones && <div>Milestones: {(h.milestones as Row[]).filter((m) => m.done).length} of {(h.milestones as Row[]).length} done</div>}
                {h.confidence && <div>Confidence {h.confidence}/10 <span className={BAND_CLASS[h.confidence_band]}>{BAND_LABEL[h.confidence_band]}</span></div>}
                {h.comment && <div className="text-gray-700">{h.comment}</div>}
              </div>))}
          </div>
        </div>
      </div>
      {closing && (
        <Modal size="lg" title={closing.to === "cancelled" ? `Cancel: ${o.title}` : `Close: ${o.title}`} subtitle={closing.to === "cancelled" ? "A cancelled objective stops counting toward its parent and the review." : "A closed objective keeps its progress and stops taking check-ins."} onClose={() => setClosing(null)}
          footer={<><button className="btn-secondary" onClick={() => setClosing(null)}>Back</button>
            <button className={`btn-primary disabled:opacity-50 ${closing.to === "cancelled" ? "!bg-red-600" : ""}`} disabled={status.isPending}
              onClick={() => { const e = collect({ reason: (closing.to === "cancelled" ? v.required(closing.reason.trim(), "Reason") : null) || v.maxLen(closing.reason.trim(), 400, "Reason") }); if (Object.keys(e).length) { toast.error(firstError(e)); return; } status.mutate({ to: closing.to, reason: closing.reason.trim() }); }}>
              {closing.to === "cancelled" ? "Cancel objective" : "Close objective"}</button></>}>
          <Field label={closing.to === "cancelled" ? <>Why is it cancelled<Req /></> : "Closing note (optional)"} hint="Kept in the audit record. Up to 400 characters.">
            <Textarea rows={3} autoFocus maxLength={400} value={closing.reason} onChange={(e) => setClosing({ ...closing, reason: e.target.value })} /></Field>
        </Modal>)}
    </Modal>
  );
}
