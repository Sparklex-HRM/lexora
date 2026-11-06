import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import toast from "react-hot-toast";
import { BellRing, GraduationCap, Play, Plus, Trash2 } from "lucide-react";
import DataGrid, { Tabs, WideModal, type GridColumn } from "../../components/DataGrid";
import { PageHeader, StatTile } from "../../components/ui";
import { employeeApi } from "../../api/endpoints";
import { engageApi, type Course } from "../../api/engage";
import { learningApi, type AsgRow, type LearningPath, type LmsQuestion, type LmsRule, type TeamPerson } from "../../api/learning";
import { day, errText, when } from "../../api/platform";
import { useAuthStore } from "../../store/authStore";
import { useUrlTab } from "../../search/useUrlTab";
import { v, collect, firstError, FieldError, Req } from "../../lib/validators";
import { todayISO } from "../../lib/datetime";   // 2026-10-25: UTC store, org-zone today

const guard = (errs: Record<string, string>, go: () => void) => { const m = firstError(errs); if (m) toast.error(m); else go(); };

const HR = ["superadmin", "org_admin", "hr_admin", "hr_manager"];
const ROLES = ["employee", "manager", "hr_admin", "hr_manager", "payroll_admin", "org_admin"];
const F = "input !py-1.5 text-sm w-full";
const L = "block text-[11px] font-semibold text-gray-500 mb-1 uppercase tracking-wide";

/** /learning/admin — Learning admin: paths, auto-assignment rules, question bank, completion report, team view, reminders.
 *  Courses themselves are edited on /engage/learning (Courses tab). */
export default function LearningAdminPage() {
  const role = useAuthStore((s) => s.user?.role) || "";
  const hr = HR.includes(role);
  const [tab, setTab] = useUrlTab<"report" | "paths" | "rules" | "bank" | "team">(hr ? "report" : "team");   // ?tab= (global search deep link)
  const qc = useQueryClient();
  const remind = useMutation({ mutationFn: learningApi.runReminders, onSuccess: (r) => toast.success(`${r.sent} reminder(s) sent through notifications`), onError: (e) => toast.error(errText(e)) });
  const tabs = hr ? [{ key: "report", label: "Completion report" }, { key: "paths", label: "Learning paths" }, { key: "rules", label: "Assignment rules" }, { key: "bank", label: "Question bank" }, { key: "team", label: "Team view" }]
    : [{ key: "team", label: "My team" }];
  return (
    <div>
      <PageHeader title="Learning admin" icon={GraduationCap} subtitle="Paths, rules by role / department / joining date, question bank, completion and compliance"
        actions={<div className="flex gap-2">{hr && <button className="btn-secondary text-sm inline-flex items-center gap-1" onClick={() => remind.mutate()} disabled={remind.isPending}><BellRing className="w-4 h-4" /> Send due reminders</button>}
          <Link to="/engage/learning" className="btn-secondary text-sm">Courses</Link></div>} />
      <Tabs value={tab} onChange={(k) => setTab(k as any)} tabs={tabs as any} />
      {tab === "report" && <Report />}
      {tab === "paths" && <Paths qc={qc} />}
      {tab === "rules" && <Rules />}
      {tab === "bank" && <Bank />}
      {tab === "team" && <Team hr={hr} />}
    </div>
  );
}

const STATE: Record<string, string> = { completed: "bg-green-100 text-green-700", overdue: "bg-red-100 text-red-700", in_progress: "bg-amber-100 text-amber-800", assigned: "bg-gray-100 text-gray-700" };
const asgCols: GridColumn<AsgRow>[] = [
  { key: "employee_name", label: "Person", className: "text-gray-800" }, { key: "department", label: "Department" }, { key: "manager_name", label: "Manager" },
  { key: "course_title", label: "Course" }, { key: "category", label: "Category", width: "100px" },
  { key: "mandatory", label: "Mandatory", width: "90px", text: (r) => (r.mandatory ? "yes" : "no"), render: (r) => (r.mandatory ? "yes" : "—") },
  { key: "due_on", label: "Due", type: "date", width: "105px" },
  { key: "state", label: "State", width: "105px", render: (r) => <span className={`inline-block text-[11px] font-semibold px-2 py-0.5 rounded-full ${STATE[r.state] || STATE.assigned}`}>{r.state.replace("_", " ")}</span> },
  { key: "progress_pct", label: "Progress", type: "percent", width: "85px" }, { key: "quiz_score", label: "Quiz %", type: "number", width: "75px" },
  { key: "quiz_attempts", label: "Attempts", type: "number", width: "80px" }, { key: "completed_at", label: "Completed", type: "date", width: "105px" }, { key: "certificate_no", label: "Certificate", width: "120px" },
];

