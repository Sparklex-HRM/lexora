import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { UserCheck, Settings2, Plus, Trash2 } from "lucide-react";
import { PageHeader } from "../../components/ui";
import { HR_WRITE } from "../../store/authStore";
import { probationApi, careerApi, lettersApi, openPdf, errText, fmtDate } from "../../api/lifecycle";
import { FormGrid, FormSection } from "../../components/Modal";
import { v, collect, firstError, FieldError } from "../../lib/validators";
import { Grid, Modal, Field, Input, Select, Textarea, Stat, today } from "../common/_shared";
import { addMonthsISO } from "../../lib/datetime";   // 2026-10-25: UTC store, org-zone today
import { useCan } from "../../config/permissions";   // 2026-10-26: UI gates = permission + role (custom roles)

/* Probation → confirmation. HR sees everyone; a manager sees direct reports and can
   only recommend. Confirm / Extend / Do not confirm are HR actions. */

const BUCKETS: [string, string][] = [["overdue", "Overdue"], ["7", "Ends in 7 days"], ["15", "Ends in 15 days"], ["30", "Ends in 30 days"], ["later", "Later"]];
const REC: Record<string, string> = { confirm: "Confirm", extend: "Extend", do_not_confirm: "Do not confirm" };
type Act = "recommend" | "confirm" | "extend" | "dnc";

export default function ProbationPage() {
  const qc = useQueryClient();
  const isHr = useCan(["career.edit", "career.approve"], [...HR_WRITE, "payroll_admin"]);
  const [bucket, setBucket] = useState("");
  const [showAll, setShowAll] = useState(false);
  const [act, setAct] = useState<{ a: Act; row: any } | null>(null);
  const [settings, setSettings] = useState(false);
  const { data, isLoading } = useQuery({ queryKey: ["probation-due", showAll], queryFn: () => probationApi.due({ within: 30, all: showAll ? 1 : 0 }) });
  const rows: any[] = data?.rows || [];
  const counts = data?.counts || {};
  const refresh = () => qc.invalidateQueries({ queryKey: ["probation-due"] });
  const shown = bucket ? rows.filter((r) => r.bucket === bucket) : rows;
  return (
    <div>
      <PageHeader title="Probation due" icon={UserCheck} subtitle="People whose probation is ending. Confirm, extend, or do not confirm."
        actions={isHr ? <button className="btn-secondary flex items-center gap-1" onClick={() => setSettings(true)}><Settings2 className="w-4 h-4" /> Probation settings</button> : undefined} />
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 mb-4">
        {BUCKETS.map(([k, l]) => <button key={k} className="text-left" onClick={() => { setBucket(bucket === k ? "" : k); if (k === "later") setShowAll(true); }}>
          <Stat label={l} value={counts[k] ?? 0} accent={bucket === k} sub={k === "overdue" && counts[k] ? "Act now" : undefined} /></button>)}
      </div>
      <Grid rows={shown} keyOf={(r: any) => r.employee_id} loading={isLoading} exportName="probation_due.csv"
        empty="Nobody's probation is ending in this window."
        filters={<label className="text-xs text-gray-600 flex items-center gap-1"><input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> Show everyone on probation</label>}
        cols={[
          { k: "employee", label: "Employee", render: (r: any) => <span className="font-medium text-gray-800">{r.employee}<span className="text-gray-400 ml-1">{r.employee_number}</span></span> },
          { k: "job_title", label: "Designation" }, { k: "department", label: "Department" }, { k: "manager_name", label: "Manager" },
          { k: "hire_date", label: "Joined", render: (r: any) => fmtDate(r.hire_date) },
          { k: "probation_end_date", label: "Probation ends", render: (r: any) => fmtDate(r.probation_end_date) },
          { k: "days_left", label: "Days left", align: "right", render: (r: any) => r.days_left < 0 ? <span className="text-red-600 font-semibold">{-r.days_left} overdue</span> : r.days_left },
          { k: "extended_times", label: "Extended", align: "center", render: (r: any) => r.extended_times ? `${r.extended_times}×` : "—" },
          { k: "recommendation", label: "Manager says", csv: (r: any) => REC[r.recommendation] || "", render: (r: any) => r.recommendation ? <span title={`${r.recommendation_by || ""} ${r.recommendation_note || ""}`} className={r.recommendation === "confirm" ? "badge-green" : r.recommendation === "extend" ? "badge-amber" : "badge-red"}>{REC[r.recommendation]}</span> : <span className="text-gray-300">Not yet</span> },
          { k: "actions", label: "", csv: () => "", render: (r: any) => <span className="flex gap-1">
            <button className="btn-secondary !py-0.5 !px-2 text-xs" onClick={() => setAct({ a: "recommend", row: r })}>Recommend</button>
            {isHr && <>
              <button className="btn-primary !py-0.5 !px-2 text-xs" onClick={() => setAct({ a: "confirm", row: r })}>Confirm</button>
              <button className="btn-secondary !py-0.5 !px-2 text-xs" onClick={() => setAct({ a: "extend", row: r })}>Extend</button>
              <button className="btn-secondary !py-0.5 !px-2 text-xs text-red-600" onClick={() => setAct({ a: "dnc", row: r })}>Do not confirm</button></>}
          </span> },
        ]} />
      {act && <ActionModal act={act.a} row={act.row} onClose={() => setAct(null)} onDone={() => { setAct(null); refresh(); }} />}
      {settings && <SettingsModal onClose={() => setSettings(false)} onDone={() => { setSettings(false); refresh(); }} />}
    </div>
  );
}

