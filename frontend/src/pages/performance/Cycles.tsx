import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import toast from "react-hot-toast";
import { money } from "../../api/endpoints";
import { err, fmtDate, fmtDateTime } from "../hiring/api";
import { Col, Field, FirstStep, Grid, Input, Modal, Note, Pill, ReasonDialog, Select, Stat } from "../hiring/ui";
import { FormGrid, FormSection } from "../../components/Modal";
import { collect, FieldError, firstError, Req, v } from "../../lib/validators";
import { perfApi, Row, STAGE_LABEL } from "./api";
import { todayISO } from "../../lib/datetime";   // 2026-10-25: UTC store, org-zone today

type Open = { kind: "edit" | "launch" | "calibration" | "increment" | "promotions"; cycle: Row | null };

export default function Cycles({ info }: { info: Row }) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["perf", "cycles"], queryFn: perfApi.cycles });
  const [open, setOpen] = useState<Open | null>(null);
  const act = useMutation({
    mutationFn: (v: { id: string; action: "remind" | "publish" }) => perfApi.actCycle(v.id, v.action),
    onSuccess: (r, v) => {
      qc.invalidateQueries({ queryKey: ["perf"] });
      if (v.action === "publish") toast.success(`${r.published} rating(s) published.${r.left_behind ? ` ${r.left_behind} review(s) are not at HR calibration yet and were left alone.` : ""}`, { duration: 7000 });
      else toast.success(r.notify_available ? `Reminders sent to ${r.reminded} people. Waiting: ${r.pending.self} self, ${r.pending.manager} manager, ${r.pending.reviewer} reviewer.` : "Notifications are not installed, so no reminder was sent.", { duration: 7000 });
    },
    onError: (e) => toast.error(err(e)),
  });
  const cols: Col<Row>[] = [
    { k: "name", label: "Appraisal cycle", render: (c) => <span className="font-medium text-gray-800">{c.name}</span> },
    { k: "period_start", label: "Period", render: (c) => `${fmtDate(c.period_start)} to ${fmtDate(c.period_end)}`, csv: (c) => `${c.period_start} to ${c.period_end}` },
    { k: "status", label: "Status", render: (c) => <Pill s={c.status} />, csv: (c) => c.status },
    { k: "reviews", label: "Reviews", align: "right" },
    ...["self", "manager", "reviewer", "calibration", "published"].map((s) => ({ k: `s_${s}`, label: STAGE_LABEL[s], align: "right" as const,
      render: (c: Row) => <span className={c.overdue?.[s] ? "text-red-600 font-semibold" : ""} title={c.overdue?.[s] ? "Past the deadline" : ""}>{c.by_stage?.[s] || ""}</span>, csv: (c: Row) => c.by_stage?.[s] || 0, sortVal: (c: Row) => c.by_stage?.[s] || 0 })),
    { k: "acknowledged", label: "Acknowledged", align: "right" }, { k: "disagreed", label: "Disagreed", align: "right" },
    { k: "_a", label: "Actions", csv: () => "", render: (c) => (
      <div className="flex gap-1" onClick={(e) => e.stopPropagation()}>
        {c.status === "draft" && <><button className="btn-secondary !py-0.5 !px-2 text-[11px]" onClick={() => setOpen({ kind: "edit", cycle: c })}>Edit</button>
          <button className="btn-primary !py-0.5 !px-2 text-[11px]" onClick={() => setOpen({ kind: "launch", cycle: c })}>Check who is eligible and launch</button></>}
        {c.status !== "draft" && <button className="btn-primary !py-0.5 !px-2 text-[11px]" onClick={() => setOpen({ kind: "calibration", cycle: c })}>Calibration</button>}
        {c.status === "active" && <><button className="btn-secondary !py-0.5 !px-2 text-[11px]" onClick={() => setOpen({ kind: "edit", cycle: c })}>Deadlines</button>
          <button className="btn-secondary !py-0.5 !px-2 text-[11px]" onClick={() => act.mutate({ id: c.id, action: "remind" })}>Send reminders</button></>}
        {c.status === "published" && info.can_edit && <button className="btn-secondary !py-0.5 !px-2 text-[11px]" onClick={() => setOpen({ kind: "increment", cycle: c })}>Increment proposal</button>}
        {c.status !== "draft" && <button className="btn-secondary !py-0.5 !px-2 text-[11px]" onClick={() => setOpen({ kind: "promotions", cycle: c })}>Promotions</button>}
      </div>) },
  ];
  return (
    <div>
      {!isLoading && !(data || []).length ? (
        <FirstStep title="No appraisal cycle yet">
          First check the rating scale and competencies in Settings. Then create a cycle, check who is eligible and launch it. Each eligible employee gets a review that starts at Self review.
          <div className="mt-4"><button className="btn-primary inline-flex items-center gap-1.5" onClick={() => setOpen({ kind: "edit", cycle: null })}><Plus className="w-4 h-4" />Create an appraisal cycle</button></div>
        </FirstStep>
      ) : <Grid rows={data || []} cols={cols} keyOf={(c) => c.id} loading={isLoading} exportName="appraisal-cycles.csv" title="Appraisal cycles"
        actions={<button className="btn-primary !py-1 !px-2.5 text-xs inline-flex items-center gap-1" onClick={() => setOpen({ kind: "edit", cycle: null })}><Plus className="w-3.5 h-3.5" />Create an appraisal cycle</button>} />}
      {open?.kind === "edit" && <CycleForm cycle={open.cycle} onClose={() => setOpen(null)} />}
      {open?.kind === "launch" && <LaunchModal cycle={open.cycle!} onClose={() => setOpen(null)} />}
      {open?.kind === "calibration" && <CalibrationModal cycle={open.cycle!} canPublish={info.can_edit} onPublish={() => act.mutate({ id: open.cycle!.id, action: "publish" })} onClose={() => setOpen(null)} />}
      {open?.kind === "increment" && <IncrementModal cycle={open.cycle!} onClose={() => setOpen(null)} />}
      {open?.kind === "promotions" && <PromotionsModal cycle={open.cycle!} onClose={() => setOpen(null)} />}
    </div>
  );
}

