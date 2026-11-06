import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { DoorOpen } from "lucide-react";
import { Empty, PageHeader, Spinner, StatTile } from "../../components/ui";
import { useAuthStore } from "../../store/authStore";
import { exitApi, errText, dmy, STATUS_TONE, type ExitCase } from "../../api/exit";
import { REASON_OPTIONS } from "./StartExitModal";

/** Self-service: submit a resignation, follow it, withdraw it, fill the exit interview. */

const L = "block text-xs font-semibold text-gray-500";
const STEPS = ["submitted", "manager_review", "hr_review", "accepted", "clearance", "settlement", "settled", "closed"];
const STEP_LABEL: Record<string, string> = { submitted: "Submitted", manager_review: "Manager", hr_review: "HR", accepted: "Accepted", clearance: "Clearance", settlement: "Settlement", settled: "Settled", closed: "Closed" };

export default function MyResignationPage() {
  const qc = useQueryClient();
  const me = useAuthStore((s) => s.user?.employee_id) || "";
  const list = useQuery({ queryKey: ["exit-cases", "mine"], queryFn: () => exitApi.list({ scope: "mine" }) });
  const cases = list.data?.cases || [];
  const open = cases.find((c) => !["closed", "withdrawn", "rejected"].includes(c.status));
  const preview = useQuery({ queryKey: ["exit-notice-preview", me], queryFn: () => exitApi.noticePreview(me), enabled: !!me && !open });
  const [f, setF] = useState({ reason_category: "", reason: "", requested_last_working_day: "", note: "" });
  const refresh = () => qc.invalidateQueries({ queryKey: ["exit-cases"] });
  const submit = useMutation({
    mutationFn: () => exitApi.create({ exit_type: "resignation", reason_category: f.reason_category || undefined, reason: f.reason || undefined, requested_last_working_day: f.requested_last_working_day || undefined, note: f.note || undefined }),
    onSuccess: (c) => { toast.success(`Resignation submitted (${c.case_number})`); refresh(); }, onError: (e) => toast.error(errText(e, "Could not submit")),
  });
  if (list.isLoading) return <div className="grid place-items-center py-24"><Spinner /></div>;
  return (
    <div>
      <PageHeader title="My resignation" icon={DoorOpen} subtitle="Submit your resignation and follow it to your last working day." />
      {!me && <div className="card text-sm text-gray-600">Your login is not linked to an employee record. Ask HR to link it.</div>}
      {me && !open && (
        <div className="card max-w-5xl">
          <h3 className="font-display font-semibold text-gray-800 mb-3">Submit a resignation</h3>
          <div className="grid md:grid-cols-2 gap-5">
            <div className="space-y-3">
              <label className={L}>Main reason
                <select className="input mt-1" value={f.reason_category} onChange={(e) => setF({ ...f, reason_category: e.target.value })}><option value="">Select…</option>{REASON_OPTIONS.slice(0, 8).concat("Other").map((r) => <option key={r}>{r}</option>)}</select></label>
              <label className={L}>In your words<textarea className="input mt-1 text-sm" rows={4} value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} /></label>
            </div>
            <div className="space-y-3">
              <label className={L}>Last working day you ask for (optional)<input type="date" className="input mt-1" value={f.requested_last_working_day} onChange={(e) => setF({ ...f, requested_last_working_day: e.target.value })} /></label>
              {preview.data && <div className="rounded-lg bg-surface p-3 text-xs text-gray-600">Your notice period is <b>{preview.data.days} days</b>. It starts on the day HR accepts. If accepted today, your last working day would be <b>{dmy(preview.data.expected_lwd)}</b>. HR confirms the date.</div>}
              <label className={L}>Note for HR (optional)<textarea className="input mt-1 text-sm" rows={2} value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></label>
            </div>
          </div>
          <div className="flex justify-end mt-4"><button className="btn-primary" disabled={!f.reason_category || submit.isPending} onClick={() => { if (window.confirm("Submit your resignation? Your manager and HR will be told.")) submit.mutate(); }}>{submit.isPending ? "Submitting…" : "Submit resignation"}</button></div>
        </div>)}
      {open && <OpenCase c={open} onChanged={refresh} />}
      {cases.filter((c) => c !== open).length > 0 && (
        <div className="card !p-0 mt-4 max-w-5xl">
          <div className="px-4 py-2.5 border-b border-surface-border font-display font-semibold text-gray-800 text-sm">Earlier requests</div>
          <table className="w-full text-xs"><tbody>{cases.filter((c) => c !== open).map((c) => (
            <tr key={c.id} className="border-t border-surface-border"><td className="px-3 py-1.5 font-medium">{c.case_number}</td><td className="px-3 py-1.5">{dmy(c.submitted_on)}</td><td className="px-3 py-1.5"><span className={STATUS_TONE[c.status]}>{c.status_label}</span></td><td className="px-3 py-1.5">{c.lwd ? `Last day ${dmy(c.lwd)}` : ""}</td></tr>))}</tbody></table>
        </div>)}
      {me && !open && !cases.length && <div className="mt-4 max-w-5xl"><Empty>You have no exit request.</Empty></div>}
    </div>
  );
}