const MAX_PROBATION_MONTHS = 36;   // same ceiling the settings screen and the server use for one probation length
const addMonths = (iso: string, n: number) => addMonthsISO(iso, n);

function ActionModal({ act, row, onClose, onDone }: { act: Act; row: any; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState<any>({ recommendation: "confirm", reason: "", new_end_date: row.recommended_end_date || "", confirmation_date: "", last_working_date: "", issue_letter: true });
  const [tried, setTried] = useState(false);
  const set = (k: string, val: any) => setF((p: any) => ({ ...p, [k]: val }));
  const after = async (r: any, msg: string) => {
    toast.success(msg);
    if (r.letter_id) openPdf(await lettersApi.pdf(r.letter_id), `${r.number}.pdf`);
    if (r.letter_error) toast.error(`The letter was not issued: ${r.letter_error}`);
    onDone();
  };
  const run = useMutation({
    mutationFn: (): Promise<any> => act === "recommend" ? probationApi.recommend(row.employee_id, { recommendation: f.recommendation, reason: f.reason.trim(), new_end_date: (f.recommendation === "extend" && f.new_end_date) || undefined })
      : act === "confirm" ? probationApi.confirm(row.employee_id, { confirmation_date: f.confirmation_date || undefined, reason: f.reason.trim(), issue_letter: f.issue_letter })
      : act === "extend" ? probationApi.extend(row.employee_id, { new_end_date: f.new_end_date, reason: f.reason.trim(), issue_letter: f.issue_letter })
      : probationApi.doNotConfirm(row.employee_id, { reason: f.reason.trim(), last_working_date: f.last_working_date || undefined }),
    onSuccess: (r: any) => act === "recommend" ? after(r, "Recommendation saved")
      : act === "confirm" ? after(r, `${row.employee} is confirmed from ${fmtDate(r.confirmation_date)}`)
      : act === "extend" ? after(r, `Probation extended to ${fmtDate(r.probation_end_date)}`)
      : (r.exit_started ? toast.success(r.message) : toast.error(r.message), onDone()),
    onError: (e) => toast.error(errText(e)) });
  const title = { recommend: "Manager recommendation", confirm: "Confirm employee", extend: "Extend probation", dnc: "Do not confirm" }[act];
  const needReason = act === "extend" || act === "dnc" || (act === "recommend" && f.recommendation !== "confirm");
  const showNewEnd = act === "extend" || (act === "recommend" && f.recommendation === "extend");
  const hire = String(row.hire_date || "").slice(0, 10);
  const end = String(row.probation_end_date || "").slice(0, 10);
  const cap = hire ? addMonths(hire, MAX_PROBATION_MONTHS) : "";
  const errs = collect({
    new_end_date: showNewEnd ? ((act === "extend" ? v.required(f.new_end_date, "New probation end date") : null)
      || (end && f.new_end_date && f.new_end_date <= end ? "The new end date must be after the current end date" : null)
      || (cap && f.new_end_date && f.new_end_date > cap ? `Probation cannot run past ${fmtDate(cap)} (${MAX_PROBATION_MONTHS} months from joining)` : null)) : null,
    confirmation_date: act === "confirm" ? v.dateOrder(hire, f.confirmation_date, "The confirmation date cannot be before the joining date") : null,
    last_working_date: act === "dnc" ? (v.dateOrder(today(), f.last_working_date, "The last working day cannot be in the past")
      || v.dateOrder(hire, f.last_working_date, "The last working day cannot be before the joining date")) : null,
    reason: (needReason ? v.required(f.reason, "Reason") : null) || v.maxLen(f.reason, 1000, needReason ? "Reason" : "Note"),
  });
  const show = (k: string) => (tried ? errs[k] : undefined);
  const go = () => {
    setTried(true);
    if (Object.keys(errs).length) { toast.error(firstError(errs)); return; }
    run.mutate();
  };
  return (
    <Modal title={`${title} · ${row.employee}`} size="lg" onClose={onClose} subtitle={`Joined ${fmtDate(row.hire_date)} · probation ends ${fmtDate(row.probation_end_date)}${row.extended_times ? ` · extended ${row.extended_times}×` : ""}`}
      footer={<><button className="btn-secondary" onClick={onClose}>Close</button><button className={act === "dnc" ? "btn-secondary text-red-600" : "btn-primary"} disabled={run.isPending} onClick={go}>{act === "dnc" ? "Do not confirm and start exit" : "Save"}</button></>}>
      {row.recommendation && act !== "recommend" && <p className="text-xs text-gray-500 mb-3">Manager recommendation: <b>{REC[row.recommendation]}</b>{row.recommended_end_date ? ` to ${fmtDate(row.recommended_end_date)}` : ""}{row.recommendation_note ? ` — ${row.recommendation_note}` : ""}</p>}
      <FormGrid cols={2}>
        {act === "recommend" && <Field label="Recommendation *" hint="Your suggestion to HR, who makes the decision"><Select value={f.recommendation} onChange={(e) => set("recommendation", e.target.value)}>{Object.entries(REC).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</Select></Field>}
        {act === "confirm" && <div><Field label="Confirmation date" hint="Empty = the day after probation ends"><Input type="date" min={hire || undefined} value={f.confirmation_date} onChange={(e) => set("confirmation_date", e.target.value)} /></Field><FieldError msg={show("confirmation_date")} /></div>}
        {showNewEnd && <div><Field label={`New probation end date${act === "extend" ? " *" : ""}`} hint="The last day of the extended probation. Must be after the current end date."><Input type="date" min={end || undefined} max={cap || undefined} value={f.new_end_date} onChange={(e) => set("new_end_date", e.target.value)} /></Field><FieldError msg={show("new_end_date")} /></div>}
        {act === "dnc" && <div><Field label="Last working day" hint="Optional. Leave blank to set it on the Exit screen after the notice period."><Input type="date" min={today()} value={f.last_working_date} onChange={(e) => set("last_working_date", e.target.value)} /></Field><FieldError msg={show("last_working_date")} /></div>}
        <div className="sm:col-span-2"><Field label={needReason ? "Reason *" : "Note"} hint={needReason ? "Shown on the letter and kept in the audit trail. Be specific." : "Optional. Kept in the audit trail."}><Textarea rows={3} maxLength={1000} value={f.reason} onChange={(e) => set("reason", e.target.value)} /></Field><FieldError msg={show("reason")} /></div>
        {(act === "confirm" || act === "extend") && <label className="flex items-center gap-2 text-sm sm:col-span-2"><input type="checkbox" checked={f.issue_letter} onChange={(e) => set("issue_letter", e.target.checked)} /> Issue the {act === "confirm" ? "confirmation" : "probation extension"} letter now <span className="text-[11px] text-gray-400">(leave off to issue it later from Letters)</span></label>}
        {act === "dnc" && <p className="text-xs rounded-lg px-3 py-2 bg-red-50 text-red-700 border border-red-200 sm:col-span-2">This records the decision and starts an exit case for the employee. The notice period, last working day and dues are completed on the Exit screen.</p>}
      </FormGrid>
    </Modal>
  );
}

const RULE_TYPES = ["full_time", "part_time", "contract", "fixed_term", "seasonal", "intern"];

function SettingsModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const { data: s } = useQuery({ queryKey: ["probation-settings"], queryFn: () => probationApi.settings() });
  const { data: meta } = useQuery({ queryKey: ["career-fields"], queryFn: () => careerApi.fields(), retry: false });
  const grades = Array.from(new Set(Object.values(meta?.extras || {}).map((x: any) => x?.grade).filter(Boolean))).sort() as string[];
  const [f, setF] = useState<any | null>(null);
  const [tried, setTried] = useState(false);
  const cfg = f || (s ? { probation_months: s.probation_months, probation_rules: s.probation_rules || [] } : { probation_months: 6, probation_rules: [] });
  const setRule = (i: number, k: string, val: any) => setF({ ...cfg, probation_rules: cfg.probation_rules.map((r: any, j: number) => (j === i ? { ...r, [k]: val } : r)) });
  const whole = (x: any, label: string) => (String(x ?? "").trim() === "" ? `${label} is required` : v.range(x, 0, 36, label) || (!Number.isInteger(Number(x)) ? `${label} must be a whole number` : null));
  const keyOf = (r: any) => `${String(r.grade || "").trim().toLowerCase()}|${r.employment_type || ""}`;
  const checks: Record<string, string | null> = { probation_months: whole(cfg.probation_months, "Default probation months") };
  cfg.probation_rules.forEach((r: any, i: number) => {
    checks[`rule${i}`] = (!String(r.grade || "").trim() && !r.employment_type ? `Rule ${i + 1}: pick a grade or an employment type` : null)
      || whole(r.months, `Rule ${i + 1} months`)
      || (cfg.probation_rules.findIndex((o: any) => keyOf(o) === keyOf(r)) !== i ? `Rule ${i + 1} repeats an earlier rule` : null);
  });
  const errs = collect(checks);
  const show = (k: string) => (tried ? errs[k] : undefined);
  const save = useMutation({ mutationFn: () => probationApi.saveSettings({ probation_months: Number(cfg.probation_months), probation_rules: cfg.probation_rules.map((r: any) => ({ ...r, grade: String(r.grade || "").trim(), months: Number(r.months) })) }),
    onSuccess: () => { toast.success("Probation settings saved"); onDone(); }, onError: (e) => toast.error(errText(e)) });
  const assign = useMutation({ mutationFn: () => probationApi.assignMissing(), onSuccess: (r: any) => toast.success(`${r.updated} employee(s) updated`), onError: (e) => toast.error(errText(e)) });
  const go = () => {
    setTried(true);
    if (Object.keys(errs).length) { toast.error(firstError(errs)); return; }
    save.mutate();
  };
  return (
    <Modal title="Probation settings" size="lg" onClose={onClose} subtitle="The end date is set from the joining date. It is the last day of probation."
      footer={<><button className="btn-secondary" onClick={() => assign.mutate()} disabled={assign.isPending}>Set missing end dates now</button><button className="btn-secondary" onClick={onClose}>Close</button><button className="btn-primary" disabled={!f || save.isPending} onClick={go}>Save</button></>}>
      <FormSection title="Default length">
        <FormGrid cols={3}>
          <div><Field label="Default probation (months) *" hint="0–36. Used when none of the rules below match the employee."><Input type="number" min={0} max={36} step={1} value={cfg.probation_months} onChange={(e) => setF({ ...cfg, probation_months: e.target.value })} /></Field><FieldError msg={show("probation_months")} /></div>
        </FormGrid>
      </FormSection>
      <FormSection title="Different length for a grade or an employment type (optional)" hint="A rule with both grade and type wins over a rule with only one. The order of rules does not change the result.">
        <div className="flex justify-end mb-2">
          <button className="btn-secondary !py-1 !px-2 text-xs flex items-center gap-1" onClick={() => setF({ ...cfg, probation_rules: [...cfg.probation_rules, { grade: "", employment_type: "", months: cfg.probation_months }] })}><Plus className="w-3.5 h-3.5" /> Add rule</button></div>
        {cfg.probation_rules.length === 0 ? <p className="text-sm text-gray-400">No rules. Everyone gets the default.</p> : <>
          <div className="hidden sm:grid grid-cols-[minmax(0,2fr)_minmax(0,2fr)_minmax(0,1fr)_2rem] gap-2 px-1 mb-1 text-[10px] uppercase tracking-wide text-gray-400 font-semibold">
            <span>Grade</span><span>Employment type</span><span>Months</span><span /></div>
          {cfg.probation_rules.map((r: any, i: number) => (
            <div key={i} className="mb-2">
              <div className="grid grid-cols-1 sm:grid-cols-[minmax(0,2fr)_minmax(0,2fr)_minmax(0,1fr)_2rem] gap-2 items-center">
                <Input list="probation-grades" aria-label={`Rule ${i + 1} grade`} maxLength={40} value={r.grade || ""} onChange={(e) => setRule(i, "grade", e.target.value)} placeholder="Grade, e.g. L1" />
                <Select aria-label={`Rule ${i + 1} employment type`} value={r.employment_type || ""} onChange={(e) => setRule(i, "employment_type", e.target.value)}><option value="">Any type</option>{RULE_TYPES.map((t) => <option key={t} value={t}>{t.replace("_", " ")}</option>)}</Select>
                <Input aria-label={`Rule ${i + 1} months`} type="number" min={0} max={36} step={1} value={r.months} onChange={(e) => setRule(i, "months", e.target.value)} />
                <button className="icon-btn justify-self-start" title="Remove rule" onClick={() => setF({ ...cfg, probation_rules: cfg.probation_rules.filter((_: any, j: number) => j !== i) })}><Trash2 className="w-4 h-4" /></button>
              </div>
              <FieldError msg={show(`rule${i}`)} />
            </div>))}
          <datalist id="probation-grades">{grades.map((g) => <option key={g} value={g} />)}</datalist>
        </>}
      </FormSection>
      <p className="text-xs text-gray-500">End dates are filled in only for people who are still inside their probation window. People who joined long ago are left as they are.</p>
    </Modal>
  );
}
