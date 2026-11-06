import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, RefreshCw } from "lucide-react";
import toast from "react-hot-toast";
import { money } from "../../api/endpoints";
import { EMP_TYPES, err, fmtDate, fmtDateTime, hiringApi, Row } from "./api";
import { Col, empLabel, Field, FirstStep, Grid, Input, Modal, Note, Pill, ReasonDialog, Select, useEmployees } from "./ui";
import { DataList, useMasterNames } from "./Requisitions";
import { OfferApprovalPanel } from "./D2Widgets";
import { FormGrid, FormSection } from "../../components/Modal";
import { v, collect, firstError, FieldError, Req } from "../../lib/validators";

/** A pay break-up saved with an older offer (new Canadian offers show the annual salary only). */
export function Breakup({ b }: { b?: Row | null }) {
  if (!b) return null;
  if (!b.ok) return <Note tone="warn">{b.reason || "The break-up is not available."}</Note>;
  return (
    <div className="border border-surface-border rounded-lg overflow-hidden">
      <table className="w-full text-xs">
        <thead className="bg-surface text-gray-400 text-[10px] uppercase tracking-wide"><tr>
          <th className="px-2.5 py-1.5 text-left">Component</th><th className="px-2.5 py-1.5 text-right">Monthly</th><th className="px-2.5 py-1.5 text-right">Annual</th></tr></thead>
        <tbody>
          {(b.lines || []).map((l: Row) => (
            <tr key={l.code} className="border-t border-surface-border"><td className="px-2.5 py-1.5">{l.name}</td>
              <td className="px-2.5 py-1.5 text-right tabular-nums">{money(l.monthly)}</td><td className="px-2.5 py-1.5 text-right tabular-nums">{money(l.annual)}</td></tr>
          ))}
          <tr className="border-t border-surface-border font-semibold bg-surface"><td className="px-2.5 py-1.5">Total</td>
            <td className="px-2.5 py-1.5 text-right tabular-nums">{money(b.monthly_gross)}</td><td className="px-2.5 py-1.5 text-right tabular-nums">{money(b.annual_ctc)}</td></tr>
        </tbody>
      </table>
      <div className="px-2.5 py-1.5 text-[11px] text-gray-400 border-t border-surface-border">
        Saved with the offer. Statutory deductions are worked out by payroll at pay time, not here.
        {(b.warnings || []).map((w: string, i: number) => <div key={i} className="text-amber-700">{w}</div>)}
      </div>
    </div>
  );
}

