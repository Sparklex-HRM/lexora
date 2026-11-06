import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import toast from "react-hot-toast";
import { CalendarClock, Plus, Trash2 } from "lucide-react";
import DataGrid, { Tabs, WideModal, type GridColumn } from "../../components/DataGrid";
import { PageHeader, StatTile } from "../../components/ui";
import SimpleChart from "../../components/SimpleChart";
import { employeeApi } from "../../api/endpoints";
import { engageApi, type Survey } from "../../api/engage";
import { engage2Api, type HeatRow, type KudosBudget, type SurveyBankQ, type SurveySchedule } from "../../api/analytics";
import { day, errText } from "../../api/platform";
import { useUrlTab } from "../../search/useUrlTab";
import { v, collect, firstError, FieldError, Req } from "../../lib/validators";
import { todayISO } from "../../lib/datetime";   // 2026-10-25: UTC store, org-zone today

/** Survey schedule / bank checks — return { field: message }. */
function scheduleErrors(f: any, qs: any[], isNew: boolean) {
  const today = todayISO();
  const int = (x: any) => Number.isInteger(Number(x));
  return collect({
    title: v.required(String(f.title || "").trim(), "Title") || v.maxLen(f.title, 120, "Title"),
    day_of_month: f.cadence !== "weekly" && (!int(f.day_of_month) || Number(f.day_of_month) < 1 || Number(f.day_of_month) > 28) ? "Day of month is 1 to 28 (so it runs every month, February too)" : null,
    open_days: !int(f.open_days) || Number(f.open_days) < 1 || Number(f.open_days) > 60 ? "Open for 1 to 60 days" : null,
    threshold: f.threshold !== "" && f.threshold != null && (!int(f.threshold) || Number(f.threshold) < 1) ? "Minimum responses is a whole number, 1 or more" : null,
    start_on: isNew && f.start_on && f.start_on < today ? "First run cannot be in the past" : null,
    next_run_on: !isNew && f.next_run_on && String(f.next_run_on).slice(0, 10) < today ? "Next run cannot be in the past" : null,
    questions: !qs.length ? "Add at least one question" : qs.some((q) => !String(q.text || "").trim()) ? "Every question needs its text" : qs.some((q) => q.type === "choice" && (q.options || []).length < 2) ? "A choice question needs at least two options" : null,
  });
}

const F = "input !py-1.5 text-sm w-full";
const L = "block text-[11px] font-semibold text-gray-500 mb-1 uppercase tracking-wide";
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** /engage/surveys/admin — HR: pulse / eNPS schedules, question bank, heatmaps by department / manager behind the
 *  anonymity threshold, eNPS trend, recognition budgets per manager, onboarding check-ins. */
export default function SurveysAdminPage() {
  const [tab, setTab] = useUrlTab<"schedules" | "heatmap" | "trend" | "bank" | "budgets">("schedules");   // ?tab= (global search deep link)
  return (
    <div>
      <PageHeader title="Surveys admin" icon={CalendarClock} subtitle="Schedules · heatmaps · eNPS trend · question bank · recognition budgets"
        actions={<Link to="/engage/surveys" className="btn-secondary text-sm">Surveys list</Link>} />
      <Tabs value={tab} onChange={setTab} tabs={[{ key: "schedules", label: "Schedules" }, { key: "heatmap", label: "Heatmaps" }, { key: "trend", label: "eNPS trend" }, { key: "bank", label: "Question bank" }, { key: "budgets", label: "Recognition budgets" }]} />
      {tab === "schedules" && <Schedules />}
      {tab === "heatmap" && <Heatmaps />}
      {tab === "trend" && <Trend />}
      {tab === "bank" && <Bank />}
      {tab === "budgets" && <Budgets />}
    </div>
  );
}

