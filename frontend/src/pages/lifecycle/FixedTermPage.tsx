import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { CalendarClock, Plus, History, BellRing, Info } from "lucide-react";
import { PageHeader } from "../../components/ui";
import { HR_WRITE } from "../../store/authStore";
import api from "../../api/client";
import { Grid, Modal, Field, Input, Select, Textarea, Stat, today, errMsg, useEmployees, EmployeePicker } from "../common/_shared";
import { Link } from "react-router-dom";
import { FormGrid, FormSection } from "../../components/Modal";
import { v, collect, firstError, FieldError } from "../../lib/validators";
import { useCan } from "../../config/permissions";   // 2026-10-26: UI gates = permission + role (custom roles)

/* Fixed-term contracts (2026-10-22) — direct employees on a contract / fixed-term.
   HR (org admin, HR admin, HR manager) sets the term and acts: renew, convert to permanent, end on expiry (→ exit case).
   A manager sees only direct reports and records a recommendation. Alerts go out at 60 / 30 / 7 / 0 days. */

const BUCKETS: [string, string][] = [["expired", "Ended — no decision"], ["7", "Ends in 7 days"], ["30", "Ends in 30 days"], ["60", "Ends in 60 days"], ["90", "Ends in 90 days"], ["later", "Later"]];
const REC: Record<string, string> = { renew: "Renew", convert: "Make permanent", end: "End on expiry" };
type Act = "set" | "recommend" | "renew" | "convert" | "end";
export interface FixedTermRow {
  employee_id: string; employee: string; employee_number: string; job_title: string | null; department: string | null; work_location: string | null;
  manager_id: string | null; manager_name: string | null; employment_type: string; status: string; hire_date: string | null;
  contract_start_date: string | null; contract_end_date: string | null; contract_notice_days: number | null; contract_ref: string | null;
  notice_by: string | null; notice_overdue: boolean; days_left: number | null; bucket: string; term_no: number; renewals: number;
  recommendation: string | null; recommendation_by: string | null; recommendation_note: string | null; recommended_end_date: string | null;
  last_action: string | null; exit_handover: string | null; service_days_at_end: number | null;
}
interface FixedTermList { today: string; rows: FixedTermRow[]; counts: Record<string, number>; can_act: boolean; rule_note: string; types: string[]; converted_last_90d: number }

/* Backend: employee service, /employees/fixed-term + /employees/{id}/fixed-term/* (contracts_routers.py). */
const E = "/employees";
const fixedTermApi = {
  list: () => api.get<FixedTermList>(`${E}/fixed-term`).then((r) => r.data),
  setTerm: (id: string, body: Record<string, any>) => api.post(`${E}/${id}/fixed-term`, body).then((r) => r.data),
  recommend: (id: string, body: Record<string, any>) => api.post(`${E}/${id}/fixed-term/recommend`, body).then((r) => r.data),
  renew: (id: string, body: Record<string, any>) => api.post(`${E}/${id}/fixed-term/renew`, body).then((r) => r.data),
  convert: (id: string, body: Record<string, any>) => api.post(`${E}/${id}/fixed-term/convert`, body).then((r) => r.data),
  end: (id: string, body: Record<string, any>) => api.post(`${E}/${id}/fixed-term/end`, body).then((r) => r.data),
  history: (id: string) => api.get<{ employee_id: string; employee: string; rows: any[] }>(`${E}/${id}/fixed-term/history`).then((r) => r.data),
  scan: () => api.post<{ sent: number }>(`${E}/fixed-term/alerts/scan`).then((r) => r.data),
};

const fmt = (d?: string | null) => (d ? new Date(d + "T00:00:00").toLocaleDateString("en-CA", { day: "2-digit", month: "short", year: "numeric" }) : "—");

