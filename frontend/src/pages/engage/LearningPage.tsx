import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { BookOpen, CheckCircle2, Download, ExternalLink } from "lucide-react";
import DataGrid, { Tabs, WideModal, type GridColumn } from "../../components/DataGrid";
import { PageHeader, StatTile } from "../../components/ui";
import { employeeApi } from "../../api/endpoints";
import { engageApi, type Assignment, type Course, type CourseModule, type QuizQ } from "../../api/engage";
import { learningApi } from "../../api/learning";   // D7: SCORM-lite package upload
import { errText } from "../../api/platform";
import { openSafe } from "../../lib/safeUrl";
import { useLang, useT } from "../../i18n";
import { fmtDate } from "../../i18n/format";
import { useAuthStore } from "../../store/authStore";
import ReadPage from "../../i18n/ReadPage";
import { useUrlTab } from "../../search/useUrlTab";
import { v, collect, firstError, FieldError, Req } from "../../lib/validators";
import { todayISO } from "../../lib/datetime";   // 2026-10-25: UTC store, org-zone today
import { can, viewRoleFor } from "../../config/permissions";   // 2026-10-26: UI gates = permission + role (custom roles)

/** Course sheet checks — mirror the server's module / quiz rules so Save explains itself. */
function courseErrors(f: any) {
  const int = (x: any, lo: number, hi: number) => x === undefined || x === null || x === "" || (Number.isInteger(Number(x)) && Number(x) >= lo && Number(x) <= hi);
  const badMod = (f.modules || []).findIndex((m: any) => !String(m.title || "").trim() || ((m.kind === "video" || m.kind === "url") && !/^https?:\/\/\S+$/i.test(String(m.url || "").trim())) || Number(m.minutes || 0) < 0);
  const badQ = (f.quiz || []).findIndex((q: any) => !String(q.q || "").trim() || q.options.filter((o: string) => o.trim()).length < 2 || !String(q.options[q.answer] || "").trim());
  return collect({
    title: v.required(String(f.title || "").trim(), "Course title") || v.maxLen(f.title, 200, "Course title"),
    pass_mark: int(f.pass_mark, 0, 100) ? null : "Pass % is a whole number, 0 to 100",
    max_attempts: int(f.max_attempts, 0, 50) ? null : "Max attempts is 0 to 50",
    quiz_sample: int(f.quiz_sample, 0, 200) ? null : "Questions per attempt is 0 to 200",
    category: v.maxLen(f.category, 60, "Category"),
    modules: badMod >= 0 ? `Module ${badMod + 1}: give a title${["video", "url"].includes(f.modules[badMod].kind) ? " and a full http(s) link" : ""}` : null,
    quiz: badQ >= 0 ? `Quiz question ${badQ + 1}: needs the question, two or more options, and the ticked answer must not be blank` : null,
  });
}

const HR = ["superadmin", "org_admin", "hr_admin", "hr_manager"];
const FIELD = "input w-full";
const LABEL = "block text-xs font-semibold text-gray-500 mb-1";

/** /engage/learning — my courses (phone-first) · HR: courses, assign, compliance. */
export default function LearningPage() {
  const t = useT();
  const user = useAuthStore((s) => s.user);
  const role = viewRoleFor(user, "learning.view", [...HR, "manager"]);
  const hr = can(user, ["learning.create", "learning.edit", "learning.admin"], HR);
  const [urlTab, setTab] = useUrlTab<"mine" | "courses" | "compliance">("mine");   // ?tab= (global search deep link)
  const tabs: [typeof urlTab, string][] = hr ? [["mine", t("learn.mine")], ["courses", "Courses"], ["compliance", "Compliance"]] : role === "manager" ? [["mine", t("learn.mine")], ["compliance", "Team compliance"]] : [["mine", t("learn.mine")]];
  // A tab this role does not have (e.g. an employee opening ?tab=compliance) shows "My learning" instead of a 403.
  const tab = tabs.some(([k]) => k === urlTab) ? urlTab : "mine";
  return (
    <div>
      <PageHeader title={t("learn.title")} icon={BookOpen} subtitle={hr ? "Courses, assignments and training compliance" : "Your courses, quizzes and certificates"} actions={<ReadPage />} />
      {tabs.length > 1 && <Tabs value={tab} onChange={setTab} tabs={tabs.map(([k, l]) => ({ key: k, label: l }))} />}
      {tab === "mine" && <Mine />}
      {tab === "courses" && <Courses />}
      {tab === "compliance" && <Compliance />}
    </div>
  );
}

