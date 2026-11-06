import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import toast from "react-hot-toast";
import { AlarmClock, ArrowUpRight, Inbox, Play, Settings2, ShieldAlert, ShieldCheck, XCircle, type LucideIcon } from "lucide-react";
import DataGrid, { WideModal } from "../../../../components/DataGrid";
import { PageHeader, Spinner, StatTile } from "../../../../components/ui";
import { errText, when } from "../../../../api/platform";
import { ADMIN_ROLES } from "../../../../store/authStore";
import { useCan } from "../../../../config/permissions";
import { ModeBadge, Pill, TierBadge } from "../shared";
import { OWNER_LABEL, lifecycleApi, type CaseRow, type Clock, type Item, type Slug } from "./api";

const SEV_TONE: Record<string, string> = { blocking: "bg-red-100 text-red-700", warning: "bg-amber-100 text-amber-700", info: "bg-gray-100 text-gray-600" };
const CLOCK_TONE: Record<string, string> = { overdue: "bg-red-100 text-red-700", due_soon: "bg-amber-100 text-amber-800", ok: "bg-green-50 text-green-700",
  undefined: "bg-gray-100 text-gray-600", met: "bg-green-50 text-green-700", late: "bg-red-50 text-red-700" };

export function ClockChip({ c }: { c: Clock }) {
  const left = c.days_left === null ? "rule undefined" : c.days_left < 0 ? `${-c.days_left} day(s) overdue` : `${c.days_left} day(s) left`;
  return (
    <span className={`inline-flex items-center gap-1 text-[11px] font-medium px-2 py-0.5 rounded-full whitespace-nowrap ${CLOCK_TONE[c.status] || "bg-gray-100 text-gray-600"}`}
      title={`${c.label}${c.note ? " — " + c.note : ""}${c.source ? "\nSource: " + c.source : ""}`}>
      <AlarmClock className="w-3 h-3" />{c.due ? `due ${c.due}` : "—"} · {left}
      {!c.verified && <span className="ml-1 text-[10px] font-semibold text-orange-700">estimate — CA to confirm</span>}
    </span>
  );
}

/** One workspace shape for the three lifecycle agents: header + run, shadow banner, tiles, the case list (most urgent
 *  first) with each case's open items (severity · check · owner · due clock · chase task), what-I-would-have-done in
 *  shadow, the cards waiting in the Approvals inbox, the checks, recent runs and the agent's own settings (org admin).
 *  The server decides every rule; this screen shows them and records "does not apply". */
