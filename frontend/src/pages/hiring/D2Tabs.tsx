import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Plus, RefreshCw, Trash2 } from "lucide-react";
import toast from "react-hot-toast";
import { money } from "../../api/endpoints";
import { err, fmtDate, hiringApi, Row } from "./api";
import { safeHref } from "../../lib/safeUrl";
import { Col, downloadBlob, Field, FirstStep, Grid, Input, Modal, Note, Pill, Select, Textarea } from "./ui";
import { FormGrid, FormSection } from "../../components/Modal";
import { v, collect, firstError, FieldError, Req } from "../../lib/validators";

/* D2 tabs on the Hiring page: Referrals (HR), Scorecards, Messages; plus the calendar block for Settings. */

const BONUS: Record<string, string> = { none: "No bonus", waiting_join: "Waiting: not joined", waiting_days: "Joined: qualifying days", due: "Due", exported: "Sent to payroll", paid: "Paid", void: "Not payable" };

export function HrReferrals() {
  const qc = useQueryClient();
  const refs = useQuery({ queryKey: ["hire", "referrals"], queryFn: hiringApi.referrals });
  const bonuses = useQuery({ queryKey: ["hire", "ref-bonuses"], queryFn: hiringApi.referralBonuses });
  const reqs = useQuery({ queryKey: ["hire", "reqs"], queryFn: hiringApi.requisitions });
  const [rule, setRule] = useState<Row | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["hire"] });
  const sync = useMutation({ mutationFn: hiringApi.syncReferralBonuses, onSuccess: (r) => { toast.success(`Checked ${r.checked} referral(s); ${r.new_due} bonus(es) newly due.`); refresh(); }, onError: (e) => toast.error(err(e)) });
  const act = useMutation({ mutationFn: (v: { id: string; action: string }) => hiringApi.bonusAction(v.id, v.action), onSuccess: () => { toast.success("Updated."); refresh(); }, onError: (e) => toast.error(err(e)) });
  const refCols: Col<Row>[] = [
    { k: "first_name", label: "Person", render: (r) => `${r.first_name} ${r.last_name || ""}`.trim(), csv: (r) => `${r.first_name} ${r.last_name || ""}`.trim() },
    { k: "referrer_name", label: "Referred by" }, { k: "designation", label: "Job" }, { k: "req_number", label: "Requisition" },
    { k: "created_at", label: "On", render: (r) => fmtDate(r.created_at) },
    { k: "status", label: "Referral", render: (r) => <Pill s={r.status === "hired" ? "joined" : r.status === "duplicate" ? "rejected" : "screening"} label={r.status} />, csv: (r) => r.status },
    { k: "stage_label", label: "Stage", render: (r) => r.stage ? <Pill s={r.stage} label={r.stage_label} /> : "", csv: (r) => r.stage_label || "" },
    { k: "email", label: "E-mail" }, { k: "phone", label: "Phone" }, { k: "link_url", label: "Profile", render: (r) => r.link_url ? (safeHref(r.link_url) ? <a className="underline text-primary-600" href={safeHref(r.link_url)} target="_blank" rel="noopener noreferrer">link</a> : String(r.link_url)) : "" },
    { k: "bonus_amount", label: "Bonus", align: "right", render: (r) => (r.bonus_amount ? money(r.bonus_amount) : ""), csv: (r) => r.bonus_amount || "" },
    { k: "bonus_state", label: "Bonus status", render: (r) => BONUS[r.bonus_state] || r.bonus_state, csv: (r) => BONUS[r.bonus_state] || r.bonus_state },
    { k: "note", label: "Why", render: (r) => <span className="max-w-[14rem] truncate inline-block align-bottom" title={r.note || ""}>{r.note || ""}</span> },
  ];
  const bonusCols: Col<Row>[] = [
    { k: "employee_name", label: "Pay to (referrer)" }, { k: "hired_name", label: "Hired person" },
    { k: "amount", label: "Amount", align: "right", render: (b) => money(b.amount) }, { k: "due_on", label: "Due on", render: (b) => fmtDate(b.due_on) }, { k: "pay_month", label: "Pay month" },
    { k: "status", label: "Status", render: (b) => <Pill s={b.status === "paid" ? "accepted" : b.status === "cancelled" ? "rejected" : b.status === "exported" ? "sent" : "pending"} label={b.status} />, csv: (b) => b.status },
    { k: "pay_entry_ref", label: "Payroll ref" }, { k: "note", label: "Note" },
    { k: "_a", label: "Actions", csv: () => "", render: (b) => <div className="flex gap-1" onClick={(e) => e.stopPropagation()}>
      {b.status === "due" && <button className="btn-secondary !py-0.5 !px-2 text-[11px]" onClick={() => act.mutate({ id: b.id, action: "mark-exported" })}>Mark sent to payroll</button>}
      {["due", "exported"].includes(b.status) && <button className="btn-primary !py-0.5 !px-2 text-[11px]" onClick={() => act.mutate({ id: b.id, action: "mark-paid" })}>Mark paid</button>}
      {["due", "exported"].includes(b.status) && <button className="btn-secondary !py-0.5 !px-2 text-[11px] !text-red-600" onClick={() => { if (window.confirm("Cancel this bonus?")) act.mutate({ id: b.id, action: "cancel" }); }}>Cancel</button>}</div> },
  ];
  const ruleCols: Col<Row>[] = [
    { k: "req_number", label: "Requisition" }, { k: "designation", label: "Designation" }, { k: "status", label: "Status", render: (r) => <Pill s={r.status} />, csv: (r) => r.status },
    { k: "_a", label: "Referral bonus", csv: () => "", render: (r) => <button className="btn-secondary !py-0.5 !px-2 text-[11px]" onClick={(e) => { e.stopPropagation(); setRule(r); }}>Set bonus rule</button> },
  ];
  return (
    <div className="space-y-4">
      <Note>{bonuses.data?.payroll_note || "Bonuses that become due are listed below for payroll."}</Note>
      <Grid rows={refs.data || []} cols={refCols} keyOf={(r) => r.id} loading={refs.isLoading} title="Employee referrals" exportName="referrals.csv" empty="No referrals yet. Employees refer people from Refer a Friend." />
      <Grid rows={bonuses.data?.items || []} cols={bonusCols} keyOf={(b) => b.id} loading={bonuses.isLoading} title="Referral bonuses due (code REFBONUS)" exportName="referral-bonuses.csv" empty="Nothing due. A bonus becomes due when the referred person has joined and the qualifying days have passed."
        actions={<><button className="btn-secondary !py-1 !px-2 text-xs inline-flex items-center gap-1" onClick={() => sync.mutate()}><RefreshCw className="w-3.5 h-3.5" />Check for due bonuses</button>
          <button className="btn-secondary !py-1 !px-2 text-xs inline-flex items-center gap-1" onClick={async () => { try { downloadBlob("referral-bonuses.csv", await hiringApi.bonusesCsvBlob()); } catch (e) { toast.error(err(e)); } }}><Download className="w-3.5 h-3.5" />CSV for payroll</button></>} />
      <Grid rows={(reqs.data || []).filter((r) => ["open", "on_hold"].includes(r.status))} cols={ruleCols} keyOf={(r) => r.id} title="Referral bonus per requisition" exportName="referral-rules.csv" empty="No open requisition." />
      {rule && <RuleModal req={rule} onClose={() => setRule(null)} />}
    </div>
  );
}