function Report() {
  const [by, setBy] = useState("department");
  const [state, setState] = useState("");
  const [dept, setDept] = useState("");
  const q = useQuery({ queryKey: ["lms-report", by, state, dept], queryFn: () => learningApi.report({ by, state: state || undefined, department: dept || undefined }) });
  const qc = useQueryClient();
  const reset = useMutation({ mutationFn: (id: string) => learningApi.resetQuiz(id), onSuccess: () => { toast.success("Quiz reset"); qc.invalidateQueries({ queryKey: ["lms-report"] }); }, onError: (e) => toast.error(errText(e)) });
  const r = q.data;
  const depts = useMemo(() => Array.from(new Set((r?.rows || []).map((x) => x.department).filter(Boolean))).sort() as string[], [r]);
  const gcols: GridColumn[] = [{ key: "group", label: by === "course_title" ? "Course" : by === "manager_name" ? "Manager" : by === "category" ? "Category" : "Department", className: "text-gray-800" },
    { key: "assigned", label: "Assigned", type: "number" }, { key: "completed", label: "Completed", type: "number" }, { key: "overdue", label: "Overdue", type: "number" }, { key: "completion_pct", label: "Completion %", type: "percent" }];
  const cols: GridColumn<AsgRow>[] = [...asgCols, { key: "act", label: "", sortable: false, render: (x) => x.quiz_attempts > 0 && x.status !== "completed" ? <button className="text-xs text-primary-700" onClick={() => reset.mutate(x.id)}>Reset quiz</button> : null }];
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatTile label="Mandatory assignments" value={r?.mandatory?.assigned ?? "—"} accent /><StatTile label="Mandatory completed" value={r?.mandatory?.completed ?? "—"} />
        <StatTile label="Mandatory compliance" value={r?.mandatory?.compliance_pct != null ? `${r.mandatory.compliance_pct}%` : "—"} /><StatTile label="All assignments" value={r?.rows.length ?? "—"} sub={r ? `as of ${day(r.as_of)}` : ""} />
      </div>
      <DataGrid columns={gcols} rows={r?.groups || []} rowKey={(x) => x.group} minWidth={640} exportName={`learning-by-${by}`} density="compact"
        filters={<select className="input !py-1.5 text-sm w-40" value={by} onChange={(e) => setBy(e.target.value)} aria-label="Group by"><option value="department">By department</option><option value="course_title">By course</option><option value="manager_name">By manager</option><option value="category">By category</option></select>} />
      <h3 className="font-display font-semibold text-gray-800">Line items</h3>
      <DataGrid<AsgRow> columns={cols} rows={r?.rows || []} rowKey={(x) => x.id} minWidth={1300} exportName="learning-assignments" density="compact" initialSort={{ key: "due_on", dir: "asc" }}
        filters={<><select className="input !py-1.5 text-sm w-36" value={state} onChange={(e) => setState(e.target.value)} aria-label="State"><option value="">All states</option><option value="overdue">Overdue</option><option value="assigned">Assigned</option><option value="in_progress">In progress</option><option value="completed">Completed</option></select>
          <select className="input !py-1.5 text-sm w-44" value={dept} onChange={(e) => setDept(e.target.value)} aria-label="Department"><option value="">All departments</option>{depts.map((x) => <option key={x} value={x}>{x}</option>)}</select></>} />
    </div>
  );
}

