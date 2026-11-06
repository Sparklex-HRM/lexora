import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { ShieldCheck, Play, RefreshCw, AlertTriangle, CheckCircle2, Info, Bot } from "lucide-react";
import { PageHeader, Spinner, StatTile } from "../../components/ui";
import DataGrid, { Tabs, WideModal, type GridColumn } from "../../components/DataGrid";
import { errText, when } from "../../api/platform";
import { watchdogApi, type WdAgent, type WdFinding, type WdRun, type WdSettings } from "../../api/ai";
import { useUrlTab } from "../../search/useUrlTab";

type Tab = "health" | "findings" | "runs" | "settings";

const SEV: Record<string, string> = { high: "bg-red-100 text-red-700", warn: "bg-amber-100 text-amber-700", info: "bg-sky-100 text-sky-700" };
const STATUS: Record<string, string> = { ok: "bg-green-100 text-green-700", attention: "bg-amber-100 text-amber-700", skipped: "bg-gray-100 text-gray-600", error: "bg-red-100 text-red-700", never_run: "bg-gray-100 text-gray-500" };
const Pill = ({ v, map }: { v: string | null | undefined; map: Record<string, string> }) =>
  v ? <span className={`inline-block text-[11px] font-semibold px-2 py-0.5 rounded-full ${map[v] || "bg-gray-100 text-gray-600"}`}>{v.replace("_", " ")}</span> : <span className="text-gray-300">—</span>;
const cadence = (m: number) => m >= 1440 ? `every ${m / 1440} day${m / 1440 === 1 ? "" : "s"}` : `every ${m / 60} h`;

