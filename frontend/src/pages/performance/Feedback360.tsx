import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import toast from "react-hot-toast";
import { err, fmtDate } from "../hiring/api";
import { Col, empLabel, Field, Grid, Input, Modal, Note, Select, Textarea, today, useEmployees } from "../hiring/ui";
import { FormGrid, FormSection } from "../../components/Modal";
import { collect, FieldError, firstError, Req, v } from "../../lib/validators";
import { perfApi, type Row } from "./api";
import { fbApi } from "./api_g4";

/* G4: 360 feedback. Answers are never shown one by one to the person reviewed; the report groups
   them and hides any group with fewer answers than the threshold (minimum 3) — decided by the server. */

const NEXT: Record<string, [string, string]> = { draft: ["open_nominations", "Open: people pick reviewers"], nominating: ["start_collecting", "Start collecting feedback"],
  collecting: ["close", "Close (HR and managers can read)"], closed: ["release", "Release reports to employees"] };

export default function Feedback360() {
  const rounds = useQuery({ queryKey: ["perf", "360", "rounds"], queryFn: fbApi.rounds });
  const reqs = useQuery({ queryKey: ["perf", "360", "mine"], queryFn: fbApi.myRequests });
  const [newRound, setNewRound] = useState(false);
  const [openRound, setOpenRound] = useState<string | null>(null);
  const [answer, setAnswer] = useState<Row | null>(null);
  const isHr = !!rounds.data?.is_hr;
  const items: Row[] = reqs.data?.items || [];
  const reqCols: Col<Row>[] = [
    { k: "subject_name", label: "About", render: (r) => <span className="font-medium text-gray-800">{r.subject_name}</span> },
    { k: "relationship_label", label: "You are their" },
    { k: "round", label: "Round" }, { k: "due_date", label: "Due", render: (r) => fmtDate(r.due_date) },
    { k: "anonymous", label: "Shown as", render: (r) => (r.anonymous ? `Combined with at least ${r.threshold - 1} others` : r.relationship === "self" ? "Your own view" : "Manager (named)"), csv: (r) => (r.anonymous ? "anonymous" : "named") },
    { k: "status", label: "Status", render: (r) => (r.status === "submitted" ? <span className="badge-green">Sent</span> : r.status === "declined" ? <span className="badge-amber">Declined</span> : r.can_answer ? <span className="badge-purple">Waiting for you</span> : "Closed") },
  ];
  const roundCols: Col<Row>[] = [
    { k: "name", label: "Round", render: (r) => <span className="font-medium text-gray-800">{r.name}</span> },
    { k: "status_label", label: "Status" }, { k: "min_responses", label: "Anonymity threshold", align: "right" },
    { k: "nominate_by", label: "Reviewers picked by", render: (r) => ({ employee: "Employee (manager approves)", manager: "Manager", hr: "HR" } as Row)[r.nominate_by] },
    { k: "due_date", label: "Due", render: (r) => fmtDate(r.due_date) },
  ];
  return (
    <div className="space-y-4">
      <Note>Feedback to peers, reports and others is combined: a group is shown only when at least 3 people in it answered, and nobody sees who wrote what. Your manager's feedback is shown as the manager's.</Note>
      <Grid rows={items} cols={reqCols} keyOf={(r) => r.id} loading={reqs.isLoading} title="Feedback I have been asked for" onRow={(r) => setAnswer(r)}
        empty={reqs.data && !reqs.data.linked ? "Your login is not linked to an employee record." : "Nobody has asked you for feedback right now."} />
      <Grid rows={rounds.data?.rounds || []} cols={roundCols} keyOf={(r) => r.id} loading={rounds.isLoading} title={isHr ? "All 360 rounds" : "Rounds I take part in"} onRow={(r) => setOpenRound(r.id)}
        empty={isHr ? "No 360 round yet. Press New round." : "You are not part of a 360 round yet."}
        actions={isHr && <button className="btn-primary !py-1 !px-2.5 text-xs inline-flex items-center gap-1" onClick={() => setNewRound(true)}><Plus className="w-3.5 h-3.5" />New round</button>} />
      {newRound && <RoundForm onClose={() => setNewRound(false)} />}
      {openRound && <RoundModal id={openRound} onClose={() => setOpenRound(null)} />}
      {answer && <AnswerModal item={answer} onClose={() => setAnswer(null)} />}
    </div>
  );
}

