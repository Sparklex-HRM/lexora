import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import toast from "react-hot-toast";
import { mastersApi, money } from "../../api/endpoints";
import { EMP_TYPES, err, fmtDate, hiringApi, Row } from "./api";
import { Col, empLabel, Field, FirstStep, Grid, Input, Modal, Pill, ReasonDialog, Select, Textarea, useEmployees } from "./ui";
import { FormGrid, FormSection } from "../../components/Modal";
import { v, collect, firstError, FieldError, Req } from "../../lib/validators";

const ACTION_LABEL: Record<string, string> = {
  submit: "Send for approval", approve: "Approve", reject: "Send back", hold: "Put on hold", resume: "Resume",
  cancel: "Cancel requisition", reopen: "Reopen",
};
const NEEDS_REASON = ["reject", "hold", "cancel", "reopen"];

export function useMasterNames(kind: "departments" | "designations" | "locations") {
  const { data } = useQuery({ queryKey: ["masters", kind], queryFn: () => mastersApi.list(kind), retry: false });
  return (data || []).map((r) => r.name).filter(Boolean) as string[];
}
export const DataList = ({ id, items }: { id: string; items: string[] }) =>
  <datalist id={id}>{items.map((n) => <option key={n} value={n} />)}</datalist>;

export default function Requisitions({ canCreate }: { canCreate: boolean }) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["hire", "reqs"], queryFn: hiringApi.requisitions });
  const [edit, setEdit] = useState<Row | "new" | null>(null);
  const [ask, setAsk] = useState<{ row: Row; action: string } | null>(null);
  const [status, setStatus] = useState("");
  const rows = (data || []).filter((r) => !status || r.status === status);

  const act = useMutation({
    mutationFn: (v: { id: string; action: string; reason?: string }) => hiringApi.actRequisition(v.id, v.action, v.reason),
    onSuccess: (r, v) => { toast.success(`${r.req_number}: ${ACTION_LABEL[v.action].toLowerCase()} done. Status is now ${String(r.status).replace(/_/g, " ")}.`); qc.invalidateQueries({ queryKey: ["hire"] }); setAsk(null); },
    onError: (e) => toast.error(err(e)),
  });
  const run = (row: Row, action: string) => (NEEDS_REASON.includes(action) ? setAsk({ row, action }) : act.mutate({ id: row.id, action }));

  const cols: Col<Row>[] = [
    { k: "req_number", label: "Requisition" },
    { k: "designation", label: "Designation" },
    { k: "department", label: "Department" },
    { k: "location", label: "Location" },
    { k: "grade", label: "Grade" },
    { k: "employment_type", label: "Type", render: (r) => EMP_TYPES[r.employment_type] || r.employment_type, csv: (r) => EMP_TYPES[r.employment_type] || r.employment_type },
    { k: "openings", label: "Openings", align: "right" },
    { k: "offers_accepted", label: "Offers accepted", align: "right" },
    { k: "joined", label: "Joined", align: "right", render: (r) => <span className={r.joined >= r.openings ? "text-green-600 font-semibold" : ""}>{r.joined} of {r.openings}</span>, csv: (r) => r.joined },
    { k: "in_pipeline", label: "In pipeline", align: "right" },
    { k: "reason_type", label: "Reason", render: (r) => (r.reason_type === "replacement" ? `Replaces ${r.replaces_employee_name || ""}` : "New position"), csv: (r) => (r.reason_type === "replacement" ? `Replaces ${r.replaces_employee_name || ""}` : "New position") },
    { k: "budget_max", label: "Budget (annual)", align: "right", render: (r) => (r.budget_max != null ? `${r.budget_min != null ? money(r.budget_min) + " to " : "up to "}${money(r.budget_max)}` : ""), csv: (r) => r.budget_max ?? "" },
    { k: "hiring_manager_name", label: "Hiring manager" },
    { k: "target_date", label: "Target date", render: (r) => fmtDate(r.target_date) },
    { k: "status", label: "Status", render: (r) => <Pill s={r.status} label={r.status === "pending_approval" ? `Waiting: ${r.approval_step === "manager" ? "hiring manager" : "HR"}` : undefined} />, csv: (r) => r.status },
    { k: "_a", label: "Actions", csv: () => "", render: (r) => (
      <div className="flex gap-1" onClick={(e) => e.stopPropagation()}>
        {r.can?.edit && <button className="btn-secondary !py-0.5 !px-2 text-[11px]" onClick={() => setEdit(r)}>Edit</button>}
        {Object.keys(ACTION_LABEL).filter((a) => r.can?.[a]).map((a) => (
          <button key={a} className={`${a === "approve" || a === "submit" ? "btn-primary" : "btn-secondary"} !py-0.5 !px-2 text-[11px]`} onClick={() => run(r, a)}>{ACTION_LABEL[a]}</button>
        ))}
      </div>) },
  ];

  return (
    <div>
      {!isLoading && !(data || []).length ? (
        <FirstStep title="No requisitions yet">
          A requisition is the approved request to hire. Raise one, send it for approval, and once it is Open you can post the job and add candidates.
          {canCreate && <div className="mt-4"><button className="btn-primary inline-flex items-center gap-1.5" onClick={() => setEdit("new")}><Plus className="w-4 h-4" />Raise a requisition</button></div>}
        </FirstStep>
      ) : (
        <Grid rows={rows} cols={cols} keyOf={(r) => r.id} loading={isLoading} exportName="requisitions.csv" title="Requisitions"
          empty="No requisition matches this filter."
          filters={<select className="input !py-1.5 text-xs !w-40" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option>
            {["draft", "pending_approval", "open", "on_hold", "filled", "cancelled"].map((s) => <option key={s} value={s}>{s.replace(/_/g, " ")}</option>)}
          </select>}
          actions={canCreate && <button className="btn-primary !py-1 !px-2.5 text-xs inline-flex items-center gap-1" onClick={() => setEdit("new")}><Plus className="w-3.5 h-3.5" />Raise a requisition</button>} />
      )}
      {edit && <RequisitionForm row={edit === "new" ? null : edit} onClose={() => setEdit(null)} />}
      {ask && <ReasonDialog title={`${ACTION_LABEL[ask.action]}: ${ask.row.req_number}`} hint="The reason is kept on the requisition."
        confirm={ACTION_LABEL[ask.action]} onClose={() => setAsk(null)} onConfirm={(reason) => act.mutate({ id: ask.row.id, action: ask.action, reason })} />}
    </div>
  );
}

