import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ClipboardList } from "lucide-react";
import { PageHeader } from "../../../components/ui";
import { money } from "../../../api/endpoints";
import { err, fmtDate } from "../../hiring/api";
import { Col, FirstStep, Grid, Note, Pill } from "../../hiring/ui";
import { ccApi, pct, Row, STATUS_LABEL } from "./api";
import Worksheet from "./Worksheet";

/* Managers: the cycles where they have reports to plan for, then the worksheet itself. */
export default function MyWorksheetsPage() {
  const nav = useNavigate();
  const { data, isLoading, isError, error } = useQuery({ queryKey: ["comp", "my-worksheets"], queryFn: ccApi.myWorksheets });
  const cols: Col<Row>[] = [
    { k: "name", label: "Cycle", render: (c) => <span className="font-medium text-gray-800">{c.name}</span> },
    { k: "effective_from", label: "Effective", render: (c) => fmtDate(c.effective_from) },
    { k: "status", label: "Stage", render: (c) => <Pill s={c.status} label={STATUS_LABEL[c.status]} />, csv: (c) => STATUS_LABEL[c.status] },
    { k: "can_edit", label: "Editable", render: (c) => c.can_edit ? <span className="text-green-700 font-semibold">Yes</span> : "No", csv: (c) => c.can_edit ? "yes" : "no" },
    { k: "people", label: "Your people", align: "right" },
    { k: "base", label: "Base $/yr", align: "right", render: (c) => money(c.base) },
    { k: "used", label: "Proposed", align: "right", render: (c) => money(c.used), sortVal: (c) => c.used },
    { k: "pct", label: "%", align: "right", render: (c) => pct(c.base ? (c.used * 100) / c.base : 0) },
    { k: "flagged", label: "Flags", align: "right", render: (c) => <span className={c.flagged ? "text-amber-700 font-semibold" : ""}>{c.flagged}</span> },
  ];
  return (
    <div>
      <PageHeader title="Compensation worksheets" icon={ClipboardList} subtitle="Your reports in each compensation cycle: current pay, rating, compa-ratio and the matrix recommendation. Propose a % or an amount, a lump sum or a promotion, and watch the budget as you go." />
      {isError && <Note tone="warn">{err(error, "Worksheets could not be loaded.")}</Note>}
      {!isLoading && !isError && !(data || []).length ? <FirstStep title="No worksheet for you right now">When HR opens a compensation cycle that includes your reports, it appears here.</FirstStep>
        : <Grid rows={data || []} cols={cols} keyOf={(c) => c.id} loading={isLoading} title="Cycles with your reports" onRow={(c) => nav(`/comp/worksheets/${c.id}`)} />}
    </div>
  );
}

export function MyWorksheetPage() {
  const { id = "" } = useParams();
  const q = useQuery({ queryKey: ["comp", "cycle", id], queryFn: () => ccApi.cycle(id) });
  return (
    <div>
      <PageHeader title={q.data?.name || "Worksheet"} icon={ClipboardList} subtitle={q.data ? `${STATUS_LABEL[q.data.status]} · increases effective ${fmtDate(q.data.effective_from)}` : ""}
        actions={<Link to="/comp/worksheets" className="btn-secondary !py-1.5 text-xs inline-flex items-center gap-1"><ArrowLeft className="w-3.5 h-3.5" />All worksheets</Link>} />
      <Worksheet cycleId={id} />
    </div>
  );
}
