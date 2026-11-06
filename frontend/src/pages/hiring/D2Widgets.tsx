import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarCheck, CalendarX, Copy, Link2, RefreshCw } from "lucide-react";
import toast from "react-hot-toast";
import { err, fmtDateTime, hiringApi, Row } from "./api";
import { safeHref } from "../../lib/safeUrl";
import { Field, Input, Modal, Note, Pill, ReasonDialog, Select, Textarea } from "./ui";
import { FormGrid, FormSection } from "../../components/Modal";
import { v, collect, firstError, FieldError, Req } from "../../lib/validators";
import { fromDateTimeLocal, toDateTimeLocal } from "../../lib/datetime";   // 2026-10-25: UTC store, org-zone today

/* D2 widgets used inside the candidate / offer modals: interview scheduling tools,
   the aggregated scorecard, candidate contact preferences + message log, offer approval chain. */

const REC: Record<string, string> = { hire: "Hire", no_hire: "No hire", hold: "Hold" };

/** Slots + self-schedule link, calendar sync, reschedule, cancel — for one interview round. */
export function InterviewTools({ iv }: { iv: Row }) {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["hire", "sched", iv.id], queryFn: () => hiringApi.scheduling(iv.id) });
  const [slots, setSlots] = useState(false);
  const [resched, setResched] = useState(false);
  const [cancel, setCancel] = useState(false);
  const refresh = () => qc.invalidateQueries({ queryKey: ["hire"] });
  const sync = useMutation({ mutationFn: () => hiringApi.syncCalendar(iv.id), onSuccess: (r) => { toast.success(r.join_url ? `Calendar event ${r.status}. Join link: ${r.join_url}` : `Calendar event ${r.status} on ${r.provider}.`); refresh(); }, onError: (e) => toast.error(err(e)) });
  const doCancel = useMutation({ mutationFn: (reason: string) => hiringApi.cancelInterview(iv.id, reason), onSuccess: () => { toast.success("Round cancelled. The panel has been told; the calendar event is withdrawn if one existed."); setCancel(false); refresh(); }, onError: (e) => toast.error(err(e)) });
  if (iv.status === "cancelled") return null;
  const s = data?.slots, cal = data?.calendar;
  return (
    <div className="mt-2 border-t border-surface-border pt-2 text-xs space-y-1.5">
      <div className="flex flex-wrap items-center gap-1.5">
        <button className="btn-secondary !py-0.5 !px-2 text-[11px]" onClick={() => setSlots(true)}>Propose slots</button>
        <button className="btn-secondary !py-0.5 !px-2 text-[11px]" onClick={() => setResched(true)}>Reschedule</button>
        <button className="btn-secondary !py-0.5 !px-2 text-[11px] inline-flex items-center gap-1 disabled:opacity-50" disabled={!data?.can_sync || sync.isPending} title={data?.can_sync ? "Create or update the event on the connected calendar" : "Needs a date and a connected calendar (Hiring, Settings)"} onClick={() => sync.mutate()}><CalendarCheck className="w-3 h-3" />{cal?.event_id ? "Update calendar" : "Add to calendar"}</button>
        <button className="btn-secondary !py-0.5 !px-2 text-[11px] !text-red-600 inline-flex items-center gap-1" onClick={() => setCancel(true)}><CalendarX className="w-3 h-3" />Cancel round</button>
      </div>
      {s && <div className="text-gray-500">Slots: {s.link_state === "picked" ? <>candidate picked <b>{fmtDateTime(s.picked_slot?.start)}</b>{s.candidate_message ? ` · "${s.candidate_message}"` : ""}</> : s.link_state === "ok" ? `${(s.slots || []).length} proposed, link open until ${fmtDateTime(s.token_expires_at)}` : `link ${s.link_state}`}</div>}
      {cal && <div className="text-gray-500 flex flex-wrap items-center gap-1">Calendar: <Pill s={cal.status === "failed" ? "rejected" : "active"} label={`${cal.provider} ${cal.status}`} />{safeHref(cal.join_url) && <a className="underline text-primary-600 inline-flex items-center gap-1" href={safeHref(cal.join_url)} target="_blank" rel="noopener noreferrer"><Link2 className="w-3 h-3" />Join link</a>}{cal.last_error && <span className="text-red-600">{cal.last_error}</span>}</div>}
      {data && !data.connections?.length && <div className="text-gray-400">No organiser calendar connected: invites are not created automatically. Connect Google or Microsoft 365 in Hiring, Settings.</div>}
      {slots && <SlotsModal iv={iv} onClose={() => setSlots(false)} />}
      {resched && <RescheduleModal iv={iv} onClose={() => setResched(false)} />}
      {cancel && <ReasonDialog title={`Cancel round ${iv.round_no}: ${iv.round_name}`} hint="The panel is told, the candidate's open slot link closes and the calendar event is withdrawn." confirm="Cancel round" onClose={() => setCancel(false)} onConfirm={(r) => doCancel.mutate(r)} />}
    </div>
  );
}

