import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { ArrowLeft, CheckCircle2, DoorOpen, FileText, Plus, RefreshCw } from "lucide-react";
import { PageHeader, Spinner, StatTile } from "../../components/ui";
import { money } from "../../api/endpoints";
import { exitApi, errText, dmy, STATUS_TONE, type CaseDetail, type ClearanceItem, type ExitAction } from "../../api/exit";
import { WideModal } from "./StartExitModal";
import { usersApi } from "../../api/identity";
import { v, collect, firstError, FieldError, Req } from "../../lib/validators";   // forms wrap-up 2026-10-24
import { showLetter, printLetter } from "../lifecycle/LettersPage";   // print wrap-up 2026-10-24
import { todayISO } from "../../lib/datetime";   // 2026-10-25: UTC store, org-zone today

/** One exit case: state + actions, notice, clearance, assets, settlement, letters,
 *  exit interview (HR only) and the timeline. */

const today = () => todayISO();
const th = "px-3 py-2 font-semibold whitespace-nowrap text-left";
const td = "px-3 py-1.5 align-top";
const L = "block text-xs font-semibold text-gray-500";
const when = (iso?: string | null) => (iso ? new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "");

export default function ExitCasePage() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["exit-case", id], queryFn: () => exitApi.detail(id), enabled: !!id });
  const refresh = () => { qc.invalidateQueries({ queryKey: ["exit-case", id] }); qc.invalidateQueries({ queryKey: ["exit-cases"] }); };
  const [dialog, setDialog] = useState<ExitAction | null>(null);
  const act = useMutation({
    mutationFn: async (body: Record<string, any>) => {
      const { notice_treatment, notice_treatment_reason, garden_leave, ...rest } = body;
      const r = await exitApi.action(id, rest);
      if (rest.action === "accept" && (notice_treatment || garden_leave)) {
        // 2026-10-24: the short-notice choice / garden leave made on Accept are saved on the notice right away
        await exitApi.notice(id, { notice_treatment: notice_treatment || undefined, notice_treatment_reason: notice_treatment_reason || undefined, garden_leave: garden_leave || undefined });
      }
      return r;
    },
    onSuccess: (r) => { toast.success(`Case is now: ${r.case.status_label}`); setDialog(null); refresh(); qc.invalidateQueries({ queryKey: ["employees"] }); },
    onError: (e) => toast.error(errText(e, "Could not do that")),
  });
  if (q.isLoading) return <div className="grid place-items-center py-24"><Spinner /></div>;
  if (q.isError || !q.data) return <div className="card text-sm text-gray-600">This exit case could not be opened. <Link className="text-primary-600" to="/exits">Back to Exits</Link></div>;
  const d = q.data;
  const c = d.case;
  const isHr = (c.my_relation || []).includes("hr");
  const isPay = isHr || (c.my_relation || []).includes("payroll");
  const cur = "$";   // CAD; money() adds the symbol itself
  const onAction = (a: ExitAction) => {
    if (a.needs_reason || ["accept", "start_settlement", "close"].includes(a.action)) setDialog(a);
    else act.mutate({ action: a.action });
  };
  return (
    <div>
      <PageHeader title={`${c.case_number} · ${c.employee_name}`} icon={DoorOpen}
        subtitle={`${c.exit_type_label}${c.reason_category ? " · " + c.reason_category : ""} · ${c.department || "No department"} · ${c.job_title || ""}`}
        actions={<button className="btn-secondary inline-flex items-center gap-1.5" onClick={() => nav(-1)}><ArrowLeft className="w-4 h-4" />Back</button>} />
      <div className="grid grid-cols-2 lg:grid-cols-6 gap-3 mb-3">
        <StatTile label="State" value={c.status_label} sub={`Pending with: ${c.pending_with}`} accent />
        <StatTile label="Raised on" value={dmy(c.submitted_on)} sub={`by ${c.initiated_by}`} />
        <StatTile label="Last working day" value={dmy(c.lwd || c.requested_lwd)} sub={c.lwd ? (c.separated ? "Employee has left" : d.notice ? `${d.notice.days_left} day(s) to go` : "") : c.requested_lwd ? "asked by the employee" : "not set yet"} />
        <StatTile label="Notice" value={c.notice_days == null ? "—" : `${c.notice_days} days`} sub={c.notice_shortfall_days ? `short by ${c.notice_shortfall_days}` : c.notice_days != null ? "served in full" : ""} />
        <StatTile label="Clearance" value={`${d.clearance_summary.cleared} / ${d.clearance_summary.total}`} sub={d.clearance_summary.recovery_total ? `recover ${money(d.clearance_summary.recovery_total)}` : d.clearance_summary.complete ? "complete" : "pending"} />
        <StatTile label="Settlement" value={c.settlement_status === "paid" ? "Paid" : c.settlement_status === "started" ? "In progress" : "Not started"} sub={c.settlement_paid_at ? dmy(c.settlement_paid_at) : ""} />
      </div>
      <div className="card mb-3 flex flex-wrap items-center gap-2">
        <span className={STATUS_TONE[c.status] || "badge-purple"}>{c.status_label}</span>
        {c.withdraw_requested && <span className="badge-amber">The employee asked to withdraw: {c.withdraw_reason}</span>}
        {c.garden_leave && <span className="badge-teal">Garden leave</span>}
        <span className="flex-1" />
        {(c.actions || []).map((a) => (
          <button key={a.action} disabled={act.isPending}
            className={["reject", "manager_reject", "hold", "withdraw", "request_withdraw", "decline_withdraw"].includes(a.action) ? "btn-secondary" : "btn-primary"}
            onClick={() => onAction(a)}>{a.label}</button>))}
        {!(c.actions || []).length && <span className="text-xs text-gray-400">No action for you on this case right now.</span>}
      </div>
      {c.reason && <div className="card mb-3 text-sm text-gray-700"><span className="text-xs font-semibold text-gray-500 block mb-1">Reason given</span>{c.reason}{c.note && <span className="block text-xs text-gray-500 mt-1">Note: {c.note}</span>}</div>}

      <div className="grid xl:grid-cols-3 gap-3">
        <div className="xl:col-span-2 space-y-3">
          <NoticeCard d={d} isHr={isHr} onSaved={refresh} />
          <ClearanceCard d={d} isHr={isHr} cur={cur} onSaved={refresh} />
          <AssetsCard d={d} canEdit={isHr || (c.my_relation || []).includes("manager")} onSaved={refresh} />
          {isPay && <SettlementCard d={d} isHr={isHr} cur={cur} onAction={(body) => act.mutate(body)} busy={act.isPending} onSaved={refresh} />}
          <LettersCard d={d} isHr={isHr} onSaved={refresh} />
          {d.can_fill_interview && <InterviewCard d={d} isHr={isHr} onSaved={refresh} />}
          {isHr && <RehireCard d={d} onSaved={refresh} />}
        </div>
        <div className="card !p-0 self-start">
          <div className="px-4 py-2.5 border-b border-surface-border font-display font-semibold text-gray-800 text-sm">Timeline</div>
          <ol className="p-4 space-y-3">
            {d.timeline.map((e) => (
              <li key={e.id} className="text-xs border-l-2 border-primary-200 pl-3">
                <div className="font-semibold text-gray-800">{e.to_label ? `${e.from_label ? e.from_label + " → " : ""}${e.to_label}` : e.action.replace(/_/g, " ")}</div>
                {e.note && <div className="text-gray-600">{e.note}</div>}
                <div className="text-gray-400">{when(e.at)} · {e.actor}</div>
              </li>))}
          </ol>
        </div>
      </div>
      {dialog && <ActionDialog a={dialog} d={d} busy={act.isPending} onClose={() => setDialog(null)} onGo={(body) => act.mutate(body)} />}
    </div>
  );
}