function OpenCase({ c, onChanged }: { c: ExitCase; onChanged: () => void }) {
  const qc = useQueryClient();
  const det = useQuery({ queryKey: ["exit-case", c.id], queryFn: () => exitApi.detail(c.id) });
  const act = useMutation({
    mutationFn: (b: Record<string, any>) => exitApi.action(c.id, b),
    onSuccess: (r) => { toast.success(r.case.status === "withdrawn" ? "Resignation withdrawn" : "Sent to HR"); onChanged(); qc.invalidateQueries({ queryKey: ["exit-case", c.id] }); },
    onError: (e) => toast.error(errText(e)),
  });
  const d = det.data;
  const [ans, setAns] = useState<Record<string, string>>({});
  const save = useMutation({
    mutationFn: (submitIt: boolean) => exitApi.interview(c.id, (d?.interview_template || []).map((q) => ({ key: q.key, question: q.question, answer: ans[q.key] ?? "" })), submitIt),
    onSuccess: (r) => { toast.success(r.submitted ? "Exit interview submitted" : "Saved"); qc.invalidateQueries({ queryKey: ["exit-case", c.id] }); }, onError: (e) => toast.error(errText(e)),
  });
  const at = STEPS.indexOf(c.status);
  const can = (a: string) => (c.actions || []).some((x) => x.action === a);
  return (
    <div className="max-w-5xl space-y-3">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatTile label="Request" value={c.case_number} sub={`Submitted ${dmy(c.submitted_on)}`} />
        <StatTile label="Where it is" value={c.status_label} sub={`With: ${c.pending_with}`} accent />
        <StatTile label="Last working day" value={dmy(c.lwd || c.requested_lwd)} sub={c.lwd ? "confirmed by HR" : c.requested_lwd ? "you asked for this" : "HR will confirm"} />
        <StatTile label="Notice" value={c.notice_days == null ? "—" : `${c.notice_days} days`} sub={c.notice_shortfall_days ? `short by ${c.notice_shortfall_days} (${c.notice_treatment_label || "HR to decide"})` : ""} />
      </div>
      <div className="card">
        <div className="flex flex-wrap gap-1.5">
          {STEPS.map((s, i) => <span key={s} className={`px-2.5 py-1 rounded-full text-xs font-semibold border ${c.status === "on_hold" ? "bg-surface text-gray-500 border-surface-border" : i < at ? "bg-primary-50 text-primary-700 border-primary-200" : i === at ? "bg-primary-600 text-white border-primary-600" : "bg-surface-card text-gray-400 border-surface-border"}`}>{i + 1}. {STEP_LABEL[s]}</span>)}
        </div>
        {c.status === "on_hold" && <p className="text-sm text-amber-700 mt-2">HR has put this request on hold.</p>}
        {c.withdraw_requested && <p className="text-sm text-amber-700 mt-2">You asked to withdraw. HR will decide.</p>}
        <div className="flex flex-wrap gap-2 mt-3">
          {can("withdraw") && <button className="btn-secondary" disabled={act.isPending} onClick={() => { if (window.confirm("Withdraw your resignation?")) act.mutate({ action: "withdraw", reason: "Withdrawn by the employee" }); }}>Withdraw my resignation</button>}
          {can("request_withdraw") && <button className="btn-secondary" disabled={act.isPending} onClick={() => { const r = window.prompt("Your resignation is already accepted. Why do you want to withdraw it? HR must approve."); if (r && r.trim()) act.mutate({ action: "request_withdraw", reason: r.trim() }); }}>Ask HR to withdraw it</button>}
        </div>
      </div>
      {d && d.clearance.length > 0 && (
        <div className="card !p-0">
          <div className="px-4 py-2.5 border-b border-surface-border font-display font-semibold text-gray-800 text-sm">Your clearance — {d.clearance_summary.cleared} of {d.clearance_summary.total} done</div>
          <table className="w-full text-xs"><tbody>{d.clearance.map((i) => (
            <tr key={i.id} className="border-t border-surface-border"><td className="px-3 py-1.5 font-medium whitespace-nowrap">{i.department}</td><td className="px-3 py-1.5">{i.item}</td><td className="px-3 py-1.5"><span className={i.status === "pending" ? "badge-amber" : "badge-green"}>{i.status === "pending" ? "pending" : "cleared"}</span></td><td className="px-3 py-1.5 text-gray-500">{i.recovery_amount ? `Recovery: ${i.comment || ""}` : ""}</td></tr>))}</tbody></table>
        </div>)}
      {d && d.letters.some((l) => l.issued) && (
        <div className="card text-sm text-gray-700"><b>Your letters:</b> {d.letters.filter((l) => l.issued).map((l) => `${l.label}${l.issued?.number ? " (" + l.issued.number + ")" : ""}`).join(" · ")}. Ask HR for a copy if you have not received it.</div>)}
      {d && d.can_fill_interview && ["accepted", "clearance", "settlement", "settled"].includes(c.status) && (
        d.interview?.submitted ? <div className="card text-sm text-gray-600">Thank you. Your exit interview is with HR. Only HR can read it.</div> : (
          <div className="card">
            <h3 className="font-display font-semibold text-gray-800 mb-1">Exit interview</h3>
            <p className="text-xs text-gray-500 mb-3">Only HR can read your answers.</p>
            <div className="grid md:grid-cols-2 gap-3">
              {d.interview_template.map((q) => (
                <label key={q.key} className={L}>{q.question}
                  {q.type === "yes_no" ? <select className="input mt-1" value={ans[q.key] ?? ""} onChange={(e) => setAns({ ...ans, [q.key]: e.target.value })}><option value="">Select…</option><option>Yes</option><option>No</option></select>
                    : q.type === "rating" ? <select className="input mt-1" value={ans[q.key] ?? ""} onChange={(e) => setAns({ ...ans, [q.key]: e.target.value })}><option value="">Select…</option>{[1, 2, 3, 4, 5].map((n) => <option key={n}>{n}</option>)}</select>
                    : <textarea className="input mt-1 text-sm" rows={2} value={ans[q.key] ?? ""} onChange={(e) => setAns({ ...ans, [q.key]: e.target.value })} />}
                </label>))}
            </div>
            <div className="flex justify-end mt-3"><button className="btn-primary" disabled={save.isPending} onClick={() => save.mutate(true)}>Submit exit interview</button></div>
          </div>))}
    </div>
  );
}
