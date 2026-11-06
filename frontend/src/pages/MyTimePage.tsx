import { useState, useMemo, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Clock, ChevronLeft, ChevronRight, Send, Save, Plus, Trash2, ArrowRight } from "lucide-react";
import toast from "react-hot-toast";
import { PageHeader, StatTile, Spinner, Empty, Badge } from "../components/ui";
import { timeApi, leaveApi, mastersApi, opsApi } from "../api/endpoints";

// Local calendar date — toISOString() is UTC and shifts IST local midnight to the previous day.
const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const mondayOf = (d: Date) => { const x = new Date(d); const dow = (x.getDay() + 6) % 7; x.setDate(x.getDate() - dow); x.setHours(0, 0, 0, 0); return x; };
const addDays = (d: Date, n: number) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const DAY_COLORS = ["#4f46e5", "#0ea5e9", "#d97706", "#059669", "#e11d48", "#3b82f6"];
const num = (v: any) => { const n = parseFloat(v); return isNaN(n) ? 0 : n; };
const clean = (n: number) => n.toFixed(2).replace(/\.?0+$/, "");
const TS_TONE: Record<string, string> = { draft: "onboarding", submitted: "on_leave", approved: "active", locked: "active", rejected: "terminated" };

type Row = { key: string; projectId: string; hours: Record<string, string> };

