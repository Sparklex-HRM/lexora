import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Award, BookOpen, CheckCircle2, Download, ExternalLink, PlayCircle, Printer } from "lucide-react";
import { printPdfBlob } from "../../lib/print";   // print wrap-up 2026-10-24
import DataGrid, { WideModal, type GridColumn } from "../../components/DataGrid";
import { PageHeader, Spinner, StatTile } from "../../components/ui";
import { engageApi, type Assignment, type CourseModule } from "../../api/engage";
import { learningApi, type PathProgress, type QuizSet } from "../../api/learning";
import { day, errText } from "../../api/platform";
import { openSafe, safeHref } from "../../lib/safeUrl";

const STATE: Record<string, string> = { completed: "bg-green-100 text-green-700", overdue: "bg-red-100 text-red-700", in_progress: "bg-amber-100 text-amber-800", assigned: "bg-gray-100 text-gray-700" };

/** /learning/my — ESS "My learning": summary, learning paths with step progress, every assignment as a dense row,
 *  module player (PDF / link / sandboxed package), quiz (own questions or a bank draw with attempt limits), certificates. */
export default function MyLearningPage() {
  const qc = useQueryClient();
  const my = useQuery({ queryKey: ["lms-my"], queryFn: learningApi.my });
  const asg = useQuery({ queryKey: ["m-learn"], queryFn: () => engageApi.assignments("mine") });
  const [open, setOpen] = useState<Assignment | null>(null);
  const [quiz, setQuiz] = useState<Assignment | null>(null);
  const [player, setPlayer] = useState<{ a: Assignment; m: CourseModule } | null>(null);
  const refresh = () => { qc.invalidateQueries({ queryKey: ["lms-my"] }); qc.invalidateQueries({ queryKey: ["m-learn"] }); };
  const done = useMutation({ mutationFn: ({ id, key }: { id: string; key: string }) => engageApi.progress(id, key), onSuccess: (r) => { refresh(); setOpen(r); }, onError: (e) => toast.error(errText(e)) });
  const rows = asg.data?.assignments || [];
  const cols: GridColumn<Assignment>[] = [
    { key: "course_title", label: "Course", className: "font-medium text-gray-800" },
    { key: "category", label: "Category", width: "110px" },
    { key: "mandatory", label: "Mandatory", width: "90px", text: (r) => (r.mandatory ? "yes" : "no"), render: (r) => (r.mandatory ? <span className="badge-red">mandatory</span> : "—") },
    { key: "due_on", label: "Due", type: "date", width: "110px" },
    { key: "state", label: "Status", width: "110px", render: (r) => <span className={`inline-block text-[11px] font-semibold px-2 py-0.5 rounded-full ${STATE[r.state] || STATE.assigned}`}>{r.state.replace("_", " ")}</span> },
    { key: "progress_pct", label: "Progress", type: "percent", width: "90px" },
    { key: "modules", label: "Modules", type: "number", text: (r) => String(r.modules.length), render: (r) => `${r.completed_modules.length}/${r.modules.length}` },
    { key: "quiz_score", label: "Quiz", type: "number", width: "80px", render: (r) => (r.quiz.length || (r as any).from_bank) ? (r.quiz_score != null ? `${r.quiz_score}%` : "—") : "none" },
    { key: "certificate_no", label: "Certificate", width: "130px" },
    { key: "act", label: "", sortable: false, render: (r) => <button className="text-xs text-primary-700 font-semibold" onClick={(e) => { e.stopPropagation(); setOpen(r); }}>Open</button> },
  ];
  if (asg.isLoading || my.isLoading) return <div className="grid place-items-center py-24"><Spinner className="h-8 w-8" /></div>;
  const m = my.data;
  return (
    <div>
      <PageHeader title="My learning" icon={BookOpen} subtitle="Your courses, learning paths and certificates" />
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-4">
        <StatTile label="Assigned" value={m?.assigned ?? 0} accent /><StatTile label="Completed" value={m?.completed ?? 0} />
        <StatTile label="Due soon" value={m?.due_soon ?? 0} /><StatTile label="Overdue" value={m?.overdue ?? 0} />
        <StatTile label="Certificates" value={m?.certificates.length ?? 0} icon={Award} />
      </div>
      {!!m?.paths.length && (
        <div className="grid md:grid-cols-2 gap-3 mb-4">
          {m.paths.map((p: PathProgress) => (
            <div key={p.path_id} className="card !py-3">
              <div className="flex items-center justify-between gap-2"><div className="font-semibold text-gray-800">{p.title}{p.mandatory && <span className="badge-red ml-2">mandatory</span>}</div><span className="text-xs text-gray-500">{p.completed}/{p.total} · {p.due_on ? `due ${day(p.due_on)}` : ""}</span></div>
              <div className="h-1.5 bg-gray-100 rounded mt-2"><div className="h-1.5 bg-primary-500 rounded" style={{ width: `${p.pct}%` }} /></div>
              <ol className="mt-2 space-y-1">{p.steps.map((s) => <li key={s.course_id} className="flex items-center gap-2 text-sm">{s.status === "completed" ? <CheckCircle2 className="w-4 h-4 text-green-600" /> : <span className="w-4 h-4 rounded-full border border-gray-300 inline-block" />}<span className="flex-1 truncate">{s.n}. {s.title}</span><span className="text-[11px] text-gray-400">{s.status.replace("_", " ")}</span>
                {s.assignment_id && <button className="text-xs text-primary-700" onClick={() => { const a = rows.find((x) => x.id === s.assignment_id); if (a) setOpen(a); }}>open</button>}</li>)}</ol>
            </div>))}
        </div>)}
      <DataGrid<Assignment> columns={cols} rows={rows} rowKey={(r) => r.id} minWidth={960} exportName="my-learning" onRowClick={setOpen} initialSort={{ key: "due_on", dir: "asc" }} empty="No courses assigned to you yet." density="compact" />
      {open && <CourseModal a={rows.find((x) => x.id === open.id) || open} onClose={() => setOpen(null)} onModule={(mm) => setPlayer({ a: open, m: mm })} onDone={(k) => done.mutate({ id: open.id, key: k })} onQuiz={() => setQuiz(open)} />}
      {quiz && <QuizModal a={quiz} onClose={() => { setQuiz(null); refresh(); }} />}
      {player && <PlayerModal a={player.a} m={player.m} onClose={() => setPlayer(null)} onDone={() => { done.mutate({ id: player.a.id, key: player.m.key }); setPlayer(null); }} />}
    </div>
  );
}