export default function ComplianceHealthPage() {
  const [sp, setSp] = useSearchParams();
  const [tab, setTab] = useUrlTab<Tab>(sp.get("agent") ? "findings" : "health");   // ?tab= (global search deep link)
  const [agent, setAgent] = useState(sp.get("agent") || "");
  const [severity, setSeverity] = useState("");
  const [open, setOpen] = useState<WdFinding | null>(null);
  const qc = useQueryClient();
  const health = useQuery({ queryKey: ["wd", "health"], queryFn: watchdogApi.health });
  const fleet = useQuery({ queryKey: ["wd", "fleet"], queryFn: watchdogApi.fleet });
  const findings = useQuery({ queryKey: ["wd", "findings", agent, severity], queryFn: () => watchdogApi.findings({ agent, severity }) });
  const runs = useQuery({ queryKey: ["wd", "runs", agent], queryFn: () => watchdogApi.runs(agent), enabled: tab === "runs" });
  const refresh = () => qc.invalidateQueries({ queryKey: ["wd"] });
  const runOne = useMutation({ mutationFn: (k: string) => watchdogApi.run(k),
    onSuccess: (r) => { toast.success(`${r.run.agent_key}: ${r.run.summary}`); refresh(); }, onError: (e) => toast.error(errText(e)) });
  const runAll = useMutation({ mutationFn: watchdogApi.runAll,
    onSuccess: (r) => { toast.success(`${r.runs.length} watchdog(s) ran`); refresh(); }, onError: (e) => toast.error(errText(e)) });
  useEffect(() => { if (agent) setSp({ agent }); else setSp({}); }, [agent]);   // eslint-disable-line react-hooks/exhaustive-deps

  if (health.isLoading || fleet.isLoading) return <div className="grid place-items-center py-24"><Spinner className="h-8 w-8" /></div>;
  if (health.isError || !health.data || !fleet.data) return <div className="card text-sm text-red-600">Could not load compliance health. {errText(health.error)}</div>;
  const h = health.data;
  const agents = fleet.data.agents;

  return (
    <div>
      <PageHeader title="Compliance health" icon={ShieldCheck}
        subtitle={`${agents.length} read-only watchdogs · ${h.country} · as of ${when(h.as_of)}`}
        actions={<div className="flex gap-2">
          <button className="btn-secondary !py-1.5 text-xs inline-flex items-center gap-1.5" onClick={() => refresh()}><RefreshCw className="w-3.5 h-3.5" /> Refresh</button>
          <button className="btn-primary !py-1.5 text-xs inline-flex items-center gap-1.5" disabled={runAll.isPending} onClick={() => runAll.mutate()}><Play className="w-3.5 h-3.5" /> Run all now</button>
        </div>} />

      <div className={`card mb-4 flex items-center gap-4 ${h.totals.high ? "border-red-200" : h.totals.warn ? "border-amber-200" : "border-green-200"}`}>
        <div className={`w-16 h-16 rounded-2xl flex flex-col items-center justify-center text-white shrink-0 ${h.totals.high ? "bg-red-500" : h.totals.warn ? "bg-amber-500" : "bg-emerald-500"}`}>
          <span className="text-2xl font-bold leading-none">{h.score}</span><span className="text-[9px] uppercase mt-0.5">score</span>
        </div>
        <div className="min-w-0">
          <div className="flex items-center gap-2 font-display font-bold text-gray-800">
            {h.totals.high || h.totals.warn ? <AlertTriangle className="w-4 h-4 text-amber-600" /> : <CheckCircle2 className="w-4 h-4 text-emerald-600" />}{h.headline}
          </div>
          <p className="text-xs text-gray-500 mt-1">{h.note}</p>
          {h.never_run > 0 && <p className="text-xs text-gray-500">{h.never_run} watchdog(s) have not run yet — press <b>Run all now</b> or wait for the schedule.</p>}
        </div>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3 mb-4">
        <StatTile label="High" value={h.totals.high} icon={AlertTriangle} />
        <StatTile label="Warn" value={h.totals.warn} icon={AlertTriangle} />
        <StatTile label="Info" value={h.totals.info} icon={Info} />
        <StatTile label="Agents with findings" value={h.agents.filter((a) => a.status === "attention").length} icon={Bot} />
        <StatTile label="Errors / skipped" value={`${h.errors} / ${h.agents.filter((a) => a.status === "skipped").length}`} icon={Info} />
      </div>

      <Tabs value={tab} onChange={setTab} tabs={[{ key: "health", label: "Agents" }, { key: "findings", label: "Findings", count: findings.data?.total },
        { key: "runs", label: "Run log" }, { key: "settings", label: "Settings" }]} />

      {tab === "health" && <AgentsGrid agents={agents} health={h} onRun={(k) => runOne.mutate(k)} busy={runOne.isPending} onFindings={(k) => { setAgent(k); setTab("findings"); }} />}
      {tab === "findings" && (
        <DataGrid<WdFinding> rows={findings.data?.items || []} rowKey={(r) => r.id} exportName="watchdog-findings" minWidth={900}
          empty={findings.isLoading ? "Loading…" : "No findings from the latest runs."} initialSort={{ key: "severity", dir: "asc" }} onRowClick={setOpen}
          filters={<>
            <select className="input !py-1.5 text-sm w-48" value={agent} onChange={(e) => setAgent(e.target.value)} aria-label="Agent">
              <option value="">All agents</option>{agents.map((a) => <option key={a.key} value={a.key}>{a.name}</option>)}
            </select>
            <select className="input !py-1.5 text-sm w-32" value={severity} onChange={(e) => setSeverity(e.target.value)} aria-label="Severity">
              <option value="">All severities</option><option value="high">High</option><option value="warn">Warn</option><option value="info">Info</option>
            </select>
          </>}
          columns={[
            { key: "severity", label: "Severity", render: (r) => <Pill v={r.severity} map={SEV} />, text: (r) => r.severity, width: "90px" },
            { key: "agent", label: "Watchdog" }, { key: "subject_name", label: "Person" }, { key: "title", label: "Finding", className: "text-gray-800" },
            { key: "confidence", label: "Confidence", width: "90px" },
            { key: "evidence", label: "Evidence rows", type: "number", render: (r) => r.evidence.length, text: (r) => String(r.evidence.length) },
            { key: "is_new", label: "New", render: (r) => r.is_new ? "new" : "", text: (r) => (r.is_new ? "new" : "") },
            { key: "created_at", label: "Found", type: "date" },
          ]} />
      )}
      {tab === "runs" && (
        <DataGrid<WdRun> rows={runs.data?.items || []} rowKey={(r) => r.id} exportName="watchdog-runs" minWidth={900} empty={runs.isLoading ? "Loading…" : "No runs yet."}
          filters={<select className="input !py-1.5 text-sm w-48" value={agent} onChange={(e) => setAgent(e.target.value)} aria-label="Agent">
            <option value="">All agents</option>{agents.map((a) => <option key={a.key} value={a.key}>{a.name}</option>)}</select>}
          columns={[
            { key: "started_at", label: "Ran", render: (r) => when(r.started_at), text: (r) => r.started_at || "" },
            { key: "agent", label: "Watchdog" }, { key: "trigger", label: "Trigger" },
            { key: "status", label: "Status", render: (r) => <Pill v={r.status} map={STATUS} />, text: (r) => r.status },
            { key: "rows_read", label: "Rows read", type: "number" }, { key: "findings_count", label: "Findings", type: "number" }, { key: "new_findings", label: "New", type: "number" },
            { key: "severity_max", label: "Worst", render: (r) => <Pill v={r.severity_max} map={SEV} />, text: (r) => r.severity_max || "" },
            { key: "notified", label: "Notified", render: (r) => (r.notified ? "yes" : ""), text: (r) => (r.notified ? "yes" : "") },
            { key: "duration_ms", label: "ms", type: "number" }, { key: "summary", label: "Summary", render: (r) => r.error || r.summary, text: (r) => r.error || r.summary },
          ]} />
      )}
      {tab === "settings" && <SettingsPanel agents={agents} />}

      {open && (
        <WideModal title={`${open.agent}: ${open.title}`} onClose={() => setOpen(null)}>
          <div className="flex flex-wrap items-center gap-2 mb-3 text-xs">
            <Pill v={open.severity} map={SEV} /><span className="text-gray-500">confidence {open.confidence}</span>
            {open.subject_name && <span className="text-gray-700 font-medium">· {open.subject_name}</span>}
            <span className="text-gray-400">· found {when(open.created_at)}</span>
          </div>
          <p className="text-sm text-gray-700 whitespace-pre-wrap mb-4">{open.explanation}</p>
          <h4 className="text-xs uppercase tracking-wide text-gray-500 font-semibold mb-2">Evidence — the rows this was computed from</h4>
          <EvidenceTable rows={open.evidence} />
        </WideModal>
      )}
    </div>
  );
}

