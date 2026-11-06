import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import toast from "react-hot-toast";
import { ArrowUpRight, Plus, X } from "lucide-react";
import { PageHeader } from "../../components/ui";
import { careerApi, lettersApi, revisionsApi, openPdf, errText, fmtDate, cur } from "../../api/lifecycle";
import { FormGrid, FormSection } from "../../components/Modal";
import { v, collect, firstError, FieldError } from "../../lib/validators";
import { Grid, Modal, Field, Input, Select, Textarea, Status, Stat, useEmployees, empLabel, today } from "../common/_shared";

/* Promotion / transfer / re-designation / manager, location, department, grade change
   AS A RECORD: old → new captured automatically, approved, applied on the effective date. */

const LABEL: Record<string, string> = { job_title: "Designation", department: "Department", grade: "Grade", manager_id: "Manager",
  work_location: "Location", division: "Division", cost_center: "Cost centre" };

export default function CareerChangesPage() {
  const qc = useQueryClient();
  /* Deep link from the employee profile: /career-changes?employee=<id> opens the new-change
     form with that person picked and narrows the list to their changes until the chip is closed. */
  const [params] = useSearchParams();
  const linkedEmployee = params.get("employee") || "";
  const [onlyEmployee, setOnlyEmployee] = useState(linkedEmployee);
  const employees = useEmployees();
  const onlyEmp: any = onlyEmployee ? employees.find((e: any) => e.id === onlyEmployee) : null;
  const [status, setStatus] = useState("");
  const [showNew, setShowNew] = useState(!!linkedEmployee);
  const [view, setView] = useState<any | null>(null);
  const { data: rows = [], isLoading } = useQuery({ queryKey: ["career-changes"], queryFn: () => careerApi.list() });
  const refresh = () => { qc.invalidateQueries({ queryKey: ["career-changes"] }); qc.invalidateQueries({ queryKey: ["employees"] }); };
  const shown = rows.filter((r: any) => (!status || r.status === status) && (!onlyEmployee || r.employee_id === onlyEmployee));
  const count = (s: string) => rows.filter((r: any) => r.status === s).length;
  return (
    <div>
      <PageHeader title="Career changes" icon={ArrowUpRight} subtitle="Promotions, transfers and other job changes. Each one is a record with an effective date, an approval and a history."
        actions={<button className="btn-primary flex items-center gap-1" onClick={() => setShowNew(true)}><Plus className="w-4 h-4" /> New career change</button>} />
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
        <Stat label="Drafts" value={count("draft")} />
        <Stat label="Waiting for approval" value={count("submitted")} accent />
        <Stat label="Approved, not yet due" value={count("approved")} />
        <Stat label="Applied" value={count("applied")} />
      </div>
      <Grid rows={shown} keyOf={(r: any) => r.id} loading={isLoading} exportName="career_changes.csv" empty={onlyEmployee ? "No career changes for this employee yet. Click New career change." : "No career changes yet. Click New career change."} onRow={setView}
        filters={<>
          <Select className="!w-44 !py-1 text-xs" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">All statuses</option>{["draft", "submitted", "approved", "applied", "rejected", "cancelled"].map((s) => <option key={s} value={s}>{s}</option>)}</Select>
          {onlyEmployee && <span className="badge-purple flex items-center gap-1" title="Opened from the employee profile. Click × to see everyone.">
            Only {onlyEmp ? empLabel(onlyEmp) : "this employee"}
            <button className="hover:text-red-600" aria-label="Show all employees" onClick={() => setOnlyEmployee("")}><X className="w-3 h-3" /></button></span>}
        </>}
        cols={[
          { k: "created_at", label: "Created", render: (r: any) => fmtDate(r.created_at) },
          { k: "employee", label: "Employee", render: (r: any) => <span className="font-medium text-gray-800">{r.employee}<span className="text-gray-400 ml-1">{r.employee_number}</span></span> },
          { k: "type_label", label: "Type" },
          { k: "effective_date", label: "Effective", render: (r: any) => fmtDate(r.effective_date) },
          { k: "summary", label: "Change (old → new)", render: (r: any) => <span className="whitespace-normal">{r.summary}</span> },
          { k: "reason", label: "Reason" },
          { k: "status", label: "Status", render: (r: any) => <Status s={r.status} /> },
          { k: "created_by", label: "Created by" },
          { k: "decided_by", label: "Approved by" },
        ]} />
      {showNew && <ChangeForm initialEmployeeId={linkedEmployee} onClose={() => setShowNew(false)} onDone={() => { setShowNew(false); refresh(); }} />}
      {view && <ChangeView row={view} onClose={() => setView(null)} onDone={() => { setView(null); refresh(); }} />}
    </div>
  );
}