function RoundForm({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const defs = useQuery({ queryKey: ["perf", "360", "defaults"], queryFn: fbApi.defaults });
  const cycles = useQuery({ queryKey: ["perf", "cycles"], queryFn: perfApi.cycles, retry: false });
  const [f, setF] = useState<Row>({ name: "", review_cycle_id: "", min_responses: 3, peers_min: 3, peers_max: 8, nominate_by: "employee", include_upward: true, include_self: true, due_date: "" });
  const [qs, setQs] = useState<Row[] | null>(null);
  const [tried, setTried] = useState(false);
  const questions = qs || defs.data?.questions || [];
  const upd = (i: number, k: string, val: any) => setQs(questions.map((q: Row, j: number) => (j === i ? { ...q, [k]: val } : q)));
  const whole = (s: any, label: string) => (String(s ?? "").trim() !== "" && !Number.isInteger(Number(s)) ? `${label} must be a whole number` : null);
  const errs = collect({
    name: v.required(String(f.name).trim(), "Name") || v.maxLen(String(f.name).trim(), 160, "Name"),
    min_responses: v.required(f.min_responses, "Anonymity threshold") || whole(f.min_responses, "Anonymity threshold") || v.range(f.min_responses, 3, 10, "Anonymity threshold"),
    peers_min: whole(f.peers_min, "Minimum peers") || v.range(f.peers_min, 0, 15, "Minimum peers"),
    peers_max: whole(f.peers_max, "Maximum peers") || v.range(f.peers_max, 1, 20, "Maximum peers")
      || (f.peers_min !== "" && f.peers_max !== "" && Number(f.peers_min) > Number(f.peers_max) ? "Maximum peers cannot be less than the minimum" : null),
    due_date: f.due_date && f.due_date < today() ? "The due date cannot be in the past" : null,
    questions: !questions.length ? "Add at least one question"
      : questions.some((q: Row) => !String(q.text || "").trim()) ? "Every question needs its text (or remove the empty row)"
        : !questions.some((q: Row) => (q.kind || "rating") === "rating") ? "Add at least one rating question" : null,
  });
  const show = (k: string) => (tried || (k !== "name" && k !== "questions") ? errs[k] : null);
  const save = useMutation({
    mutationFn: () => fbApi.createRound({ ...f, name: String(f.name).trim(), review_cycle_id: f.review_cycle_id || null, due_date: f.due_date || null, questions }),
    onSuccess: () => { toast.success("Round created. Add the people to be reviewed next."); qc.invalidateQueries({ queryKey: ["perf", "360"] }); onClose(); },
    onError: (e) => toast.error(err(e), { duration: 7000 }),
  });
  const onSave = () => { setTried(true); if (Object.keys(errs).length) { toast.error(firstError(errs)); return; } save.mutate(); };
  const set = (k: string, val: any) => setF({ ...f, [k]: val });
  const bad = (s: any) => (tried && !String(s || "").trim() ? "!border-red-400" : "");
  return (
    <Modal title="New 360 round" subtitle="The questions freeze once reviewers are being picked." onClose={onClose}
      footer={<><button className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary disabled:opacity-50" disabled={save.isPending} onClick={onSave}>Create round</button></>}>
      <FormSection title="Round">
        <FormGrid cols={4}>
          <div className="sm:col-span-2"><Field label={<>Name<Req /></>}><Input maxLength={160} value={f.name} onChange={(e) => set("name", e.target.value)} placeholder="H2 FY 2026-27 360" /></Field><FieldError msg={show("name")} /></div>
          <div className="sm:col-span-2"><Field label="Linked appraisal cycle" hint="Optional. Ties this round to the appraisal year it feeds into.">
            <Select value={f.review_cycle_id} onChange={(e) => set("review_cycle_id", e.target.value)}><option value="">Not linked</option>
              {(cycles.data || []).map((c: Row) => <option key={c.id} value={c.id}>{c.name}</option>)}</Select></Field></div>
          <div><Field label="Due date" hint="Last day to send feedback."><Input type="date" min={today()} value={f.due_date} onChange={(e) => set("due_date", e.target.value)} /></Field><FieldError msg={show("due_date")} /></div>
          <Field label="Reviewers picked by" hint="Employee picks still need the manager's approval."><Select value={f.nominate_by} onChange={(e) => set("nominate_by", e.target.value)}><option value="employee">Employee, manager approves</option><option value="manager">Manager</option><option value="hr">HR</option></Select></Field>
        </FormGrid>
      </FormSection>
      <FormSection title="Who gives feedback" hint="Peer and direct-report answers are combined; a group is shown only when it has at least the threshold number of answers.">
        <FormGrid cols={4}>
          <div><Field label={<>Anonymity threshold<Req /></>} hint="Answers per group before it is shown. 3 to 10."><Input type="number" min={3} max={10} step={1} value={f.min_responses} onChange={(e) => set("min_responses", e.target.value)} /></Field><FieldError msg={show("min_responses")} /></div>
          <div><Field label="Peers per person (min)" hint="Keep at or above the threshold, or peer answers stay hidden."><Input type="number" min={0} max={15} step={1} value={f.peers_min} onChange={(e) => set("peers_min", e.target.value)} /></Field><FieldError msg={show("peers_min")} />
            {!errs.peers_min && Number(f.peers_min) > 0 && Number(f.peers_min) < Number(f.min_responses) && <span className="block text-[11px] text-amber-700 mt-0.5">Below the threshold: peer answers may not be shown.</span>}</div>
          <div><Field label="Peers per person (max)"><Input type="number" min={1} max={20} step={1} value={f.peers_max} onChange={(e) => set("peers_max", e.target.value)} /></Field><FieldError msg={show("peers_max")} /></div>
          <Field label="Upward feedback" hint="Direct reports rate their manager, anonymously."><Select value={f.include_upward ? "1" : "0"} onChange={(e) => set("include_upward", e.target.value === "1")}><option value="1">Direct reports rate their manager</option><option value="0">Off</option></Select></Field>
          <Field label="Self review"><Select value={f.include_self ? "1" : "0"} onChange={(e) => set("include_self", e.target.value === "1")}><option value="1">Included</option><option value="0">Off</option></Select></Field>
        </FormGrid>
      </FormSection>
      <FormSection title="Questions" hint="Ratings run 1 (rarely) to 5 (consistently). A blank competency is grouped as General. At least one rating question is needed.">
      <FieldError msg={show("questions")} />
      <div className="overflow-x-auto">
        <table className="w-full text-xs"><thead className="text-[10px] uppercase text-gray-400 text-left"><tr><th className="py-1">Competency</th><th>Question *</th><th>Answer</th><th>Asked of</th><th /></tr></thead>
          <tbody>{questions.map((q: Row, i: number) => (
            <tr key={i} className="border-t border-surface-border">
              <td className="py-1 pr-1 w-44"><Input value={q.competency} maxLength={80} placeholder="General" onChange={(e) => upd(i, "competency", e.target.value)} /></td>
              <td className="pr-1 min-w-[18rem]"><Input value={q.text} maxLength={400} className={bad(q.text)} onChange={(e) => upd(i, "text", e.target.value)} /></td>
              <td className="pr-1 w-32"><select className="input !py-1.5 text-xs w-full" value={q.kind} onChange={(e) => upd(i, "kind", e.target.value)}><option value="rating">Rating 1-5</option><option value="text">Written</option></select></td>
              <td className="pr-1 w-40"><select className="input !py-1.5 text-xs w-full" value={q.upward ? "1" : "0"} onChange={(e) => upd(i, "upward", e.target.value === "1")}><option value="0">Self, manager, peers</option><option value="1">Direct reports (upward)</option></select></td>
              <td><button className="icon-btn" title="Remove" onClick={() => setQs(questions.filter((_: Row, j: number) => j !== i))}><Trash2 className="w-3.5 h-3.5 text-gray-400" /></button></td>
            </tr>))}</tbody></table>
      </div>
      <button className="btn-secondary !py-1 !px-2 text-xs inline-flex items-center gap-1 mt-2" onClick={() => setQs([...questions, { competency: "", text: "", kind: "rating", upward: false }])}><Plus className="w-3 h-3" />Add a question</button>
      </FormSection>
    </Modal>
  );
}

function RoundModal({ id, onClose }: { id: string; onClose: () => void }) {
  const qc = useQueryClient();
  const employees = useEmployees();
  const q = useQuery({ queryKey: ["perf", "360", "round", id], queryFn: () => fbApi.round(id) });
  const [pick, setPick] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const [dept, setDept] = useState("");
  const [nom, setNom] = useState<Row>({});
  const [report, setReport] = useState<string | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["perf", "360"] });
  const ok = (m: string) => () => { toast.success(m); refresh(); };
  const fail = (e: any) => toast.error(err(e), { duration: 7000 });
  const advance = useMutation({ mutationFn: (a: string) => fbApi.advance(id, a), onSuccess: ok("Round moved on. People have been told."), onError: fail });
  const add = useMutation({ mutationFn: (all: boolean) => fbApi.addSubjects(id, all ? { all: true } : { employee_ids: pick }), onSuccess: (r) => { toast.success(`${r.added} added.`); setPick([]); refresh(); }, onError: fail });
  const nominate = useMutation({ mutationFn: (v: { sid: string; rid: string; rel: string }) => fbApi.nominate(v.sid, { reviewer_employee_id: v.rid, relationship: v.rel }),
    onSuccess: (r) => { toast.success(r.status === "proposed" ? "Added. Your manager approves the list." : "Reviewer added."); setNom({}); refresh(); }, onError: fail });
  const decide = useMutation({ mutationFn: (v: { nid: string; ok: boolean }) => fbApi.decide(v.nid, v.ok), onSuccess: ok("Decision saved."), onError: fail });
  const withdraw = useMutation({ mutationFn: fbApi.withdraw, onSuccess: ok("Removed."), onError: fail });
  if (q.isLoading || !q.data) return <Modal title="360 round" onClose={onClose}><div className="text-sm text-gray-400">Loading…</div></Modal>;
  const r: Row = q.data.round, isHr = q.data.is_hr, subjects: Row[] = q.data.subjects || [];
  const next = NEXT[r.status];
  const inRound = new Set(subjects.map((s) => String(s.employee_id)));
  const depts = [...new Set(employees.map((e: any) => e.department).filter(Boolean))].sort() as string[];
  const needle = search.trim().toLowerCase();
  const candidates = employees.filter((e: any) => !inRound.has(String(e.id)) && (!dept || e.department === dept)
    && (!needle || `${e.first_name} ${e.last_name} ${e.employee_number} ${e.job_title || ""}`.toLowerCase().includes(needle)));
  return (
    <Modal size="screen" title={r.name} subtitle={`${r.status_label} · anonymity threshold ${r.min_responses}`} onClose={onClose}
      footer={<>{isHr && next && <button className="btn-primary disabled:opacity-50" disabled={advance.isPending} onClick={() => { if (window.confirm(`${next[1]}?`)) advance.mutate(next[0]); }}>{next[1]}</button>}<button className="btn-secondary" onClick={onClose}>Close</button></>}>
      {isHr && ["draft", "nominating"].includes(r.status) && (
        <div className="card !p-3 mb-3">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-primary-700 mb-2">Add people to be reviewed</div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-[1fr_14rem_2fr] gap-3 items-start">
            <Field label="Search" hint="Type a name, employee number or designation to narrow the list."><Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Name or number" /></Field>
            <Field label="Department"><Select value={dept} onChange={(e) => setDept(e.target.value)}><option value="">All departments</option>{depts.map((d) => <option key={d} value={d}>{d}</option>)}</Select></Field>
            <div className="sm:col-span-2 lg:col-span-1"><Field label={`Pick (${candidates.length} shown, ${pick.length} picked)`} hint="Hold Ctrl to pick several. Self, the manager and (upward) direct reports are added as reviewers automatically.">
              <select multiple className="input text-sm w-full h-28" value={pick} onChange={(e) => setPick(Array.from(e.target.selectedOptions).map((o) => o.value))}>{candidates.map((e: any) => <option key={e.id} value={e.id}>{empLabel(e)}{e.department ? ` · ${e.department}` : ""}</option>)}</select></Field></div>
          </div>
          <div className="flex flex-wrap gap-2 mt-2 justify-end">
            {!!candidates.length && (needle || dept) && <button className="btn-secondary !py-1 text-xs" onClick={() => setPick([...new Set([...pick, ...candidates.map((e: any) => e.id)])])}>Pick all {candidates.length} shown</button>}
            <button className="btn-primary disabled:opacity-50" disabled={!pick.length || add.isPending} title={!pick.length ? "Pick at least one employee" : ""} onClick={() => add.mutate(false)}>Add picked ({pick.length})</button>
            <button className="btn-secondary disabled:opacity-50" disabled={add.isPending} onClick={() => { if (window.confirm("Add every active employee to this round?")) add.mutate(true); }}>Add everyone</button>
          </div>
        </div>)}
      {!subjects.length ? <div className="text-sm text-gray-400 text-center py-8">{isHr ? "Nobody added yet." : "Nothing for you in this round yet."}</div> : (
        <div className="space-y-2">{subjects.map((s) => (
          <div key={s.id} className="border border-surface-border rounded-lg p-3 text-xs">
            <div className="flex flex-wrap items-center gap-2 mb-1">
              <b className="text-sm text-gray-800">{s.scope === "self" ? "You" : s.employee_name}</b>
              <span className="text-gray-500">Manager: {s.manager_name || "—"}</span>
              <span className="text-gray-500">Peers {s.counts.peer} · reports {s.counts.direct_report} · others {s.counts.other}</span>
              {s.peers_pending > 0 && <span className="badge-amber">{s.peers_pending} waiting for approval</span>}
              {s.submitted != null && <span className="text-gray-500">{s.submitted} answered</span>}
              <span className="ml-auto" />
              {s.can_read_report && <button className="btn-primary !py-0.5 !px-2 text-[11px]" onClick={() => setReport(s.id)}>Read report</button>}
            </div>
            <div className="flex flex-wrap gap-1">{(s.nominations || []).map((n: Row) => (
              <span key={n.id} className="inline-flex items-center gap-1 border border-surface-border rounded-full px-2 py-0.5">
                {n.reviewer_name} <span className="text-gray-400">({n.relationship_label})</span>
                {n.status === "proposed" && <span className="badge-amber !text-[10px]">waiting</span>}
                {n.status === "rejected" && <span className="badge-red !text-[10px]">not approved</span>}
                {s.scope !== "self" && n.status === "submitted" && <span className="badge-green !text-[10px]">answered</span>}
                {n.status === "proposed" && s.scope !== "self" && <><button className="underline text-green-700" onClick={() => decide.mutate({ nid: n.id, ok: true })}>approve</button><button className="underline text-red-600" onClick={() => decide.mutate({ nid: n.id, ok: false })}>no</button></>}
                {r.status === "nominating" && n.relationship !== "self" && n.relationship !== "manager" && (s.scope !== "self" || n.status === "proposed") && <button className="text-gray-400" title="Remove" onClick={() => withdraw.mutate(n.id)}><Trash2 className="w-3 h-3" /></button>}
              </span>))}</div>
            {r.status === "nominating" && (
              <div className="flex flex-wrap items-end gap-2 mt-2">
                <div className="w-full sm:w-72"><Select value={nom.sid === s.id ? nom.rid || "" : ""} onChange={(e) => setNom({ sid: s.id, rid: e.target.value, rel: nom.sid === s.id ? nom.rel || "peer" : "peer" })} aria-label="Reviewer">
                  <option value="">Pick a colleague to give feedback</option>{employees.filter((e: any) => e.id !== s.employee_id && !(s.nominations || []).some((n: Row) => String(n.reviewer_employee_id) === String(e.id) && n.status !== "rejected")).map((e: any) => <option key={e.id} value={e.id}>{empLabel(e)}</option>)}</Select></div>
                <div className="w-full sm:w-40"><Select title="Sets how the answer is grouped in the report" value={nom.sid === s.id ? nom.rel || "peer" : "peer"} onChange={(e) => setNom({ ...nom, sid: s.id, rel: e.target.value })}>
                  <option value="peer">Peer</option><option value="other">Other</option>{s.scope !== "self" && <option value="direct_report">Direct report</option>}</Select></div>
                <button className="btn-secondary !py-1 !px-2 text-xs disabled:opacity-50" disabled={nom.sid !== s.id || !nom.rid || nominate.isPending} onClick={() => nominate.mutate({ sid: s.id, rid: nom.rid, rel: nom.rel || "peer" })}>Add reviewer</button>
              </div>)}
          </div>))}</div>)}
      {report && <ReportModal subjectId={report} onClose={() => setReport(null)} />}
    </Modal>
  );
}