function Schedules() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["svy-sched"], queryFn: engage2Api.schedules });
  const [edit, setEdit] = useState<Partial<SurveySchedule> | null>(null);
  const inv = () => qc.invalidateQueries({ queryKey: ["svy-sched"] });
  const run = useMutation({ mutationFn: engage2Api.runDue, onSuccess: (r) => { toast.success(`${r.opened} survey(s) opened`); inv(); }, onError: (e) => toast.error(errText(e)) });
  const onb = useMutation({ mutationFn: engage2Api.runOnboarding, onSuccess: (r) => toast.success(`${r.sent} onboarding check-in(s) sent`), onError: (e) => toast.error(errText(e)) });
  const del = useMutation({ mutationFn: (id: string) => engage2Api.deleteSchedule(id), onSuccess: inv, onError: (e) => toast.error(errText(e)) });
  const cols: GridColumn<SurveySchedule>[] = [
    { key: "title", label: "Schedule", className: "text-gray-800 font-medium" }, { key: "kind", label: "Kind", width: "70px" },
    { key: "cadence", label: "Cadence", width: "100px", render: (r) => r.cadence === "weekly" ? `weekly · ${DAYS[r.weekday]}` : `${r.cadence} · day ${r.day_of_month}` },
    { key: "open_days", label: "Open for (days)", type: "number", width: "110px" }, { key: "questions", label: "Questions", type: "number", text: (r) => String(r.questions.length), render: (r) => r.questions.length },
    { key: "anonymous", label: "Anonymous", width: "90px", text: (r) => (r.anonymous ? "yes" : "no"), render: (r) => (r.anonymous ? "yes" : "named") },
    { key: "target", label: "Target", text: (r) => (r.target.departments || []).join(", "), render: (r) => (r.target.departments || []).join(", ") || "everyone" },
    { key: "next_run_on", label: "Next run", type: "date", width: "105px" }, { key: "last_run_on", label: "Last run", type: "date", width: "105px" }, { key: "runs", label: "Runs", type: "number", width: "70px" },
    { key: "active", label: "Active", width: "70px", text: (r) => (r.active ? "yes" : "no"), render: (r) => (r.active ? "yes" : "paused") },
    { key: "act", label: "", sortable: false, render: (r) => <div className="flex gap-2"><button className="text-xs text-primary-700" onClick={(e) => { e.stopPropagation(); setEdit(r); }}>Edit</button><button className="text-xs text-red-600 inline-flex items-center gap-0.5" onClick={(e) => { e.stopPropagation(); if (confirm("Delete this schedule? Surveys already opened stay.")) del.mutate(r.id); }}><Trash2 className="w-3 h-3" /></button></div> },
  ];
  return (
    <div>
      <div className="mb-2 flex flex-wrap gap-2"><button className="btn-primary text-sm inline-flex items-center gap-1" onClick={() => setEdit({ title: "", kind: "pulse", cadence: "monthly", weekday: 0, day_of_month: 1, open_days: 7, anonymous: true, target: {}, active: true })}><Plus className="w-4 h-4" /> Schedule</button>
        <button className="btn-secondary text-sm" onClick={() => run.mutate()} disabled={run.isPending}>Open due surveys now</button>
        <button className="btn-secondary text-sm" onClick={() => onb.mutate()} disabled={onb.isPending}>Send 30/60/90-day check-ins now</button>
        <span className="text-xs text-gray-500 self-center">The service tick does both every 10 minutes. Exit surveys go out when an exit case is accepted.</span></div>
      <DataGrid<SurveySchedule> columns={cols} rows={q.data?.schedules || []} rowKey={(r) => r.id} minWidth={1200} exportName="survey-schedules" density="compact" onRowClick={setEdit} empty="No schedules yet. A weekly pulse or a quarterly eNPS is two clicks." />
      {edit && <ScheduleModal s={edit} onClose={() => { setEdit(null); inv(); }} />}
    </div>
  );
}

