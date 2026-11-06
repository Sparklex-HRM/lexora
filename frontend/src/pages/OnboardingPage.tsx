import { useState, ReactNode } from "react";
import { Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import {
  UserPlus, Plus, X, CheckCircle2, Loader2, ClipboardCheck, ThumbsUp, Ban, Copy, Send, Undo2,
  Eye, ListChecks, Trash2, ExternalLink, ArrowRight,
} from "lucide-react";
import { PageHeader, StatTile, Spinner } from "../components/ui";
import DataGrid, { GridColumn } from "../components/DataGrid";
import {
  onboardingApi, mastersApi, opsApi, OnboardingSession, SessionCreate, PAY_FREQUENCIES, EMPLOYMENT_TYPES, PAY_TYPES,
} from "../api/endpoints";
import {
  onboardingHrApi, employeeFormApi, ApproveResult, JoinerTask, TemplateItem, StoredDoc, errText, openBlob,
} from "../api/identity";
import { regionOptions, COUNTRY_NAME } from "../api/regions";
import { LinkBox } from "./UsersPage";
import { FormSection } from "../components/Modal";
import { v as chk, collect, firstError, FieldError, Req } from "../lib/validators";   // forms wrap-up 2026-10-24
import { todayISO } from "../lib/datetime";   // 2026-10-25: UTC store, org-zone today

const LABEL = "block text-[10px] font-semibold text-gray-400 uppercase tracking-wide mb-1";
const STATUS_TONE: Record<string, string> = {
  invited: "badge-amber", submitted: "badge-purple", approved: "badge-teal", activated: "badge-green", cancelled: "badge-red",
};
const STATUS_LABEL: Record<string, string> = {
  invited: "Waiting for joiner", submitted: "Ready to review", approved: "Approved — not finished",
  activated: "Joined", cancelled: "Cancelled",
};
type Row = OnboardingSession & Record<string, any>;

function Field({ label, children, wide, hint, req, err }: { label: string; children: ReactNode; wide?: boolean; hint?: string; req?: boolean; err?: string }) {
  return <div className={wide ? "sm:col-span-2" : ""}><label className={LABEL}>{label}{req && <Req />}</label>{children}
    <FieldError msg={err} />
    {hint && !err && <p className="text-[11px] text-gray-400 mt-1">{hint}</p>}</div>;
}
const GRID4 = "grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3";
/** yyyy-mm-dd + n months, minus a day (probation of 6 months from 1 Jan ends 30 Jun). */
function addMonths(iso: string, n: number): string {
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime()) || !n) return "";
  d.setMonth(d.getMonth() + n); d.setDate(d.getDate() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function Modal({ title, sub, children, onClose, footer }: { title: string; sub?: string; children: ReactNode; onClose: () => void; footer?: ReactNode }) {
  return (
    <div className="fixed inset-0 z-50 bg-black/40 backdrop-blur-sm flex items-center justify-center p-2 sm:p-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-[min(110rem,96vw)] max-h-[94vh] flex flex-col overflow-hidden">
        <div className="flex items-center justify-between gap-3 px-5 py-3.5 border-b border-surface-border bg-gradient-to-r from-primary-50 to-teal-50">
          <div className="min-w-0"><h2 className="font-display font-semibold text-gray-800 truncate">{title}</h2>
            {sub && <p className="text-[11px] text-gray-500 truncate">{sub}</p>}</div>
          <button onClick={onClose} className="icon-btn text-gray-400"><X className="h-4 w-4" /></button>
        </div>
        <div className="p-4 sm:p-5 overflow-y-auto flex-1">{children}</div>
        {footer && <div className="px-5 py-3 border-t border-surface-border bg-surface flex flex-wrap items-center justify-end gap-2">{footer}</div>}
      </div>
    </div>
  );
}

export default function OnboardingPage() {
  const qc = useQueryClient();
  const { data: sessions, isLoading } = useQuery({ queryKey: ["onboarding"], queryFn: () => onboardingApi.list() });
  const [creating, setCreating] = useState(false);
  const [reviewId, setReviewId] = useState<string | null>(null);
  const [template, setTemplate] = useState(false);
  const [fStatus, setFStatus] = useState("");
  const list = (sessions || []) as Row[];
  const count = (s: string) => list.filter((x) => x.status === s).length;
  const rows = list.filter((s) => !fStatus || s.status === fStatus);

  const columns: GridColumn<Row>[] = [
    { key: "name", label: "New hire", text: (s) => `${s.first_name} ${s.last_name}`,
      render: (s) => <div><div className="font-medium text-gray-800">{s.first_name} {s.last_name}</div>
        <div className="text-xs text-gray-400">{s.email || "No e-mail"}</div></div> },
    { key: "job_title", label: "Job title" },
    { key: "department", label: "Department" },
    { key: "country", label: "Country", text: (s) => COUNTRY_NAME[s.country] || s.country || "" },
    { key: "hire_date", label: "Joining date", type: "date" },
    { key: "status", label: "Status", text: (s) => STATUS_LABEL[s.status] || s.status,
      render: (s) => <span className={STATUS_TONE[s.status] || "badge-purple"}>{STATUS_LABEL[s.status] || s.status}</span> },
    { key: "progress_pct", label: "Form filled", type: "percent" },
    { key: "checklist", label: "Checklist", text: (s) => `${s.tasks_done ?? 0}/${s.tasks_total ?? 0}`,
      render: (s) => <span className="tabular-nums">{s.tasks_done ?? 0} of {s.tasks_total ?? 0} done</span> },
    { key: "employee_number", label: "Employee no." },
    { key: "actions", label: "Actions", sortable: false, text: () => "",
      render: (s) => <button className="btn-secondary !py-1 !px-2.5 text-xs" onClick={(e) => { e.stopPropagation(); setReviewId(s.id); }}>
        {s.status === "submitted" ? "Review" : "Open"}</button> },
  ];

  return (
    <div className="space-y-4">
      <PageHeader title="Onboarding" icon={UserPlus}
        subtitle="Invite a new hire. They fill their own details on their phone. You review and approve."
        actions={<div className="flex items-center gap-2">
          <button className="btn-secondary flex items-center gap-1.5" onClick={() => setTemplate(true)}><ListChecks className="h-4 w-4" /> Checklist</button>
          <button className="btn-primary flex items-center gap-1.5" onClick={() => setCreating(true)}><Plus className="h-4 w-4" /> New hire</button>
        </div>} />

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatTile label="Waiting for joiner" value={count("invited")} icon={Loader2} />
        <StatTile label="Ready to review" value={count("submitted")} icon={ClipboardCheck} accent />
        <StatTile label="Joined" value={count("activated")} icon={CheckCircle2} />
        <StatTile label="Cancelled" value={count("cancelled")} icon={Ban} />
      </div>

      {isLoading ? <div className="flex justify-center py-16"><Spinner /></div> : (
        <DataGrid<Row> columns={columns} rows={rows} rowKey={(s) => s.id} exportName="onboarding" minWidth={1100}
          searchPlaceholder="Search name, title, department…" empty="No new hires yet. Start one with New hire."
          onRowClick={(s) => setReviewId(s.id)}
          filters={<select className="input !py-1.5 !w-auto text-sm" value={fStatus} onChange={(e) => setFStatus(e.target.value)}>
            <option value="">All statuses</option>{Object.entries(STATUS_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>} />
      )}

      {creating && <NewHireModal onClose={() => setCreating(false)} onCreated={(id) => { setCreating(false); setReviewId(id); }} />}
      {reviewId && <ReviewModal id={reviewId} onClose={() => { setReviewId(null); qc.invalidateQueries({ queryKey: ["onboarding"] }); }} />}
      {template && <TemplateModal onClose={() => setTemplate(false)} />}
    </div>
  );
}

/* ─────────── HR: job + pay, then the joiner gets a link ─────────── */
function NewHireModal({ onClose, onCreated }: { onClose: () => void; onCreated: (id: string) => void }) {
  const qc = useQueryClient();
  const { data: opts } = useQuery({ queryKey: ["employees", "form-options"], queryFn: employeeFormApi.options, staleTime: 0 });
  const { data: depts } = useQuery({ queryKey: ["m", "departments"], queryFn: () => mastersApi.list("departments") });
  const { data: desigs } = useQuery({ queryKey: ["m", "designations"], queryFn: () => mastersApi.list("designations") });
  const { data: locs } = useQuery({ queryKey: ["m", "locations"], queryFn: () => mastersApi.list("locations") });
  const { data: branches } = useQuery({ queryKey: ["ops", "branches"], queryFn: () => opsApi.list("branches") });
  const [f, setF] = useState<Record<string, any> | null>(null);
  const [errs, setErrs] = useState<Record<string, string>>({});
  if (!f && opts) setF({
    first_name: "", last_name: "", email: "", work_email: "", employee_number: "", job_title: "", department: "", work_location: "",
    manager_id: "", grade: "", address_country: "CA", province_of_employment: opts.org_region || "",
    hire_date: todayISO(), employment_type: "full_time", pay_type: "salary",
    annual_salary: "", hourly_rate: "", pay_frequency: "biweekly", standard_hours_per_week: 40,
    branch_id: "", branch_name: "", division: "", cost_center: "", probation_end_date: "", overtime_eligible: true, invite_expiry_days: 14,
  });
  const set = (k: string, v: any) => setF((p) => ({ ...(p || {}), [k]: v }));
  const v = f || {};
  const create = useMutation({
    mutationFn: () => {
      const body: Record<string, any> = {};
      for (const [k, x] of Object.entries(v)) if (x !== "" && x !== null && x !== undefined) body[k] = x;
      for (const k of ["annual_salary", "hourly_rate", "standard_hours_per_week", "invite_expiry_days"]) if (body[k] !== undefined) body[k] = Number(body[k]);
      if (body.pay_type === "hourly") delete body.annual_salary; else delete body.hourly_rate;
      const m = (opts?.managers || []).find((x) => x.id === body.manager_id);
      if (m) body.manager_name = m.name;
      return onboardingApi.create(body as SessionCreate);
    },
    onSuccess: (s) => { toast.success("New hire added. Send them the link."); qc.invalidateQueries({ queryKey: ["onboarding"] }); onCreated(s.id); },
    onError: (e: any) => toast.error(errText(e, "Could not add the new hire")) });
  const inp = (k: string, props: Record<string, any> = {}) => <input {...props} className="input" value={v[k] ?? ""} onChange={(e) => set(k, e.target.value)} />;
  const submit = () => {
    const found = collect({
      first_name: chk.required(v.first_name, "First name") || chk.maxLen(v.first_name, 100, "First name"),
      last_name: chk.required(v.last_name, "Last name") || chk.maxLen(v.last_name, 100, "Last name"),
      email: chk.email(v.email), work_email: chk.email(v.work_email),
      province_of_employment: v.province_of_employment ? null : "Choose the province of employment",
      hire_date: chk.required(v.hire_date, "Joining date"),
      probation_end_date: chk.dateOrder(v.hire_date, v.probation_end_date, "Probation cannot end before the joining date"),
      annual_salary: v.pay_type === "salary" ? chk.range(v.annual_salary, 0, undefined, "Yearly pay") : null,
      hourly_rate: v.pay_type === "hourly" ? chk.range(v.hourly_rate, 0, undefined, "Hourly rate") : null,
      standard_hours_per_week: chk.range(v.standard_hours_per_week, 0, 168, "Hours per week"),
      invite_expiry_days: chk.range(v.invite_expiry_days, 1, 90, "Link valid for"),
      employee_number: chk.maxLen(v.employee_number, 30, "Employee number"),
    });
    setErrs(found);
    if (Object.keys(found).length) return toast.error(firstError(found));
    create.mutate();
  };

  return (
    <Modal title="New hire" sub="You fill the job and pay. The new hire fills the personal, ID and bank details from their link." onClose={onClose}
      footer={<>
        <button className="btn-secondary text-xs" onClick={onClose}>Cancel</button>
        <button className="btn-primary text-xs flex items-center gap-1.5 disabled:opacity-60" disabled={create.isPending || !f} onClick={submit}>
          {create.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ArrowRight className="h-3.5 w-3.5" />} Add and get the link</button>
      </>}>
      {!f ? <div className="flex justify-center py-12"><Spinner /></div> : (
        <div>
        <FormSection title="Person">
        <div className={GRID4}>
          <Field label="First name" req err={errs.first_name}>{inp("first_name", { maxLength: 100 })}</Field>
          <Field label="Last name" req err={errs.last_name}>{inp("last_name", { maxLength: 100 })}</Field>
          <Field label="Personal e-mail" err={errs.email} hint="The joining link is sent here. Without it, copy the link and send it yourself.">{inp("email", { type: "email" })}</Field>
          <Field label="Work e-mail" err={errs.work_email} hint="Official mailbox, if already created. Used for the login after approval.">{inp("work_email", { type: "email" })}</Field>
          <Field label="Employee number" err={errs.employee_number} hint={`Leave empty to use the next number (${opts?.next_number || "…"}) on approval.`}>{inp("employee_number", { maxLength: 30 })}</Field>
          <Field label="Link valid for (days)" err={errs.invite_expiry_days} hint="1 to 90 days. A new link can be sent later.">{inp("invite_expiry_days", { type: "number", min: 1, max: 90 })}</Field>
        </div>
        </FormSection>
        <FormSection title="Job">
        <div className={GRID4}>
          <Field label="Work country" hint="Lexora HRM runs Canadian payroll only.">
            <input className="input bg-surface" value={COUNTRY_NAME.CA} readOnly /></Field>
          <Field label="Province of employment" req err={errs.province_of_employment} hint="Decides provincial tax, employment standards and stat holidays.">
            <select className="input" value={v.province_of_employment} onChange={(e) => set("province_of_employment", e.target.value)}>
              <option value="">Choose…</option>{regionOptions().map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
            </select></Field>
          <Field label="Joining date" req err={errs.hire_date}>{inp("hire_date", { type: "date" })}</Field>
          <Field label="Employment type"><select className="input capitalize" value={v.employment_type} onChange={(e) => set("employment_type", e.target.value)}>
            {EMPLOYMENT_TYPES.map((t) => <option key={t} value={t}>{t.replace("_", " ")}</option>)}</select></Field>
          <Field label="Job title"><input className="input" list="oh-desigs" value={v.job_title} onChange={(e) => set("job_title", e.target.value)} placeholder="Choose or type…" />
            <datalist id="oh-desigs">{(desigs || []).map((o) => <option key={o.id} value={o.name} />)}</datalist></Field>
          <Field label="Department"><input className="input" list="oh-depts" value={v.department} onChange={(e) => set("department", e.target.value)} placeholder="Choose or type…" />
            <datalist id="oh-depts">{(depts || []).map((o) => <option key={o.id} value={o.name} />)}</datalist></Field>
          <Field label="Work location"><input className="input" list="oh-locs" value={v.work_location} onChange={(e) => set("work_location", e.target.value)} placeholder="Choose or type…" />
            <datalist id="oh-locs">{(locs || []).map((o) => <option key={o.id} value={o.name} />)}</datalist></Field>
          <Field label="Branch"><select className="input" value={v.branch_id} onChange={(e) => {
            const b = (branches || []).find((x: any) => String(x.id) === e.target.value);
            set("branch_id", e.target.value); set("branch_name", b?.name || ""); }}>
            <option value="">— No branch —</option>
            {(branches || []).map((b: any) => <option key={b.id} value={b.id}>{b.name}{b.is_hq ? " (HQ)" : ""}</option>)}</select></Field>
          <Field label="Grade"><select className="input" value={v.grade} onChange={(e) => set("grade", e.target.value)}>
            <option value="">—</option>{(opts?.grades || []).map((g) => <option key={g.name} value={g.name}>{g.name}</option>)}</select></Field>
          <Field label="Division">{inp("division", { maxLength: 120 })}</Field>
          <Field label="Cost centre" hint="Salary cost is posted here in the books.">{inp("cost_center", { maxLength: 60 })}</Field>
          <Field label="Probation" err={errs.probation_end_date} hint={v.probation_end_date ? `Ends ${v.probation_end_date}. Confirmation is tracked in People → Probation.` : "Blank = the company's default probation rule applies."}>
            <div className="flex gap-2">
              <select className="input !w-28 shrink-0" value="" onChange={(e) => e.target.value && set("probation_end_date", addMonths(v.hire_date, Number(e.target.value)))}>
                <option value="">Months…</option>{[3, 6, 9, 12].map((m) => <option key={m} value={m}>{m} months</option>)}</select>
              {inp("probation_end_date", { type: "date", min: v.hire_date || undefined })}
            </div></Field>
          <Field label="Reports to" wide><select className="input" value={v.manager_id} onChange={(e) => set("manager_id", e.target.value)}>
            <option value="">— No manager —</option>
            {(opts?.managers || []).map((m) => <option key={m.id} value={m.id}>{m.name}{m.employee_number ? ` · ${m.employee_number}` : ""}{m.job_title ? ` · ${m.job_title}` : ""}</option>)}</select></Field>
        </div>
        </FormSection>
        <FormSection title="Pay">
        <div className={GRID4}>
          <Field label="Pay type"><select className="input capitalize" value={v.pay_type} onChange={(e) => set("pay_type", e.target.value)}>
            {PAY_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}</select></Field>
          {v.pay_type === "salary"
            ? <Field label="Yearly pay ($)" err={errs.annual_salary}>{inp("annual_salary", { type: "number", min: 0 })}</Field>
            : <Field label="Hourly rate ($)" err={errs.hourly_rate}>{inp("hourly_rate", { type: "number", min: 0, step: "0.25" })}</Field>}
          <Field label="Pay frequency"><select className="input" value={v.pay_frequency} onChange={(e) => set("pay_frequency", e.target.value)}>
            {PAY_FREQUENCIES.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}</select></Field>
          <Field label="Hours per week" err={errs.standard_hours_per_week}>{inp("standard_hours_per_week", { type: "number", min: 0, max: 168, step: "0.5" })}</Field>
          <label className="flex items-center gap-2 text-xs text-gray-600 sm:pt-5">
            <input type="checkbox" checked={v.overtime_eligible !== false} onChange={(e) => set("overtime_eligible", e.target.checked)} /> Eligible for overtime pay</label>
        </div>
        </FormSection>
        </div>
      )}
    </Modal>
  );
}

/* ─────────── HR: one joiner — link, details, documents, checklist, approve ─────────── */
const SHOW: [string, string][] = [
  ["date_of_birth", "Date of birth"], ["gender", "Gender"],
  ["phone", "Mobile"], ["email", "Personal e-mail"], ["address_line1", "Address"], ["address_line2", "Address line 2"],
  ["city", "City"], ["postal_code", "Postal code"],
  ["sin_masked", "SIN"], ["work_eligibility", "Work eligibility"],
  ["td1_federal_claim", "TD1 federal claim"], ["td1_provincial_claim", "TD1 provincial claim"],
  ["bank_account_masked", "Bank account"], ["bank_institution", "Institution no."], ["bank_transit", "Transit no."],
  ["emergency_name", "Emergency contact"], ["emergency_relationship", "Relationship"], ["emergency_phone", "Emergency phone"],
  ["nominee_name", "Nominee"],
];
const JOB: [string, string][] = [
  ["job_title", "Job title"], ["department", "Department"], ["work_location", "Work location"], ["grade", "Grade"],
  ["manager_name", "Reports to"], ["hire_date", "Joining date"], ["employment_type", "Employment type"],
  ["province_of_employment", "State / province"], ["pay_type", "Pay type"], ["annual_salary", "Yearly pay"],
  ["hourly_rate", "Hourly rate"], ["pay_frequency", "Pay frequency"],
];

function ReviewModal({ id, onClose }: { id: string; onClose: () => void }) {
  const qc = useQueryClient();
  const key = ["onboarding", id];
  const { data: s, isLoading } = useQuery({ queryKey: key, queryFn: () => onboardingApi.get(id) as Promise<Row>, staleTime: 0 });
  const { data: docs } = useQuery({ queryKey: [...key, "docs"], queryFn: () => onboardingHrApi.docs(id) });
  const { data: consents } = useQuery({ queryKey: [...key, "consents"], queryFn: () => onboardingApi.consents(id) });
  const { data: tasks } = useQuery({ queryKey: [...key, "tasks"], queryFn: () => onboardingHrApi.tasks(id) });
  const [result, setResult] = useState<ApproveResult | null>(null);
  const [note, setNote] = useState("");
  const [sendingBack, setSendingBack] = useState(false);
  const refresh = () => { qc.invalidateQueries({ queryKey: key }); qc.invalidateQueries({ queryKey: ["onboarding"] }); };
  const fail = (m: string) => (e: any) => toast.error(errText(e, m));

  const approve = useMutation({ mutationFn: () => onboardingHrApi.approve(id),
    onSuccess: (r) => { setResult(r); refresh(); qc.invalidateQueries({ queryKey: ["employees"] }); qc.invalidateQueries({ queryKey: ["logins"] }); },
    onError: fail("Could not approve") });
  const resend = useMutation({ mutationFn: () => onboardingHrApi.resend(id),
    onSuccess: (r) => { refresh(); if (r.email_sent) toast.success("New link sent by e-mail"); else toast.error(r.invite_link ? "E-mail not sent (test set-up). Copy the link below." : "The e-mail could not be sent. Set up e-mail, then press New link again."); }, onError: fail("Could not send a new link") });
  const back = useMutation({ mutationFn: () => onboardingHrApi.sendBack(id, note),
    onSuccess: () => { toast.success("Sent back to the joiner"); setSendingBack(false); refresh(); }, onError: fail("Could not send it back") });
  const cancel = useMutation({ mutationFn: () => onboardingApi.cancel(id), onSuccess: () => { toast("Cancelled"); refresh(); onClose(); } });
  const copy = async (text: string) => { try { await navigator.clipboard.writeText(text); toast.success("Link copied"); } catch { toast.error("Could not copy"); } };

  if (isLoading || !s) return <Modal title="Loading…" onClose={onClose}><div className="flex justify-center py-12"><Spinner /></div></Modal>;
  const d = s.data || {};
  const open = s.status === "invited" || s.status === "submitted" || s.status === "approved";
  const show = (pairs: [string, string][]) => pairs.filter(([k]) => d[k] !== undefined && d[k] !== null && d[k] !== "");

  return (
    <Modal title={`${s.first_name} ${s.last_name}`} onClose={onClose}
      sub={`${STATUS_LABEL[s.status] || s.status} · ${COUNTRY_NAME[s.country] || s.country || "Country not set"}${s.job_title ? ` · ${s.job_title}` : ""}`}
      footer={open ? <>
        <button className="btn-secondary text-xs flex items-center gap-1.5 !text-red-600 mr-auto" onClick={() => { if (window.confirm("Cancel this onboarding? The link stops working.")) cancel.mutate(); }}><Ban className="h-3.5 w-3.5" /> Cancel onboarding</button>
        {s.status === "submitted" && <button className="btn-secondary text-xs flex items-center gap-1.5" onClick={() => setSendingBack(true)}><Undo2 className="h-3.5 w-3.5" /> Send back to joiner</button>}
        <button className="btn-primary text-xs flex items-center gap-1.5 disabled:opacity-60" disabled={approve.isPending}
          onClick={() => { if (s.status === "submitted" || window.confirm("The joiner has not submitted the form yet. Approve anyway?")) approve.mutate(); }}>
          {approve.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ThumbsUp className="h-3.5 w-3.5" />} Approve and make employee</button>
      </> : <button className="btn-primary text-xs" onClick={onClose}>Close</button>}>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <div className="xl:col-span-2 space-y-4">
          {!s.invite_link && (s.status === "invited" || s.status === "submitted") && (
            <Section title="Joiner's link">
              <p className="text-xs text-gray-500 mb-2">The personal link goes only to the joiner's e-mail{s.email ? ` (${s.email})` : ""}. For safety it is not shown here. {s.invite_expires_at && `It works until ${new Date(s.invite_expires_at).toLocaleDateString()}.`}</p>
              <button className="btn-secondary text-xs flex items-center justify-center gap-1.5" onClick={() => resend.mutate()}><Send className="h-3.5 w-3.5" /> Send a new link</button>
            </Section>
          )}
          {s.invite_link && (
            <Section title="Joiner's link (test set-up only)">
              <p className="text-xs text-gray-500 mb-2">Personal to this joiner. They can fill the form on a phone. {s.invite_expires_at && `Works until ${new Date(s.invite_expires_at).toLocaleDateString()}.`}</p>
              <div className="flex flex-col sm:flex-row gap-2">
                <input className="input font-mono text-xs flex-1" readOnly value={s.invite_link} onFocus={(e) => e.target.select()} />
                <button className="btn-secondary text-xs flex items-center justify-center gap-1.5" onClick={() => copy(s.invite_link)}><Copy className="h-3.5 w-3.5" /> Copy</button>
                <a className="btn-secondary text-xs flex items-center justify-center gap-1.5" href={s.invite_link} target="_blank" rel="noreferrer"><ExternalLink className="h-3.5 w-3.5" /> Open</a>
                <button className="btn-secondary text-xs flex items-center justify-center gap-1.5" onClick={() => resend.mutate()}><Send className="h-3.5 w-3.5" /> New link</button>
              </div>
            </Section>
          )}
          {s.missing?.length > 0 && s.status !== "activated" && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 text-amber-800 text-sm p-3">Not filled yet: {s.missing.join(", ")}.</div>
          )}
          <Section title="Job and pay (entered by HR)"><Grid pairs={show(JOB)} d={d} /></Section>
          <Section title="Filled by the joiner">
            {show(SHOW).length ? <Grid pairs={show(SHOW)} d={d} /> : <p className="text-sm text-gray-400">Nothing filled yet.</p>}
            <p className="text-[11px] text-gray-400 mt-2">ID and bank numbers are stored encrypted and shown masked.</p>
          </Section>
          <Section title={`Documents (${docs?.length || 0})`}><DocTable id={id} docs={docs || []} onChange={() => qc.invalidateQueries({ queryKey: [...key, "docs"] })} /></Section>
          <Section title={`Agreements ticked (${consents?.length || 0})`}>
            {!(consents || []).length ? <p className="text-sm text-gray-400">None yet.</p> : (
              <div className="flex flex-wrap gap-1.5">{(consents || []).map((c: any) => (
                <span key={c.id} className="badge-green" title={c.consented_at ? new Date(c.consented_at).toLocaleString() : ""}>{String(c.consent_type).replace(/_/g, " ")}</span>))}</div>
            )}
          </Section>
        </div>
        <div><Section title="Joining checklist"><Checklist id={id} tasks={tasks || []} onChange={() => { qc.invalidateQueries({ queryKey: [...key, "tasks"] }); qc.invalidateQueries({ queryKey: ["onboarding"] }); }} /></Section></div>
      </div>

      {sendingBack && (
        <Modal title="Send back to the joiner" sub="The link opens again so they can correct the form." onClose={() => setSendingBack(false)}
          footer={<><button className="btn-secondary text-xs" onClick={() => setSendingBack(false)}>Cancel</button>
            <button className="btn-primary text-xs disabled:opacity-60" disabled={back.isPending} onClick={() => back.mutate()}>Send back</button></>}>
          <label className={LABEL}>What should they correct? (shown to the joiner)</label>
          <textarea className="input min-h-[96px]" value={note} autoFocus onChange={(e) => setNote(e.target.value)} placeholder="e.g. The transit number does not match the void cheque." />
        </Modal>
      )}
      {result && <ApprovedBox result={result} onClose={() => { setResult(null); onClose(); }} />}
    </Modal>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return <div className="rounded-xl border border-surface-border p-3 sm:p-4">
    <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-2.5">{title}</h3>{children}</div>;
}
function Grid({ pairs, d }: { pairs: [string, string][]; d: Record<string, any> }) {
  return <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-x-6 gap-y-1.5 text-sm">
    {pairs.map(([k, label]) => <div key={k} className="flex justify-between gap-3 border-b border-surface-border/60 py-1">
      <span className="text-gray-400">{label}</span><span className="text-gray-800 font-medium text-right break-all">{String(d[k]).replace(/_/g, " ")}</span></div>)}
  </div>;
}

function DocTable({ id, docs, onChange }: { id: string; docs: StoredDoc[]; onChange: () => void }) {
  if (!docs.length) return <p className="text-sm text-gray-400">No documents uploaded yet.</p>;
  const open = async (d: StoredDoc) => { try { openBlob(await onboardingHrApi.downloadDoc(id, d.id)); } catch (e) { toast.error(errText(e, "Could not open the file")); } };
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm min-w-[520px]">
        <thead className="text-left text-[11px] uppercase tracking-wide text-gray-400"><tr>
          <th className="py-1.5 pr-3">Type</th><th className="py-1.5 pr-3">File</th><th className="py-1.5 pr-3">Size</th><th className="py-1.5 pr-3">Checked</th><th className="py-1.5 text-right">Actions</th></tr></thead>
        <tbody>{docs.map((d) => (
          <tr key={d.id} className="border-t border-surface-border">
            <td className="py-1.5 pr-3 capitalize">{d.doc_type.replace(/_/g, " ")}</td>
            <td className="py-1.5 pr-3 text-gray-700">{d.file_name}</td>
            <td className="py-1.5 pr-3 tabular-nums">{Math.max(1, Math.round(d.size_bytes / 1024))} KB</td>
            <td className="py-1.5 pr-3">{d.verified ? <span className="badge-green">Checked</span> : <span className="badge-amber">Not yet</span>}</td>
            <td className="py-1.5"><div className="flex justify-end gap-1">
              <button className="icon-btn text-gray-400 hover:text-primary-600" title="Open" onClick={() => open(d)}><Eye className="h-4 w-4" /></button>
              <button className="icon-btn text-gray-400 hover:text-green-600" title={d.verified ? "Mark as not checked" : "Mark as checked"}
                onClick={async () => { await onboardingHrApi.verifyDoc(id, d.id, !d.verified); onChange(); }}><CheckCircle2 className="h-4 w-4" /></button>
            </div></td>
          </tr>))}</tbody>
      </table>
    </div>
  );
}

function Checklist({ id, tasks, onChange }: { id: string; tasks: JoinerTask[]; onChange: () => void }) {
  const [title, setTitle] = useState(""); const [owner, setOwner] = useState(""); const [due, setDue] = useState("");
  const upd = async (t: JoinerTask, body: Partial<JoinerTask>) => { try { await onboardingHrApi.updateTask(t.id, body); onChange(); } catch (e) { toast.error(errText(e, "Could not save")); } };
  const add = async () => {
    if (!title.trim()) return;
    try { await onboardingHrApi.addTask(id, { title: title.trim(), owner: owner.trim() || undefined, due_date: due || undefined }); setTitle(""); setOwner(""); setDue(""); onChange(); }
    catch (e) { toast.error(errText(e, "Could not add the task")); }
  };
  const done = tasks.filter((t) => t.done).length;
  return (
    <div className="space-y-2">
      <p className="text-xs text-gray-500">{done} of {tasks.length} done</p>
      {tasks.map((t) => (
        <div key={t.id} className={`rounded-lg border p-2.5 ${t.done ? "border-teal-200 bg-teal-50/40" : t.overdue ? "border-red-200 bg-red-50/40" : "border-surface-border"}`}>
          <label className="flex items-start gap-2 cursor-pointer">
            <input type="checkbox" className="mt-0.5" checked={t.done} onChange={(e) => upd(t, { done: e.target.checked })} />
            <span className={`text-sm flex-1 ${t.done ? "line-through text-gray-400" : "text-gray-800"}`}>{t.title}</span>
            <button className="text-gray-300 hover:text-red-500" title="Remove" onClick={async (e) => { e.preventDefault(); await onboardingHrApi.removeTask(t.id); onChange(); }}><Trash2 className="h-3.5 w-3.5" /></button>
          </label>
          <div className="grid grid-cols-2 gap-2 mt-2 pl-6">
            <input className="input !py-1 text-xs" defaultValue={t.owner || ""} placeholder="Owner" onBlur={(e) => e.target.value !== (t.owner || "") && upd(t, { owner: e.target.value })} />
            <input className="input !py-1 text-xs" type="date" defaultValue={t.due_date || ""} onBlur={(e) => e.target.value !== (t.due_date || "") && upd(t, { due_date: e.target.value })} />
          </div>
          {t.done && t.done_by && <p className="text-[11px] text-gray-400 mt-1 pl-6">Done by {t.done_by}</p>}
          {t.overdue && <p className="text-[11px] text-red-500 mt-1 pl-6">Past the due date</p>}
        </div>
      ))}
      <div className="rounded-lg border border-dashed border-surface-border p-2.5 space-y-2">
        <input className="input !py-1.5 text-sm" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Add a task, e.g. Safety shoes issued" />
        <div className="grid grid-cols-2 gap-2">
          <input className="input !py-1 text-xs" value={owner} onChange={(e) => setOwner(e.target.value)} placeholder="Owner" />
          <input className="input !py-1 text-xs" type="date" value={due} onChange={(e) => setDue(e.target.value)} />
        </div>
        <button className="btn-secondary text-xs w-full disabled:opacity-60" disabled={!title.trim()} onClick={add}>Add task</button>
      </div>
    </div>
  );
}

function ApprovedBox({ result, onClose }: { result: ApproveResult; onClose: () => void }) {
  const l = result.login || {};
  if (l.link) return <LinkBox title={`${result.employee?.first_name} is now an employee — ${result.employee?.employee_number}`}
    res={{ email: l.email || "", email_sent: false, link: l.link }} onClose={onClose} />;
  return (
    <Modal title="Approved" onClose={onClose} footer={<>
      <Link to={`/employees?q=${encodeURIComponent(result.employee?.employee_number || "")}`} className="btn-secondary text-xs">Open employee</Link>
      <button className="btn-primary text-xs" onClick={onClose}>Done</button></>}>
      <p className="text-sm text-gray-800"><b>{result.employee?.first_name} {result.employee?.last_name}</b> is now an active employee. Employee number <b>{result.employee?.employee_number}</b>.</p>
      <p className="text-sm text-gray-600 mt-2">
        {l.created || l.linked
          ? (l.email_sent ? `A login was created. The e-mail to set a password went to ${l.email}.`
            : l.invited ? "A login was created, but the e-mail could not be sent. Set up e-mail, then press Resend invite in Users & Logins." : "A login was created.")
          : `No login was created: ${l.reason || "create it from Users & Logins"}.`}
      </p>
    </Modal>
  );
}

/* ─────────── the org's checklist template ─────────── */
function TemplateModal({ onClose }: { onClose: () => void }) {
  const { data } = useQuery({ queryKey: ["onboarding", "template"], queryFn: onboardingHrApi.template, staleTime: 0 });
  const [items, setItems] = useState<TemplateItem[] | null>(null);
  if (!items && data) setItems(data.map((x) => ({ ...x })));
  const save = useMutation({ mutationFn: () => onboardingHrApi.saveTemplate((items || []).filter((i) => i.title.trim())),
    onSuccess: () => { toast.success("Checklist saved. New hires from now on get this list."); onClose(); },
    onError: (e: any) => toast.error(errText(e, "Could not save the checklist")) });
  const upd = (i: number, k: keyof TemplateItem, v: any) => setItems((p) => (p || []).map((x, n) => (n === i ? { ...x, [k]: v } : x)));
  const [errs, setErrs] = useState<Record<string, string>>({});
  const submit = () => {
    const rows = items || [];
    const checks: Record<string, string | null> = {
      count: rows.filter((r) => r.title.trim()).length > 60 ? "A checklist can have 60 lines at most" : null,
    };
    rows.forEach((r, i) => {
      if (!r.title.trim()) return;                      // blank lines are dropped on save
      checks[`title${i}`] = chk.maxLen(r.title.trim(), 200, `Line ${i + 1} task`);
      checks[`owner${i}`] = chk.maxLen((r.owner || "").trim(), 120, `Line ${i + 1} owner`);
      checks[`due${i}`] = !Number.isInteger(Number(r.due_offset_days)) ? `Line ${i + 1}: due days must be a whole number`
        : chk.range(r.due_offset_days, -60, 365, `Line ${i + 1} due days`);
    });
    const found = collect(checks);
    setErrs(found);
    if (Object.keys(found).length) return toast.error(firstError(found));
    save.mutate();
  };
  return (
    <Modal title="Joining checklist" sub="Every new hire starts with these tasks. Due dates are counted from the joining date." onClose={onClose}
      footer={<><button className="btn-secondary text-xs" onClick={onClose}>Cancel</button>
        <button className="btn-primary text-xs disabled:opacity-60" disabled={!items || save.isPending} onClick={submit}>Save checklist</button></>}>
      {!items ? <div className="flex justify-center py-10"><Spinner /></div> : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm min-w-[640px]">
            <thead className="text-left text-[11px] uppercase tracking-wide text-gray-400"><tr>
              <th className="py-1.5 pr-2">Task<Req /></th><th className="py-1.5 pr-2 w-48">Owner</th><th className="py-1.5 pr-2 w-44">Due (days from joining)</th><th className="w-10" /></tr></thead>
            <tbody>{items.map((it, i) => (
              <tr key={i} className="align-top">
                <td className="py-1 pr-2"><input className="input !py-1.5" maxLength={200} value={it.title} onChange={(e) => upd(i, "title", e.target.value)} placeholder="e.g. Signed TD1 forms collected" /><FieldError msg={errs[`title${i}`]} /></td>
                <td className="py-1 pr-2"><input className="input !py-1.5" list="ob-owners" maxLength={120} value={it.owner || ""} onChange={(e) => upd(i, "owner", e.target.value)} placeholder="IT, Admin, HR…" /><FieldError msg={errs[`owner${i}`]} /></td>
                <td className="py-1 pr-2"><input className="input !py-1.5" type="number" step={1} min={-60} max={365} value={it.due_offset_days} onChange={(e) => upd(i, "due_offset_days", Number(e.target.value))} /><FieldError msg={errs[`due${i}`]} /></td>
                <td><button className="icon-btn text-gray-300 hover:text-red-500" title="Remove" onClick={() => setItems(items.filter((_, n) => n !== i))}><Trash2 className="h-4 w-4" /></button></td>
              </tr>))}</tbody>
          </table>
          <datalist id="ob-owners">{["HR", "IT", "Admin", "Payroll", "Finance", "Manager", "Security"].map((o) => <option key={o} value={o} />)}</datalist>
          <button className="btn-secondary text-xs mt-3 flex items-center gap-1.5" disabled={items.length >= 60} onClick={() => setItems([...items, { title: "", owner: "", due_offset_days: 0 }])}><Plus className="h-3.5 w-3.5" /> Add line</button>
          <p className="text-[11px] text-gray-400 mt-2">Use a minus number for something due before the joining date (for example -3). Up to 60 lines; blank lines are dropped. Changes apply to new hires started from now on.</p>
        </div>
      )}
    </Modal>
  );
}