function CourseModal({ a, onClose, onModule, onDone, onQuiz }: { a: Assignment; onClose: () => void; onModule: (m: CourseModule) => void; onDone: (key: string) => void; onQuiz: () => void }) {
  const hasQuiz = a.quiz.length > 0 || (a as any).from_bank;
  return (
    <WideModal title={a.course_title || "Course"} onClose={onClose}>
      <div className="grid md:grid-cols-[1fr_18rem] gap-4">
        <div>
          <div className="text-xs text-gray-500 mb-2">{a.mandatory ? "Mandatory · " : ""}{a.due_on ? `due ${day(a.due_on)} · ` : ""}{a.progress_pct}% done · {a.state.replace("_", " ")}</div>
          <div className="card !p-0 overflow-x-auto">
            <table className="w-full text-[13px]"><thead className="bg-surface text-[11px] uppercase tracking-wide text-gray-500 text-left"><tr><th className="px-3 py-2 w-8"></th><th className="px-3 py-2">Module</th><th className="px-3 py-2">Type</th><th className="px-3 py-2 text-right">Min</th><th className="px-3 py-2"></th></tr></thead>
              <tbody>{a.modules.map((mm) => { const ok = a.completed_modules.includes(mm.key); return (
                <tr key={mm.key} className="border-t border-surface-border"><td className="px-3 py-1.5">{ok ? <CheckCircle2 className="w-4 h-4 text-green-600" /> : <span className="w-4 h-4 rounded-full border border-gray-300 inline-block" />}</td>
                  <td className="px-3 py-1.5 text-gray-800">{mm.title}</td><td className="px-3 py-1.5 text-gray-500">{mm.kind === "scorm" ? "interactive" : mm.kind}</td><td className="px-3 py-1.5 text-right tabular-nums">{mm.minutes || "—"}</td>
                  <td className="px-3 py-1.5 text-right whitespace-nowrap"><button className="text-xs text-primary-700 inline-flex items-center gap-1 mr-3" onClick={() => onModule(mm)}>{mm.kind === "scorm" ? <PlayCircle className="w-3.5 h-3.5" /> : <ExternalLink className="w-3.5 h-3.5" />} Open</button>
                    {!ok && a.status !== "completed" && <button className="text-xs font-semibold text-green-700" onClick={() => onDone(mm.key)}>Mark done</button>}</td></tr>); })}
                {hasQuiz && <tr className="border-t border-surface-border"><td className="px-3 py-1.5">{a.quiz_passed ? <CheckCircle2 className="w-4 h-4 text-green-600" /> : <span className="w-4 h-4 rounded-full border border-gray-300 inline-block" />}</td>
                  <td className="px-3 py-1.5 text-gray-800">Quiz{(a as any).from_bank ? " (questions drawn from the bank)" : ""}</td><td className="px-3 py-1.5 text-gray-500">pass mark {a.pass_mark}% · attempts {a.quiz_attempts}{(a as any).max_attempts ? ` / ${(a as any).max_attempts}` : ""}</td><td className="px-3 py-1.5 text-right tabular-nums">{a.quiz_score != null ? `${a.quiz_score}%` : "—"}</td>
                  <td className="px-3 py-1.5 text-right">{!a.quiz_passed && <button className="text-xs font-semibold text-primary-700" onClick={onQuiz}>Take the quiz</button>}</td></tr>}
              </tbody></table>
          </div>
        </div>
        <div className="space-y-2">
          {a.status === "completed" ? <div className="card !py-3 bg-green-50 border-green-200"><div className="text-sm font-semibold text-green-800">Completed {day(a.completed_at)}</div><div className="text-xs text-gray-600">Certificate {a.certificate_no}</div>
            <button className="btn-primary text-sm mt-2 inline-flex items-center gap-1" onClick={() => engageApi.certificateBlob(a.id).then((u) => window.open(u, "_blank")).catch((e) => toast.error(errText(e)))}><Download className="w-4 h-4" /> Certificate PDF</button>
              <button className="btn-secondary text-sm mt-2 ml-2 inline-flex items-center gap-1" title="Print the certificate (A4)" onClick={() => engageApi.certificatePdf(a.id).then((b) => printPdfBlob(b, "certificate.pdf")).catch((e) => toast.error(errText(e)))}><Printer className="w-4 h-4" /> Print</button></div>
            : <div className="card !py-3 text-sm text-gray-600">Finish every module{hasQuiz ? " and pass the quiz" : ""} to get your certificate.</div>}
        </div>
      </div>
    </WideModal>
  );
}