function CycleForm({ cycle, onClose }: { cycle: Row | null; onClose: () => void }) {
  const qc = useQueryClient();
  const goalCycles = useQuery({ queryKey: ["perf", "goal-cycles"], queryFn: perfApi.goalCycles });
  const launched = !!cycle && cycle.status !== "draft";
  const [f, setF] = useState<Row>(cycle ? { ...cycle, deadlines: { ...(cycle.deadlines || {}) } } : { name: defaultCycleName(), exclude_notice: true, use_reviewer: false, deadlines: {} });
  const [tried, setTried] = useState(false);
  const set = (k: string, v: any) => setF((p) => ({ ...p, [k]: v }));
  const setDl = (k: string, v: string) => setF((p) => ({ ...p, deadlines: { ...p.deadlines, [k]: v } }));
  const dl: Row = f.deadlines || {};
  // Stage deadlines must run in order: self -> manager -> (reviewer) -> calibration.
  const lastBeforeCal = f.use_reviewer && dl.reviewer ? dl.reviewer : dl.manager || dl.self;
  const errs = collect({
    name: v.required(String(f.name || "").trim(), "Name") || v.maxLen(String(f.name || "").trim(), 160, "Name"),
    period_start: v.required(f.period_start, "Period start"),
    period_end: v.required(f.period_end, "Period end") || (f.period_start && f.period_end && f.period_end <= f.period_start ? "The period must end after it starts" : null),
    joined_before: f.joined_before && f.period_end && f.joined_before > f.period_end ? "Joined-before date should not be after the period end" : null,
    manager: v.dateOrder(dl.self, dl.manager, "Manager review cannot be due before self review"),
    reviewer: f.use_reviewer ? v.dateOrder(dl.manager || dl.self, dl.reviewer, "Reviewer stage cannot be due before manager review") : null,
    calibration: v.dateOrder(lastBeforeCal, dl.calibration, "Calibration must be the last deadline"),
  });
  // Order / format problems show at once; "required" ones only after the first Save.
  const show = (k: string) => (errs[k] && (tried || !["name", "period_start", "period_end"].includes(k) || !!f[k]) ? errs[k] : null);
  const save = useMutation({
    mutationFn: () => {
      const body: Row = launched ? { name: String(f.name).trim(), deadlines: f.deadlines } : { name: String(f.name).trim(), period_start: f.period_start, period_end: f.period_end, goal_cycle_id: f.goal_cycle_id || null,
        joined_before: f.joined_before || null, exclude_notice: f.exclude_notice, use_reviewer: f.use_reviewer, deadlines: f.use_reviewer ? f.deadlines : { ...f.deadlines, reviewer: "" } };
      return cycle ? perfApi.updateCycle(cycle.id, body) : perfApi.createCycle(body);
    },
    onSuccess: () => { toast.success(launched ? "Deadlines saved." : "Cycle saved as a draft. Check who is eligible, then launch it."); qc.invalidateQueries({ queryKey: ["perf"] }); onClose(); },
    onError: (e) => toast.error(err(e)),
  });
  const onSave = () => { setTried(true); if (Object.keys(errs).length) { toast.error(firstError(errs)); return; } save.mutate(); };
  return (
    <Modal title={cycle ? (launched ? `Deadlines: ${cycle.name}` : "Edit appraisal cycle") : "Create an appraisal cycle"} subtitle="The rating scale, competencies and score weights are taken from Settings when you launch, and then stay fixed for this cycle." onClose={onClose}
      footer={<><button className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary disabled:opacity-50" disabled={save.isPending} onClick={onSave}>Save</button></>}>
      <FormSection title="Cycle and period" hint="The period is the work being appraised (for example 1 Apr 2025 to 31 Mar 2026 for FY 2025-26), not the window for filling in reviews.">
        <FormGrid cols={4}>
          <div className="sm:col-span-2"><Field label={<>Name<Req /></>}><Input maxLength={160} placeholder="Annual appraisal FY 2025-26" value={f.name || ""} onChange={(e) => set("name", e.target.value)} /></Field><FieldError msg={show("name")} /></div>
          <div><Field label={<>Period starts<Req /></>}><Input type="date" disabled={launched} value={f.period_start || ""} onChange={(e) => set("period_start", e.target.value)} /></Field><FieldError msg={show("period_start")} /></div>
          <div><Field label={<>Period ends<Req /></>}><Input type="date" disabled={launched} value={f.period_end || ""} onChange={(e) => set("period_end", e.target.value)} /></Field><FieldError msg={show("period_end")} /></div>
          <div className="sm:col-span-2"><Field label="Goals come from" hint="Approved goals of this goal cycle are copied into each review at launch.">
            <Select disabled={launched} value={f.goal_cycle_id || ""} onChange={(e) => set("goal_cycle_id", e.target.value)}><option value="">No goals: competencies only</option>
              {(goalCycles.data || []).map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}</Select></Field></div>
        </FormGrid>
      </FormSection>
      <FormSection title="Who is reviewed" hint="Eligibility is worked out when you launch. You can check the list before launching.">
        <FormGrid cols={3}>
          <div><Field label="Eligible: joined before" hint="Leave empty to include everyone. Many firms leave out people who joined in the last 3 to 6 months."><Input type="date" disabled={launched} value={f.joined_before || ""} onChange={(e) => set("joined_before", e.target.value)} /></Field><FieldError msg={show("joined_before")} /></div>
          <Field label="People in notice period" hint="Usually left out. Include them only if their rating still matters, for example for the final settlement."><Select disabled={launched} value={f.exclude_notice ? "1" : "0"} onChange={(e) => set("exclude_notice", e.target.value === "1")}><option value="1">Leave out</option><option value="0">Include</option></Select></Field>
          <Field label="Skip-level reviewer stage"><Select disabled={launched} value={f.use_reviewer ? "1" : "0"} onChange={(e) => set("use_reviewer", e.target.value === "1")}><option value="0">Off</option><option value="1">On: the manager's manager reviews</option></Select></Field>
        </FormGrid>
      </FormSection>
      <FormSection title="Stage deadlines" hint="Due dates drive reminders and the overdue flags. A stage can still be submitted after its date.">
        <FormGrid cols={4}>
          <Field label="Self review due"><Input type="date" value={dl.self || ""} onChange={(e) => setDl("self", e.target.value)} /></Field>
          <div><Field label="Manager review due"><Input type="date" value={dl.manager || ""} onChange={(e) => setDl("manager", e.target.value)} /></Field><FieldError msg={show("manager")} /></div>
          {f.use_reviewer && <div><Field label="Reviewer due"><Input type="date" value={dl.reviewer || ""} onChange={(e) => setDl("reviewer", e.target.value)} /></Field><FieldError msg={show("reviewer")} /></div>}
          <div><Field label="Calibration due"><Input type="date" value={dl.calibration || ""} onChange={(e) => setDl("calibration", e.target.value)} /></Field><FieldError msg={show("calibration")} /></div>
        </FormGrid>
        {dl.self && f.period_end && dl.self < f.period_end && <p className="text-[11px] text-amber-700 mt-2">Self review is due before the period ends. That is allowed, but people will be rating work that is not finished yet.</p>}
      </FormSection>
    </Modal>
  );
}

/** yyyy-mm-dd of the 1st of the month after the given date. */
function firstOfNextMonth(iso: string) {
  const y = Number(iso.slice(0, 4)), m = Number(iso.slice(5, 7));
  return m === 12 ? `${y + 1}-01-01` : `${y}-${String(m + 1).padStart(2, "0")}-01`;
}

/** "Annual appraisal 2025": the calendar year that ended most recently. */
function defaultCycleName() {
  return `Annual appraisal ${new Date().getFullYear() - 1}`;
}

function LaunchModal({ cycle, onClose }: { cycle: Row; onClose: () => void }) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["perf", "eligibility", cycle.id], queryFn: () => perfApi.eligibility(cycle.id) });
  const launch = useMutation({
    mutationFn: () => perfApi.actCycle(cycle.id, "launch"),
    onSuccess: (r) => { toast.success(`Launched. ${r.reviews} review(s) are open at Self review.${r.without_manager ? ` ${r.without_manager} have no manager on record: HR must move those past the manager stage.` : ""}`, { duration: 8000 }); qc.invalidateQueries({ queryKey: ["perf"] }); onClose(); },
    onError: (e) => toast.error(err(e)),
  });
  const rows = data || [];
  const yes = rows.filter((r) => r.eligible);
  return (
    <Modal size="screen" title={`Who is eligible: ${cycle.name}`} subtitle="Check this list before launching. After launch the list is fixed." onClose={onClose}
      footer={<><button className="btn-secondary" onClick={onClose}>Close</button><button className="btn-primary disabled:opacity-50" disabled={launch.isPending || !yes.length} onClick={() => { if (window.confirm(`Launch for ${yes.length} employee(s)? This cannot be undone.`)) launch.mutate(); }}>Launch for {yes.length} employee(s)</button></>}>
      <div className="grid grid-cols-3 gap-3 mb-3 max-w-2xl"><Stat label="Eligible" value={yes.length} accent /><Stat label="Left out" value={rows.length - yes.length} /><Stat label="Eligible, no manager" value={yes.filter((r) => !r.has_manager).length} /></div>
      {yes.some((r) => !r.has_manager) && <Note tone="warn">Some eligible employees have no manager on record. Their review will wait at Manager review until HR moves it on with a reason. Set the manager on the employee record first if you can.</Note>}
      <Grid rows={rows} keyOf={(r) => r.employee_id} loading={isLoading} exportName="appraisal-eligibility.csv" empty="There are no employees yet. Add employees first."
        cols={[{ k: "name", label: "Employee" }, { k: "employee_number", label: "Number" }, { k: "department", label: "Department" }, { k: "designation", label: "Designation" }, { k: "grade", label: "Grade" },
          { k: "hire_date", label: "Joined", render: (r) => fmtDate(r.hire_date) }, { k: "manager_name", label: "Manager", render: (r) => r.manager_name || <span className="text-amber-700">None on record</span>, csv: (r) => r.manager_name || "" },
          { k: "eligible", label: "Eligible", render: (r) => <Pill s={r.eligible ? "accepted" : "rejected"} label={r.eligible ? "Yes" : "No"} />, csv: (r) => (r.eligible ? "Yes" : "No") }, { k: "reason", label: "Why not" }]} />
    </Modal>
  );
}