export default function FixedTermPage() {
  const qc = useQueryClient();
  const isHr = useCan(["career.create", "career.edit"], HR_WRITE);
  const [bucket, setBucket] = useState("");
  const [act, setAct] = useState<{ a: Act; row: Partial<FixedTermRow> } | null>(null);
  const [hist, setHist] = useState<FixedTermRow | null>(null);
  const { data, isLoading } = useQuery({ queryKey: ["fixed-term"], queryFn: fixedTermApi.list });
  const rows = data?.rows || [];
  const counts: Record<string, number> = data?.counts || {};
  const shown = bucket ? rows.filter((r) => r.bucket === bucket) : rows;
  const refresh = () => qc.invalidateQueries({ queryKey: ["fixed-term"] });
  const scan = useMutation({ mutationFn: fixedTermApi.scan, onSuccess: (r) => toast.success(`${r.sent} reminder(s) sent`), onError: (e) => toast.error(errMsg(e)) });
  return (
    <div className="space-y-4">
      <PageHeader title="Fixed-term contracts" icon={CalendarClock}
        subtitle={isHr ? "Direct employees on a contract or fixed term: when each term ends, what the manager recommends, and renew / make permanent / end on expiry"
          : "Your team members on a fixed-term contract — add your recommendation before the end date"}
        actions={isHr ? <div className="flex flex-wrap gap-2">
          <button className="btn-secondary flex items-center gap-1" onClick={() => scan.mutate()} disabled={scan.isPending}><BellRing className="w-4 h-4" /> Send due reminders</button>
          <button className="btn-primary flex items-center gap-1" onClick={() => setAct({ a: "set", row: {} })}><Plus className="w-4 h-4" /> Put an employee on a fixed term</button></div> : undefined} />
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-7 gap-3">
        {BUCKETS.map(([k, l]) => <button key={k} className="text-left" onClick={() => setBucket(bucket === k ? "" : k)}>
          <Stat label={l} value={counts[k] ?? 0} accent={bucket === k} sub={k === "expired" && counts[k] ? "Act now" : undefined} /></button>)}
        <Stat label="Made permanent (90 days)" value={data?.converted_last_90d ?? 0} />
      </div>
      {data?.rule_note && <div className="card !p-3 text-[12px] text-gray-600 flex gap-2"><Info className="w-4 h-4 text-primary-600 shrink-0 mt-0.5" /><span>{data.rule_note}</span></div>}
      <Grid rows={shown} keyOf={(r) => r.employee_id} loading={isLoading} exportName="fixed_term_contracts.csv"
        empty={isHr ? "No one is on a fixed-term or contract term. Use “Put an employee on a fixed term”, or set the employment type and contract dates on the employee form." : "None of your direct reports is on a fixed-term contract."}
        cols={[
          { k: "employee", label: "Employee", render: (r) => <span className="font-medium text-gray-800">{r.employee}<span className="text-gray-400 ml-1">{r.employee_number}</span></span> },
          { k: "employment_type", label: "Type", render: (r) => <span className="badge-purple">{r.employment_type === "fixed_term" ? "fixed term" : "contract"}</span> },
          { k: "job_title", label: "Designation" }, { k: "department", label: "Department" }, { k: "manager_name", label: "Manager" },
          { k: "term_no", label: "Term", align: "center", render: (r) => `#${r.term_no}` },
          { k: "contract_start_date", label: "Term from", render: (r) => fmt(r.contract_start_date) },
          { k: "contract_end_date", label: "Term ends", render: (r) => <b>{fmt(r.contract_end_date)}</b> },
          { k: "days_left", label: "Days left", align: "right", render: (r) => r.days_left == null ? "—" : r.days_left < 0 ? <span className="text-red-600 font-semibold">ended {-r.days_left}d ago</span> : <span className={r.days_left <= 30 ? "text-amber-600 font-semibold" : ""}>{r.days_left}</span> },
          { k: "notice_by", label: "Tell by", render: (r) => r.notice_by ? <span className={r.notice_overdue ? "text-red-600" : ""} title="End date minus the notice days in the contract">{fmt(r.notice_by)}</span> : "—" },
          { k: "recommendation", label: "Manager says", csv: (r) => REC[r.recommendation || ""] || "", render: (r) => r.recommendation ? <span title={`${r.recommendation_by || ""}: ${r.recommendation_note || ""}`} className={r.recommendation === "convert" ? "badge-green" : r.recommendation === "renew" ? "badge-amber" : "badge-red"}>{REC[r.recommendation]}</span> : <span className="text-gray-300">Not yet</span> },
          { k: "actions", label: "", csv: () => "", render: (r) => <span className="flex flex-wrap gap-1">
            <button className="btn-secondary !py-0.5 !px-2 text-xs" onClick={() => setAct({ a: "recommend", row: r })}>Recommend</button>
            {isHr && <>
              <button className="btn-primary !py-0.5 !px-2 text-xs" onClick={() => setAct({ a: "renew", row: r })}>Renew</button>
              <button className="btn-secondary !py-0.5 !px-2 text-xs" onClick={() => setAct({ a: "convert", row: r })}>Make permanent</button>
              <button className="btn-secondary !py-0.5 !px-2 text-xs text-red-600" onClick={() => setAct({ a: "end", row: r })}>End on expiry</button>
              <button className="btn-secondary !py-0.5 !px-2 text-xs" onClick={() => setAct({ a: "set", row: r })}>Correct term</button></>}
            <button className="icon-btn" title="History" onClick={() => setHist(r)}><History className="w-3.5 h-3.5" /></button>
          </span> },
        ]} />
      {act && <ActionModal act={act.a} row={act.row} onClose={() => setAct(null)} onDone={() => { setAct(null); refresh(); }} />}
      {hist && <HistoryModal row={hist} onClose={() => setHist(null)} />}
    </div>
  );
}

