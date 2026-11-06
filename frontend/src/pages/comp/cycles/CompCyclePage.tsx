import { useState } from "react";
import { Link, Navigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, BarChart3, CheckSquare, ClipboardList, Coins, Play, Settings, SlidersHorizontal, Users } from "lucide-react";
import toast from "react-hot-toast";
import { PageHeader } from "../../../components/ui";
import { money } from "../../../api/endpoints";
import { err, fmtDate, fmtDateTime } from "../../hiring/api";
import { Col, Grid, Modal, Note, Pill, Stat, Tabs } from "../../hiring/ui";
import { ccApi, pct, Row, STATUS_LABEL } from "./api";
import CycleWizard from "./CycleWizard";
import Worksheet, { BudgetBar } from "./Worksheet";
import Calibration from "./Calibration";
import { Apply, Approval } from "./ApprovalApply";
import Analytics from "./Analytics";

const TABS = [
  { key: "setup", label: "Setup", icon: Settings }, { key: "worksheet", label: "Worksheet", icon: ClipboardList },
  { key: "calibration", label: "Calibration", icon: SlidersHorizontal }, { key: "approval", label: "Approval", icon: CheckSquare },
  { key: "apply", label: "Apply", icon: Play }, { key: "analytics", label: "Analytics", icon: BarChart3 },
];

/* One cycle: stage bar + actions on top, tabs below. Managers only ever see the Worksheet. */
export default function CompCyclePage() {
  const { id = "", tab = "setup" } = useParams();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["comp", "cycle", id], queryFn: () => ccApi.cycle(id) });
  const [wizard, setWizard] = useState(false);
  const [elig, setElig] = useState(false);
  const act = useMutation({
    mutationFn: (v: { what: string }) => v.what === "submit" ? ccApi.submit(id) : v.what === "populate" ? ccApi.populate(id) : ccApi.move(id, v.what as any),
    onSuccess: (r, v) => { qc.invalidateQueries({ queryKey: ["comp"] }); toast.success(v.what === "populate" ? `${r.added} person(s) added (${r.total} in the cycle).` : v.what === "submit" ? "Sent for approval." : `Cycle is now ${STATUS_LABEL[r.status] || r.status}.`); },
    onError: (e) => toast.error(err(e), { duration: 8000 }),
  });
  if (q.isError) return <Note tone="warn">{err(q.error, "The cycle could not be loaded.")}</Note>;
  if (!q.data) return <div className="text-sm text-gray-400">Loading…</div>;
  const c = q.data;
  if (!c.is_hr) return <Navigate to={`/comp/worksheets/${id}`} replace />;
  if (!TABS.some((t) => t.key === tab)) return <Navigate to={`/comp/cycles/${id}/setup`} replace />;
  const s = c.summary || {};
  const head = c.is_hr_head;
  const btn = (what: string, label: string, primary = false) => <button className={`${primary ? "btn-primary" : "btn-secondary"} !py-1.5 text-xs`} disabled={act.isPending} onClick={() => act.mutate({ what })}>{label}</button>;
  const stages = ["draft", "open", "calibration", "approval", "applying", "applied", "closed"];
  return (
    <div>
      <PageHeader title={c.name} icon={Coins} subtitle={`${fmtDate(c.period_start)} to ${fmtDate(c.period_end)} · increases effective ${fmtDate(c.effective_from)} · ${c.budget_mode === "hard" ? "hard" : "soft"} budget limit`}
        actions={<div className="flex flex-wrap gap-2">
          <Link to="/comp/cycles" className="btn-secondary !py-1.5 text-xs inline-flex items-center gap-1"><ArrowLeft className="w-3.5 h-3.5" />Cycles</Link>
          {head && !["applying", "applied", "closed"].includes(c.status) && <button className="btn-secondary !py-1.5 text-xs" onClick={() => setWizard(true)}>Edit setup</button>}
          {["draft", "open"].includes(c.status) && <button className="btn-secondary !py-1.5 text-xs inline-flex items-center gap-1" onClick={() => setElig(true)}><Users className="w-3.5 h-3.5" />Check eligibility</button>}
          {head && ["draft", "open"].includes(c.status) && btn("populate", c.lines_count ? "Add newly eligible people" : "Build the population")}
          {head && c.status === "draft" && btn("open", "Open to managers", true)}
          {head && c.status === "open" && btn("calibrate", "Start HR calibration", true)}
          {head && c.status === "calibration" && <>{btn("reopen", "Reopen to managers")}{btn("submit", "Submit for approval", true)}</>}
          {head && c.status === "approval" && c.approval_state === "pending" && btn("withdraw", "Withdraw from approval")}
          {head && c.status === "applied" && btn("close", "Close the cycle", true)}
        </div>} />
      <div className="flex flex-wrap items-center gap-1 mb-3 text-[11px]">
        {stages.map((st, i) => <span key={st} className={`px-2 py-0.5 rounded-full ${st === c.status ? "bg-primary-50 text-primary-700 font-semibold" : stages.indexOf(c.status) > i ? "bg-green-50 text-green-700" : "bg-gray-100 text-gray-400"}`}>{STATUS_LABEL[st]}</span>)}
        {c.approval_state && <Pill s={c.approval_state} />}
      </div>
      <div className="flex flex-wrap gap-2 mb-3">
        <Stat label="People" value={s.people ?? c.lines_count ?? 0} sub={s.excluded ? `${s.excluded} excluded` : undefined} />
        <Stat label="Increase" value={money(s.increase || 0)} sub={`${pct(s.weighted_pct)} of ${money(s.base || 0)}`} accent />
        <Stat label="Lump sums" value={money(s.lump_sum || 0)} />
        <Stat label="Promotions" value={s.promotions ?? 0} />
        <Stat label="Flagged" value={s.flagged ?? 0} />
        {c.budget_usage?.["total:"] && <BudgetBar u={c.budget_usage["total:"]} label="Whole cycle budget" />}
        {c.budget_usage?.promotion?.allowed != null && <BudgetBar u={c.budget_usage.promotion} />}
      </div>
      <Tabs base={`/comp/cycles/${id}`} tabs={TABS} />
      {tab === "setup" && <Setup c={c} />}
      {tab === "worksheet" && <Worksheet cycleId={id} />}
      {tab === "calibration" && <Calibration cycleId={id} />}
      {tab === "approval" && <Approval cycleId={id} />}
      {tab === "apply" && <Apply cycleId={id} />}
      {tab === "analytics" && <Analytics cycleId={id} />}
      {wizard && <CycleWizard cycle={c} onClose={() => setWizard(false)} />}
      {elig && <EligibilityModal cycleId={id} onClose={() => setElig(false)} />}
    </div>
  );
}

