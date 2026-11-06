import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { FieldError } from "../../../lib/validators";
import { Table2, Play, Save, Download, Trash2, CalendarClock, Plus, X, FolderOpen } from "lucide-react";
import { PageHeader, Spinner } from "../../../components/ui";
import DataGrid, { Tabs, WideModal, type GridColumn } from "../../../components/DataGrid";
import { errText, when } from "../../../api/platform";
import { useAuthStore } from "../../../store/authStore";
import { builderApi, type RbDataset, type RbResult, type RbSaved, type RbSpec } from "../../../api/ai";
import { useUrlTab } from "../../../search/useUrlTab";
import { orgTimeZone, zoneLabel } from "../../../lib/datetime";   // 2026-10-25: UTC store, org-zone today

const EMPTY: RbSpec = { dataset: "", columns: [], filters: [], group_by: [], aggregates: [], sort: [], limit: 1000 };
const NUMERIC = new Set(["number", "money", "hours", "percent"]);
const ROLES = [["org_admin", "Org admin"], ["hr", "HR"], ["payroll", "Payroll"], ["finance", "Finance"], ["manager", "Managers"]];
const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export default function ReportBuilderPage() {
  const [sp, setSp] = useSearchParams();
  const [tab, setTab] = useUrlTab<"build" | "saved">("build");   // ?tab= (global search deep link)
  const [spec, setSpec] = useState<RbSpec>(EMPTY);
  const [result, setResult] = useState<RbResult | null>(null);
  const [current, setCurrent] = useState<RbSaved | null>(null);
  const [saveOpen, setSaveOpen] = useState(false);
  const [schedFor, setSchedFor] = useState<RbSaved | null>(null);
  const qc = useQueryClient();
  const meta = useQuery({ queryKey: ["rb", "datasets"], queryFn: builderApi.datasets });
  const saved = useQuery({ queryKey: ["rb", "saved"], queryFn: builderApi.saved });
  const ds: RbDataset | undefined = meta.data?.datasets.find((d) => d.key === spec.dataset);
  const run = useMutation({ mutationFn: (s: RbSpec) => builderApi.run(s), onSuccess: setResult, onError: (e) => toast.error(errText(e)) });
  const exp = useMutation({ mutationFn: (f: "csv" | "xlsx") => builderApi.exportFile(spec, f, current?.name || `${spec.dataset}-report`), onError: (e) => toast.error(errText(e)) });
  const del = useMutation({ mutationFn: (id: string) => builderApi.remove(id), onSuccess: () => { toast.success("Deleted"); qc.invalidateQueries({ queryKey: ["rb", "saved"] }); if (current) setCurrent(null); }, onError: (e) => toast.error(errText(e)) });
  const sendNow = useMutation({ mutationFn: (id: string) => builderApi.sendNow(id), onSuccess: (r) => { toast.success(r.status); qc.invalidateQueries({ queryKey: ["rb", "saved"] }); }, onError: (e) => toast.error(errText(e)) });

  const openSaved = (r: RbSaved) => { setCurrent(r); setSpec({ ...EMPTY, ...r.spec }); setTab("build"); setSp({ saved: r.id }); run.mutate({ ...EMPTY, ...r.spec }); };
  useEffect(() => {
    const id = sp.get("saved");
    if (id && saved.data && !current) { const r = saved.data.items.find((x) => x.id === id); if (r) openSaved(r); }
  }, [saved.data]);   // eslint-disable-line react-hooks/exhaustive-deps

  const pick = (key: string) => { setSpec({ ...EMPTY, dataset: key }); setResult(null); setCurrent(null); setSp({}); };
  const toggleCol = (k: string) => setSpec((s) => ({ ...s, columns: s.columns.includes(k) ? s.columns.filter((x) => x !== k) : [...s.columns, k] }));
  const toggleGroup = (k: string) => setSpec((s) => ({ ...s, group_by: s.group_by.includes(k) ? s.group_by.filter((x) => x !== k) : [...s.group_by, k] }));
  const outKeys = useMemo(() => spec.group_by.length ? [...spec.group_by, "count", ...spec.aggregates.filter((a) => a.fn !== "count" && a.col).map((a) => `${a.fn}_${a.col}`)] : spec.columns, [spec]);
  const gridCols: GridColumn[] = useMemo(() => (result?.columns || []).map((c) => ({ key: c.key, label: c.label, type: c.type as any })), [result]);

  if (meta.isLoading) return <div className="grid place-items-center py-24"><Spinner className="h-8 w-8" /></div>;
  if (meta.isError || !meta.data) return <div className="card text-sm text-red-600">Could not load the report builder. {errText(meta.error)}</div>;
  const m = meta.data;

  return (
    <div>
      <PageHeader title="Report builder" icon={Table2} subtitle={`${m.scope} · money columns ${m.shows_money ? "shown (your role may see pay)" : "hidden for your role"} · up to ${m.max_rows.toLocaleString()} rows`}
        actions={current && <span className="text-xs text-gray-500">Editing saved report <b className="text-gray-800">{current.name}</b>{current.shared ? " (shared)" : ""}</span>} />
      <Tabs value={tab} onChange={setTab} tabs={[{ key: "build", label: "Build" }, { key: "saved", label: "Saved reports", count: saved.data?.items.length }]} />

      {tab === "build" && (
        <div className="grid lg:grid-cols-[22rem_1fr] gap-4 items-start">
          <div className="space-y-3">
            <div className="card space-y-2">
              <label className="block"><span className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">Dataset</span>
                <select className="input !py-1.5 text-sm w-full" value={spec.dataset} onChange={(e) => pick(e.target.value)}>
                  <option value="">Pick a dataset…</option>{m.datasets.map((d) => <option key={d.key} value={d.key}>{d.title}</option>)}</select>
                {ds && <span className="text-[11px] text-gray-400">{ds.description} · {ds.scope}</span>}
              </label>
            </div>
            {ds && <>
              <div className="card">
                <div className="flex items-center justify-between mb-1"><span className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">Columns ({spec.columns.length})</span>
                  <button className="text-[11px] text-primary-600" onClick={() => setSpec((s) => ({ ...s, columns: s.columns.length ? [] : ds.columns.map((c) => c.key) }))}>{spec.columns.length ? "none" : "all"}</button></div>
                <div className="grid grid-cols-2 gap-x-2 gap-y-0.5 max-h-56 overflow-y-auto">
                  {ds.columns.map((c) => <label key={c.key} className="inline-flex items-center gap-1.5 text-xs text-gray-700"><input type="checkbox" checked={spec.columns.includes(c.key)} onChange={() => toggleCol(c.key)} />{c.label}{c.money && <span className="text-[9px] px-1 rounded bg-amber-100 text-amber-700">$</span>}</label>)}
                </div>
              </div>
              <div className="card">
                <div className="flex items-center justify-between mb-1"><span className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">Filters</span>
                  <button className="text-[11px] text-primary-600 inline-flex items-center gap-0.5" onClick={() => setSpec((s) => ({ ...s, filters: [...s.filters, { col: ds.columns[0].key, op: "eq", value: "" }] }))}><Plus className="w-3 h-3" /> add</button></div>
                {spec.filters.length === 0 && <p className="text-[11px] text-gray-400">No filters — every row in your scope.</p>}
                {spec.filters.map((f, i) => (
                  <div key={i} className="grid grid-cols-[1fr_1fr_1fr_auto] gap-1 mb-1">
                    <select className="input !py-1 text-xs" value={f.col} onChange={(e) => setF(i, { col: e.target.value })}>{ds.columns.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}</select>
                    <select className="input !py-1 text-xs" value={f.op} onChange={(e) => setF(i, { op: e.target.value })}>{m.ops.map((o) => <option key={o.op} value={o.op}>{o.label}</option>)}</select>
                    <input className="input !py-1 text-xs" value={f.value ?? ""} disabled={f.op === "is_null" || f.op === "not_null"} placeholder={f.op === "between" ? "a, b" : f.op === "in" ? "a, b, c" : "value"} onChange={(e) => setF(i, { value: e.target.value })} />
                    <button className="icon-btn text-gray-400" onClick={() => setSpec((s) => ({ ...s, filters: s.filters.filter((_, j) => j !== i) }))} aria-label="Remove filter"><X className="w-3.5 h-3.5" /></button>
                  </div>))}
              </div>
              <div className="card">
                <span className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">Group by</span>
                <div className="grid grid-cols-2 gap-x-2 gap-y-0.5 max-h-32 overflow-y-auto mb-2">
                  {ds.columns.filter((c) => !NUMERIC.has(c.type)).map((c) => <label key={c.key} className="inline-flex items-center gap-1.5 text-xs text-gray-700"><input type="checkbox" checked={spec.group_by.includes(c.key)} onChange={() => toggleGroup(c.key)} />{c.label}</label>)}
                </div>
                {spec.group_by.length > 0 && <>
                  <div className="flex items-center justify-between mb-1"><span className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">Totals (count is always added)</span>
                    <button className="text-[11px] text-primary-600 inline-flex items-center gap-0.5" onClick={() => setSpec((s) => ({ ...s, aggregates: [...s.aggregates, { fn: "sum", col: ds.columns.find((c) => NUMERIC.has(c.type))?.key }] }))}><Plus className="w-3 h-3" /> add</button></div>
                  {spec.aggregates.map((a, i) => (
                    <div key={i} className="grid grid-cols-[1fr_1fr_auto] gap-1 mb-1">
                      <select className="input !py-1 text-xs" value={a.fn} onChange={(e) => setA(i, { fn: e.target.value })}>{m.aggregates.map((x) => <option key={x} value={x}>{x}</option>)}</select>
                      <select className="input !py-1 text-xs" value={a.col || ""} disabled={a.fn === "count"} onChange={(e) => setA(i, { col: e.target.value })}>{ds.columns.filter((c) => NUMERIC.has(c.type)).map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}</select>
                      <button className="icon-btn text-gray-400" onClick={() => setSpec((s) => ({ ...s, aggregates: s.aggregates.filter((_, j) => j !== i) }))} aria-label="Remove total"><X className="w-3.5 h-3.5" /></button>
                    </div>))}
                </>}
              </div>
              <div className="card grid grid-cols-[1fr_auto_auto] gap-1 items-end">
                <label className="block"><span className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">Sort by</span>
                  <select className="input !py-1 text-xs w-full" value={spec.sort[0]?.col || ""} onChange={(e) => setSpec((s) => ({ ...s, sort: e.target.value ? [{ col: e.target.value, dir: s.sort[0]?.dir || "asc" }] : [] }))}>
                    <option value="">—</option>{outKeys.map((k) => <option key={k} value={k}>{ds.columns.find((c) => c.key === k)?.label || k}</option>)}</select></label>
                <select className="input !py-1 text-xs" value={spec.sort[0]?.dir || "asc"} onChange={(e) => setSpec((s) => ({ ...s, sort: s.sort[0] ? [{ col: s.sort[0].col, dir: e.target.value as any }] : [] }))}><option value="asc">A→Z</option><option value="desc">Z→A</option></select>
                <label className="block"><span className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">Rows</span><input className="input !py-1 text-xs w-20" type="number" min={1} max={m.max_rows} value={spec.limit} onChange={(e) => setSpec((s) => ({ ...s, limit: Number(e.target.value) || 1000 }))} /></label>
              </div>
              <div className="flex flex-wrap gap-2">
                <button className="btn-primary !py-1.5 text-xs inline-flex items-center gap-1.5" disabled={run.isPending} onClick={() => run.mutate(spec)}><Play className="w-3.5 h-3.5" /> Run</button>
                <button className="btn-secondary !py-1.5 text-xs inline-flex items-center gap-1.5" onClick={() => setSaveOpen(true)}><Save className="w-3.5 h-3.5" /> {current ? "Save changes" : "Save as…"}</button>
                <button className="btn-secondary !py-1.5 text-xs inline-flex items-center gap-1.5" disabled={exp.isPending} onClick={() => exp.mutate("csv")}><Download className="w-3.5 h-3.5" /> CSV</button>
                <button className="btn-secondary !py-1.5 text-xs inline-flex items-center gap-1.5" disabled={exp.isPending} onClick={() => exp.mutate("xlsx")}><Download className="w-3.5 h-3.5" /> XLSX</button>
              </div>
            </>}
          </div>
          <div>
            {!result ? <div className="card text-sm text-gray-500">{ds ? "Pick columns and press Run." : "Pick a dataset on the left."}</div> : (
              <>
                {result.truncated && <p className="text-xs text-amber-700 mb-2">Only the first {result.limit.toLocaleString()} rows are shown — add a filter or raise the row limit.</p>}
                <DataGrid rows={result.rows} rowKey={(_, i) => String(i)} columns={gridCols} exportName={current?.name || `${spec.dataset}-report`} minWidth={Math.max(600, gridCols.length * 140)}
                  empty="No rows match." toolbar={<span className="text-[11px] text-gray-400">as of {when(result.as_of)}</span>} />
              </>)}
          </div>
        </div>
      )}

      {tab === "saved" && (
        <>
          {saved.data && <p className="text-xs text-gray-500 mb-2">Scheduled e-mails: {saved.data.smtp.message}</p>}
          <DataGrid<RbSaved> rows={saved.data?.items || []} rowKey={(r) => r.id} minWidth={1000} empty="No saved reports yet. Build one and press Save as…" exportName="saved-reports"
            columns={[
              { key: "name", label: "Name", className: "text-gray-800 font-medium", render: (r) => <button className="text-primary-600 hover:underline inline-flex items-center gap-1" onClick={() => openSaved(r)}><FolderOpen className="w-3.5 h-3.5" />{r.name}</button>, text: (r) => r.name },
              { key: "dataset", label: "Dataset" }, { key: "owner", label: "Owner" },
              { key: "shared", label: "Visibility", render: (r) => (r.shared ? "shared" : "private"), text: (r) => (r.shared ? "shared" : "private") },
              { key: "updated_at", label: "Updated", render: (r) => when(r.updated_at), text: (r) => r.updated_at || "" },
              { key: "sched", label: "Schedule", render: (r) => r.schedule ? `${r.schedule.cadence}${r.schedule.enabled ? "" : " (off)"} · next ${when(r.schedule.next_run_at)}` : "—", text: (r) => r.schedule?.cadence || "" },
              { key: "last", label: "Last send", render: (r) => r.schedule?.last_status ? <span title={r.schedule.last_status}>{when(r.schedule.last_run_at)} · {r.schedule.last_rows} rows · {r.schedule.last_status.split(";")[0]}</span> : "—", text: (r) => r.schedule?.last_status || "" },
              { key: "actions", label: "", sortable: false, text: () => "", render: (r) => (
                <div className="flex gap-1">
                  {r.mine && <button className="btn-secondary !py-1 !px-2 text-xs inline-flex items-center gap-1" onClick={() => setSchedFor(r)}><CalendarClock className="w-3 h-3" /> Schedule</button>}
                  {r.schedule && r.mine && <button className="btn-secondary !py-1 !px-2 text-xs" disabled={sendNow.isPending} onClick={() => sendNow.mutate(r.id)}>Send now</button>}
                  {r.mine && <button className="btn-secondary !py-1 !px-2 text-xs text-red-600" onClick={() => { if (confirm(`Delete "${r.name}"?`)) del.mutate(r.id); }}><Trash2 className="w-3 h-3" /></button>}
                </div>) },
            ]} />
        </>
      )}

      {saveOpen && <SaveModal spec={spec} current={current} onClose={() => setSaveOpen(false)} onSaved={(r) => { setCurrent(r); setSaveOpen(false); setSp({ saved: r.id }); qc.invalidateQueries({ queryKey: ["rb", "saved"] }); }} />}
      {schedFor && <ScheduleModal report={schedFor} onClose={() => { setSchedFor(null); qc.invalidateQueries({ queryKey: ["rb", "saved"] }); }} />}
    </div>
  );

  function setF(i: number, patch: Partial<{ col: string; op: string; value: any }>) { setSpec((s) => ({ ...s, filters: s.filters.map((f, j) => (j === i ? { ...f, ...patch } : f)) })); }
  function setA(i: number, patch: Partial<{ fn: string; col?: string }>) { setSpec((s) => ({ ...s, aggregates: s.aggregates.map((a, j) => (j === i ? { ...a, ...patch } : a)) })); }
}

