import { useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import toast from "react-hot-toast";
import { TrendingUp, Plus, Users, Upload, Download, RefreshCw, X } from "lucide-react";
import { PageHeader } from "../../components/ui";
import { useAuthStore } from "../../store/authStore";
import { revisionsApi, lettersApi, openPdf, errText, cur, fmtDate, pct, REASONS } from "../../api/lifecycle";
import { FormGrid, FormSection } from "../../components/Modal";
import { v, collect, firstError, FieldError } from "../../lib/validators";
import { Grid, Modal, Field, Input, Select, Textarea, Status, Stat, useEmployees, empLabel, downloadBlob, today } from "../common/_shared";
import { useUrlTab } from "../../search/useUrlTab";

/* Salary revisions (hikes): single + bulk, approval, apply on the effective date.
   Only an APPROVED revision is ever applied. A back-dated date becomes a retro-pay proposal. History is never edited — correct with a new revision. */

type Tab = "revisions" | "batches" | "retro" | "settings";
const MODES = [["new_annual", "New salary"], ["percent", "% hike"], ["amount", "Amount hike"]];
const unit = (r: any) => (r.pay_type === "hourly" ? " / hour" : " / year");

export default function SalaryRevisionsPage() {
  const qc = useQueryClient();
  const role = useAuthStore((s) => s.user?.role) || "";
  /* Deep link from the employee profile: /salary-revisions?employee=<id> opens the new-revision
     form with that person picked and narrows the grid to their revisions until the chip is closed. */
  const [params] = useSearchParams();
  const linkedEmployee = params.get("employee") || "";
  const [onlyEmployee, setOnlyEmployee] = useState(linkedEmployee);
  const employees = useEmployees();
  const onlyEmp: any = onlyEmployee ? employees.find((e: any) => e.id === onlyEmployee) : null;
  const [tab, setTab] = useUrlTab<Tab>("revisions");   // ?tab= (global search deep link)
  const [status, setStatus] = useState("");
  const [form, setForm] = useState<any | null>(linkedEmployee ? { employee_id: linkedEmployee } : null);
  const [view, setView] = useState<any | null>(null);
  const [bulk, setBulk] = useState(false);
  const [batchId, setBatchId] = useState<string | null>(null);
  const { data: rows = [], isLoading } = useQuery({ queryKey: ["revisions"], queryFn: () => revisionsApi.list() });
  const { data: batches = [] } = useQuery({ queryKey: ["revision-batches"], queryFn: () => revisionsApi.batches() });
  const { data: retro = [] } = useQuery({ queryKey: ["retro-proposals"], queryFn: () => revisionsApi.retro() });
  const refresh = () => { ["revisions", "revision-batches", "retro-proposals"].forEach((k) => qc.invalidateQueries({ queryKey: [k] })); };
  const applyDue = useMutation({ mutationFn: () => revisionsApi.applyDue(), onSuccess: (r: any) => { toast.success(`${r.applied} revision(s) applied`); refresh(); }, onError: (e) => toast.error(errText(e)) });
  const shown = rows.filter((r: any) => (!status || r.status === status) && (!onlyEmployee || r.employee_id === onlyEmployee));
  const count = (s: string) => rows.filter((r: any) => r.status === s).length;

  return (
    <div>
      <PageHeader title="Salary revisions" icon={TrendingUp}
        subtitle="Raise or correct a salary with an effective date. It is applied only after approval, on that date."
        actions={<div className="flex gap-2">
          <button className="btn-secondary flex items-center gap-1" onClick={() => applyDue.mutate()} title="Apply approved revisions whose date has arrived"><RefreshCw className="w-4 h-4" /> Apply due</button>
          <button className="btn-secondary flex items-center gap-1" onClick={() => setBulk(true)}><Users className="w-4 h-4" /> Bulk revision</button>
          <button className="btn-primary flex items-center gap-1" onClick={() => setForm({})}><Plus className="w-4 h-4" /> New revision</button>
        </div>} />
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 mb-4">
        <Stat label="Drafts" value={count("draft")} />
        <Stat label="Waiting for approval" value={count("submitted")} accent />
        <Stat label="Approved, not yet due" value={count("approved")} />
        <Stat label="Applied" value={count("applied")} />
        <Stat label="Retro pay to decide" value={retro.filter((p: any) => p.status === "proposed").length} sub="Canada" />
      </div>
      <div className="flex gap-1 mb-3 border-b border-surface-border">
        {([["revisions", "Revisions"], ["batches", `Bulk batches (${batches.length})`], ["retro", `Retro pay (${retro.length})`], ["settings", "Approval settings"]] as [Tab, string][]).map(([k, l]) => (
          <button key={k} onClick={() => setTab(k)} className={`px-3 py-2 text-sm font-medium border-b-2 -mb-px ${tab === k ? "border-primary-500 text-primary-600" : "border-transparent text-gray-500 hover:text-gray-700"}`}>{l}</button>))}
      </div>

      {tab === "revisions" && <Grid rows={shown} keyOf={(r: any) => r.id} loading={isLoading} exportName="salary_revisions.csv" empty={onlyEmployee ? "No salary revisions for this employee yet. Click New revision." : "No salary revisions yet. Click New revision."} onRow={setView}
        filters={<>
          <Select className="!w-44 !py-1 text-xs" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option>{["draft", "submitted", "approved", "applied", "rejected", "cancelled"].map((s) => <option key={s} value={s}>{s}</option>)}</Select>
          {onlyEmployee && <span className="badge-purple flex items-center gap-1" title="Opened from the employee profile. Click × to see everyone.">
            Only {onlyEmp ? empLabel(onlyEmp) : "this employee"}
            <button className="hover:text-red-600" aria-label="Show all employees" onClick={() => setOnlyEmployee("")}><X className="w-3 h-3" /></button></span>}
        </>}
        cols={revisionCols} />}

      {tab === "batches" && <Grid rows={batches} keyOf={(r: any) => r.id} exportName="revision_batches.csv" empty="No bulk batches yet. Click Bulk revision." onRow={(b: any) => setBatchId(b.id)} cols={[
        { k: "created_at", label: "Created", render: (r: any) => fmtDate(r.created_at) },
        { k: "name", label: "Batch", render: (r: any) => <span className="font-medium text-gray-800">{r.name}</span> },
        { k: "source", label: "From" },
        { k: "employees", label: "Employees", align: "right" },
        { k: "cost_month", label: "Cost per month", align: "right", csv: (r: any) => totalsText(r.totals, "cost_per_month"), render: (r: any) => totalsText(r.totals, "cost_per_month") },
        { k: "cost_year", label: "Cost per year", align: "right", csv: (r: any) => totalsText(r.totals, "cost_per_year"), render: (r: any) => <b>{totalsText(r.totals, "cost_per_year")}</b> },
        { k: "status", label: "Status", render: (r: any) => <Status s={r.status} /> },
        { k: "created_by", label: "Created by" },
      ]} />}

      {tab === "retro" && <RetroTab rows={retro} onDone={refresh} />}
      {tab === "settings" && <SettingsTab canEdit={["org_admin", "superadmin"].includes(role)} />}

      {form && <RevisionForm initial={form} onClose={() => setForm(null)} onDone={() => { setForm(null); refresh(); }} />}
      {view && <RevisionView row={view} onClose={() => setView(null)} onDone={() => { setView(null); refresh(); }}
        onCorrect={(r: any) => { setView(null); setForm({ employee_id: r.employee_id, reason: "correction", corrects_revision_id: r.id, effective_from: r.effective_from }); }}
        onEdit={(r: any) => { setView(null); setForm({ ...r, edit_id: r.id }); }} />}
      {bulk && <BulkWizard onClose={() => setBulk(false)} onDone={(id: string) => { setBulk(false); refresh(); setTab("batches"); setBatchId(id); }} />}
      {batchId && <BatchView id={batchId} onClose={() => setBatchId(null)} onDone={refresh} />}
    </div>
  );
}

const totalsText = (t: any, k: string) => Object.entries(t || {}).map(([c, v]: any) => cur(v[k], c)).join(" + ") || "—";

const revisionCols = [
  { k: "created_at", label: "Created", render: (r: any) => fmtDate(r.created_at) },
  { k: "employee", label: "Employee", render: (r: any) => <span className="font-medium text-gray-800">{r.employee || "—"}<span className="text-gray-400 ml-1">{r.employee_number}</span></span> },
  { k: "department", label: "Department" },
  { k: "effective_from", label: "Effective", render: (r: any) => fmtDate(r.effective_from) },
  { k: "old", label: "Old", align: "right" as const, render: (r: any) => cur(r.old, r.currency) },
  { k: "new", label: "New", align: "right" as const, render: (r: any) => <b>{cur(r.new, r.currency)}</b> },
  { k: "percent", label: "Hike", align: "right" as const, render: (r: any) => pct(r.percent) },
  { k: "diff_monthly", label: "Per month", align: "right" as const, render: (r: any) => cur(r.diff_monthly, r.currency) },
  { k: "reason_label", label: "Reason" },
  { k: "status", label: "Status", csv: (r: any) => r.status, render: (r: any) => <span><Status s={r.status} />{r.status === "submitted" && r.needs_two_step ? <span className="badge-amber ml-1">{(r.approvals || []).length}/2</span> : null}</span> },
  { k: "created_by", label: "Created by" },
  { k: "decided_by", label: "Approved by" },
];

/* ── single revision form ─────────────────────────────────────────────────── */
function RevisionForm({ initial, onClose, onDone }: { initial: any; onClose: () => void; onDone: () => void }) {
  const employees = useEmployees();
  const [f, setF] = useState<any>({ employee_id: initial.employee_id || "", effective_from: initial.effective_from || today(), mode: initial.mode || "percent",
    value: initial.value ?? "", reason: initial.reason || "annual_increment", note: initial.note || "" });
  const [pv, setPv] = useState<any | null>(null);
  const [tried, setTried] = useState(false);
  const set = (k: string, val: any) => { setF((p: any) => ({ ...p, [k]: val })); setPv(null); };
  const body = () => ({ ...f, value: Number(f.value), note: String(f.note || "").trim() || undefined, corrects_revision_id: initial.corrects_revision_id });
  const emp: any = employees.find((e: any) => e.id === f.employee_id);
  const hire = emp?.hire_date ? String(emp.hire_date).slice(0, 10) : "";
  const n = Number(f.value);
  const errs = collect({
    employee_id: v.required(f.employee_id, "Employee"),
    effective_from: v.required(f.effective_from, "Effective date") || v.dateOrder(hire, f.effective_from, "The effective date cannot be before the joining date"),
    value: v.required(f.value, f.mode === "percent" ? "Percent" : f.mode === "amount" ? "Amount" : "New salary")
      || (!Number.isFinite(n) ? "Enter a number" : null)
      || (f.mode === "new_annual" && n <= 0 ? "The new salary must be more than 0" : null)
      || (f.mode === "percent" && n <= -100 ? "A cut cannot be 100% or more" : null)
      || (f.mode !== "new_annual" && n === 0 ? "A change of 0 does not change the salary" : null),
    note: v.maxLen(f.note, 1000, "Note"),
  });
  const show = (k: string) => (tried ? errs[k] : undefined);
  const monthsBack = f.effective_from ? Math.floor((Date.parse(today()) - Date.parse(f.effective_from)) / (30.44 * 86400000)) : 0;
  const preview = useMutation({ mutationFn: () => revisionsApi.preview(body()), onSuccess: setPv, onError: (e) => toast.error(errText(e)) });
  const save = useMutation({
    mutationFn: (submit: boolean) => initial.edit_id ? revisionsApi.update(initial.edit_id, { ...body(), submit }) : revisionsApi.create({ ...body(), submit }),
    onSuccess: (_r, submit) => { toast.success(submit ? "Sent for approval" : "Saved as draft"); onDone(); }, onError: (e) => toast.error(errText(e)) });
  const check = () => { setTried(true); if (Object.keys(errs).length) { toast.error(firstError(errs)); return false; } return true; };
  const codes = pv ? Array.from(new Set([...Object.keys(pv.old_components || {}), ...Object.keys(pv.new_components || {})])) : [];
  return (
    <Modal title={initial.corrects_revision_id ? "Correcting revision" : initial.edit_id ? "Edit draft revision" : "New salary revision"} size="xl" onClose={onClose}
      subtitle="Fill the form, click Preview, then save. Nothing changes on the employee until it is approved and the date arrives."
      footer={<>
        <button className="btn-secondary" onClick={onClose}>Close</button>
        <button className="btn-secondary" disabled={preview.isPending} onClick={() => { if (check()) preview.mutate(); }}>Preview</button>
        <button className="btn-secondary" disabled={!pv || save.isPending} title={!pv ? "Click Preview first" : undefined} onClick={() => { if (check()) save.mutate(false); }}>Save draft</button>
        <button className="btn-primary" disabled={!pv || save.isPending} title={!pv ? "Click Preview first" : undefined} onClick={() => { if (check()) save.mutate(true); }}>Submit for approval</button>
      </>}>
      <div className="grid lg:grid-cols-5 gap-4">
        <div className="lg:col-span-2 content-start">
          <FormSection title="Who and when">
            <FormGrid cols={2}>
              <div className="sm:col-span-2"><Field label="Employee *"><Select value={f.employee_id} disabled={!!initial.edit_id || !!initial.corrects_revision_id} onChange={(e) => set("employee_id", e.target.value)}>
                <option value="">— pick an employee —</option>{employees.map((e: any) => <option key={e.id} value={e.id}>{empLabel(e)}</option>)}</Select></Field><FieldError msg={show("employee_id")} /></div>
              <div><Field label="Effective date *" hint={hire ? `Joined ${fmtDate(hire)}` : undefined}><Input type="date" min={hire || undefined} value={f.effective_from} onChange={(e) => set("effective_from", e.target.value)} /></Field><FieldError msg={show("effective_from")} /></div>
              <Field label="Reason"><Select value={f.reason} onChange={(e) => set("reason", e.target.value)}>{Object.entries(REASONS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</Select></Field>
            </FormGrid>
            <p className="text-[11px] text-gray-500 mt-1.5">{f.effective_from && f.effective_from < today()
              ? `Back-dated${monthsBack >= 1 ? ` by about ${monthsBack} month(s)` : ""}: the difference for periods already paid is proposed as retro pay (see the preview).`
              : "A future date is applied automatically when it arrives (after approval)."}</p>
          </FormSection>
          <FormSection title="The change" hint="% hike raises the current salary by a percentage. Amount hike adds a fixed sum to the salary. New salary replaces the figure.">
            <FormGrid cols={2}>
              <Field label="Change by"><Select value={f.mode} onChange={(e) => set("mode", e.target.value)}>{MODES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</Select></Field>
              <div><Field label={`${f.mode === "percent" ? "Percent" : f.mode === "amount" ? "Amount to add" : "New salary"} *`}
                hint={f.mode === "new_annual" ? "Annual salary (per hour for hourly staff)" : f.mode === "percent" && n > 100 ? "Above 100% — check this is right" : f.mode === "percent" ? "Use a minus sign for a cut, e.g. -5" : "Use a minus sign for a reduction"}>
                <Input type="number" step="0.01" value={f.value} onChange={(e) => set("value", e.target.value)} placeholder={f.mode === "percent" ? "8" : f.mode === "amount" ? "60000" : "1320000"} /></Field><FieldError msg={show("value")} /></div>
              <div className="sm:col-span-2"><Field label="Note" hint="Why this change. Shown in the history and to the approver."><Textarea rows={3} maxLength={1000} value={f.note} onChange={(e) => set("note", e.target.value)} placeholder="Annual review 2026 — rating Exceeds" /></Field><FieldError msg={show("note")} /></div>
            </FormGrid>
          </FormSection>
          {initial.reason === "promotion" || f.reason === "promotion" ? <p className="text-[11px] text-gray-500">A new designation or grade is recorded on <Link className="underline" to={f.employee_id ? `/career-changes?employee=${f.employee_id}` : "/career-changes"}>Career changes</Link>, where you can link this revision.</p> : null}
        </div>
        <div className="lg:col-span-3 lg:sticky lg:top-0 self-start">
          {!pv ? <div className="card text-sm text-gray-400 text-center py-10">Click Preview to see old and new salary, the monthly difference and any retro pay.</div> : (
            <div className="space-y-3">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                <Stat label="Old" value={cur(pv.old, pv.currency)} sub={unit(pv).trim()} />
                <Stat label="New" value={cur(pv.new, pv.currency)} sub={unit(pv).trim()} accent />
                <Stat label="Difference per month" value={cur(pv.diff_monthly, pv.currency)} />
                <Stat label="Hike" value={pct(pv.percent)} sub={`${cur(pv.diff_annual, pv.currency)} a year`} />
              </div>
              {(pv.warnings || []).map((w: string) => <div key={w} className="text-xs rounded-lg px-3 py-2 bg-amber-50 text-amber-800 border border-amber-200">{w}</div>)}
              {codes.length > 0 && <div className="card !p-0"><table className="w-full text-xs">
                <thead className="bg-surface text-gray-400 text-[10px] uppercase"><tr><th className="px-3 py-2 text-left">Component (per month)</th><th className="px-3 py-2 text-right">Old</th><th className="px-3 py-2 text-right">New</th><th className="px-3 py-2 text-right">Difference</th></tr></thead>
                <tbody>{codes.map((c) => { const o = pv.old_components?.[c] || 0, nw = pv.new_components?.[c] || 0; return (
                  <tr key={c} className="border-t border-surface-border"><td className="px-3 py-1.5">{pv.component_names?.[c] || c}</td><td className="px-3 py-1.5 text-right tabular-nums">{cur(o, pv.currency)}</td><td className="px-3 py-1.5 text-right tabular-nums font-semibold">{cur(nw, pv.currency)}</td><td className="px-3 py-1.5 text-right tabular-nums">{cur(nw - o, pv.currency)}</td></tr>); })}</tbody></table></div>}
              {pv.retro && <div className="card !p-3"><div className="text-xs font-semibold text-gray-700 mb-1">Back-dated: retro pay of {cur(pv.retro.amount, pv.currency)} will be proposed for the next run</div>
                <div className="text-[11px] text-gray-500 mb-1">{pv.retro.note}</div>
                <table className="w-full text-xs"><thead className="text-gray-400 text-[10px] uppercase"><tr><th className="text-left py-1">Paid period</th><th className="text-right">Days</th><th className="text-right">Difference</th></tr></thead>
                  <tbody>{pv.retro.lines.map((l: any) => <tr key={l.period_end} className="border-t border-surface-border"><td className="py-1">{fmtDate(l.period_start)} – {fmtDate(l.period_end)}</td><td className="text-right">{l.days} of {l.period_days}</td><td className="text-right font-semibold">{cur(l.amount, pv.currency)}</td></tr>)}</tbody></table></div>}
            </div>)}
        </div>
      </div>
    </Modal>
  );
}

/* ── one revision: detail + actions ───────────────────────────────────────── */
function RevisionView({ row, onClose, onDone, onCorrect, onEdit }: any) {
  const act = useMutation({
    mutationFn: ({ a, note }: { a: "submit" | "approve" | "reject" | "cancel"; note?: string }) => revisionsApi.act(row.id, a, note),
    onSuccess: (r: any, v) => { toast.success(v.a === "approve" ? (r.waiting_second_approval ? "First approval recorded. A second approver is needed." : r.status === "applied" ? "Approved and applied" : "Approved. It will be applied on the effective date.") : `Revision ${r.status}`); onDone(); },
    onError: (e) => toast.error(errText(e)) });
  const letter = useMutation({ mutationFn: () => revisionsApi.letter(row.id), onSuccess: async (r: any) => { toast.success(`Letter ${r.number} issued`); openPdf(await lettersApi.pdf(r.letter_id), `${r.number}.pdf`); onDone(); }, onError: (e) => toast.error(errText(e)) });
  const ar = row.apply_result || {};
  const codes = Array.from(new Set([...Object.keys(row.old_components || {}), ...Object.keys(row.new_components || {})]));
  const s = row.status;
  return (
    <Modal title={`${row.employee || "Employee"} · salary revision`} size="xl" onClose={onClose}
      subtitle={`${cur(row.old, row.currency)} → ${cur(row.new, row.currency)}${unit(row)} from ${fmtDate(row.effective_from)} · ${row.reason_label}`}
      footer={<>
        {s === "draft" && <><button className="btn-secondary" onClick={() => onEdit(row)}>Edit</button><button className="btn-primary" onClick={() => act.mutate({ a: "submit" })}>Submit for approval</button></>}
        {s === "submitted" && <><button className="btn-secondary text-red-600" onClick={() => { const n = window.prompt("Reason for rejecting"); if (n) act.mutate({ a: "reject", note: n }); }}>Reject</button><button className="btn-primary" onClick={() => act.mutate({ a: "approve" })}>Approve</button></>}
        {["draft", "submitted", "approved"].includes(s) && <button className="btn-secondary" onClick={() => { if (window.confirm("Withdraw this revision? It will not be applied.")) act.mutate({ a: "cancel" }); }}>Withdraw</button>}
        {["approved", "applied"].includes(s) && <button className="btn-secondary" disabled={letter.isPending} onClick={() => letter.mutate()}>Issue revision letter</button>}
        {s === "applied" && <button className="btn-secondary" onClick={() => onCorrect(row)}>Correct with a new revision</button>}
      </>}>
      <div className="grid sm:grid-cols-4 gap-2 mb-3">
        <Stat label="Status" value={<Status s={s} />} sub={row.needs_two_step ? `Two approvals needed · ${(row.approvals || []).length} given` : undefined} />
        <Stat label="Hike" value={pct(row.percent)} sub={`${cur(row.diff_monthly, row.currency)} a month`} />
        <Stat label="Created by" value={<span className="text-sm">{row.created_by || "—"}</span>} sub={fmtDate(row.created_at)} />
        <Stat label="Approved by" value={<span className="text-sm">{row.decided_by || "—"}</span>} sub={row.applied_at ? `Applied ${fmtDate(row.applied_at)}` : row.decided_at ? fmtDate(row.decided_at) : undefined} />
      </div>
      {row.note && <p className="text-sm text-gray-600 mb-2"><b>Note:</b> {row.note}</p>}
      {row.decision_note && <p className="text-sm text-gray-600 mb-2"><b>Decision note:</b> {row.decision_note}</p>}
      {row.corrects_revision_id && <p className="text-xs text-gray-500 mb-2">This revision corrects an earlier applied revision.</p>}
      {ar.retro_amount !== undefined && <div className="text-xs rounded-lg px-3 py-2 bg-primary-50 text-primary-700 mb-2">Retro pay of {cur(ar.retro_amount, row.currency)} is proposed. Decide it on the Retro pay tab.</div>}
      {ar.retro_note && <div className="text-xs rounded-lg px-3 py-2 bg-amber-50 text-amber-800 mb-2">{ar.retro_note}</div>}
      {ar.error && <div className="text-xs rounded-lg px-3 py-2 bg-red-50 text-red-700 mb-2">Not applied yet: {ar.error}. Click Apply due to try again.</div>}
      {codes.length > 0 && <div className="card !p-0"><table className="w-full text-xs">
        <thead className="bg-surface text-gray-400 text-[10px] uppercase"><tr><th className="px-3 py-2 text-left">Component (per month)</th><th className="px-3 py-2 text-right">Old</th><th className="px-3 py-2 text-right">New</th></tr></thead>
        <tbody>{codes.map((c) => <tr key={c} className="border-t border-surface-border"><td className="px-3 py-1.5">{c}</td><td className="px-3 py-1.5 text-right tabular-nums">{cur(row.old_components?.[c] || 0, row.currency)}</td><td className="px-3 py-1.5 text-right tabular-nums font-semibold">{cur(row.new_components?.[c] || 0, row.currency)}</td></tr>)}</tbody></table></div>}
      {(row.approvals || []).length > 0 && <div className="text-xs text-gray-500 mt-3">Approvals: {row.approvals.map((a: any) => `${a.by} (${fmtDate(a.at)})`).join(", ")}</div>}
    </Modal>
  );
}

/* ── bulk wizard ──────────────────────────────────────────────────────────── */
const BULK_TYPES = [["full_time", "Full time"], ["part_time", "Part time"], ["contract", "Contract"], ["fixed_term", "Fixed term"], ["seasonal", "Seasonal"], ["intern", "Intern"]];
const MAX_SHEET_MB = 5;   // the server refuses larger files

function BulkWizard({ onClose, onDone }: { onClose: () => void; onDone: (batchId: string) => void }) {
  const employees = useEmployees();
  const fileRef = useRef<HTMLInputElement>(null);
  const [src, setSrc] = useState<"filter" | "sheet">("filter");
  const [flt, setFlt] = useState<any>({ department: "", grade: "", job_title: "", work_location: "", employment_type: "", country: "" });
  const [f, setF] = useState<any>({ mode: "percent", value: "", effective_from: today(), reason: "annual_increment", name: "", note: "" });
  const [review, setReview] = useState<any | null>(null);
  const [fileErrors, setFileErrors] = useState<string[]>([]);
  const [tried, setTried] = useState(false);
  const opts = (k: string) => Array.from(new Set(employees.map((e: any) => e[k]).filter(Boolean))).sort() as string[];
  /* Same matching as the server (case-insensitive equals; country from the address, default CA). */
  const matched = useMemo(() => employees.filter((e: any) =>
    (["department", "grade", "job_title", "work_location", "employment_type"] as const).every((k) => !flt[k] || String(e[k] || "").trim().toLowerCase() === String(flt[k]).toLowerCase())
    && (!flt.country || String(e.address_country || "CA").toUpperCase() === flt.country)).length, [employees, flt]);
  const n = Number(f.value);
  const errs = collect({
    value: src === "filter" ? (v.required(f.value, f.mode === "percent" ? "Percent" : "Amount") || (!Number.isFinite(n) ? "Enter a number" : null)
      || (n === 0 ? "A change of 0 does not change any salary" : null) || (f.mode === "percent" && n <= -100 ? "A cut cannot be 100% or more" : null)) : null,
    effective_from: v.required(f.effective_from, "Effective date"),
    matched: src === "filter" && matched === 0 && employees.length > 0 ? "No employee matches these filters" : null,
  });
  const show = (k: string) => (tried ? errs[k] : undefined);
  const saveErrs = collect({ name: v.maxLen(f.name, 160, "Batch name"), note: v.maxLen(f.note, 1000, "Batch note") });
  const setFlt1 = (k: string, val: string) => { setFlt({ ...flt, [k]: val }); setReview(null); };
  const preview = useMutation({
    mutationFn: () => revisionsApi.bulkPreview({ filters: flt, mode: f.mode, value: Number(f.value), effective_from: f.effective_from, reason: f.reason }),
    onSuccess: (r) => { setReview(r); setFileErrors([]); }, onError: (e) => toast.error(errText(e)) });
  const upload = useMutation({ mutationFn: (file: File) => revisionsApi.bulkUpload(file, f.effective_from), onSuccess: (r: any) => { setReview(r); setFileErrors(r.file_errors || []); }, onError: (e) => toast.error(errText(e)) });
  const okRows = useMemo(() => (review?.rows || []).filter((r: any) => r.ok), [review]);
  const save = useMutation({
    mutationFn: () => revisionsApi.bulkDraft({ source: src === "sheet" ? "sheet" : "bulk", name: String(f.name || "").trim() || undefined, note: String(f.note || "").trim() || undefined,
      items: okRows.map((r: any) => ({ employee_id: r.employee_id, [r.mode]: r.value, effective_from: r.effective_from, reason: r.reason, note: r.note, rating: r.rating })) }),
    onSuccess: (r: any) => { toast.success(`Draft batch saved with ${r.created} employees`); onDone(r.batch_id); }, onError: (e) => toast.error(errText(e)) });
  const runReview = () => {
    setTried(true);
    if (Object.keys(errs).length) { toast.error(firstError(errs)); return; }
    preview.mutate();
  };
  const pickFile = (file: File) => {
    if (!/\.(csv|xlsx)$/i.test(file.name)) { toast.error("Choose a .csv or .xlsx file"); return; }
    if (file.size > MAX_SHEET_MB * 1024 * 1024) { toast.error(`The file is too large (max ${MAX_SHEET_MB} MB)`); return; }
    if (!f.effective_from) { setTried(true); toast.error("Effective date is required"); return; }
    upload.mutate(file);
  };
  const saveBatch = () => {
    if (Object.keys(saveErrs).length) { toast.error(firstError(saveErrs)); return; }
    save.mutate();
  };
  const t = review?.totals;
  return (
    <Modal title="Bulk salary revision" size="screen" onClose={onClose} subtitle="Pick employees or upload a sheet, review every row, then save as a draft batch. A draft changes nothing until it is submitted and approved."
      footer={<><button className="btn-secondary" onClick={onClose}>Close</button>
        <button className="btn-primary" disabled={!okRows.length || save.isPending} onClick={saveBatch}>Save {okRows.length} rows as draft batch</button></>}>
      <div className="flex flex-wrap items-center gap-2 mb-3">
        {([["filter", "Filter employees"], ["sheet", "Upload a sheet"]] as const).map(([k, l]) => <button key={k} onClick={() => { setSrc(k); setReview(null); setTried(false); }} className={`px-3 py-1.5 rounded-lg text-sm ${src === k ? "bg-primary-500 text-white" : "bg-surface text-gray-600"}`}>{l}</button>)}
        <span className="text-[11px] text-gray-500">{src === "filter" ? "One rule for everyone who matches." : "A different figure per employee, from your sheet."}</span>
      </div>
      {src === "filter" ? (<>
        <FormSection title="Who" hint={<>Leave a filter on All to include everyone. <b className="text-gray-700">{matched}</b> employee(s) match now.</>}>
          <FormGrid cols={3} className="lg:!grid-cols-6">
            {([["department", "Department"], ["grade", "Grade"], ["job_title", "Designation"], ["work_location", "Location"]] as const).map(([k, l]) => (
              <Field key={k} label={l}><Select value={flt[k]} onChange={(e) => setFlt1(k, e.target.value)}><option value="">All</option>{opts(k).map((o) => <option key={o} value={o}>{o}</option>)}</Select></Field>))}
            <Field label="Employment type"><Select value={flt.employment_type} onChange={(e) => setFlt1("employment_type", e.target.value)}><option value="">All</option>{BULK_TYPES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</Select></Field>
          </FormGrid>
          <FieldError msg={show("matched")} />
        </FormSection>
        <FormSection title="What to apply" hint="People serving notice or still on probation are included when they match. To leave them out, upload a sheet with only the people to revise.">
          <FormGrid cols={4} className="lg:!grid-cols-5 items-start">
            <Field label="Apply"><Select value={f.mode} onChange={(e) => { setF({ ...f, mode: e.target.value }); setReview(null); }}><option value="percent">% hike</option><option value="amount">Amount hike</option></Select></Field>
            <div><Field label={`${f.mode === "percent" ? "Percent" : "Amount"} *`} hint={f.mode === "percent" && n > 100 ? "Above 100% — check this is right" : f.mode === "percent" ? "e.g. 8 for 8%" : "Added to each salary"}><Input type="number" step="0.01" value={f.value} onChange={(e) => { setF({ ...f, value: e.target.value }); setReview(null); }} placeholder={f.mode === "percent" ? "8" : "60000"} /></Field><FieldError msg={show("value")} /></div>
            <div><Field label="Effective date *"><Input type="date" value={f.effective_from} onChange={(e) => { setF({ ...f, effective_from: e.target.value }); setReview(null); }} /></Field><FieldError msg={show("effective_from")} /></div>
            <Field label="Reason"><Select value={f.reason} onChange={(e) => { setF({ ...f, reason: e.target.value }); setReview(null); }}>{Object.entries(REASONS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</Select></Field>
            <div className="flex items-end h-full"><button className="btn-primary w-full mt-5" disabled={preview.isPending} onClick={runReview}>Review {matched} employee(s)</button></div>
          </FormGrid>
        </FormSection>
      </>) : (
        <FormSection title="Sheet" hint="Columns: Employee no, New salary or %, Effective date, Reason, Note. CSV or XLSX, up to 5 MB.">
          <div className="flex flex-wrap items-end gap-3">
            <div><Field label="Effective date for rows with no date *" hint="Used only where the sheet's Effective date column is blank"><Input type="date" value={f.effective_from} onChange={(e) => setF({ ...f, effective_from: e.target.value })} /></Field><FieldError msg={show("effective_from")} /></div>
            <input ref={fileRef} type="file" accept=".csv,.xlsx" className="hidden" onChange={(e) => { const file = e.target.files?.[0]; if (file) pickFile(file); e.target.value = ""; }} />
            <button className="btn-primary flex items-center gap-1" disabled={upload.isPending} onClick={() => fileRef.current?.click()}><Upload className="w-4 h-4" /> Choose CSV or XLSX</button>
            <button className="btn-secondary flex items-center gap-1" onClick={async () => downloadBlob("salary-revision-sheet.csv", await revisionsApi.sheetTemplate())}><Download className="w-4 h-4" /> Sample sheet</button>
          </div>
        </FormSection>)}
      {fileErrors.map((e) => <div key={e} className="text-xs rounded-lg px-3 py-1.5 bg-red-50 text-red-700 mb-1">{e}</div>)}
      {review && <>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-2 mb-3">
          <Stat label="Rows" value={t.rows} sub={`${t.ok} can be saved`} />
          <Stat label="Rows with errors" value={t.with_errors} />
          <Stat label="Rows with warnings" value={t.with_warnings} />
          <Stat label="Cost per month" value={totalsText(t.by_currency, "cost_per_month")} accent />
          <Stat label="Cost per year" value={totalsText(t.by_currency, "cost_per_year")} sub={Object.entries(t.by_currency || {}).map(([c, val]: any) => `${c} avg ${pct(val.average_percent)}`).join(" · ")} />
        </div>
        <FormGrid cols={2} className="mb-2">
          <div><Field label="Batch name" hint="A name you will recognise in approvals"><Input maxLength={160} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="April 2027 increments" /></Field><FieldError msg={saveErrs.name} /></div>
          <div><Field label="Batch note (optional)" hint="Shown to the approver with the batch, e.g. the sanctioned budget"><Input maxLength={1000} value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} placeholder="Approved by the board on 15 Mar — 9% pool" /></Field><FieldError msg={saveErrs.note} /></div>
        </FormGrid>
        <Grid rows={review.rows} keyOf={(r: any) => `${r.employee_id}-${r.employee_number}-${r.row || ""}`} exportName="bulk_revision_review.csv" empty="No employees match." cols={[
          { k: "employee_number", label: "Employee no" },
          { k: "employee", label: "Employee", render: (r: any) => <span className="font-medium text-gray-800">{r.employee || "—"}</span> },
          { k: "department", label: "Department" }, { k: "grade", label: "Grade" },
          { k: "old", label: "Old", align: "right", render: (r: any) => r.old !== undefined ? cur(r.old, "CAD") : "—" },
          { k: "new", label: "New", align: "right", render: (r: any) => r.new !== undefined ? <b>{cur(r.new, "CAD")}</b> : "—" },
          { k: "percent", label: "Hike", align: "right", render: (r: any) => pct(r.percent) },
          { k: "diff_monthly", label: "Per month", align: "right", render: (r: any) => r.diff_monthly !== undefined ? cur(r.diff_monthly, "CAD") : "—" },
          { k: "diff_annual", label: "Per year", align: "right", render: (r: any) => r.diff_annual !== undefined ? cur(r.diff_annual, "CAD") : "—" },
          { k: "effective_from", label: "Effective", render: (r: any) => fmtDate(r.effective_from) },
          { k: "check", label: "Check", csv: (r: any) => [...(r.errors || []), ...(r.warnings || [])].join(" | "), render: (r: any) => (r.errors || []).length ? <span className="text-red-600">{r.errors.join(" ")}</span> : (r.warnings || []).length ? <span className="text-amber-700">{r.warnings.join(" ")}</span> : <span className="text-emerald-600">OK</span> },
        ]} />
      </>}
    </Modal>
  );
}

function BatchView({ id, onClose, onDone }: { id: string; onClose: () => void; onDone: () => void }) {
  const qc = useQueryClient();
  const { data: b } = useQuery({ queryKey: ["revision-batch", id], queryFn: () => revisionsApi.batch(id) });
  const act = useMutation({
    mutationFn: ({ a, note }: { a: "submit" | "approve" | "reject" | "cancel"; note?: string }) => revisionsApi.batchAct(id, a, note),
    onSuccess: (r: any, v) => { const x = r.result || {}; toast.success(v.a === "approve" ? `${x.approved} approved${x.waiting_second_approval ? `, ${x.waiting_second_approval} need a second approval` : ""}${x.applied_now ? `, ${x.applied_now} applied now` : ""}` : `Batch ${r.status}`); qc.invalidateQueries({ queryKey: ["revision-batch", id] }); onDone(); },
    onError: (e) => toast.error(errText(e)) });
  if (!b) return null;
  return (
    <Modal title={b.name} size="screen" onClose={onClose} subtitle={`${b.employees} employees · cost per month ${totalsText(b.totals, "cost_per_month")} · per year ${totalsText(b.totals, "cost_per_year")} · created by ${b.created_by || "—"}`}
      footer={<>
        {b.status === "draft" && <button className="btn-primary" onClick={() => act.mutate({ a: "submit" })}>Submit batch for approval</button>}
        {b.status === "submitted" && <><button className="btn-secondary text-red-600" onClick={() => { const n = window.prompt("Reason for rejecting the batch"); if (n) act.mutate({ a: "reject", note: n }); }}>Reject batch</button><button className="btn-primary" onClick={() => act.mutate({ a: "approve" })}>Approve batch</button></>}
        {["draft", "submitted", "approved"].includes(b.status) && <button className="btn-secondary" onClick={() => { if (window.confirm("Withdraw this batch? Rows not yet applied will be cancelled.")) act.mutate({ a: "cancel" }); }}>Withdraw batch</button>}
      </>}>
      <div className="mb-2"><Status s={b.status} /> <span className="text-xs text-gray-500 ml-2">{Object.entries(b.counts || {}).map(([k, v]) => `${v} ${k}`).join(" · ")}</span></div>
      <Grid rows={b.rows || []} keyOf={(r: any) => r.id} exportName="revision_batch.csv" cols={revisionCols} />
    </Modal>
  );
}

function RetroTab({ rows, onDone }: { rows: any[]; onDone: () => void }) {
  const act = useMutation({ mutationFn: ({ id, a }: { id: string; a: "accept" | "dismiss" }) => revisionsApi.retroAct(id, a), onSuccess: (r: any) => { toast.success(r.status === "accepted" ? "It will be paid in the next regular run" : "Dismissed"); onDone(); }, onError: (e) => toast.error(errText(e)) });
  return (<>
    <p className="text-xs text-gray-500 mb-2">Canada. When a revision is dated inside periods that are already paid, the difference is proposed here as Retroactive Pay (code RETRO). Accept it and it is added to the employee's next regular pay run; the pay run works out the deductions and tax on it.</p>
    <Grid rows={rows} keyOf={(r: any) => r.id} exportName="retro_pay.csv" empty="No retro pay proposals." cols={[
      { k: "created_at", label: "Created", render: (r: any) => fmtDate(r.created_at) },
      { k: "employee", label: "Employee", render: (r: any) => <span className="font-medium text-gray-800">{r.employee || "—"}<span className="text-gray-400 ml-1">{r.employee_number}</span></span> },
      { k: "label", label: "For" },
      { k: "periods", label: "Paid periods", align: "right", csv: (r: any) => (r.lines || []).length, render: (r: any) => (r.lines || []).length },
      { k: "amount", label: "Amount", align: "right", render: (r: any) => <b>{cur(r.amount, r.currency)}</b> },
      { k: "status", label: "Status", render: (r: any) => <Status s={r.status} /> },
      { k: "paid_period_end", label: "Paid in period ending", render: (r: any) => fmtDate(r.paid_period_end) },
      { k: "actions", label: "", csv: () => "", render: (r: any) => r.status === "paid" ? null : <span className="flex gap-1">
        {r.status !== "accepted" && <button className="btn-secondary !py-0.5 !px-2 text-xs" onClick={() => act.mutate({ id: r.id, a: "accept" })}>Pay in next run</button>}
        {r.status !== "dismissed" && <button className="btn-secondary !py-0.5 !px-2 text-xs" onClick={() => act.mutate({ id: r.id, a: "dismiss" })}>Dismiss</button>}</span> },
    ]} />
  </>);
}

function SettingsTab({ canEdit }: { canEdit: boolean }) {
  const qc = useQueryClient();
  const { data: s } = useQuery({ queryKey: ["revision-settings"], queryFn: () => revisionsApi.settings() });
  const [f, setF] = useState<any | null>(null);
  const v = f || s || {};
  const save = useMutation({ mutationFn: () => revisionsApi.saveSettings({ two_step_enabled: !!v.two_step_enabled, two_step_percent: v.two_step_percent === "" || v.two_step_percent == null ? null : Number(v.two_step_percent), two_step_amount: v.two_step_amount === "" || v.two_step_amount == null ? null : Number(v.two_step_amount) }),
    onSuccess: () => { toast.success("Approval settings saved"); setF(null); qc.invalidateQueries({ queryKey: ["revision-settings"] }); }, onError: (e) => toast.error(errText(e)) });
  return (
    <div className="card max-w-3xl">
      <h3 className="font-display font-semibold text-gray-800 mb-1">Who approves a salary revision</h3>
      <ul className="text-sm text-gray-600 list-disc ml-5 mb-4 space-y-1">
        <li>HR and payroll can create and submit a revision.</li>
        <li>The HR head (HR Admin) or the Org Admin approves it.</li>
        <li>The person who created a revision can never approve it.</li>
        <li>Only an approved revision is applied, and only when its effective date arrives.</li>
      </ul>
      <label className="flex items-center gap-2 text-sm mb-3"><input type="checkbox" disabled={!canEdit} checked={!!v.two_step_enabled} onChange={(e) => setF({ ...v, two_step_enabled: e.target.checked })} /> Ask for a second approval (by the Org Admin) for large revisions</label>
      <div className="grid sm:grid-cols-2 gap-3 mb-4">
        <Field label="Hike % at or above" hint="Leave empty to ignore"><Input type="number" disabled={!canEdit || !v.two_step_enabled} value={v.two_step_percent ?? ""} onChange={(e) => setF({ ...v, two_step_percent: e.target.value })} placeholder="20" /></Field>
        <Field label="Increase per year at or above" hint="Leave empty to ignore"><Input type="number" disabled={!canEdit || !v.two_step_enabled} value={v.two_step_amount ?? ""} onChange={(e) => setF({ ...v, two_step_amount: e.target.value })} placeholder="300000" /></Field>
      </div>
      {canEdit ? <button className="btn-primary" disabled={!f || save.isPending} onClick={() => save.mutate()}>Save</button> : <p className="text-xs text-gray-500">Only the Org Admin can change these settings.</p>}
    </div>
  );
}