function RuleModal({ req, onClose }: { req: Row; onClose: () => void }) {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["hire", "rule", req.id], queryFn: () => hiringApi.referralRule(req.id) });
  const [f, setF] = useState<Row | null>(null);
  const [shown, setShown] = useState(false);
  useEffect(() => { if (data && !f) setF(data); }, [data, f]);
  const save = useMutation({ mutationFn: () => hiringApi.saveReferralRule(req.id, { bonus_amount: f!.bonus_amount, payable_after_days: f!.payable_after_days, active: f!.active }), onSuccess: () => { toast.success("Referral bonus saved."); qc.invalidateQueries({ queryKey: ["hire"] }); onClose(); }, onError: (e) => toast.error(err(e)) });
  if (!f) return null;
  const errs = collect({
    bonus_amount: v.required(f.bonus_amount, "Bonus amount") || v.range(f.bonus_amount, 0, 1000000, "Bonus amount")
      || (f.active && Number(f.bonus_amount) <= 0 ? "An active rule needs a bonus above $0 (or set Active to No)" : null),
    payable_after_days: v.required(f.payable_after_days, "Payable after") || v.range(f.payable_after_days, 0, 365, "Payable after")
      || (Number.isInteger(Number(f.payable_after_days)) ? null : "Payable after must be whole days"),
  });
  const er = (k: string) => (shown ? errs[k] : undefined);
  const trySave = () => { setShown(true); if (Object.keys(errs).length) return void toast.error(firstError(errs)); save.mutate(); };
  return (
    <Modal title={`Referral bonus: ${req.req_number} · ${req.designation}`} subtitle={f.source === "default" ? "Using the org default (Hiring, Settings). Save to set a rule for this job." : "This job has its own rule."} size="lg" onClose={onClose}
      footer={<><button className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={save.isPending} onClick={trySave}>Save</button></>}>
      <FormGrid cols={3}>
        <Field label={<>Bonus amount ($)<Req /></>} hint="Gross amount before tax. Payroll pays it as a taxable earning (code REFBONUS)."><Input type="number" min={0} max={1000000} className="text-right tabular-nums" value={f.bonus_amount ?? ""} onChange={(e) => setF({ ...f, bonus_amount: e.target.value })} /><FieldError msg={er("bonus_amount")} /></Field>
        <Field label={<>Payable after (days of joining)<Req /></>} hint="Days after the referred person joins, e.g. 90 or 180 (after probation)."><Input type="number" min={0} max={365} step={1} value={f.payable_after_days ?? ""} onChange={(e) => setF({ ...f, payable_after_days: e.target.value })} /><FieldError msg={er("payable_after_days")} /></Field>
        <Field label="Active" hint="No: referrals for this job earn no bonus."><Select value={f.active ? "1" : "0"} onChange={(e) => setF({ ...f, active: e.target.value === "1" })}><option value="1">Yes</option><option value="0">No</option></Select></Field>
      </FormGrid>
      <p className="text-[11px] text-gray-400 mt-3">The amount and days are copied onto each referral when it is made, so a later change does not alter referrals already in progress.</p>
    </Modal>
  );
}