function ActionDialog({ a, d, busy, onClose, onGo }: { a: ExitAction; d: CaseDetail; busy: boolean; onClose: () => void; onGo: (b: Record<string, any>) => void }) {
  const c = d.case;
  const [reason, setReason] = useState("");
  const [acceptedOn, setAcceptedOn] = useState(today());
  const [lwd, setLwd] = useState("");
  const [lwdReason, setLwdReason] = useState("");
  const [treat, setTreat] = useState("");
  const [treatReason, setTreatReason] = useState("");
  const [garden, setGarden] = useState(false);
  const [errs, setErrs] = useState<Record<string, string>>({});
  const meta = useQuery({ queryKey: ["exit-meta"], queryFn: exitApi.meta, enabled: a.action === "accept" });
  const pv = useQuery({ queryKey: ["exit-notice-preview", c.employee_id, acceptedOn], queryFn: () => exitApi.noticePreview(c.employee_id, acceptedOn), enabled: a.action === "accept" });
  const noNotice = ["death", "absconding", "end_of_contract", "retirement"].includes(c.exit_type);
  const expected = noNotice ? acceptedOn : pv.data?.expected_lwd;
  useEffect(() => { if (a.action === "accept" && expected && !lwd) setLwd(c.requested_lwd || expected); /* eslint-disable-next-line */ }, [expected]);
  const differs = a.action === "accept" && !!lwd && !!expected && lwd !== expected;
  const needOverride = a.action === "start_settlement" && !d.clearance_summary.complete;
  const needCloseReason = a.action === "close" && !d.letters_done;
  const short = differs && !!expected && lwd < expected;
  const needReason = a.needs_reason || needOverride || needCloseReason;
  const go = () => {
    const found = collect({
      acceptedOn: a.action === "accept" ? v.required(acceptedOn, "Accepted on") : null,
      lwd: a.action === "accept" ? (v.required(lwd, "Last working day")
        || (c.exit_type === "resignation" ? v.dateOrder(acceptedOn, lwd, "The last working day cannot be before the acceptance date") : null)) : null,
      lwdReason: differs ? (v.required(lwdReason, "Why the date is different") || v.maxLen(lwdReason, 300, "Date reason")) : null,
      treatReason: short && treat === "waiver" ? v.required(treatReason, "Reason for the waiver") : v.maxLen(treatReason, 300, "Reason"),
      reason: needReason ? (v.required(reason, "Reason") || v.maxLen(reason, 500, "Reason")) : v.maxLen(reason, 500, "Note"),
    });
    setErrs(found);
    if (Object.keys(found).length) return toast.error(firstError(found));
    onGo({
      action: a.action, reason: reason.trim() || undefined,
      ...(a.action === "accept" ? { accepted_on: acceptedOn, last_working_day: lwd || undefined, lwd_reason: lwdReason.trim() || undefined,
        notice_treatment: short ? treat || undefined : undefined, notice_treatment_reason: short ? treatReason.trim() || undefined : undefined, garden_leave: garden || undefined } : {}),
      ...(needOverride || needCloseReason ? { override_reason: reason.trim() } : {}),
    });
  };
  return (
    <WideModal title={`${a.label} — ${c.case_number}`} onClose={onClose}>
      <div className="grid md:grid-cols-2 gap-5">
        {a.action === "accept" ? (<>
          <div className="space-y-3">
            <label className={L}>Accepted on<Req /><input type="date" className="input mt-1" value={acceptedOn} onChange={(e) => { setAcceptedOn(e.target.value); setLwd(""); }} /><FieldError msg={errs.acceptedOn} />
              <span className="block text-[10px] font-normal text-gray-400 mt-0.5">Changing it recalculates the notice and the expected last working day.</span></label>
            <div className="rounded-lg bg-surface p-3 text-xs text-gray-600">
              {noNotice ? <>No notice period for this type of exit. HR can set notice days later if needed.</> : pv.data ? <>Notice: <b>{pv.data.days} days</b> ({pv.data.stage}, {pv.data.source === "policy" ? "policy table" : "company default"}). {pv.data.rule} Expected last working day: <b>{dmy(pv.data.expected_lwd)}</b>.</> : "Working out the notice…"}
              {c.requested_lwd && <div className="mt-1">The employee asked for <b>{dmy(c.requested_lwd)}</b>.</div>}
            </div>
          </div>
          <div className="space-y-3">
            <label className={L}>Last working day<Req /><input type="date" className="input mt-1" min={c.exit_type === "resignation" ? acceptedOn : undefined} value={lwd} onChange={(e) => setLwd(e.target.value)} /><FieldError msg={errs.lwd} />
              <span className="block text-[10px] font-normal text-gray-400 mt-0.5">The actual last day the employee works. Payroll pays up to this day.</span></label>
            {differs && <label className={L}>Why a different date<Req /><input className="input mt-1" maxLength={300} value={lwdReason} onChange={(e) => setLwdReason(e.target.value)} placeholder="e.g. Early release agreed with the manager" /><FieldError msg={errs.lwdReason} /></label>}
            {short && (
              <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 space-y-2">
                <p className="text-xs text-amber-800">Earlier than the notice end — the short days must be settled before the settlement starts. Decide now or later on the Notice card.</p>
                <label className={L}>Short days are settled by
                  <select className="input mt-1" value={treat} onChange={(e) => setTreat(e.target.value)}>
                    <option value="">Decide later</option>{(meta.data?.notice_treatments || []).map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}</select></label>
                {treat && <label className={L}>Reason{treat === "waiver" && <Req />}<input className="input mt-1" maxLength={300} value={treatReason} onChange={(e) => setTreatReason(e.target.value)} placeholder={treat === "waiver" ? "Needed for a waiver" : "Optional"} /><FieldError msg={errs.treatReason} /></label>}
              </div>)}
            {!noNotice && <label className="flex items-center gap-2 text-sm text-gray-700"><input type="checkbox" checked={garden} onChange={(e) => setGarden(e.target.checked)} />Garden leave during the notice (paid, not working)</label>}
          </div>
        </>) : (
          <div className="md:col-span-2 space-y-3">
            {needOverride && <p className="text-sm text-amber-700">Clearance is not complete ({d.clearance_summary.pending} line(s) pending: {d.clearance_summary.pending_departments.join(", ")}). Only HR can go ahead, with a reason.</p>}
            {needCloseReason && <p className="text-sm text-amber-700">The relieving letter and the experience letter are not both issued. Give a reason to close anyway.</p>}
            <label className={L}>{needReason ? <>Reason<Req /></> : "Note (optional)"}
              <textarea className="input mt-1 text-sm" rows={3} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} autoFocus /><FieldError msg={errs.reason} />
              <span className="block text-[10px] font-normal text-gray-400 mt-0.5">Kept on the case timeline. Up to 500 characters.</span></label>
          </div>)}
      </div>
      <div className="flex items-center justify-end gap-2 mt-4">
        <button className="btn-secondary" onClick={onClose}>Cancel</button>
        <button className="btn-primary" disabled={busy} onClick={go}>{busy ? "Saving…" : a.label}</button>
      </div>
    </WideModal>
  );
}

