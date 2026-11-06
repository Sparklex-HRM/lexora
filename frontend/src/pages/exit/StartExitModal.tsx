import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import AppModal, { FormSection } from "../../components/Modal";
import { v, collect, firstError, FieldError, Req } from "../../lib/validators";   // forms wrap-up 2026-10-24
import { employeeApi } from "../../api/endpoints";
import { exitApi, errText, dmy, type ExitCase } from "../../api/exit";
import { todayISO } from "../../lib/datetime";   // 2026-10-25: UTC store, org-zone today

/** Start an exit. HR / manager pick the employee and the type; "Immediate exit" takes the
 *  REAL last working day and reason (never "today" by default-stamp). Wide, two columns. */

const today = () => todayISO();
export const REASON_OPTIONS = ["Better opportunity", "Higher studies", "Relocation", "Personal / family", "Health",
  "Compensation", "Work environment", "Career change", "Performance", "Misconduct", "Role redundant",
  "Contract ended", "Retirement age", "Did not report to work", "Other"];

// 2026-10-24: thin wrapper over the app-wide AppModal.
export function WideModal({ title, onClose, children, footer, printable }: { title: string; onClose: () => void; children: React.ReactNode; footer?: React.ReactNode; printable?: boolean }) {
  return <AppModal title={title} onClose={onClose} footer={footer} printable={printable} size="xwide">{children}</AppModal>;
}

