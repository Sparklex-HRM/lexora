import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { UserRoundCheck, Users } from "lucide-react";
import { PageHeader, Spinner } from "../../components/ui";
import DataGrid, { type GridColumn } from "../../components/DataGrid";
import { exitApi, errText, dmy, type FormerEmployee } from "../../api/exit";
import { WideModal } from "./StartExitModal";
import { FormSection } from "../../components/Modal";
import { employeeFormApi } from "../../api/identity";
import { regionOptions } from "../../api/regions";
import { v, collect, firstError, FieldError, Req } from "../../lib/validators";   // forms wrap-up 2026-10-24

/** Former employees — searchable alumni list with exit date, reason, the rehire flag,
 *  and "Rehire" (a new employment spell on the same person record). */

const L = "block text-xs font-semibold text-gray-500";

export default function FormerEmployeesPage() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["former-employees"], queryFn: () => exitApi.former() });
  const [flag, setFlag] = useState("");
  const [rehire, setRehire] = useState<FormerEmployee | null>(null);
  const [view, setView] = useState<FormerEmployee | null>(null);
  const rows = (list.data?.former_employees || []).filter((r) => !flag || (flag === "yes" ? r.rehire_eligible === true : flag === "no" ? r.rehire_eligible === false : r.rehire_eligible == null));
  const setEligible = useMutation({
    mutationFn: (v: { id: string; eligible: boolean | null; note?: string }) => exitApi.formerFlag(v.id, v.eligible, v.note),
    onSuccess: () => { toast.success("Rehire flag saved"); qc.invalidateQueries({ queryKey: ["former-employees"] }); setView(null); }, onError: (e) => toast.error(errText(e)),
  });
  const columns: GridColumn<FormerEmployee>[] = [
    { key: "name", label: "Name", sortable: true, render: (r) => <span><span className="font-medium text-gray-800">{r.name}</span> <span className="badge-purple ml-1">Former employee</span></span>, text: (r) => r.name },
    { key: "employee_number", label: "Number", sortable: true },
    { key: "department", label: "Department", sortable: true },
    { key: "job_title", label: "Last role", sortable: true },
    { key: "hire_date", label: "Joined", type: "date", sortable: true },
    { key: "exit_date", label: "Exit date", type: "date", sortable: true },
    { key: "exit_type_label", label: "Exit type", sortable: true },
    { key: "exit_reason", label: "Reason", sortable: true },
    { key: "rehire_eligible", label: "Eligible for rehire", sortable: true, render: (r) => r.rehire_eligible == null ? <span className="text-gray-400">not set</span> : r.rehire_eligible ? <span className="badge-green">yes</span> : <span className="badge-red">no</span>, text: (r) => (r.rehire_eligible == null ? "not set" : r.rehire_eligible ? "yes" : "no") },
    { key: "rehire_note", label: "Note" },
    { key: "spell_count", label: "Spells", type: "number", sortable: true },
    { key: "act", label: "", render: (r) => (
      <span className="whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
        {r.case_id && <button className="btn-secondary !py-1 text-xs mr-1" onClick={() => nav(`/exits/${r.case_id}`)}>Exit case</button>}
        {list.data?.can_rehire && <button className="btn-primary !py-1 text-xs" disabled={r.rehire_eligible === false} title={r.rehire_eligible === false ? "Marked not eligible for rehire" : "Start a new employment spell"} onClick={() => setRehire(r)}>Rehire</button>}
      </span>), text: () => "" },
  ];
  return (
    <div>
      <PageHeader title="Former employees" icon={Users} subtitle="People who have left. Their records, payslips and tax forms stay on file. Rehire starts a new employment spell on the same record." />
      {list.isLoading ? <div className="grid place-items-center py-16"><Spinner /></div> : (
        <div className="card !p-0">
          <DataGrid<FormerEmployee> columns={columns} rows={rows} rowKey={(r) => r.id} exportName="former-employees" minWidth={1200}
            searchPlaceholder="Search name, number, department" empty="No former employees." initialSort={{ key: "exit_date", dir: "desc" }}
            onRowClick={(r) => setView(r)}
            filters={<select className="input !py-1 !w-auto text-sm" value={flag} onChange={(e) => setFlag(e.target.value)}><option value="">Rehire: any</option><option value="yes">Eligible</option><option value="no">Not eligible</option><option value="unset">Not set</option></select>} />
        </div>)}
      {view && <ViewModal r={view} canEdit={!!list.data?.can_rehire} busy={setEligible.isPending} onClose={() => setView(null)} onSet={(eligible, note) => setEligible.mutate({ id: view.id, eligible, note })} />}
      {rehire && <RehireModal r={rehire} policy={list.data?.rehire_number_policy || "keep"} onClose={() => setRehire(null)} onDone={() => { setRehire(null); qc.invalidateQueries({ queryKey: ["former-employees"] }); qc.invalidateQueries({ queryKey: ["employees"] }); }} />}
    </div>
  );
}

