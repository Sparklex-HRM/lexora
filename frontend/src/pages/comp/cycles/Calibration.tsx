import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { money } from "../../../api/endpoints";
import { err } from "../../hiring/api";
import { Col, Grid, Input, Modal, Note, Select, Stat, Textarea } from "../../hiring/ui";
import { ccApi, cr, pct, Row } from "./api";
import { BudgetBar } from "./Worksheet";
import { FormGrid, FormSection } from "../../../components/Modal";
import { collect, FieldError, firstError, Req, v } from "../../../lib/validators";

const MODE_LABEL: Record<string, string> = {
  to_recommended: "Reset to the matrix recommendation", cap_to_max: "Cap at the matrix maximum", scale: "Multiply the proposed % by a factor",
  set_pct: "Set the proposed % to a value", exclude: "Exclude from the cycle", include: "Include in the cycle",
};

/** Bulk-adjust checks: a value for scale / set %, and an audit reason for anything that changes numbers or drops people. */
export function bulkErrors(b: Row): Record<string, string> {
  const needsValue = b.mode === "scale" || b.mode === "set_pct";
  return collect({
    value: needsValue ? (v.required(b.value, b.mode === "scale" ? "The factor" : "The proposed %")
      || (b.mode === "scale" ? (Number(b.value) <= 0 ? "The factor must be more than 0" : v.range(b.value, 0, 5, "The factor")) : v.percent(b.value, "The proposed %"))) : null,
    reason: b.mode !== "include" ? (v.required(b.reason, "A reason") || v.maxLen(b.reason, 500, "The reason")) : v.maxLen(b.reason, 500, "The reason"),
  });
}

/* HR calibration: department × rating grid, distributions, outliers, budgets, bulk adjust, lock. */
export function Bars({ rows, label, value, max, fmt, color = "bg-primary-500" }: { rows: Row[]; label: (r: Row) => string; value: (r: Row) => number; max?: number; fmt: (v: number) => string; color?: string }) {
  const top = max ?? Math.max(1, ...rows.map(value));
  return (
    <div className="space-y-1">
      {rows.map((r, i) => <div key={i} className="grid grid-cols-[8rem_1fr_5rem] items-center gap-2 text-xs">
        <span className="truncate text-gray-600" title={label(r)}>{label(r)}</span>
        <div className="h-3 bg-gray-100 rounded"><div className={`h-3 rounded ${color}`} style={{ width: `${Math.min(100, (value(r) / top) * 100)}%` }} /></div>
        <span className="text-right tabular-nums text-gray-700">{fmt(value(r))}</span>
      </div>)}
      {!rows.length && <div className="text-xs text-gray-400">Nothing to show.</div>}
    </div>
  );
}

