import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, MessagesSquare } from "lucide-react";
import toast from "react-hot-toast";
import { PageHeader } from "../../components/ui";
import { err, fmtDateTime, hiringApi, Row } from "./api";
import { Col, downloadBlob, Field, FirstStep, Grid, Modal, Note, Pill, Select, Textarea } from "./ui";
import { FormSection } from "../../components/Modal";
import { v, collect, firstError, FieldError, Req } from "../../lib/validators";

const MODES: Record<string, string> = { in_person: "In person", video: "Video call", phone: "Phone" };
const REC: Record<string, string> = { hire: "Hire", no_hire: "No hire", hold: "Hold" };

/** Interviews assigned to me. A panel member sees only the candidates they interview. */
export default function MyInterviewsPage() {
  const { data, isLoading } = useQuery({ queryKey: ["hire", "my-interviews"], queryFn: hiringApi.myInterviews });
  const [open, setOpen] = useState<Row | null>(null);
  const items = data?.items || [];
  const cols: Col<Row>[] = [
    { k: "candidate_name", label: "Candidate", render: (r) => <span>{r.candidate_name}{r.names_hidden && <span className="ml-1 badge-purple">name hidden</span>}</span> },
    { k: "designation", label: "Applied for" },
    { k: "round_name", label: "Round", render: (r) => `Round ${r.round_no}: ${r.round_name}`, csv: (r) => `Round ${r.round_no}: ${r.round_name}` },
    { k: "scheduled_at", label: "When", render: (r) => fmtDateTime(r.scheduled_at) || "No time set" },
    { k: "mode", label: "Mode", render: (r) => MODES[r.mode] || r.mode, csv: (r) => MODES[r.mode] || r.mode },
    { k: "place", label: "Link or room", render: (r) => (r.place && /^https?:/.test(r.place) ? <a className="underline text-primary-600" href={r.place} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>Join</a> : r.place || "") },
    { k: "current_title", label: "Current role", render: (r) => [r.current_title, r.current_employer].filter(Boolean).join(" at "), csv: (r) => [r.current_title, r.current_employer].filter(Boolean).join(" at ") },
    { k: "feedback_status", label: "My scorecard", render: (r) => <Pill s={r.feedback_status} label={r.feedback_status === "submitted" ? "Submitted" : "Waiting for you"} />, csv: (r) => r.feedback_status },
  ];
  return (
    <div>
      <PageHeader title="My Interviews" icon={MessagesSquare} subtitle="Interview rounds where you are on the panel" />
      {!isLoading && data && !data.linked ? <FirstStep title="Your login is not linked to an employee record">Ask HR to link your login to your employee record. Interviews are assigned to employees.</FirstStep>
        : !isLoading && !items.length ? <FirstStep title="No interviews assigned to you">When HR puts you on an interview panel, the round appears here with the candidate's resume and the scorecard.</FirstStep>
          : <Grid rows={items} cols={cols} keyOf={(r) => r.id} loading={isLoading} onRow={setOpen} exportName="my-interviews.csv" title="Assigned to me" />}
      {open && <ScorecardModal iv={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

/** Structured feedback: one rating per competency (from the job's scorecard), overall, hire / no-hire. */
function ScorecardModal({ iv, onClose }: { iv: Row; onClose: () => void }) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["hire", "iv-scorecard", iv.id], queryFn: () => hiringApi.interviewScorecard(iv.id) });
  const [f, setF] = useState<Row>({ rating: "", recommendation: "", scores: {} });
  const done: Row | null = data?.my_feedback || iv.my_feedback || null;
  const comps: Row[] = data?.competencies || [];
  const myScores: Record<string, Row> = Object.fromEntries((data?.my_scores || []).map((s: Row) => [s.competency_key, s]));
  const setScore = (k: string, patch: Row) => setF({ ...f, scores: { ...f.scores, [k]: { ...(f.scores[k] || {}), ...patch } } });
  const allRated = comps.every((c) => f.scores[c.key]?.rating);
  const [shown, setShown] = useState(false);
  const errs = collect({
    scores: allRated ? null : "Rate every competency from 1 to 5",
    recommendation: f.recommendation ? null : "Pick your recommendation: Hire, No hire or Hold",
    strengths: v.maxLen(f.strengths, 2000, "Strengths"),
    concerns: v.maxLen(f.concerns, 2000, "Concerns"),
    ...Object.fromEntries(comps.map((c) => [`n_${c.key}`, v.maxLen(f.scores[c.key]?.note, 1000, `The note on ${c.label}`)])),
  });
  const er = (k: string) => (shown ? errs[k] : undefined);
  const trySubmit = () => {
    setShown(true);
    if (Object.keys(errs).length) return void toast.error(firstError(errs));
    if (!window.confirm("Submit this scorecard? It cannot be changed after you submit.")) return;
    save.mutate();
  };
  const save = useMutation({
    mutationFn: () => hiringApi.submitScorecard(iv.id, { rating: f.rating || undefined, recommendation: f.recommendation, strengths: f.strengths, concerns: f.concerns,
      scores: comps.map((c) => ({ key: c.key, rating: f.scores[c.key]?.rating, note: f.scores[c.key]?.note })) }),
    onSuccess: (r) => { toast.success(`Scorecard submitted (weighted ${r.weighted_score} of 5). It cannot be changed now.`); qc.invalidateQueries({ queryKey: ["hire"] }); onClose(); },
    onError: (e) => toast.error(err(e)),
  });
  const download = async (d: Row) => { try { downloadBlob(d.file_name, await hiringApi.documentBlob(d.id)); } catch (e) { toast.error(err(e)); } };
  const name = data?.display_name || iv.candidate_name;
  return (
    <Modal title={name} subtitle={`${iv.designation} · Round ${iv.round_no}: ${iv.round_name} · ${fmtDateTime(iv.scheduled_at) || "No time set"}`} onClose={onClose}
      footer={<><button className="btn-secondary" onClick={onClose}>Close</button>
        {!done && <span className="text-[11px] text-amber-700 mr-auto">Feedback is locked once you submit it.</span>}
        {!done && <button className="btn-primary disabled:opacity-50" disabled={save.isPending || isLoading} onClick={trySubmit}>Submit scorecard</button>}</>}>
      <div className="grid grid-cols-1 lg:grid-cols-[18rem_1fr] gap-5">
        <div className="text-sm space-y-2">
          {data?.hide_names && <Note>Bias guard is on for this job: the candidate's name and employer are hidden until the scorecard is in. The resume below may still carry them.</Note>}
          <div><div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">Current role</div>{[iv.current_title, iv.current_employer].filter(Boolean).join(" at ") || "Not given"}</div>
          <div><div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">Mode</div>{MODES[iv.mode]}{iv.place ? <> · {/^https?:/.test(iv.place) ? <a className="underline text-primary-600" href={iv.place} target="_blank" rel="noreferrer">join link</a> : iv.place}</> : ""}</div>
          <div><div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">Panel</div>{(iv.panel || []).map((p: Row) => p.name).join(", ")}</div>
          <div><div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold mb-1">Resume and documents</div>
            {!(iv.documents || []).length ? <span className="text-gray-400 text-xs">None on file.</span>
              : (iv.documents as Row[]).map((d) => <button key={d.id} className="btn-secondary !py-1 !px-2 text-xs inline-flex items-center gap-1 mr-1 mb-1" onClick={() => download(d)}><Download className="w-3 h-3" />{d.file_name}</button>)}</div>
          <div className="text-[11px] text-gray-400">Scorecard: {data?.name}{data?.source === "built_in" ? " (built-in)" : ""}</div>
        </div>
        <div className="space-y-3">
          {isLoading ? <div className="text-sm text-gray-400">Loading…</div> : (
            <div className="border border-surface-border rounded-lg overflow-hidden">
              <table className="w-full text-xs">
                <thead className="bg-surface text-gray-400 text-[10px] uppercase"><tr><th className="px-2.5 py-1.5 text-left">Competency</th><th className="px-2.5 py-1.5 text-left w-28">Rating 1-5 *</th><th className="px-2.5 py-1.5 text-left">Note</th></tr></thead>
                <tbody>{comps.map((c) => <tr key={c.key} className="border-t border-surface-border align-top">
                  <td className="px-2.5 py-1.5"><div className="font-medium text-gray-800">{c.label} <span className="text-gray-400">x{c.weight}</span></div><div className="text-gray-400">{c.description}</div></td>
                  <td className="px-2.5 py-1.5">{done ? <b>{myScores[c.key]?.rating ?? ""}</b> : <Select value={f.scores[c.key]?.rating || ""} onChange={(e) => setScore(c.key, { rating: e.target.value })}><option value="">Pick</option>{[5, 4, 3, 2, 1].map((n) => <option key={n} value={n}>{n}</option>)}</Select>}</td>
                  <td className="px-2.5 py-1.5">{done ? (myScores[c.key]?.note || "") : <><input className="input !py-1 text-xs w-full" maxLength={1000} placeholder="What you saw (optional)" value={f.scores[c.key]?.note || ""} onChange={(e) => setScore(c.key, { note: e.target.value })} /><FieldError msg={er(`n_${c.key}`)} /></>}</td>
                </tr>)}</tbody>
              </table>
              {!done && <div className="px-2.5 py-1 text-[11px] text-gray-400 border-t border-surface-border">1 = poor, 5 = excellent. Each rating is weighted by the x factor next to the competency.</div>}
            </div>)}
          {!done && <FieldError msg={er("scores")} />}
          <FormSection title="Summary and recommendation">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Field label={<>Recommendation<Req /></>} hint="Your hire / no-hire call. With the scores it drives the round outcome."><Select disabled={!!done} value={done ? done.recommendation : f.recommendation} onChange={(e) => setF({ ...f, recommendation: e.target.value })}>
              <option value="">Pick one</option>{Object.entries(REC).map(([k, lbl]) => <option key={k} value={k}>{lbl}</option>)}</Select><FieldError msg={er("recommendation")} /></Field>
            <Field label="Overall rating" hint="Leave empty to use the weighted competency score."><Select disabled={!!done} value={done ? done.rating : f.rating} onChange={(e) => setF({ ...f, rating: e.target.value })}>
              <option value="">Use the weighted score</option>{[5, 4, 3, 2, 1].map((n) => <option key={n} value={n}>{n}</option>)}</Select></Field>
            <Field label="Strengths" hint="Specific, evidence-based positives from this round. Also note what you covered, so the next round does not repeat it."><Textarea rows={4} maxLength={2000} disabled={!!done} value={done ? done.strengths || "" : f.strengths || ""} onChange={(e) => setF({ ...f, strengths: e.target.value })} /><FieldError msg={er("strengths")} /></Field>
            <Field label="Concerns" hint="Specific gaps or risks. Do not refer to age, gender, religion, caste or marital status."><Textarea rows={4} maxLength={2000} disabled={!!done} value={done ? done.concerns || "" : f.concerns || ""} onChange={(e) => setF({ ...f, concerns: e.target.value })} /><FieldError msg={er("concerns")} /></Field>
            {done && <div className="sm:col-span-2 text-xs text-gray-500">Submitted on {fmtDateTime(done.submitted_at)}. Feedback is locked once submitted.</div>}
          </div>
          </FormSection>
        </div>
      </div>
    </Modal>
  );
}