function Section({ title, right, children }: { title: string; right?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="card !p-0">
      <div className="px-4 py-2.5 border-b border-surface-border flex items-center justify-between gap-2"><h3 className="font-display font-semibold text-gray-800 text-sm">{title}</h3>{right}</div>
      <div className="p-4">{children}</div>
    </div>
  );
}

function NoticeCard({ d, isHr, onSaved }: { d: CaseDetail; isHr: boolean; onSaved: () => void }) {
  const c = d.case;
  const editable = isHr && ["accepted", "clearance", "settlement"].includes(c.status);
  const [f, setF] = useState<any>(null);
  const v = f || { last_working_day: c.lwd || "", lwd_reason: "", notice_days: c.notice_days ?? 0, notice_treatment: c.notice_treatment && c.notice_treatment !== "none" ? c.notice_treatment : "", notice_treatment_reason: c.notice_treatment_reason || "", garden_leave: c.garden_leave, leave_during_notice_days: c.leave_during_notice_days, roe_reason_code: c.roe_reason_code || "" };
  const set = (k: string, val: any) => setF({ ...v, [k]: val });
  const meta = useQuery({ queryKey: ["exit-meta"], queryFn: exitApi.meta });
  const save = useMutation({
    mutationFn: () => exitApi.notice(c.id, { ...v, last_working_day: v.last_working_day || undefined, lwd_reason: v.lwd_reason || undefined, notice_treatment: v.notice_treatment || undefined }),
    onSuccess: () => { toast.success("Notice details saved"); setF(null); onSaved(); }, onError: (e) => toast.error(errText(e)),
  });
  if (!c.accepted_on) return <Section title="Notice"><p className="text-sm text-gray-500">Notice is fixed when HR accepts the exit.{c.requested_lwd ? ` The employee asked for ${dmy(c.requested_lwd)}.` : ""}</p></Section>;
  const n = d.notice;
  return (
    <Section title="Notice and last working day" right={editable && f ? <button className="btn-primary !py-1 text-xs" disabled={save.isPending} onClick={() => save.mutate()}>Save</button> : undefined}>
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-xs mb-3">
        <div><span className="text-gray-400 block">Accepted on</span><b>{dmy(c.accepted_on)}</b></div>
        <div><span className="text-gray-400 block">Notice ({c.notice_source === "policy" ? "policy table" : c.notice_source === "manual" ? "set by HR" : c.notice_source === "not_applicable" ? "not applicable" : `default, ${c.notice_stage}`})</span><b>{c.notice_days} days</b></div>
        <div><span className="text-gray-400 block">Notice ends</span><b>{dmy(c.expected_lwd)}</b></div>
        <div><span className="text-gray-400 block">Served</span><b>{n ? Math.min(n.served_raw, Math.max(n.notice_days, n.served_raw)) : c.notice_served_days} days</b></div>
        <div><span className="text-gray-400 block">Short by</span><b className={c.notice_shortfall_days ? "text-amber-700" : ""}>{c.notice_shortfall_days} days</b></div>
      </div>
      {c.lwd_reason && <p className="text-xs text-gray-500 mb-2">Date reason: {c.lwd_reason}</p>}
      {c.notice_shortfall_days > 0 && !c.notice_treatment && <p className="text-xs text-amber-700 mb-2">Choose how the {c.notice_shortfall_days} short day(s) are settled before the settlement starts.</p>}
      {c.notice_treatment && c.notice_treatment !== "none" && !f && <p className="text-xs text-gray-600 mb-2">Short days: <b>{c.notice_treatment_label}</b>{c.notice_treatment_reason ? ` — ${c.notice_treatment_reason}` : ""}{c.notice_decided_by ? ` (${c.notice_decided_by})` : ""}</p>}
      {editable && (
        <div className="grid md:grid-cols-3 gap-3">
          <label className={L}>Last working day<input type="date" className="input mt-1" disabled={c.separated} value={v.last_working_day} onChange={(e) => set("last_working_day", e.target.value)} /></label>
          <label className={L}>Reason for changing the date<input className="input mt-1" value={v.lwd_reason} onChange={(e) => set("lwd_reason", e.target.value)} placeholder="needed when the date changes" /></label>
          <label className={L}>Notice days<input type="number" className="input mt-1" value={v.notice_days} onChange={(e) => set("notice_days", Number(e.target.value))} /></label>
          <label className={L}>Short days are settled by
            <select className="input mt-1" value={v.notice_treatment} onChange={(e) => set("notice_treatment", e.target.value)}><option value="">Not decided</option>{(meta.data?.notice_treatments || []).map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}</select></label>
          <label className={L}>Reason (needed for a waiver)<input className="input mt-1" value={v.notice_treatment_reason} onChange={(e) => set("notice_treatment_reason", e.target.value)} /></label>
          <label className={L}>Leave days taken during notice<input type="number" className="input mt-1" value={v.leave_during_notice_days} onChange={(e) => set("leave_during_notice_days", Number(e.target.value))} />
            <span className="block text-[10px] font-normal text-gray-400 mt-0.5">{c.leave_extends_lwd ? "Company rule: leave pushes the last working day out." : "Company rule: leave does not move the last working day."}</span></label>
          <label className="flex items-center gap-2 text-sm text-gray-700"><input type="checkbox" checked={!!v.garden_leave} onChange={(e) => set("garden_leave", e.target.checked)} />Garden leave (paid, not working)</label>
          {<label className={L}>Record of Employment reason code
            <select className="input mt-1" value={v.roe_reason_code} onChange={(e) => set("roe_reason_code", e.target.value)}><option value="">Select…</option>{(meta.data?.roe_codes || []).map((k) => <option key={k}>{k}</option>)}</select></label>}
        </div>)}
    </Section>
  );
}