function Paths({ qc }: { qc: ReturnType<typeof useQueryClient> }) {
  const q = useQuery({ queryKey: ["lms-paths"], queryFn: learningApi.paths });
  const courses = useQuery({ queryKey: ["lms-courses"], queryFn: () => engageApi.courses() });
  const [edit, setEdit] = useState<Partial<LearningPath> | null>(null);
  const [assign, setAssign] = useState<LearningPath | null>(null);
  const [progress, setProgress] = useState<LearningPath | null>(null);
  const cols: GridColumn<LearningPath>[] = [
    { key: "title", label: "Path", className: "text-gray-800 font-medium" }, { key: "courses", label: "Courses", type: "number", text: (r) => String(r.courses.length), render: (r) => r.courses.map((c) => c.title).join(" → ") },
    { key: "mandatory", label: "Mandatory", width: "90px", text: (r) => (r.mandatory ? "yes" : "no"), render: (r) => (r.mandatory ? "yes" : "—") }, { key: "people", label: "People", type: "number", width: "80px" }, { key: "completed", label: "Completed", type: "number", width: "90px" },
    { key: "active", label: "Active", width: "70px", text: (r) => (r.active ? "yes" : "no"), render: (r) => (r.active ? "yes" : "off") },
    { key: "act", label: "", sortable: false, render: (r) => <div className="flex gap-2"><button className="text-xs text-primary-700" onClick={(e) => { e.stopPropagation(); setEdit(r); }}>Edit</button><button className="text-xs text-primary-700" onClick={(e) => { e.stopPropagation(); setAssign(r); }}>Assign</button><button className="text-xs text-primary-700" onClick={(e) => { e.stopPropagation(); setProgress(r); }}>Progress</button></div> },
  ];
  return (
    <div>
      <div className="mb-2"><button className="btn-primary text-sm inline-flex items-center gap-1" onClick={() => setEdit({ title: "", description: "", course_ids: [], mandatory: false, active: true })}><Plus className="w-4 h-4" /> Learning path</button></div>
      <DataGrid<LearningPath> columns={cols} rows={q.data?.paths || []} rowKey={(r) => r.id} minWidth={900} exportName="learning-paths" density="compact" onRowClick={setEdit} empty="No learning paths yet. A path is an ordered list of courses with one due date." />
      {edit && <PathModal p={edit} courses={courses.data?.courses || []} onClose={() => { setEdit(null); qc.invalidateQueries({ queryKey: ["lms-paths"] }); }} />}
      {assign && <AssignModal title={`Assign path · ${assign.title}`} onClose={() => { setAssign(null); qc.invalidateQueries({ queryKey: ["lms-paths"] }); }} run={(b) => learningApi.assignPath(assign.id, b)} />}
      {progress && <PathProgressModal p={progress} onClose={() => setProgress(null)} />}
    </div>
  );
}

function PathModal({ p, courses, onClose }: { p: Partial<LearningPath>; courses: Course[]; onClose: () => void }) {
  const [f, setF] = useState<any>({ ...p, course_ids: p.course_ids || [] });
  const save = useMutation({ mutationFn: () => (p.id ? learningApi.updatePath(p.id, f) : learningApi.createPath(f)), onSuccess: () => { toast.success("Saved"); onClose(); }, onError: (e) => toast.error(errText(e)) });
  const move = (i: number, d: number) => { const a = [...f.course_ids]; const j = i + d; if (j < 0 || j >= a.length) return; [a[i], a[j]] = [a[j], a[i]]; setF({ ...f, course_ids: a }); };
  return (
    <WideModal title={p.id ? p.title || "" : "New learning path"} onClose={onClose} footer={<button className="btn-primary" disabled={save.isPending} onClick={() => guard(collect({ title: v.required(String(f.title || "").trim(), "Title") || v.maxLen(f.title, 150, "Title"), courses: f.course_ids.length ? null : "Add at least one course to the path" }), () => save.mutate())}>Save</button>}>
      <div className="grid md:grid-cols-2 gap-3">
        <div><label className={L}>Title<Req /></label><input className={F} maxLength={150} value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></div>
        <div className="flex items-end gap-4 text-sm"><label className="flex items-center gap-2"><input type="checkbox" checked={!!f.mandatory} onChange={(e) => setF({ ...f, mandatory: e.target.checked })} /> Mandatory</label><label className="flex items-center gap-2"><input type="checkbox" checked={f.active !== false} onChange={(e) => setF({ ...f, active: e.target.checked })} /> Active</label></div>
        <div className="md:col-span-2"><label className={L}>Description</label><textarea className={F} rows={2} value={f.description || ""} onChange={(e) => setF({ ...f, description: e.target.value })} /></div>
        <div><label className={L}>Courses in order ({f.course_ids.length})<Req /></label><span className="block text-[11px] text-gray-400 mb-1">Learners take them top to bottom</span>
          <ol className="divide-y divide-surface-border border border-surface-border rounded-lg">{f.course_ids.map((cid: string, i: number) => <li key={cid} className="flex items-center gap-2 px-2 py-1.5 text-sm"><span className="text-gray-400 w-5">{i + 1}.</span><span className="flex-1 truncate">{courses.find((c) => c.id === cid)?.title || cid}</span><button type="button" className="text-xs text-gray-500" onClick={() => move(i, -1)}>▲</button><button type="button" className="text-xs text-gray-500" onClick={() => move(i, 1)}>▼</button><button type="button" className="text-xs text-red-600" onClick={() => setF({ ...f, course_ids: f.course_ids.filter((x: string) => x !== cid) })}>✕</button></li>)}
            {!f.course_ids.length && <li className="px-2 py-3 text-xs text-gray-400">Pick courses on the right.</li>}</ol></div>
        <div><label className={L}>Available courses</label><div className="max-h-64 overflow-y-auto border border-surface-border rounded-lg divide-y divide-surface-border">{courses.filter((c) => !f.course_ids.includes(c.id)).map((c) => <button type="button" key={c.id} className="w-full text-left px-2 py-1.5 text-sm hover:bg-surface" onClick={() => setF({ ...f, course_ids: [...f.course_ids, c.id] })}>+ {c.title}<span className="text-[11px] text-gray-400 ml-2">{c.modules.length} modules{c.mandatory ? " · mandatory" : ""}</span></button>)}</div></div>
      </div>
    </WideModal>
  );
}