function ScheduleModal({ s, onClose }: { s: Partial<SurveySchedule>; onClose: () => void }) {
  const tpls = useQuery({ queryKey: ["eng-tpl"], queryFn: () => engageApi.surveyTemplates() });
  const bank = useQuery({ queryKey: ["svy-bank", ""], queryFn: () => engage2Api.bank() });
  const emps = useQuery({ queryKey: ["employees"], queryFn: () => employeeApi.list() });
  const deps = Array.from(new Set(((emps.data || []) as any[]).map((e) => e.department).filter(Boolean))) as string[];
  const locs = Array.from(new Set(((emps.data || []) as any[]).map((e) => e.work_location).filter(Boolean))) as string[];
  const [f, setF] = useState<any>({ ...s, questions: s.questions || null, template: s.id ? "" : "pulse", departments: s.target?.departments || [], locations: (s.target as any)?.locations || [], threshold: s.threshold || "", next_run_on: s.next_run_on ? String(s.next_run_on).slice(0, 10) : "" });
  const tpl = (tpls.data || []).find((x) => x.key === f.template);
  const qs: any[] = f.questions || tpl?.questions || [];
  const setQ = (i: number, p: any) => setF({ ...f, questions: qs.map((x, j) => (j === i ? { ...x, ...p } : x)) });
  // keep any target keys this form does not edit (e.g. employee_ids set elsewhere) — saving used to wipe them
  const target = () => { const t: any = { ...((s.target as any) || {}) }; delete t.departments; delete t.locations; if (f.departments.length) t.departments = f.departments; if (f.locations.length) t.locations = f.locations; return t; };
  const save = useMutation({ mutationFn: () => { const { locations: _l, ...rest } = f; const body: any = { ...rest, title: String(f.title || "").trim(), questions: qs, target: target(), threshold: f.threshold ? Number(f.threshold) : null };
    if (s.id) { if (f.next_run_on && f.next_run_on !== String(s.next_run_on || "").slice(0, 10)) body.next_run_on = f.next_run_on; else delete body.next_run_on; } else delete body.next_run_on;
    return s.id ? engage2Api.updateSchedule(s.id, body) : engage2Api.createSchedule(body); }, onSuccess: () => { toast.success("Saved"); onClose(); }, onError: (e) => toast.error(errText(e)) });
  const errs = scheduleErrors(f, qs, !s.id);
  const submit = () => { if (Object.keys(errs).length) { toast.error(firstError(errs)); return; } save.mutate(); };
  return (
    <WideModal title={s.id ? s.title || "" : "New survey schedule"} onClose={onClose} footer={<button className="btn-primary" disabled={save.isPending} onClick={submit}>Save</button>}>
      <div className="grid sm:grid-cols-2 md:grid-cols-3 gap-3">
        <div><label className={L}>Title<Req /></label><input className={F} maxLength={120} value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /><FieldError msg={errs.title} /></div>
        <div><label className={L}>Kind</label><select className={F} value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}><option value="pulse">Pulse (1–5)</option><option value="enps">eNPS (0–10)</option></select><span className="block text-[11px] text-gray-400 mt-0.5">eNPS asks "how likely to recommend us" on 0–10</span></div>
        {!s.id && <div><label className={L}>Start from template</label><select className={F} value={f.template} onChange={(e) => setF({ ...f, template: e.target.value, questions: null })}>{(tpls.data || []).filter((x) => x.kind !== "exit").map((x) => <option key={x.key} value={x.key}>{x.title}</option>)}</select></div>}
        <div><label className={L}>Cadence</label><select className={F} value={f.cadence} onChange={(e) => setF({ ...f, cadence: e.target.value })}><option value="weekly">Weekly</option><option value="monthly">Monthly</option><option value="quarterly">Quarterly</option></select></div>
        {f.cadence === "weekly" ? <div><label className={L}>Weekday</label><select className={F} value={f.weekday} onChange={(e) => setF({ ...f, weekday: Number(e.target.value) })}>{DAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}</select></div>
          : <div><label className={L}>Day of month (1–28)</label><input type="number" className={F} min={1} max={28} value={f.day_of_month} onChange={(e) => setF({ ...f, day_of_month: Number(e.target.value) })} /><FieldError msg={errs.day_of_month} /></div>}
        <div><label className={L}>Open for (days)<Req /></label><input type="number" className={F} min={1} max={60} value={f.open_days} onChange={(e) => setF({ ...f, open_days: Number(e.target.value) })} /><FieldError msg={errs.open_days} /></div>
        {!s.id && <div><label className={L}>First run on (blank = next slot)</label><input type="date" className={F} min={todayISO()} value={f.start_on || ""} onChange={(e) => setF({ ...f, start_on: e.target.value })} /><FieldError msg={errs.start_on} /></div>}
        {s.id && <div><label className={L}>Next run on</label><input type="date" className={F} min={todayISO()} value={f.next_run_on || ""} onChange={(e) => setF({ ...f, next_run_on: e.target.value })} /><span className="block text-[11px] text-gray-400 mt-0.5">Move the next send; later runs follow the cadence</span><FieldError msg={errs.next_run_on} /></div>}
        <div><label className={L}>Min. responses before results (blank = org default)</label><input type="number" className={F} min={1} step={1} value={f.threshold} onChange={(e) => setF({ ...f, threshold: e.target.value })} /><span className="block text-[11px] text-gray-400 mt-0.5">Results stay hidden until this many people answer</span><FieldError msg={errs.threshold} /></div>
        <div className="flex items-end gap-4 text-sm"><label className="flex items-center gap-2" title="Individual answers are never shown — only totals above the minimum"><input type="checkbox" checked={!!f.anonymous} onChange={(e) => setF({ ...f, anonymous: e.target.checked })} /> Anonymous</label><label className="flex items-center gap-2"><input type="checkbox" checked={f.active !== false} onChange={(e) => setF({ ...f, active: e.target.checked })} /> Active</label></div>
        <div className="md:col-span-3"><label className={L}>Target departments (none = everyone)</label><div className="flex flex-wrap gap-1.5">{deps.map((d) => <button type="button" key={d} onClick={() => setF({ ...f, departments: f.departments.includes(d) ? f.departments.filter((x: string) => x !== d) : [...f.departments, d] })} className={`px-2 py-1 rounded-lg text-xs border ${f.departments.includes(d) ? "bg-primary-600 text-white border-primary-600" : "bg-white border-surface-border"}`}>{d}</button>)}</div></div>
        {locs.length > 0 && <div className="sm:col-span-2 md:col-span-3"><label className={L}>Target locations (none = every location)</label><div className="flex flex-wrap gap-1.5">{locs.map((d) => <button type="button" key={d} onClick={() => setF({ ...f, locations: f.locations.includes(d) ? f.locations.filter((x: string) => x !== d) : [...f.locations, d] })} className={`px-2 py-1 rounded-lg text-xs border ${f.locations.includes(d) ? "bg-primary-600 text-white border-primary-600" : "bg-white border-surface-border"}`}>{d}</button>)}</div>
          <span className="block text-[11px] text-gray-400 mt-0.5">Matched on the employee's work location; combine with departments to narrow further</span></div>}
        {!!(s.target as any)?.employee_ids?.length && <div className="sm:col-span-2 md:col-span-3 text-[11px] text-gray-500">Also limited to {(s.target as any).employee_ids.length} named employee(s) — kept as is.</div>}
        <div className="sm:col-span-2 md:col-span-3 space-y-1.5"><label className={L}>Questions<Req /></label><FieldError msg={errs.questions} />
          {qs.map((q, i) => <div key={i} className="grid grid-cols-12 gap-2 items-center"><select className={`${F} col-span-2`} value={q.type} onChange={(e) => setQ(i, { type: e.target.value })}><option value="scale">1–5</option><option value="nps">0–10 (NPS)</option><option value="choice">choice</option><option value="text">text</option></select><input className={`${F} col-span-9`} value={q.text} onChange={(e) => setQ(i, { text: e.target.value })} /><button type="button" className="text-xs text-red-600" onClick={() => setF({ ...f, questions: qs.filter((_, j) => j !== i) })}>✕</button>
            {q.type === "choice" && <input className={`${F} col-span-12`} placeholder="Options, comma separated" value={(q.options || []).join(", ")} onChange={(e) => setQ(i, { options: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })} />}</div>)}
          <div className="flex gap-2 items-center"><button type="button" className="btn-secondary text-xs" onClick={() => setF({ ...f, questions: [...qs, { key: `q${qs.length + 1}`, type: "scale", text: "" }] })}>+ question</button>
            {!!bank.data?.questions.length && <select className="input !py-1 text-xs w-72" value="" onChange={(e) => { const b = bank.data!.questions.find((x) => x.id === e.target.value); if (b) setF({ ...f, questions: [...qs, { key: `q${qs.length + 1}`, type: b.type, text: b.text, options: b.options }] }); }}><option value="">+ from the question bank…</option>{bank.data.questions.map((b) => <option key={b.id} value={b.id}>[{b.tag}] {b.text}</option>)}</select>}</div></div>
      </div>
    </WideModal>
  );
}