export function Scorecards() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["hire", "scorecards"], queryFn: hiringApi.scorecards });
  const reqs = useQuery({ queryKey: ["hire", "reqs"], queryFn: hiringApi.requisitions });
  const [edit, setEdit] = useState<Row | "new" | null>(null);
  const del = useMutation({ mutationFn: (id: string) => hiringApi.deleteScorecard(id), onSuccess: () => { toast.success("Scorecard retired."); qc.invalidateQueries({ queryKey: ["hire"] }); }, onError: (e) => toast.error(err(e)) });
  const cols: Col<Row>[] = [
    { k: "name", label: "Scorecard" }, { k: "designation", label: "Applies to", render: (t) => t.requisition_id ? `${t.req_number} · ${t.designation}` : "Every requisition without its own", csv: (t) => t.requisition_id ? t.req_number : "Org default" },
    { k: "competencies", label: "Competencies", render: (t) => (t.competencies || []).map((c: Row) => `${c.label} (x${c.weight})`).join(", "), csv: (t) => (t.competencies || []).map((c: Row) => c.label).join("; ") },
    { k: "hide_names", label: "Bias guard", render: (t) => (t.hide_names ? <Pill s="active" label="Names hidden" /> : ""), csv: (t) => (t.hide_names ? "Names hidden" : "") },
    { k: "_a", label: "", csv: () => "", render: (t) => <div className="flex gap-1" onClick={(e) => e.stopPropagation()}><button className="btn-secondary !py-0.5 !px-2 text-[11px]" onClick={() => setEdit(t)}>Edit</button><button className="btn-secondary !py-0.5 !px-2 text-[11px] !text-red-600" onClick={() => { if (window.confirm("Retire this scorecard? Past feedback keeps its answers.")) del.mutate(t.id); }}><Trash2 className="w-3 h-3" /></button></div> },
  ];
  return (
    <div className="space-y-3">
      <Note>Interviewers rate each competency 1 to 5 and give a hire / no-hire recommendation. Without a scorecard of its own a requisition uses the org default, and without that the built-in five: {(data?.built_in || []).map((c: Row) => c.label).join(", ")}.</Note>
      {!isLoading && !(data?.templates || []).length ? <FirstStep title="No scorecard yet"><button className="btn-primary inline-flex items-center gap-1.5 mt-2" onClick={() => setEdit("new")}><Plus className="w-4 h-4" />Create a scorecard</button></FirstStep>
        : <Grid rows={data?.templates || []} cols={cols} keyOf={(t) => t.id} loading={isLoading} title="Scorecards" exportName="scorecards.csv" actions={<button className="btn-primary !py-1 !px-2.5 text-xs inline-flex items-center gap-1" onClick={() => setEdit("new")}><Plus className="w-3.5 h-3.5" />New scorecard</button>} />}
      {edit && <ScorecardForm row={edit === "new" ? null : edit} reqs={reqs.data || []} builtIn={data?.built_in || []} onClose={() => setEdit(null)} />}
    </div>
  );
}