export function AssignModal({ title, onClose, run }: { title: string; onClose: () => void; run: (b: { employee_ids?: string[]; departments?: string[]; due_on?: string }) => Promise<{ assigned: number; already: number }> }) {
  const emps = useQuery({ queryKey: ["employees"], queryFn: () => employeeApi.list() });
  const list = ((emps.data || []) as any[]).filter((e) => e.status !== "terminated");
  const deps = Array.from(new Set(list.map((e) => e.department).filter(Boolean))) as string[];
  const [ids, setIds] = useState<string[]>([]); const [dd, setDd] = useState<string[]>([]); const [due, setDue] = useState(""); const [q, setQ] = useState("");
  const today = todayISO();
  const dueErr = due && due < today ? "Due date cannot be in the past" : null;
  const covered = new Set([...ids, ...list.filter((e) => dd.includes(e.department)).map((e) => e.id)]).size;
  const m = useMutation({ mutationFn: () => run({ employee_ids: ids, departments: dd, due_on: due || undefined }), onSuccess: (r) => { toast.success(`Assigned to ${r.assigned} (already had it: ${r.already})`); onClose(); }, onError: (e) => toast.error(errText(e)) });
  return (
    <WideModal title={title} onClose={onClose} footer={<button className="btn-primary" disabled={m.isPending || (!ids.length && !dd.length) || !!dueErr} onClick={() => m.mutate()}>Assign{covered ? ` · ${covered} people` : ""}</button>}>
      <div className="grid md:grid-cols-2 gap-3">
        <div><label className={L}>Departments</label><div className="flex flex-wrap gap-1.5">{deps.map((d) => <button type="button" key={d} onClick={() => setDd(dd.includes(d) ? dd.filter((x) => x !== d) : [...dd, d])} className={`px-2 py-1 rounded-lg text-xs border ${dd.includes(d) ? "bg-primary-600 text-white border-primary-600" : "bg-white border-surface-border"}`}>{d}</button>)}</div>
          <label className={`${L} mt-3`}>Due date</label><input type="date" min={today} className={F} value={due} onChange={(e) => setDue(e.target.value)} /><span className="block text-[11px] text-gray-400 mt-0.5">Blank = no deadline; a department covers everyone in it today</span><FieldError msg={dueErr} /></div>
        <div><label className={L}>People ({ids.length})</label><input className={`${F} mb-1`} placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} />
          <div className="max-h-64 overflow-y-auto border border-surface-border rounded-lg divide-y divide-surface-border">{list.filter((e) => !q || `${e.first_name} ${e.last_name}`.toLowerCase().includes(q.toLowerCase())).map((e) => <label key={e.id} className="flex items-center gap-2 px-2 py-1 text-sm"><input type="checkbox" checked={ids.includes(e.id)} onChange={(ev) => setIds(ev.target.checked ? [...ids, e.id] : ids.filter((x) => x !== e.id))} /> {e.first_name} {e.last_name} <span className="text-[11px] text-gray-400">{e.department || ""}</span></label>)}</div></div>
      </div>
    </WideModal>
  );
}

function PathProgressModal({ p, onClose }: { p: LearningPath; onClose: () => void }) {
  const q = useQuery({ queryKey: ["lms-path-progress", p.id], queryFn: () => learningApi.pathProgress(p.id) });
  const steps = q.data?.path.courses || [];
  const cols: GridColumn<any>[] = [{ key: "employee_name", label: "Person", className: "text-gray-800" }, { key: "due_on", label: "Due", type: "date", width: "105px" }, { key: "pct", label: "Progress", type: "percent", width: "90px" }, { key: "status", label: "Status", width: "110px" },
    ...steps.map((c, i) => ({ key: `s${i}`, label: `${i + 1}. ${c.title}`, text: (r: any) => r.steps[i]?.status || "", render: (r: any) => <span className={`inline-block text-[11px] font-semibold px-2 py-0.5 rounded-full ${STATE[r.steps[i]?.status] || STATE.assigned}`}>{(r.steps[i]?.status || "—").replace("_", " ")}</span> }))];
  return <WideModal title={`Progress · ${p.title}`} onClose={onClose}><DataGrid columns={cols} rows={q.data?.people || []} rowKey={(r) => r.employee_id} minWidth={700} exportName={`path-${p.title}`} density="compact" empty="Nobody is on this path yet." /></WideModal>;
}

