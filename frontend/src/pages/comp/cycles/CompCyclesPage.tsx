import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Coins, Plus, Settings } from "lucide-react";
import { PageHeader } from "../../../components/ui";
import { money } from "../../../api/endpoints";
import { useAuthStore } from "../../../store/authStore";
import { err, fmtDate } from "../../hiring/api";
import { Col, FirstStep, Grid, Note, Pill } from "../../hiring/ui";
import { ccApi, pct, Row, STATUS_LABEL } from "./api";
import CycleWizard from "./CycleWizard";

/* Cycles list: one dense row per cycle with budget vs spent, people, flags, stage. */
export default function CompCyclesPage() {
  const nav = useNavigate();
  const role = useAuthStore((s) => s.user?.role || "");
  const canCreate = ["superadmin", "org_admin", "hr_admin"].includes(role);
  const { data, isLoading, isError, error } = useQuery({ queryKey: ["comp", "cycles"], queryFn: ccApi.cycles });
  const [wizard, setWizard] = useState<Row | null | false>(false);
  const cols: Col<Row>[] = [
    { k: "name", label: "Cycle", render: (c) => <span className="font-medium text-gray-800">{c.name}</span> },
    { k: "period_start", label: "Period", render: (c) => `${fmtDate(c.period_start)} to ${fmtDate(c.period_end)}`, csv: (c) => `${c.period_start} to ${c.period_end}` },
    { k: "effective_from", label: "Effective", render: (c) => fmtDate(c.effective_from) },
    { k: "status", label: "Stage", render: (c) => <Pill s={c.status} label={STATUS_LABEL[c.status]} />, csv: (c) => STATUS_LABEL[c.status] },
    { k: "approval_state", label: "Approval", render: (c) => c.approval_state ? <Pill s={c.approval_state} /> : "—" },
    { k: "people", label: "People", align: "right", render: (c) => c.summary?.people ?? 0, csv: (c) => c.summary?.people ?? 0, sortVal: (c) => c.summary?.people ?? 0 },
    { k: "base", label: "Base $/yr", align: "right", render: (c) => money(c.summary?.base || 0), csv: (c) => c.summary?.base, sortVal: (c) => c.summary?.base },
    { k: "budget", label: "Budget", align: "right", render: (c) => c.summary?.budget == null ? "—" : money(c.summary.budget), csv: (c) => c.summary?.budget, sortVal: (c) => c.summary?.budget },
    { k: "spent", label: "Spent", align: "right", render: (c) => <span className={c.summary?.over_budget ? "text-red-600 font-semibold" : ""}>{money(c.summary?.spent || 0)}</span>, csv: (c) => c.summary?.spent, sortVal: (c) => c.summary?.spent },
    { k: "avg", label: "Avg %", align: "right", render: (c) => pct(c.summary?.weighted_pct), csv: (c) => c.summary?.weighted_pct, sortVal: (c) => c.summary?.weighted_pct },
    { k: "promotions", label: "Promotions", align: "right", render: (c) => c.summary?.promotions ?? 0, csv: (c) => c.summary?.promotions ?? 0 },
    { k: "flagged", label: "Flags", align: "right", render: (c) => <span className={c.summary?.flagged ? "text-amber-700 font-semibold" : ""}>{c.summary?.flagged ?? 0}</span>, csv: (c) => c.summary?.flagged ?? 0 },
    { k: "budget_mode", label: "Limit", render: (c) => c.budget_mode === "hard" ? "Hard" : "Soft" },
    { k: "created_by", label: "Created by" },
  ];
  return (
    <div>
      <PageHeader title="Compensation cycles" icon={Coins} subtitle="Plan increases by rating and compa-ratio, within budgets, with manager worksheets, HR calibration, an approval chain and one-click apply through salary revisions."
        actions={<div className="flex gap-2"><Link to="/comp/settings" className="btn-secondary !py-1.5 text-xs inline-flex items-center gap-1"><Settings className="w-3.5 h-3.5" />Settings</Link>
          {canCreate && <button className="btn-primary !py-1.5 text-xs inline-flex items-center gap-1" onClick={() => setWizard(null)}><Plus className="w-3.5 h-3.5" />New cycle</button>}</div>} />
      {isError && <Note tone="warn">{err(error, "Cycles could not be loaded.")}</Note>}
      {!isLoading && !isError && !(data || []).length ? (
        <FirstStep title="No compensation cycle yet">
          First set the merit matrix, grade midpoints and budget rules in Settings. Then create a cycle, check who is eligible, build the population and open it to managers.
          {canCreate && <div className="mt-4"><button className="btn-primary inline-flex items-center gap-1.5" onClick={() => setWizard(null)}><Plus className="w-4 h-4" />Create a cycle</button></div>}
        </FirstStep>
      ) : <Grid rows={data || []} cols={cols} keyOf={(c) => c.id} loading={isLoading} exportName="comp-cycles.csv" title="Cycles" onRow={(c) => nav(`/comp/cycles/${c.id}/setup`)} />}
      {wizard !== false && <CycleWizard cycle={wizard} onClose={() => setWizard(false)} />}
    </div>
  );
}