const MODES: Record<string, string> = { video: "Video call", phone: "Phone", in_person: "In person" };
const browserTz = (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "America/Toronto"; } catch { return "America/Toronto"; } })();
const TZ_OPTIONS = [...new Set(["America/Toronto", browserTz, "America/Halifax", "America/St_Johns", "America/Winnipeg", "America/Regina", "America/Edmonton", "America/Vancouver", "America/Whitehorse"])];

function SlotsModal({ iv, onClose }: { iv: Row; onClose: () => void }) {
  const qc = useQueryClient();
  const [rows, setRows] = useState<string[]>(["", "", ""]);
  const [dur, setDur] = useState("45");
  const [tz, setTz] = useState(browserTz);
  const [mode, setMode] = useState<string>(iv.mode || "video");
  const [place, setPlace] = useState<string>(iv.place || "");
  const [shown, setShown] = useState(false);
  const [link, setLink] = useState<Row | null>(null);
  const filled = rows.filter(Boolean);
  const durN = Number(dur);
  const starts = filled.map((x) => new Date(x).getTime()).sort((p, q) => p - q);
  const overlap = Number.isFinite(durN) && starts.some((t, i) => i > 0 && t - starts[i - 1] < durN * 60000);
  const errs = collect({
    dur: v.required(dur, "Duration") || v.range(dur, 10, 480, "Duration") || (Number.isInteger(durN) ? null : "Duration must be whole minutes"),
    slots: !filled.length ? "Propose at least one slot"
      : filled.some((x) => new Date(x).getTime() <= Date.now()) ? "Every slot must be in the future"
      : new Set(filled).size !== filled.length ? "Two slots are the same time" : overlap ? "Two slots overlap for this duration" : null,
    place: mode === "video" && place && !/^https:\/\/\S+/i.test(place.trim()) ? "For a video call, paste the meeting link (https://…)"
      : mode === "in_person" && !place.trim() ? "Give the room or office address for an in-person round" : v.maxLen(place, 300, "Link or room"),
  });
  const er = (k: string) => (shown ? errs[k] : undefined);
  const save = useMutation({
    mutationFn: async () => {
      // where / how is kept on the interview itself, so the pick e-mail and the panel invite carry it
      if (mode !== iv.mode || place.trim() !== (iv.place || "")) await hiringApi.updateInterview(iv.id, { mode, place: place.trim() });
      return hiringApi.proposeSlots(iv.id, { slots: filled.map((x) => fromDateTimeLocal(x) as string), duration_min: durN, timezone: tz });
    },
    onSuccess: (r) => { setLink(r); qc.invalidateQueries({ queryKey: ["hire"] }); },
    onError: (e) => toast.error(err(e)),
  });
  const trySave = () => { setShown(true); if (Object.keys(errs).length) return void toast.error(firstError(errs)); save.mutate(); };
  const minNow = toDateTimeLocal(new Date());
  return (
    <Modal title={`Propose interview slots: ${iv.round_name}`} subtitle="The candidate gets a single-use link and picks one. Earlier proposals for this round are withdrawn." size="lg" onClose={onClose}
      footer={<><button className="btn-secondary" onClick={onClose}>Close</button>{!link && <button className="btn-primary disabled:opacity-50" disabled={save.isPending} onClick={trySave}>Send slots</button>}</>}>
      {link ? <div>
        <Note>{link.comms?.some((c: Row) => c.status === "sent") ? "The link was e-mailed to the candidate." : `The e-mail was not sent (${(link.comms || []).map((c: Row) => `${c.channel}: ${c.status}`).join(", ") || "no channel"}). Send the link yourself:`}</Note>
        <div className="flex gap-2"><Input readOnly value={link.pick_link} onFocus={(e) => e.target.select()} /><button className="btn-secondary inline-flex items-center gap-1" onClick={() => { navigator.clipboard?.writeText(link.pick_link); toast.success("Link copied."); }}><Copy className="w-4 h-4" />Copy</button></div>
        <p className="text-[11px] text-gray-400 mt-2">{link.note}</p>
      </div> : <>
        <FormSection title="When" hint="Offer 2 or 3 options. The candidate picks one; the rest are released. The link stays open for the days set in Hiring, Settings.">
          <FormGrid cols={2}>
            <Field label={<>Duration (minutes)<Req /></>} hint="How long each interview runs (10 to 480)."><Input type="number" min={10} max={480} step={5} value={dur} onChange={(e) => setDur(e.target.value)} /><FieldError msg={er("dur")} /></Field>
            <Field label="Candidate's time zone" hint="The times are shown to the candidate in this zone."><Select value={tz} onChange={(e) => setTz(e.target.value)}>
              {TZ_OPTIONS.map((z) => <option key={z} value={z}>{z}</option>)}</Select></Field>
            {rows.map((val, i) => <Field key={i} label={`Slot ${i + 1}`}><Input type="datetime-local" min={minNow} value={val} onChange={(e) => setRows(rows.map((x, j) => (j === i ? e.target.value : x)))} /></Field>)}
            <div className="sm:col-span-2"><FieldError msg={er("slots")} /><button className="btn-secondary !py-1 !px-2 text-xs" disabled={rows.length >= 10} onClick={() => setRows([...rows, ""])}>Add another slot</button></div>
          </FormGrid>
        </FormSection>
        <FormSection title="Where and how" hint="Saved on the round, so the panel and the candidate see the same details.">
          <FormGrid cols={2}>
            <Field label="Mode"><Select value={mode} onChange={(e) => setMode(e.target.value)}>{Object.entries(MODES).map(([k, lbl]) => <option key={k} value={k}>{lbl}</option>)}</Select></Field>
            <Field label={mode === "video" ? "Meeting link" : mode === "phone" ? "Number to call / notes" : <>Room or address<Req /></>}>
              <Input maxLength={300} placeholder={mode === "video" ? "https://" : ""} value={place} onChange={(e) => setPlace(e.target.value)} /><FieldError msg={er("place")} /></Field>
            <div className="sm:col-span-2 text-xs text-gray-500">Panel: {(iv.panel || []).map((x: Row) => x.name).join(", ") || "nobody yet"}</div>
          </FormGrid>
        </FormSection>
      </>}
    </Modal>
  );
}