function Heatmaps() {
  const list = useQuery({ queryKey: ["eng-surveys"], queryFn: () => engageApi.surveys() });
  const surveys = (list.data?.surveys || []).filter((s: Survey) => s.status !== "draft");
  const [sid, setSid] = useState("");
  const [by, setBy] = useState<"department" | "manager">("department");
  const q = useQuery({ queryKey: ["heat", sid, by], queryFn: () => engage2Api.heatmap(sid, by), enabled: !!sid });
  const h = q.data;
  const tone = (v: number | null | undefined, type: string) => { if (v == null) return ""; const t = type === "nps" ? (v + 100) / 200 : (v - 1) / 4; return t >= 0.75 ? "bg-green-100 text-green-800" : t >= 0.5 ? "bg-lime-50 text-gray-800" : t >= 0.3 ? "bg-amber-100 text-amber-800" : "bg-red-100 text-red-800"; };
  const cols: GridColumn<HeatRow>[] = [{ key: "segment", label: by === "manager" ? "Manager" : "Department", className: "text-gray-800 font-medium" }, { key: "responses", label: "Responses", type: "number", width: "95px" },
    ...(h?.questions || []).map((qq) => ({ key: qq.key, label: qq.type === "nps" ? `eNPS · ${qq.text.slice(0, 28)}` : qq.text.slice(0, 36), type: "number" as const, text: (r: HeatRow) => r.hidden ? "hidden" : String(r.scores[qq.key] ?? ""), render: (r: HeatRow) => r.hidden ? <span className="text-[11px] text-gray-400">hidden (&lt; {h?.survey.threshold})</span> : <span className={`inline-block px-2 py-0.5 rounded tabular-nums ${tone(r.scores[qq.key], qq.type)}`}>{r.scores[qq.key] ?? "—"}</span> }))];
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2"><select className="input !py-1.5 text-sm w-80" value={sid} onChange={(e) => setSid(e.target.value)} aria-label="Survey"><option value="">Pick a survey…</option>{surveys.map((s: Survey) => <option key={s.id} value={s.id}>{s.title} ({s.responses ?? 0})</option>)}</select>
        <select className="input !py-1.5 text-sm w-40" value={by} onChange={(e) => setBy(e.target.value as any)} aria-label="By"><option value="department">By department</option><option value="manager">By manager</option></select></div>
      {h && <div className="text-xs text-gray-500">{h.note}{h.unstamped ? ` · ${h.unstamped} answer(s) carry no segment (given before segments were recorded).` : ""}</div>}
      <DataGrid<HeatRow> columns={cols} rows={h?.rows || []} rowKey={(r) => r.segment} minWidth={700} exportName={`heatmap-${by}`} density="compact" empty={sid ? "No answers yet." : "Pick a survey."} />
    </div>
  );
}