function QuizModal({ a, onClose }: { a: Assignment; onClose: () => void }) {
  const set = useQuery({ queryKey: ["quiz-set", a.id], queryFn: () => learningApi.quizSet(a.id) });
  const [ans, setAns] = useState<number[]>([]);
  const [result, setResult] = useState<string | null>(null);
  useEffect(() => { if (set.data) setAns(set.data.questions.map(() => -1)); }, [set.data]);
  const m = useMutation({ mutationFn: () => engageApi.quiz(a.id, ans), onSuccess: (r) => setResult(r.message || ""), onError: (e) => toast.error(errText(e)) });
  const s: QuizSet | undefined = set.data;
  return (
    <WideModal title={`Quiz · ${a.course_title}`} onClose={onClose}>
      {!s ? <Spinner /> : result ? <div className="space-y-3"><div className="card text-sm">{result}</div><button className="btn-primary" onClick={onClose}>Close</button></div> : (
        <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); if (ans.some((x) => x < 0)) return toast.error("Answer every question"); m.mutate(); }}>
          <div className="text-xs text-gray-500">Pass mark {s.pass_mark}% · attempt {s.attempts + 1}{s.max_attempts ? ` of ${s.max_attempts}` : ""}{s.from_bank ? " · questions drawn from the bank for this attempt" : ""}</div>
          {s.attempts_left === 0 ? <div className="card text-sm text-red-700 bg-red-50 border-red-200">No attempts left. Ask HR to reset the quiz.</div> : (
            <div className="grid md:grid-cols-2 gap-3">{s.questions.map((q, i) => <div key={i} className="card !py-3"><div className="text-sm font-medium text-gray-800 mb-1.5">{i + 1}. {q.q}</div><div className="space-y-1">{q.options.map((o, j) => <label key={j} className={`flex items-center gap-2 rounded-lg border px-3 py-1.5 text-sm cursor-pointer ${ans[i] === j ? "border-primary-500 bg-primary-50" : "border-surface-border"}`}><input type="radio" name={`q${i}`} checked={ans[i] === j} onChange={() => setAns(ans.map((x, k) => (k === i ? j : x)))} /> {o}</label>)}</div></div>)}</div>)}
          {s.attempts_left !== 0 && s.questions.length > 0 && <button className="btn-primary" disabled={m.isPending}>Submit answers</button>}
        </form>)}
    </WideModal>
  );
}