export default function LifecycleAgentPage({ slug, title, icon, intro, caseLabel }: { slug: Slug; title: string; icon: LucideIcon; intro: string; caseLabel: string }) {
  const qc = useQueryClient();
  const A = useMemo(() => lifecycleApi(slug), [slug]);
  const [sp, setSp] = useSearchParams();
  const focusCase = sp.get("case") || "";
  const [item, setItem] = useState<Item | null>(null);
  const [reason, setReason] = useState("");
  const [showSettings, setShowSettings] = useState(false);
  const isAdmin = useCan("ai.admin", ADMIN_ROLES);
  const q = useQuery({ queryKey: ["lifecycle", slug], queryFn: A.overview });
  const refresh = () => { qc.invalidateQueries({ queryKey: ["lifecycle", slug] }); qc.invalidateQueries({ queryKey: ["agentic"] }); };
  const runNow = useMutation({ mutationFn: A.run, onSuccess: (r) => { toast.success(r.summary?.headline || "Run finished"); refresh(); }, onError: (e) => toast.error(errText(e)) });
  const dismiss = useMutation({ mutationFn: (p: { id: string; reason: string }) => A.dismiss(p.id, p.reason),
    onSuccess: () => { toast.success("Marked as not applicable"); setItem(null); setReason(""); refresh(); }, onError: (e) => toast.error(errText(e)) });
  const o = q.data;
  const cases = o?.cases || [];
  const selected: CaseRow | null = useMemo(() => cases.find((c) => c.case_key === focusCase) || null, [cases, focusCase]);
  const setCase = (k: string) => setSp((prev) => { const n = new URLSearchParams(prev); if (k) n.set("case", k); else n.delete("case"); return n; }, { replace: true });
  const allItems = useMemo(() => cases.flatMap((c) => c.items), [cases]);
  if (q.isLoading) return <div className="p-10 grid place-items-center"><Spinner /></div>;
  if (q.isError || !o) return <div className="card text-sm text-red-700">{errText(q.error) || "Could not load the agent."}</div>;
  const latest = o.latest_run;
  const would = (latest?.summary?.would_open || []) as { owner_role: string; title: string; code: string; employee_ref: string | null }[];

  const itemCols = [
    { key: "severity", label: "Sev", render: (r: Item) => <Pill v={r.severity} map={SEV_TONE} />, text: (r: Item) => r.severity, width: "90px" },
    { key: "code", label: "Check", width: "80px" },
    { key: "case_label", label: caseLabel, text: (r: Item) => r.employee_ref || r.case_label || r.case_key },
    { key: "title", label: "Item", render: (r: Item) => <span className="text-gray-800">{r.title}</span>, text: (r: Item) => r.title },
    { key: "owner_role", label: "Owner", text: (r: Item) => OWNER_LABEL[r.owner_role || ""] || r.owner_role || "—", width: "100px" },
    { key: "due_on", label: "Due", render: (r: Item) => r.clock ? <ClockChip c={r.clock} /> : <span className="tabular-nums">{r.due_on || "—"}</span>, text: (r: Item) => r.due_on || "" },
    { key: "task", label: "Chase", render: (r: Item) => r.task_id ? <Link className="text-primary-600 hover:underline" to={`/ai/agents?tab=tasks&agent=${o.agent}`} onClick={(e) => e.stopPropagation()}>task</Link>
        : <span className="text-gray-400">{o.state.mode === "shadow" && r.severity !== "info" ? "would chase" : "—"}</span>, text: (r: Item) => (r.task_id ? "task" : ""), width: "100px" },
  ];

  return (
    <div>
      <PageHeader title={title} icon={icon} subtitle={intro}
        actions={<div className="flex items-center gap-2 flex-wrap">
          <ModeBadge mode={o.state.mode} enabled={o.state.enabled} />
          {isAdmin && <button className="btn-secondary" onClick={() => setShowSettings(true)}><Settings2 className="w-4 h-4" />Settings</button>}
          <button className="btn-primary" disabled={!o.state.enabled || runNow.isPending} onClick={() => runNow.mutate()}><Play className="w-4 h-4" />{runNow.isPending ? "Running…" : "Run now"}</button>
        </div>} />
      {!o.state.enabled && <div className="card mb-4 border-amber-200 bg-amber-50/60 text-sm text-amber-900">The Agentic operations add-on is not active for this company — see <Link className="underline" to="/ai/agents">Agents</Link>.</div>}
      {o.state.enabled && o.state.mode === "shadow" && (
        <div className="card mb-4 border-violet-200 bg-violet-50/60 text-sm text-violet-900" data-testid="lifecycle-shadow-banner">
          <b>Shadow mode — nothing was sent; this is what I would have done.</b> Reminders and drafts below are recorded only. {o.state.live_gate_note}
        </div>
      )}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3 mb-4">
        <StatTile label={caseLabel + "s in scope"} value={o.counts.cases} sub={latest ? `last run ${when(latest.started_at)}` : "not run yet"} />
        <StatTile label="Blocking" value={o.counts.blocking} accent={o.counts.blocking > 0} icon={ShieldAlert} sub="must be fixed" />
        <StatTile label="To do" value={o.counts.warning} sub="this week" />
        <StatTile label={o.state.mode === "shadow" ? "Would chase" : "Chasing"} value={o.state.mode === "shadow" ? would.length : allItems.filter((i) => i.task_id).length} sub="deduplicated reminders" />
        <StatTile label="Awaiting approval" value={o.inbox.length} icon={Inbox} sub="cards in the inbox" />
      </div>
      {latest?.summary?.headline && <div className="card mb-4 text-sm text-gray-700"><b>Latest run:</b> {latest.summary.headline} <Pill v={latest.status} /> <span className="text-gray-400">· {latest.trigger}</span>
        {latest.summary?.cutoff?.cutoff && <span className="ml-2 text-gray-500">Payroll cut-off <b>{latest.summary.cutoff.cutoff}</b> ({latest.summary.cutoff.days_to_cutoff} day(s)) — unpaid days must be in before the pay run.</span>}</div>}

      <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,360px)_minmax(0,1fr)] gap-4 mb-4">
        <div className="card !p-0 overflow-hidden">
          <div className="px-4 py-2.5 border-b border-surface-border text-sm font-semibold text-gray-700">{caseLabel}s ({cases.length}) — most urgent first</div>
          <div className="max-h-[520px] overflow-y-auto divide-y divide-surface-border">
            <button className={`w-full text-left px-4 py-2 text-xs ${!focusCase ? "bg-primary-50/60" : "hover:bg-surface"}`} onClick={() => setCase("")}>All open items ({allItems.length})</button>
            {cases.map((c) => (
              <button key={c.case_key} data-testid="lifecycle-case" className={`w-full text-left px-4 py-2 ${focusCase === c.case_key ? "bg-primary-50/60" : "hover:bg-surface"}`} onClick={() => setCase(c.case_key)}>
                <div className="flex items-center gap-2"><span className="font-medium text-gray-800 text-sm truncate">{c.employee_ref || c.case_label || c.case_key}</span>
                  {c.blocking > 0 && <span className="ml-auto text-[11px] font-semibold text-red-700">{c.blocking} blocking</span>}
                  {c.blocking === 0 && c.warning > 0 && <span className="ml-auto text-[11px] text-amber-700">{c.warning} to do</span>}
                  {c.blocking === 0 && c.warning === 0 && <span className="ml-auto text-[11px] text-green-700 inline-flex items-center gap-1"><ShieldCheck className="w-3 h-3" />clear</span>}</div>
                <div className="mt-1 flex flex-wrap gap-1">{c.clocks.slice(0, 2).map((k, i) => <ClockChip key={i} c={k} />)}{!c.clocks.length && c.next_due && <span className="text-[11px] text-gray-500">next due {c.next_due}</span>}</div>
              </button>
            ))}
            {!cases.length && <div className="px-4 py-6 text-sm text-gray-400">Nothing open. {latest ? "" : "Run the agent to check."}</div>}
          </div>
        </div>
        <div className="min-w-0">
          <DataGrid<Item> columns={itemCols as any} rows={selected ? selected.items : allItems} rowKey={(r) => r.id} exportName={`${slug}-items`} density="compact" minWidth={900}
            onRowClick={(r) => setItem(r)} empty="No open items." searchPlaceholder="Search items…" />
        </div>
      </div>

      {o.inbox.length > 0 && (
        <div className="card mb-4">
          <div className="flex items-center justify-between mb-2"><h3 className="font-semibold text-gray-800 text-sm">Waiting for your approval</h3>
            <Link to={`/ai/agents?tab=approvals&agent=${o.agent}`} className="text-primary-600 text-sm hover:underline inline-flex items-center gap-1">Open the Approvals inbox <ArrowUpRight className="w-3.5 h-3.5" /></Link></div>
          <ul className="divide-y divide-surface-border">{o.inbox.map((p) => <li key={p.id} className="py-2 flex items-center gap-3 text-sm"><TierBadge tier={p.risk_tier} untrusted={(p as any).untrusted_input} /><span className="text-gray-700">{p.plain_action}</span><Pill v={p.status} /></li>)}</ul>
        </div>
      )}
      {o.state.mode === "shadow" && (would.length > 0 || o.would_have_done.length > 0) && (
        <div className="card mb-4">
          <h3 className="font-semibold text-gray-800 text-sm mb-2">What I would have done (latest shadow run)</h3>
          <div className="overflow-x-auto"><table className="w-full text-[13px]"><thead className="text-[11px] uppercase text-gray-500 text-left"><tr><th className="py-1 pr-3">Action</th><th className="py-1 pr-3">Tier</th><th className="py-1">Status</th></tr></thead>
            <tbody>{o.would_have_done.slice(0, 60).map((p) => <tr key={p.id} className="border-t border-surface-border"><td className="py-1.5 pr-3 text-gray-700">{p.plain_action}</td><td className="py-1.5 pr-3"><TierBadge tier={p.risk_tier} /></td><td className="py-1.5"><Pill v={p.status} /></td></tr>)}</tbody></table></div>
        </div>
      )}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="card text-sm">
          <h3 className="font-semibold text-gray-800 mb-2">What it checks</h3>
          <ul className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1">{o.checks.map((c) => <li key={c.code} className="text-gray-700"><span className="text-gray-400 tabular-nums mr-1">{c.code}</span>{c.name}</li>)}</ul>
          <p className="mt-3 text-gray-600"><b>You approve:</b> {o.you_approve}</p>
          <ul className="mt-1 list-disc pl-4 text-gray-600">{o.never.map((n) => <li key={n}>Never {n}</li>)}</ul>
        </div>
        <div className="card text-sm">
          <h3 className="font-semibold text-gray-800 mb-2">Recent runs</h3>
          <ul className="divide-y divide-surface-border">{o.runs.map((r) => <li key={r.id} className="py-1.5 flex items-center gap-2"><span className="tabular-nums text-gray-500 w-40 shrink-0">{when(r.started_at)}</span><Pill v={r.mode} /><Pill v={r.status} /><span className="truncate text-gray-700">{r.summary?.headline || r.error || r.trigger}</span></li>)}
            {!o.runs.length && <li className="py-2 text-gray-400">No runs yet.</li>}</ul>
        </div>
      </div>

      {item && (
        <WideModal title={`${item.code} · ${item.title}`} subtitle={item.employee_ref || item.case_label || undefined} onClose={() => { setItem(null); setReason(""); }} size="wide"
          footer={<div className="flex items-center gap-2 w-full flex-wrap">
            <input className="input flex-1 min-w-[220px]" placeholder="Why this does not apply (3+ characters)" value={reason} onChange={(e) => setReason(e.target.value)} />
            <button className="btn-secondary" disabled={reason.trim().length < 3 || dismiss.isPending} onClick={() => dismiss.mutate({ id: item.id, reason })}><XCircle className="w-4 h-4" />Does not apply</button>
            {item.link && <Link className="btn-primary" to={item.link}>Open the record <ArrowUpRight className="w-4 h-4" /></Link>}
          </div>}>
          <div className="space-y-3 text-sm">
            <div className="flex items-center gap-2 flex-wrap"><Pill v={item.severity} map={SEV_TONE} /><span className="text-gray-500">Owner: <b className="text-gray-800">{OWNER_LABEL[item.owner_role || ""] || item.owner_role}</b></span>
              {item.clock && <ClockChip c={item.clock} />}{item.task_id && <Link className="text-primary-600 hover:underline" to={`/ai/agents?tab=tasks&agent=${o.agent}`}>chase task →</Link>}</div>
            {item.what && <p className="text-gray-700">{item.what}</p>}
            {item.how_to_fix && <p className="text-gray-700"><b>How to fix:</b> {item.how_to_fix}</p>}
            {item.clock && <div className="rounded-lg border border-surface-border p-3 text-xs text-gray-600"><b>{item.clock.label}</b>{item.clock.note ? ` — ${item.clock.note}` : ""}<br />Rule {item.clock.rule_key || "—"} · {item.clock.verified ? "verified" : "needs confirmation (estimate — CA to confirm)"}{item.clock.source && <> · <a className="underline" href={item.clock.source} target="_blank" rel="noreferrer">source</a></>}</div>}
          </div>
        </WideModal>
      )}
      {showSettings && <SettingsModal slug={slug} value={o.settings} onClose={() => setShowSettings(false)} onSaved={refresh} />}
    </div>
  );
}