function Trend() {
  const q = useQuery({ queryKey: ["enps-trend"], queryFn: engage2Api.enpsTrend });
  const pts = q.data?.points || [];
  const cols: GridColumn[] = [{ key: "date", label: "Date", type: "date", width: "110px" }, { key: "title", label: "Survey" }, { key: "responses", label: "Responses", type: "number" }, { key: "participation_pct", label: "Participation %", type: "percent" },
    { key: "score", label: "eNPS", type: "number", render: (r) => r.visible ? r.score : <span className="text-[11px] text-gray-400">hidden (below threshold)</span> }, { key: "delta", label: "Change vs previous", type: "number", render: (r) => r.delta == null ? "—" : <span className={r.delta >= 0 ? "text-green-700" : "text-red-700"}>{r.delta > 0 ? "+" : ""}{r.delta}</span> }];
  const visible = pts.filter((p) => p.visible);
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3"><StatTile label="Latest eNPS" value={visible.length ? visible[visible.length - 1].score : "—"} accent /><StatTile label="Surveys" value={pts.length} /><StatTile label="Headcount" value={q.data?.headcount ?? "—"} /><StatTile label="Latest participation" value={pts.length && pts[pts.length - 1].participation_pct != null ? `${pts[pts.length - 1].participation_pct}%` : "—"} /></div>
      {visible.length > 1 && <div className="card"><SimpleChart spec={{ type: "line", x: "date", series: [{ key: "score", label: "eNPS" }] }} data={visible} height={220} /></div>}
      <DataGrid columns={cols} rows={pts} rowKey={(r) => r.survey_id} minWidth={800} exportName="enps-trend" density="compact" empty="No eNPS surveys yet." />
    </div>
  );
}