function RequisitionForm({ row, onClose }: { row: Row | null; onClose: () => void }) {
  const qc = useQueryClient();
  const employees = useEmployees();
  const departments = useMasterNames("departments"), designations = useMasterNames("designations"), locations = useMasterNames("locations");
  const limited = !!row && row.status !== "draft";       // open / on hold: only openings, target date, notes
  const [f, setF] = useState<Row>(row ? { ...row } : { employment_type: "full_time", openings: 1, reason_type: "new" });
  const set = (k: string, v: any) => setF((p) => ({ ...p, [k]: v }));
  const [shown, setShown] = useState(false);              // show errors after the first Save press
  const todayLocal = new Date().toLocaleDateString("en-CA");
  const errs = collect({
    designation: v.required(f.designation, "Designation") || v.maxLen(f.designation, 120, "Designation"),
    department: v.maxLen(f.department, 120, "Department"),
    grade: v.maxLen(f.grade, 60, "Grade"),
    location: v.maxLen(f.location, 120, "Location"),
    openings: v.required(f.openings, "Number of openings") || v.range(f.openings, 1, 999, "Number of openings")
      || (Number.isInteger(Number(f.openings)) ? null : "Number of openings must be a whole number"),
    replaces_employee_id: f.reason_type === "replacement" && !limited ? v.required(f.replaces_employee_id, "The employee being replaced") : null,
    budget_min: v.range(f.budget_min, 0, undefined, "Budget minimum"),
    budget_max: v.range(f.budget_max, 0, undefined, "Budget maximum")
      || (f.budget_min !== "" && f.budget_min != null && f.budget_max !== "" && f.budget_max != null && Number(f.budget_max) < Number(f.budget_min) ? "Budget maximum cannot be less than the minimum" : null),
    target_date: (!row || f.target_date !== row.target_date) ? v.dateOrder(todayLocal, f.target_date, "Target date cannot be in the past") : null,
  });
  const er = (k: string) => (shown ? errs[k] : undefined);
  const trySave = () => { setShown(true); if (Object.keys(errs).length) return void toast.error(firstError(errs)); save.mutate(); };
  const save = useMutation({
    mutationFn: () => {
      const body: Row = limited ? { openings: f.openings, target_date: f.target_date || null, notes: f.notes || "" } : {
        designation: f.designation, department: f.department, grade: f.grade, location: f.location, employment_type: f.employment_type,
        openings: f.openings, reason_type: f.reason_type, replaces_employee_id: f.replaces_employee_id || null,
        budget_min: f.budget_min === "" ? null : f.budget_min, budget_max: f.budget_max === "" ? null : f.budget_max,
        hiring_manager_id: f.hiring_manager_id || null, target_date: f.target_date || null, notes: f.notes || "",
      };
      return row ? hiringApi.updateRequisition(row.id, body) : hiringApi.createRequisition(body);
    },
    onSuccess: (r) => { toast.success(`${r.req_number} saved as ${String(r.status).replace(/_/g, " ")}.`); qc.invalidateQueries({ queryKey: ["hire"] }); onClose(); },
    onError: (e) => toast.error(err(e)),
  });
  return (
    <Modal title={row ? `Edit ${row.req_number}` : "Raise a requisition"} subtitle={limited ? "This requisition is approved: HR can change only the openings, the target date and the notes." : "Saved as a draft. Send it for approval from the list."} onClose={onClose}
      footer={<><button className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary disabled:opacity-50" disabled={save.isPending} onClick={trySave}>Save</button></>}>
      <FormSection title="Role">
        <FormGrid cols={3}>
          <Field label={<>Designation<Req /></>}><Input list="rq-des" maxLength={120} disabled={limited} value={f.designation || ""} onChange={(e) => set("designation", e.target.value)} /><FieldError msg={er("designation")} /></Field>
          <Field label="Department"><Input list="rq-dep" maxLength={120} disabled={limited} value={f.department || ""} onChange={(e) => set("department", e.target.value)} /><FieldError msg={er("department")} /></Field>
          <Field label="Grade" hint="Internal pay / seniority band this role maps to."><Input maxLength={60} disabled={limited} value={f.grade || ""} onChange={(e) => set("grade", e.target.value)} /><FieldError msg={er("grade")} /></Field>
          <Field label="Location"><Input list="rq-loc" maxLength={120} disabled={limited} value={f.location || ""} onChange={(e) => set("location", e.target.value)} /><FieldError msg={er("location")} /></Field>
          <Field label="Employment type"><Select disabled={limited} value={f.employment_type} onChange={(e) => set("employment_type", e.target.value)}>
            {Object.entries(EMP_TYPES).map(([k, lbl]) => <option key={k} value={k}>{lbl}</option>)}</Select></Field>
          <Field label="Hiring manager" hint="Approves first when your setting asks for it."><Select disabled={limited} value={f.hiring_manager_id || ""} onChange={(e) => set("hiring_manager_id", e.target.value)}>
            <option value="">Pick an employee</option>{employees.map((x) => <option key={x.id} value={x.id}>{empLabel(x)}</option>)}</Select></Field>
        </FormGrid>
      </FormSection>
      <FormSection title="Headcount and reason">
        <FormGrid cols={3}>
          <Field label={<>Number of openings<Req /></>}><Input type="number" min={1} max={999} step={1} value={f.openings ?? ""} onChange={(e) => set("openings", e.target.value)} /><FieldError msg={er("openings")} /></Field>
          <Field label="Reason" hint="Replacement backfills a named employee; New adds to the headcount."><Select disabled={limited} value={f.reason_type} onChange={(e) => set("reason_type", e.target.value)}>
            <option value="new">New position</option><option value="replacement">Replacement</option></Select></Field>
          {f.reason_type === "replacement" && (
            <Field label={<>Replaces<Req /></>}><Select disabled={limited} value={f.replaces_employee_id || ""} onChange={(e) => set("replaces_employee_id", e.target.value)}>
              <option value="">Pick an employee</option>{employees.map((x) => <option key={x.id} value={x.id}>{empLabel(x)}</option>)}</Select><FieldError msg={er("replaces_employee_id")} /></Field>
          )}
        </FormGrid>
      </FormSection>
      <FormSection title="Budget" hint="Annual salary in Canadian dollars. An offer above the maximum needs a second approver.">
        <FormGrid cols={3}>
          <Field label="Budget minimum ($ a year)" hint="Lowest annual salary you plan to offer."><Input type="number" min={0} className="text-right tabular-nums" disabled={limited} value={f.budget_min ?? ""} onChange={(e) => set("budget_min", e.target.value)} /><FieldError msg={er("budget_min")} /></Field>
          <Field label="Budget maximum ($ a year)"><Input type="number" min={0} className="text-right tabular-nums" disabled={limited} value={f.budget_max ?? ""} onChange={(e) => set("budget_max", e.target.value)} /><FieldError msg={er("budget_max")} /></Field>
        </FormGrid>
      </FormSection>
      <FormSection title="Timeline and notes">
        <FormGrid cols={3}>
          <Field label="Target date" hint="When you want the person to join by."><Input type="date" min={todayLocal} value={f.target_date || ""} onChange={(e) => set("target_date", e.target.value)} /><FieldError msg={er("target_date")} /></Field>
          <div className="sm:col-span-2"><Field label="Notes" hint="Role scope, key skills, experience and qualification you are looking for."><Textarea rows={3} value={f.notes || ""} onChange={(e) => set("notes", e.target.value)} /></Field></div>
        </FormGrid>
      </FormSection>
      <DataList id="rq-des" items={designations} /><DataList id="rq-dep" items={departments} /><DataList id="rq-loc" items={locations} />
      {!employees.length && <p className="text-xs text-amber-700 mt-3">There are no employees yet, so no hiring manager can be picked. Add employees first, or leave it empty: HR then approves alone.</p>}
    </Modal>
  );
}
