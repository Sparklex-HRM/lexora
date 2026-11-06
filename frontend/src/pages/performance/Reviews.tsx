import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { err, fmtDate, fmtDateTime } from "../hiring/api";
import { Col, Field, FirstStep, Grid, Modal, Note, Pill, Select, Textarea } from "../hiring/ui";
import { FormGrid, FormSection } from "../../components/Modal";
import { collect, FieldError, firstError, Req, v } from "../../lib/validators";
import { perfApi, Row, STAGE_LABEL } from "./api";

const SCOPES: Record<string, string> = { mine: "My review", team: "My team's reviews" };

export default function Reviews({ info }: { info: Row }) {
  const [scope, setScope] = useState("mine");
  const [openId, setOpenId] = useState<string | null>(null);
  const { data, isLoading } = useQuery({ queryKey: ["perf", "reviews", scope], queryFn: () => perfApi.reviews(scope) });
  const cols: Col<Row>[] = [
    { k: "cycle_name", label: "Appraisal cycle" },
    { k: "employee_name", label: "Employee", hide: scope === "mine" },
    { k: "department", label: "Department", hide: scope === "mine" },
    { k: "stage", label: "Stage", render: (r) => <Pill s={r.stage} label={r.stage_label} />, csv: (r) => r.stage_label },
    { k: "my_turn", label: "Waiting for", render: (r) => (r.my_turn ? <Pill s="pending" label="You" /> : r.stage === "published" ? "" : r.stage === "self" ? "The employee" : r.stage === "manager" ? (r.manager_name || "Manager") : r.stage === "reviewer" ? (r.reviewer_name || "Reviewer") : "HR"), csv: (r) => (r.my_turn ? "You" : r.stage) },
    { k: "deadline", label: "Due", render: (r) => fmtDate((r.deadlines || {})[r.stage]), csv: (r) => (r.deadlines || {})[r.stage] || "" },
    { k: "final_band", label: "Final rating", render: (r) => (r.final_band ? `${r.final_band} (${r.final_score})` : ""), csv: (r) => r.final_band || "" },
    { k: "ack_status", label: "Employee's answer", render: (r) => (r.ack_status ? <Pill s={r.ack_status} /> : r.stage === "published" ? "Not yet" : ""), csv: (r) => r.ack_status || "" },
  ];
  return (
    <div>
      <div className="flex gap-2 mb-3">
        <div className="inline-flex rounded-lg border border-surface-border overflow-hidden">
          {Object.keys(SCOPES).filter((s) => s === "mine" || info.has_team).map((s) => <button key={s} className={`px-3 py-1.5 text-xs ${scope === s ? "bg-primary-50 text-primary-600 font-medium" : "text-gray-500"}`} onClick={() => setScope(s)}>{SCOPES[s]}</button>)}</div>
      </div>
      {scope === "mine" && !info.linked ? <Note tone="warn">Your login is not linked to an employee record, so you have no review. Ask HR to link it.</Note> : null}
      {!isLoading && !(data || []).length ? (
        <FirstStep title={scope === "mine" ? "You have no review yet" : "No reviews for your team yet"}>
          Reviews appear here when HR launches an appraisal cycle. {scope === "mine" ? "You then fill in your self review first." : "Each one comes to you after the employee submits the self review."}
        </FirstStep>
      ) : <Grid rows={data || []} cols={cols} keyOf={(r) => r.id} loading={isLoading} onRow={(r) => setOpenId(r.id)} exportName="reviews.csv" title={SCOPES[scope]} />}
      {openId && <ReviewModal id={openId} onClose={() => setOpenId(null)} />}
    </div>
  );
}

function RatingSelect({ scale, value, onChange, disabled }: { scale: Row[]; value: any; onChange: (v: string) => void; disabled?: boolean }) {
  return (
    <Select disabled={disabled} value={value ?? ""} onChange={(e) => onChange(e.target.value)} className="!w-56">
      <option value="">Pick a rating</option>{scale.map((s) => <option key={s.value} value={s.value}>{s.value} · {s.label}</option>)}
    </Select>
  );
}