function Bank() {
  const qc = useQueryClient();
  const [tag, setTag] = useState("");
  const q = useQuery({ queryKey: ["svy-bank", tag], queryFn: () => engage2Api.bank(tag || undefined) });
  const [edit, setEdit] = useState<Partial<SurveyBankQ> | null>(null);
  const inv = () => qc.invalidateQueries({ queryKey: ["svy-bank"] });
  const del = useMutation({ mutationFn: (id: string) => engage2Api.deleteBank(id), onSuccess: inv, onError: (e) => toast.error(errText(e)) });
  const cols: GridColumn<SurveyBankQ>[] = [{ key: "tag", label: "Tag", width: "120px" }, { key: "type", label: "Type", width: "80px" }, { key: "text", label: "Question", className: "text-gray-800" }, { key: "options", label: "Options", text: (r) => r.options.join(" | "), render: (r) => r.options.join(" · ") || "—" },
    { key: "active", label: "Active", width: "70px", text: (r) => (r.active ? "yes" : "no"), render: (r) => (r.active ? "yes" : "off") },
    { key: "act", label: "", sortable: false, render: (r) => <div className="flex gap-2"><button className="text-xs text-primary-700" onClick={(e) => { e.stopPropagation(); setEdit(r); }}>Edit</button><button className="text-xs text-red-600" onClick={(e) => { e.stopPropagation(); if (confirm("Delete?")) del.mutate(r.id); }}>Delete</button></div> }];
  return (
    <div>
      <div className="mb-2"><button className="btn-primary text-sm inline-flex items-center gap-1" onClick={() => setEdit({ tag: tag || "", type: "scale", text: "", options: [], active: true })}><Plus className="w-4 h-4" /> Question</button></div>
      <DataGrid<SurveyBankQ> columns={cols} rows={q.data?.questions || []} rowKey={(r) => r.id} minWidth={800} exportName="survey-question-bank" density="compact" onRowClick={setEdit} empty="The bank is empty — add Likert / eNPS / open-text questions by theme."
        filters={<select className="input !py-1.5 text-sm w-40" value={tag} onChange={(e) => setTag(e.target.value)} aria-label="Tag"><option value="">All tags</option>{(q.data?.tags || []).map((t) => <option key={t} value={t}>{t}</option>)}</select>} />
      {edit && <BankModal q={edit} onClose={() => { setEdit(null); inv(); }} />}
    </div>
  );
}

function BankModal({ q, onClose }: { q: Partial<SurveyBankQ>; onClose: () => void }) {
  const [f, setF] = useState<any>({ ...q, options: (q.options || []).join(", ") });
  const opts = String(f.options || "").split(",").map((x: string) => x.trim()).filter(Boolean);
  const errs = collect({
    tag: v.required(String(f.tag || "").trim(), "Tag / theme") || v.maxLen(f.tag, 40, "Tag / theme"),
    text: v.required(String(f.text || "").trim(), "Question") || v.maxLen(f.text, 300, "Question"),
    options: f.type === "choice" && opts.length < 2 ? "A choice question needs at least two options" : f.type === "choice" && opts.length > 12 ? "At most 12 options" : null,
  });
  const save = useMutation({ mutationFn: () => { const body = { ...f, options: f.type === "choice" ? String(f.options).split(",").map((x: string) => x.trim()).filter(Boolean) : [] }; return q.id ? engage2Api.updateBank(q.id, body) : engage2Api.addBank(body); }, onSuccess: () => { toast.success("Saved"); onClose(); }, onError: (e) => toast.error(errText(e)) });
  return (
    <WideModal title={q.id ? "Edit question" : "New survey question"} onClose={onClose} footer={<button className="btn-primary" disabled={save.isPending} onClick={() => { if (Object.keys(errs).length) { toast.error(firstError(errs)); return; } save.mutate(); }}>Save</button>}>
      <div className="grid sm:grid-cols-2 md:grid-cols-3 gap-3">
        <div><label className={L}>Tag / theme<Req /></label><input className={F} maxLength={40} value={f.tag} placeholder="e.g. Manager, Workload, Growth" onChange={(e) => setF({ ...f, tag: e.target.value })} /><span className="block text-[11px] text-gray-400 mt-0.5">Groups questions in the heatmaps</span><FieldError msg={errs.tag} /></div>
        <div><label className={L}>Type<Req /></label><select className={F} value={f.type} onChange={(e) => setF({ ...f, type: e.target.value })}><option value="scale">Likert 1–5</option><option value="nps">eNPS 0–10</option><option value="choice">Choice</option><option value="text">Open text</option></select></div>
        <label className="flex items-center gap-2 text-sm self-end"><input type="checkbox" checked={f.active !== false} onChange={(e) => setF({ ...f, active: e.target.checked })} /> Active</label>
        <div className="sm:col-span-2 md:col-span-3"><label className={L}>Question<Req /></label><input className={F} maxLength={300} value={f.text} onChange={(e) => setF({ ...f, text: e.target.value })} /><FieldError msg={errs.text} /></div>
        {f.type === "choice" && <div className="sm:col-span-2 md:col-span-3"><label className={L}>Options (comma separated)<Req /></label><input className={F} value={f.options} onChange={(e) => setF({ ...f, options: e.target.value })} placeholder="e.g. Yes, No, Not sure" /><FieldError msg={errs.options} /></div>}
      </div>
    </WideModal>
  );
}