function ClearanceCard({ d, isHr, cur, onSaved }: { d: CaseDetail; isHr: boolean; cur: string; onSaved: () => void }) {
  const c = d.case;
  const [edit, setEdit] = useState<ClearanceItem | null>(null);
  const [form, setForm] = useState({ status: "cleared", recovery_amount: "", comment: "" });
  const [line, setLine] = useState({ department: "", item: "", owner_role: "hr_admin", owner_user_id: "", owner_name: "" });
  const [errs, setErrs] = useState<Record<string, string>>({});
  const users = useQuery({ queryKey: ["logins", "all"], queryFn: () => usersApi.list(), enabled: isHr && ["accepted", "clearance"].includes(c.status), staleTime: 60_000 });
  const saveClear = () => {
    const amt = form.recovery_amount;
    const found = collect({
      recovery_amount: form.status !== "cleared_with_recovery" ? null
        : !String(amt).trim() || !(Number(amt) > 0) ? "Enter the amount to recover (more than 0)"
        : !/^\d+(\.\d{1,2})?$/.test(String(amt).trim()) ? "Amount can have at most 2 decimals" : v.range(amt, 0, 10000000, "Amount"),
      comment: form.status === "cleared_with_recovery" ? (v.required(form.comment, "What the recovery is for") || v.maxLen(form.comment, 300, "Comment")) : v.maxLen(form.comment, 300, "Comment"),
    });
    setErrs(found);
    if (Object.keys(found).length) return toast.error(firstError(found));
    clear.mutate();
  };
  const addLine = () => {
    const found = collect({
      department: v.required(line.department, "Department") || v.maxLen(line.department.trim(), 80, "Department"),
      item: v.required(line.item, "What must be cleared") || v.maxLen(line.item.trim(), 240, "Item"),
    });
    setErrs(found);
    if (Object.keys(found).length) return toast.error(firstError(found));
    add.mutate();
  };
  const clear = useMutation({
    mutationFn: () => exitApi.clear(c.id, edit!.id, { status: form.status, recovery_amount: form.status === "cleared_with_recovery" ? Number(form.recovery_amount) : undefined, comment: form.comment || undefined }),
    onSuccess: () => { toast.success("Clearance line updated"); setEdit(null); onSaved(); }, onError: (e) => toast.error(errText(e)),
  });
  const add = useMutation({ mutationFn: () => exitApi.addLine(c.id, { department: line.department.trim(), item: line.item.trim(), owner_role: line.owner_role,
      owner_user_id: line.owner_user_id || undefined, owner_name: line.owner_name || undefined }),
    onSuccess: () => { setLine({ department: "", item: "", owner_role: "hr_admin", owner_user_id: "", owner_name: "" }); onSaved(); }, onError: (e) => toast.error(errText(e)) });
  if (!d.clearance.length) return <Section title="Clearance"><p className="text-sm text-gray-500">The checklist is created when HR starts clearance.</p></Section>;
  const s = d.clearance_summary;
  return (
    <Section title={`Clearance — ${s.cleared} of ${s.total} done`} right={s.recovery_total ? <span className="badge-amber">Recover {money(s.recovery_total)} in the settlement</span> : s.complete ? <span className="badge-green">Complete</span> : undefined}>
      <div className="overflow-x-auto -m-4"><table className="w-full text-xs min-w-[760px]">
        <thead className="bg-surface text-gray-400 text-[11px] uppercase"><tr><th className={th}>Department</th><th className={th}>What</th><th className={th}>Owner</th><th className={th}>Status</th><th className={`${th} text-right`}>Recovery</th><th className={th}>Comment</th><th className={th}>By</th><th /></tr></thead>
        <tbody>{d.clearance.map((i) => (
          <tr key={i.id} className="border-t border-surface-border">
            <td className={`${td} font-medium text-gray-800 whitespace-nowrap`}>{i.department}</td><td className={td}>{i.item}</td>
            <td className={`${td} whitespace-nowrap`}>{i.owner_name || (i.owner_role === "manager" ? "Reporting manager" : i.owner_role === "payroll_admin" ? "Payroll / finance" : "HR")}</td>
            <td className={td}><span className={i.status === "pending" ? "badge-amber" : i.status === "cleared" ? "badge-green" : "badge-purple"}>{i.status === "cleared_with_recovery" ? "cleared, recover" : i.status}</span></td>
            <td className={`${td} text-right tabular-nums`}>{i.recovery_amount ? `${money(i.recovery_amount)}` : "—"}</td>
            <td className={td}>{i.comment || "—"}</td><td className={`${td} whitespace-nowrap text-gray-500`}>{i.acted_by ? `${i.acted_by} · ${when(i.acted_at)}` : "—"}</td>
            <td className={`${td} text-right`}>{i.can_clear && c.settlement_status !== "paid" && <button className="btn-secondary !py-1 text-xs" onClick={() => { setEdit(i); setForm({ status: i.status === "pending" ? "cleared" : i.status, recovery_amount: i.recovery_amount ? String(i.recovery_amount) : "", comment: i.comment || "" }); }}>Update</button>}</td>
          </tr>))}
          {isHr && ["accepted", "clearance"].includes(c.status) && (
            <tr className="border-t border-surface-border bg-surface/50">
              <td className={td}><input className="input !py-1 text-xs" list="exit-depts" maxLength={80} placeholder="Department *" value={line.department} onChange={(e) => setLine({ ...line, department: e.target.value })} /><FieldError msg={errs.department} />
                <datalist id="exit-depts">{["HR", "IT", "Admin", "Finance", "Payroll", "Reporting manager", "Security", "Stores"].map((x) => <option key={x} value={x} />)}</datalist></td>
              <td className={td}><input className="input !py-1 text-xs" maxLength={240} placeholder="What must be cleared *" value={line.item} onChange={(e) => setLine({ ...line, item: e.target.value })} /><FieldError msg={errs.item} /></td>
              <td className={td}><select className="input !py-1 text-xs" value={line.owner_role} onChange={(e) => setLine({ ...line, owner_role: e.target.value })}><option value="hr_admin">HR</option><option value="manager">Reporting manager</option><option value="payroll_admin">Payroll / finance</option></select>
                <span className="block text-[10px] text-gray-400 mt-0.5">Who signs off this line.</span></td>
              <td className={td} colSpan={2}><select className="input !py-1 text-xs" value={line.owner_user_id} title="Optional: a named person who may also clear this line" onChange={(e) => {
                  const u = (users.data?.items || []).find((x) => x.id === e.target.value);
                  setLine({ ...line, owner_user_id: e.target.value, owner_name: u ? u.name || u.email : "" }); }}>
                <option value="">Any {line.owner_role === "manager" ? "reporting manager" : line.owner_role === "payroll_admin" ? "payroll user" : "HR user"}</option>
                {(users.data?.items || []).filter((u) => u.status !== "disabled").map((u) => <option key={u.id} value={u.id}>{u.name || u.email} · {u.role_label}</option>)}</select>
                <span className="block text-[10px] text-gray-400 mt-0.5">Optional named person.</span></td>
              <td className={td} colSpan={2} /><td className={`${td} text-right`}><button className="btn-secondary !py-1 text-xs inline-flex items-center gap-1" disabled={add.isPending} onClick={addLine}><Plus className="w-3 h-3" />Add line</button></td>
            </tr>)}
        </tbody></table></div>
      {edit && (
        <WideModal title={`${edit.department} — ${edit.item}`} onClose={() => setEdit(null)}>
          <div className="grid md:grid-cols-2 gap-5">
            <div className="space-y-2">
              {[["cleared", "Cleared – nothing to recover"], ["cleared_with_recovery", "Cleared – recover an amount in the settlement"], ["pending", "Still pending"]].map(([k, l]) => (
                <label key={k} className="flex items-center gap-2 text-sm text-gray-700"><input type="radio" checked={form.status === k} onChange={() => setForm({ ...form, status: k })} />{l}</label>))}
              <p className="text-[11px] text-gray-400">"Cleared – recover" marks the line done and carries the amount into the full &amp; final settlement.</p>
            </div>
            <div className="space-y-3">
              {form.status === "cleared_with_recovery" && <label className={L}>Amount to recover ({cur})<Req /><input type="number" min={0} step="0.01" className="input mt-1 text-right" value={form.recovery_amount} onChange={(e) => setForm({ ...form, recovery_amount: e.target.value })} /><FieldError msg={errs.recovery_amount} />
                <span className="block text-[10px] font-normal text-gray-400 mt-0.5">Deducted from the employee's full &amp; final settlement for this item.</span></label>}
              <label className={L}>Comment{form.status === "cleared_with_recovery" ? <> (what the recovery is for)<Req /></> : ""}<textarea className="input mt-1 text-sm" rows={2} maxLength={300} value={form.comment} onChange={(e) => setForm({ ...form, comment: e.target.value })} /><FieldError msg={errs.comment} /></label>
            </div>
          </div>
          <div className="flex justify-end gap-2 mt-4"><button className="btn-secondary" onClick={() => setEdit(null)}>Cancel</button><button className="btn-primary" disabled={clear.isPending} onClick={saveClear}>Save</button></div>
        </WideModal>)}
    </Section>
  );
}

function AssetsCard({ d, canEdit, onSaved }: { d: CaseDetail; canEdit: boolean; onSaved: () => void }) {
  const c = d.case;
  const [a, setA] = useState({ item: "", tag: "", issued_on: "" });
  const add = useMutation({ mutationFn: () => exitApi.addAsset({ employee_id: c.employee_id, item: a.item, tag: a.tag || undefined, issued_on: a.issued_on || undefined }), onSuccess: () => { setA({ item: "", tag: "", issued_on: "" }); onSaved(); }, onError: (e) => toast.error(errText(e)) });
  const ret = useMutation({ mutationFn: (v: { id: string; condition: string }) => exitApi.updateAsset(v.id, { returned_on: today(), condition: v.condition }), onSuccess: () => { toast.success("Marked as returned"); onSaved(); }, onError: (e) => toast.error(errText(e)) });
  const out = d.assets.filter((x) => !x.returned).length;
  return (
    <Section title={`Assets issued — ${out} still with the employee`}>
      <div className="overflow-x-auto -m-4"><table className="w-full text-xs min-w-[620px]">
        <thead className="bg-surface text-gray-400 text-[11px] uppercase"><tr><th className={th}>Item</th><th className={th}>Tag / serial</th><th className={th}>Issued on</th><th className={th}>Returned on</th><th className={th}>Condition</th><th /></tr></thead>
        <tbody>
          {!d.assets.length && <tr><td className={`${td} text-gray-400`} colSpan={6}>No assets on record for this employee.</td></tr>}
          {d.assets.map((x) => (
            <tr key={x.id} className="border-t border-surface-border"><td className={`${td} font-medium text-gray-800`}>{x.item}</td><td className={td}>{x.tag || "—"}</td><td className={td}>{dmy(x.issued_on)}</td>
              <td className={td}>{x.returned ? dmy(x.returned_on) : <span className="badge-amber">not returned</span>}</td><td className={td}>{x.condition || "—"}</td>
              <td className={`${td} text-right`}>{canEdit && !x.returned && <button className="btn-secondary !py-1 text-xs" onClick={() => { const cond = window.prompt("Condition on return (e.g. Good, Damaged screen)", "Good"); if (cond !== null) ret.mutate({ id: x.id, condition: cond }); }}>Mark returned</button>}</td></tr>))}
          {canEdit && (
            <tr className="border-t border-surface-border bg-surface/50">
              <td className={td}><input className="input !py-1 text-xs" placeholder="Item, e.g. Laptop" value={a.item} onChange={(e) => setA({ ...a, item: e.target.value })} /></td>
              <td className={td}><input className="input !py-1 text-xs" placeholder="Tag / serial" value={a.tag} onChange={(e) => setA({ ...a, tag: e.target.value })} /></td>
              <td className={td}><input type="date" className="input !py-1 text-xs" value={a.issued_on} onChange={(e) => setA({ ...a, issued_on: e.target.value })} /></td>
              <td className={td} colSpan={2} /><td className={`${td} text-right`}><button className="btn-secondary !py-1 text-xs" disabled={!a.item || add.isPending} onClick={() => add.mutate()}>Add asset</button></td>
            </tr>)}
        </tbody></table></div>
    </Section>
  );
}

function SettlementCard({ d, isHr, cur, onAction, busy, onSaved }: { d: CaseDetail; isHr: boolean; cur: string; onAction: (b: Record<string, any>) => void; busy: boolean; onSaved: () => void }) {
  const c = d.case;
  const nav = useNavigate();
  const [override, setOverride] = useState("");
  const open = ["settlement", "settled", "closed"].includes(c.status);
  const st = useQuery({ queryKey: ["exit-settlement", c.id], queryFn: () => exitApi.settlement(c.id), enabled: open });
  const sync = useMutation({ mutationFn: () => exitApi.settlementSync(c.id), onSuccess: (r) => { toast.success(r.changed ? "Settlement is paid – case moved to Settled" : "Not paid yet"); onSaved(); }, onError: (e) => toast.error(errText(e)) });
  const pre = d.settlement.prefill;
  const pay = st.data?.pay_status;
  const calc = `/offboarding?case=${c.id}`;
  return (
    <Section title="Settlement" right={c.status === "settlement" ? <button className="btn-secondary !py-1 text-xs inline-flex items-center gap-1" disabled={sync.isPending} onClick={() => sync.mutate()}><RefreshCw className="w-3 h-3" />Check payment</button> : undefined}>
      {c.status === "clearance" && (
        <div className="space-y-2">
          {d.settlement.ready ? <p className="text-sm text-gray-700">Clearance is complete. The settlement can start.</p> : <p className="text-sm text-amber-700">{d.settlement.blocked_reason}</p>}
          {!d.clearance_summary.complete && isHr && c.lwd && (!c.notice_shortfall_days || c.notice_treatment) && (
            <label className={L}>Start anyway – reason<input className="input mt-1" value={override} onChange={(e) => setOverride(e.target.value)} placeholder="e.g. Employee abroad, laptop being couriered" /></label>)}
          <button className="btn-primary" disabled={busy || (!d.settlement.ready && !override.trim())} onClick={() => onAction({ action: "start_settlement", override_reason: override || undefined })}>Start settlement</button>
        </div>)}
      {!open && c.status !== "clearance" && <p className="text-sm text-gray-500">The settlement starts after clearance.</p>}
      {open && (
        <div className="space-y-3">
          {c.clearance_override && <p className="text-xs text-amber-700">Clearance was overridden by {c.clearance_override.by}: {c.clearance_override.reason}</p>}
          {pay ? (
            <div className="rounded-lg bg-surface p-3 text-xs text-gray-700">
              <b>Salary for the exit month.</b> {pay.note}
              {pay.excess_days > 0 && <div className="text-amber-700 mt-1">{pay.excess_days} day(s) were paid after the last working day. The settlement recovers them.</div>}
            </div>) : st.isFetched ? <p className="text-xs text-amber-700">Payroll could not be reached – what was already paid for the exit month is not shown.</p> : null}
          {pre && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
              <div><span className="text-gray-400 block">Last working day</span><b>{dmy(pre.last_working_day)}</b></div>
              <div><span className="text-gray-400 block">Notice short by</span><b>{pre.notice.shortfall} days</b> {pre.notice.shortfall ? `(${c.notice_treatment_label || "not decided"})` : ""}</div>
              <div><span className="text-gray-400 block">Clearance recoveries</span><b>{money(pre.clearance_recovery_total)}</b></div>
              <div><span className="text-gray-400 block">Calculator</span><b>Final pay + ROE</b></div>
            </div>)}
          {(pre?.manual || []).map((m: string, i: number) => <p key={i} className="text-xs text-amber-700">{m}</p>)}
          {c.status === "settlement" && (
            <div className="flex flex-wrap items-center gap-2">
              <button className="btn-primary" onClick={() => nav(calc)}>{c.settlement_ref ? "Open the settlement" : "Work out the settlement"}</button>
              <span className="text-xs text-gray-500">Opens the existing calculator, filled from this case. Sign it and pay it there.</span>
            </div>)}
          {c.settlement_status === "paid" && <p className="text-sm text-gray-700 inline-flex items-center gap-1.5"><CheckCircle2 className="w-4 h-4 text-green-600" />Paid on {dmy(c.settlement_paid_at)}{c.settlement_net != null ? ` · net ${money(c.settlement_net)}` : ""}. </p>}
        </div>)}
    </Section>
  );
}