function ScorecardForm({ row, reqs, builtIn, onClose }: { row: Row | null; reqs: Row[]; builtIn: Row[]; onClose: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState<Row>(row ? { ...row } : { name: "Standard scorecard", requisition_id: "", hide_names: false, competencies: builtIn.map((c) => ({ ...c })) });
  const [shown, setShown] = useState(false);
  const comps: Row[] = f.competencies || [];
  const setC = (i: number, k: string, val: any) => setF({ ...f, competencies: comps.map((c, j) => (j === i ? { ...c, [k]: val } : c)) });
  const labels = comps.map((c) => String(c.label || "").trim().toLowerCase());
  const rowErr = (c: Row, i: number) => (!String(c.label || "").trim() ? "Name this competency"
    : String(c.label).length > 120 ? "At most 120 characters"
    : labels.indexOf(labels[i]) !== i ? "Listed twice"
    : v.range(c.weight, 0.5, 10, "Weight") || v.maxLen(c.description, 400, "Description"));
  const errs = collect({
    name: v.required(f.name, "Name") || v.maxLen(f.name, 120, "Name"),
    competencies: !comps.length ? "Add at least one competency" : comps.length > 20 ? "At most 20 competencies" : null,
    ...Object.fromEntries(comps.map((c, i) => [`c${i}`, rowErr(c, i) ? `Competency ${i + 1}: ${rowErr(c, i)}` : null])),
  });
  const er = (k: string) => (shown ? errs[k] : undefined);
  const save = useMutation({ mutationFn: () => hiringApi.saveScorecard({ id: row?.id, name: String(f.name).trim(), requisition_id: f.requisition_id || null, hide_names: !!f.hide_names, competencies: comps.map((c) => ({ ...c, label: String(c.label || "").trim() })) }), onSuccess: () => { toast.success("Scorecard saved."); qc.invalidateQueries({ queryKey: ["hire"] }); onClose(); }, onError: (e) => toast.error(err(e)) });
  const trySave = () => { setShown(true); if (Object.keys(errs).length) return void toast.error(firstError(errs)); save.mutate(); };
  return (
    <Modal title={row ? "Edit scorecard" : "New scorecard"} subtitle="One scorecard per requisition, or one org default. Interviewers rate every competency 1 to 5." onClose={onClose}
      footer={<><button className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary disabled:opacity-50" disabled={save.isPending} onClick={trySave}>Save</button></>}>
      <FormGrid cols={3} className="mb-3">
        <Field label={<>Name<Req /></>}><Input maxLength={120} value={f.name || ""} onChange={(e) => setF({ ...f, name: e.target.value })} /><FieldError msg={er("name")} /></Field>
        <Field label="Applies to"><Select value={f.requisition_id || ""} onChange={(e) => setF({ ...f, requisition_id: e.target.value })}><option value="">Org default (every requisition)</option>{reqs.map((r) => <option key={r.id} value={r.id}>{r.req_number} · {r.designation}</option>)}</Select></Field>
        <Field label="Bias guard" hint="Hides the candidate's name and current employer from interviewers while they score."><Select value={f.hide_names ? "1" : "0"} onChange={(e) => setF({ ...f, hide_names: e.target.value === "1" })}><option value="0">Show names</option><option value="1">Hide names from interviewers</option></Select></Field>
      </FormGrid>
      <FormSection title={`Competencies (${comps.length} of 20)`} hint="Weight is the relative importance: 0.5 = minor, 10 = critical. The overall score is weighted by it.">
        <div className="hidden sm:grid sm:grid-cols-[1fr_6rem_2fr_auto] gap-2 text-[10px] uppercase tracking-wide text-gray-400 font-semibold mb-1"><span>Competency *</span><span>Weight</span><span>What a 5 looks like</span><span /></div>
        <div className="space-y-2">
          {comps.map((c, i) => <div key={i}>
            <div className="grid grid-cols-1 sm:grid-cols-[1fr_6rem_2fr_auto] gap-2 items-start">
              <Input placeholder="Competency" maxLength={120} aria-label={`Competency ${i + 1}`} value={c.label || ""} onChange={(e) => setC(i, "label", e.target.value)} />
              <Input type="number" min={0.5} max={10} step={0.5} aria-label={`Weight ${i + 1}`} value={c.weight ?? 1} onChange={(e) => setC(i, "weight", e.target.value)} />
              <Input placeholder="What a 5 looks like" maxLength={400} aria-label={`Description ${i + 1}`} value={c.description || ""} onChange={(e) => setC(i, "description", e.target.value)} />
              <button className="btn-secondary !py-1 !px-2 text-xs !text-red-600" onClick={() => setF({ ...f, competencies: comps.filter((_, j) => j !== i) })}>Remove</button>
            </div>
            <FieldError msg={shown ? (rowErr(c, i) || undefined) : undefined} />
          </div>)}
        </div>
        <FieldError msg={er("competencies")} />
        <button className="btn-secondary !py-1 !px-2 text-xs mt-2" disabled={comps.length >= 20} onClick={() => setF({ ...f, competencies: [...comps, { label: "", weight: 1, description: "" }] })}>Add a competency</button>
      </FormSection>
    </Modal>
  );
}

export function Messages() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["hire", "comms"], queryFn: hiringApi.commsTemplates });
  const [edit, setEdit] = useState<Row | null>(null);
  const [dirty, setDirty] = useState<Record<string, Row>>({});
  const save = useMutation({ mutationFn: (items: Row[]) => hiringApi.saveCommsTemplates(items), onSuccess: () => { toast.success("Messages saved."); setDirty({}); setEdit(null); qc.invalidateQueries({ queryKey: ["hire"] }); }, onError: (e) => toast.error(err(e)) });
  const preview = useMutation({ mutationFn: (b: Row) => hiringApi.commsPreview(b) });
  const [mShown, setMShown] = useState(false);
  if (isLoading || !data) return <div className="text-sm text-gray-400">Loading…</div>;
  const known = new Set<string>(data.placeholders || []);
  const unknownIn = (t?: string) => [...new Set([...String(t || "").matchAll(/\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}/g)].map((m) => m[1]).filter((k) => !known.has(k)))];
  const mErrs = edit ? collect({
    subject: v.required(edit.subject, edit.channel === "email" ? "Subject" : "Title") || v.maxLen(edit.subject, 200, "Subject")
      || (unknownIn(edit.subject).length ? `Unknown placeholder: ${unknownIn(edit.subject).map((k) => `{{${k}}}`).join(", ")}` : null),
    body: v.required(edit.body, "Body") || v.maxLen(edit.body, 4000, "Body")
      || (unknownIn(edit.body).length ? `Unknown placeholder: ${unknownIn(edit.body).map((k) => `{{${k}}}`).join(", ")}` : null),
    whatsapp_template: edit.channel === "whatsapp" && edit.whatsapp_template && !/^[a-z0-9_]+$/.test(String(edit.whatsapp_template).trim()) ? "Template names use lowercase letters, digits and underscores only" : null,
  }) : {};
  const mer = (k: string) => (mShown || (k !== "whatsapp_template" && String(mErrs[k] || "").startsWith("Unknown placeholder")) ? mErrs[k] : undefined);
  const trySaveEdit = () => {
    setMShown(true);
    if (Object.keys(mErrs).length) return void toast.error(firstError(mErrs));
    save.mutate([{ event: edit!.event, channel: edit!.channel, enabled: edit!.enabled, subject: edit!.subject, body: edit!.body, whatsapp_template: edit!.whatsapp_template ? String(edit!.whatsapp_template).trim() : "" }]);
  };
  const ro = !data.can_edit;
  const key = (t: Row) => `${t.event}|${t.channel}`;
  const toggle = (t: Row) => { const n = { ...dirty, [key(t)]: { ...(dirty[key(t)] || { event: t.event, channel: t.channel }), enabled: !((dirty[key(t)]?.enabled) ?? t.enabled) } }; setDirty(n); };
  const rows = (data.templates as Row[]).map((t) => ({ ...t, enabled: dirty[key(t)]?.enabled ?? t.enabled }));
  const cols: Col<Row>[] = [
    { k: "label", label: "Event" }, { k: "channel", label: "Channel" },
    { k: "enabled", label: "On", render: (t) => <input type="checkbox" disabled={ro} checked={!!t.enabled} onChange={() => toggle(t)} onClick={(e) => e.stopPropagation()} />, csv: (t) => (t.enabled ? "on" : "off") },
    { k: "subject", label: "Subject / title", render: (t) => <span className="truncate inline-block max-w-[20rem]" title={t.subject}>{t.subject}</span> },
    { k: "whatsapp_template", label: "WhatsApp template", render: (t) => (t.channel === "whatsapp" ? t.whatsapp_template || <span className="text-amber-700">not set</span> : "") },
    { k: "is_default", label: "Source", render: (t) => (t.is_default ? "Default text" : "Edited"), csv: (t) => (t.is_default ? "default" : "edited") },
    { k: "_a", label: "", csv: () => "", render: (t) => <button className="btn-secondary !py-0.5 !px-2 text-[11px]" onClick={(e) => { e.stopPropagation(); setMShown(false); setEdit({ ...t }); }}>{ro ? "View" : "Edit"}</button> },
  ];
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        <Note tone={data.email?.configured ? "info" : "warn"}>{data.email?.message}</Note>
        <Note tone={data.whatsapp?.configured ? "info" : "warn"}>{data.whatsapp?.message}</Note>
      </div>
      <Grid rows={rows} cols={cols} keyOf={key} title="Candidate messages" exportName="candidate-messages.csv"
        actions={!ro && Object.keys(dirty).length ? <button className="btn-primary !py-1 !px-2.5 text-xs" onClick={() => save.mutate(Object.values(dirty))}>Save on/off changes</button> : undefined} />
      <p className="text-[11px] text-gray-400">Placeholders: {(data.placeholders || []).map((p: string) => `{{${p}}}`).join(" ")}. WhatsApp sends the approved template with two variables: the subject and the link. Candidates get a channel only if they agreed to it.</p>
      {edit && <Modal title={`${edit.label} · ${edit.channel}`} subtitle="What the candidate reads." size="lg" onClose={() => setEdit(null)}
        footer={<><button className="btn-secondary" onClick={() => setEdit(null)}>Close</button><button className="btn-secondary" onClick={() => preview.mutate({ subject: edit.subject, body: edit.body })}>Preview</button>{!ro && <button className="btn-primary" disabled={save.isPending} onClick={trySaveEdit}>Save</button>}</>}>
        <FormGrid cols={2}>
          <div className="sm:col-span-2"><Field label={<>{edit.channel === "email" ? "Subject" : "Title (variable 1)"}<Req /></>} hint={`${String(edit.subject || "").length} of 200 characters.`}><Input disabled={ro} maxLength={200} value={edit.subject || ""} onChange={(e) => setEdit({ ...edit, subject: e.target.value })} /><FieldError msg={mer("subject")} /></Field></div>
          {edit.channel === "whatsapp" && <Field label="Approved Meta template name" hint="Must match the approved template exactly: lowercase letters, digits and underscores. Leave empty to use the generic notification template from Integrations, WhatsApp."><Input disabled={ro} maxLength={120} value={edit.whatsapp_template || ""} onChange={(e) => setEdit({ ...edit, whatsapp_template: e.target.value })} /><FieldError msg={mer("whatsapp_template")} /></Field>}
          <Field label="Enabled"><Select disabled={ro} value={edit.enabled ? "1" : "0"} onChange={(e) => setEdit({ ...edit, enabled: e.target.value === "1" })}><option value="1">On</option><option value="0">Off</option></Select></Field>
          <div className="sm:col-span-2"><Field label={<>{edit.channel === "email" ? "Body" : "Body (used for the preview only; WhatsApp sends the approved template)"}<Req /></>} hint={`Use the placeholders listed under the table, e.g. {{first_name}}. ${String(edit.body || "").length} of 4000 characters.`}><Textarea rows={8} disabled={ro} maxLength={4000} value={edit.body || ""} onChange={(e) => setEdit({ ...edit, body: e.target.value })} /><FieldError msg={mer("body")} /></Field></div>
        </FormGrid>
        <div className="grid grid-cols-1 gap-3 mt-3">
          {preview.data && <div className="border border-surface-border rounded-lg p-3 text-xs bg-surface"><div className="font-semibold">{preview.data.subject}</div><pre className="whitespace-pre-wrap font-sans mt-1">{preview.data.body}</pre>{!!preview.data.unknown?.length && <div className="text-red-600 mt-1">Unknown placeholders: {preview.data.unknown.join(", ")}</div>}</div>}
        </div>
      </Modal>}
    </div>
  );
}