function SettingsModal({ slug, value, onClose, onSaved }: { slug: Slug; value: Record<string, any>; onClose: () => void; onSaved: () => void }) {
  const A = useMemo(() => lifecycleApi(slug), [slug]);
  const [v, setV] = useState<Record<string, any>>(() => JSON.parse(JSON.stringify(value)));
  const save = useMutation({ mutationFn: () => { const { chase_every_hours: _c, ...rest } = v; return A.saveSettings(rest); },
    onSuccess: () => { toast.success("Settings saved"); onSaved(); onClose(); }, onError: (e) => toast.error(errText(e)) });
  const keys = Object.keys(v).filter((k) => k !== "chase_every_hours");
  return (
    <WideModal title="Agent settings" subtitle="Org admin — saved under this agent's own keys" onClose={onClose} size="wide"
      footer={<button className="btn-primary" disabled={save.isPending} onClick={() => save.mutate()}>{save.isPending ? "Saving…" : "Save"}</button>}>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {keys.map((k) => typeof v[k] === "object" && v[k] !== null ? (
          <fieldset key={k} className="md:col-span-2 rounded-lg border border-surface-border p-3"><legend className="text-xs font-semibold text-gray-600 px-1">{k.replace(/_/g, " ")} (login ids; blank = HR)</legend>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-2">{Object.keys(v[k]).map((kk) => <label key={kk} className="text-xs text-gray-600">{kk}
              <input className="input mt-1" value={v[k][kk] || ""} onChange={(e) => setV({ ...v, [k]: { ...v[k], [kk]: e.target.value || null } })} /></label>)}</div></fieldset>
        ) : typeof v[k] === "boolean" ? (
          <label key={k} className="flex items-center gap-2 text-sm text-gray-700"><input type="checkbox" checked={!!v[k]} onChange={(e) => setV({ ...v, [k]: e.target.checked })} />{k.replace(/_/g, " ")}</label>
        ) : (
          <label key={k} className="text-xs text-gray-600">{k.replace(/_/g, " ")}<input className="input mt-1" type="number" min={0} value={v[k] ?? ""} onChange={(e) => setV({ ...v, [k]: e.target.value === "" ? null : Number(e.target.value) })} /></label>
        ))}
      </div>
    </WideModal>
  );
}