function ViewModal({ r, canEdit, busy, onClose, onSet }: { r: FormerEmployee; canEdit: boolean; busy: boolean; onClose: () => void; onSet: (eligible: boolean | null, note?: string) => void }) {
  const [note, setNote] = useState(r.rehire_note || "");
  return (
    <WideModal title={`${r.name} · ${r.employee_number} · Former employee`} onClose={onClose}>
      <div className="grid md:grid-cols-2 gap-5">
        <div>
          <h4 className="text-sm font-semibold text-gray-800 mb-2">Employment history</h4>
          <table className="w-full text-xs"><thead className="text-left text-[11px] uppercase text-gray-400"><tr><th className="py-1">Spell</th><th>Number</th><th>Joined</th><th>Left</th><th>Why</th></tr></thead>
            <tbody>{(r.spells.length ? r.spells : [{ spell_no: 1, employee_number: r.employee_number, hire_date: r.hire_date, exit_date: r.exit_date, exit_type_label: r.exit_type_label, exit_reason: r.exit_reason }]).map((s) => (
              <tr key={s.spell_no} className="border-t border-surface-border"><td className="py-1.5">{s.spell_no}</td><td>{s.employee_number || "—"}</td><td>{dmy(s.hire_date)}</td><td>{dmy(s.exit_date)}</td><td>{[s.exit_type_label, s.exit_reason].filter(Boolean).join(" · ") || "—"}</td></tr>))}</tbody></table>
        </div>
        <div className="space-y-3">
          <h4 className="text-sm font-semibold text-gray-800">Eligible for rehire</h4>
          <p className="text-sm text-gray-700">{r.rehire_eligible == null ? "Not set." : r.rehire_eligible ? "Yes." : "No."}</p>
          {canEdit && (<>
            <label className={L}>Note<input className="input mt-1" value={note} onChange={(e) => setNote(e.target.value)} /></label>
            <div className="flex gap-2"><button className="btn-secondary" disabled={busy} onClick={() => onSet(true, note)}>Eligible</button><button className="btn-secondary" disabled={busy} onClick={() => onSet(false, note)}>Not eligible</button></div>
          </>)}
        </div>
      </div>
    </WideModal>
  );
}