function Rules() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["lms-rules"], queryFn: learningApi.rules });
  const courses = useQuery({ queryKey: ["lms-courses"], queryFn: () => engageApi.courses() });
  const paths = useQuery({ queryKey: ["lms-paths"], queryFn: learningApi.paths });
  const emps = useQuery({ queryKey: ["employees"], queryFn: () => employeeApi.list() });
  const deps = Array.from(new Set(((emps.data || []) as any[]).map((e) => e.department).filter(Boolean))) as string[];
  const locs = Array.from(new Set(((emps.data || []) as any[]).map((e) => e.work_location).filter(Boolean))) as string[];
  const [edit, setEdit] = useState<Partial<LmsRule> | null>(null);
  const inv = () => qc.invalidateQueries({ queryKey: ["lms-rules"] });
  const run = useMutation({ mutationFn: (id: string) => learningApi.runRule(id), onSuccess: (r) => { toast.success(`Assigned to ${r.assigned} people`); inv(); }, onError: (e) => toast.error(errText(e)) });
  const runAll = useMutation({ mutationFn: learningApi.runAllRules, onSuccess: (r) => { toast.success(`Assigned to ${r.assigned} people`); inv(); }, onError: (e) => toast.error(errText(e)) });
  const del = useMutation({ mutationFn: (id: string) => learningApi.deleteRule(id), onSuccess: inv, onError: (e) => toast.error(errText(e)) });
  const cols: GridColumn<LmsRule>[] = [
    { key: "name", label: "Rule", className: "text-gray-800 font-medium" }, { key: "course_title", label: "Course / path", text: (r) => r.course_title || r.path_title || "", render: (r) => r.course_title || (r.path_title ? `Path: ${r.path_title}` : "—") },
    { key: "roles", label: "Roles", text: (r) => r.roles.join(", "), render: (r) => r.roles.join(", ") || "any" }, { key: "departments", label: "Departments", text: (r) => r.departments.join(", "), render: (r) => r.departments.join(", ") || "any" },
    { key: "entities", label: "Entities", text: (r) => r.entities.join(", "), render: (r) => r.entities.join(", ") || "any" }, { key: "joined_from", label: "Joined from", type: "date", width: "105px" }, { key: "joined_to", label: "Joined to", type: "date", width: "105px" },
    { key: "due_days", label: "Due (days)", type: "number", width: "90px" }, { key: "new_joiners_only", label: "New joiners", width: "95px", text: (r) => (r.new_joiners_only ? "yes" : "no"), render: (r) => (r.new_joiners_only ? "only" : "all") },
    { key: "active", label: "Active", width: "70px", text: (r) => (r.active ? "yes" : "no"), render: (r) => (r.active ? "yes" : "off") }, { key: "last_run_at", label: "Last run", render: (r) => when(r.last_run_at), text: (r) => r.last_run_at || "" }, { key: "last_assigned", label: "Last assigned", type: "number", width: "100px" },
    { key: "act", label: "", sortable: false, render: (r) => <div className="flex gap-2"><button className="text-xs text-primary-700" onClick={(e) => { e.stopPropagation(); setEdit(r); }}>Edit</button><button className="text-xs text-primary-700 inline-flex items-center gap-0.5" onClick={(e) => { e.stopPropagation(); run.mutate(r.id); }}><Play className="w-3 h-3" /> Run</button><button className="text-xs text-red-600 inline-flex items-center gap-0.5" onClick={(e) => { e.stopPropagation(); if (confirm("Delete this rule? Existing assignments stay.")) del.mutate(r.id); }}><Trash2 className="w-3 h-3" /></button></div> },
  ];
  return (
    <div>
      <div className="card mb-3 text-sm text-gray-700 bg-amber-50/40 border-amber-200">{q.data?.note || "Rules assign a course or path automatically — e.g. 'Workplace harassment prevention' to everyone within 30 days of joining. The tick runs them every 10 minutes; new joiners are picked up as they appear."}</div>
      <div className="mb-2 flex gap-2"><button className="btn-primary text-sm inline-flex items-center gap-1" onClick={() => setEdit({ name: "", roles: [], departments: [], entities: [], locations: [], due_days: 30, new_joiners_only: false, active: true })}><Plus className="w-4 h-4" /> Rule</button><button className="btn-secondary text-sm" onClick={() => runAll.mutate()} disabled={runAll.isPending}>Run all active rules now</button></div>
      <DataGrid<LmsRule> columns={cols} rows={q.data?.rules || []} rowKey={(r) => r.id} minWidth={1400} exportName="learning-rules" density="compact" onRowClick={setEdit} empty="No assignment rules yet." />
      {edit && <RuleModal r={edit} courses={courses.data?.courses || []} paths={paths.data?.paths || []} deps={deps} locs={locs} onClose={() => { setEdit(null); inv(); }} />}
    </div>
  );
}