export default function MyTimePage() {
  const qc = useQueryClient();
  const [anchor, setAnchor] = useState(() => mondayOf(new Date()));
  const week = useMemo(() => Array.from({ length: 7 }, (_, i) => iso(addDays(anchor, i))), [anchor]);
  const start = week[0], end = week[6];
  const [rows, setRows] = useState<Row[]>([]);

  const projects = useQuery({ queryKey: ["ops", "projects"], queryFn: () => opsApi.list("projects") });
  const mine = useQuery({ queryKey: ["ta", "my-timesheet", start], queryFn: () => timeApi.myTimesheet(start, end) });
  const leave = useQuery({ queryKey: ["lv", "requests", "mine"], queryFn: () => leaveApi.requests({ scope: "mine" }), retry: false });
  const holidays = useQuery({ queryKey: ["m", "stat-holidays"], queryFn: () => mastersApi.list("stat-holidays") });

  const projList = (projects.data || []) as any[];
  const projName = (id: string) => projList.find((p) => String(p.id) === String(id))?.name || id;
  const projClient = (id: string) => projList.find((p) => String(p.id) === String(id))?.client;

  // hydrate the grid from an existing timesheet for this week
  const ts = mine.data?.timesheet;
  useEffect(() => {
    if (!ts) { setRows([]); return; }
    const byProj: Record<string, Row> = {};
    (ts.lines || []).forEach((l: any) => {
      if (!l.project_id) return;
      const r = byProj[l.project_id] || (byProj[l.project_id] = { key: l.project_id, projectId: l.project_id, hours: {} });
      r.hours[l.work_date] = clean(l.hours || 0);
    });
    setRows(Object.values(byProj));
  }, [mine.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const locked = ts && !["draft", "rejected"].includes(ts.status);

  // read-only leave + holiday overlays for the week
  const leaveByDate = useMemo(() => {
    const m: Record<string, { label: string; hours: number }> = {};
    (leave.data || []).forEach((r) => {
      if (!["approved", "pending"].includes(r.status)) return;
      week.forEach((d) => {
        if (d >= r.start_date && d <= r.end_date && new Date(d + "T00:00:00").getDay() % 6 !== 0) {
          m[d] = { label: `${r.leave_type_name}${r.half_day ? " ½" : ""}`, hours: r.half_day ? 3.75 : 7.5 };
        }
      });
    });
    return m;
  }, [leave.data, week]);
  const holByDate = useMemo(() => {
    const m: Record<string, string> = {};
    (holidays.data || []).forEach((h: any) => { if (h.holiday_date && week.includes(h.holiday_date.slice(0, 10))) m[h.holiday_date.slice(0, 10)] = h.name; });
    return m;
  }, [holidays.data, week]);
  const hasLeave = Object.keys(leaveByDate).length > 0, hasHol = Object.keys(holByDate).length > 0;

  const setHours = (rk: string, date: string, v: string) =>
    setRows((rs) => rs.map((r) => r.key === rk ? { ...r, hours: { ...r.hours, [date]: v } } : r));
  const setProject = (rk: string, pid: string) => setRows((rs) => rs.map((r) => r.key === rk ? { ...r, projectId: pid } : r));
  const addRow = () => {
    const used = new Set(rows.map((r) => r.projectId));
    const next = projList.find((p) => !used.has(String(p.id)));
    setRows((rs) => [...rs, { key: `r${Date.now()}`, projectId: next ? String(next.id) : "", hours: {} }]);
  };
  const removeRow = (rk: string) => setRows((rs) => rs.filter((r) => r.key !== rk));

  const dayWorked = (d: string) => rows.reduce((a, r) => a + num(r.hours[d]), 0);
  const rowTotal = (r: Row) => week.reduce((a, d) => a + num(r.hours[d]), 0);
  const worked = rows.reduce((a, r) => a + rowTotal(r), 0);
  const otHint = Math.max(0, +(worked - 44).toFixed(2));

  const build = (submit: boolean) => {
    const lines: any[] = [];
    rows.forEach((r) => {
      if (!r.projectId) return;
      week.forEach((d) => {
        const h = num(r.hours[d]);
        if (h > 0) lines.push({ work_date: d, project_id: r.projectId, project_name: projName(r.projectId), billable: !!projClient(r.projectId), hours: h });
      });
    });
    return { period_start: start, period_end: end, submit, lines };
  };
  const save = useMutation({
    mutationFn: (submit: boolean) => timeApi.submitEntry(build(submit)),
    onSuccess: (_r, submit) => { toast.success(submit ? "Submitted for approval" : "Draft saved"); qc.invalidateQueries({ queryKey: ["ta", "my-timesheet"] }); },
    onError: (e: any) => toast.error(e?.response?.data?.detail || "Save failed"),
  });

  const weekLabel = `${new Date(start + "T00:00:00").toLocaleDateString("en-CA", { month: "short", day: "numeric" })} – ${new Date(end + "T00:00:00").toLocaleDateString("en-CA", { month: "short", day: "numeric" })}`;
  const canSubmit = rows.some((r) => r.projectId && week.some((d) => num(r.hours[d]) > 0));

  return (
    <div>
      <PageHeader title="My Time" icon={Clock} subtitle="Log your worked hours by project — sent to your manager"
        actions={
          <div className="flex items-center gap-1.5">
            <button className="btn-secondary !p-2" onClick={() => setAnchor(addDays(anchor, -7))} aria-label="Previous week"><ChevronLeft className="w-4 h-4" /></button>
            <span className="text-sm font-medium text-gray-700 w-32 text-center tabular-nums">{weekLabel}</span>
            <button className="btn-secondary !p-2" onClick={() => setAnchor(addDays(anchor, 7))} aria-label="Next week"><ChevronRight className="w-4 h-4" /></button>
          </div>
        } />

      {mine.isError ? <div className="card"><Empty>Your login isn't linked to an employee record, so you can't submit hours. Ask HR to link your account.</Empty></div> : (
      <>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
        <StatTile label="Worked this week" value={`${clean(worked)}h`} sub={ts ? `timesheet ${ts.status}` : "not started"} accent />
        <StatTile label="Billable" value={`${clean(rows.filter((r) => projClient(r.projectId)).reduce((a, r) => a + rowTotal(r), 0))}h`} />
        <StatTile label="On leave" value={`${clean(Object.values(leaveByDate).reduce((a, l) => a + l.hours, 0))}h`} sub={hasLeave ? "pulled from Leave" : "none"} />
        <StatTile label="Status" value={ts ? <Badge tone={TS_TONE[ts.status]}>{ts.status}</Badge> : <Badge tone="onboarding">new</Badge>} />
      </div>

      <div className="card !p-0">
        <div className="px-5 py-3 border-b border-surface-border flex items-center justify-between gap-3 flex-wrap">
          <h3 className="font-display font-semibold text-gray-800">Hours by project · week of {weekLabel}</h3>
          {locked && <span className="badge-amber">Locked — already {ts!.status}</span>}
        </div>

        {mine.isLoading || projects.isLoading ? <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm border-collapse min-w-[720px]">
              <thead>
                <tr className="bg-surface text-gray-400 text-[11px] uppercase tracking-wide">
                  <th className="px-4 py-2.5 text-left font-semibold sticky left-0 bg-surface z-10 min-w-[190px]">Project / activity</th>
                  {week.map((d, i) => { const dd = new Date(d + "T00:00:00"); return <th key={d} className={`px-2 py-2.5 text-center font-semibold ${i >= 5 ? "text-gray-300" : ""}`}>{DOW[i]}<div className="text-[10px] font-normal">{dd.getDate()}</div></th>; })}
                  <th className="px-3 py-2.5 text-center font-semibold">Total</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, ri) => (
                  <tr key={r.key} className="border-t border-surface-border">
                    <td className="px-4 py-2 sticky left-0 bg-surface-card z-10">
                      <div className="flex items-center gap-2">
                        <span className="w-2.5 h-2.5 rounded-sm flex-shrink-0" style={{ background: DAY_COLORS[ri % DAY_COLORS.length] }} />
                        <select className="input !py-1.5 text-sm" value={r.projectId} disabled={!!locked} onChange={(e) => setProject(r.key, e.target.value)}>
                          <option value="">Select project…</option>
                          {projList.map((p) => <option key={p.id} value={p.id}>{p.name}{p.client ? ` · ${p.client}` : ""}</option>)}
                        </select>
                        {!locked && <button className="btn-secondary !p-1.5" onClick={() => removeRow(r.key)} aria-label="Remove"><Trash2 className="w-3.5 h-3.5 text-gray-400" /></button>}
                      </div>
                    </td>
                    {week.map((d, i) => (
                      <td key={d} className={`px-1.5 py-1.5 text-center ${i >= 5 ? "bg-surface/40" : ""}`}>
                        <input className="input !px-1 !py-1.5 w-14 text-center tabular-nums" value={r.hours[d] || ""} disabled={!!locked}
                          onChange={(e) => setHours(r.key, d, e.target.value)} placeholder="–" inputMode="decimal" />
                      </td>
                    ))}
                    <td className="px-3 py-2 text-center tabular-nums font-semibold text-gray-800">{clean(rowTotal(r)) || "–"}</td>
                  </tr>
                ))}
                {!locked && (
                  <tr className="border-t border-surface-border">
                    <td className="px-4 py-2 sticky left-0 bg-surface-card z-10" colSpan={9}>
                      <button className="btn-secondary text-xs inline-flex items-center gap-1.5" onClick={addRow}><Plus className="w-3.5 h-3.5" />Add a project</button>
                    </td>
                  </tr>
                )}
                {/* read-only leave + holiday */}
                {hasLeave && (
                  <tr className="border-t border-surface-border bg-surface/60">
                    <td className="px-4 py-2 sticky left-0 bg-surface z-10 text-gray-500"><span className="inline-flex items-center gap-2"><span className="w-2 h-2 rotate-45 border border-primary-400" />Leave <span className="text-[11px] text-gray-400">(from Leave)</span></span></td>
                    {week.map((d, i) => <td key={d} className={`px-1.5 py-2 text-center text-[11px] ${i >= 5 ? "bg-surface/40" : ""}`}>{leaveByDate[d] ? <span className="text-primary-600 font-medium">{clean(leaveByDate[d].hours)}</span> : "–"}</td>)}
                    <td></td>
                  </tr>
                )}
                {hasHol && (
                  <tr className="border-t border-surface-border bg-surface/60">
                    <td className="px-4 py-2 sticky left-0 bg-surface z-10 text-gray-500"><span className="inline-flex items-center gap-2"><span className="w-2 h-2 rotate-45 border border-accent" />Statutory holiday <span className="text-[11px] text-gray-400">(auto-paid)</span></span></td>
                    {week.map((d, i) => <td key={d} className={`px-1.5 py-2 text-center text-[10px] ${i >= 5 ? "bg-surface/40" : ""}`}>{holByDate[d] ? <span className="text-accent font-medium">{holByDate[d].split(" ")[0]}</span> : "–"}</td>)}
                    <td></td>
                  </tr>
                )}
              </tbody>
              <tfoot>
                <tr className="border-t-2 border-surface-border">
                  <td className="px-4 py-2.5 text-left font-semibold text-gray-500 sticky left-0 bg-surface-card z-10">Day total</td>
                  {week.map((d, i) => { const t = dayWorked(d) + (leaveByDate[d]?.hours || 0); return <td key={d} className={`px-2 py-2.5 text-center tabular-nums font-semibold ${i >= 5 ? "bg-surface/40 text-gray-300" : "text-gray-700"}`}>{t ? clean(t) : "–"}</td>; })}
                  <td className="px-3 py-2.5 text-center tabular-nums font-bold text-primary-600">{clean(worked)}</td>
                </tr>
              </tfoot>
            </table>
          </div>
        )}

        {!locked && (
          <div className="px-5 py-3.5 border-t border-surface-border bg-surface flex items-center justify-between gap-4 flex-wrap rounded-b-xl">
            <div className="text-xs text-gray-500 max-w-md">
              <span className="font-semibold text-gray-700">{clean(worked)}h worked.</span> {otHint > 0 ? <>≈{clean(otHint)}h likely overtime — </> : "Overtime & holiday pay are "}worked out by the provincial employment-standards rules when your timesheet is approved. Leave &amp; holidays are auto-included.
            </div>
            <div className="flex gap-2">
              <button className="btn-secondary inline-flex items-center gap-1.5" onClick={() => save.mutate(false)} disabled={save.isPending || !canSubmit}><Save className="w-4 h-4" />Save draft</button>
              <button className="btn-primary inline-flex items-center gap-1.5" onClick={() => save.mutate(true)} disabled={save.isPending || !canSubmit}><Send className="w-4 h-4" />Submit for approval</button>
            </div>
          </div>
        )}
      </div>

      <div className="card pad mt-4 !p-4">
        <div className="flex items-center gap-2 text-sm text-gray-600 flex-wrap">
          <span className="badge-green">✓ You</span><ArrowRight className="w-3.5 h-3.5 text-gray-400" />
          <span className="badge-amber">{ts?.manager_name || "Your manager"}</span><ArrowRight className="w-3.5 h-3.5 text-gray-400" />
          <span className="badge-purple">Payroll · REG / OT / STAT</span>
          <span className="text-[11px] text-gray-400 ml-1">Billable hours also roll into client invoicing.</span>
        </div>
      </div>
      </>
      )}
    </div>
  );
}
