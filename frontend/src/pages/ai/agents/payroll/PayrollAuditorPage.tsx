import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams, Link } from "react-router-dom";
import toast from "react-hot-toast";
import { Bot, Play, RefreshCw, ShieldCheck, ShieldAlert, CheckCircle2, XCircle, MessageSquareText, PenLine, ArrowUpRight, Lock, Send, Inbox, Settings2 } from "lucide-react";
import DataGrid, { WideModal } from "../../../../components/DataGrid";
import { PageHeader, Spinner, StatTile } from "../../../../components/ui";
import { errText, when } from "../../../../api/platform";
import { ModeBadge, Pill, TierBadge } from "../shared";
import { CHECK_LABEL, FREQ_LABEL, VARIANCE_CODES, money, payrollAuditorApi, periodLabel, type Finding, type Pack } from "./api";

const SEV_TONE: Record<string, string> = { critical: "bg-red-100 text-red-700", warning: "bg-amber-100 text-amber-700", info: "bg-gray-100 text-gray-600" };
const ST_TONE: Record<string, string> = { unexplained: "bg-amber-100 text-amber-800", explained: "bg-sky-100 text-sky-700", confirmed: "bg-green-100 text-green-700", dismissed: "bg-gray-100 text-gray-500", info: "bg-gray-100 text-gray-500", open: "bg-amber-100 text-amber-800" };
const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(1)} %`);
const statusLabel = (f: Finding) => (f.status === "unexplained" && !VARIANCE_CODES.has(f.code) ? "open" : f.status);

/** Payroll Auditor — the review pack for one pay period (Canada): summary tiles, the severity-ranked exception grid,
 *  waiting-on, Canada statutory sanity (CPP / QPP, EI, QPIP from the payroll engine's constants, verified flags),
 *  register = GL tie-out, timing; Confirm / Dismiss / Note / Explain; Run now. Office roles. */
export default function PayrollAuditorPage() {
  const qc = useQueryClient();
  const [sp, setSp] = useSearchParams();
  const period = sp.get("period") || "";
  const freq = sp.get("frequency") || "";
  const focus = sp.get("finding") || "";
  const [open, setOpen] = useState<Finding | null>(null);
  const [showThresholds, setShowThresholds] = useState(false);
  const status = useQuery({ queryKey: ["pa", "status", period, freq], queryFn: () => payrollAuditorApi.status(period || undefined, freq || undefined) });
  const periods = useQuery({ queryKey: ["pa", "periods"], queryFn: payrollAuditorApi.periods });
  const st = status.data;
  const shownPeriod = period || st?.period || "";
  const pack = useQuery({ queryKey: ["pa", "pack", shownPeriod], queryFn: () => payrollAuditorApi.pack({ period: shownPeriod || undefined }), retry: false, enabled: !!shownPeriod });
  const refresh = () => { qc.invalidateQueries({ queryKey: ["pa"] }); qc.invalidateQueries({ queryKey: ["agentic"] }); };
  const runNow = useMutation({ mutationFn: () => payrollAuditorApi.run(shownPeriod || undefined, st?.pay_frequency), onSuccess: (r) => { toast.success(r.summary?.headline || "Run finished"); refresh(); }, onError: (e) => toast.error(errText(e)) });
  const setParam = (k: string, v: string) => setSp((prev) => { const n = new URLSearchParams(prev); if (v) n.set(k, v); else n.delete(k); n.delete("finding"); if (k === "frequency") n.delete("period"); return n; }, { replace: true });
  const d = pack.data;
  const s = d?.summary;
  const grid = d?.grid || [];
  const focused = useMemo(() => grid.find((g) => g.id === focus) || null, [grid, focus]);
  const shown = open || (open === null && focus ? focused : null);
  const options = useMemo(() => {
    const set = new Set<string>();
    if (st?.period) set.add(st.period);
    if (st?.prev_period) set.add(st.prev_period);
    (periods.data?.items || []).filter((p) => !st || p.pay_period.startsWith(`${st.pay_frequency}:`)).forEach((p) => set.add(p.pay_period));
    return Array.from(set).sort().reverse();
  }, [st, periods.data]);
  if (status.isLoading) return <div className="grid place-items-center py-24"><Spinner className="h-8 w-8" /></div>;
  if (status.isError || !st) return <div className="card text-sm text-red-600">Could not load the Payroll Auditor. {errText(status.error)}</div>;
  const enabled = st.state.enabled;
  const mode = st.state.mode;
  return (
    <div>
      <PageHeader title="Payroll Auditor" icon={Bot}
        subtitle={`${periodLabel(st.period)} · pay date ${st.pay_date} · freeze by ${st.freeze_by} (T-${st.thresholds.lead_days}) · approve by ${st.approve_by} (T-${st.thresholds.approval_lead_days}) · ${st.headcount} on this pay frequency`}
        actions={<div className="flex items-center gap-2 flex-wrap">
          <select className="input !py-1.5 text-sm w-36" value={st.pay_frequency} onChange={(e) => setParam("frequency", e.target.value)} aria-label="Pay frequency">
            {Array.from(new Set([...(st.pay_frequencies || []), st.pay_frequency])).map((f) => <option key={f} value={f}>{FREQ_LABEL[f] || f}</option>)}</select>
          <select className="input !py-1.5 text-sm w-72" value={shownPeriod} onChange={(e) => setParam("period", e.target.value === st.period ? "" : e.target.value)} aria-label="Pay period">
            {options.map((p) => <option key={p} value={p}>{periodLabel(p)}{p === st.period ? " (current)" : ""}</option>)}</select>
          <ModeBadge mode={mode} enabled={enabled} />
          {st.can_edit && <button className="btn-secondary !py-1.5 text-xs inline-flex items-center gap-1" onClick={() => setShowThresholds(true)}><Settings2 className="w-3.5 h-3.5" /> Thresholds</button>}
          <button className="btn-secondary !py-1.5 text-xs inline-flex items-center gap-1.5" onClick={refresh}><RefreshCw className="w-3.5 h-3.5" /> Refresh</button>
          <button className="btn-primary !py-1.5 text-xs inline-flex items-center gap-1.5" disabled={!enabled || runNow.isPending} onClick={() => runNow.mutate()} title={enabled ? "Collect inputs, run the 17 checks, chase, prepare the pack" : "The Agentic operations add-on is off"}>
            {runNow.isPending ? <Spinner className="h-3.5 w-3.5" /> : <Play className="w-3.5 h-3.5" />} Run now</button>
        </div>} />

      {!enabled && <div className="card mb-4 border-amber-200 bg-amber-50/60 text-sm text-amber-900"><b>The Agentic operations add-on is {st.state.paused ? "paused" : "not active"} for this company.</b> The Payroll Auditor only runs with the add-on on — see <Link to="/ai/agents?tab=settings" className="underline">Agents → Settings</Link>.</div>}
      {enabled && mode === "shadow" && <div className="card mb-4 border-violet-200 bg-violet-50/60 text-sm text-violet-900"><b>Shadow mode — nothing was sent; this is what I would have done.</b> Tasks it would have opened and actions it would have proposed are listed below; nobody is chased and nothing executes until an org admin switches to live. {st.state.live_gate_note}</div>}

      <div className="card mb-4 text-xs text-gray-600 flex flex-wrap gap-x-5 gap-y-1 items-center">
        <span className="inline-flex items-center gap-1"><Lock className={`w-3.5 h-3.5 ${st.attendance.locked ? "text-emerald-600" : "text-gray-300"}`} />
          Timesheets {st.attendance.tracked ? (st.attendance.locked ? "locked" : `${st.attendance.ready}/${st.attendance.total} approved — not locked`) : "not tracked (lock state unknown)"}</span>
        <span>Previous regular run: {st.previous_run ? `${st.previous_run.period_end} (${st.previous_run.status})` : "none"}</span>
        <span>Payroll run for this period: {st.engine_run ? <>{st.engine_run.status} · <Link to="/payroll" className="text-primary-600 hover:underline">open payroll</Link></> : "not yet (the pack works on the engine's dry run)"}</span>
        <span>Latest audit run: {st.latest_run ? <>{when(st.latest_run.started_at)} · <Pill v={st.latest_run.status} /> · {st.latest_run.trigger.replace(/_/g, " ")}</> : "none — press Run now"}</span>
        <Link to="/ai/agents?tab=approvals&agent=payroll_auditor" className="ml-auto text-primary-600 hover:underline inline-flex items-center gap-1"><Inbox className="w-3.5 h-3.5" /> Approvals inbox</Link>
      </div>

      {pack.isLoading && <div className="grid place-items-center py-12"><Spinner className="h-6 w-6" /></div>}
      {(pack.isError || (pack.isSuccess && !pack.data)) && <div className="card text-sm text-gray-600">No review pack for {periodLabel(shownPeriod)} yet. {enabled ? "Press Run now — the agent collects the inputs, gets the draft from the payroll engine's dry run and runs the 17 checks." : ""}</div>}
      {d && s && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3 mb-4">
            <StatTile label="Handled" value={s.handled} sub={`of ${s.projected.this_period.headcount} pay stubs — nothing to look at`} />
            <StatTile label="Need a look" value={s.needs_attention} sub={`${s.blocking ?? s.critical} blocking · ${s.critical} critical · ${s.warnings} warning(s)`} accent={s.needs_attention > 0} />
            <StatTile label="Unexplained" value={s.unexplained} sub={`${s.explained} explained (${s.explained_by_model} rephrased by the model)`} accent={s.unexplained > 0} />
            <StatTile label="Projected net" value={money(s.projected.this_period.net)} sub={s.projected.delta ? `${pct(s.projected.delta_pct?.net)} vs the previous run (${money(s.projected.delta.net)})` : "no previous run to compare"} />
            <StatTile label="Gross · CPP/QPP+EI+QPIP · tax" value={<span className="text-sm sm:text-base">{money(s.projected.this_period.gross)} · {money(s.projected.this_period.statutory)} · {money(s.projected.this_period.income_tax)}</span>} sub={s.projected.delta ? `gross ${pct(s.projected.delta_pct?.gross)} · tax ${pct(s.projected.delta_pct?.income_tax)}` : undefined} />
            <StatTile label="$ at risk flagged" value={money(s.dollars_at_risk)} sub={`${money(s.confirmed_dollars || 0)} confirmed by a person`} />
          </div>

          <Gates d={d} />

          <div className="grid grid-cols-1 2xl:grid-cols-[1fr_380px] gap-4 mb-4">
            <Grid grid={grid} onOpen={setOpen} runId={d.run.id} shadow={d.shadow} />
            <div className="space-y-4">
              <WaitingOn d={d} />
              <Statutory s={s} grid={grid} onOpen={setOpen} />
              <TieOut s={s} />
              <Checks s={s} />
            </div>
          </div>
          {d.shadow && d.would_have_done.length > 0 && <WouldHaveDone d={d} />}
        </>
      )}
      {shown && <FindingModal f={shown} shadow={!!d?.shadow} onClose={() => { setOpen(null); if (focus) setSp((prev) => { const n = new URLSearchParams(prev); n.delete("finding"); return n; }, { replace: true }); }} onChanged={refresh} />}
      {showThresholds && <ThresholdsModal onClose={() => setShowThresholds(false)} onSaved={refresh} />}
    </div>
  );
}

function Gates({ d }: { d: Pack }) {
  const g = d.summary.gates;
  if (!g) return null;
  const item = (ok: boolean, label: string, Icon: any) => <span className={`inline-flex items-center gap-1 text-xs px-2 py-1 rounded-full border ${ok ? "border-emerald-200 bg-emerald-50 text-emerald-700" : "border-gray-200 text-gray-500"}`}><Icon className="w-3.5 h-3.5" />{label}</span>;
  return (
    <div className="card mb-4 flex flex-wrap items-center gap-2 text-xs">
      <span className="font-semibold text-gray-700 mr-1">Gates</span>
      {item(g.attendance_locked || !g.attendance_tracked, g.attendance_tracked ? "timesheets locked" : "timesheets not tracked", Lock)}
      {item(g.blocking === 0, g.blocking === 0 ? "no blocking finding" : `${g.blocking} blocking (${g.blocking_codes.join(", ")})`, ShieldAlert)}
      {item(g.critical_unexplained === 0, g.critical_unexplained === 0 ? "critical variances explained" : `${g.critical_unexplained} critical unexplained`, MessageSquareText)}
      {item(g.can_lock, "freeze inputs (tier 1)", Lock)}
      {item(g.can_send, "hand off to Payroll (tier 2)", Send)}
      {item(g.can_release, "ready to release (tier 2)", CheckCircle2)}
      {g.why_not && <span className="text-gray-500">— not proposed because: {g.why_not}</span>}
      {d.summary.sent_for_approval_at && <span className="text-emerald-700">approved by two people {when(d.summary.sent_for_approval_at)} — a person commits the run in Payroll</span>}
      {d.summary.release_ready_at && <span className="text-emerald-700">ready to release recorded {when(d.summary.release_ready_at)}</span>}
      <Link to="/ai/agents?tab=approvals&agent=payroll_auditor" className="ml-auto text-primary-600 hover:underline inline-flex items-center gap-1">tier-2 items in the Approvals inbox <ArrowUpRight className="w-3 h-3" /></Link>
    </div>
  );
}

function Grid({ grid, onOpen, runId, shadow }: { grid: Finding[]; onOpen: (f: Finding) => void; runId: string; shadow: boolean }) {
  const [sev, setSev] = useState("");
  const [stt, setStt] = useState("");
  const [dept, setDept] = useState("");
  const [code, setCode] = useState("");
  const depts = useMemo(() => Array.from(new Set(grid.map((g) => g.department || g.cost_centre || "").filter(Boolean))).sort(), [grid]);
  const codes = useMemo(() => Array.from(new Set(grid.map((g) => g.code))).sort(), [grid]);
  const rows = grid.filter((g) => (!sev || g.severity === sev) && (!stt || statusLabel(g) === stt) && (!dept || (g.department || g.cost_centre) === dept) && (!code || g.code === code));
  return (
    <div>
      <DataGrid<Finding> rows={rows} rowKey={(r) => r.id} minWidth={1240} exportName={`payroll-auditor-${runId.slice(0, 8)}`} density="compact" onRowClick={onOpen} stickyHeader maxHeight="70vh"
        initialSort={{ key: "rank", dir: "asc" }} pageSizeDefault={50}
        empty={grid.length ? "No row matches the filters." : "Clean — every pay stub passed the 17 checks."}
        rowClassName={(r) => r.severity === "critical" && !["dismissed", "confirmed"].includes(r.status) ? "bg-red-50/40" : r.status === "dismissed" ? "opacity-60" : ""}
        filters={<>
          <select className="input !py-1.5 text-sm w-32" value={sev} onChange={(e) => setSev(e.target.value)} aria-label="Severity"><option value="">All severities</option><option value="critical">Critical</option><option value="warning">Warning</option><option value="info">Info</option></select>
          <select className="input !py-1.5 text-sm w-36" value={stt} onChange={(e) => setStt(e.target.value)} aria-label="Status"><option value="">All statuses</option><option value="open">Open (needs a fix)</option><option value="unexplained">Unexplained</option><option value="explained">Explained</option><option value="confirmed">Confirmed</option><option value="dismissed">Dismissed</option><option value="info">Info</option></select>
          <select className="input !py-1.5 text-sm w-44" value={code} onChange={(e) => setCode(e.target.value)} aria-label="Check"><option value="">All checks</option>{codes.map((c) => <option key={c} value={c}>{c} {CHECK_LABEL[c] || ""}</option>)}</select>
          <select className="input !py-1.5 text-sm w-40" value={dept} onChange={(e) => setDept(e.target.value)} aria-label="Department"><option value="">All departments</option>{depts.map((x) => <option key={x} value={x}>{x}</option>)}</select>
          {shadow && <span className="badge-purple">shadow: tasks not opened</span>}
        </>}
        columns={[
          { key: "rank", label: "Sev", width: "84px", render: (r) => <Pill v={r.severity} map={SEV_TONE} />, text: (r) => String(r.rank) },
          { key: "code", label: "Check", width: "140px", render: (r) => <span title={r.check}><span className="font-mono text-[11px] text-gray-500">{r.code}</span> <span className="text-xs">{CHECK_LABEL[r.code] || r.check}</span></span>, text: (r) => `${r.code} ${r.check}` },
          { key: "employee_ref", label: "Employee", render: (r) => <span className="whitespace-nowrap">{r.employee_ref || <span className="text-gray-400">— (company level)</span>}</span>, text: (r) => r.employee_ref || "" },
          { key: "title", label: "What", render: (r) => <span className="text-xs text-gray-700 whitespace-normal block min-w-[16rem] max-w-md line-clamp-2" title={r.title}>{r.title}</span>, text: (r) => r.title },
          { key: "component", label: "Component", width: "110px", render: (r) => <span className="font-mono text-xs">{r.component || "—"}</span>, text: (r) => r.component || "" },
          { key: "prev", label: "Previous / expected", type: "money", render: (r) => <span className="tabular-nums">{money(r.prev)}</span>, text: (r) => String(r.prev ?? "") },
          { key: "curr", label: "This run", type: "money", render: (r) => <span className="tabular-nums">{money(r.curr)}</span>, text: (r) => String(r.curr ?? "") },
          { key: "delta_cad", label: "Δ $", type: "money", render: (r) => <span className={`tabular-nums whitespace-nowrap ${(r.delta_cad || 0) < 0 ? "text-red-700" : (r.delta_cad || 0) > 0 ? "text-emerald-700" : ""}`}>{money(r.delta_cad)}</span>, text: (r) => String(r.delta_cad ?? "") },
          { key: "delta_pct", label: "Δ %", type: "percent", width: "80px", render: (r) => <span className="tabular-nums whitespace-nowrap">{pct(r.delta_pct)}</span>, text: (r) => String(r.delta_pct ?? "") },
          { key: "owner_role", label: "Owner", width: "90px", render: (r) => <span className="text-xs">{r.owner_role || "—"}</span>, text: (r) => r.owner_role || "" },
          { key: "linked", label: "Linked event", width: "140px", render: (r) => r.linked_event ? <span className="text-xs">{String(r.linked_event.type).replace(/_/g, " ")}</span> : <span className="text-gray-300">—</span>, text: (r) => r.linked_event?.type || "" },
          { key: "status", label: "Status", width: "110px", render: (r) => <Pill v={statusLabel(r)} map={ST_TONE} />, text: (r) => statusLabel(r) },
          { key: "checker_note", label: "Checker note", render: (r) => <span className="text-xs text-gray-600 whitespace-normal block min-w-[12rem] max-w-sm line-clamp-2" title={r.checker_note || r.explanation || ""}>{r.checker_note || (r.explanation && r.status !== "unexplained" && VARIANCE_CODES.has(r.code) ? <span className="text-gray-400 italic">{r.explanation}</span> : "")}</span>, text: (r) => r.checker_note || "" },
        ]} />
    </div>
  );
}

function WaitingOn({ d }: { d: Pack }) {
  const s = d.summary;
  const live = d.waiting_on;
  const would = s.would_open || [];
  return (
    <div className="card">
      <div className="flex items-center justify-between mb-2"><h4 className="text-xs uppercase tracking-wide text-gray-500 font-semibold">Waiting on</h4>
        <Link to="/ai/agents?tab=tasks&agent=payroll_auditor" className="text-[11px] text-primary-600 hover:underline">all tasks</Link></div>
      {d.shadow ? (
        would.length === 0 ? <p className="text-xs text-gray-500">Nothing — no gap needed a person.</p> : (
          <ul className="text-xs space-y-1">{would.map((w, i) => <li key={i} className="flex gap-2"><span className="badge-purple shrink-0">{w.owner_role}</span><span className="text-gray-700">{w.title}{w.employee_ref ? <span className="text-gray-400"> · {w.employee_ref}</span> : null}</span></li>)}</ul>)
      ) : live.length === 0 ? <p className="text-xs text-gray-500">Nobody — every input is in.</p> : (
        <ul className="text-xs space-y-1.5">{live.map((t) => <li key={t.id} className="flex gap-2 items-start">
          <Pill v={t.status} /><div className="min-w-0"><div className="text-gray-800">{t.title}</div>
            <div className="text-[11px] text-gray-500">{t.owner_user_id || t.owner_employee_id} · {t.age_hours !== null ? `${t.age_hours} h` : ""} · {t.chase_count} reminder(s){t.overdue ? " · overdue" : ""}</div></div></li>)}</ul>)}
      {s.chase && <div className="text-[11px] text-gray-400 mt-2">{s.chase.proposed} chase(s) proposed · {s.chase.opened} opened · {s.chase.deduplicated} already open · {s.chase.skipped_no_owner} with no login to chase</div>}
    </div>
  );
}

function Statutory({ s, grid, onOpen }: { s: Pack["summary"]; grid: Finding[]; onOpen: (f: Finding) => void }) {
  const st = s.statutory || ({} as Pack["summary"]["statutory"]);
  const rows = grid.filter((g) => ["PA-13", "PA-14", "PA-15", "PA-17"].includes(g.code));
  const roc = st.roc || null;
  const qcT = st.qc || null;
  const allVerified = st.tracked > 0 && st.verified === st.tracked;
  const flag = allVerified ? <span className="badge-green"><ShieldCheck className="w-3 h-3 mr-0.5" />verified</span> : <span className="badge-amber"><ShieldAlert className="w-3 h-3 mr-0.5" />{st.tracked ? `${st.verified}/${st.tracked} verified` : "verification state unknown"}</span>;
  return (
    <div className="card">
      <h4 className="text-xs uppercase tracking-wide text-gray-500 font-semibold mb-2">Statutory sanity (Canada)</h4>
      <dl className="text-xs grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        <dt className="text-gray-500">Source</dt><dd className="text-gray-700">{st.source || "—"} {flag}</dd>
        <dt className="text-gray-500">Edition</dt><dd>{st.year || "—"}{st.edition_found === false ? <span className="text-red-700"> — no CPP / EI tables for this year</span> : null}</dd>
        <dt className="text-gray-500">Plans in this run</dt><dd>{(st.plans || []).join(" · ") || "—"}</dd>
        {roc?.cpp && <><dt className="text-gray-500">CPP</dt><dd className="tabular-nums">{(roc.cpp.rate * 100).toFixed(2)} % · YMPE {money(roc.cpp.ympe)} · max {money(roc.cpp.max_employee)}</dd></>}
        {roc?.cpp2 && <><dt className="text-gray-500">CPP2</dt><dd className="tabular-nums">{(roc.cpp2.rate * 100).toFixed(2)} % · YAMPE {money(roc.cpp2.yampe)} · max {money(roc.cpp2.max_employee)}</dd></>}
        {roc?.ei && <><dt className="text-gray-500">EI</dt><dd className="tabular-nums">{(roc.ei.rate * 100).toFixed(2)} % · max {money(roc.ei.max_employee)}</dd></>}
        {qcT?.qpp && (st.plans || []).includes("QPP") && <><dt className="text-gray-500">QPP · QPIP · EI (QC)</dt><dd className="tabular-nums">{(qcT.qpp.rate * 100).toFixed(2)} % · {(qcT.qpip.rate * 100).toFixed(2)} % · {(qcT.ei_qc.rate * 100).toFixed(2)} %</dd></>}
        <dt className="text-gray-500">Vacation minimum</dt><dd>{Object.keys(st.vacation_min || {}).length ? Object.entries(st.vacation_min).map(([p, r]) => `${p}: ${r.tiers.map(([y, v]) => `${v} % from ${y} y`).join(", ")}`).join(" · ") : "unknown — only the employee's own rate is checked"}</dd>
      </dl>
      {rows.length > 0 && <ul className="mt-2 text-xs space-y-1">{rows.slice(0, 8).map((f) => <li key={f.id}><button className="text-left hover:underline" onClick={() => onOpen(f)}><Pill v={f.severity} map={SEV_TONE} /> {f.title} <span className="text-gray-400">{f.employee_ref}</span></button></li>)}{rows.length > 8 && <li className="text-gray-400">+ {rows.length - 8} more in the grid</li>}</ul>}
      {st.note && <p className="text-[11px] text-amber-700 mt-2">{st.note}</p>}
    </div>
  );
}

function TieOut({ s }: { s: Pack["summary"] }) {
  const t = s.tieout || {};
  const reg = t.register;
  const gl = t.gl;
  const line = (a: string, av: any, b: string, bv: any) => {
    const ok = av !== undefined && bv !== undefined && bv !== null && Math.abs(Number(av) - Number(bv)) <= 0.05;
    return <tr key={a + b} className="border-t border-surface-border"><td className="py-1 pr-2 text-gray-600">{a}</td><td className="py-1 pr-2 tabular-nums text-right">{money(av)}</td><td className="py-1 pr-2 text-gray-600">{b}</td><td className="py-1 tabular-nums text-right">{bv === undefined || bv === null ? <span className="text-gray-400">pending</span> : money(bv)}</td><td className="py-1 pl-2">{bv === undefined || bv === null ? "" : ok ? <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600" /> : <XCircle className="w-3.5 h-3.5 text-red-600" />}</td></tr>;
  };
  return (
    <div className="card">
      <h4 className="text-xs uppercase tracking-wide text-gray-500 font-semibold mb-2">Totals tie-out</h4>
      {!reg ? <p className="text-xs text-gray-500">Runs on the committed payroll run (register = GL journal). The draft is a dry run — nothing to tie yet.</p> : (
        <table className="w-full text-xs"><tbody>
          {line("Register net", reg.net, "GL net pay payable", gl?.net_credit)}
          {line("Register gross", reg.gross, "GL salaries & wages", gl?.gross_debit)}
          {line("Register income tax", reg.income_tax, "GL income tax payable", gl?.tax_credit)}
        </tbody></table>)}
      {gl && gl.balanced === false && <p className="text-[11px] text-red-700 mt-1">The GL journal does not balance.</p>}
      <p className="text-[11px] text-gray-400 mt-1">Bank-file leg: not available — Lexora HRM has no direct-deposit (EFT) file to tie to.</p>
    </div>
  );
}

function Checks({ s }: { s: Pack["summary"] }) {
  return (
    <div className="card">
      <h4 className="text-xs uppercase tracking-wide text-gray-500 font-semibold mb-2">17 checks · {s.timing?.freeze_by ? `freeze ${s.timing.freeze_by} · approve ${s.timing.approve_by}` : ""}</h4>
      <table className="w-full text-[11px]"><tbody>
        {(s.checks || []).map((c) => <tr key={c.code} className="border-t border-surface-border">
          <td className="py-0.5 pr-1 font-mono text-gray-500">{c.code}</td><td className="py-0.5 pr-1 text-gray-700">{c.name}</td>
          <td className="py-0.5 text-right tabular-nums">{c.status === "ran" ? <>{c.critical ? <span className="text-red-700 font-semibold">{c.critical}</span> : null}{c.critical && c.warning ? " · " : ""}{c.warning ? <span className="text-amber-700">{c.warning}</span> : null}{!c.critical && !c.warning ? <span className="text-emerald-700">ok</span> : null}</> : <span className="text-gray-400" title={c.note || ""}>{c.status}</span>}</td></tr>)}
      </tbody></table>
      {(s.checks || []).some((c) => c.note) && <details className="mt-1 text-[11px] text-gray-500"><summary className="cursor-pointer">What was skipped and why</summary><ul className="list-disc pl-4">{(s.checks || []).filter((c) => c.note).map((c) => <li key={c.code}>{c.code}: {c.note}</li>)}</ul></details>}
      {s.sources && <details className="mt-1 text-[11px] text-gray-500"><summary className="cursor-pointer">Sources read</summary><ul className="list-disc pl-4">{Object.entries(s.sources).map(([k, v]) => <li key={k}><b>{k}</b>: {v}</li>)}</ul></details>}
    </div>
  );
}

function WouldHaveDone({ d }: { d: Pack }) {
  return (
    <div className="card mb-4">
      <h4 className="text-xs uppercase tracking-wide text-gray-500 font-semibold mb-2">What I would have done (shadow — nothing ran)</h4>
      <div className="overflow-x-auto"><table className="w-full text-[13px]">
        <thead className="bg-surface text-[11px] uppercase tracking-wide text-gray-500 text-left"><tr><th className="px-2 py-1.5">Tier</th><th className="px-2 py-1.5">Action</th><th className="px-2 py-1.5">Tool</th><th className="px-2 py-1.5 text-right">$</th></tr></thead>
        <tbody>{d.would_have_done.map((p) => <tr key={p.id} className="border-t border-surface-border"><td className="px-2 py-1"><TierBadge tier={p.risk_tier} untrusted={p.untrusted_input} /></td><td className="px-2 py-1 text-gray-800">{p.plain_action}</td><td className="px-2 py-1 font-mono text-xs text-gray-500">{p.tool_name}</td><td className="px-2 py-1 text-right tabular-nums">{p.aggregate_impact?.dollars ? money(p.aggregate_impact.dollars) : "—"}</td></tr>)}</tbody></table></div>
    </div>
  );
}

function FindingModal({ f, shadow, onClose, onChanged }: { f: Finding; shadow: boolean; onClose: () => void; onChanged: () => void }) {
  const [note, setNote] = useState("");
  const [why, setWhy] = useState("");
  const [cur, setCur] = useState<Finding>(f);
  const done = (r: Finding, msg: string) => { setCur(r); toast.success(msg); onChanged(); };
  const confirm = useMutation({ mutationFn: () => payrollAuditorApi.confirm(cur.id, note), onSuccess: (r) => done(r, `Confirmed — ${money(r.dollars_at_risk)} counts as caught before pay`), onError: (e) => toast.error(errText(e)) });
  const dismiss = useMutation({ mutationFn: () => payrollAuditorApi.dismiss(cur.id, note), onSuccess: (r) => done(r, "Dismissed with your reason"), onError: (e) => toast.error(errText(e)) });
  const addNote = useMutation({ mutationFn: () => payrollAuditorApi.note(cur.id, note), onSuccess: (r) => { done(r, "Note added"); setNote(""); }, onError: (e) => toast.error(errText(e)) });
  const explain = useMutation({ mutationFn: () => payrollAuditorApi.explain(cur.id, why), onSuccess: (r) => done(r, "Reason recorded — the chase task is closed"), onError: (e) => toast.error(errText(e)) });
  const busy = confirm.isPending || dismiss.isPending || addNote.isPending || explain.isPending;
  const decided = cur.status === "confirmed" || cur.status === "dismissed";
  return (
    <WideModal size="xwide" title={`${cur.code} · ${cur.check}`} onClose={onClose} printable
      subtitle={<span className="inline-flex items-center gap-2 flex-wrap"><Pill v={cur.severity} map={SEV_TONE} /><Pill v={statusLabel(cur)} map={ST_TONE} />{cur.employee_ref || "company level"}{cur.department ? ` · ${cur.department}` : ""}{cur.owner_role ? ` · owner: ${cur.owner_role}` : ""}{cur.verified === false ? <span className="badge-amber">figure not yet verified</span> : null}</span>}
      footer={<div className="flex flex-wrap items-center gap-2 w-full">
        <input className="input !py-1.5 text-sm flex-1 min-w-[200px]" placeholder="Checker note (optional for Confirm; required for Dismiss as the reason)" value={note} onChange={(e) => setNote(e.target.value)} />
        <button className="btn-secondary !py-1.5 text-xs inline-flex items-center gap-1" disabled={busy || !note.trim()} onClick={() => addNote.mutate()}><PenLine className="w-3.5 h-3.5" /> Add note</button>
        <button className="btn-secondary !py-1.5 text-xs inline-flex items-center gap-1 text-red-700" disabled={busy || decided || note.trim().length < 3} onClick={() => dismiss.mutate()} title="Needs a reason (at least 3 characters)"><XCircle className="w-3.5 h-3.5" /> Dismiss</button>
        <button className="btn-primary !py-1.5 text-xs inline-flex items-center gap-1" disabled={busy || cur.status === "confirmed" || cur.severity === "info"} onClick={() => confirm.mutate()} title="Records the dollars at risk as caught before pay (value ledger)"><CheckCircle2 className="w-3.5 h-3.5" /> Confirm exception {cur.dollars_at_risk ? `(${money(cur.dollars_at_risk)})` : ""}</button>
      </div>}>
      <div className="space-y-4">
        <div className="text-base font-display font-semibold text-gray-800">{cur.title}</div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="card !p-3">
            <div className="text-[11px] uppercase text-gray-500 mb-1">Reason</div>
            <p className="text-sm text-gray-800">{cur.explanation || "—"}</p>
            <div className="text-[11px] text-gray-400 mt-1">{cur.explained_by === "model" ? "Rephrased by Lexi from the linked event (every figure validated against the record)." : cur.explained_by === "person" ? `Written by ${cur.decided_by || "a person"}.` : cur.explained_by === "template" ? "Deterministic link — templated wording." : ""}</div>
            <div className="text-[11px] uppercase text-gray-500 mt-3 mb-1">Rule</div>
            <p className="text-xs text-gray-700">{cur.rule}</p>
            {Object.keys(cur.threshold || {}).filter((k) => !k.startsWith("_")).length > 0 && <div className="mt-2 flex flex-wrap gap-1">{Object.entries(cur.threshold).filter(([k]) => !k.startsWith("_")).map(([k, v]) => <span key={k} className="text-[10px] px-1.5 py-0.5 rounded border border-gray-200 text-gray-600">{k.replace(/_/g, " ")}: {typeof v === "object" ? JSON.stringify(v) : String(v)}</span>)}</div>}
          </div>
          <div className="card !p-3">
            <div className="text-[11px] uppercase text-gray-500 mb-1">Before → after</div>
            <table className="w-full text-sm"><tbody>
              <tr className="border-t border-surface-border"><td className="py-1 text-gray-600">Previous / expected</td><td className="py-1 text-right tabular-nums">{money(cur.prev)}</td></tr>
              <tr className="border-t border-surface-border"><td className="py-1 text-gray-600">This run</td><td className="py-1 text-right tabular-nums font-semibold">{money(cur.curr)}</td></tr>
              <tr className="border-t border-surface-border"><td className="py-1 text-gray-600">Δ</td><td className="py-1 text-right tabular-nums">{money(cur.delta_cad)} ({pct(cur.delta_pct)})</td></tr>
              <tr className="border-t border-surface-border"><td className="py-1 text-gray-600">$ at risk</td><td className="py-1 text-right tabular-nums">{money(cur.dollars_at_risk)}</td></tr>
            </tbody></table>
            {cur.linked_event && <><div className="text-[11px] uppercase text-gray-500 mt-3 mb-1">Linked event</div><dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">{Object.entries(cur.linked_event).map(([k, v]) => <span key={k} className="contents"><dt className="text-gray-500">{k.replace(/_/g, " ")}</dt><dd className="font-mono break-all">{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd></span>)}</dl></>}
          </div>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="card !p-3">
            <div className="text-[11px] uppercase text-gray-500 mb-1">Evidence</div>
            {cur.evidence_refs?.length ? <ul className="text-sm space-y-0.5">{cur.evidence_refs.map((e, i) => <li key={i}>{e.link ? <Link to={e.link} className="text-primary-600 hover:underline inline-flex items-center gap-1" onClick={onClose}>{e.label || e.link} <ArrowUpRight className="w-3 h-3" /></Link> : e.label}</li>)}</ul> : <p className="text-xs text-gray-400">No record link for this finding.</p>}
            {cur.fix_hint && <><div className="text-[11px] uppercase text-gray-500 mt-3 mb-1">How to fix</div><p className="text-sm text-gray-700">{cur.fix_hint}</p></>}
            {cur.task_id && <p className="text-[11px] text-gray-500 mt-2">Chase task {cur.task_id.slice(0, 8)}… — <Link to="/ai/agents?tab=tasks&agent=payroll_auditor" className="text-primary-600 hover:underline" onClick={onClose}>open</Link></p>}
            {shadow && !cur.task_id && cur.severity !== "info" && <p className="text-[11px] text-violet-700 mt-2">Shadow mode — the chase for this item was recorded, not sent.</p>}
          </div>
          <div className="card !p-3">
            <div className="text-[11px] uppercase text-gray-500 mb-1">Checker notes</div>
            <pre className="text-xs whitespace-pre-wrap text-gray-700 font-sans">{cur.checker_note || "—"}</pre>
            {cur.decided_at && <div className="text-[11px] text-gray-400 mt-1">{cur.status} by {cur.decided_by} · {when(cur.decided_at)}</div>}
            {VARIANCE_CODES.has(cur.code) && cur.status === "unexplained" && (
              <div className="mt-3">
                <div className="text-[11px] uppercase text-gray-500 mb-1">Write the reason (closes the chase)</div>
                <div className="flex gap-2"><input className="input !py-1.5 text-sm flex-1" placeholder="e.g. Quarterly commission approved by the CFO on 20 Oct" value={why} onChange={(e) => setWhy(e.target.value)} />
                  <button className="btn-secondary !py-1.5 text-xs inline-flex items-center gap-1" disabled={busy || why.trim().length < 3} onClick={() => explain.mutate()}><MessageSquareText className="w-3.5 h-3.5" /> Explain</button></div>
              </div>)}
          </div>
        </div>
      </div>
    </WideModal>
  );
}

function ThresholdsModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const q = useQuery({ queryKey: ["pa", "thresholds"], queryFn: payrollAuditorApi.thresholds });
  const [v, setV] = useState<Record<string, any> | null>(null);
  const cur = v || (q.data ? { ...q.data.own, preset: q.data.value.preset, net_delta_pct: q.data.value.net_delta_pct } : null);
  const save = useMutation({ mutationFn: () => payrollAuditorApi.saveThresholds(cur || {}), onSuccess: () => { toast.success("Thresholds saved — the next run uses them"); onSaved(); onClose(); }, onError: (e) => toast.error(errText(e)) });
  const set = (k: string, val: any) => setV({ ...(cur || {}), [k]: val });
  const fields: [string, string][] = [["lead_days", "Start T-N days before the pay date"], ["approval_lead_days", "Approval expected T-N days before pay"], ["explanation_hours", "Explanations due within (hours)"],
    ["cost_centre_pct", "Cost-centre change needing a reason (%)"], ["deduction_floor_cad", "Ignore CPP / EI / tax moves below ($)"], ["tax_delta_pct", "Income-tax move without a TD1 change (%)"],
    ["tax_floor_cad", "…and at least ($)"], ["rate_tolerance_cad", "CPP / EI / QPIP recompute tolerance ($)"], ["vacation_tolerance_cad", "Vacation accrual tolerance ($)"],
    ["off_cycle_runs_per_quarter", "Off-cycle runs per quarter (KPI)"], ["chase_employee_hours", "Chase employee after (h)"], ["chase_manager_hours", "Chase manager after (h)"], ["chase_hr_hours", "Chase HR / payroll after (h)"]];
  return (
    <WideModal size="form" title="Payroll Auditor thresholds" onClose={onClose} subtitle="Presets: tight 2 % floods the list, loose 10 % misses errors; 5 % plus a dollar floor is the default. Direct-deposit changes use half the percentage. Review thresholds only — every statutory figure comes from the payroll engine."
      footer={<div className="flex justify-end gap-2 w-full"><button className="btn-secondary !py-1.5 text-xs" onClick={onClose}>Cancel</button><button className="btn-primary !py-1.5 text-xs" disabled={!q.data?.can_edit || save.isPending || !cur} onClick={() => save.mutate()}>Save</button></div>}>
      {!cur ? <Spinner /> : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 text-sm">
          <label className="block"><span className="text-xs text-gray-500">Variance preset</span>
            <select className="input mt-1" value={cur.preset} onChange={(e) => set("preset", e.target.value)}>{Object.entries(q.data!.presets).map(([k, p]) => <option key={k} value={k}>{k} — {p} %</option>)}<option value="custom">custom — {cur.net_delta_pct} %</option></select></label>
          <label className="block"><span className="text-xs text-gray-500">Custom variance % (sets preset to custom)</span><input className="input mt-1" type="number" step="0.5" value={cur.net_delta_pct ?? ""} onChange={(e) => setV({ ...(cur || {}), net_delta_pct: e.target.value, preset: "custom" })} /></label>
          <div className="block"><span className="text-xs text-gray-500">Dollar floor (shared, Agents → Settings)</span><div className="input mt-1 bg-surface text-gray-500">{money(q.data!.value.net_delta_floor_cad)}</div></div>
          {fields.map(([k, label]) => <label key={k} className="block"><span className="text-xs text-gray-500">{label}</span><input className="input mt-1" type="number" step="any" value={cur[k] ?? q.data!.defaults[k]} onChange={(e) => set(k, e.target.value)} /></label>)}
        </div>)}
    </WideModal>
  );
}