function Chips({ all, value, onChange, free }: { all: string[]; value: string[]; onChange: (v: string[]) => void; free?: boolean }) {
  const [txt, setTxt] = useState("");
  const opts = Array.from(new Set([...all, ...value]));
  return (
    <div className="flex flex-wrap gap-1.5 items-center">{opts.map((d) => <button type="button" key={d} onClick={() => onChange(value.includes(d) ? value.filter((x) => x !== d) : [...value, d])} className={`px-2 py-1 rounded-lg text-xs border ${value.includes(d) ? "bg-primary-600 text-white border-primary-600" : "bg-white border-surface-border"}`}>{d}</button>)}
      {free && <input className="input !py-1 !px-2 text-xs w-32" placeholder="add + Enter" value={txt} onChange={(e) => setTxt(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && txt.trim()) { e.preventDefault(); onChange([...value, txt.trim()]); setTxt(""); } }} />}
      {!opts.length && !free && <span className="text-xs text-gray-400">none on record</span>}</div>
  );
}

function RuleModal({ r, courses, paths, deps, locs, onClose }: { r: Partial<LmsRule>; courses: Course[]; paths: LearningPath[]; deps: string[]; locs: string[]; onClose: () => void }) {
  const [f, setF] = useState<any>({ ...r, target: r.path_id ? `p:${r.path_id}` : r.course_id ? `c:${r.course_id}` : "" });
  const save = useMutation({ mutationFn: () => { const body = { ...f, course_id: f.target.startsWith("c:") ? f.target.slice(2) : null, path_id: f.target.startsWith("p:") ? f.target.slice(2) : null }; return r.id ? learningApi.updateRule(r.id, body) : learningApi.createRule(body); }, onSuccess: () => { toast.success("Saved"); onClose(); }, onError: (e) => toast.error(errText(e)) });
  return (
    <WideModal title={r.id ? r.name || "" : "New assignment rule"} onClose={onClose} footer={<button className="btn-primary" disabled={save.isPending} onClick={() => guard(collect({
        name: v.required(String(f.name || "").trim(), "Rule name") || v.maxLen(f.name, 150, "Rule name"),
        target: v.required(f.target, "Course or learning path"),
        joined_to: v.dateOrder(f.joined_from, f.joined_to, "Joined to cannot be before joined from"),
        due_days: Number.isInteger(Number(f.due_days ?? 30)) && Number(f.due_days ?? 30) >= 0 && Number(f.due_days ?? 30) <= 730 ? null : "Due within is 0 to 730 days" }), () => save.mutate())}>Save</button>}>
      <div className="grid md:grid-cols-2 gap-3">
        <div><label className={L}>Rule name<Req /></label><input className={F} maxLength={150} value={f.name} placeholder="e.g. Harassment prevention — all new joiners" onChange={(e) => setF({ ...f, name: e.target.value })} /></div>
        <div><label className={L}>Course or learning path<Req /></label><select className={F} value={f.target} onChange={(e) => setF({ ...f, target: e.target.value })} disabled={!!r.id}><option value="">Pick one…</option><optgroup label="Courses">{courses.map((c) => <option key={c.id} value={`c:${c.id}`}>{c.title}</option>)}</optgroup><optgroup label="Learning paths">{paths.map((p) => <option key={p.id} value={`p:${p.id}`}>{p.title}</option>)}</optgroup></select></div>
        <div className="md:col-span-2 text-[11px] font-semibold uppercase tracking-wide text-primary-700 border-b border-surface-border pb-1">Who it applies to (leave a list empty to match everyone)</div>
        <div><label className={L}>Roles (any = everyone)</label><Chips all={ROLES} value={f.roles || []} onChange={(v) => setF({ ...f, roles: v })} /></div>
        <div><label className={L}>Departments</label><Chips all={deps} value={f.departments || []} onChange={(v) => setF({ ...f, departments: v })} /></div>
        <div><label className={L}>Legal entities (id or name)</label><Chips all={[]} value={f.entities || []} onChange={(v) => setF({ ...f, entities: v })} free /></div>
        <div><label className={L}>Locations</label><Chips all={locs} value={f.locations || []} onChange={(v) => setF({ ...f, locations: v })} /></div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
          <div><label className={L}>Joined from</label><input type="date" className={F} value={f.joined_from || ""} onChange={(e) => setF({ ...f, joined_from: e.target.value || null })} /></div>
          <div><label className={L}>Joined to</label><input type="date" min={f.joined_from || undefined} className={F} value={f.joined_to || ""} onChange={(e) => setF({ ...f, joined_to: e.target.value || null })} /></div>
          <div><label className={L}>Due within (days)</label><input type="number" className={F} min={0} max={730} value={f.due_days ?? 30} onChange={(e) => setF({ ...f, due_days: Number(e.target.value) })} /></div>
        </div>
        <div className="flex items-end gap-4 text-sm"><label className="flex items-center gap-2"><input type="checkbox" checked={!!f.new_joiners_only} onChange={(e) => setF({ ...f, new_joiners_only: e.target.checked })} /> Only people who join after this rule is made</label><label className="flex items-center gap-2"><input type="checkbox" checked={f.active !== false} onChange={(e) => setF({ ...f, active: e.target.checked })} /> Active</label></div>
        <p className="md:col-span-2 text-xs text-gray-500">Due date = joining date + days (never in the past: existing staff get the same number of days from today). Content is the organisation's own — nothing legal is pre-written.</p>
      </div>
    </WideModal>
  );
}