function AgentsGrid({ agents, health, onRun, busy, onFindings }: { agents: WdAgent[]; health: any; onRun: (k: string) => void; busy: boolean; onFindings: (k: string) => void }) {
  const byKey: Record<string, any> = Object.fromEntries(health.agents.map((a: any) => [a.key, a]));
  const cols: GridColumn<WdAgent>[] = [
    { key: "name", label: "Watchdog", className: "text-gray-800 font-medium", render: (a) => <div><div>{a.name}</div><div className="text-[11px] text-gray-400 font-normal max-w-md whitespace-normal">{a.checks}</div></div>, text: (a) => a.name },
    { key: "domain", label: "Area", width: "90px" },
    { key: "cadence_minutes", label: "Cadence", render: (a) => cadence(a.cadence_minutes), text: (a) => String(a.cadence_minutes) },
    { key: "enabled", label: "On", render: (a) => (a.enabled ? "on" : "off"), text: (a) => (a.enabled ? "on" : "off"), width: "50px" },
    { key: "status", label: "Last status", render: (a) => <Pill v={byKey[a.key]?.status} map={STATUS} />, text: (a) => byKey[a.key]?.status || "" },
    { key: "last", label: "Last run", render: (a) => (a.last_run ? when(a.last_run.started_at) : "never"), text: (a) => a.last_run?.started_at || "" },
    { key: "rows", label: "Rows read", type: "number", render: (a) => a.last_run?.rows_read ?? "—", text: (a) => String(a.last_run?.rows_read ?? "") },
    { key: "findings", label: "Findings", type: "number", render: (a) => {
        const f = byKey[a.key]?.findings || {}; const n = (f.high || 0) + (f.warn || 0) + (f.info || 0);
        return n ? <button className="text-primary-600 hover:underline" onClick={(e) => { e.stopPropagation(); onFindings(a.key); }}>{n} ({f.high || 0}h/{f.warn || 0}w/{f.info || 0}i)</button> : "0";
      }, text: (a) => String(a.last_run?.findings_count ?? "") },
    { key: "notify", label: "Tells", render: (a) => a.notify_roles.join(", "), text: (a) => a.notify_roles.join(", ") },
    { key: "run", label: "", sortable: false, render: (a) => <button className="btn-secondary !py-1 !px-2 text-xs" disabled={busy || !a.enabled} onClick={(e) => { e.stopPropagation(); onRun(a.key); }}>Run now</button>, text: () => "" },
  ];
  return <DataGrid<WdAgent> rows={agents} rowKey={(a) => a.key} columns={cols} minWidth={1000} pageSizeDefault={25} exportName="watchdogs" />;
}

