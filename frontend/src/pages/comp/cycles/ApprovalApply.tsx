import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Play, XCircle } from "lucide-react";
import toast from "react-hot-toast";
import { money } from "../../../api/endpoints";
import { lettersApi, openPdf } from "../../../api/lifecycle";
import { err, fmtDateTime } from "../../hiring/api";
import { Col, Grid, Note, Pill, ReasonDialog, Stat } from "../../hiring/ui";
import { ccApi, pct, Row } from "./api";

/* Approval tab: the chain (request type comp_cycle via the approvals inbox), who is up, decide here. */
export function Approval({ cycleId }: { cycleId: string }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["comp", "approval", cycleId], queryFn: () => ccApi.approval(cycleId) });
  const [reject, setReject] = useState(false);
  const decide = useMutation({
    mutationFn: (v: { d: "approve" | "reject"; c: string }) => ccApi.decide(cycleId, v.d, v.c),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["comp"] }); setReject(false); toast.success(r.approval?.state === "approved" ? "Approved. The cycle can now be applied." : r.approval?.state === "rejected" ? "Rejected and sent back to calibration." : "Approved. Sent on to the next step."); },
    onError: (e) => toast.error(err(e), { duration: 7000 }),
  });
  if (q.isError) return <Note tone="warn">{err(q.error, "Approval could not be loaded.")}</Note>;
  if (!q.data) return <div className="text-sm text-gray-400">Loading…</div>;
  const { cycle, approval: a, current_step, can_decide, events } = q.data;
  const meta = a?.metadata || {};
  if (!a?.chain) return <Note>Not submitted yet. Finish calibration, then use "Submit for approval" at the top of the cycle.</Note>;
  return (
    <div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
        <Stat label="State" value={<Pill s={a.state} />} sub={`submitted by ${a.requester || "—"}`} />
        <Stat label="Total (increases + lump sums)" value={money(meta.total_amount || 0)} accent />
        <Stat label="People" value={meta.people ?? "—"} sub={(meta.department || []).join(", ")} />
        <Stat label="Request type" value={<span className="text-base font-mono">{a.request_type}</span>} sub="routes through the approvals inbox" />
      </div>
      <div className="card mb-3">
        <h3 className="font-display font-semibold text-gray-800 mb-2">Approval chain</h3>
        <ol className="space-y-1.5">
          {a.chain.map((s: Row, i: number) => {
            const dec = (a.decisions || []).filter((d: Row) => d.step === i).slice(-1)[0];
            const state = dec ? dec.decision : i === a.step && a.state === "pending" ? "waiting" : i < a.step ? "approved" : "later";
            return <li key={i} className="flex flex-wrap items-center gap-2 text-sm">
              <span className="w-6 h-6 rounded-full bg-gray-100 text-gray-600 text-xs flex items-center justify-center">{i + 1}</span>
              <span className="font-medium text-gray-800">{s.label || (s.roles || []).join(", ")}</span>
              <span className="text-xs text-gray-400">{(s.roles || []).join(", ")}{(s.user_ids || []).length ? ` · ${s.user_ids.length} named` : ""}</span>
              <Pill s={state} />
              {dec && <span className="text-xs text-gray-500">{dec.by} · {fmtDateTime(dec.at)}{dec.comment ? ` · "${dec.comment}"` : ""}</span>}
            </li>;
          })}
        </ol>
        <div className="text-[11px] text-gray-400 mt-2">Until the no-code chain registry is configured for request type "comp_cycle", the fallback chain is HR head, then org admin. Nobody approves their own submission.</div>
      </div>
      {a.state === "pending" && current_step && <div className="card mb-3 flex flex-wrap items-center gap-2">
        <span className="text-sm text-gray-700">Waiting for: <b>{current_step.label}</b></span>
        {can_decide ? <>
          <button className="btn-primary !py-1.5 text-xs inline-flex items-center gap-1" disabled={decide.isPending} onClick={() => decide.mutate({ d: "approve", c: "" })}><CheckCircle2 className="w-3.5 h-3.5" />Approve</button>
          <button className="btn-secondary !py-1.5 text-xs inline-flex items-center gap-1 text-red-600" onClick={() => setReject(true)}><XCircle className="w-3.5 h-3.5" />Reject…</button>
        </> : <span className="text-xs text-gray-400">You cannot decide this step. It is also in the Approvals inbox of the people who can.</span>}
      </div>}
      {a.state === "approved" && <Note>Approved on {fmtDateTime(cycle.approved_at)}. Go to the Apply tab to write the increases, promotions and letters.</Note>}
      {a.state === "rejected" && <Note tone="warn">Rejected. The cycle is back in calibration; fix the lines and submit again.</Note>}
      <Grid rows={events} cols={[{ k: "at", label: "When", render: (e) => fmtDateTime(e.at) }, { k: "action", label: "Action", render: (e) => <Pill s={e.action} /> }, { k: "by_user", label: "By" }, { k: "by_role", label: "Role" },
        { k: "detail", label: "Detail", render: (e) => e.detail?.comment || (e.detail?.chain ? `chain: ${e.detail.chain.join(" → ")}` : e.detail?.state || ""), csv: (e) => JSON.stringify(e.detail) }] as Col<Row>[]}
        keyOf={(e) => e.id} title="Approval history" />
      {reject && <ReasonDialog title="Reject this cycle" hint="The reason goes to HR with the cycle." confirm="Reject" onClose={() => setReject(false)} onConfirm={(c) => decide.mutate({ d: "reject", c })} />}
    </div>
  );
}

