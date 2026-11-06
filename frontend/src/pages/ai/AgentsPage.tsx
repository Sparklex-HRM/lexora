import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, RefreshCw } from "lucide-react";
import { PageHeader, Spinner, StatTile } from "../../components/ui";
import { Tabs } from "../../components/DataGrid";
import { errText, when } from "../../api/platform";
import { agenticApi, cad } from "../../api/agentic";
import { useUrlTab } from "../../search/useUrlTab";
import { useAuthStore } from "../../store/authStore";
import { ModeBadge } from "./agents/shared";
import OverviewTab from "./agents/OverviewTab";
import ApprovalsTab from "./agents/ApprovalsTab";
import TasksTab from "./agents/TasksTab";
import RunsTab from "./agents/RunsTab";
import ValueTab from "./agents/ValueTab";
import SettingsTab from "./agents/SettingsTab";

type Tab = "overview" | "approvals" | "tasks" | "runs" | "value" | "settings";

/** Agents — the Agentic operations add-on (2026-10-25): Payroll Auditor, HR Desk + the onboarding, exit and attendance agents.
 *  Role-gated by App.tsx (org admin, HR admin, payroll admin); the server enforces the same. */
export default function AgentsPage() {
  const qc = useQueryClient();
  const [tab, setTab] = useUrlTab<Tab>("overview");
  const [agent, setAgent] = useState("");
  const me = useAuthStore((s) => s.user?.id) || "";
  const o = useQuery({ queryKey: ["agentic", "overview"], queryFn: agenticApi.overview, refetchInterval: 60000 });
  if (o.isLoading) return <div className="grid place-items-center py-24"><Spinner className="h-8 w-8" /></div>;
  if (o.isError || !o.data) return <div className="card text-sm text-red-600">Could not load the agents. {errText(o.error)}</div>;
  const d = o.data;
  const st = d.state;
  const go = (t: string, a?: string) => { if (a !== undefined) setAgent(a); setTab(t as Tab); };
  const month = d.agents.reduce((acc, a) => ({ r: acc.r + a.value_this_month.avoided_dollars, m: acc.m + a.value_this_month.proxy_minutes }), { r: 0, m: 0 });
  return (
    <div>
      <PageHeader title="Agents" icon={Bot}
        subtitle={`Payroll Auditor · HR Desk · Onboarding · Exit · Attendance — they prepare, chase and explain; you approve. ${st.enabled ? (st.mode === "live" ? "Live mode" : "Shadow mode") : "Add-on off"} · as of ${when(d.as_of)}.`}
        actions={<div className="flex items-center gap-2"><ModeBadge mode={st.mode} enabled={st.enabled} />
          <button className="btn-secondary !py-1.5 text-xs inline-flex items-center gap-1.5" onClick={() => qc.invalidateQueries({ queryKey: ["agentic"] })}><RefreshCw className="w-3.5 h-3.5" /> Refresh</button></div>} />
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3 mb-4">
        <StatTile label="Waiting for approval" value={d.inbox_total} accent={d.inbox_total > 0} />
        <StatTile label="Open tasks (people chased)" value={d.open_tasks_total} />
        <StatTile label="Mode" value={st.enabled ? (st.mode === "live" ? "Live" : "Shadow") : "Off"} sub={st.enabled ? `${st.shadow_cycles_done}/${st.live_after_shadow_cycles} shadow cycles` : st.addon_purchased ? "paused" : "add-on not bought"} />
        <StatTile label="Confirmed $ this month" value={cad(month.r)} />
        <StatTile label="Est. time this month" value={month.m >= 60 ? `${(month.m / 60).toFixed(1)} h` : `${Math.round(month.m)} min`} sub="proxy layer, editable minutes" />
      </div>
      <Tabs value={tab} onChange={setTab} tabs={[{ key: "overview", label: "Overview" }, { key: "approvals", label: "Approvals inbox", count: d.inbox_total }, { key: "tasks", label: "Tasks", count: d.open_tasks_total },
        { key: "runs", label: "Shadow reports & runs" }, { key: "value", label: "Value" }, { key: "settings", label: "Settings" }]} />
      {tab === "overview" && <OverviewTab o={d} onTab={go} />}
      {tab === "approvals" && <ApprovalsTab agents={d.agents} agent={agent} setAgent={setAgent} canAct={st.enabled} me={me} />}
      {tab === "tasks" && <TasksTab agents={d.agents} agent={agent} setAgent={setAgent} canAct={st.enabled} />}
      {tab === "runs" && <RunsTab agents={d.agents} agent={agent} setAgent={setAgent} />}
      {tab === "value" && <ValueTab canEdit={d.can_edit} />}
      {tab === "settings" && <SettingsTab />}
    </div>
  );
}