function RescheduleModal({ iv, onClose }: { iv: Row; onClose: () => void }) {
  const qc = useQueryClient();
  const [when, setWhen] = useState("");
  const [reason, setReason] = useState("");
  const save = useMutation({
    mutationFn: () => hiringApi.reschedule(iv.id, { scheduled_at: fromDateTimeLocal(when), reason }),
    onSuccess: (r) => { toast.success(`Rescheduled. ${r.sync?.calendar ? (r.sync.calendar.ok ? "Calendar event updated." : `Calendar: ${r.sync.calendar.reason}`) : ""}`); qc.invalidateQueries({ queryKey: ["hire"] }); onClose(); },
    onError: (e) => toast.error(err(e)),
  });
  return (
    <Modal title={`Reschedule: ${iv.round_name}`} subtitle={`Now ${fmtDateTime(iv.scheduled_at) || "no time set"}. The panel and the candidate are told; open slot links close.`} size="lg" onClose={onClose}
      footer={<><button className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary disabled:opacity-50" disabled={!when || save.isPending} onClick={() => save.mutate()}>Reschedule</button></>}>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <Field label="New date and time *"><Input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} /></Field>
        <Field label="Reason"><Textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
      </div>
    </Modal>
  );
}

/** The aggregated scorecard for one application. */
export function ScorecardSummary({ appId }: { appId: string }) {
  const { data, isLoading } = useQuery({ queryKey: ["hire", "scorecard", appId], queryFn: () => hiringApi.applicationScorecard(appId) });
  if (isLoading || !data) return <div className="text-sm text-gray-400">Loading…</div>;
  if (!data.feedback_count) return <div className="text-sm text-gray-400 text-center py-6">No scorecards yet. Interviewers fill them in from My Interviews ({data.template?.name}).</div>;
  const r = data.recommendations;
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        {[["Weighted score", data.overall_weighted ?? "", "of 5"], ["Hire", r.hire, `${data.hire_share_pct ?? 0}% of ${data.feedback_count}`], ["No hire", r.no_hire, ""], ["Hold", r.hold, ""]].map(([k, v, sub]) => (
          <div key={k as string} className="card !py-2"><div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">{k}</div><div className="text-lg font-bold tabular-nums">{v as any}</div><div className="text-[11px] text-gray-400">{sub}</div></div>))}
      </div>
      <div className="border border-surface-border rounded-lg overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="bg-surface text-gray-400 text-[10px] uppercase"><tr><th className="px-2.5 py-1.5 text-left">Competency</th><th className="px-2.5 py-1.5 text-right">Weight</th><th className="px-2.5 py-1.5 text-right">Average</th>{(data.interviewers as Row[]).map((i) => <th key={i.feedback_id} className="px-2.5 py-1.5 text-right">{i.interviewer_name}</th>)}</tr></thead>
          <tbody>
            {(data.competencies as Row[]).map((c) => <tr key={c.key} className="border-t border-surface-border"><td className="px-2.5 py-1.5 font-medium">{c.label}</td><td className="px-2.5 py-1.5 text-right">{c.weight}</td><td className="px-2.5 py-1.5 text-right font-semibold tabular-nums">{c.avg}</td>
              {(data.interviewers as Row[]).map((i) => { const s = (i.scores || []).find((x: Row) => x.key === c.key); return <td key={i.feedback_id} className="px-2.5 py-1.5 text-right tabular-nums" title={s?.note || ""}>{s?.rating ?? ""}{s?.note ? "*" : ""}</td>; })}</tr>)}
            <tr className="border-t border-surface-border bg-surface font-semibold"><td className="px-2.5 py-1.5">Recommendation</td><td /><td />{(data.interviewers as Row[]).map((i) => <td key={i.feedback_id} className="px-2.5 py-1.5 text-right"><Pill s={i.recommendation === "hire" ? "accepted" : i.recommendation === "no_hire" ? "rejected" : "on_hold"} label={REC[i.recommendation]} /></td>)}</tr>
          </tbody>
        </table>
      </div>
      <p className="text-[11px] text-gray-400">* has a note (hover). Weighted score = sum(weight x average) / sum(weight). Template: {data.template?.name}{data.template?.hide_names ? " · names hidden from interviewers" : ""}.</p>
    </div>
  );
}

/** Contact preferences + what was sent (and what was not, and why). */
export function CandidateComms({ candidate, isHr }: { candidate: Row; isHr: boolean }) {
  const qc = useQueryClient();
  const log = useQuery({ queryKey: ["hire", "comms-log", candidate.id], queryFn: () => hiringApi.commsLog(candidate.id), enabled: isHr });
  const save = useMutation({ mutationFn: (b: Row) => hiringApi.setCandidateComms(candidate.id, b), onSuccess: () => { toast.success("Contact preference saved."); qc.invalidateQueries({ queryKey: ["hire"] }); }, onError: (e) => toast.error(err(e)) });
  if (!isHr) return null;
  const rows = log.data || [];
  const latest = rows[0];
  return (
    <div className="card !p-3 text-xs space-y-2">
      <div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">Messages to the candidate</div>
      <div className="flex gap-2">
        <button className="btn-secondary !py-0.5 !px-2 text-[11px]" onClick={() => save.mutate({ email_opt_in: true })}>E-mail: allow</button>
        <button className="btn-secondary !py-0.5 !px-2 text-[11px]" onClick={() => save.mutate({ email_opt_in: false })}>E-mail: stop</button>
        <button className="btn-secondary !py-0.5 !px-2 text-[11px]" onClick={() => save.mutate({ whatsapp_opt_in: true })}>WhatsApp: allow</button>
        <button className="btn-secondary !py-0.5 !px-2 text-[11px]" onClick={() => save.mutate({ whatsapp_opt_in: false })}>WhatsApp: stop</button>
      </div>
      {!rows.length ? <div className="text-gray-400">Nothing sent yet. Messages go out at stage changes and interview scheduling (Hiring, Messages).</div>
        : <div className="max-h-40 overflow-y-auto divide-y divide-surface-border">{rows.slice(0, 20).map((r: Row) => <div key={r.id} className="py-1 flex gap-2"><span className="w-28 text-gray-400 shrink-0">{fmtDateTime(r.at)}</span><span className="w-16 shrink-0">{r.channel}</span><span className="font-medium">{r.event}</span><Pill s={r.status === "sent" ? "accepted" : r.status === "failed" ? "rejected" : "on_hold"} label={r.status.replace(/_/g, " ")} />{r.reason && <span className="text-gray-400 truncate" title={r.reason}>{r.reason}</span>}</div>)}</div>}
      {latest && latest.status === "not_configured" && <div className="text-amber-700">{latest.reason}</div>}
    </div>
  );
}

/** Offer approval chain: steps, who is next, approve / reject for the current approver. */
export function OfferApprovalPanel({ offer }: { offer: Row }) {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["hire", "approval", offer.id], queryFn: () => hiringApi.approval(offer.id) });
  const [reject, setReject] = useState(false);
  const refresh = () => qc.invalidateQueries({ queryKey: ["hire"] });
  const submit = useMutation({ mutationFn: () => hiringApi.submitApproval(offer.id), onSuccess: (r) => { toast.success(`Submitted: ${r.steps.length} step(s), ${r.chain_source === "registry" ? "chain from the approval registry" : "default chain (hiring manager, then HR admin)"}.`); refresh(); }, onError: (e) => toast.error(err(e)) });
  const decide = useMutation({ mutationFn: (v: { d: "approve" | "reject"; c?: string }) => hiringApi.decideApproval(offer.id, v.d, v.c), onSuccess: (r) => { toast.success(r.status === "approved" ? "Offer approved. It can be sent now." : r.status === "draft" ? "Offer rejected: back to HR as a draft." : "Recorded. Next approver has been told."); setReject(false); refresh(); }, onError: (e) => toast.error(err(e)) });
  const steps: Row[] = data?.steps || [];
  const open = steps.some((s) => s.status === "pending" || s.status === "waiting");
  return (
    <div className="pt-2">
      <div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold mb-1 flex items-center gap-2">Approval chain {data?.chain_source && <span className="normal-case font-normal">({data.chain_source === "registry" ? "approval registry" : "default: hiring manager, then HR admin"})</span>}</div>
      {!steps.length ? <div className="text-xs text-gray-400">Not submitted for approval yet.</div>
        : steps.map((s) => <div key={s.id} className="text-xs flex items-center gap-2 py-0.5"><span className="w-5 text-gray-400">{s.step_no}.</span><span className="font-medium text-gray-700">{s.label}</span><Pill s={s.status === "approved" ? "accepted" : s.status === "rejected" ? "rejected" : s.status === "pending" ? "pending" : "draft"} label={s.status} />{s.decided_by_name && <span className="text-gray-400">{s.decided_by_name} · {fmtDateTime(s.decided_at)}{s.comment ? ` · ${s.comment}` : ""}</span>}</div>)}
      <div className="flex flex-wrap gap-1 mt-2">
        {offer.status === "draft" && !open && <button className="btn-primary !py-1 !px-2.5 text-xs inline-flex items-center gap-1 disabled:opacity-50" disabled={submit.isPending} onClick={() => submit.mutate()}><RefreshCw className="w-3 h-3" />{steps.length ? "Submit again" : "Submit for approval"}</button>}
        {data?.can_decide && <><button className="btn-primary !py-1 !px-2.5 text-xs" disabled={decide.isPending} onClick={() => decide.mutate({ d: "approve" })}>Approve step {data.current_step}</button>
          <button className="btn-secondary !py-1 !px-2.5 text-xs !text-red-600" onClick={() => setReject(true)}>Reject</button></>}
      </div>
      {data && data.inbox_available === false && <p className="text-[11px] text-amber-700 mt-1">The approvals inbox is not available on this installation; approvers decide from this screen.</p>}
      {reject && <ReasonDialog title="Reject this offer" hint="The offer goes back to HR as a draft with your reason." confirm="Reject" onClose={() => setReject(false)} onConfirm={(c) => decide.mutate({ d: "reject", c })} />}
    </div>
  );
}