export default function Calibration({ cycleId }: { cycleId: string }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["comp", "calibration", cycleId], queryFn: () => ccApi.calibration(cycleId) });
  const [bulk, setBulk] = useState<Row | null>(null);
  const [showErr, setShowErr] = useState(false);
  const openBulk = (b: Row) => { setShowErr(false); setBulk(b); };
  const applyBulk = () => {
    const e = bulkErrors(bulk!);
    if (Object.keys(e).length) { setShowErr(true); toast.error(firstError(e)); return; }
    const body: Row = { mode: bulk!.mode, reason: String(bulk!.reason || "").trim() };
    if (bulk!.mode === "scale" || bulk!.mode === "set_pct") body.value = Number(bulk!.value);
    for (const k of ["department", "rating", "grade"]) if (bulk![k]) body[k] = bulk![k];
    run.mutate(body);
  };
  const run = useMutation({ mutationFn: (b: Row) => ccApi.bulk(cycleId, b), onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["comp"] }); setBulk(null); toast.success(`${r.changed} line(s) changed.`); }, onError: (e) => toast.error(err(e)) });
  if (q.isError) return <Note tone="warn">{err(q.error, "Calibration could not be loaded.")}</Note>;
  if (!q.data) return <div className="text-sm text-gray-400">Loading…</div>;
  const { cycle, summary: s, budget_usage: bu, by_department, by_rating, grid, compa, outliers, lines = [] } = q.data;
  const grades: string[] = Array.from(new Set<string>((lines as Row[]).map((l) => l.grade).filter(Boolean))).sort();
  const matching = bulk ? (lines as Row[]).filter((l) => (!bulk.department || (l.department || "") === bulk.department) && (!bulk.rating || (l.rating || "") === bulk.rating) && (!bulk.grade || (l.grade || "") === bulk.grade)) : [];
  const bErr = bulk && showErr ? bulkErrors(bulk) : {};
  const canEdit = ["open", "calibration"].includes(cycle.status);
  const ocols: Col<Row>[] = [
    { k: "employee_number", label: "Emp no" }, { k: "employee_name", label: "Name" }, { k: "department", label: "Dept" }, { k: "manager_name", label: "Manager" },
    { k: "rating", label: "Rating" }, { k: "compa_ratio", label: "Compa", align: "right", render: (l) => cr(l.compa_ratio) },
    { k: "rec_pct", label: "Matrix", align: "right", render: (l) => l.rec_pct == null ? "—" : `${pct(l.rec_pct)} (${pct(l.min_pct)}–${pct(l.max_pct)})`, csv: (l) => l.rec_pct },
    { k: "proposed_pct", label: "Proposed", align: "right", render: (l) => <b>{pct(l.proposed_pct)}</b> }, { k: "proposed_amount", label: "$", align: "right", render: (l) => money(l.proposed_amount) },
    { k: "flags", label: "Why flagged", render: (l) => <span className="text-amber-700">{(l.flags || []).map((f: Row) => f.text).join("; ")}</span>, csv: (l) => (l.flags || []).map((f: Row) => f.text).join("; ") },
    { k: "manager_comment", label: "Manager note" }, { k: "hr_comment", label: "HR note" },
  ];
  return (
    <div>
      <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-3 mb-3">
        <Stat label="People" value={s.people} sub={s.excluded ? `${s.excluded} excluded` : undefined} />
        <Stat label="Base" value={money(s.base)} />
        <Stat label="Increase" value={money(s.increase)} sub={`${pct(s.weighted_pct)} weighted`} accent />
        <Stat label="Lump sums" value={money(s.lump_sum)} />
        <Stat label="Budget" value={s.budget == null ? "—" : money(s.budget)} sub={s.remaining == null ? undefined : s.over_budget ? `over by ${money(-s.remaining)}` : `${money(s.remaining)} left`} />
        <Stat label="Avg %" value={pct(s.avg_pct)} />
        <Stat label="Promotions" value={s.promotions} />
        <Stat label="Flagged" value={s.flagged} />
      </div>
      <div className="flex flex-wrap gap-2 mb-3">{Object.values(bu || {}).filter((u: any) => u.allowed != null).map((u: any) => <BudgetBar key={`${u.scope}:${u.key}`} u={u} />)}</div>
      {canEdit && <div className="flex flex-wrap gap-2 mb-3">
        <button className="btn-secondary !py-1 !px-2 text-xs" onClick={() => openBulk({ mode: "to_recommended" })}>Reset to matrix recommendation…</button>
        <button className="btn-secondary !py-1 !px-2 text-xs" onClick={() => openBulk({ mode: "cap_to_max" })}>Cap at matrix maximum…</button>
        <button className="btn-secondary !py-1 !px-2 text-xs" onClick={() => openBulk({ mode: "scale", value: 0.9 })}>Scale all by a factor…</button>
        <button className="btn-secondary !py-1 !px-2 text-xs" onClick={() => openBulk({ mode: "set_pct", value: 0 })}>Set a % for a group…</button>
      </div>}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4 mb-4">
        <div className="card"><h3 className="font-display font-semibold text-gray-800 mb-2">Average % by department</h3><Bars rows={by_department} label={(r) => r.key} value={(r) => r.weighted_pct || 0} fmt={pct} /></div>
        <div className="card"><h3 className="font-display font-semibold text-gray-800 mb-2">Average % by rating</h3><Bars rows={by_rating} label={(r) => `${r.key} (${r.people})`} value={(r) => r.avg_pct || 0} fmt={pct} color="bg-amber-500" /></div>
        <div className="card"><h3 className="font-display font-semibold text-gray-800 mb-2">Compa-ratio bands: before → after</h3>
          <table className="w-full text-xs"><thead className="text-gray-400 text-[10px] uppercase"><tr><th className="text-left py-1">Band</th><th className="text-right">Before</th><th className="text-right">After</th></tr></thead>
            <tbody>{compa.map((r: Row) => <tr key={r.key} className="border-t border-surface-border"><td className="py-1">{r.label}</td><td className="text-right tabular-nums">{r.before}</td><td className="text-right tabular-nums font-semibold">{r.after}</td></tr>)}</tbody></table></div>
      </div>
      <div className="card !p-0 mb-4 overflow-x-auto">
        <div className="px-3 py-2 border-b border-surface-border font-display font-semibold text-gray-800 text-sm">Department × rating: people and average %</div>
        <table className="w-full text-xs"><thead className="bg-surface text-gray-400 text-[10px] uppercase tracking-wide"><tr><th className="text-left px-3 py-2">Department</th>{grid.ratings.map((r: string) => <th key={r} className="text-right px-3 py-2">{r}</th>)}</tr></thead>
          <tbody>{grid.rows.map((row: Row) => <tr key={row.department} className="border-t border-surface-border"><td className="px-3 py-1.5 font-medium text-gray-700">{row.department}</td>
            {grid.ratings.map((r: string) => { const c = row.cells[r]; return <td key={r} className="px-3 py-1.5 text-right tabular-nums">{c ? <span>{c.people} <span className="text-gray-400">· {pct(c.avg_pct)}</span></span> : <span className="text-gray-300">—</span>}</td>; })}</tr>)}</tbody></table>
      </div>
      <Grid rows={outliers} cols={ocols} keyOf={(l) => l.id} title={`Outliers (${outliers.length})`} exportName="comp-outliers.csv" empty="No outliers: every line sits inside the matrix and its budget." />
      {bulk && <Modal title="Bulk adjust" size="lg" onClose={() => setBulk(null)} subtitle="Applies to every line that matches the filters. Each change is recorded against HR with your reason."
        footer={<><span className="mr-auto text-xs text-gray-500">{matching.length} line(s) match</span><button className="btn-secondary" onClick={() => setBulk(null)}>Cancel</button><button className="btn-primary disabled:opacity-50" disabled={run.isPending || !matching.length} onClick={applyBulk}>Apply to {matching.length}</button></>}>
        <FormSection title="What to do">
          <FormGrid>
            <div><label className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">Action<Req /></label><Select value={bulk.mode} onChange={(e) => setBulk({ ...bulk, mode: e.target.value, value: e.target.value === "scale" ? 0.9 : e.target.value === "set_pct" ? 0 : undefined })}>
              {Object.entries(MODE_LABEL).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</Select>
              <span className="block text-[11px] text-gray-500 mt-0.5">Applies only to the lines that match the filters below.</span></div>
            {(bulk.mode === "scale" || bulk.mode === "set_pct") && <div><label className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">{bulk.mode === "scale" ? "Factor" : "Proposed %"}<Req /></label>
              <Input type="number" step={bulk.mode === "scale" ? "0.05" : "0.25"} min={0} max={bulk.mode === "scale" ? 5 : 100} value={bulk.value ?? ""} onChange={(e) => setBulk({ ...bulk, value: e.target.value })} />
              <span className="block text-[11px] text-gray-500 mt-0.5">{bulk.mode === "scale" ? "0.9 lowers every proposed % by a tenth; 1.1 raises it by a tenth (0 to 5)." : "0 to 100. Every matching line gets exactly this %."}</span>
              <FieldError msg={bErr.value} /></div>}
          </FormGrid>
        </FormSection>
        <FormSection title="Who it applies to" hint="Leave a filter on All to include everyone.">
          <FormGrid cols={3}>
            <div><label className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">Only department</label><Select value={bulk.department || ""} onChange={(e) => setBulk({ ...bulk, department: e.target.value || undefined })}><option value="">All</option>{by_department.map((d: Row) => <option key={d.key}>{d.key}</option>)}</Select></div>
            <div><label className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">Only rating</label><Select value={bulk.rating || ""} onChange={(e) => setBulk({ ...bulk, rating: e.target.value || undefined })}><option value="">All</option>{by_rating.map((d: Row) => <option key={d.key}>{d.key}</option>)}</Select></div>
            <div><label className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">Only grade</label><Select value={bulk.grade || ""} onChange={(e) => setBulk({ ...bulk, grade: e.target.value || undefined })}><option value="">All</option>{grades.map((g) => <option key={g}>{g}</option>)}</Select></div>
          </FormGrid>
        </FormSection>
        <FormSection title="Audit">
          <label className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">Reason{bulk.mode !== "include" && <Req />}</label>
          <Textarea rows={2} maxLength={500} value={bulk.reason || ""} onChange={(e) => setBulk({ ...bulk, reason: e.target.value })} placeholder="e.g. Calibration committee 14 Oct: bring Tech within budget" />
          <span className="block text-[11px] text-gray-500 mt-0.5">Saved in the cycle history with who applied it and the filters used (max 500 characters).</span>
          <FieldError msg={bErr.reason} />
        </FormSection>
      </Modal>}
    </div>
  );
}