/* Apply tab: one button, live progress, per-person result (revision batch, career change, letters). */
export function Apply({ cycleId }: { cycleId: string }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["comp", "progress", cycleId], queryFn: () => ccApi.progress(cycleId),
    refetchInterval: (query) => (query.state.data?.progress?.state === "running" || query.state.data?.progress?.state === "queued" ? 1500 : false) });
  const start = useMutation({ mutationFn: () => ccApi.apply(cycleId), onSuccess: () => { qc.invalidateQueries({ queryKey: ["comp"] }); toast.success("Apply started. Progress updates below."); }, onError: (e) => toast.error(err(e), { duration: 7000 }) });
  if (q.isError) return <Note tone="warn">{err(q.error, "Progress could not be loaded.")}</Note>;
  if (!q.data) return <div className="text-sm text-gray-400">Loading…</div>;
  const { cycle, progress: p, lines } = q.data;
  const canApply = cycle.status === "approval" && cycle.approval_state === "approved" && cycle.is_hr_head;
  const running = p.state === "running" || p.state === "queued";
  const share = p.total ? Math.round(((p.done || 0) / p.total) * 100) : 0;
  const cols: Col<Row>[] = [
    { k: "employee_number", label: "Emp no" }, { k: "employee_name", label: "Name" }, { k: "department", label: "Dept" },
    { k: "proposed_pct", label: "Increase", align: "right", render: (l) => l.proposed_amount > 0 ? `${pct(l.proposed_pct)} · ${money(l.proposed_amount)}` : "—" },
    { k: "promotion", label: "Promotion", render: (l) => l.promotion ? `${l.promo_designation || ""} ${l.promo_grade ? `(${l.promo_grade})` : ""}` : "", csv: (l) => l.promotion ? "yes" : "" },
    { k: "rev", label: "Salary revision", render: (l) => l.apply_result?.revision_batch_id ? <span className="text-green-700">batch {String(l.apply_result.revision_batch_id).slice(0, 8)}</span> : l.proposed_amount > 0 ? <span className="text-gray-400">pending</span> : "", csv: (l) => l.apply_result?.revision_batch_id || "" },
    { k: "cc", label: "Career change", render: (l) => l.apply_result?.career_change_id ? <span className="text-green-700">recorded</span> : l.apply_result?.promotion_error ? <span className="text-red-600">{l.apply_result.promotion_error}</span> : l.promotion ? <span className="text-gray-400">pending</span> : "", csv: (l) => l.apply_result?.career_change_id || l.apply_result?.promotion_error || "" },
    { k: "letter", label: "Letters", render: (l) => <span>{l.apply_result?.letter_number && <button className="text-primary-600 hover:underline mr-2" onClick={async (e) => { e.stopPropagation(); openPdf(await lettersApi.pdf(l.apply_result.letter_id), `${l.apply_result.letter_number}.pdf`); }}>{l.apply_result.letter_number}</button>}
      {l.apply_result?.promotion_letter_number && <button className="text-primary-600 hover:underline mr-2" onClick={async (e) => { e.stopPropagation(); openPdf(await lettersApi.pdf(l.apply_result.promotion_letter_id), `${l.apply_result.promotion_letter_number}.pdf`); }}>{l.apply_result.promotion_letter_number}</button>}
      {(l.apply_result?.letter_error || l.apply_result?.promotion_letter_error) && <span className="text-red-600">{l.apply_result.letter_error || l.apply_result.promotion_letter_error}</span>}</span>,
      csv: (l) => [l.apply_result?.letter_number, l.apply_result?.promotion_letter_number].filter(Boolean).join(" ") },
  ];
  return (
    <div>
      <Note>Apply never writes a salary here. Increases become a salary-revision batch in Payroll (submitted and approved with your identity, applied on their date, with a retro-pay proposal when the date is past). Promotions become approved career-change records. Letters come from the letter engine (templates "Salary revision letter" and "Promotion letter") and appear in each person's My Letters. Lump sums are recorded on the cycle for payroll to pay as a one-off.</Note>
      <div className="flex flex-wrap items-center gap-3 mb-3">
        {canApply && !running && <button className="btn-primary inline-flex items-center gap-1.5" disabled={start.isPending} onClick={() => start.mutate()}><Play className="w-4 h-4" />{p.state === "failed" ? "Retry apply" : "Apply the cycle"}</button>}
        {cycle.status === "approval" && cycle.approval_state !== "approved" && <span className="text-sm text-gray-500">The cycle must be approved first.</span>}
        {cycle.status !== "approval" && cycle.status !== "applying" && cycle.status !== "applied" && cycle.status !== "closed" && <span className="text-sm text-gray-500">Submit the cycle for approval first.</span>}
        {p.state && <Pill s={p.state} />}
        {p.step && <span className="text-xs text-gray-500">step: {p.step}</span>}
      </div>
      {(running || p.state === "done" || p.state === "failed") && <div className="card mb-3">
        <div className="flex justify-between text-xs text-gray-500 mb-1"><span>{p.done || 0} of {p.total || 0} people</span><span>{share}%</span></div>
        <div className="h-2 bg-gray-100 rounded"><div className={`h-2 rounded ${p.state === "failed" ? "bg-red-500" : "bg-primary-500"}`} style={{ width: `${share}%` }} /></div>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mt-3">
          <Stat label="Revision batch" value={<span className="text-base">{p.batch_id ? String(p.batch_id).slice(0, 8) : "—"}</span>} sub={p.revisions_created != null ? `${p.revisions_created} drafted` : undefined} />
          <Stat label="Approved in payroll" value={p.revisions_approved ?? "—"} sub={p.revisions_waiting_second_approval ? `${p.revisions_waiting_second_approval} need a second approver` : undefined} />
          <Stat label="Applied now" value={p.revisions_applied_now ?? "—"} sub="future dates apply on the day" />
          <Stat label="Started" value={<span className="text-base">{fmtDateTime(p.started_at)}</span>} />
          <Stat label="Finished" value={<span className="text-base">{p.finished_at ? fmtDateTime(p.finished_at) : "—"}</span>} />
        </div>
        {!!(p.errors || []).length && <div className="mt-3 text-xs text-red-700 space-y-0.5">{p.errors.map((e: string, i: number) => <div key={i}>{e}</div>)}</div>}
        {!!(p.revisions_skipped || []).length && <div className="mt-2 text-xs text-amber-700">Payroll skipped {p.revisions_skipped.length} row(s): {p.revisions_skipped.map((s: Row) => `${s.employee_number || s.employee_id}: ${(s.errors || []).join(" ")}`).join(" | ")}</div>}
      </div>}
      <Grid rows={lines} cols={cols} keyOf={(l) => l.id} title="What gets written, per person" exportName="comp-apply.csv" empty="Nobody has an increase or a promotion in this cycle." />
    </div>
  );
}