function SaveModal({ spec, current, onClose, onSaved }: { spec: RbSpec; current: RbSaved | null; onClose: () => void; onSaved: (r: RbSaved) => void }) {
  const [name, setName] = useState(current?.name || "");
  const [shared, setShared] = useState(current?.shared || false);
  const save = useMutation({ mutationFn: () => (current ? builderApi.update(current.id, name, spec, shared) : builderApi.save(name, spec, shared)),
    onSuccess: (r) => { toast.success("Saved"); onSaved(r); }, onError: (e) => toast.error(errText(e)) });
  return (
    <WideModal title={current ? "Save changes" : "Save report"} onClose={onClose}
      footer={<><button className="btn-secondary !py-1.5 text-xs" onClick={onClose}>Cancel</button><button className="btn-primary !py-1.5 text-xs" disabled={!name.trim() || save.isPending} onClick={() => save.mutate()}>Save</button></>}>
      <div className="grid sm:grid-cols-2 gap-3">
        <label className="block"><span className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">Name</span><input className="input !py-1.5 text-sm w-full" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Leave this quarter by department" /></label>
        <label className="block"><span className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">Visibility</span>
          <select className="input !py-1.5 text-sm w-full" value={shared ? "shared" : "private"} onChange={(e) => setShared(e.target.value === "shared")}><option value="private">Private — only me</option><option value="shared">Shared — everyone who may open this dataset</option></select></label>
      </div>
      <p className="text-xs text-gray-400 mt-3">A shared report runs in each reader's own scope — a manager still sees only direct reports.</p>
    </WideModal>
  );
}