export function OfferForm({ appId, offer, defaults, onClose }: { appId?: string; offer?: Row | null; defaults?: Row; onClose: () => void }) {
  const qc = useQueryClient();
  const employees = useEmployees();
  const designations = useMasterNames("designations"), departments = useMasterNames("departments"), locations = useMasterNames("locations");
  const [f, setF] = useState<Row>(offer ? { ...offer } : { employment_type: "full_time", ...(defaults || {}) });
  const [preview, setPreview] = useState<Row | null>(offer?.breakup || null);
  const set = (k: string, v: any) => setF((p) => ({ ...p, [k]: v }));
  const budget = offer?.budget_max ?? defaults?.budget_max;
  const over = budget != null && Number(f.annual_ctc) > Number(budget);
  const doPreview = useMutation({
    mutationFn: () => hiringApi.previewCtc(Number(f.annual_ctc)),
    onSuccess: setPreview, onError: (e) => toast.error(err(e)),
  });
  const save = useMutation({
    mutationFn: () => {
      const body = { designation: f.designation, department: f.department, grade: f.grade, location: f.location,
        employment_type: f.employment_type, joining_date: f.joining_date || null, annual_ctc: f.annual_ctc,
        // a new offer with no pick keeps the requisition's hiring manager
        ...(f.manager_id || offer ? { manager_id: f.manager_id || null } : {}) };
      return offer ? hiringApi.updateOffer(offer.id, body) : hiringApi.createOffer(appId!, body);
    },
    onSuccess: (o) => { toast.success(`Offer saved as a draft. It needs ${o.approvals_required} approval${o.approvals_required > 1 ? "s" : ""} before it can be sent.`); qc.invalidateQueries({ queryKey: ["hire"] }); onClose(); },
    onError: (e) => toast.error(err(e)),
  });
  const [shown, setShown] = useState(false);
  const todayLocal = new Date().toLocaleDateString("en-CA");
  const errs = collect({
    designation: v.required(f.designation, "Designation") || v.maxLen(f.designation, 120, "Designation"),
    department: v.maxLen(f.department, 120, "Department"),
    grade: v.maxLen(f.grade, 60, "Grade"),
    location: v.maxLen(f.location, 120, "Location"),
    annual_ctc: v.required(f.annual_ctc, "Annual salary") || v.range(f.annual_ctc, 1, 1e9, "Annual salary"),
    joining_date: (!offer || f.joining_date !== offer.joining_date) ? v.dateOrder(todayLocal, f.joining_date, "Joining date cannot be in the past") : null,
  });
  const er = (k: string) => (shown ? errs[k] : undefined);
  const trySave = () => { setShown(true); if (Object.keys(errs).length) return void toast.error(firstError(errs)); save.mutate(); };
  return (
    <Modal size="xl" title={offer ? "Edit offer" : "Prepare an offer"} subtitle={defaults?.candidate_name || offer?.candidate_name} onClose={onClose}
      footer={<><button className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary disabled:opacity-50" disabled={save.isPending} onClick={trySave}>Save draft</button></>}>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <div>
          <FormSection title="Role and reporting">
            <FormGrid cols={2}>
              <Field label={<>Designation<Req /></>}><Input list="of-des" maxLength={120} value={f.designation || ""} onChange={(e) => set("designation", e.target.value)} /><FieldError msg={er("designation")} /></Field>
              <Field label="Department"><Input list="of-dep" maxLength={120} value={f.department || ""} onChange={(e) => set("department", e.target.value)} /><FieldError msg={er("department")} /></Field>
              <Field label="Grade"><Input maxLength={60} value={f.grade || ""} onChange={(e) => set("grade", e.target.value)} /><FieldError msg={er("grade")} /></Field>
              <Field label="Location"><Input list="of-loc" maxLength={120} value={f.location || ""} onChange={(e) => set("location", e.target.value)} /><FieldError msg={er("location")} /></Field>
              <Field label="Employment type"><Select value={f.employment_type || "full_time"} onChange={(e) => set("employment_type", e.target.value)}>
                {Object.entries(EMP_TYPES).map(([k, lbl]) => <option key={k} value={k}>{lbl}</option>)}</Select></Field>
              <Field label="Reports to" hint={offer ? "" : "Leave empty to keep the requisition's hiring manager."}><Select value={f.manager_id || ""} onChange={(e) => set("manager_id", e.target.value)}>
                <option value="">Pick an employee</option>{employees.map((x) => <option key={x.id} value={x.id}>{empLabel(x)}</option>)}</Select></Field>
            </FormGrid>
          </FormSection>
          <FormSection title="Compensation and dates">
            <FormGrid cols={2}>
              <Field label={<>Annual salary ($ a year)<Req /></>} hint={budget != null ? `Gross annual salary in Canadian dollars. Requisition budget: up to ${money(budget)}.` : "Gross annual salary in Canadian dollars. No budget was set on the requisition."}>
                <Input type="number" min={1} className="text-right tabular-nums" value={f.annual_ctc ?? ""} onChange={(e) => { set("annual_ctc", e.target.value); setPreview(null); }} /><FieldError msg={er("annual_ctc")} /></Field>
              <Field label="Joining date" hint="Needed before the offer is sent. Today or later."><Input type="date" min={todayLocal} value={f.joining_date || ""} onChange={(e) => set("joining_date", e.target.value)} /><FieldError msg={er("joining_date")} /></Field>
              {over && <div className="sm:col-span-2"><Note tone="warn">This is above the requisition budget. If your setting asks for it, two different approvers are needed.</Note></div>}
            </FormGrid>
          </FormSection>
          <p className="text-[11px] text-gray-400">How long the candidate has to accept is set by the offer-link validity in Hiring, Settings. Any edit sends the offer back for approval.</p>
        </div>
        <div>
          <div className="text-xs text-gray-500 border border-dashed border-surface-border rounded-lg p-4">The offer states the gross annual salary. CPP / QPP, EI and income tax are worked out by payroll at pay time from the employee's TD1 and province of employment.</div>
        </div>
      </div>
      <DataList id="of-des" items={designations} /><DataList id="of-dep" items={departments} /><DataList id="of-loc" items={locations} />
    </Modal>
  );
}

export function OfferActions({ o, onEdit, compact }: { o: Row; onEdit?: () => void; compact?: boolean }) {
  const qc = useQueryClient();
  const [ask, setAsk] = useState<"withdraw" | "declined" | null>(null);
  const [link, setLink] = useState<Row | null>(null);
  const act = useMutation({
    mutationFn: (v: { action: string; body?: Row }) => hiringApi.actOffer(o.id, v.action, v.body),
    onSuccess: (r, v) => {
      qc.invalidateQueries({ queryKey: ["hire"] }); setAsk(null);
      if (r.accept_link) setLink(r);
      else if (v.action === "approve") toast.success(r.status === "approved" ? "Offer approved. It can be sent now." : `Approval recorded: ${r.approvals_done} of ${r.approvals_required}. A second, different approver is needed.`);
      else if (v.action === "record-response") toast.success(r.status === "accepted" ? (r.onboarding_session_id ? "Acceptance recorded. Onboarding has started and the joiner invite is sent." : `Acceptance recorded, but onboarding did not start: ${r.onboarding_note || "try again"}`) : "Decline recorded.");
      else if (v.action === "issue-letter") r.letter ? toast.success(`Offer letter issued${r.letter.number ? ": " + r.letter.number : ""}.`) : toast.error(r.letter_note || "The letter was not issued.");
      else if (v.action === "retry-onboarding") toast.success("Onboarding started. The joiner invite is sent.");
      else toast.success("Done.");
    },
    onError: (e) => toast.error(err(e)),
  });
  const b = `${compact ? "!py-0.5 !px-2 text-[11px]" : "!py-1 !px-2.5 text-xs"}`;
  const can = o.can || {};
  return (
    <div className="flex flex-wrap gap-1" onClick={(e) => e.stopPropagation()}>
      {can.edit && onEdit && <button className={`btn-secondary ${b}`} onClick={onEdit}>Edit</button>}
      {can.approve && <button className={`btn-primary ${b}`} onClick={() => act.mutate({ action: "approve" })}>Approve</button>}
      {can.send && <button className={`btn-primary ${b}`} onClick={() => act.mutate({ action: "send" })}>Send to candidate</button>}
      {can.resend && <button className={`btn-secondary ${b}`} onClick={() => act.mutate({ action: "resend" })}>New link</button>}
      {can.record && <button className={`btn-secondary ${b}`} onClick={() => act.mutate({ action: "record-response", body: { accepted: true } })}>Record accepted</button>}
      {can.record && <button className={`btn-secondary ${b}`} onClick={() => setAsk("declined")}>Record declined</button>}
      {can.retry_onboarding && <button className={`btn-primary ${b}`} onClick={() => act.mutate({ action: "retry-onboarding" })}>Start onboarding</button>}
      {can.issue_letter && <button className={`btn-secondary ${b}`} onClick={() => act.mutate({ action: "issue-letter" })}>Issue offer letter</button>}
      {can.withdraw && <button className={`btn-secondary ${b}`} onClick={() => setAsk("withdraw")}>Withdraw</button>}
      {ask && <ReasonDialog title={ask === "withdraw" ? "Withdraw this offer" : "Record that the candidate declined"}
        hint={ask === "withdraw" ? (o.status === "accepted" ? "The application closes as Withdrawn and the onboarding session is cancelled." : "The candidate goes back to Selected.") : "Type what the candidate said."}
        confirm={ask === "withdraw" ? "Withdraw offer" : "Record declined"} onClose={() => setAsk(null)}
        onConfirm={(reason) => act.mutate(ask === "withdraw" ? { action: "withdraw", body: { reason } } : { action: "record-response", body: { accepted: false, reason } })} />}
      {link && (
        <Modal title="Offer sent" subtitle={link.emailed ? `An e-mail with the link went to ${link.candidate_email}, if e-mail is set up.` : "This candidate has no e-mail on file. Send the link yourself."} size="lg" onClose={() => setLink(null)}
          footer={<button className="btn-primary" onClick={() => setLink(null)}>Close</button>}>
          <p className="text-sm text-gray-600 mb-2">The candidate opens this link to accept or decline. It works once and is valid until {fmtDate(link.token_expires_at)}. It is shown only now.</p>
          <div className="flex gap-2"><Input readOnly value={link.accept_link} onFocus={(e) => e.target.select()} />
            <button className="btn-secondary inline-flex items-center gap-1" onClick={() => { navigator.clipboard?.writeText(link.accept_link); toast.success("Link copied."); }}><Copy className="w-4 h-4" />Copy</button></div>
          {!link.letter && link.letter_note && <div className="mt-3"><Note tone="warn">{link.letter_note}</Note></div>}
        </Modal>
      )}
    </div>
  );
}

export default function Offers() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["hire", "offers"], queryFn: hiringApi.offers });
  const [edit, setEdit] = useState<Row | null>(null);
  const [view, setView] = useState<Row | null>(null);
  const [status, setStatus] = useState("");
  const sync = useMutation({
    mutationFn: hiringApi.syncJoined,
    onSuccess: (r) => { toast.success(r.note || `Checked ${r.checked} accepted offer(s). ${r.joined} newly marked as Joined.`); qc.invalidateQueries({ queryKey: ["hire"] }); },
    onError: (e) => toast.error(err(e)),
  });
  const rows = (data || []).filter((o) => !status || o.status === status);
  const cols: Col<Row>[] = [
    { k: "candidate_name", label: "Candidate" },
    { k: "designation", label: "Designation" },
    { k: "req_number", label: "Requisition" },
    { k: "location", label: "Location" },
    { k: "joining_date", label: "Joining date", render: (o) => fmtDate(o.joining_date) },
    { k: "annual_ctc", label: "Annual salary", align: "right", render: (o) => money(o.annual_ctc) },
    { k: "above_budget", label: "Budget", render: (o) => (o.above_budget ? <Pill s="rejected" label="Above budget" /> : o.budget_max != null ? "Within" : "No budget set"), csv: (o) => (o.above_budget ? "Above budget" : "Within") },
    { k: "approvals_done", label: "Approvals", render: (o) => `${o.approvals_done} of ${o.approvals_required}`, csv: (o) => `${o.approvals_done}/${o.approvals_required}` },
    { k: "status", label: "Status", render: (o) => <Pill s={o.status} />, csv: (o) => o.status },
    { k: "token_expires_at", label: "Link valid until", render: (o) => (o.status === "sent" ? fmtDate(o.token_expires_at) : "") },
    { k: "onboarding_session_id", label: "Onboarding", render: (o) => (o.status !== "accepted" ? "" : o.onboarding_session_id ? <Pill s="active" label="Started" /> : <Pill s="rejected" label="Not started" />), csv: (o) => (o.onboarding_session_id ? "Started" : "") },
    { k: "letter", label: "Letter", render: (o) => (o.letter ? (o.letter.number || "Issued") : ""), csv: (o) => o.letter?.number || "" },
    { k: "_a", label: "Actions", csv: () => "", render: (o) => <OfferActions o={o} compact onEdit={() => setEdit(o)} /> },
  ];
  return (
    <div>
      {!isLoading && !(data || []).length ? (
        <FirstStep title="No offers yet">Open a candidate who is marked Selected (Hiring, Candidates) and press Prepare an offer. The offer then appears here for approval and sending.</FirstStep>
      ) : (
        <Grid rows={rows} cols={cols} keyOf={(o) => o.id} loading={isLoading} exportName="offers.csv" title="Offers" onRow={setView}
          filters={<select className="input !py-1.5 text-xs !w-36" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option>{["draft", "approved", "sent", "accepted", "declined", "expired", "withdrawn"].map((s) => <option key={s} value={s}>{s}</option>)}</select>}
          actions={<button className="btn-secondary !py-1 !px-2 text-xs inline-flex items-center gap-1" onClick={() => sync.mutate()} title="Marks people as Joined once Onboarding has activated them"><RefreshCw className="w-3.5 h-3.5" />Refresh joining status</button>} />
      )}
      {edit && <OfferForm offer={edit} onClose={() => setEdit(null)} />}
      {view && (
        <Modal title={`Offer: ${view.candidate_name}`} subtitle={`${view.designation} · ${view.req_number}`} onClose={() => setView(null)}>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
            <div className="text-sm space-y-1.5">
              {[["Status", <Pill s={view.status} />], ["Annual salary", money(view.annual_ctc)], ["Joining date", fmtDate(view.joining_date)],
                ["Department", view.department], ["Grade", view.grade], ["Location", view.location], ["Reports to", view.manager_name],
                ["Sent on", fmtDateTime(view.sent_at)], ["Answered on", fmtDateTime(view.responded_at)],
                ["Answered by", view.response_via === "link" ? `The candidate, through the link${view.signed_name ? " (signed " + view.signed_name + ")" : ""}` : view.response_via === "hr" ? "Recorded by HR" : ""],
                ["Reason", view.status_reason]].filter(([, v]) => v).map(([k, v]) => (
                <div key={k as string} className="flex gap-3"><span className="w-32 shrink-0 text-gray-400 text-xs pt-0.5">{k}</span><span className="text-gray-700">{v}</span></div>))}
              <div className="pt-2">
                <div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold mb-1">Approvals ({view.approvals_done} of {view.approvals_required})</div>
                {(view.approvals || []).length ? (view.approvals as Row[]).map((a, i) => <div key={i} className="text-xs text-gray-600">{a.name} · {fmtDateTime(a.at)}{a.decision ? ` · ${a.decision}` : ""}</div>) : <div className="text-xs text-gray-400">Nobody has approved yet.</div>}
              </div>
              <OfferApprovalPanel offer={view} />
              {view.onboarding_note && <Note tone="warn">Onboarding did not start: {view.onboarding_note}</Note>}
              {view.letter_note && !view.letter && <Note tone="warn">{view.letter_note}</Note>}
            </div>
            {view.breakup?.ok ? <div><div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold mb-1">Pay break-up saved with the offer</div><Breakup b={view.breakup} /></div> : <div />}
          </div>
        </Modal>
      )}
    </div>
  );
}