function PlayerModal({ a, m, onClose, onDone }: { a: Assignment; m: CourseModule; onClose: () => void; onDone: () => void }) {
  const [html, setHtml] = useState<string | null>(null);
  const [pdf, setPdf] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let revoke: (() => void) | null = null;
    if (m.kind === "scorm" && (m as any).pkg_id) learningApi.packageDocument((m as any).pkg_id).then((r) => { setHtml(r.html); revoke = r.revoke; }).catch((e) => setErr(errText(e)));
    else if (m.kind === "pdf" && m.doc_id) engageApi.lmsFileBlob(m.doc_id).then(setPdf).catch((e) => setErr(errText(e)));
    else if (m.url) { openSafe(m.url); }
    return () => { if (revoke) revoke(); if (pdf) URL.revokeObjectURL(pdf); };
  }, [m]);   // eslint-disable-line react-hooks/exhaustive-deps
  const ok = a.completed_modules.includes(m.key);
  return (
    <WideModal title={`${m.title} · ${a.course_title}`} onClose={onClose} footer={<>{!ok && a.status !== "completed" && <button className="btn-primary" onClick={onDone}>Mark as done</button>}<button className="btn-secondary" onClick={onClose}>Close</button></>}>
      {err && <div className="card text-sm text-red-600">{err}</div>}
      {m.kind === "scorm" && (html ? <iframe title={m.title} sandbox="allow-scripts" srcDoc={html} className="w-full h-[70vh] rounded-lg border border-surface-border bg-white" /> : !err && <div className="grid place-items-center py-12"><Spinner /></div>)}
      {m.kind === "pdf" && (pdf ? <iframe title={m.title} src={pdf} className="w-full h-[70vh] rounded-lg border border-surface-border" /> : !err && <div className="grid place-items-center py-12"><Spinner /></div>)}
      {(m.kind === "url" || m.kind === "video") && <div className="card text-sm text-gray-700">The link opened in a new tab. {safeHref(m.url) ? <a className="text-primary-700 underline" href={safeHref(m.url)} target="_blank" rel="noopener noreferrer">Open again</a> : <span className="text-gray-500">(this module's link is not a web address)</span>}</div>}
    </WideModal>
  );
}