function ChangeForm({ initialEmployeeId = "", onClose, onDone }: { initialEmployeeId?: string; onClose: () => void; onDone: () => void }) {
  const employees = useEmployees();
  const { data: meta } = useQuery({ queryKey: ["career-fields"], queryFn: () => careerApi.fields() });
  const [employeeId, setEmployeeId] = useState(initialEmployeeId);
  const [type, setType] = useState("promotion");
  const [date, setDate] = useState(today());
  const [nv, setNv] = useState<Record<string, string>>({});
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  const [linkedRevision, setLinkedRevision] = useState("");
  const [tried, setTried] = useState(false);
  const emp: any = employees.find((e: any) => e.id === employeeId);
  /* Salary revisions of this person that are still live — a promotion usually has one. */
  const { data: revisions = [] } = useQuery({ queryKey: ["revisions", "for-career", employeeId], enabled: !!employeeId,
    queryFn: () => revisionsApi.list({ employee_id: employeeId }), retry: false });
  const liveRevisions = (revisions as any[]).filter((r) => !["rejected", "cancelled"].includes(r.status));
  const grade = meta?.extras?.[employeeId]?.grade || "";
  const current: Record<string, string> = emp ? { job_title: emp.job_title || "", department: emp.department || "", grade,
    manager_id: emp.manager_id || "", work_location: emp.work_location || "", division: emp.division || "", cost_center: emp.cost_center || "" } : {};
  /* A legal-entity transfer changes the CRA payroll account (BN / RP) and is recorded from the
     employee profile (Transfer to another entity), not from this form. */
  const types: any[] = (meta?.types || []).filter((t: any) => t.key !== "entity_transfer");
  const typeLabel = types.find((t: any) => t.key === type)?.label || type;
  const needs: string[] = types.find((t: any) => t.key === type)?.needs_one_of || [];
  const opts = (k: string) => Array.from(new Set(employees.map((e: any) => (k === "grade" ? meta?.extras?.[e.id]?.grade : e[k])).filter(Boolean))).sort() as string[];
  const changed = Object.fromEntries(Object.entries(nv).filter(([k, val]) => val !== undefined && val !== (current[k] || "")));
  const hire = emp?.hire_date ? String(emp.hire_date).slice(0, 10) : "";
  const errs = collect({
    employee: v.required(employeeId, "Employee"),
    date: v.required(date, "Effective date") || (hire ? v.dateOrder(hire, date, "The effective date cannot be before the joining date") : null),
    values: employeeId && Object.keys(changed).length === 0 ? "Type at least one new value" : null,
    needs: employeeId && Object.keys(changed).length > 0 && needs.length && !needs.some((k) => k in changed)
      ? `A ${String(typeLabel).toLowerCase()} must change the ${needs.map((k) => (LABEL[k] || k).toLowerCase()).join(" or ")}` : null,
    manager: changed.manager_id && changed.manager_id === employeeId ? "An employee cannot be their own manager" : null,
    reason: v.required(reason, "Reason") || v.maxLen(reason, 200, "Reason"),
    note: v.maxLen(note, 1000, "Note"),
  });
  const show = (k: string) => (tried ? errs[k] : undefined);
  const save = useMutation({
    mutationFn: (submit: boolean) => careerApi.create(employeeId, { change_type: type, effective_date: date, new_values: changed, reason: reason.trim(),
      note: note.trim() || undefined, linked_revision_id: linkedRevision || undefined, submit }),
    onSuccess: (_r, submit) => { toast.success(submit ? "Sent for approval" : "Saved as draft"); onDone(); }, onError: (e) => toast.error(errText(e)) });
  const go = (submit: boolean) => {
    setTried(true);
    if (Object.keys(errs).length) { toast.error(firstError(errs)); return; }
    save.mutate(submit);
  };
  const mgrName = (id: string) => { const m: any = employees.find((e: any) => e.id === id); return m ? `${m.first_name} ${m.last_name}` : "—"; };
  return (
    <Modal title="New career change" size="xl" onClose={onClose} subtitle="Pick the employee and the type. Type the new values only for what changes. The old values are kept automatically."
      footer={<><button className="btn-secondary" onClick={onClose}>Close</button>
        <button className="btn-secondary" disabled={save.isPending} onClick={() => go(false)}>Save draft</button>
        <button className="btn-primary" disabled={save.isPending} onClick={() => go(true)}>Submit for approval</button></>}>
      <FormSection title="Employee and change" hint="The effective date is the day the new details take effect. Payroll and reports pick them up from that date.">
        <FormGrid cols={4}>
          <div className="sm:col-span-2"><Field label="Employee *"><Select value={employeeId} onChange={(e) => { setEmployeeId(e.target.value); setNv({}); setLinkedRevision(""); }}><option value="">— pick an employee —</option>{employees.map((e: any) => <option key={e.id} value={e.id}>{empLabel(e)}</option>)}</Select></Field><FieldError msg={show("employee")} /></div>
          <Field label="Type *" hint="Rows marked ● are what this type changes"><Select value={type} onChange={(e) => setType(e.target.value)}>{types.map((t: any) => <option key={t.key} value={t.key}>{t.label}</option>)}</Select></Field>
          <div><Field label="Effective date *" hint={hire ? `Joined ${fmtDate(hire)}` : undefined}><Input type="date" min={hire || undefined} value={date} onChange={(e) => setDate(e.target.value)} /></Field><FieldError msg={show("date")} /></div>
        </FormGrid>
        <p className="text-[11px] text-gray-500 mt-2">Moving someone to another legal entity (company) changes the employer (CRA payroll account, T4 issuer, WCB account) — record that from the employee profile with <b>Transfer to another entity</b>.</p>
      </FormSection>
      {emp && <FormSection title="Old and new values" hint="Leave a row as it is when it does not change. A move to another province changes the provincial tax, employment-standards and WCB rules — confirm with payroll.">
        <div className="card !p-0 overflow-x-auto"><table className="w-full text-sm">
          <thead className="bg-surface text-gray-400 text-[10px] uppercase"><tr><th className="px-3 py-2 text-left w-40">Field</th><th className="px-3 py-2 text-left">Now</th><th className="px-3 py-2 text-left">New value</th></tr></thead>
          <tbody>{Object.keys(LABEL).map((k) => (
            <tr key={k} className={`border-t border-surface-border ${k in changed ? "bg-primary-50/40" : ""}`}>
              <td className="px-3 py-1.5 text-gray-600">{LABEL[k]}{needs.includes(k) && <span className="text-primary-600 ml-1" title="This type changes this field">●</span>}</td>
              <td className="px-3 py-1.5 text-gray-800">{k === "manager_id" ? (emp.manager_name || (current[k] ? mgrName(current[k]) : "—")) : current[k] || "—"}</td>
              <td className="px-3 py-1.5 min-w-[12rem]">{k === "manager_id"
                ? <Select value={nv[k] ?? current[k]} onChange={(e) => setNv({ ...nv, [k]: e.target.value })}><option value="">— no manager —</option>{employees.filter((e: any) => e.id !== employeeId).map((e: any) => <option key={e.id} value={e.id}>{empLabel(e)}</option>)}</Select>
                : <><Input list={`opts-${k}`} maxLength={120} value={nv[k] ?? current[k]} onChange={(e) => setNv({ ...nv, [k]: e.target.value })} /><datalist id={`opts-${k}`}>{opts(k).map((o) => <option key={o} value={o} />)}</datalist></>}</td>
            </tr>))}</tbody></table></div>
        <FieldError msg={show("values") || show("needs") || show("manager")} />
      </FormSection>}
      <FormSection title="Why">
        <FormGrid cols={3}>
          <div><Field label="Reason *" hint="Short business reason, up to 200 characters"><Input value={reason} maxLength={200} onChange={(e) => setReason(e.target.value)} placeholder="Annual review 2026" /></Field><FieldError msg={show("reason")} /></div>
          <Field label="Linked salary revision (optional)" hint={employeeId && !liveRevisions.length ? "No open salary revision for this person" : "Ties the pay change to this job change in the history"}>
            <Select value={linkedRevision} disabled={!liveRevisions.length} onChange={(e) => setLinkedRevision(e.target.value)}>
              <option value="">— none —</option>
              {liveRevisions.map((r: any) => <option key={r.id} value={r.id}>{fmtDate(r.effective_from)} · {cur(r.old, r.currency)} → {cur(r.new, r.currency)} · {r.status}</option>)}
            </Select></Field>
          <div><Field label="Note"><Textarea rows={2} maxLength={1000} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Anything the approver should know" /></Field><FieldError msg={show("note")} /></div>
        </FormGrid>
      </FormSection>
      <p className="text-xs text-gray-500">A salary change is a separate record. Open <Link className="underline" to="/salary-revisions">Salary revisions</Link>, pick the reason Promotion, then link it here.</p>
    </Modal>
  );
}