function AnswerModal({ item, onClose }: { item: Row; onClose: () => void }) {
  const qc = useQueryClient();
  const [a, setA] = useState<Row>(item.my_answers || {});
  const ro = !item.can_answer;
  const done = () => { qc.invalidateQueries({ queryKey: ["perf", "360"] }); onClose(); };
  const submit = useMutation({ mutationFn: () => fbApi.submit(item.id, a), onSuccess: () => { toast.success("Thank you. Your feedback is sent."); done(); }, onError: (e) => toast.error(err(e)) });
  const decline = useMutation({ mutationFn: () => fbApi.decline(item.id), onSuccess: () => { toast.success("Declined."); done(); }, onError: (e) => toast.error(err(e)) });
  return (
    <Modal title={`Feedback about ${item.subject_name}`} subtitle={item.anonymous ? `Your answers are combined with at least ${item.threshold - 1} others and never shown with your name.` : item.relationship === "self" ? "Your own view of your work." : "As the manager, your answers are shown as the manager's."} onClose={onClose}
      footer={<>{!ro && item.relationship !== "self" && <button className="btn-secondary" onClick={() => { if (window.confirm("Decline to give this feedback?")) decline.mutate(); }}>Decline</button>}
        <button className="btn-secondary" onClick={onClose}>Close</button>{!ro && <button className="btn-primary disabled:opacity-50" disabled={submit.isPending} onClick={() => submit.mutate()}>Send feedback</button>}</>}>
      <div className="space-y-3">{(item.questions || []).map((q: Row) => (
        <div key={q.key} className="border-b border-surface-border pb-2">
          <div className="text-xs text-gray-400">{q.competency}</div><div className="text-sm text-gray-800 mb-1">{q.text}</div>
          {q.kind === "rating" ? (
            <div className="flex flex-wrap gap-1">{[1, 2, 3, 4, 5].map((v) => (
              <button key={v} disabled={ro} onClick={() => setA({ ...a, [q.key]: v })} className={`px-3 py-1 rounded-lg text-xs border ${a[q.key] === v ? "bg-primary-500 text-white border-primary-500" : "border-surface-border text-gray-600"}`}>{v}{v === 1 ? " Rarely" : v === 5 ? " Consistently" : ""}</button>))}</div>
          ) : <Textarea rows={3} disabled={ro} value={a[q.key] || ""} onChange={(e) => setA({ ...a, [q.key]: e.target.value })} placeholder="Specific examples help most. Optional." />}
        </div>))}</div>
    </Modal>
  );
}