/** A legacy schedule's UTC hour as "HH:MM" on ``tz``'s clock (19:00 / 20:00 for 0 UTC in Toronto). */
const utcHourAsLocal = (h: number, tz: string) => {
  try { return new Intl.DateTimeFormat("en-GB", { timeZone: tz, hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(Date.UTC(2026, 0, 5, h || 0))); }
  catch { return "09:00"; }
};

function ScheduleModal({ report, onClose }: { report: RbSaved; onClose: () => void }) {
  const me = useAuthStore((s) => s.user);
  const s0 = report.schedule;
  const [cadence, setCadence] = useState(s0?.cadence || "weekly");
  // 2026-10-25: the send time is the org's wall clock ("09:00" IST); old schedules kept a UTC hour — shown converted.
  const zone = s0?.tz || orgTimeZone();
  const [localTime, setLocalTime] = useState<string>(s0?.local_time || (s0 ? utcHourAsLocal(s0.hour_utc, zone) : "09:00"));
  const [weekday, setWeekday] = useState(s0?.weekday ?? 0);
  const [dom, setDom] = useState(s0?.day_of_month ?? 1);
  const [roles, setRoles] = useState<string[]>(s0?.roles || []);
  const [toMe, setToMe] = useState(s0 ? (s0.user_ids || []).includes(me?.id || "") : true);
  const [enabled, setEnabled] = useState(s0?.enabled ?? true);
  const [note, setNote] = useState("");
  // people added to this schedule by someone else stay on it (saving used to replace the list with just "me")
  const others = (s0?.user_ids || []).filter((u) => u !== me?.id);
  const zl = zoneLabel(zone);
  const times = Array.from({ length: 48 }, (_, i) => `${String(Math.floor(i / 2)).padStart(2, "0")}:${i % 2 ? "30" : "00"}`);
  if (!times.includes(localTime)) times.push(localTime);
  const timeLabel = (t: string) => { const [h, m] = t.split(":").map(Number); return `${String(h % 12 || 12).padStart(2, "0")}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"} ${zl}`; };
  const domErr = cadence === "monthly" && !(Number.isInteger(dom) && dom >= 1 && dom <= 28) ? "Day of month is 1 to 28 so it runs every month" : null;
  const save = useMutation({ mutationFn: () => builderApi.schedule(report.id, { cadence, local_time: localTime, tz: zone, weekday, day_of_month: dom, roles, user_ids: [...others, ...(toMe && me?.id ? [me.id] : [])], enabled }),
    onSuccess: (r) => { toast.success(`Scheduled · next ${when(r.next_run_at)}`); setNote(r.note); }, onError: (e) => toast.error(errText(e)) });
  const remove = useMutation({ mutationFn: () => builderApi.unschedule(report.id), onSuccess: () => { toast.success("Schedule removed"); onClose(); }, onError: (e) => toast.error(errText(e)) });
  return (
    <WideModal title={`Schedule: ${report.name}`} onClose={onClose}
      footer={<>{s0 && <button className="btn-secondary !py-1.5 text-xs text-red-600 mr-auto" onClick={() => remove.mutate()}>Remove schedule</button>}
        <button className="btn-secondary !py-1.5 text-xs" onClick={onClose}>Close</button><button className="btn-primary !py-1.5 text-xs" disabled={save.isPending || (!roles.length && !toMe && !others.length)} title={!roles.length && !toMe && !others.length ? "Pick at least one recipient" : ""} onClick={() => { if (domErr) { toast.error(domErr); return; } save.mutate(); }}>Save schedule</button></>}>
      <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3">
        <label className="block"><span className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">Every</span><select className="input !py-1.5 text-sm w-full" value={cadence} onChange={(e) => setCadence(e.target.value)}><option value="daily">Day</option><option value="weekly">Week</option><option value="monthly">Month</option></select></label>
        {cadence === "weekly" && <label className="block"><span className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">On</span><select className="input !py-1.5 text-sm w-full" value={weekday} onChange={(e) => setWeekday(Number(e.target.value))}>{DAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}</select></label>}
        {cadence === "monthly" && <label className="block"><span className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">Day of month</span><input className="input !py-1.5 text-sm w-full" type="number" min={1} max={28} value={dom} onChange={(e) => setDom(Number(e.target.value))} /><FieldError msg={domErr} /></label>}
        <label className="block"><span className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">Send at</span><select className="input !py-1.5 text-sm w-full" value={localTime} onChange={(e) => setLocalTime(e.target.value)}>{times.map((t) => <option key={t} value={t}>{timeLabel(t)}</option>)}</select>
          <span className="block text-[11px] text-gray-400 mt-0.5">Your company's time ({zone}); the day above is that zone's day too</span></label>
        <label className="block"><span className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">State</span><select className="input !py-1.5 text-sm w-full" value={enabled ? "on" : "off"} onChange={(e) => setEnabled(e.target.value === "on")}><option value="on">On</option><option value="off">Paused</option></select><span className="block text-[11px] text-gray-400 mt-0.5">Paused keeps it saved but stops sends</span></label>
      </div>
      <div className="mt-3"><span className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">Send to</span>
        <div className="flex flex-wrap gap-3 mt-1 text-sm">
          <label className="inline-flex items-center gap-1.5"><input type="checkbox" checked={toMe} onChange={(e) => setToMe(e.target.checked)} /> Me</label>
          {others.length > 0 && <span className="text-xs text-gray-500 self-center">+ {others.length} other person{others.length > 1 ? "s" : ""} already on it (kept)</span>}
          {ROLES.map(([k, l]) => <label key={k} className="inline-flex items-center gap-1.5"><input type="checkbox" checked={roles.includes(k)} onChange={(e) => setRoles(e.target.checked ? [...roles, k] : roles.filter((x) => x !== k))} /> {l}</label>)}
        </div>
        <span className="block text-[11px] text-gray-400 mt-1">A role sends to everyone who holds it at send time — the list follows people joining or leaving the role.</span>
      </div>
      <p className="text-xs text-gray-500 mt-3">Each run sends an in-app notification and an e-mail with the report name and a link. The rows stay behind the login — nothing is attached. Whether the e-mail was really sent shows in Notifications → Delivery log.</p>
      {s0?.last_status && <p className="text-xs text-gray-600 mt-1">Last send: {when(s0.last_run_at)} — {s0.last_status}</p>}
      {note && <p className="text-xs text-emerald-700 mt-1">{note}</p>}
    </WideModal>
  );
}