function CalibrationModal({ cycle, canPublish, onPublish, onClose }: { cycle: Row; canPublish: boolean; onPublish: () => void; onClose: () => void }) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["perf", "calibration", cycle.id], queryFn: () => perfApi.calibration(cycle.id) });
  const [edit, setEdit] = useState<Row | null>(null);
  const [score, setScore] = useState("");
  const [reason, setReason] = useState("");
  const [promo, setPromo] = useState(false);
  const [move, setMove] = useState<{ row: Row; to: string } | null>(null);
  const [band, setBand] = useState("");
  const refresh = () => qc.invalidateQueries({ queryKey: ["perf"] });
  const cal = useMutation({ mutationFn: (reason: string) => perfApi.calibrate(edit!.id, Number(score), reason, promo), onSuccess: (r) => { toast.success(`Final rating is now ${r.final_band} (${r.final_score}). The change is in the audit trail.`); refresh(); setEdit(null); }, onError: (e) => toast.error(err(e)) });
  const mv = useMutation({ mutationFn: (reason: string) => perfApi.moveStage(move!.row.id, move!.to, reason), onSuccess: (r) => { toast.success(`Moved to ${STAGE_LABEL[r.stage]}.`); refresh(); setMove(null); }, onError: (e) => toast.error(err(e)) });
  const rows: Row[] = (data?.rows || []).filter((r: Row) => !band || r.final_band === band);
  const scale: Row[] = data?.scale || [];
  const ready = (data?.rows || []).filter((r: Row) => r.stage === "calibration" && r.final_score != null).length;
  const cols: Col<Row>[] = [
    { k: "employee_name", label: "Employee" }, { k: "department", label: "Department" }, { k: "grade", label: "Grade" }, { k: "manager_name", label: "Manager" },
    { k: "stage", label: "Stage", render: (r) => <Pill s={r.stage} label={r.stage_label} />, csv: (r) => r.stage_label },
    { k: "goal_score", label: "Goal score", align: "right", render: (r) => r.goal_score ?? "" }, { k: "competency_score", label: "Competency score", align: "right", render: (r) => r.competency_score ?? "" },
    { k: "overall_score", label: "Computed", align: "right", render: (r) => <span title={r.overall_text || ""}>{r.overall_score ?? ""}</span> },
    { k: "final_score", label: "Final score", align: "right", render: (r) => (r.own ? "" : <b>{r.final_score ?? ""}</b>) },
    { k: "final_band", label: "Final rating", render: (r) => (r.own ? "Your own review" : r.final_band || "") },
    { k: "calibrated", label: "Changed by HR", render: (r) => (r.calibrated ? <span title={r.calibration_note || ""}><Pill s="pending" label="Yes" /></span> : ""), csv: (r) => (r.calibrated ? "Yes" : "") },
    { k: "calibration_note", label: "Reason for change" },
    { k: "promotion_recommended", label: "Promotion", render: (r) => (r.promotion_recommended ? <span title={r.promotion_note || ""}>Recommended</span> : ""), csv: (r) => (r.promotion_recommended ? "Recommended" : "") },
    { k: "pip_recommended", label: "Improvement plan", render: (r) => (r.pip_recommended ? <span className="text-amber-700" title={r.pip_note || ""}>Suggested by manager</span> : ""), csv: (r) => (r.pip_recommended ? `Suggested: ${r.pip_note || ""}` : "") },
    { k: "recommended_hike_pct", label: "Manager's suggested increase %", align: "right", render: (r) => (r.recommended_hike_pct != null ? `${r.recommended_hike_pct}%` : "") },
    { k: "ack_status", label: "Employee's answer", render: (r) => (r.ack_status ? <Pill s={r.ack_status} /> : "") },
    { k: "_a", label: "Actions", csv: () => "", render: (r) => (r.own || r.stage === "published" ? null : (
      <div className="flex gap-1">
        {r.stage === "calibration" && <button className="btn-primary !py-0.5 !px-2 text-[11px]" onClick={() => { setEdit(r); setReason(""); setPromo(!!r.promotion_recommended); setScore(String(r.final_score ?? r.overall_score ?? "")); }}>Change rating</button>}
        <select className="input !py-0.5 text-[11px] !w-32" value="" onChange={(e) => e.target.value && setMove({ row: r, to: e.target.value })}>
          <option value="">Move stage</option>{["self", "manager", "reviewer", "calibration"].filter((s) => s !== r.stage).map((s) => <option key={s} value={s}>{STAGE_LABEL[s]}</option>)}</select>
      </div>)) },
  ];
  return (
    <Modal size="screen" title={`Calibration: ${cycle.name}`} subtitle="Final ratings before publishing. Every change needs a reason and is kept in the audit trail." onClose={onClose}
      footer={<><span className="text-xs text-gray-500 mr-auto">{ready} review(s) at HR calibration are ready to publish.</span><button className="btn-secondary" onClick={onClose}>Close</button>
        {canPublish && <button className="btn-primary disabled:opacity-50" disabled={!ready} onClick={() => { if (window.confirm(`Publish ${ready} rating(s)? Employees will see their rating and the manager's comments.`)) onPublish(); }}>Publish {ready} rating(s)</button>}</>}>
      {isLoading || !data ? <div className="text-sm text-gray-400">Loading…</div> : <>
        <div className="card !p-3 mb-3">
          <div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold mb-2">Distribution by rating ({data.counted} rated){data.distribution[0]?.guideline_pct === undefined ? " · no guideline distribution is set in Settings" : ""}</div>
          <div className="grid gap-2" style={{ gridTemplateColumns: `repeat(${Math.max(scale.length, 1)}, minmax(0, 1fr))` }}>
            {(data.distribution as Row[]).map((d) => (
              <button key={d.label} onClick={() => setBand(band === d.label ? "" : d.label)} className={`text-left rounded-lg border px-2.5 py-2 ${band === d.label ? "border-primary-400 bg-primary-50" : "border-surface-border"}`}>
                <div className="text-[11px] text-gray-500 truncate">{d.value} · {d.label}</div>
                <div className="text-lg font-bold tabular-nums text-gray-800">{d.count} <span className="text-xs font-normal text-gray-500">{d.pct}%</span></div>
                <div className="h-1.5 bg-gray-100 rounded mt-1 overflow-hidden"><div className="h-full bg-primary-400" style={{ width: `${Math.min(d.pct, 100)}%` }} /></div>
                {d.guideline_pct !== undefined && <div className={`text-[11px] mt-1 ${Math.abs(d.delta_pct) > 5 ? "text-amber-700" : "text-gray-400"}`}>Guideline {d.guideline_pct}% ({d.guideline_count} people) · {d.delta_pct > 0 ? "+" : ""}{d.delta_pct} points</div>}
              </button>))}
          </div>
        </div>
        <Grid rows={rows} cols={cols} keyOf={(r) => r.id} exportName="calibration.csv" title={band ? `Rated ${band}` : "All reviews"} empty="No reviews match." />
        <div className="mt-3"><Grid rows={data.audit} keyOf={(a) => a.id} title="Audit trail" exportName="calibration-audit.csv" empty="No changes yet."
          cols={[{ k: "at", label: "When", render: (a) => fmtDateTime(a.at) }, { k: "employee_name", label: "Employee" }, { k: "action", label: "Action", render: (a) => String(a.action).replace(/_/g, " ") },
            { k: "field", label: "What", render: (a) => String(a.field || "").replace(/_/g, " ") }, { k: "old_value", label: "From" }, { k: "new_value", label: "To" }, { k: "reason", label: "Reason" }, { k: "by_name", label: "By" }]} /></div>
      </>}
      {edit && (() => {
        const lo = scale.length ? Math.min(...scale.map((s) => Number(s.value))) : 0;
        const hi = scale.length ? Math.max(...scale.map((s) => Number(s.value))) : undefined;
        const scoreChanged = edit.final_score != null && Number(score) !== Number(edit.final_score);
        const promoChanged = promo !== !!edit.promotion_recommended;
        const calErrs = collect({
          score: v.required(score, "Final score") || v.range(score, lo, hi, "Final score"),
          reason: ((scoreChanged || promoChanged) && !reason.trim() ? "Give a reason for the change" : null) || v.maxLen(reason.trim(), 400, "Reason"),
        });
        const onCal = () => { if (Object.keys(calErrs).length) { toast.error(firstError(calErrs)); return; } cal.mutate(reason.trim()); };
        return (
        <Modal size="lg" title={`Change rating: ${edit.employee_name}`} subtitle={`Computed ${edit.overall_score ?? "none"}${edit.overall_text ? " (" + edit.overall_text + ")" : ""}. Final now ${edit.final_score ?? "none"} · ${edit.final_band || "not set"}.`} onClose={() => setEdit(null)}
          footer={<><button className="btn-secondary" onClick={() => setEdit(null)}>Cancel</button>
            <button className="btn-primary disabled:opacity-50" disabled={cal.isPending} onClick={onCal}>Save rating</button></>}>
          <FormGrid>
            <Field label="New final rating"><Select value={scale.some((s) => Number(s.value) === Number(score)) ? score : ""} onChange={(e) => setScore(e.target.value)}>
              <option value="">Between two levels</option>{scale.map((s) => <option key={s.value} value={s.value}>{s.value} · {s.label}</option>)}</Select></Field>
            <div><Field label={<>Exact final score<Req /></>} hint={`Type an exact score${scale.length ? ` from ${lo} to ${hi}` : ""}; the rating shown snaps to the nearest level.`}><Input type="number" step="0.01" min={lo} max={hi} value={score} onChange={(e) => setScore(e.target.value)} /></Field><FieldError msg={calErrs.score} /></div>
            <div className="sm:col-span-2"><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={promo} onChange={(e) => setPromo(e.target.checked)} />Recommended for promotion</label>
              <p className="text-[11px] text-gray-400 mt-0.5">{edit.promotion_recommended ? `The manager recommended this${edit.promotion_note ? `: ${edit.promotion_note}` : "."}` : "The manager did not recommend a promotion."} Changing it needs a reason.</p></div>
            {edit.pip_recommended && <div className="sm:col-span-2"><Note tone="warn">The manager suggested an improvement plan{edit.pip_note ? `: ${edit.pip_note}` : "."} Start one from Improvement plans if HR agrees.</Note></div>}
            <div className="sm:col-span-2"><Field label={<>Reason for the change{(scoreChanged || promoChanged) && <Req />}</>} hint="Kept in the audit trail. HR only; the employee never sees it. Up to 400 characters.">
              <textarea className="input text-sm w-full" rows={3} maxLength={400} value={reason} onChange={(e) => setReason(e.target.value)} /></Field><FieldError msg={calErrs.reason} /></div>
          </FormGrid>
        </Modal>);
      })()}
      {move && <ReasonDialog title={`Move ${move.row.employee_name} to ${STAGE_LABEL[move.to]}`} hint="Use this to reopen a submitted stage, or to skip a stage nobody can complete. The reason is kept in the audit trail." confirm="Move" onClose={() => setMove(null)} onConfirm={(reason) => mv.mutate(reason)} />}
    </Modal>
  );
}