const TITLES: Record<Act, string> = { set: "Fixed-term contract", recommend: "Recommend", renew: "Renew the contract", convert: "Make permanent", end: "End on expiry" };

function ActionModal({ act, row, onClose, onDone }: { act: Act; row: Partial<FixedTermRow>; onClose: () => void; onDone: () => void }) {
  const emps = useEmployees();
  const [empId, setEmpId] = useState(row.employee_id || "");
  const [tried, setTried] = useState(false);
  const [f, setF] = useState<any>({
    start_date: row.contract_start_date || "", end_date: row.contract_end_date || "", notice_days: row.contract_notice_days ?? "", contract_ref: row.contract_ref || "",
    employment_type: row.employment_type || "fixed_term", recommendation: "renew", reason: "", new_end_date: row.recommended_end_date || "", effective_date: today(), issue_letter: true,
    probation_months: "", last_working_date: row.contract_end_date || "",
  });
  const set = (k: string, val: any) => setF((s: any) => ({ ...s, [k]: val }));
  const id = empId;
  const picked: any = emps.find((e: any) => e.id === empId);
  const hire = String(row.hire_date || picked?.hire_date || "").slice(0, 10);
  const curEnd = String(row.contract_end_date || "").slice(0, 10);
  const curStart = String(row.contract_start_date || "").slice(0, 10);
  const after = (from: string, to: string, msg: string) => (from && to && to <= from ? msg : null);
  const reasonNeeded = act !== "set";
  const errs = collect({
    employee: act === "set" ? v.required(id, "Employee") : null,
    end_date: act === "set" ? (v.required(f.end_date, "Term end date") || after(f.start_date || hire, f.end_date, "The term must end after it starts")) : null,
    start_date: act === "set" ? v.dateOrder(hire, f.start_date, "The term cannot start before the joining date") : null,
    new_end_date: act === "renew" ? (v.required(f.new_end_date, "New end date") || after(curEnd, f.new_end_date, "The new end date must be after the current end date"))
      : act === "recommend" && f.recommendation === "renew" ? after(curEnd, f.new_end_date, "The proposed end date must be after the current end date") : null,
    notice_days: (act === "set" || act === "renew") ? (v.range(f.notice_days, 0, 365, "Notice days") || (f.notice_days !== "" && !Number.isInteger(Number(f.notice_days)) ? "Notice days must be a whole number" : null)) : null,
    contract_ref: v.maxLen(String(f.contract_ref || "").trim(), 80, "Contract reference"),
    effective_date: act === "convert" ? (v.dateOrder(hire, f.effective_date, "The permanent-from date cannot be before the joining date")
      || v.dateOrder(curStart, f.effective_date, "The permanent-from date cannot be before the current term started")) : null,
    probation_months: act === "convert" ? (v.range(f.probation_months, 0, 12, "Probation months") || (f.probation_months !== "" && !Number.isInteger(Number(f.probation_months)) ? "Probation months must be a whole number" : null)) : null,
    last_working_date: act === "end" ? (v.required(f.last_working_date, "Last working day") || v.dateOrder(hire, f.last_working_date, "The last working day cannot be before the joining date")) : null,
    reason: reasonNeeded ? (v.required(f.reason, "Reason") || (String(f.reason).trim().length < 3 ? "Write a few words for the reason" : null) || v.maxLen(f.reason, 1000, "Reason"))
      : v.maxLen(f.reason, 1000, "Note"),
  });
  const show = (k: string) => (tried ? errs[k] : undefined);
  const m = useMutation({
    mutationFn: async () => {
      const num = (x: any) => (x === "" || x == null ? undefined : Number(x));
      const ref = String(f.contract_ref || "").trim();
      const reason = String(f.reason || "").trim();
      if (act === "set") return fixedTermApi.setTerm(id, { start_date: f.start_date || undefined, end_date: f.end_date, notice_days: num(f.notice_days), contract_ref: ref, employment_type: f.employment_type, reason });
      if (act === "recommend") return fixedTermApi.recommend(id, { recommendation: f.recommendation, reason, new_end_date: (f.recommendation === "renew" && f.new_end_date) || undefined });
      if (act === "renew") return fixedTermApi.renew(id, { new_end_date: f.new_end_date, reason, notice_days: num(f.notice_days), contract_ref: ref || undefined, issue_letter: f.issue_letter });
      if (act === "convert") return fixedTermApi.convert(id, { effective_date: f.effective_date, reason, issue_letter: f.issue_letter, probation_months: num(f.probation_months) });
      return fixedTermApi.end(id, { reason, last_working_date: f.last_working_date || undefined });
    },
    onSuccess: (r: any) => {
      if (r?.letter_error) toast(`Done — the letter still needs issuing: ${r.letter_error}`);
      else if (act === "end") (r?.exit_started ? toast.success(r.message) : toast(r?.message || "Recorded"));
      else toast.success(act === "renew" ? `Renewed to ${fmt(r?.contract_end_date)}${r?.letter_id ? " — letter issued" : ""}` : act === "convert" ? "Now a permanent employee" : "Saved");
      onDone();
    },
    onError: (e) => toast.error(errMsg(e)),
  });
  const go = () => {
    setTried(true);
    if (Object.keys(errs).length) { toast.error(firstError(errs)); return; }
    m.mutate();
  };
  const termFacts = row.employee_id ? (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-3 text-[12px]">
      <div className="rounded-lg bg-surface px-3 py-2"><div className="text-[10px] uppercase text-gray-400">Current term</div><b>#{row.term_no ?? 1}</b> · {fmt(row.contract_start_date)} – {fmt(row.contract_end_date)}</div>
      <div className="rounded-lg bg-surface px-3 py-2"><div className="text-[10px] uppercase text-gray-400">Renewed so far</div><b>{row.renewals ?? 0}</b> time(s)</div>
      <div className="rounded-lg bg-surface px-3 py-2"><div className="text-[10px] uppercase text-gray-400">Joined</div>{fmt(row.hire_date)}</div>
      <div className="rounded-lg bg-surface px-3 py-2"><div className="text-[10px] uppercase text-gray-400">Tell by</div>{fmt(row.notice_by)}</div>
    </div>) : null;
  return (
    <Modal title={`${TITLES[act]}${row.employee ? ` — ${row.employee}` : ""}`} size="lg"
      subtitle={act === "end" ? "Starts an exit case of type “End of contract” with the last working day below; settle final pay and the ROE through the exit case."
        : act === "renew" ? "The new term starts the day after the current end date." : act === "convert" ? "Employment type becomes full time and the contract end date is cleared; service continues from the joining date." : undefined}
      onClose={onClose} footer={<><button className="btn-secondary" onClick={onClose}>Cancel</button><button className={act === "end" ? "btn-secondary text-red-600" : "btn-primary"} disabled={m.isPending} onClick={go}>{act === "end" ? "End and start exit" : "Save"}</button></>}>
      {termFacts}
      {(row.renewals ?? 0) >= 2 && (act === "renew" || act === "recommend") && <div className="text-xs rounded-lg px-3 py-2 bg-amber-50 text-amber-800 border border-amber-200 mb-3">This contract has already been renewed {row.renewals} times. Repeated renewals for the same ongoing work can be read as a permanent job — check with your legal adviser before renewing again.</div>}
      {row.recommendation && act !== "recommend" && <p className="text-xs text-gray-500 mb-3">Manager recommends: <b>{REC[row.recommendation]}</b>{row.recommended_end_date ? ` to ${fmt(row.recommended_end_date)}` : ""}{row.recommendation_note ? ` — ${row.recommendation_note}` : ""}</p>}
      {act === "set" && <FormSection title="The term">
        <FormGrid cols={3}>
          {!row.employee_id && <div className="sm:col-span-2 lg:col-span-3"><Field label="Employee *"><EmployeePicker value={empId} onChange={setEmpId} employees={emps} /></Field><FieldError msg={show("employee")} /></div>}
          <Field label="Employment type" hint="Fixed term = a set end date for this job"><Select value={f.employment_type} onChange={(e) => set("employment_type", e.target.value)}><option value="fixed_term">Fixed term</option><option value="contract">Contract</option></Select></Field>
          <div><Field label="Term starts" hint={hire ? `Blank = joining date (${fmt(hire)})` : "Blank = joining date"}><Input type="date" min={hire || undefined} value={f.start_date} onChange={(e) => set("start_date", e.target.value)} /></Field><FieldError msg={show("start_date")} /></div>
          <div><Field label="Term ends *"><Input type="date" min={f.start_date || hire || undefined} value={f.end_date} onChange={(e) => set("end_date", e.target.value)} /></Field><FieldError msg={show("end_date")} /></div>
          <div><Field label="Notice days in the contract" hint="0–365. Used for the “tell by” date; blank = none"><Input type="number" min={0} max={365} step={1} value={f.notice_days} onChange={(e) => set("notice_days", e.target.value)} /></Field><FieldError msg={show("notice_days")} /></div>
          <div><Field label="Contract reference" hint="Up to 80 characters"><Input maxLength={80} value={f.contract_ref} onChange={(e) => set("contract_ref", e.target.value)} placeholder="FTC/2026/014" /></Field><FieldError msg={show("contract_ref")} /></div>
        </FormGrid>
      </FormSection>}
      {act === "recommend" && <FormGrid cols={3} className="mb-3">
        <Field label="Recommendation *" hint="Your suggestion to HR, who decides"><Select value={f.recommendation} onChange={(e) => set("recommendation", e.target.value)}>{Object.entries(REC).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</Select></Field>
        {f.recommendation === "renew" && <div><Field label="Proposed new end date" hint={`Current end ${fmt(row.contract_end_date)}`}><Input type="date" min={curEnd || undefined} value={f.new_end_date} onChange={(e) => set("new_end_date", e.target.value)} /></Field><FieldError msg={show("new_end_date")} /></div>}
      </FormGrid>}
      {act === "renew" && <FormSection title="New term" hint="The renewed term runs from the day after the current end date to the new end date.">
        <FormGrid cols={4}>
          <Field label="Current end date"><Input value={fmt(row.contract_end_date)} disabled /></Field>
          <div><Field label="New end date *"><Input type="date" min={curEnd || undefined} value={f.new_end_date} onChange={(e) => set("new_end_date", e.target.value)} /></Field><FieldError msg={show("new_end_date")} /></div>
          <div><Field label="Notice days (new term)" hint="0–365; blank = keep current"><Input type="number" min={0} max={365} step={1} value={f.notice_days} onChange={(e) => set("notice_days", e.target.value)} /></Field><FieldError msg={show("notice_days")} /></div>
          <div><Field label="Contract reference (new term)" hint="Up to 80 characters"><Input maxLength={80} value={f.contract_ref} onChange={(e) => set("contract_ref", e.target.value)} /></Field><FieldError msg={show("contract_ref")} /></div>
        </FormGrid>
        <p className="text-[11px] text-gray-500 mt-2">A pay change with the renewal is a separate record — raise it on <Link className="underline" to={`/salary-revisions?employee=${id}`}>Salary revisions</Link>.</p>
      </FormSection>}
      {act === "convert" && <FormSection title="Permanent employment">
        <FormGrid cols={3}>
          <div><Field label="Permanent from *"><Input type="date" min={curStart || hire || undefined} value={f.effective_date} onChange={(e) => set("effective_date", e.target.value)} /></Field><FieldError msg={show("effective_date")} /></div>
          <div><Field label="Probation months (optional)" hint="0–12; blank = no probation"><Input type="number" min={0} max={12} step={1} value={f.probation_months} onChange={(e) => set("probation_months", e.target.value)} /></Field><FieldError msg={show("probation_months")} /></div>
        </FormGrid>
        <p className="text-[11px] text-gray-500 mt-2">A revised salary on conversion is raised on <Link className="underline" to={`/salary-revisions?employee=${id}`}>Salary revisions</Link>.</p>
      </FormSection>}
      {act === "end" && <FormGrid cols={3} className="mb-3">
        <div><Field label="Last working day *" hint="Default = the contract end date. Final pay is worked out up to this day."><Input type="date" min={hire || undefined} value={f.last_working_date} onChange={(e) => set("last_working_date", e.target.value)} /></Field><FieldError msg={show("last_working_date")} /></div>
      </FormGrid>}
      <div className="mb-3"><Field label={act === "set" ? "Note (optional)" : "Reason *"} hint={act === "set" ? "Why this person is on a fixed term (project, season, replacement cover). Kept in the history." : "Kept in the contract history and shown on the letter."}>
        <Textarea rows={2} maxLength={1000} value={f.reason} onChange={(e) => set("reason", e.target.value)} placeholder={act === "set" ? "Project-based role for the Calgary plant expansion" : undefined} /></Field><FieldError msg={show("reason")} /></div>
      {(act === "renew" || act === "convert") && <label className="text-xs text-gray-600 flex items-center gap-2"><input type="checkbox" checked={f.issue_letter} onChange={(e) => set("issue_letter", e.target.checked)} /> Issue the {act === "renew" ? "renewal" : "conversion"} letter now (Letters → templates)</label>}
    </Modal>
  );
}

const ACT_LABEL: Record<string, string> = { start: "Term set", correct: "Term corrected", recommend: "Recommendation", renew: "Renewed", convert: "Made permanent", end: "Ended on expiry", alert: "Reminder sent" };

function HistoryModal({ row, onClose }: { row: FixedTermRow; onClose: () => void }) {
  const { data, isLoading } = useQuery({ queryKey: ["fixed-term-history", row.employee_id], queryFn: () => fixedTermApi.history(row.employee_id) });
  return (
    <Modal title={`Contract history — ${row.employee}`} size="lg" onClose={onClose}>
      <Grid rows={data?.rows || []} keyOf={(r: any) => r.id} loading={isLoading} exportName={`contract_history_${row.employee_number}.csv`} empty="No history yet."
        cols={[{ k: "at", label: "When", render: (r: any) => (r.at || "").slice(0, 10) }, { k: "action", label: "What", render: (r: any) => ACT_LABEL[r.action] || r.action },
          { k: "term_no", label: "Term", render: (r: any) => r.term_no ? `#${r.term_no}` : "" },
          { k: "start_date", label: "From", render: (r: any) => fmt(r.start_date) }, { k: "end_date", label: "To", render: (r: any) => fmt(r.end_date) },
          { k: "recommendation", label: "Recommendation", render: (r: any) => REC[r.recommendation] || (r.alert_days != null ? `${r.alert_days} days left` : "") },
          { k: "reason", label: "Reason" }, { k: "actor", label: "By" }, { k: "exit_handover", label: "Exit", render: (r: any) => r.exit_handover || "" }]} />
    </Modal>
  );
}
