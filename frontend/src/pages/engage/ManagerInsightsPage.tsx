import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Users } from "lucide-react";
import DataGrid, { type GridColumn } from "../../components/DataGrid";
import { PageHeader, Spinner, StatTile } from "../../components/ui";
import SimpleChart from "../../components/SimpleChart";
import { employeeApi } from "../../api/endpoints";
import { engage2Api } from "../../api/analytics";
import { learningApi } from "../../api/learning";
import { errText } from "../../api/platform";
import { useAuthStore } from "../../store/authStore";

const HR = ["superadmin", "org_admin", "hr_admin", "hr_manager"];

/** /engage/insights — manager insights: team eNPS / pulse per survey (threshold), participation, recognition given / received,
 *  this month's points budget, team learning completion. HR picks any manager; a manager sees their own team. */
export default function ManagerInsightsPage() {
  const role = useAuthStore((s) => s.user?.role) || "";
  const hr = HR.includes(role);
  const [mgr, setMgr] = useState("");
  const emps = useQuery({ queryKey: ["employees"], queryFn: () => employeeApi.list(), enabled: hr });
  const managers = useMemo(() => { const list = (emps.data || []) as any[]; const ids = new Set(list.map((e) => e.manager_id).filter(Boolean)); return list.filter((e) => ids.has(e.id)).sort((a, b) => `${a.first_name}`.localeCompare(b.first_name)); }, [emps.data]);
  const q = useQuery({ queryKey: ["mgr-insights", mgr], queryFn: () => engage2Api.managerInsights(mgr || undefined), enabled: !hr || !!mgr });
  const team = useQuery({ queryKey: ["lms-team", mgr], queryFn: () => learningApi.team(mgr || undefined), enabled: !hr || !!mgr });
  const d = q.data;
  const chart = (d?.surveys || []).filter((s) => !s.hidden && s.score != null).map((s) => ({ date: s.date, team: s.score, company: s.company_score }));
  const cols: GridColumn<any>[] = [{ key: "date", label: "Date", type: "date", width: "105px" }, { key: "title", label: "Survey", className: "text-gray-800" }, { key: "kind", label: "Kind", width: "70px" },
    { key: "team_responses", label: "Team answers", type: "number" }, { key: "participation_pct", label: "Participation %", type: "percent" },
    { key: "score", label: "Team score", type: "number", render: (r) => r.hidden ? <span className="text-[11px] text-gray-400">hidden (&lt; {d?.threshold} answers)</span> : r.score ?? "—" },
    { key: "company_score", label: "Company", type: "number" }];
  return (
    <div>
      <PageHeader title="Manager insights" icon={Users} subtitle={d ? `${d.manager_name || "Team"} · ${d.team_size} direct reports · last ${d.window_days} days of recognition` : "Team engagement, recognition and learning"}
        actions={hr && <select className="input !py-1.5 text-sm w-64" value={mgr} onChange={(e) => setMgr(e.target.value)} aria-label="Manager"><option value="">Pick a manager…</option>{managers.map((m) => <option key={m.id} value={m.id}>{m.first_name} {m.last_name}</option>)}</select>} />
      {q.isLoading && <div className="grid place-items-center py-16"><Spinner /></div>}
      {q.isError && <div className="card text-sm text-red-600">{errText(q.error)}</div>}
      {!d && !q.isLoading && hr && <div className="card text-sm text-gray-500">Pick a manager to see their team.</div>}
      {d && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
            <StatTile label="Team" value={d.team_size} accent />
            <StatTile label="Latest team score" value={chart.length ? chart[chart.length - 1].team : "—"} sub={chart.length ? `company ${chart[chart.length - 1].company ?? "—"}` : "needs ≥ threshold answers"} />
            <StatTile label="Kudos given" value={d.recognition.given} sub={`${d.recognition.given_points} points`} />
            <StatTile label="Received by team" value={d.recognition.received_by_team} sub={`${d.recognition.team_members_not_recognised} not recognised yet`} />
            <StatTile label={`Points left · ${d.budget.month}`} value={d.budget.remaining} sub={`${d.budget.points_used} of ${d.budget.points_budget} used`} />
            <StatTile label="Learning completion" value={team.data?.completion_pct != null ? `${team.data.completion_pct}%` : "—"} sub={team.data ? `${team.data.overdue} overdue` : ""} />
          </div>
          {chart.length > 1 && <div className="card"><div className="text-sm font-semibold text-gray-800 mb-1">Team vs company</div><SimpleChart spec={{ type: "line", x: "date", series: [{ key: "team", label: "Team" }, { key: "company", label: "Company" }] }} data={chart} height={220} /></div>}
          <DataGrid columns={cols} rows={d.surveys} rowKey={(r) => r.survey_id} minWidth={800} exportName="team-surveys" density="compact" empty="No pulse / eNPS surveys yet." />
          <div className="grid md:grid-cols-2 gap-3">
            <div className="card !py-3"><div className="text-sm font-semibold text-gray-800 mb-2">Team</div><div className="flex flex-wrap gap-1.5">{d.team.map((t) => <Link key={t.id} to={`/employees/${t.id}`} className="px-2 py-1 rounded-lg text-xs border border-surface-border hover:bg-surface">{t.name || t.id}</Link>)}</div></div>
            <div className="card !py-3 text-sm text-gray-600">Anonymous answers are grouped by the manager segment stamped at answer time and shown only when at least {d.threshold} team members answered. Flight-risk signals for this team: <Link className="text-primary-700 underline" to="/ai/flight-risk">open</Link>. Team learning detail: <Link className="text-primary-700 underline" to="/learning/admin">open</Link>.</div>
          </div>
        </div>)}
    </div>
  );
}