export function ReportModal({ subjectId, onClose }: { subjectId: string; onClose: () => void }) {
  const q = useQuery({ queryKey: ["perf", "360", "report", subjectId], queryFn: () => fbApi.report(subjectId) });
  const r = q.data;
  return (
    <Modal size="xl" title={r ? `360 report: ${r.subject.employee_name}` : "360 report"} subtitle={r ? `${r.round.name} · groups with fewer than ${r.threshold} answers are not shown` : ""} onClose={onClose}
      footer={<button className="btn-primary" onClick={onClose}>Close</button>}>
      {q.isLoading ? <div className="text-sm text-gray-400">Loading…</div> : q.isError || !r ? <Note tone="warn">{err(q.error, "The report could not be loaded.")}</Note> : (
        <div className="space-y-4">
          <div className="text-xs text-gray-500">{r.total_responses} answers received of {r.asked} asked (excluding self).{r.hidden_note ? ` ${r.hidden_note}` : ""}</div>
          <div className="overflow-x-auto"><table className="w-full text-xs">
            <thead className="bg-surface text-gray-400 text-left text-[10px] uppercase tracking-wide"><tr><th className="px-2.5 py-2">Competency</th>
              {r.groups.map((g: Row) => <th key={g.group} className="px-2.5 text-right">{g.label} ({g.responses})</th>)}<th className="px-2.5 text-right">Everyone except self</th></tr></thead>
            <tbody>{r.competency.map((c: Row) => (
              <tr key={c.competency} className="border-t border-surface-border"><td className="px-2.5 py-1.5 font-medium">{c.competency}</td>
                {r.groups.map((g: Row) => <td key={g.group} className="px-2.5 text-right tabular-nums">{c[g.group] ?? "—"}</td>)}
                <td className="px-2.5 text-right tabular-nums font-semibold">{c.overall ?? "—"}</td></tr>))}</tbody></table></div>
          {r.overall == null && <Note tone="warn">The overall figure needs at least {r.threshold} anonymous answers, so it is not shown.</Note>}
          {r.comments.map((c: Row) => (
            <div key={c.key}><div className="text-sm font-semibold text-gray-800 mb-1">{c.question}</div>
              {!c.answers.length ? <div className="text-xs text-gray-400">No written answers that can be shown.</div> : <ul className="list-disc pl-5 text-xs space-y-0.5">{c.answers.map((x: Row, i: number) => <li key={i}><span className="text-gray-400">{x.group}:</span> {x.text}</li>)}</ul>}
            </div>))}
          <div className="text-[11px] text-gray-400">Ratings run 1 (rarely) to 5 (consistently). Written answers are sorted, not in the order people sent them.</div>
        </div>)}
    </Modal>
  );
}