function Bank() {
  const qc = useQueryClient();
  const [tag, setTag] = useState("");
  const q = useQuery({ queryKey: ["lms-bank", tag], queryFn: () => learningApi.questions(tag || undefined) });
  const [edit, setEdit] = useState<Partial<LmsQuestion> | null>(null);
  const inv = () => qc.invalidateQueries({ queryKey: ["lms-bank"] });
  const del = useMutation({ mutationFn: (id: string) => learningApi.deleteQuestion(id), onSuccess: inv, onError: (e) => toast.error(errText(e)) });
  const cols: GridColumn<LmsQuestion>[] = [
    { key: "tag", label: "Tag", width: "120px" }, { key: "q", label: "Question", className: "text-gray-800" },
    { key: "options", label: "Options", text: (r) => r.options.join(" | "), render: (r) => r.options.map((o, i) => <span key={i} className={i === r.answer ? "font-semibold text-green-700" : ""}>{o}{i < r.options.length - 1 ? " · " : ""}</span>) },
    { key: "active", label: "Active", width: "70px", text: (r) => (r.active ? "yes" : "no"), render: (r) => (r.active ? "yes" : "off") },
    { key: "act", label: "", sortable: false, render: (r) => <div className="flex gap-2"><button className="text-xs text-primary-700" onClick={(e) => { e.stopPropagation(); setEdit(r); }}>Edit</button><button className="text-xs text-red-600" onClick={(e) => { e.stopPropagation(); if (confirm("Delete this question?")) del.mutate(r.id); }}>Delete</button></div> },
  ];
  return (
    <div>
      <div className="mb-2 flex gap-2 items-center"><button className="btn-primary text-sm inline-flex items-center gap-1" onClick={() => setEdit({ tag: tag || "", q: "", options: ["", ""], answer: 0, active: true })}><Plus className="w-4 h-4" /> Question</button>
        <span className="text-xs text-gray-500">A course with "question tags" draws its quiz from here — a fresh set each attempt. Correct option in green.</span></div>
      <DataGrid<LmsQuestion> columns={cols} rows={q.data?.questions || []} rowKey={(r) => r.id} minWidth={900} exportName="question-bank" density="compact" onRowClick={setEdit} empty="The bank is empty."
        filters={<select className="input !py-1.5 text-sm w-40" value={tag} onChange={(e) => setTag(e.target.value)} aria-label="Tag"><option value="">All tags</option>{(q.data?.tags || []).map((t) => <option key={t} value={t}>{t}</option>)}</select>} />
      {edit && <QuestionModal q={edit} onClose={() => { setEdit(null); inv(); }} />}
    </div>
  );
}