function IncrementModal({ cycle, onClose }: { cycle: Row; onClose: () => void }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["perf", "proposal", cycle.id], queryFn: () => perfApi.proposal(cycle.id), retry: false });
  const [p, setP] = useState<Row | null>(null);
  const [dirty, setDirty] = useState(false);
  // Default effective date: the 1st of the month after the appraisal period ends (only a suggestion until saved).
  useEffect(() => { if (q.data && !dirty) setP(q.data.status === "draft" && !q.data.effective_from && cycle.period_end ? { ...q.data, effective_from: firstOfNextMonth(cycle.period_end) } : q.data); }, [q.data, dirty, cycle.period_end]);
  const apply = (r: Row) => { setP(r); setDirty(false); qc.setQueryData(["perf", "proposal", cycle.id], r); };
  const body = () => ({ budget: p!.budget === "" ? null : p!.budget, effective_from: p!.effective_from || null,
    items: (p!.items as Row[]).map((i) => ({ employee_id: i.employee_id, proposed_pct: i.proposed_pct, reason: i.reason, include: i.include })) });
  const save = useMutation({ mutationFn: () => perfApi.saveProposal(p!.id, body()), onSuccess: (r) => { apply(r); toast.success("Proposal saved."); }, onError: (e) => toast.error(err(e)) });
  const lineErrs = () => collect({
    budget: v.range(p!.budget, 0, undefined, "Budget"),
    ...Object.fromEntries(((p!.items || []) as Row[]).filter((i) => i.include).map((i) => [`pct_${i.employee_id}`, v.percent(i.proposed_pct, `Proposed % for ${i.name}`)])),
  });
  const guard = (then: () => void, needDate = false) => () => {
    const e = { ...lineErrs(), ...(needDate ? collect({ effective_from: v.required(p!.effective_from, "Effective date") }) : {}) };
    if (Object.keys(e).length) { toast.error(firstError(e)); return; }
    then();
  };
  const rebuild = useMutation({ mutationFn: () => perfApi.rebuildProposal(p!.id), onSuccess: (r) => { apply(r); toast.success("Lines filled again from the current matrix."); }, onError: (e) => toast.error(err(e)) });
  const send = useMutation({
    mutationFn: async () => { await perfApi.saveProposal(p!.id, body()); return perfApi.sendProposal(p!.id); },
    onSuccess: (r) => { apply(r); toast.success(`Sent ${r.sent_items} draft revision(s) to Payroll, Salary Revisions${r.batch_id ? ` (batch ${r.batch_id})` : ""}. Review and approval happen there.${(r.skipped || []).length ? ` Payroll skipped ${r.skipped.length} line(s); open the batch there to see why.` : ""}`, { duration: 9000 }); qc.invalidateQueries({ queryKey: ["perf"] }); },
    onError: (e) => toast.error(err(e), { duration: 9000 }),
  });
  const setItem = (id: string, k: string, v: any) => { setDirty(true); setP((x) => ({ ...x!, items: (x!.items as Row[]).map((i) => (i.employee_id === id ? { ...i, [k]: v } : i)) })); };
  if (q.isError) return <Modal title={`Increment proposal: ${cycle.name}`} size="lg" onClose={onClose}><Note tone="warn">{err(q.error)}</Note></Modal>;
  if (!p) return <Modal title="Loading" size="lg" onClose={onClose}><div className="text-sm text-gray-400">Loading…</div></Modal>;
  const sent = p.status !== "draft";
  const items: Row[] = p.items || [];
  // live arithmetic on screen: current annual pay x proposed % / 100 (the same sum the server does)
  const inc = items.filter((i) => i.include);
  const cost = inc.reduce((s, i) => s + (Number(i.current_annual) || 0) * (Number(i.proposed_pct) || 0) / 100, 0);
  const base = inc.reduce((s, i) => s + (Number(i.current_annual) || 0), 0);
  const budget = p.budget === "" || p.budget == null ? null : Number(p.budget);
  const outside = (i: Row) => i.include && (i.min_pct == null ? Number(i.proposed_pct) > 0 : Number(i.proposed_pct) < i.min_pct || Number(i.proposed_pct) > i.max_pct);
  const problems = inc.filter((i) => outside(i) && !(i.reason || "").trim()).length;
  const cols: Col<Row>[] = [
    { k: "include", label: "Include", csv: (i) => (i.include ? "Yes" : "No"), render: (i) => <input type="checkbox" disabled={sent} checked={!!i.include} onChange={(e) => setItem(i.employee_id, "include", e.target.checked)} aria-label={`Include ${i.name}`} /> },
    { k: "name", label: "Employee" }, { k: "department", label: "Department" }, { k: "grade", label: "Grade" },
    { k: "band", label: "Rating", render: (i) => `${i.band || ""}${i.final_score != null ? ` (${i.final_score})` : ""}`, csv: (i) => i.band },
    { k: "current_annual", label: "Current annual pay", align: "right", render: (i) => (i.current_annual ? money(i.current_annual) : <span className="text-amber-700">Not on record</span>) },
    { k: "range", label: "Matrix range", render: (i) => (i.min_pct == null ? <span className="text-amber-700">No matrix row</span> : `${i.min_pct}% to ${i.max_pct}%`), csv: (i) => (i.min_pct == null ? "" : `${i.min_pct}-${i.max_pct}`) },
    { k: "proposed_pct", label: "Proposed %", align: "right", render: (i) => <input type="number" step="0.01" min={0} max={100} disabled={sent || !i.include} className={`input !py-1 text-xs !w-20 text-right ${outside(i) ? "!border-amber-400" : ""}`} value={i.proposed_pct ?? ""} onChange={(e) => setItem(i.employee_id, "proposed_pct", e.target.value)} /> },
    { k: "increase", label: "Increase per year", align: "right", render: (i) => (i.include ? money((Number(i.current_annual) || 0) * (Number(i.proposed_pct) || 0) / 100) : ""), csv: (i) => (i.include ? (Number(i.current_annual) || 0) * (Number(i.proposed_pct) || 0) / 100 : "") },
    { k: "new_annual", label: "New annual pay", align: "right", render: (i) => (i.include && i.current_annual ? money((Number(i.current_annual) || 0) * (1 + (Number(i.proposed_pct) || 0) / 100)) : ""), csv: (i) => (i.include && i.current_annual ? Math.round((Number(i.current_annual) || 0) * (1 + (Number(i.proposed_pct) || 0) / 100)) : "") },
    { k: "reason", label: "Reason (needed outside the range)", render: (i) => <input disabled={sent || !i.include} className={`input !py-1 text-xs !w-64 ${outside(i) && !(i.reason || "").trim() ? "!border-red-400" : ""}`} placeholder={outside(i) ? "Needed: outside the range" : ""} value={i.reason || ""} onChange={(e) => setItem(i.employee_id, "reason", e.target.value)} /> },
  ];
  return (
    <Modal size="screen" title={`Increment proposal: ${cycle.name}`} subtitle="Built from published ratings and your matrix. Sending creates DRAFT revisions in Payroll. Nothing changes a salary here." onClose={onClose}
      footer={sent ? <><span className="text-xs text-gray-500 mr-auto">Sent on {fmtDateTime(p.sent_at)}{p.batch_id ? ` · batch ${p.batch_id}` : ""}. Review and approval continue in Payroll, Salary Revisions.</span><button className="btn-primary" onClick={onClose}>Close</button></>
        : <><button className="btn-secondary mr-auto" onClick={() => { if (window.confirm("Fill all lines again from the current matrix? Your edits to % and reasons are lost.")) rebuild.mutate(); }}>Fill again from the matrix</button>
          <button className="btn-secondary" onClick={onClose}>Close</button>
          <button className="btn-secondary disabled:opacity-50" disabled={save.isPending} onClick={guard(() => save.mutate())}>Save</button>
          <button className="btn-primary disabled:opacity-50" disabled={send.isPending || !p.effective_from || problems > 0 || !inc.length} title={!p.effective_from ? "Set the effective date" : problems ? "Give a reason on the lines outside their range" : ""}
            onClick={guard(() => { if (window.confirm(`Send ${inc.filter((i) => Number(i.proposed_pct) > 0).length} draft revision(s) to salary revisions, effective ${p.effective_from}? This proposal is then locked.`)) send.mutate(); }, true)}>Send to salary revisions</button></>}>
      {!(p.matrix || []).length && <Note tone="warn">No increment matrix is set, so no % was pre-filled. Set the matrix in Performance, Settings, then press Fill again from the matrix. Or type each % with a reason.</Note>}
      <FormGrid className="mb-3">
        <div><Field label="Budget for increases (per year, in dollars)" hint="All increases together, as a full dollar figure (for example 500000). Leave empty for no budget check."><Input type="number" min={0} step="1" disabled={sent} value={p.budget ?? ""} onChange={(e) => { setDirty(true); setP({ ...p, budget: e.target.value }); }} /></Field>
          <FieldError msg={v.range(p.budget, 0, undefined, "Budget")} /></div>
        <div><Field label={<>Effective date<Req /></>} hint="The date the new pay starts in Payroll; usually the 1st of the month after the appraisal period."><Input type="date" disabled={sent} value={p.effective_from || ""} onChange={(e) => { setDirty(true); setP({ ...p, effective_from: e.target.value }); }} /></Field>
          {!sent && p.effective_from && cycle.period_end && p.effective_from <= cycle.period_end && <span className="block text-[11px] text-amber-700 mt-0.5">This is on or before the appraisal period end ({fmtDate(cycle.period_end)}).</span>}
          {!sent && p.effective_from && p.effective_from < todayISO() && <span className="block text-[11px] text-amber-700 mt-0.5">This date is in the past. Check with Payroll before sending a back-dated increase.</span>}</div>
      </FormGrid>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
        <Stat label="People included" value={inc.length} sub={`of ${items.length} published`} />
        <Stat label="Total increase per year" value={money(cost)} sub={base ? `${(Math.round(10000 * cost / base) / 100)}% of ${money(base)}` : ""} accent />
        <Stat label="Budget" value={budget == null ? "Not set" : money(budget)} />
        <Stat label={budget != null && cost > budget ? "Over budget by" : "Budget left"} value={budget == null ? "Not set" : money(Math.abs(budget - cost))} sub={problems ? `${problems} line(s) need a reason` : ""} />
      </div>
      {budget != null && cost > budget && <Note tone="warn">The total increase is above the budget you typed. You can still send it; Payroll will see the same figures.</Note>}
      <Grid rows={items} cols={cols} keyOf={(i) => i.employee_id} exportName="increment-proposal.csv" title="Increase per year = current annual pay x proposed % / 100"
        empty="No published ratings in this cycle yet. Publish ratings from Calibration first." />
    </Modal>
  );
}

function PromotionsModal({ cycle, onClose }: { cycle: Row; onClose: () => void }) {
  const { data, isLoading } = useQuery({ queryKey: ["perf", "promotions", cycle.id], queryFn: () => perfApi.promotions(cycle.id) });
  return (
    <Modal title={`Promotion recommendations: ${cycle.name}`} subtitle="A list only. A promotion itself is made in the employee's career change." onClose={onClose} footer={<button className="btn-primary" onClick={onClose}>Close</button>}>
      <Grid rows={data || []} keyOf={(r) => r.review_id} loading={isLoading} exportName="promotion-recommendations.csv" empty="No manager has recommended a promotion in this cycle."
        cols={[{ k: "employee_name", label: "Employee" }, { k: "employee_number", label: "Number" }, { k: "department", label: "Department" }, { k: "designation", label: "Designation" }, { k: "grade", label: "Grade" },
          { k: "manager_name", label: "Recommended by" }, { k: "note", label: "Why" }, { k: "final_band", label: "Final rating", render: (r) => r.final_band || "Not published yet" }]} />
    </Modal>
  );
}