function EvidenceTable({ rows }: { rows: Record<string, any>[] }) {
  if (!rows.length) return <p className="text-sm text-gray-500">No evidence rows were stored for this finding.</p>;
  const keys = Array.from(new Set(rows.flatMap((r) => Object.keys(r))));
  return (
    <div className="overflow-x-auto card !p-0">
      <table className="w-full text-[13px]">
        <thead className="bg-surface text-[11px] uppercase tracking-wide text-gray-500 text-left"><tr>{keys.map((k) => <th key={k} className="px-3 py-2 whitespace-nowrap">{k.replace(/_/g, " ")}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => <tr key={i} className="border-t border-surface-border">{keys.map((k) => <td key={k} className="px-3 py-1.5 text-gray-700 tabular-nums whitespace-nowrap">{fmt(r[k])}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}
const fmt = (v: any) => v === null || v === undefined || v === "" ? "—" : typeof v === "number" ? v.toLocaleString(undefined, { maximumFractionDigits: 2 }) : typeof v === "boolean" ? (v ? "yes" : "no") : String(v);

function SettingsPanel({ agents }: { agents: WdAgent[] }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["wd", "settings"], queryFn: watchdogApi.settings });
  const [v, setV] = useState<WdSettings["value"] | null>(null);
  useEffect(() => { if (q.data) setV(JSON.parse(JSON.stringify(q.data.value))); }, [q.data]);
  const save = useMutation({ mutationFn: (val: WdSettings["value"]) => watchdogApi.saveSettings(val),
    onSuccess: () => { toast.success("Watchdog settings saved"); qc.invalidateQueries({ queryKey: ["wd"] }); }, onError: (e) => toast.error(errText(e)) });
  const thFor = useMemo(() => (k: string) => ({ ...(q.data?.defaults[k] || {}), ...(v?.thresholds?.[k] || {}) }), [q.data, v]);
  if (q.isLoading || !v) return <Spinner />;
  const canEdit = q.data!.can_edit;
  const toggle = (k: string) => setV({ ...v, disabled_agents: v.disabled_agents.includes(k) ? v.disabled_agents.filter((x) => x !== k) : [...v.disabled_agents, k] });
  const setTh = (k: string, name: string, val: string) => setV({ ...v, thresholds: { ...v.thresholds, [k]: { ...(v.thresholds[k] || {}), [name]: val } } });
  return (
    <div className="space-y-4">
      <div className="card flex flex-wrap items-center gap-4 text-sm">
        <label className="inline-flex items-center gap-2"><input type="checkbox" disabled={!canEdit} checked={v.enabled} onChange={(e) => setV({ ...v, enabled: e.target.checked })} /> Watchdogs on for this company</label>
        <label className="inline-flex items-center gap-2"><input type="checkbox" disabled={!canEdit} checked={v.notify} onChange={(e) => setV({ ...v, notify: e.target.checked })} /> Notify the responsible roles about new findings</label>
        <span className="text-xs text-gray-400 ml-auto">{canEdit ? "Saved as company setting “AI watchdogs” (audited)." : "Only an org admin can change these."}</span>
        {canEdit && <button className="btn-primary !py-1.5 text-xs" disabled={save.isPending} onClick={() => save.mutate(v)}>Save</button>}
      </div>
      <div className="card !p-0 overflow-x-auto">
        <table className="w-full text-[13px]">
          <thead className="bg-surface text-[11px] uppercase tracking-wide text-gray-500 text-left"><tr><th className="px-3 py-2">On</th><th className="px-3 py-2">Watchdog</th><th className="px-3 py-2">Thresholds (blank = default)</th></tr></thead>
          <tbody>{agents.map((a) => (
            <tr key={a.key} className="border-t border-surface-border align-top">
              <td className="px-3 py-2"><input type="checkbox" disabled={!canEdit} checked={!v.disabled_agents.includes(a.key)} onChange={() => toggle(a.key)} /></td>
              <td className="px-3 py-2"><div className="font-medium text-gray-800">{a.name}</div><div className="text-[11px] text-gray-400 max-w-xs">{a.checks}</div></td>
              <td className="px-3 py-2">
                {Object.keys(a.threshold_help).length === 0 ? <span className="text-gray-400">No thresholds.</span> : (
                  <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-2">
                    {Object.entries(a.threshold_help).map(([name, help]) => (
                      <label key={name} className="block"><span className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">{name.replace(/_/g, " ")}</span>
                        <input className="input !py-1 text-sm w-full" disabled={!canEdit} value={v.thresholds?.[a.key]?.[name] ?? ""} placeholder={String(thFor(a.key)[name] ?? "")}
                          onChange={(e) => setTh(a.key, name, e.target.value)} />
                        <span className="text-[11px] text-gray-400">{help}</span></label>))}
                  </div>)}
              </td>
            </tr>))}</tbody>
        </table>
      </div>
    </div>
  );
}