/** One section (self or manager): goal ratings + competency ratings + comments. */
function RatingsBlock({ title, review, part, edit, form, setForm }: { title: string; review: Row; part: Row | null; edit: boolean; form: Row; setForm: (f: Row) => void }) {
  const scale: Row[] = review.template?.scale || [];
  const src = edit ? form : (part || {});
  const setIn = (group: string, key: string, val: string) => setForm({ ...form, [group]: { ...(form[group] || {}), [key]: val === "" ? null : Number(val) } });
  const label = (v: any) => { const s = scale.find((x) => Number(x.value) === Number(v)); return s ? `${s.value} · ${s.label}` : ""; };
  if (!edit && !part) return null;
  return (
    <div className="card !p-3">
      <div className="font-display font-semibold text-gray-800 mb-2">{title}</div>
      <div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold mb-1">Goal achievement</div>
      {!(review.goals || []).length ? <div className="text-xs text-gray-400 mb-3">There were no approved goals for this period, so this section is left out of the score.</div> : (
        <table className="w-full text-xs mb-3"><tbody>
          {(review.goals as Row[]).map((g) => (
            <tr key={g.id} className="border-t border-surface-border align-top">
              <td className="py-1.5 pr-2"><div className="font-medium text-gray-800">{g.title}</div><div className="text-gray-400">{[g.measure, g.target && `target ${g.target}`, `progress ${g.progress_pct}%`].filter(Boolean).join(" · ")}</div></td>
              <td className="py-1.5 px-2 text-right whitespace-nowrap text-gray-500">Weight {g.weight}%</td>
              <td className="py-1.5 pl-2 text-right">{edit ? <RatingSelect scale={scale} value={(src.goal_ratings || {})[g.id]} onChange={(v) => setIn("goal_ratings", g.id, v)} /> : label((src.goal_ratings || {})[g.id])}</td>
            </tr>))}
        </tbody></table>)}
      <div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold mb-1">Competencies</div>
      <table className="w-full text-xs mb-3"><tbody>
        {((review.template?.competencies || []) as string[]).map((c) => (
          <tr key={c} className="border-t border-surface-border"><td className="py-1.5 pr-2 text-gray-800">{c}</td>
            <td className="py-1.5 pl-2 text-right">{edit ? <RatingSelect scale={scale} value={(src.competency_ratings || {})[c]} onChange={(v) => setIn("competency_ratings", c, v)} /> : label((src.competency_ratings || {})[c])}</td></tr>))}
      </tbody></table>
      {edit ? <Field label={review.stage === "manager" ? "Overall comments * (the employee sees these when the review is published)" : "Overall comments"}><Textarea rows={4} value={form.comments || ""} onChange={(e) => setForm({ ...form, comments: e.target.value })} /></Field>
        : src.comments ? <div><div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold mb-1">Overall comments</div><p className="text-sm text-gray-700 whitespace-pre-wrap">{src.comments}</p></div> : null}
    </div>
  );
}

/** The arithmetic, line by line, exactly as the server computed it. */
export function ScoreMath({ scores, template }: { scores?: Row | null; template?: Row }) {
  if (!scores) return null;
  const g = scores.goal || {}, c = scores.competency || {}, o = scores.overall || {};
  return (
    <div className="card !p-3 text-xs">
      <div className="font-display font-semibold text-gray-800 mb-2 text-sm">How the score is worked out</div>
      <div className="mb-2"><b>Goal score</b> = sum of (weight x rating) / sum of weights
        {(g.lines || []).map((l: Row, i: number) => <div key={i} className="text-gray-500 pl-3">{l.title}: {l.weight} x {l.rating} = {l.points}</div>)}
        <div className="pl-3 text-gray-700">{g.text}</div></div>
      <div className="mb-2"><b>Competency score</b> = average of the competency ratings
        <div className="pl-3 text-gray-500">{(c.lines || []).map((l: Row) => `${l.name} ${l.rating}`).join(" + ")}</div><div className="pl-3 text-gray-700">{c.text}</div></div>
      <div><b>Overall</b> = (goal score x {template?.goal_weight}% + competency score x {template?.competency_weight}%) / total weight
        <div className="pl-3 text-gray-700">{o.text}</div></div>
      <div className="mt-2 text-sm">Computed score <b>{o.score ?? "none"}</b>{scores.band ? <> · nearest rating <b>{scores.band}</b></> : null}</div>
    </div>
  );
}

function ReviewModal({ id, onClose }: { id: string; onClose: () => void }) {
  const qc = useQueryClient();
  const { data: r, isLoading } = useQuery({ queryKey: ["perf", "review", id], queryFn: () => perfApi.review(id) });
  const [form, setForm] = useState<Row>({});
  const [loaded, setLoaded] = useState(false);
  const [ack, setAck] = useState<{ status: string; comment: string } | null>(null);
  const field = r ? ({ self: "self_review", manager: "manager_review", reviewer: "reviewer_review" } as Row)[r.stage] : null;
  useEffect(() => { if (r && !loaded) { setForm(r.my_turn && field ? { ...(r[field] || {}) } : {}); setLoaded(true); } }, [r, loaded, field]);
  const refresh = () => qc.invalidateQueries({ queryKey: ["perf"] });
  const draft = useMutation({ mutationFn: () => perfApi.saveDraft(id, form), onSuccess: () => { toast.success("Draft saved. It is not submitted yet."); refresh(); }, onError: (e) => toast.error(err(e)) });
  const submit = useMutation({
    mutationFn: () => perfApi.submit(id, form),
    onSuccess: (x) => { toast.success(`Submitted. The review is now at ${STAGE_LABEL[x.stage]}. Your part is locked.`); refresh(); onClose(); },
    onError: (e) => toast.error(err(e), { duration: 7000 }),
  });
  const scaleVals = ((r?.template?.scale || []) as Row[]).map((s) => Number(s.value));
  const lo = scaleVals.length ? Math.min(...scaleVals) : 0, hi = scaleVals.length ? Math.max(...scaleVals) : undefined;
  const stageErrs = (): Record<string, string> => {
    if (!r) return {};
    if (r.stage === "reviewer") return collect({
      comments: v.required(String(form.comments || "").trim(), "Comments") || v.maxLen(form.comments, 4000, "Comments"),
      suggested_score: (String(form.suggested_score ?? "") !== "" && Number.isNaN(Number(form.suggested_score)) ? "The suggested score must be a number" : null) || v.range(form.suggested_score, lo, hi, "Suggested score"),
    });
    if (r.stage !== "manager") return collect({ comments: v.maxLen(form.comments, 4000, "Comments") });
    return collect({
      comments: v.required(String(form.comments || "").trim(), "Overall comments") || v.maxLen(form.comments, 4000, "Overall comments"),
      promotion_note: form.promotion_recommended ? (v.required(String(form.promotion_note || "").trim(), "Why you recommend a promotion") || (String(form.promotion_note).trim().length < 10 ? "Say in at least 10 characters why you recommend a promotion" : null) || v.maxLen(form.promotion_note, 400, "Promotion note")) : null,
      pip_note: form.pip_recommended ? (v.required(String(form.pip_note || "").trim(), "Why you suggest an improvement plan") || (String(form.pip_note).trim().length < 10 ? "Say in at least 10 characters why you suggest an improvement plan" : null) || v.maxLen(form.pip_note, 400, "Improvement plan note")) : null,
      recommended_hike_pct: (String(form.recommended_hike_pct ?? "") !== "" && Number.isNaN(Number(form.recommended_hike_pct)) ? "The suggested increase must be a number" : null) || v.percent(form.recommended_hike_pct, "Suggested increase")
        || (String(form.recommended_hike_pct ?? "").includes(".") && String(form.recommended_hike_pct).split(".")[1].length > 2 ? "Suggested increase can have at most 2 decimals" : null),
      strengths_note: v.maxLen(form.strengths_note, 2000, "Strengths"),
      improvement_note: v.maxLen(form.improvement_note, 2000, "Areas to improve"),
    });
  };
  const errs = stageErrs();
  const onSubmit = () => { if (Object.keys(errs).length) { toast.error(firstError(errs)); return; } if (window.confirm("Submit now? After you submit, only HR can reopen it.")) submit.mutate(); };
  const doAck = useMutation({
    mutationFn: () => perfApi.ack(id, ack!.status, ack!.comment),
    onSuccess: () => { toast.success("Your answer is recorded."); refresh(); setAck(null); },
    onError: (e) => toast.error(err(e)),
  });
  if (isLoading || !r) return <Modal title="Loading" onClose={onClose}><div className="text-sm text-gray-400">Loading…</div></Modal>;
  const mine = r.my_turn, viewer = r.viewer;
  const published = r.stage === "published";
  return (
    <Modal size="screen" title={viewer === "self" ? `My review: ${r.cycle_name}` : `${r.employee_name}: ${r.cycle_name}`}
      subtitle={`${[r.designation, r.department, r.grade && "Grade " + r.grade].filter(Boolean).join(" · ")} · Stage: ${r.stage_label}${(r.deadlines || {})[r.stage] ? " · due " + fmtDate(r.deadlines[r.stage]) : ""}`} onClose={onClose}
      footer={mine ? <><button className="btn-secondary" onClick={onClose}>Close</button>
        <button className="btn-secondary disabled:opacity-50" disabled={draft.isPending} onClick={() => draft.mutate()}>Save draft</button>
        <button className="btn-primary disabled:opacity-50" disabled={submit.isPending} onClick={onSubmit}>Submit {STAGE_LABEL[r.stage].toLowerCase()}</button></>
        : <button className="btn-primary" onClick={onClose}>Close</button>}>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <div className="space-y-4">
          {!mine && !published && <Note>{viewer === "self" ? (r.stage === "self" ? "Your self review is open." : "You have submitted your self review. The rating appears here when HR publishes it.") : `This review is at ${r.stage_label}. Nothing is waiting for you.`}</Note>}
          {published && (
            <div className="card !p-4 border-primary-200">
              <div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">Final rating</div>
              <div className="font-display text-2xl font-bold text-primary-600">{r.final_band || "Not set"}</div>
              <div className="text-sm text-gray-500">Score {r.final_score} · published {fmtDate(r.published_at)}</div>
              {r.ack_status ? <div className="mt-2 text-sm"><Pill s={r.ack_status} /> on {fmtDateTime(r.ack_at)}{r.ack_comment ? <div className="text-gray-700 mt-1 whitespace-pre-wrap">{r.ack_comment}</div> : null}</div>
                : r.can_ack ? <div className="flex gap-2 mt-3"><button className="btn-primary" onClick={() => setAck({ status: "acknowledged", comment: "" })}>Acknowledge</button><button className="btn-secondary" onClick={() => setAck({ status: "disagreed", comment: "" })}>Disagree</button></div>
                  : <div className="mt-2 text-xs text-gray-400">The employee has not answered yet.</div>}
            </div>)}
          <RatingsBlock title={viewer === "self" ? "My self review" : "Self review"} review={r} part={r.stage === "self" && mine ? null : r.self_review} edit={mine && r.stage === "self"} form={form} setForm={setForm} />
          {viewer !== "self" && r.stage === "self" && <Note>The employee has not submitted the self review yet. It stays private until then.</Note>}
        </div>
        <div className="space-y-4">
          <RatingsBlock title={viewer === "self" ? "Manager's review" : viewer === "manager" ? "My review as manager" : "Manager review"} review={r} part={r.stage === "manager" && mine ? null : r.manager_review} edit={mine && r.stage === "manager"} form={form} setForm={setForm} />
          {mine && r.stage === "manager" && (
            <div className="card !p-3">
              <FormSection title="Summary for the employee" hint="The employee sees these when the review is published.">
                <FormGrid>
                  <div><Field label="Strengths and achievements" hint="What went well this period, with examples."><Textarea rows={3} maxLength={2000} value={form.strengths_note || ""} onChange={(e) => setForm({ ...form, strengths_note: e.target.value })} /></Field><FieldError msg={errs.strengths_note} /></div>
                  <div><Field label="Areas to improve and development plan" hint="What to work on next cycle and the support offered."><Textarea rows={3} maxLength={2000} value={form.improvement_note || ""} onChange={(e) => setForm({ ...form, improvement_note: e.target.value })} /></Field><FieldError msg={errs.improvement_note} /></div>
                </FormGrid>
              </FormSection>
              <FormSection title="Recommendations to HR" hint="HR and the skip-level reviewer see these. The employee never does." className="!mb-0">
                <FormGrid>
                  <div><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!!form.promotion_recommended} onChange={(e) => setForm({ ...form, promotion_recommended: e.target.checked })} />Recommend for promotion</label>
                    {form.promotion_recommended && <div className="mt-2"><Field label={<>Why<Req /></>} hint="HR sees this in the promotion list."><Textarea rows={2} maxLength={400} value={form.promotion_note || ""} onChange={(e) => setForm({ ...form, promotion_note: e.target.value })} /></Field><FieldError msg={form.promotion_note ? errs.promotion_note : null} /></div>}</div>
                  <div><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!!form.pip_recommended} onChange={(e) => setForm({ ...form, pip_recommended: e.target.checked })} />Suggest an improvement plan</label>
                    {form.pip_recommended && <div className="mt-2"><Field label={<>Why<Req /></>} hint="Facts, not labels. HR decides whether to open a plan."><Textarea rows={2} maxLength={400} value={form.pip_note || ""} onChange={(e) => setForm({ ...form, pip_note: e.target.value })} /></Field><FieldError msg={form.pip_note ? errs.pip_note : null} /></div>}</div>
                  <div><Field label="Suggested increase %" hint="Optional input for HR and finance when they build the increment proposal; not a final figure."><input className="input !py-1.5 text-sm w-full" type="number" min={0} max={100} step="0.01" value={form.recommended_hike_pct ?? ""} onChange={(e) => setForm({ ...form, recommended_hike_pct: e.target.value })} /></Field><FieldError msg={errs.recommended_hike_pct} /></div>
                </FormGrid>
              </FormSection>
            </div>)}
          {!(mine && r.stage === "manager") && r.manager_review && !r.manager_review.draft && (r.manager_review.strengths_note || r.manager_review.improvement_note) && (
            <div className="card !p-3 text-sm space-y-2">
              {r.manager_review.strengths_note && <div><div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold mb-0.5">Strengths and achievements</div><p className="text-gray-700 whitespace-pre-wrap">{r.manager_review.strengths_note}</p></div>}
              {r.manager_review.improvement_note && <div><div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold mb-0.5">Areas to improve and development plan</div><p className="text-gray-700 whitespace-pre-wrap">{r.manager_review.improvement_note}</p></div>}
            </div>)}
          {viewer !== "self" && r.promotion_recommended && !(mine && r.stage === "manager") && <Note>Recommended for promotion: {r.promotion_note}</Note>}
          {viewer !== "self" && !(mine && r.stage === "manager") && r.manager_review?.pip_recommended && !r.manager_review.draft && <Note tone="warn">The manager suggests an improvement plan: {r.manager_review.pip_note}</Note>}
          {viewer !== "self" && !(mine && r.stage === "manager") && r.manager_review?.recommended_hike_pct != null && !r.manager_review.draft && <Note>Manager's suggested increase: {r.manager_review.recommended_hike_pct}%</Note>}
          {r.stage === "reviewer" && mine ? (
            <div className="card !p-3"><div className="font-display font-semibold text-gray-800 mb-2">My review as skip-level reviewer</div>
              <FormGrid>
                <Field label="Do you agree with the manager?" hint="Pick No if your view differs; then explain in the comments."><Select value={form.agree === false ? "0" : "1"} onChange={(e) => setForm({ ...form, agree: e.target.value === "1" })}><option value="1">Yes</option><option value="0">No</option></Select></Field>
                <div><Field label="Suggested overall score" hint={`Optional${scaleVals.length ? `, ${lo} to ${hi}` : ""}. Only HR sees it at calibration; it does not change the manager's score.`}><input className="input !py-1.5 text-sm w-full" type="number" step="0.01" min={lo} max={hi} value={form.suggested_score ?? ""} onChange={(e) => setForm({ ...form, suggested_score: e.target.value })} /></Field><FieldError msg={errs.suggested_score} /></div>
                <div className="sm:col-span-2"><Field label={<>Comments<Req /></>}><Textarea rows={4} maxLength={4000} value={form.comments || ""} onChange={(e) => setForm({ ...form, comments: e.target.value })} /></Field></div>
              </FormGrid></div>
          ) : r.reviewer_review && !r.reviewer_review.draft ? (
            <div className="card !p-3"><div className="font-display font-semibold text-gray-800 mb-1">Reviewer ({r.reviewer_name})</div>
              <div className="text-xs text-gray-500 mb-1">{r.reviewer_review.agree === false ? "Does not agree with the manager." : "Agrees with the manager."}{r.reviewer_review.suggested_score != null ? ` Suggested score ${r.reviewer_review.suggested_score}.` : ""}</div>
              <p className="text-sm text-gray-700 whitespace-pre-wrap">{r.reviewer_review.comments}</p></div>) : null}
          <ScoreMath scores={r.scores} template={r.template} />
        </div>
      </div>
      {ack && (
        <Modal size="lg" title={ack.status === "acknowledged" ? "Acknowledge your rating" : "Disagree with your rating"} subtitle="This can be done once." onClose={() => setAck(null)}
          footer={<><button className="btn-secondary" onClick={() => setAck(null)}>Cancel</button><button className="btn-primary disabled:opacity-50" disabled={doAck.isPending || (ack.status === "disagreed" && !ack.comment.trim())} onClick={() => doAck.mutate()}>Confirm</button></>}>
          <Field label={ack.status === "disagreed" ? <>Why do you disagree?<Req /></> : "Comment (optional)"} hint={ack.status === "disagreed" ? "HR is told and reads this. Up to 4000 characters." : "Up to 4000 characters."}><Textarea rows={6} autoFocus maxLength={4000} value={ack.comment} onChange={(e) => setAck({ ...ack, comment: e.target.value })} /></Field>
        </Modal>)}
    </Modal>
  );
}