function LettersCard({ d, isHr, onSaved }: { d: CaseDetail; isHr: boolean; onSaved: () => void }) {
  const c = d.case;
  const issue = useMutation({
    mutationFn: (v: { key: string; override?: string }) => exitApi.letter(c.id, v.key, v.override),
    onSuccess: (r) => { toast.success(r.closed ? "Letter issued – case closed" : "Letter issued"); onSaved(); }, onError: (e) => toast.error(errText(e, "The letter was not issued")),
  });
  return (
    <Section title="Letters" right={!d.helpers.letters ? <span className="badge-amber">Letter engine not available</span> : undefined}>
      <div className="overflow-x-auto -m-4"><table className="w-full text-xs min-w-[620px]">
        <thead className="bg-surface text-gray-400 text-[11px] uppercase"><tr><th className={th}>Letter</th><th className={th}>Status</th><th className={th}>Issued</th><th /></tr></thead>
        <tbody>{d.letters.map((l) => (
          <tr key={l.key} className="border-t border-surface-border">
            <td className={`${td} font-medium text-gray-800`}><FileText className="w-3.5 h-3.5 inline mr-1 text-gray-400" />{l.label}</td>
            <td className={td}>{l.issued ? <span className="badge-green">issued</span> : l.allowed ? <span className="badge-teal">ready to issue</span> : <span className="text-amber-700">{l.held_reason}</span>}</td>
            <td className={td}>{l.issued ? `${l.issued.number || ""} · ${when(l.issued.at)} · ${l.issued.by || ""}` : "—"}{l.issued?.override_reason ? <span className="block text-gray-400">Hold overridden: {l.issued.override_reason}</span> : null}</td>
            <td className={`${td} text-right whitespace-nowrap`}>
              {l.issued?.letter_id && <><button className="btn-secondary !py-1 text-xs mr-1" onClick={() => showLetter(l.issued!.letter_id!, `${l.issued!.number || l.key}.pdf`)}>Open</button>
                <button className="btn-secondary !py-1 text-xs mr-1" title="Print this letter (A4)" onClick={() => printLetter(l.issued!.letter_id!, `${l.issued!.number || l.key}.pdf`)}>Print</button></>}
              {isHr && l.allowed && <button className="btn-secondary !py-1 text-xs" disabled={issue.isPending} onClick={() => issue.mutate({ key: l.key })}>{l.issued ? "Issue again" : "Issue"}</button>}
              {isHr && !l.allowed && (l.held_reason || "").startsWith("Held") && <button className="btn-secondary !py-1 text-xs" disabled={issue.isPending} onClick={() => { const r = window.prompt("Reason for issuing before clearance and settlement are done"); if (r && r.trim()) issue.mutate({ key: l.key, override: r.trim() }); }}>Issue anyway</button>}
            </td>
          </tr>))}</tbody></table></div>
    </Section>
  );
}

