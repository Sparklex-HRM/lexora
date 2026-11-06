import { useQuery } from "@tanstack/react-query";
import { money } from "../../../api/endpoints";
import { err } from "../../hiring/api";
import { Col, Grid, Note, Stat } from "../../hiring/ui";
import { ccApi, cr, pct, Row } from "./api";
import { BudgetBar } from "./Worksheet";
import { Bars } from "./Calibration";

/* Cycle analytics: budget vs spent, averages by department / rating / gender / grade,
   compa-ratio before and after, pay-equity table (descriptive only). */
export default function Analytics({ cycleId }: { cycleId: string }) {
  const q = useQuery({ queryKey: ["comp", "analytics", cycleId], queryFn: () => ccApi.analytics(cycleId) });
  if (q.isError) return <Note tone="warn">{err(q.error, "Analytics could not be loaded.")}</Note>;
  if (!q.data) return <div className="text-sm text-gray-400">Loading…</div>;
  const { summary: s, budget_usage: bu, by_department, by_rating, by_gender, by_grade, compa, pay_equity, note } = q.data;
  const gcols: Col<Row>[] = [
    { k: "key", label: "Group" }, { k: "people", label: "People", align: "right" }, { k: "base", label: "Base $/yr", align: "right", render: (r) => money(r.base) },
    { k: "increase", label: "Increase $", align: "right", render: (r) => money(r.increase) }, { k: "lump_sum", label: "Lump sums", align: "right", render: (r) => money(r.lump_sum) },
    { k: "weighted_pct", label: "Weighted %", align: "right", render: (r) => pct(r.weighted_pct) }, { k: "avg_pct", label: "Avg %", align: "right", render: (r) => pct(r.avg_pct) },
    { k: "promotions", label: "Promotions", align: "right" }, { k: "avg_cr_before", label: "Compa before", align: "right", render: (r) => cr(r.avg_cr_before) },
    { k: "avg_cr_after", label: "Compa after", align: "right", render: (r) => cr(r.avg_cr_after) },
  ];
  const peRows = pay_equity.flatMap((g: Row) => g.groups.map((x: Row) => ({ ...x, grade: g.grade, gap_before_pct: g.gap_before_pct, gap_after_pct: g.gap_after_pct, flag: g.flag, note: g.note, id: `${g.grade}:${x.gender}` })));
  const pcols: Col<Row>[] = [
    { k: "grade", label: "Grade" }, { k: "gender", label: "Gender" }, { k: "people", label: "People", align: "right" },
    { k: "avg_before", label: "Avg pay before", align: "right", render: (r) => money(r.avg_before) }, { k: "avg_after", label: "Avg pay after", align: "right", render: (r) => money(r.avg_after) },
    { k: "avg_pct", label: "Avg %", align: "right", render: (r) => pct(r.avg_pct) },
    { k: "gap_before_pct", label: "F vs M before", align: "right", render: (r) => r.gap_before_pct == null ? "—" : pct(r.gap_before_pct) },
    { k: "gap_after_pct", label: "F vs M after", align: "right", render: (r) => r.gap_after_pct == null ? "—" : <span className={r.flag ? "text-amber-700 font-semibold" : ""}>{pct(r.gap_after_pct)}</span> },
    { k: "flag", label: "Look at", render: (r) => r.flag ? <span className="text-amber-700">gap of 5% or more</span> : r.note ? <span className="text-gray-400">{r.note}</span> : "", csv: (r) => r.flag ? "yes" : "" },
  ];
  return (
    <div>
      <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-7 gap-3 mb-3">
        <Stat label="People" value={s.people} /><Stat label="Base" value={money(s.base)} /><Stat label="Spent" value={money(s.spent)} accent />
        <Stat label="Budget" value={s.budget == null ? "—" : money(s.budget)} sub={s.remaining == null ? undefined : s.over_budget ? `over by ${money(-s.remaining)}` : `${money(s.remaining)} left`} />
        <Stat label="Weighted %" value={pct(s.weighted_pct)} /><Stat label="With increase" value={s.with_increase} /><Stat label="Promotions" value={s.promotions} />
      </div>
      <div className="flex flex-wrap gap-2 mb-4">{Object.values(bu || {}).filter((u: any) => u.allowed != null).map((u: any) => <BudgetBar key={`${u.scope}:${u.key}`} u={u} />)}</div>
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4 mb-4">
        <div className="card"><h3 className="font-display font-semibold text-gray-800 mb-2">Weighted % by department</h3><Bars rows={by_department} label={(r) => r.key} value={(r) => r.weighted_pct || 0} fmt={pct} /></div>
        <div className="card"><h3 className="font-display font-semibold text-gray-800 mb-2">Average % by rating</h3><Bars rows={by_rating} label={(r) => `${r.key} (${r.people})`} value={(r) => r.avg_pct || 0} fmt={pct} color="bg-amber-500" /></div>
        <div className="card"><h3 className="font-display font-semibold text-gray-800 mb-2">Average % by gender</h3><Bars rows={by_gender} label={(r) => `${r.key} (${r.people})`} value={(r) => r.avg_pct || 0} fmt={pct} color="bg-teal-600" /></div>
      </div>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4 mb-4">
        <div className="card"><h3 className="font-display font-semibold text-gray-800 mb-2">Compa-ratio distribution: before → after</h3>
          <table className="w-full text-xs"><thead className="text-gray-400 text-[10px] uppercase"><tr><th className="text-left py-1">Band</th><th className="text-right">Before</th><th className="text-right">After</th><th className="text-right">Change</th></tr></thead>
            <tbody>{compa.map((r: Row) => <tr key={r.key} className="border-t border-surface-border"><td className="py-1">{r.label}</td><td className="text-right tabular-nums">{r.before}</td><td className="text-right tabular-nums font-semibold">{r.after}</td><td className={`text-right tabular-nums ${r.after - r.before > 0 ? "text-green-700" : r.after - r.before < 0 ? "text-red-600" : "text-gray-400"}`}>{r.after - r.before > 0 ? "+" : ""}{r.after - r.before}</td></tr>)}</tbody></table></div>
        <div className="card"><h3 className="font-display font-semibold text-gray-800 mb-2">By grade</h3><Bars rows={by_grade} label={(r) => `${r.key} (${r.people})`} value={(r) => r.weighted_pct || 0} fmt={pct} /></div>
      </div>
      <Grid rows={[...by_department.map((r: Row) => ({ ...r, id: `d:${r.key}` }))]} cols={gcols} keyOf={(r) => r.id} title="By department" exportName="comp-by-department.csv" />
      <div className="mt-4"><Grid rows={by_rating.map((r: Row) => ({ ...r, id: `r:${r.key}` }))} cols={gcols} keyOf={(r) => r.id} title="By rating" exportName="comp-by-rating.csv" /></div>
      <div className="mt-4">
        <Note tone="warn">{note}</Note>
        <Grid rows={peRows} cols={pcols} keyOf={(r) => r.id} title="Pay by gender within grade (descriptive)" exportName="comp-pay-equity.csv" empty="No grades to compare." />
      </div>
    </div>
  );
}