function Mine() {
  const t = useT();
  const lang = useLang();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["m-learn"], queryFn: () => engageApi.assignments("mine") });
  const [quiz, setQuiz] = useState<Assignment | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const rows = q.data?.assignments || [];
  const open = rows.find((a) => a.id === openId) || null;
  const certificate = (a: Assignment) => engageApi.certificateBlob(a.id).then((u) => window.open(u, "_blank")).catch((e) => toast.error(errText(e)));
  const statusChip = (a: Assignment) => <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full whitespace-nowrap ${a.status === "completed" ? "bg-green-100 text-green-700" : a.state === "overdue" ? "bg-red-100 text-red-700" : "bg-amber-100 text-amber-800"}`}>{a.status === "completed" ? t("learn.completed") : t("learn.progress", { n: a.progress_pct })}</span>;
  const cols: GridColumn<Assignment>[] = [
    { key: "course_title", label: "Course", render: (a) => <span className="text-gray-800 font-medium">{a.course_title}{a.mandatory && <span className="ml-2 text-[10px] font-bold uppercase px-1.5 py-0.5 rounded bg-red-100 text-red-700">mandatory</span>}</span> },
    { key: "due_on", label: "Due", width: "120px", render: (a) => <span className={a.state === "overdue" ? "text-red-600 font-semibold" : ""}>{fmtDate(a.due_on, lang)}{a.state === "overdue" ? ` · ${t("learn.overdue")}` : ""}</span>, text: (a) => a.due_on || "" },
    { key: "modules", label: t("learn.modules"), width: "100px", type: "number", text: (a) => String(a.completed_modules.length), render: (a) => `${a.completed_modules.filter((k) => a.modules.some((m) => m.key === k)).length} / ${a.modules.length}` },
    { key: "progress_pct", label: "Progress", width: "160px", type: "percent", render: (a) => <div className="flex items-center gap-2"><div className="h-1.5 bg-gray-100 rounded flex-1 min-w-[60px]"><div className="h-1.5 bg-primary-500 rounded" style={{ width: `${a.progress_pct}%` }} /></div><span className="tabular-nums text-xs">{a.progress_pct}%</span></div> },
    { key: "quiz_score", label: t("learn.quiz"), width: "120px", text: (a) => (a.quiz.length ? (a.quiz_passed ? "passed" : a.quiz_score != null ? `${a.quiz_score}%` : "pending") : ""), render: (a) => !a.quiz.length ? "—" : a.quiz_passed ? <span className="text-green-700 inline-flex items-center gap-1"><CheckCircle2 className="w-3.5 h-3.5" /> {a.quiz_score != null ? `${a.quiz_score}%` : ""}</span> : a.quiz_score != null ? `${a.quiz_score}%` : "—" },
    { key: "status", label: t("common.status"), width: "130px", text: (a) => a.status, render: statusChip },
    { key: "act", label: "", sortable: false, width: "200px", render: (a) => <div className="flex gap-1.5 justify-end">
      <button className="btn-secondary !py-1 !px-2 text-xs whitespace-nowrap" onClick={(e) => { e.stopPropagation(); setOpenId(a.id); }}>{t("learn.open")}</button>
      {a.status === "completed" && <button className="btn-secondary !py-1 !px-2 text-xs whitespace-nowrap inline-flex items-center gap-1" onClick={(e) => { e.stopPropagation(); certificate(a); }}><Download className="w-3 h-3" /> {t("learn.certificate")}</button>}
    </div> },
  ];
  return (
    <div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <StatTile label="Assigned" value={q.data ? rows.length : "—"} accent />
        <StatTile label={t("learn.completed")} value={q.data ? rows.filter((a) => a.status === "completed").length : "—"} />
        <StatTile label="In progress" value={q.data ? rows.filter((a) => a.status !== "completed" && a.state !== "overdue").length : "—"} />
        <StatTile label={t("learn.overdue")} value={q.data ? rows.filter((a) => a.status !== "completed" && a.state === "overdue").length : "—"} />
      </div>
      <DataGrid columns={cols} rows={rows} rowKey={(a) => a.id} minWidth={980} exportName="my-learning" empty={q.isLoading ? t("common.loading") : t("learn.none")} onRowClick={(a) => setOpenId(a.id)} />
      {open && <CourseDetail a={open} onClose={() => setOpenId(null)} onQuiz={() => { setOpenId(null); setQuiz(open); }} onCertificate={() => certificate(open)} />}
      {quiz && <QuizSheet a={quiz} onClose={() => { setQuiz(null); qc.invalidateQueries({ queryKey: ["m-learn"] }); }} />}
    </div>
  );
}

/** One of my courses: its modules (open / mark done), the quiz and the certificate. */
function CourseDetail({ a, onClose, onQuiz, onCertificate }: { a: Assignment; onClose: () => void; onQuiz: () => void; onCertificate: () => void }) {
  const t = useT();
  const lang = useLang();
  const qc = useQueryClient();
  const done = useMutation({ mutationFn: ({ id, key }: { id: string; key: string }) => engageApi.progress(id, key), onSuccess: () => qc.invalidateQueries({ queryKey: ["m-learn"] }), onError: (e) => toast.error(errText(e)) });
  const openModule = (m: CourseModule) => { if (m.kind === "scorm") { window.location.href = "/learning/my"; return; } if (m.kind === "pdf" && m.doc_id) engageApi.lmsFileBlob(m.doc_id).then((u) => window.open(u, "_blank")).catch((e) => toast.error(errText(e))); else if (m.url) openSafe(m.url); };
  return (
    <WideModal title={a.course_title || ""} onClose={onClose}
      footer={<>{a.status === "completed" && <button className="btn-secondary inline-flex items-center gap-1" onClick={onCertificate}><Download className="w-4 h-4" /> {t("learn.certificate")} {a.certificate_no}</button>}<button className="btn-secondary" onClick={onClose}>{t("common.close")}</button></>}>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4 text-sm">
        <div><div className={LABEL}>Due</div><b className={a.state === "overdue" ? "text-red-600" : "text-gray-800"}>{a.due_on ? fmtDate(a.due_on, lang) : "—"}{a.state === "overdue" ? ` · ${t("learn.overdue")}` : ""}</b></div>
        <div><div className={LABEL}>Progress</div><b className="text-gray-800">{a.progress_pct}%</b></div>
        <div><div className={LABEL}>{t("common.status")}</div><b className="text-gray-800">{a.status === "completed" ? t("learn.completed") : t("learn.progress", { n: a.progress_pct })}</b></div>
        <div><div className={LABEL}>Mandatory</div><b className="text-gray-800">{a.mandatory ? t("common.yes") : t("common.no")}</b></div>
      </div>
      <div className="h-1.5 bg-gray-100 rounded mb-4"><div className="h-1.5 bg-primary-500 rounded" style={{ width: `${a.progress_pct}%` }} /></div>
      <div className="overflow-x-auto border border-surface-border rounded-xl">
        <table className="w-full text-sm min-w-[560px]">
          <thead><tr className="text-left text-[11px] uppercase tracking-wide text-gray-500 border-b border-surface-border"><th className="px-3 py-2 w-8"></th><th className="px-3 py-2">{t("learn.modules")}</th><th className="px-3 py-2 w-24">Kind</th><th className="px-3 py-2 w-24">Minutes</th><th className="px-3 py-2 w-56"></th></tr></thead>
          <tbody className="divide-y divide-surface-border">
            {a.modules.map((m) => { const ok = a.completed_modules.includes(m.key); return (
              <tr key={m.key}>
                <td className="px-3 py-2">{ok ? <CheckCircle2 className="w-4 h-4 text-green-600" /> : <span className="block w-4 h-4 rounded-full border border-gray-300" />}</td>
                <td className="px-3 py-2 text-gray-800">{m.title}</td>
                <td className="px-3 py-2 text-gray-500">{m.kind}</td>
                <td className="px-3 py-2 text-gray-500 tabular-nums">{m.minutes || "—"}</td>
                <td className="px-3 py-2"><div className="flex gap-3 justify-end">
                  <button className="text-xs text-primary-700 inline-flex items-center gap-1" onClick={() => openModule(m)}><ExternalLink className="w-3 h-3" /> {t("learn.open")}</button>
                  {!ok && a.status !== "completed" && <button className="text-xs font-semibold text-green-700" disabled={done.isPending} onClick={() => done.mutate({ id: a.id, key: m.key })}>{t("learn.mark_done")}</button>}
                </div></td>
              </tr>); })}
            {a.quiz.length > 0 && <tr>
              <td className="px-3 py-2">{a.quiz_passed ? <CheckCircle2 className="w-4 h-4 text-green-600" /> : <span className="block w-4 h-4 rounded-full border border-gray-300" />}</td>
              <td className="px-3 py-2 text-gray-800" colSpan={3}>{t("learn.quiz")} · {t("learn.pass_mark", { n: a.pass_mark ?? 0 })}{a.quiz_score != null ? ` · ${a.quiz_score}%` : ""}</td>
              <td className="px-3 py-2"><div className="flex justify-end">{!a.quiz_passed && <button className="btn-primary !py-1 !px-2.5 text-xs" onClick={onQuiz}>{t("learn.take_quiz")}</button>}</div></td>
            </tr>}
          </tbody>
        </table>
      </div>
    </WideModal>
  );
}

function QuizSheet({ a, onClose }: { a: Assignment; onClose: () => void }) {
  const t = useT();
  const [ans, setAns] = useState<number[]>(a.quiz.map(() => -1));
  const [result, setResult] = useState<string | null>(null);
  const m = useMutation({ mutationFn: () => engageApi.quiz(a.id, ans), onSuccess: (r) => setResult(r.message || ""), onError: (e) => toast.error(errText(e)) });
  return (
    <WideModal title={`${t("learn.quiz")} · ${a.course_title}`} onClose={onClose}
      footer={result ? <button className="btn-primary" onClick={onClose}>{t("common.close")}</button> : <><button type="button" className="btn-secondary" onClick={onClose}>{t("common.cancel")}</button><button type="submit" form="lms-quiz" className="btn-primary" disabled={m.isPending}>{t("common.submit")}</button></>}>
      {result ? <div className="card text-sm">{result}</div> : (
        <form id="lms-quiz" className="grid grid-cols-1 lg:grid-cols-2 gap-5" onSubmit={(e) => { e.preventDefault(); if (ans.some((x) => x < 0)) return toast.error("Answer every question"); m.mutate(); }}>
          {a.quiz.map((q, i) => <div key={i}><div className="text-sm font-medium text-gray-800 mb-1.5">{i + 1}. {q.q}</div><div className="space-y-1">{q.options.map((o, j) => <label key={j} className={`flex items-center gap-2 rounded-xl border px-3 py-2 text-sm ${ans[i] === j ? "border-primary-500 bg-primary-50" : "border-surface-border"}`}><input type="radio" name={`q${i}`} checked={ans[i] === j} onChange={() => setAns(ans.map((x, k) => (k === i ? j : x)))} /> {o}</label>)}</div></div>)}
        </form>
      )}
    </WideModal>
  );
}

function Courses() {
  const t = useT();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["lms-courses"], queryFn: () => engageApi.courses() });
  const [edit, setEdit] = useState<Partial<Course> | null>(null);
  const [assign, setAssign] = useState<Course | null>(null);
  const cols: GridColumn<Course>[] = [
    { key: "title", label: t("survey.title") }, { key: "mandatory", label: "Mandatory", text: (r) => (r.mandatory ? "yes" : "no"), render: (r) => (r.mandatory ? t("common.yes") : t("common.no")), width: "90px" },
    { key: "modules", label: t("learn.modules"), type: "number", text: (r) => String(r.modules.length), render: (r) => r.modules.length }, { key: "quiz_questions", label: t("learn.quiz"), type: "number" }, { key: "pass_mark", label: "Pass %", type: "number" },
    { key: "assigned", label: "Assigned", type: "number" }, { key: "completed", label: t("learn.completed"), type: "number" }, { key: "active", label: t("common.status"), text: (r) => (r.active ? "active" : "off"), render: (r) => (r.active ? "Active" : "Off") },
    { key: "act", label: "", render: (r) => <div className="flex gap-2"><button className="text-xs text-primary-700" onClick={(e) => { e.stopPropagation(); setEdit(r); }}>Edit</button><button className="text-xs text-primary-700" onClick={(e) => { e.stopPropagation(); setAssign(r); }}>Assign</button></div> },
  ];
  return (
    <div>
      <div className="mb-2"><button className="btn-primary text-sm" onClick={() => setEdit({ title: "", description: "", modules: [], quiz: [], pass_mark: 70, mandatory: false, active: true })}>+ Course</button></div>
      <DataGrid columns={cols} rows={q.data?.courses || []} rowKey={(r) => r.id} minWidth={900} exportName="courses" onRowClick={(r) => setEdit(r)} />
      {edit && <CourseSheet c={edit} onClose={() => { setEdit(null); qc.invalidateQueries({ queryKey: ["lms-courses"] }); }} />}
      {assign && <AssignSheet c={assign} onClose={() => { setAssign(null); qc.invalidateQueries({ queryKey: ["lms-courses"] }); }} />}
    </div>
  );
}

function CourseSheet({ c, onClose }: { c: Partial<Course>; onClose: () => void }) {
  const t = useT();
  const [f, setF] = useState<any>({ ...c, modules: c.modules || [], quiz: c.quiz || [] });
  const fileRef = useRef<HTMLInputElement>(null);
  const pkgRef = useRef<HTMLInputElement>(null);   // D7: SCORM-lite zip
  const save = useMutation({ mutationFn: () => (c.id ? engageApi.updateCourse(c.id, f) : engageApi.createCourse(f)), onSuccess: () => { toast.success(t("common.saved")); onClose(); }, onError: (e) => toast.error(errText(e)) });
  const upload = useMutation({ mutationFn: (file: File) => engageApi.moduleFile(c.id!, file), onSuccess: (r) => { setF({ ...f, modules: r.modules }); toast.success(t("common.saved")); }, onError: (e) => toast.error(errText(e)) });
  const uploadPkg = useMutation({ mutationFn: (file: File) => learningApi.uploadPackage(c.id!, file), onSuccess: (r) => { setF({ ...f, modules: r.modules }); toast.success(`Package stored (${r.entries} files)`); }, onError: (e) => toast.error(errText(e)) });
  const setM = (i: number, p: Partial<CourseModule>) => setF({ ...f, modules: f.modules.map((m: CourseModule, j: number) => (j === i ? { ...m, ...p } : m)) });
  const setQ = (i: number, p: Partial<QuizQ>) => setF({ ...f, quiz: f.quiz.map((m: QuizQ, j: number) => (j === i ? { ...m, ...p } : m)) });
  return (
    <WideModal title={c.id ? c.title || "" : "+ Course"} onClose={onClose}>
      <form className="grid grid-cols-1 md:grid-cols-2 gap-3" onSubmit={(e) => { e.preventDefault(); const errs = courseErrors(f); if (Object.keys(errs).length) { toast.error(firstError(errs)); return; } save.mutate(); }}>
        <div><label className={LABEL}>Course title<Req /></label><input className={FIELD} required maxLength={200} value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /><FieldError msg={courseErrors(f).title} /></div>
        <div className="grid grid-cols-3 gap-2"><div><label className={LABEL}>Pass %</label><input type="number" className={FIELD} min={0} max={100} step={1} value={f.pass_mark} onChange={(e) => setF({ ...f, pass_mark: Number(e.target.value) })} /></div>
          <label className="flex items-center gap-2 text-sm mt-5" title="Shows on the compliance dashboards and raises overdue alerts"><input type="checkbox" checked={!!f.mandatory} onChange={(e) => setF({ ...f, mandatory: e.target.checked })} /> Mandatory</label>
          <label className="flex items-center gap-2 text-sm mt-5"><input type="checkbox" checked={f.active !== false} onChange={(e) => setF({ ...f, active: e.target.checked })} /> Active</label></div>
        <div className="md:col-span-2"><label className={LABEL}>Description</label><textarea className={FIELD} rows={2} value={f.description || ""} onChange={(e) => setF({ ...f, description: e.target.value })} /></div>
        {/* D7: category · attempts cap · question-bank draw */}
        <div className="md:col-span-2 grid grid-cols-2 md:grid-cols-4 gap-2">
          <div><label className={LABEL}>Category</label><input className={`${FIELD} !py-1.5`} maxLength={60} placeholder="Compliance / Skills…" value={f.category || ""} onChange={(e) => setF({ ...f, category: e.target.value })} /></div>
          <div><label className={LABEL}>Max quiz attempts (0 = unlimited)</label><input type="number" className={`${FIELD} !py-1.5`} min={0} max={50} value={f.max_attempts ?? 0} onChange={(e) => setF({ ...f, max_attempts: Number(e.target.value) })} /></div>
          <div><label className={LABEL}>Bank tags (comma) — draws the quiz from the question bank</label><input className={`${FIELD} !py-1.5`} placeholder="harassment, coc" value={(f.question_tags || []).join(", ")} onChange={(e) => setF({ ...f, question_tags: e.target.value.split(",").map((x: string) => x.trim()).filter(Boolean) })} /></div>
          <div><label className={LABEL}>Questions per attempt (0 = all tagged)</label><input type="number" className={`${FIELD} !py-1.5`} min={0} max={200} value={f.quiz_sample ?? 0} onChange={(e) => setF({ ...f, quiz_sample: Number(e.target.value) })} /></div>
        </div>
        <div className="md:col-span-2 space-y-2"><label className={LABEL}>{t("learn.modules")} (PDF / zip package upload after the course is saved · video / external link now)</label><FieldError msg={courseErrors(f).modules} />
          {f.modules.map((m: CourseModule, i: number) => <div key={m.key} className="grid grid-cols-12 gap-2 items-center"><input className={`${FIELD} !py-1 col-span-4`} value={m.title} onChange={(e) => setM(i, { title: e.target.value })} /><select className={`${FIELD} !py-1 col-span-2`} value={m.kind} onChange={(e) => setM(i, { kind: e.target.value as any })}><option value="pdf">pdf</option><option value="video">video</option><option value="url">link</option><option value="scorm">package</option></select><input className={`${FIELD} !py-1 col-span-4`} placeholder={m.kind === "pdf" ? (m.doc_id ? "uploaded" : "upload below") : m.kind === "scorm" ? ((m as any).pkg_id ? "package uploaded" : "upload a zip below") : "https://…"} value={m.url || ""} disabled={m.kind === "pdf" || m.kind === "scorm"} onChange={(e) => setM(i, { url: e.target.value })} /><input type="number" className={`${FIELD} !py-1 col-span-1`} placeholder="min" value={m.minutes || ""} onChange={(e) => setM(i, { minutes: Number(e.target.value) })} /><button type="button" className="text-xs text-red-600" onClick={() => setF({ ...f, modules: f.modules.filter((_: any, j: number) => j !== i) })}>✕</button></div>)}
          <div className="flex gap-2"><button type="button" className="btn-secondary text-xs" onClick={() => setF({ ...f, modules: [...f.modules, { key: `m${Date.now()}`, title: "", kind: "url", url: "", minutes: 0 }] })}>+ link / video</button>
            {c.id && <><button type="button" className="btn-secondary text-xs" onClick={() => fileRef.current?.click()}>+ PDF upload</button><input ref={fileRef} type="file" accept="application/pdf" className="hidden" onChange={(e) => { const x = e.target.files?.[0]; if (x) upload.mutate(x); }} />
              <button type="button" className="btn-secondary text-xs" onClick={() => pkgRef.current?.click()} disabled={uploadPkg.isPending}>+ Package (zip with index.html)</button><input ref={pkgRef} type="file" accept=".zip,application/zip" className="hidden" onChange={(e) => { const x = e.target.files?.[0]; if (x) uploadPkg.mutate(x); }} /></>}</div></div>
        <div className="md:col-span-2 space-y-2"><label className={LABEL}>{t("learn.quiz")} (MCQ · tick the correct option)</label><FieldError msg={courseErrors(f).quiz} />
          {f.quiz.map((q: QuizQ, i: number) => <div key={i} className="card !py-2 space-y-1"><div className="flex gap-2"><input className={`${FIELD} !py-1`} placeholder="Question" value={q.q} onChange={(e) => setQ(i, { q: e.target.value })} /><button type="button" className="text-xs text-red-600" onClick={() => setF({ ...f, quiz: f.quiz.filter((_: any, j: number) => j !== i) })}>✕</button></div>
            {q.options.map((o, j) => <div key={j} className="flex items-center gap-2"><input type="radio" name={`ans${i}`} checked={q.answer === j} onChange={() => setQ(i, { answer: j })} /><input className={`${FIELD} !py-1`} value={o} onChange={(e) => setQ(i, { options: q.options.map((x, k) => (k === j ? e.target.value : x)) })} /></div>)}
            <button type="button" className="text-xs text-primary-700" onClick={() => setQ(i, { options: [...q.options, ""] })}>+ option</button></div>)}
          <button type="button" className="btn-secondary text-xs" onClick={() => setF({ ...f, quiz: [...f.quiz, { q: "", options: ["", ""], answer: 0 }] })}>+ question</button></div>
        <div className="md:col-span-2"><button className="btn-primary" disabled={save.isPending}>{t("common.save")}</button></div>
      </form>
    </WideModal>
  );
}

function AssignSheet({ c, onClose }: { c: Course; onClose: () => void }) {
  const t = useT();
  const emps = useQuery({ queryKey: ["employees"], queryFn: () => employeeApi.list() });
  const list = ((emps.data || []) as any[]).filter((e) => e.status !== "terminated");
  const deps = Array.from(new Set(list.map((e) => e.department).filter(Boolean))) as string[];
  const [ids, setIds] = useState<string[]>([]); const [dd, setDd] = useState<string[]>([]); const [due, setDue] = useState(""); const [q, setQ] = useState("");
  const today = todayISO();
  const covered = new Set([...ids, ...list.filter((e) => dd.includes(e.department)).map((e) => e.id)]).size;
  const dueErr = due && due < today ? "Due date cannot be in the past" : null;
  const m = useMutation({ mutationFn: () => engageApi.assign({ course_id: c.id, employee_ids: ids, departments: dd, due_on: due || undefined }), onSuccess: (r) => { toast.success(`Assigned to ${r.assigned} (already had it: ${r.already})`); onClose(); }, onError: (e) => toast.error(errText(e)) });
  return (
    <WideModal title={`Assign · ${c.title}`} onClose={onClose}>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div><label className={LABEL}>Departments</label><div className="flex flex-wrap gap-1.5">{deps.map((d) => <button type="button" key={d} onClick={() => setDd(dd.includes(d) ? dd.filter((x) => x !== d) : [...dd, d])} className={`px-2 py-1 rounded-lg text-xs border ${dd.includes(d) ? "bg-primary-600 text-white border-primary-600" : "bg-white border-surface-border"}`}>{d}</button>)}</div>
          <label className={`${LABEL} mt-3`}>{t("learn.due", { date: "" })}</label><input type="date" min={today} className={FIELD} value={due} onChange={(e) => setDue(e.target.value)} /><span className="block text-[11px] text-gray-400 mt-0.5">Blank = no deadline (overdue tracking needs a date)</span><FieldError msg={dueErr} />
          <span className="block text-[11px] text-gray-400 mt-2">A department covers everyone active in it, including people not ticked on the right.</span></div>
        <div><label className={LABEL}>People ({ids.length})</label><input className={`${FIELD} mb-1`} placeholder={t("common.search")} value={q} onChange={(e) => setQ(e.target.value)} />
          <div className="max-h-64 overflow-y-auto border border-surface-border rounded-xl divide-y divide-surface-border">{list.filter((e) => !q || `${e.first_name} ${e.last_name}`.toLowerCase().includes(q.toLowerCase())).map((e) => <label key={e.id} className="flex items-center gap-2 px-2 py-1.5 text-sm"><input type="checkbox" checked={ids.includes(e.id)} onChange={(ev) => setIds(ev.target.checked ? [...ids, e.id] : ids.filter((x) => x !== e.id))} /> {e.first_name} {e.last_name} <span className="text-[11px] text-gray-400">{e.department || ""}</span></label>)}</div></div>
        <div className="md:col-span-2"><button className="btn-primary" disabled={m.isPending || (!ids.length && !dd.length) || !!dueErr} onClick={() => m.mutate()}>Assign{covered ? ` · ${covered} people` : ""}</button></div>
      </div>
    </WideModal>
  );
}

function Compliance() {
  const t = useT();
  const lang = useLang();
  const q = useQuery({ queryKey: ["lms-compliance"], queryFn: () => engageApi.compliance() });
  const r = q.data;
  const cols: GridColumn[] = [{ key: "title", label: "Course" }, { key: "assigned", label: "Assigned", type: "number" }, { key: "completed", label: t("learn.completed"), type: "number" }, { key: "pending", label: "Pending", type: "number" }, { key: "overdue", label: t("learn.overdue"), type: "number" }, { key: "compliance_pct", label: "Compliance %", type: "percent" }];
  const pcols: GridColumn<Assignment>[] = [{ key: "employee_name", label: t("team.person") }, { key: "course_title", label: "Course" }, { key: "due_on", label: "Due", render: (x) => fmtDate(x.due_on, lang), text: (x) => x.due_on || "" }, { key: "state", label: t("common.status") }, { key: "progress_pct", label: "Progress", type: "percent" }];
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3"><StatTile label="Mandatory assignments" value={r?.assigned ?? "—"} accent /><StatTile label={t("learn.completed")} value={r?.completed ?? "—"} /><StatTile label="Compliance" value={r?.compliance_pct != null ? `${r.compliance_pct}%` : "—"} /></div>
      <DataGrid columns={cols} rows={r?.courses || []} rowKey={(x) => x.course_id} minWidth={700} exportName="training-compliance" />
      <h3 className="font-display font-semibold text-gray-800">Pending people</h3>
      <DataGrid columns={pcols} rows={r?.pending_people || []} rowKey={(x) => x.id} minWidth={700} exportName="training-pending" />
    </div>
  );
}