function RehireModal({ r, policy, onClose, onDone }: { r: FormerEmployee; policy: string; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ new_hire_date: "", new_employee_number: "", job_title: r.job_title || "", department: r.department || "", annual_salary: "", note: "",
    employment_type: "", work_location: "", grade: "", province_of_employment: "", probation_end_date: "", manager_id: "" });
  const [errs, setErrs] = useState<Record<string, string>>({});
  const opts = useQuery({ queryKey: ["employees", "form-options"], queryFn: employeeFormApi.options, staleTime: 60_000 });
  const [done, setDone] = useState<any>(null);
  const go = useMutation({
    mutationFn: () => exitApi.rehire({ employee_id: r.id, new_hire_date: f.new_hire_date, new_employee_number: f.new_employee_number.trim() || undefined,
      job_title: f.job_title.trim() || undefined, department: f.department.trim() || undefined, annual_salary: f.annual_salary === "" ? undefined : Number(f.annual_salary), note: f.note.trim() || undefined,
      employment_type: f.employment_type || undefined, work_location: f.work_location.trim() || undefined, grade: f.grade || undefined,
      province_of_employment: f.province_of_employment || undefined, probation_end_date: f.probation_end_date || undefined, manager_id: f.manager_id || undefined }),
    onSuccess: (res) => { toast.success(`${r.name} rehired`); setDone(res); }, onError: (e) => toast.error(errText(e, "Could not rehire")),
  });
  const submit = () => {
    const found = collect({
      new_hire_date: v.required(f.new_hire_date, "New joining date")
        || (r.exit_date && f.new_hire_date <= r.exit_date ? `The new joining date must be after the exit date (${dmy(r.exit_date)})` : null),
      new_employee_number: policy === "new" ? (v.required(f.new_employee_number, "New employee number") || v.maxLen(f.new_employee_number.trim(), 20, "Employee number")
        || (/\s/.test(f.new_employee_number.trim()) ? "Employee number cannot contain spaces" : null)) : null,
      job_title: v.maxLen(f.job_title, 100, "Role"), department: v.maxLen(f.department, 100, "Department"),
      annual_salary: v.range(f.annual_salary, 0, undefined, "Annual salary"),
      probation_end_date: v.dateOrder(f.new_hire_date, f.probation_end_date, "Probation cannot end before the new joining date"),
      note: v.maxLen(f.note, 500, "Note"),
    });
    setErrs(found);
    if (Object.keys(found).length) return toast.error(firstError(found));
    go.mutate();
  };
  if (done) return (
    <WideModal title={`${r.name} is back – spell ${done.spell_no}`} onClose={onDone}>
      <p className="text-sm text-gray-700 mb-2 inline-flex items-center gap-1.5"><UserRoundCheck className="w-4 h-4 text-green-600" />Employee number {done.employee_number}. Joining date {dmy(done.hire_date)}. The earlier spell is kept on file.</p>
      <ul className="list-disc pl-5 text-sm text-gray-600 space-y-1">{(done.next_steps || []).map((s: string, i: number) => <li key={i}>{s}</li>)}</ul>
      <div className="flex justify-end mt-4"><button className="btn-primary" onClick={onDone}>Done</button></div>
    </WideModal>);
  return (
    <WideModal title={`Rehire ${r.name}`} onClose={onClose}>
      <div className="rounded-lg bg-surface p-3 text-xs text-gray-600 mb-4">Left on <b>{dmy(r.exit_date)}</b>{r.exit_type_label ? ` (${r.exit_type_label})` : ""}. This starts a new employment spell on the same record: the SIN stays the same, and pay already made in this calendar year stays counted toward CPP, EI and the T4. Blank fields keep what the record has.</div>
      <div className="grid md:grid-cols-2 gap-x-5">
        <FormSection title="New spell">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <label className={L}>New joining date<Req /><input type="date" className="input mt-1" value={f.new_hire_date} min={r.exit_date || undefined} onChange={(e) => setF({ ...f, new_hire_date: e.target.value })} /><FieldError msg={errs.new_hire_date} />
              <span className="block text-[10px] font-normal text-gray-400 mt-0.5">Must be after the last working day.</span></label>
            {policy === "new" ? <label className={L}>New employee number<Req /><input className="input mt-1" maxLength={20} value={f.new_employee_number} onChange={(e) => setF({ ...f, new_employee_number: e.target.value })} /><FieldError msg={errs.new_employee_number} />
              <span className="block text-[10px] font-normal text-gray-400 mt-0.5">Company setting: a fresh number for each spell.</span></label>
              : <p className="text-xs text-gray-500 sm:pt-5">Employee number stays <b>{r.employee_number}</b> (company setting).</p>}
            <label className={L}>Employment type<select className="input mt-1" value={f.employment_type} onChange={(e) => setF({ ...f, employment_type: e.target.value })}>
              <option value="">Keep as before</option>{(opts.data?.employment_types || ["full_time", "part_time", "contract", "fixed_term", "seasonal"]).map((t) => <option key={t} value={t}>{t.replace("_", " ")}</option>)}</select></label>
            <label className={L}>Probation ends<input type="date" className="input mt-1" min={f.new_hire_date || undefined} value={f.probation_end_date} onChange={(e) => setF({ ...f, probation_end_date: e.target.value })} /><FieldError msg={errs.probation_end_date} />
              <span className="block text-[10px] font-normal text-gray-400 mt-0.5">Blank = no probation on this spell.</span></label>
          </div>
        </FormSection>
        <FormSection title="Role and pay">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <label className={L}>Role<input className="input mt-1" maxLength={100} value={f.job_title} onChange={(e) => setF({ ...f, job_title: e.target.value })} /><FieldError msg={errs.job_title} /></label>
            <label className={L}>Department<input className="input mt-1" maxLength={100} value={f.department} onChange={(e) => setF({ ...f, department: e.target.value })} /><FieldError msg={errs.department} /></label>
            <label className={L}>Grade<select className="input mt-1" value={f.grade} onChange={(e) => setF({ ...f, grade: e.target.value })}>
              <option value="">Keep as before</option>{(opts.data?.grades || []).map((g) => <option key={g.name} value={g.name}>{g.name}</option>)}</select></label>
            <label className={L}>Reports to<select className="input mt-1" value={f.manager_id} onChange={(e) => setF({ ...f, manager_id: e.target.value })}>
              <option value="">Keep as before</option>{(opts.data?.managers || []).filter((m) => m.id !== r.id).map((m) => <option key={m.id} value={m.id}>{m.name}{m.employee_number ? ` · ${m.employee_number}` : ""}</option>)}</select></label>
            <label className={`${L} sm:col-span-2`}>Annual salary ($)<input type="number" min={0} className="input mt-1 text-right" value={f.annual_salary} onChange={(e) => setF({ ...f, annual_salary: e.target.value })} /><FieldError msg={errs.annual_salary} />
              <span className="block text-[10px] font-normal text-gray-400 mt-0.5">Leave blank to keep the salary from the earlier spell. Change it later in Salary revisions.</span></label>
          </div>
        </FormSection>
        <FormSection title="Where">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <label className={L}>Province of employment<select className="input mt-1" value={f.province_of_employment} onChange={(e) => setF({ ...f, province_of_employment: e.target.value })}>
              <option value="">Keep as before</option>{regionOptions().map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</select>
              <span className="block text-[10px] font-normal text-gray-400 mt-0.5">Decides provincial tax, employment standards and stat holidays for the new spell.</span></label>
            <label className={L}>Work location<input className="input mt-1" maxLength={120} value={f.work_location} onChange={(e) => setF({ ...f, work_location: e.target.value })} /></label>
          </div>
        </FormSection>
        <FormSection title="Note">
          <label className={L}>Note (kept in the audit log)<input className="input mt-1" maxLength={500} value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /><FieldError msg={errs.note} /></label>
        </FormSection>
      </div>
      <div className="flex items-center justify-end gap-2 mt-2">
        <button className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={go.isPending} onClick={submit}>{go.isPending ? "Saving…" : "Rehire"}</button></div>
    </WideModal>
  );
}