export default function StartExitModal({ employeeId, lockEmployee, defaultImmediate, onClose, onCreated }: {
  employeeId?: string; lockEmployee?: boolean; defaultImmediate?: boolean;
  onClose: () => void; onCreated: (c: ExitCase) => void;
}) {
  const qc = useQueryClient();
  const meta = useQuery({ queryKey: ["exit-meta"], queryFn: exitApi.meta });
  const emps = useQuery({ queryKey: ["employees"], queryFn: () => employeeApi.list() });
  const isHr = !!meta.data?.is_hr;
  const [f, setF] = useState({
    employee_id: employeeId || "", exit_type: "resignation", reason_category: "", reason: "", note: "",
    requested_last_working_day: "", immediate: !!defaultImmediate, last_working_day: "", lwd_reason: "",
    accepted_on: "", rehire: "", rehire_note: "",
  });
  const [errs, setErrs] = useState<Record<string, string>>({});
  const set = (k: string, v: any) => setF((x) => ({ ...x, [k]: v }));
  useEffect(() => { if (defaultImmediate && f.exit_type === "resignation") set("exit_type", "termination"); /* eslint-disable-next-line */ }, [defaultImmediate]);
  const people = useMemo(() => (emps.data || []).filter((e: any) => e.status !== "terminated"), [emps.data]);
  const types = (meta.data?.exit_types || []).filter((t) => isHr || ["resignation", "absconding", "end_of_contract"].includes(t.value));
  const preview = useQuery({
    queryKey: ["exit-notice-preview", f.employee_id], enabled: !!f.employee_id,
    queryFn: () => exitApi.noticePreview(f.employee_id),
  });
  const employerStarted = !!meta.data?.exit_types.find((t) => t.value === f.exit_type)?.employer_initiated;
  const create = useMutation({
    mutationFn: async () => { const c = await exitApi.create({
      employee_id: f.employee_id, exit_type: f.exit_type, reason_category: f.reason_category || undefined,
      reason: f.reason || undefined, note: f.note || undefined,
      requested_last_working_day: f.immediate ? undefined : f.requested_last_working_day || undefined,
      immediate: f.immediate, last_working_day: f.immediate ? f.last_working_day : undefined,
      lwd_reason: f.immediate ? (f.lwd_reason || f.reason || undefined) : undefined,
      accepted_on: f.immediate && f.accepted_on ? f.accepted_on : undefined,
    });
      // 2026-10-24: HR can record "eligible for rehire" right away (same call as the case's Rehire card)
      if (isHr && f.rehire) {
        try { await exitApi.rehireFlag(c.id, f.rehire === "yes", f.rehire_note.trim() || undefined); }
        catch { toast.error("Exit started, but the rehire flag was not saved — set it on the case."); }
      }
      return c;
    },
    onSuccess: (c) => {
      qc.invalidateQueries({ queryKey: ["exit-cases"] }); qc.invalidateQueries({ queryKey: ["employees"] });
      toast.success(`Exit case ${c.case_number} started`);
      onCreated(c);
    },
    onError: (e) => toast.error(errText(e, "Could not start the exit")),
  });
  const submit = () => {
    const found = collect({
      employee_id: v.required(f.employee_id, "Employee"),
      reason: employerStarted ? (v.required(f.reason, "Reason") || v.maxLen(f.reason, 2000, "Reason")) : v.maxLen(f.reason, 2000, "Reason"),
      note: v.maxLen(f.note, 2000, "Note"),
      last_working_day: f.immediate ? v.required(f.last_working_day, "Real last working day") : null,
      accepted_on: f.immediate ? (v.notFuture(f.accepted_on, "Notice date") || v.dateOrder(f.accepted_on, f.last_working_day, "The notice date cannot be after the last working day")) : null,
      lwd_reason: v.maxLen(f.lwd_reason, 300, "Why this date"),
      requested_last_working_day: !f.immediate && f.requested_last_working_day && f.requested_last_working_day < today() ? "The date asked for cannot be in the past — use Immediate exit for someone who has already left" : null,
      rehire_note: v.maxLen(f.rehire_note, 300, "Rehire note"),
    });
    setErrs(found);
    if (Object.keys(found).length) return toast.error(firstError(found));
    create.mutate();
  };
  const L = "block text-xs font-semibold text-gray-500";
  const H = "block text-[10px] font-normal text-gray-400 mt-0.5";
  return (
    <WideModal title={f.immediate ? "Immediate exit" : "Start an exit"} onClose={onClose}>
      <div className="grid md:grid-cols-2 gap-5">
        <FormSection title="Who and why" className="!mb-0"><div className="space-y-3">
          <label className={L}>Employee<Req />
            <select className="input mt-1" value={f.employee_id} disabled={lockEmployee} onChange={(e) => set("employee_id", e.target.value)}>
              <option value="">Select…</option>
              {people.map((e: any) => <option key={e.id} value={e.id}>{e.first_name} {e.last_name} · {e.employee_number}{e.department ? ` · ${e.department}` : ""}</option>)}
            </select><FieldError msg={errs.employee_id} /></label>
          <label className={L}>Type of exit<Req />
            <select className="input mt-1" value={f.exit_type} onChange={(e) => set("exit_type", e.target.value)}>
              {types.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
            </select>
            <span className={H}>{employerStarted ? "Started by the company — a written reason is needed." : "Started by the employee."}</span></label>
          <label className={L}>Reason (pick one)
            <select className="input mt-1" value={f.reason_category} onChange={(e) => set("reason_category", e.target.value)}>
              <option value="">Select…</option>{REASON_OPTIONS.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
            <span className={H}>The closest standard reason, for attrition reports. Add the detail below.</span></label>
          <label className={L}>Reason in your words{employerStarted && <Req />}
            <textarea className="input mt-1 text-sm" rows={3} maxLength={2000} value={f.reason} onChange={(e) => set("reason", e.target.value)} placeholder="What happened" /><FieldError msg={errs.reason} /></label>
        </div></FormSection>
        <FormSection title="Dates and notice" className="!mb-0"><div className="space-y-3">
          {isHr && (
            <label className="flex items-start gap-2 text-sm text-gray-700 rounded-lg bg-surface p-3">
              <input type="checkbox" className="mt-0.5" checked={f.immediate} onChange={(e) => set("immediate", e.target.checked)} />
              <span><b>Immediate exit.</b> Accept now and record the real last working day. Use this when the person has already left or leaves today.</span>
            </label>)}
          {f.immediate ? (<>
            <label className={L}>Real last working day<Req />
              <input type="date" className="input mt-1" value={f.last_working_day} max="2099-12-31" onChange={(e) => set("last_working_day", e.target.value)} /><FieldError msg={errs.last_working_day} />
              <span className={H}>The actual date. It can be in the past. Payroll pays up to this day.</span></label>
            <label className={L}>Notice given / accepted on
              <input type="date" className="input mt-1" value={f.accepted_on} max={f.last_working_day || today()} onChange={(e) => set("accepted_on", e.target.value)} /><FieldError msg={errs.accepted_on} />
              <span className={H}>The date notice was given (or the termination letter date). The notice served is counted from here. Blank = today, or the last working day if that is earlier.</span></label>
            <label className={L}>Why this date
              <input className="input mt-1" maxLength={300} value={f.lwd_reason} onChange={(e) => set("lwd_reason", e.target.value)} placeholder="e.g. Left without notice on this day" /><FieldError msg={errs.lwd_reason} /></label>
            <p className="text-[11px] text-gray-500">Short notice (buy-out, waiver or pay in lieu) and garden leave are set on the case's Notice card after saving.</p>
            {f.last_working_day && f.last_working_day <= today() && <p className="text-xs text-amber-700">On save: the employee is marked as left on {dmy(f.last_working_day)} and the login is switched off.</p>}
          </>) : (<>
            <label className={L}>Last working day the employee asks for (optional)
              <input type="date" className="input mt-1" min={today()} max="2099-12-31" value={f.requested_last_working_day} onChange={(e) => set("requested_last_working_day", e.target.value)} /><FieldError msg={errs.requested_last_working_day} />
              <span className={H}>What the employee is asking for. HR confirms the final date on acceptance.</span></label>
            {preview.data && (
              <div className="rounded-lg bg-surface p-3 text-xs text-gray-600">
                Notice for this employee: <b>{preview.data.days} days</b> ({preview.data.stage}, {preview.data.source === "policy" ? "from the policy table" : "company default"}).
                If accepted today, the last working day would be <b>{dmy(preview.data.expected_lwd)}</b>. HR fixes the date on acceptance.
              </div>)}
          </>)}
          <label className={L}>Note for HR (optional)
            <textarea className="input mt-1 text-sm" rows={2} maxLength={2000} value={f.note} onChange={(e) => set("note", e.target.value)} /><FieldError msg={errs.note} /></label>
          {isHr && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <label className={L}>Eligible for rehire
                <select className="input mt-1" value={f.rehire} onChange={(e) => set("rehire", e.target.value)}>
                  <option value="">Decide later</option><option value="yes">Yes</option><option value="no">No</option></select>
                <span className={H}>Shown when this person applies again. HR only.</span></label>
              {f.rehire && <label className={L}>Rehire note
                <input className="input mt-1" maxLength={300} value={f.rehire_note} onChange={(e) => set("rehire_note", e.target.value)} placeholder="e.g. Absconded — do not rehire" /><FieldError msg={errs.rehire_note} /></label>}
            </div>)}
        </div></FormSection>
      </div>
      <div className="flex items-center justify-end gap-2 mt-4">
        <button className="btn-secondary" onClick={onClose}>Cancel</button>
        <button className="btn-primary" disabled={create.isPending} onClick={submit}>{create.isPending ? "Saving…" : f.immediate ? "Record exit" : "Start exit"}</button>
      </div>
    </WideModal>
  );
}
