import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import toast from "react-hot-toast";
import { err } from "../../hiring/api";
import { Field, Input, Modal, Note, Select, Textarea } from "../../hiring/ui";
import { FormSection } from "../../../components/Modal";
import { collect, FieldError, firstError, v } from "../../../lib/validators";
import { perfApi } from "../../performance/api";
import { ccApi, Row } from "./api";
import { todayISO } from "../../../lib/datetime";   // 2026-10-25: UTC store, org-zone today

/* Cycle setup wizard: 1 Basics · 2 Eligible population · 3 Budgets · 4 Merit matrix & guidelines.
   Every rule is frozen ON the cycle, so later setting changes never move numbers people saw. */
const STEPS = ["Basics", "Eligible population", "Budgets", "Merit matrix & guidelines"];
const blank = (x: unknown) => x === undefined || x === null || String(x).trim() === "";
const todayIso = () => todayISO();

/** Every check of the wizard, keyed `<step>.<field>` so Save can jump to the step with the first problem. */
export function wizardErrors(f: Row, draft: boolean): Record<string, string> {
  const checks: Record<string, string | null> = {
    "0.name": v.required(f.name, "The cycle name") || v.maxLen(f.name, 160, "The cycle name"),
    "0.period_start": draft ? v.required(f.period_start, "The period start") : null,
    "0.period_end": draft ? (v.required(f.period_end, "The period end")
      || (f.period_start && f.period_end && f.period_end <= f.period_start ? "The period end must be after its start" : null)) : null,
    "0.effective_from": draft ? v.required(f.effective_from, "The effective date") : null,
    "2.default_budget_pct": v.percent(f.default_budget_pct, "The default budget %"),
    "2.promotion_budget": v.range(f.promotion_budget, 0, undefined, "The promotion budget"),
  };
  const seenB = new Set<string>();
  (f.budgets as Row[] || []).forEach((b, i) => {
    const who = b.scope === "total" ? "the whole cycle" : (b.key || `line ${i + 1}`);
    const k = `${b.scope}:${String(b.scope === "total" ? "" : b.key || "").trim().toLowerCase()}`;
    checks[`2.budget${i}`] = (b.scope !== "total" && blank(b.key) ? `Budget line ${i + 1}: name the ${b.scope} it applies to` : null)
      || (blank(b.amount) && blank(b.pct) ? `Budget for ${who}: give an amount or a %` : null)
      || v.range(b.amount, 0, undefined, `Budget amount for ${who}`) || v.percent(b.pct, `Budget % for ${who}`)
      || (seenB.has(k) ? `There are two budget lines for ${who}` : null);
    seenB.add(k);
  });
  if (draft) {
    const seenM = new Set<string>();
    (f.matrix as Row[] || []).forEach((m, i) => {
      const r = String(m.rating || "").trim();
      const k = `${r.toLowerCase()}|${m.cr_band || ""}`;
      const lo = Number(m.min_pct), hi = Number(m.max_pct), rec = Number(m.rec_pct);
      checks[`3.matrix${i}`] = (!r ? `Matrix row ${i + 1}: give the rating` : null)
        || (blank(m.min_pct) || blank(m.max_pct) ? `Matrix row ${r || i + 1}: give a minimum % and a maximum %` : null)
        || v.percent(m.min_pct, `Row ${r}: minimum %`) || v.percent(m.max_pct, `Row ${r}: maximum %`) || v.percent(m.rec_pct, `Row ${r}: recommended %`)
        || (lo > hi ? `Row ${r}: the minimum % cannot be above the maximum %` : null)
        || (!blank(m.rec_pct) && (rec < lo || rec > hi) ? `Row ${r}: the recommended % must sit between the minimum and maximum` : null)
        || (seenM.has(k) ? `There are two matrix rows for ${r}${m.cr_band ? ` / ${m.cr_band}` : ""}` : null);
      seenM.add(k);
    });
    Object.entries(f.grade_midpoints || {}).forEach(([g, val], i) => {
      checks[`3.mid${i}`] = (blank(g) && !blank(val) ? `Midpoint row ${i + 1}: give the grade` : null)
        || (!blank(g) && !blank(val) && !(Number(val) > 0) ? `The midpoint for ${g} must be more than 0` : null);
    });
  }
  return collect(checks);
}