function Budgets() {
  const qc = useQueryClient();
  const [month, setMonth] = useState("");
  const q = useQuery({ queryKey: ["kudos-budgets", month], queryFn: () => engage2Api.budgets(month || undefined) });
  const lb = useQuery({ queryKey: ["kudos-lb", month], queryFn: () => engage2Api.leaderboard(month || undefined) });
  const put = useMutation({ mutationFn: (b: { manager_id: string; month: string; points_budget: number }) => engage2Api.putBudget(b), onSuccess: () => { toast.success("Saved"); qc.invalidateQueries({ queryKey: ["kudos-budgets"] }); }, onError: (e) => toast.error(errText(e)) });
  const m = q.data?.month || month;
  const cols: GridColumn<KudosBudget>[] = [{ key: "manager_name", label: "Manager", className: "text-gray-800" }, { key: "points_budget", label: "Budget (points)", type: "number", render: (r) => q.data?.can_edit ? <input type="number" className="input !py-0.5 !px-2 text-sm w-24 text-right" defaultValue={r.points_budget} onBlur={(e) => { const v = Number(e.target.value); if (v !== r.points_budget) put.mutate({ manager_id: r.manager_id, month: m, points_budget: v }); }} /> : r.points_budget },
    { key: "points_used", label: "Used", type: "number" }, { key: "remaining", label: "Remaining", type: "number" }, { key: "updated_by", label: "Set by" }];
  const totals = useMemo(() => { const b = q.data?.budgets || []; return { manager_name: "Total", points_budget: b.reduce((s, x) => s + x.points_budget, 0), points_used: b.reduce((s, x) => s + x.points_used, 0), remaining: b.reduce((s, x) => s + x.remaining, 0) }; }, [q.data]);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2 items-center"><input type="month" className="input !py-1.5 text-sm w-44" value={month} onChange={(e) => setMonth(e.target.value)} aria-label="Month" /><span className="text-xs text-gray-500">Default {q.data?.default_points ?? "—"} points per manager per month · {q.data?.points_per_kudos ?? "—"} points per kudos (Engage settings). A manager's kudos stops when the budget is spent.</span></div>
      <div className="grid md:grid-cols-[1fr_22rem] gap-3">
        <DataGrid<KudosBudget> columns={cols} rows={q.data?.budgets || []} rowKey={(r) => r.id} minWidth={600} exportName={`recognition-budgets-${m}`} density="compact" totals={totals} empty="No managers on record." />
        <div className="card !py-3"><div className="text-sm font-semibold text-gray-800 mb-2">Top receivers · {lb.data?.month}</div>
          <table className="w-full text-[13px]"><tbody>{(lb.data?.top || []).map((t, i) => <tr key={t.employee_id} className="border-t border-surface-border"><td className="py-1 text-gray-400 w-6">{i + 1}</td><td className="py-1 text-gray-800">{t.name}</td><td className="py-1 text-right tabular-nums">{t.points} pts</td><td className="py-1 text-right text-gray-500 tabular-nums">{t.count}×</td></tr>)}
            {!lb.data?.top.length && <tr><td className="py-3 text-xs text-gray-400">No points given this month.</td></tr>}</tbody></table></div>
      </div>
      <p className="text-xs text-gray-400">Budgets are per manager per month ({day(todayISO())}).</p>
    </div>
  );
}