function ChangeView({ row, onClose, onDone }: { row: any; onClose: () => void; onDone: () => void }) {
  const act = useMutation({ mutationFn: ({ a, note }: { a: "submit" | "approve" | "reject" | "cancel"; note?: string }) => careerApi.act(row.id, a, note),
    onSuccess: (r: any) => { toast.success(r.status === "applied" ? "Approved and applied to the employee" : r.status === "approved" ? "Approved. It will be applied on the effective date." : `Change ${r.status}`); onDone(); },
    onError: (e) => toast.error(errText(e)) });
  const letter = useMutation({ mutationFn: () => careerApi.letter(row.id), onSuccess: async (r: any) => { toast.success(`Letter ${r.number} issued`); openPdf(await lettersApi.pdf(r.letter_id), `${r.number}.pdf`); onDone(); }, onError: (e) => toast.error(errText(e)) });
  const s = row.status;
  const show = (vals: any, k: string) => (k === "manager_id" ? vals?.manager_name : vals?.[k]) || "—";
  return (
    <Modal title={`${row.employee} · ${row.type_label}`} size="lg" onClose={onClose} subtitle={`Effective ${fmtDate(row.effective_date)}${row.reason ? ` · ${row.reason}` : ""}`}
      footer={<>
        {s === "draft" && <button className="btn-primary" onClick={() => act.mutate({ a: "submit" })}>Submit for approval</button>}
        {s === "submitted" && <><button className="btn-secondary text-red-600" onClick={() => { const n = window.prompt("Reason for rejecting"); if (n) act.mutate({ a: "reject", note: n }); }}>Reject</button><button className="btn-primary" onClick={() => act.mutate({ a: "approve" })}>Approve</button></>}
        {["draft", "submitted", "approved"].includes(s) && <button className="btn-secondary" onClick={() => { if (window.confirm("Withdraw this change? It will not be applied.")) act.mutate({ a: "cancel" }); }}>Withdraw</button>}
        {["approved", "applied"].includes(s) && ["promotion", "transfer", "location_change", "department_change"].includes(row.change_type) && <button className="btn-secondary" disabled={letter.isPending} onClick={() => letter.mutate()}>Issue letter</button>}
      </>}>
      <div className="grid sm:grid-cols-3 gap-2 mb-3">
        <Stat label="Status" value={<Status s={s} />} sub={row.applied_at ? `Applied ${fmtDate(row.applied_at)}` : undefined} />
        <Stat label="Created by" value={<span className="text-sm">{row.created_by || "—"}</span>} sub={fmtDate(row.created_at)} />
        <Stat label="Approved by" value={<span className="text-sm">{row.decided_by || "—"}</span>} sub={row.decided_at ? fmtDate(row.decided_at) : undefined} />
      </div>
      <div className="card !p-0"><table className="w-full text-sm">
        <thead className="bg-surface text-gray-400 text-[10px] uppercase"><tr><th className="px-3 py-2 text-left">Field</th><th className="px-3 py-2 text-left">Old</th><th className="px-3 py-2 text-left">New</th></tr></thead>
        <tbody>{Object.keys(row.new_values || {}).filter((k) => LABEL[k]).map((k) => (
          <tr key={k} className="border-t border-surface-border"><td className="px-3 py-1.5 text-gray-600">{LABEL[k]}</td><td className="px-3 py-1.5">{show(row.old_values, k)}</td><td className="px-3 py-1.5 font-semibold">{show(row.new_values, k)}</td></tr>))}</tbody></table></div>
      {row.note && <p className="text-sm text-gray-600 mt-3"><b>Note:</b> {row.note}</p>}
      {row.decision_note && <p className="text-sm text-gray-600 mt-1"><b>Decision note:</b> {row.decision_note}</p>}
      {s === "applied" && <p className="text-xs text-gray-500 mt-3">This change is applied. To reverse it, create a new career change.</p>}
    </Modal>
  );
}