function QuestionModal({ q, onClose }: { q: Partial<LmsQuestion>; onClose: () => void }) {
  const [f, setF] = useState<any>({ ...q, options: q.options || ["", ""] });
  const save = useMutation({ mutationFn: () => (q.id ? learningApi.updateQuestion(q.id, f) : learningApi.addQuestion(f)), onSuccess: () => { toast.success("Saved"); onClose(); }, onError: (e) => toast.error(errText(e)) });
  return (
    <WideModal title={q.id ? "Edit question" : "New question"} onClose={onClose} footer={<button className="btn-primary" disabled={save.isPending} onClick={() => guard(collect({
        tag: v.required(String(f.tag || "").trim(), "Tag") || v.maxLen(f.tag, 60, "Tag"),
        q: v.required(String(f.q || "").trim(), "Question") || v.maxLen(f.q, 1000, "Question"),
        options: f.options.some((o: string) => !o.trim()) ? "Fill every option or remove the blank one" : null,
        answer: !String(f.options[f.answer] ?? "").trim() ? "Tick the correct option" : null }), () => save.mutate())}>Save</button>}>
      <div className="grid md:grid-cols-[12rem_1fr] gap-3">
        <div><label className={L}>Tag<Req /></label><input className={F} maxLength={60} value={f.tag} placeholder="e.g. harassment" onChange={(e) => setF({ ...f, tag: e.target.value })} /><span className="block text-[11px] text-gray-400 mt-0.5">Courses draw quizzes by tag</span><label className="flex items-center gap-2 text-sm mt-3"><input type="checkbox" checked={f.active !== false} onChange={(e) => setF({ ...f, active: e.target.checked })} /> Active</label></div>
        <div><label className={L}>Question<Req /></label><input className={F} maxLength={1000} value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} />
          <label className={`${L} mt-3`}>Options (tick the correct one)</label>
          {f.options.map((o: string, j: number) => <div key={j} className="flex items-center gap-2 mb-1"><input type="radio" name="ans" checked={f.answer === j} onChange={() => setF({ ...f, answer: j })} /><input className={F} value={o} onChange={(e) => setF({ ...f, options: f.options.map((x: string, k: number) => (k === j ? e.target.value : x)) })} />{f.options.length > 2 && <button type="button" className="text-xs text-red-600" onClick={() => setF({ ...f, options: f.options.filter((_: string, k: number) => k !== j), answer: Math.min(f.answer, f.options.length - 2) })}>✕</button>}</div>)}
          {f.options.length < 8 && <button type="button" className="text-xs text-primary-700" onClick={() => setF({ ...f, options: [...f.options, ""] })}>+ option</button>}</div>
      </div>
    </WideModal>
  );
}

function Team({ hr }: { hr: boolean }) {
  const [mgr, setMgr] = useState("");
  const emps = useQuery({ queryKey: ["employees"], queryFn: () => employeeApi.list(), enabled: hr });
  const managers = useMemo(() => { const list = (emps.data || []) as any[]; const ids = new Set(list.map((e) => e.manager_id).filter(Boolean)); return list.filter((e) => ids.has(e.id)).sort((a, b) => `${a.first_name}`.localeCompare(b.first_name)); }, [emps.data]);
  const q = useQuery({ queryKey: ["lms-team", mgr], queryFn: () => learningApi.team(mgr || undefined) });
  const r = q.data;
  const cols: GridColumn<TeamPerson>[] = [{ key: "employee_name", label: "Person", className: "text-gray-800" }, { key: "assigned", label: "Assigned", type: "number" }, { key: "completed", label: "Completed", type: "number" }, { key: "in_progress", label: "In progress", type: "number" }, { key: "overdue", label: "Overdue", type: "number" }, { key: "mandatory_pending", label: "Mandatory pending", type: "number" }, { key: "completion_pct", label: "Completion %", type: "percent" }];
  return (
    <div className="space-y-3">
      {hr && <select className="input !py-1.5 text-sm w-64" value={mgr} onChange={(e) => setMgr(e.target.value)} aria-label="Manager"><option value="">Pick a manager…</option>{managers.map((m) => <option key={m.id} value={m.id}>{m.first_name} {m.last_name}</option>)}</select>}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3"><StatTile label="Team" value={r?.team_size ?? "—"} accent /><StatTile label="Assignments" value={r?.assigned ?? "—"} /><StatTile label="Overdue" value={r?.overdue ?? "—"} /><StatTile label="Completion" value={r?.completion_pct != null ? `${r.completion_pct}%` : "—"} /></div>
      <DataGrid<TeamPerson> columns={cols} rows={r?.people || []} rowKey={(x) => x.employee_id} minWidth={800} exportName="team-learning" density="compact" empty={hr && !mgr ? "Pick a manager to see their team." : "No direct reports."} />
      <h3 className="font-display font-semibold text-gray-800">Team assignments</h3>
      <DataGrid<AsgRow> columns={asgCols} rows={r?.assignments || []} rowKey={(x) => x.id} minWidth={1300} exportName="team-assignments" density="compact" initialSort={{ key: "due_on", dir: "asc" }} />
    </div>
  );
}