function InterviewCard({ d, isHr, onSaved }: { d: CaseDetail; isHr: boolean; onSaved: () => void }) {
  const c = d.case;
  const prior: Record<string, string> = {};
  for (const a of d.interview?.answers || []) prior[a.key] = a.answer ?? "";
  const [ans, setAns] = useState<Record<string, string>>(prior);
  const done = !!d.interview?.submitted;
  const save = useMutation({
    mutationFn: (submit: boolean) => exitApi.interview(c.id, d.interview_template.map((q) => ({ key: q.key, question: q.question, answer: ans[q.key] ?? "" })), submit),
    onSuccess: (r) => { toast.success(r.submitted ? "Exit interview submitted" : "Saved"); onSaved(); }, onError: (e) => toast.error(errText(e)),
  });
  if (!isHr && done) return <Section title="Exit interview"><p className="text-sm text-gray-600">Thank you. Your exit interview is with HR. Only HR can read it.</p></Section>;
  return (
    <Section title="Exit interview" right={<span className="badge-purple">Visible to HR only</span>}>
      {isHr && d.interview && <p className="text-xs text-gray-500 mb-2">{d.interview.by_employee ? "Filled by the employee" : "Filled by HR"} · {d.interview.filled_by} · {when(d.interview.filled_at)} · {done ? "submitted" : "draft"}</p>}
      <div className="grid md:grid-cols-2 gap-3">
        {d.interview_template.map((q) => (
          <label key={q.key} className={L}>{q.question}
            {q.type === "yes_no" ? <select className="input mt-1" value={ans[q.key] ?? ""} onChange={(e) => setAns({ ...ans, [q.key]: e.target.value })}><option value="">Select…</option><option>Yes</option><option>No</option></select>
              : q.type === "rating" ? <select className="input mt-1" value={ans[q.key] ?? ""} onChange={(e) => setAns({ ...ans, [q.key]: e.target.value })}><option value="">Select…</option>{[1, 2, 3, 4, 5].map((n) => <option key={n}>{n}</option>)}</select>
              : <textarea className="input mt-1 text-sm" rows={2} value={ans[q.key] ?? ""} onChange={(e) => setAns({ ...ans, [q.key]: e.target.value })} />}
          </label>))}
      </div>
      <div className="flex justify-end gap-2 mt-3"><button className="btn-secondary" disabled={save.isPending} onClick={() => save.mutate(false)}>Save draft</button><button className="btn-primary" disabled={save.isPending} onClick={() => save.mutate(true)}>Submit</button></div>
    </Section>
  );
}

function RehireCard({ d, onSaved }: { d: CaseDetail; onSaved: () => void }) {
  const c = d.case;
  const [note, setNote] = useState(c.rehire_note || "");
  const set = useMutation({ mutationFn: (eligible: boolean | null) => exitApi.rehireFlag(c.id, eligible, note || undefined), onSuccess: () => { toast.success("Rehire flag saved"); onSaved(); }, onError: (e) => toast.error(errText(e)) });
  return (
    <Section title="Eligible for rehire?" right={c.rehire_eligible == null ? <span className="text-xs text-gray-400">not set</span> : c.rehire_eligible ? <span className="badge-green">Yes</span> : <span className="badge-red">No</span>}>
      <div className="flex flex-wrap items-end gap-2">
        <label className={`${L} flex-1 min-w-[240px]`}>Note (HR only)<input className="input mt-1" value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Good performer, left for studies" /></label>
        <button className="btn-secondary" disabled={set.isPending} onClick={() => set.mutate(true)}>Eligible</button>
        <button className="btn-secondary" disabled={set.isPending} onClick={() => set.mutate(false)}>Not eligible</button>
      </div>
    </Section>
  );
}