export default function CycleWizard({ cycle, onClose }: { cycle: Row | null; onClose: () => void }) {
  const qc = useQueryClient();
  const settings = useQuery({ queryKey: ["comp", "settings"], queryFn: ccApi.settings });
  const perfCycles = useQuery({ queryKey: ["perf", "cycles"], queryFn: perfApi.cycles });
  const [step, setStep] = useState(0);
  const [showErr, setShowErr] = useState(false);
  const draft = !cycle || cycle.status === "draft";
  const s = settings.data;
  const [f, setF] = useState<Row | null>(cycle ? JSON.parse(JSON.stringify(cycle)) : null);
  // A new cycle starts from the org's Compensation settings once they have loaded.
  useEffect(() => {
    if (f || !s) return;
    setF({ name: "", period_start: "", period_end: "", effective_from: "", perf_cycle_id: "", allow_lump_sum: s.allow_lump_sum,
      eligibility: { departments: [], grades: [], entity_ids: [], joined_before: "", require_rating: false, exclude_notice: true },
      budgets: [], budget_mode: s.budget_mode, default_budget_pct: s.default_budget_pct, promotion_budget: "",
      matrix: s.default_matrix || [], compa_bands: s.compa_bands, grade_midpoints: { ...(s.grade_midpoints || {}) }, guidelines: s.guidelines || "" });
  }, [s, f]);
  // Hooks stay above every early return (the mutation used to sit below them).
  const save = useMutation({
    mutationFn: (fv: Row) => {
      const body: Row = draft ? { ...fv, name: String(fv.name).trim(), promotion_budget: fv.promotion_budget === "" ? null : fv.promotion_budget, perf_cycle_id: fv.perf_cycle_id || null,
        grade_midpoints: Object.fromEntries(Object.entries(fv.grade_midpoints || {}).filter(([k, val]) => k && val !== "" && val != null)) }
        : { name: String(fv.name).trim(), budgets: fv.budgets, budget_mode: fv.budget_mode, default_budget_pct: fv.default_budget_pct, guidelines: fv.guidelines,
          promotion_budget: fv.promotion_budget === "" ? null : fv.promotion_budget };
      return cycle ? ccApi.update(cycle.id, body) : ccApi.create(body);
    },
    onSuccess: () => { toast.success(cycle ? "Cycle saved." : "Cycle created as a draft. Next: check who is eligible and build the population."); qc.invalidateQueries({ queryKey: ["comp"] }); onClose(); },
    onError: (e) => toast.error(err(e), { duration: 7000 }),
  });
  if (!s || !f) return null;
  const set = (k: string, val: any) => setF((p) => ({ ...p!, [k]: val }));
  const setE = (k: string, val: any) => setF((p) => ({ ...p!, eligibility: { ...p!.eligibility, [k]: val } }));
  const list = (t: string) => t.split(",").map((x) => x.trim()).filter(Boolean);
  const errs = wizardErrors(f, draft);
  const e = (k: string) => (showErr ? errs[k] : undefined);
  const stepHasErr = (i: number) => showErr && Object.keys(errs).some((k) => k.startsWith(`${i}.`));
  const submit = () => {
    if (Object.keys(errs).length) {
      setShowErr(true);
      const first = Object.keys(errs)[0];
      setStep(Number(first.split(".")[0]) || 0);
      toast.error(firstError(errs));
      return;
    }
    save.mutate(f);
  };
  const bandKeys: string[] = (f.compa_bands || []).map((b: Row) => b.key);
  const upd = (key: string, i: number, k: string, val: any) => set(key, f[key].map((r: Row, j: number) => (j === i ? { ...r, [k]: val } : r)));
  const del = (key: string, i: number) => set(key, f[key].filter((_: any, j: number) => j !== i));
  const midRows = Object.entries(f.grade_midpoints || {});
  const pastEffective = draft && f.effective_from && f.effective_from < todayIso();
  const body = (
    <div>
      <div className="flex gap-1 mb-4 overflow-x-auto">
        {STEPS.map((t, i) => <button key={t} type="button" onClick={() => setStep(i)} className={`px-3 py-1.5 rounded-lg text-xs font-medium whitespace-nowrap ${i === step ? "bg-primary-50 text-primary-600" : "text-gray-500 hover:bg-gray-100"} ${stepHasErr(i) ? "ring-1 ring-red-300 text-red-600" : ""}`}>{i + 1}. {t}{stepHasErr(i) ? " •" : ""}</button>)}
      </div>
      {!draft && <Note>The cycle is already open: only the name, budgets and guidelines can still change. The matrix, bands and midpoints stay as managers saw them.</Note>}
      {step === 0 && <>
        <FormSection title="Cycle">
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-x-4 gap-y-3">
            <Field label="Cycle name *" span={2}><Input maxLength={160} value={f.name} onChange={(ev) => set("name", ev.target.value)} placeholder="FY 2026-27 merit cycle" /><FieldError msg={e("0.name")} /></Field>
            <Field label="Currency" hint="Compensation cycles run in Canadian dollars."><Input value="CAD ($)" disabled /></Field>
          </div>
        </FormSection>
        <FormSection title="Timeline" hint="The review period the increases are for, and the date the new pay starts.">
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-x-4 gap-y-3">
            <Field label="Period start *"><Input type="date" disabled={!draft} value={f.period_start || ""} onChange={(ev) => set("period_start", ev.target.value)} /><FieldError msg={e("0.period_start")} /></Field>
            <Field label="Period end *" hint="Must be after the start."><Input type="date" disabled={!draft} min={f.period_start || undefined} value={f.period_end || ""} onChange={(ev) => set("period_end", ev.target.value)} /><FieldError msg={e("0.period_end")} /></Field>
            <Field label="Increases effective from *" hint="A past date creates a retro-pay proposal in payroll when applied."><Input type="date" disabled={!draft} value={f.effective_from || ""} onChange={(ev) => set("effective_from", ev.target.value)} /><FieldError msg={e("0.effective_from")} /></Field>
          </div>
          {pastEffective && <div className="mt-2"><Note tone="warn">The effective date is in the past: applying this cycle will create arrears for the months already paid.</Note></div>}
        </FormSection>
        <FormSection title="Ratings and options">
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-x-4 gap-y-3">
            <Field label="Ratings from appraisal cycle" hint="Leave empty to use each person's latest published rating." span={2}>
              <Select disabled={!draft} value={f.perf_cycle_id || ""} onChange={(ev) => set("perf_cycle_id", ev.target.value)}>
                <option value="">Latest published rating</option>
                {(perfCycles.data || []).map((c: Row) => <option key={c.id} value={c.id}>{c.name} ({c.status})</option>)}
              </Select></Field>
            <Field label="Lump-sum option" hint="A one-time payout that does not change annual pay."><label className="flex items-center gap-2 text-sm mt-1.5"><input type="checkbox" disabled={!draft} checked={!!f.allow_lump_sum} onChange={(ev) => set("allow_lump_sum", ev.target.checked)} />Managers may propose a one-time lump sum</label></Field>
          </div>
        </FormSection>
      </>}
      {step === 1 && <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        <Field label="Departments (comma-separated, empty = all)" span={2}><Input disabled={!draft} value={(f.eligibility.departments || []).join(", ")} onChange={(ev) => setE("departments", list(ev.target.value))} placeholder="Tech, Sales" /></Field>
        <Field label="Grades (empty = all)"><Input disabled={!draft} value={(f.eligibility.grades || []).join(", ")} onChange={(ev) => setE("grades", list(ev.target.value))} placeholder="L1, L2" /></Field>
        <Field label="Entity ids (empty = all)" hint="Legal entities, when the org has more than one."><Input disabled={!draft} value={(f.eligibility.entity_ids || []).join(", ")} onChange={(ev) => setE("entity_ids", list(ev.target.value))} /></Field>
        <Field label="Joined before" hint="Leave out recent joiners, e.g. anyone who joined after 1 January."><Input type="date" disabled={!draft} value={f.eligibility.joined_before || ""} onChange={(ev) => setE("joined_before", ev.target.value)} /></Field>
        <div className="flex flex-col gap-2 pt-5 text-sm">
          <label className="flex items-center gap-2"><input type="checkbox" disabled={!draft} checked={!!f.eligibility.require_rating} onChange={(ev) => setE("require_rating", ev.target.checked)} />Only people with a published rating</label>
          <label className="flex items-center gap-2"><input type="checkbox" disabled={!draft} checked={f.eligibility.exclude_notice !== false} onChange={(ev) => setE("exclude_notice", ev.target.checked)} />Leave out people serving notice</label>
        </div>
        <div className="md:col-span-2 xl:col-span-3"><Note>People who have left, are still onboarding, are hourly paid or have no salary on record are never eligible. After saving, open the cycle and use "Check eligibility" to see everyone with the reason they are in or out.</Note></div>
      </div>}
      {step === 2 && <div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-3">
          <Field label="Budget limit" hint="Hard: a proposal that overruns its budget is refused. Soft: it is flagged."><Select value={f.budget_mode} onChange={(ev) => set("budget_mode", ev.target.value)}><option value="soft">Soft (flag only)</option><option value="hard">Hard (refuse)</option></Select></Field>
          <Field label="Default budget % of base" hint="For a department / entity without its own line below. 0 to 100, e.g. 8 for 8%."><Input type="number" min={0} max={100} step="0.5" value={f.default_budget_pct ?? ""} onChange={(ev) => set("default_budget_pct", ev.target.value)} /><FieldError msg={e("2.default_budget_pct")} /></Field>
          <Field label="Promotion budget ($ per year)" hint="Increases of promoted people count against this too. Blank = no separate limit."><Input type="number" min={0} value={f.promotion_budget ?? ""} onChange={(ev) => set("promotion_budget", ev.target.value)} /><FieldError msg={e("2.promotion_budget")} /></Field>
        </div>
        <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Budgets by department / entity / whole cycle</h4>
        <p className="text-xs text-gray-500 mb-2">Give an amount ($ per year) or a % of that group's current annual pay. The whole-cycle budget defaults to the sum of the department lines. One line per department / entity.</p>
        {(f.budgets as Row[]).map((b, i) => (
          <div key={i} className="mb-1.5">
            <div className="grid grid-cols-2 sm:grid-cols-[9rem_1fr_9rem_6rem_2rem] gap-2 items-end">
              <Field label={i ? "" : "Scope"}><Select value={b.scope} onChange={(ev) => upd("budgets", i, "scope", ev.target.value)}><option value="department">Department</option><option value="entity">Entity</option><option value="total">Whole cycle</option></Select></Field>
              <Field label={i ? "" : "Department / entity"}><Input disabled={b.scope === "total"} value={b.scope === "total" ? "" : b.key || ""} onChange={(ev) => upd("budgets", i, "key", ev.target.value)} placeholder={b.scope === "total" ? "whole cycle" : ""} /></Field>
              <Field label={i ? "" : "Amount $ / year"}><Input type="number" min={0} value={b.amount ?? ""} onChange={(ev) => upd("budgets", i, "amount", ev.target.value === "" ? null : ev.target.value)} /></Field>
              <Field label={i ? "" : "or %"}><Input type="number" min={0} max={100} step="0.5" value={b.pct ?? ""} onChange={(ev) => upd("budgets", i, "pct", ev.target.value === "" ? null : ev.target.value)} /></Field>
              <button className="icon-btn mb-1" title="Remove" onClick={() => del("budgets", i)}><Trash2 className="w-3.5 h-3.5 text-gray-400" /></button>
            </div>
            <FieldError msg={e(`2.budget${i}`)} />
          </div>))}
        <button className="btn-secondary !py-1 !px-2 text-xs inline-flex items-center gap-1 mt-1" onClick={() => set("budgets", [...f.budgets, { scope: "department", key: "", amount: null, pct: null }])}><Plus className="w-3 h-3" />Add a budget line</button>
      </div>}
      {step === 3 && <div className="grid grid-cols-1 xl:grid-cols-[1fr_20rem] gap-4">
        <div>
          <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Merit matrix: rating × compa-ratio band → recommended %</h4>
          <p className="text-xs text-gray-500 mb-2">Leave the band empty for a row that applies to every band; a row with a band wins for that band. Managers see the recommended % pre-filled and the min / max as guard rails; outside the range is flagged in calibration. All % are 0 to 100 with min ≤ recommended ≤ max; a blank recommended % uses the middle of the range.</p>
          {(f.matrix as Row[]).map((m, i) => (
            <div key={i} className="mb-1.5">
              <div className="grid grid-cols-2 sm:grid-cols-[1fr_8rem_6rem_6rem_6rem_2rem] gap-2 items-end">
                <Field label={i ? "" : "Rating (as published) *"}><Input disabled={!draft} value={m.rating || ""} onChange={(ev) => upd("matrix", i, "rating", ev.target.value)} /></Field>
                <Field label={i ? "" : "Compa band"}><Select disabled={!draft} value={m.cr_band || ""} onChange={(ev) => upd("matrix", i, "cr_band", ev.target.value)}><option value="">Any band</option>{bandKeys.map((k) => <option key={k} value={k}>{k}</option>)}</Select></Field>
                <Field label={i ? "" : "Min % *"}><Input type="number" min={0} max={100} step="0.5" disabled={!draft} value={m.min_pct ?? ""} onChange={(ev) => upd("matrix", i, "min_pct", ev.target.value)} /></Field>
                <Field label={i ? "" : "Recommended %"}><Input type="number" min={0} max={100} step="0.5" disabled={!draft} value={m.rec_pct ?? ""} onChange={(ev) => upd("matrix", i, "rec_pct", ev.target.value)} /></Field>
                <Field label={i ? "" : "Max % *"}><Input type="number" min={0} max={100} step="0.5" disabled={!draft} value={m.max_pct ?? ""} onChange={(ev) => upd("matrix", i, "max_pct", ev.target.value)} /></Field>
                <div className="pb-1">{draft && <button className="icon-btn" title="Remove" onClick={() => del("matrix", i)}><Trash2 className="w-3.5 h-3.5 text-gray-400" /></button>}</div>
              </div>
              <FieldError msg={e(`3.matrix${i}`)} />
            </div>))}
          {draft && <button className="btn-secondary !py-1 !px-2 text-xs inline-flex items-center gap-1 mt-1" onClick={() => set("matrix", [...f.matrix, { rating: "", cr_band: "", min_pct: "", rec_pct: "", max_pct: "" }])}><Plus className="w-3 h-3" />Add a matrix row</button>}
          <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1 mt-4">Guidelines shown to managers</h4>
          <Textarea rows={4} maxLength={4000} value={f.guidelines || ""} onChange={(ev) => set("guidelines", ev.target.value)} placeholder="What managers should keep in mind when they propose increases." />
          <span className="block text-[11px] text-gray-500 mt-0.5">Shown at the top of every worksheet (max 4,000 characters).</span>
        </div>
        <div>
          <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Grade midpoints ($ per year)</h4>
          <p className="text-xs text-gray-500 mb-2">Compa-ratio = current annual pay ÷ the grade midpoint. A grade without a midpoint has no compa-ratio (flagged, not guessed). Midpoints must be more than 0.</p>
          {midRows.map(([g, val], i) => (
            <div key={i} className="mb-1.5">
              <div className="grid grid-cols-[1fr_1fr_2rem] gap-2 items-center">
                <Input disabled={!draft} value={g} onChange={(ev) => { const o = Object.entries(f.grade_midpoints); o[i] = [ev.target.value, val]; set("grade_midpoints", Object.fromEntries(o)); }} placeholder="Grade" />
                <Input type="number" min={1} disabled={!draft} value={val as any} onChange={(ev) => set("grade_midpoints", { ...f.grade_midpoints, [g]: ev.target.value })} placeholder="Midpoint" />
                {draft && <button className="icon-btn" title="Remove" onClick={() => { const o = { ...f.grade_midpoints }; delete o[g]; set("grade_midpoints", o); }}><Trash2 className="w-3.5 h-3.5 text-gray-400" /></button>}
              </div>
              <FieldError msg={e(`3.mid${i}`)} />
            </div>))}
          {draft && <button className="btn-secondary !py-1 !px-2 text-xs inline-flex items-center gap-1 mt-1" disabled={"" in (f.grade_midpoints || {})} title={"" in (f.grade_midpoints || {}) ? "Name the new grade first" : undefined} onClick={() => set("grade_midpoints", { ...f.grade_midpoints, "": "" })}><Plus className="w-3 h-3" />Add a grade</button>}
          <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1 mt-4">Compa-ratio bands</h4>
          {(f.compa_bands as Row[]).map((b, i) => <div key={i} className="text-xs text-gray-600 mb-0.5"><b>{b.key}</b> · {b.label} ({b.min_cr ?? "…"} to {b.max_cr ?? "…"})</div>)}
          <div className="text-[11px] text-gray-400 mt-1">Bands come from Compensation settings and are frozen on the cycle.</div>
        </div>
      </div>}
    </div>
  );
  return (
    <Modal title={cycle ? `Cycle setup: ${cycle.name}` : "Create a compensation cycle"} size="screen" onClose={onClose}
      subtitle="Everything here is frozen on the cycle when it opens, so later setting changes do not move numbers managers have already seen. Fields marked * are required."
      footer={<>
        <button className="btn-secondary" onClick={onClose}>Cancel</button>
        {step > 0 && <button className="btn-secondary" onClick={() => setStep(step - 1)}>Back</button>}
        {step < STEPS.length - 1 && <button className="btn-secondary" onClick={() => setStep(step + 1)}>Next</button>}
        <button className="btn-primary disabled:opacity-50" disabled={save.isPending} onClick={submit}>{cycle ? "Save" : "Create draft"}</button>
      </>}>
      {body}
    </Modal>
  );
}