function Setup({ c }: { c: Row }) {
  const e = c.eligibility || {};
  const cols: Col<Row>[] = [{ k: "rating", label: "Rating" }, { k: "cr_band", label: "Compa band", render: (m) => m.cr_band || "any" }, { k: "min_pct", label: "Min", align: "right", render: (m) => pct(m.min_pct) },
    { k: "rec_pct", label: "Recommended", align: "right", render: (m) => <b>{pct(m.rec_pct)}</b> }, { k: "max_pct", label: "Max", align: "right", render: (m) => pct(m.max_pct) }];
  const bcols: Col<Row>[] = [{ k: "scope", label: "Scope" }, { k: "key", label: "Department / entity", render: (b) => b.key || "whole cycle" }, { k: "amount", label: "Amount $/yr", align: "right", render: (b) => b.amount == null ? "—" : money(b.amount) }, { k: "pct", label: "% of base", align: "right", render: (b) => b.pct == null ? "—" : pct(b.pct) }];
  return (
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
      <div className="card">
        <h3 className="font-display font-semibold text-gray-800 mb-2">Eligible population</h3>
        <dl className="grid grid-cols-[10rem_1fr] gap-y-1 text-sm">
          <dt className="text-gray-500">Departments</dt><dd>{(e.departments || []).join(", ") || "All"}</dd>
          <dt className="text-gray-500">Grades</dt><dd>{(e.grades || []).join(", ") || "All"}</dd>
          <dt className="text-gray-500">Entities</dt><dd>{(e.entity_ids || []).join(", ") || "All"}</dd>
          <dt className="text-gray-500">Joined before</dt><dd>{e.joined_before ? fmtDate(e.joined_before) : "Any date"}</dd>
          <dt className="text-gray-500">Rating required</dt><dd>{e.require_rating ? "Yes" : "No"}</dd>
          <dt className="text-gray-500">In notice</dt><dd>{e.exclude_notice === false ? "Included" : "Left out"}</dd>
          <dt className="text-gray-500">Ratings from</dt><dd>{c.perf_cycle_id ? "the chosen appraisal cycle" : "each person's latest published rating"}</dd>
          <dt className="text-gray-500">Lump sums</dt><dd>{c.allow_lump_sum ? "Allowed" : "Off"}</dd>
          <dt className="text-gray-500">Promotion budget</dt><dd>{c.promotion_budget == null ? "None" : money(c.promotion_budget)}</dd>
          <dt className="text-gray-500">Default budget</dt><dd>{c.default_budget_pct == null ? "—" : `${pct(c.default_budget_pct)} of base for groups without a line`}</dd>
        </dl>
        {c.guidelines && <div className="mt-3 text-sm"><div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">Guidelines</div><div className="whitespace-pre-wrap text-gray-700">{c.guidelines}</div></div>}
      </div>
      <div className="space-y-4">
        <Grid rows={(c.matrix || []).map((m: Row, i: number) => ({ ...m, id: String(i) }))} cols={cols} keyOf={(m) => m.id} title="Merit matrix (frozen on this cycle)" empty="No matrix rows: every proposal will be flagged 'no matrix row'." />
        <Grid rows={(c.budgets || []).map((b: Row, i: number) => ({ ...b, id: String(i) }))} cols={bcols} keyOf={(b) => b.id} title="Budgets" empty="No explicit budgets: the default % applies to every department." />
        <div className="card"><h3 className="font-display font-semibold text-gray-800 mb-1">Grade midpoints</h3>
          <div className="flex flex-wrap gap-2 text-xs">{Object.entries(c.grade_midpoints || {}).map(([g, v]) => <span key={g} className="badge-purple">{g}: {money(Number(v))}</span>)}{!Object.keys(c.grade_midpoints || {}).length && <span className="text-gray-400">None: compa-ratios are unknown.</span>}</div></div>
      </div>
      <div className="xl:col-span-2"><Grid rows={c.events || []} cols={[{ k: "at", label: "When", render: (x) => fmtDateTime(x.at) }, { k: "action", label: "Action", render: (x) => <Pill s={x.action} /> }, { k: "by_user", label: "By" }, { k: "by_role", label: "Role" }, { k: "detail", label: "Detail", render: (x) => JSON.stringify(x.detail || {}), csv: (x) => JSON.stringify(x.detail || {}) }] as Col<Row>[]} keyOf={(x) => x.id} title="Cycle history" /></div>
    </div>
  );
}

function EligibilityModal({ cycleId, onClose }: { cycleId: string; onClose: () => void }) {
  const q = useQuery({ queryKey: ["comp", "eligibility", cycleId], queryFn: () => ccApi.eligibility(cycleId) });
  const cols: Col<Row>[] = [
    { k: "employee_number", label: "Emp no" }, { k: "employee_name", label: "Name" }, { k: "department", label: "Dept" }, { k: "grade", label: "Grade" }, { k: "manager_name", label: "Manager" },
    { k: "hire_date", label: "Joined", render: (p) => fmtDate(p.hire_date) }, { k: "current_annual", label: "Current $/yr", align: "right", render: (p) => money(p.current_annual) },
    { k: "rating", label: "Rating", render: (p) => p.rating || <span className="text-gray-400">none</span> },
    { k: "eligible", label: "Eligible", render: (p) => p.eligible ? <span className="text-green-700 font-semibold">Yes</span> : <span className="text-gray-500">No</span>, csv: (p) => p.eligible ? "yes" : "no" },
    { k: "reason", label: "Why not" }, { k: "in_cycle", label: "In cycle", render: (p) => p.in_cycle ? "Yes" : "", csv: (p) => p.in_cycle ? "yes" : "" },
  ];
  return (
    <Modal title="Who is eligible" size="screen" onClose={onClose} subtitle={q.data ? `${q.data.eligible} eligible · ${q.data.excluded} not eligible · ${q.data.in_cycle} already in the cycle` : ""}>
      <Grid rows={q.data?.people || []} cols={cols} keyOf={(p) => p.employee_id} loading={q.isLoading} exportName="comp-eligibility.csv" />
    </Modal>
  );
}