/** Settings block: Google Calendar / Microsoft 365 connections per org. */
export function CalendarSettings({ canEdit }: { canEdit: boolean }) {
  const qc = useQueryClient();
  const [sp, setSp] = useSearchParams();
  const { data } = useQuery({ queryKey: ["hire", "calendar"], queryFn: hiringApi.calendar });
  useEffect(() => {
    const prov = sp.get("calendar"), res = sp.get("result");
    if (prov && res) { res === "connected" ? toast.success(`${prov} calendar connected.`) : toast.error(`${prov}: ${sp.get("detail") || "could not connect"}`, { duration: 8000 }); sp.delete("calendar"); sp.delete("result"); sp.delete("detail"); setSp(sp, { replace: true }); qc.invalidateQueries({ queryKey: ["hire", "calendar"] }); }
  }, [sp, setSp, qc]);
  const connect = useMutation({ mutationFn: (p: string) => hiringApi.connectCalendar(p), onSuccess: (r) => { window.location.href = r.auth_url; }, onError: (e) => toast.error(err(e), { duration: 8000 }) });
  const disconnect = useMutation({ mutationFn: (p: string) => hiringApi.disconnectCalendar(p), onSuccess: () => { toast.success("Disconnected."); qc.invalidateQueries({ queryKey: ["hire", "calendar"] }); }, onError: (e) => toast.error(err(e)) });
  if (!data) return null;
  return (
    <div className="card mt-4">
      <div className="font-display font-semibold text-gray-800 mb-1">Interview calendars</div>
      <p className="text-xs text-gray-500 mb-3">{data.note}</p>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {(data.providers as Row[]).map((p) => (
          <div key={p.key} className="border border-surface-border rounded-lg p-3 text-sm">
            <div className="flex items-center justify-between gap-2"><span className="font-medium">{p.label}</span><Pill s={p.connection?.status === "connected" ? "active" : p.state === "configured" ? "pending" : "draft"} label={p.connection?.status === "connected" ? "Connected" : p.state === "configured" ? "Ready to connect" : "Not configured"} /></div>
            <div className="text-xs text-gray-500 mt-1">{p.connection?.status === "connected" ? `Organiser: ${p.connection.account_email || "(unknown)"} · connected by ${p.connection.connected_by_name || ""}` : p.message}</div>
            {p.connection?.last_error && <div className="text-xs text-red-600 mt-1">{p.connection.last_error}</div>}
            {p.state !== "configured" && <div className="text-[11px] text-gray-400 mt-1">Redirect URI to register in the OAuth app: <code>{p.redirect_uri}</code> · docs: <a className="underline" href={p.oauth_docs_url} target="_blank" rel="noreferrer">OAuth</a>, <a className="underline" href={p.docs_url} target="_blank" rel="noreferrer">events API</a></div>}
            {canEdit && <div className="flex gap-1 mt-2">
              {p.state === "configured" && <button className="btn-primary !py-1 !px-2.5 text-xs" disabled={connect.isPending} onClick={() => connect.mutate(p.key)}>{p.connection?.status === "connected" ? "Reconnect" : "Connect"}</button>}
              {p.connection?.status === "connected" && <button className="btn-secondary !py-1 !px-2.5 text-xs" onClick={() => disconnect.mutate(p.key)}>Disconnect</button>}</div>}
          </div>))}
      </div>
    </div>
  );
}
