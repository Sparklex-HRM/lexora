import { useState, useMemo } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  CalendarClock, LogIn, LogOut, Users, Timer, Palmtree, AlertTriangle, ChevronLeft, ChevronRight,
  TrendingUp, CalendarDays, Clock, CheckCircle2, Activity, ArrowRight,
} from "lucide-react";
import {
  ResponsiveContainer, ComposedChart, Area, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, BarChart,
} from "recharts";
import { Link } from "react-router-dom";
import toast from "react-hot-toast";
import { PageHeader, Spinner, Empty } from "../components/ui";
import { timeApi, type AttendanceRow } from "../api/endpoints";
import { useUserRole, APPROVER_ROLES, TEAM_APPROVER_ROLES, HR_WRITE } from "../store/authStore";
import MyAttendance from "./employee/MyAttendance";   // 2026-10-16 employee portal
import PrintRegionButton from "../components/PrintRegionButton";   // print wrap-up 2026-10-24
import { addDaysISO, todayISO } from "../lib/datetime";   // 2026-10-25: UTC store, org-zone today
import { useCan } from "../config/permissions";   // 2026-10-26: UI gates = permission + role (custom roles)

const PRIMARY = "#4f46e5", AMBER = "#d97706", GREEN = "#059669", RED = "#e11d48", SKY = "#0ea5e9";
const shift = (iso: string, days: number) => addDaysISO(iso, days);
const fmtTime = (iso?: string | null) => iso ? new Date(iso).toLocaleTimeString("en-CA", { hour: "2-digit", minute: "2-digit" }) : "—";
const fmtDay = (iso: string) => new Date(iso + "T00:00:00").toLocaleDateString("en-CA", { weekday: "long", month: "long", day: "numeric" });
const short = (iso: string) => new Date(iso + "T00:00:00").toLocaleDateString("en-CA", { month: "short", day: "numeric" });
const initials = (n: string) => n.split(" ").map((p) => p[0]).slice(0, 2).join("").toUpperCase();

const BADGE_GRAY = "inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-600";
const STATUS: Record<string, { label: string; cls: string }> = {
  present: { label: "Present", cls: "badge-green" }, on_shift: { label: "On shift", cls: "badge-green" },
  late: { label: "Late", cls: "badge-amber" }, scheduled: { label: "Not in yet", cls: "badge-teal" },
  absent: { label: "Absent", cls: "badge-red" }, leave: { label: "On leave", cls: "badge-purple" },
  rest_day: { label: "Rest day", cls: BADGE_GRAY }, holiday: { label: "Holiday", cls: "badge-purple" },
};
const statusOf = (s: string) => STATUS[s] || { label: s, cls: BADGE_GRAY };

// A compact KPI tile with an optional trend spark / delta line.
function Kpi({ label, value, sub, icon: Icon, tone = "default", ring }:
  { label: string; value: React.ReactNode; sub?: string; icon?: any; tone?: "default" | "accent" | "amber" | "red" | "green"; ring?: number }) {
  const toneCls = { default: "text-gray-800", accent: "text-primary-600", amber: "text-amber-600", red: "text-rose-600", green: "text-emerald-600" }[tone];
  const iconCls = { default: "text-gray-300", accent: "text-primary-400", amber: "text-amber-400", red: "text-rose-400", green: "text-emerald-400" }[tone];
  return (
    <div className="card !p-4 relative overflow-hidden">
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-semibold text-gray-400 uppercase tracking-wide">{label}</span>
        {Icon && <Icon className={`w-4 h-4 ${iconCls}`} />}
      </div>
      <div className="flex items-end gap-2 mt-1.5">
        <span className={`text-2xl font-bold tabular-nums ${toneCls}`}>{value}</span>
        {ring !== undefined && (
          <div className="mb-1 flex-1 h-1.5 rounded-full bg-gray-100 overflow-hidden max-w-[70px]">
            <div className="h-full rounded-full" style={{ width: `${Math.min(100, Math.max(0, ring))}%`, background: ring >= 90 ? GREEN : ring >= 75 ? AMBER : RED }} />
          </div>
        )}
      </div>
      {sub && <div className="text-xs text-gray-500 mt-1 truncate">{sub}</div>}
    </div>
  );
}

const FILTERS = [
  { k: "", label: "All" }, { k: "on", label: "On shift" }, { k: "late", label: "Late" },
  { k: "out", label: "Clocked out" }, { k: "absent", label: "Absent" }, { k: "off", label: "Leave / rest" },
] as const;

/** /attendance — an employee (and the accountant, who approves no one's attendance and is refused the
 *  org analytics since 2026-10-21) sees their own month; approvers keep the cockpit (unchanged). */
export default function AttendancePage() {
  // a custom role carrying attendance.view and acting as an approver gets the cockpit too (2026-10-26)
  const role = useUserRole() || "";
  const cockpit = useCan("attendance.view", TEAM_APPROVER_ROLES);
  return !cockpit && (role === "employee" || role === "accountant") ? <MyAttendance /> : <AttendanceCockpit />;
}

function AttendanceCockpit() {
  const qc = useQueryClient();
  const [day, setDay] = useState(todayISO());
  const [days, setDays] = useState(28);
  const [filter, setFilter] = useState("");
  const [statusF, setStatusF] = useState("");
  const isToday = day === todayISO();
  const end = todayISO(), start = shift(end, -(days - 1));

  const board = useQuery({ queryKey: ["ta", "board", day], queryFn: () => timeApi.board(day) });
  const analytics = useQuery({ queryKey: ["ta", "analytics", start, end], queryFn: () => timeApi.analytics(start, end) });
  const canApprove = useCan("attendance.approve", TEAM_APPROVER_ROLES);
  const hrWrite = useCan(["attendance.create", "attendance.edit"], HR_WRITE);
  // the approvals queue belongs to approvers; an employee would only get a 403
  const approvals = useQuery({ queryKey: ["ta", "approvals"], queryFn: () => timeApi.approvals(),
    enabled: canApprove });
  const schedules = useQuery({ queryKey: ["ta", "schedules"], queryFn: () => timeApi.schedules() });

  const punch = useMutation({
    mutationFn: (v: { employee_id: string; direction: string }) => timeApi.punch({ ...v, source: "admin" }),
    onSuccess: (_r, v) => { toast.success(v.direction === "in" ? "Clocked in" : "Clocked out"); qc.invalidateQueries({ queryKey: ["ta", "board"] }); },
    onError: () => toast.error("Punch failed"),
  });
  const seed = useMutation({ mutationFn: () => timeApi.seedSchedule(), onSuccess: () => { toast.success("Standard 9–5 schedule created"); qc.invalidateQueries({ queryKey: ["ta"] }); } });

  const rows = useMemo(() => {
    let list = board.data?.rows || [];
    const q = filter.trim().toLowerCase();
    if (q) list = list.filter((r) => r.name.toLowerCase().includes(q) || (r.employee_number || "").toLowerCase().includes(q));
    if (statusF) list = list.filter((r) => {
      const clockedIn = r.status === "on_shift" || (!!r.first_in && !r.last_out);
      if (statusF === "on") return clockedIn && r.status !== "late";
      if (statusF === "late") return r.status === "late";
      if (statusF === "out") return !!r.first_in && !!r.last_out;
      if (statusF === "absent") return r.status === "absent" || r.status === "scheduled";
      if (statusF === "off") return ["leave", "rest_day", "holiday"].includes(r.status);
      return true;
    });
    return list;
  }, [board.data, filter, statusF]);

  const s = board.data?.summary;
  const a = analytics.data;
  const ab = a?.absenteeism;
  const attendanceRate = ab ? Math.max(0, 100 - ab.rate_pct) : undefined;
  const onShiftNow = s ? s.on_shift : 0;                       // currently clocked in
  const attendedToday = s ? s.on_shift + s.present + s.late : 0;  // came in at all today
  const daily = (a?.daily || []).map((d) => ({ ...d, label: short(d.date) }));
  const dept = a?.by_department || [];
  // Only HR may create work schedules (server: /time/schedules writes are HR's) — no dead button for others.
  const noSchedule = hrWrite && !schedules.isLoading && (schedules.data || []).length === 0;
  const pending = approvals.data?.pending_total || 0;

  return (
    <div>
      <PageHeader title="Time & Attendance" icon={CalendarClock} subtitle="Live workforce cockpit — who's in, trends, overtime & exceptions"
        actions={
          <div className="flex items-center gap-2 flex-wrap">
            <select className="input !py-1.5 !w-auto text-sm" value={days} onChange={(e) => setDays(+e.target.value)}>
              <option value={7}>Last 7 days</option><option value={14}>Last 14 days</option>
              <option value={28}>Last 28 days</option><option value={90}>Last 90 days</option>
            </select>
            {pending > 0 && <Link to="/approvals" className="btn-secondary text-sm inline-flex items-center gap-1.5"><CheckCircle2 className="w-4 h-4" />{pending} to approve</Link>}
          </div>
        } />

      {noSchedule && (
        <div className="card mb-4 flex items-center justify-between gap-4 flex-wrap border-l-4 !border-l-primary-500">
          <div className="text-sm text-gray-600">No work schedule defined yet — planned hours fall back to standard hours ÷ 5 (Mon–Fri). Add the standard pattern to drive lateness &amp; rest-day detection.</div>
          <button className="btn-primary text-sm" onClick={() => seed.mutate()} disabled={seed.isPending}>Create standard 9–5</button>
        </div>
      )}

      {/* KPI strip — live board + period analytics */}
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3 mb-4">
        <Kpi label="On shift now" value={s ? onShiftNow : "—"} sub={s ? `${attendedToday} in today · ${s.total} scheduled` : ""} icon={Activity} tone="accent" />
        <Kpi label="Attendance rate" value={attendanceRate !== undefined ? `${attendanceRate.toFixed(0)}%` : "—"} sub={ab ? `${ab.scheduled_days - ab.absent_days}/${ab.scheduled_days} days` : `${days}-day`} icon={Users} tone="green" ring={attendanceRate} />
        <Kpi label="Absenteeism" value={ab ? `${ab.rate_pct}%` : "—"} sub={ab ? `${ab.absent_days} absent days` : ""} icon={CalendarDays} tone={ab && ab.rate_pct > 8 ? "red" : "default"} />
        <Kpi label="Punctuality" value={ab ? `${(100 - ab.late_rate_pct).toFixed(0)}%` : "—"} sub={ab ? `${ab.late_days} late · avg ${ab.avg_late_min}m` : ""} icon={Timer} tone={ab && ab.late_rate_pct > 15 ? "amber" : "default"} />
        <Kpi label="Overtime" value={a ? `${a.overtime.ot_hours}h` : "—"} sub={a ? `${a.overtime.employees_with_ot} ppl${a.overtime.ot2_hours ? ` · ${a.overtime.ot2_hours}h @2×` : ""}` : ""} icon={TrendingUp} tone={a && a.anomalies.count ? "amber" : "default"} />
        <Kpi label="Avg daily hours" value={a ? `${a.headline.avg_daily_hours}h` : "—"} sub={a ? `${a.headline.total_hours}h total` : ""} icon={Clock} />
      </div>

      {/* Charts — trend + department */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4 mb-4">
        <div className="card xl:col-span-2">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-display font-semibold text-gray-800">Attendance &amp; overtime trend</h3>
            <span className="text-[11px] text-gray-400">{short(start)} – {short(end)}</span>
          </div>
          {analytics.isLoading ? <div className="grid place-items-center h-[260px]"><Spinner className="h-7 w-7" /></div>
            : !daily.length ? <Empty>No attendance in this window.</Empty> : (
            <ResponsiveContainer width="100%" height={260}>
              <ComposedChart data={daily} margin={{ top: 6, right: 8, left: -18, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.2)" />
                <XAxis dataKey="label" tick={{ fontSize: 11, fill: "#8890ad" }} interval="preserveStartEnd" />
                <YAxis tick={{ fontSize: 11, fill: "#8890ad" }} />
                <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid #e3e6f0" }} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Area type="monotone" dataKey="present" name="Present" stroke={PRIMARY} fill={PRIMARY} fillOpacity={0.14} strokeWidth={2} />
                <Bar dataKey="ot" name="OT hours" fill={AMBER} radius={[3, 3, 0, 0]} barSize={13} />
                <Bar dataKey="late" name="Late" fill={RED} radius={[3, 3, 0, 0]} barSize={9} />
              </ComposedChart>
            </ResponsiveContainer>
          )}
        </div>
        <div className="card">
          <h3 className="font-display font-semibold text-gray-800 mb-3">Hours by department</h3>
          {analytics.isLoading ? <div className="grid place-items-center h-[240px]"><Spinner className="h-6 w-6" /></div>
            : !dept.length ? <Empty>No department data.</Empty> : (
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={dept} layout="vertical" margin={{ top: 4, right: 12, left: 4, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.2)" horizontal={false} />
                <XAxis type="number" tick={{ fontSize: 11, fill: "#8890ad" }} />
                <YAxis type="category" dataKey="department" width={84} tick={{ fontSize: 11, fill: "#535b78" }} />
                <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid #e3e6f0" }} />
                <Bar dataKey="hours" name="Hours" fill={SKY} radius={[0, 4, 4, 0]} barSize={12} />
                <Bar dataKey="ot_hours" name="OT" fill={AMBER} radius={[0, 4, 4, 0]} barSize={12} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>

      {/* Live board + side rail */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <div className="card !p-0 xl:col-span-2">
          <div className="px-5 py-3 border-b border-surface-border flex items-center justify-between gap-3 flex-wrap">
            <div className="flex items-center gap-2 flex-wrap">
              <h3 className="font-display font-semibold text-gray-800">Who's in</h3>
              <div className="flex items-center gap-1 text-[11px] text-gray-400">
                <button className="btn-secondary !p-1.5" onClick={() => setDay(shift(day, -1))} aria-label="Previous day"><ChevronLeft className="w-3.5 h-3.5" /></button>
                <span className="tabular-nums min-w-[92px] text-center text-gray-600">{fmtDay(day).split(",")[0]} · {short(day)}</span>
                <button className="btn-secondary !p-1.5 disabled:opacity-30" disabled={isToday} onClick={() => setDay(shift(day, 1))} aria-label="Next day"><ChevronRight className="w-3.5 h-3.5" /></button>
                {isToday && <span className="badge-green ml-1">Live</span>}
              </div>
            </div>
            <span className="flex items-center gap-2"><input className="input !py-1.5 !w-48 max-w-full text-sm" placeholder="Search employee…" value={filter} onChange={(e) => setFilter(e.target.value)} />
            <PrintRegionButton title="Print the attendance board shown (A4)" testId="attendance-board-print" /></span>
          </div>
          <div className="px-5 py-2 border-b border-surface-border flex items-center gap-1.5 flex-wrap">
            {FILTERS.map((f) => (
              <button key={f.k} onClick={() => setStatusF(f.k)}
                className={`text-xs px-2.5 py-1 rounded-full font-medium transition ${statusF === f.k ? "bg-primary-600 text-white" : "bg-gray-100 text-gray-500 hover:bg-gray-200"}`}>{f.label}</button>
            ))}
            <span className="text-[11px] text-gray-400 ml-auto">{rows.length} shown</span>
          </div>
          {board.isLoading ? <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div>
            : board.isError ? <Empty>Couldn't load the attendance board.</Empty>
            : !rows.length ? <Empty>No employees match.</Empty>
            : (
              <div className="overflow-x-auto max-h-[560px] overflow-y-auto">
                <table className="w-full text-sm min-w-[640px]">
                  <thead className="bg-surface text-gray-400 text-left text-[11px] uppercase tracking-wide sticky top-0 z-10">
                    <tr>
                      <th className="px-5 py-2.5 font-semibold">Employee</th>
                      <th className="px-3 py-2.5 font-semibold">Plan</th><th className="px-3 py-2.5 font-semibold">Actual</th>
                      <th className="px-3 py-2.5 font-semibold">In</th><th className="px-3 py-2.5 font-semibold">Out</th>
                      <th className="px-3 py-2.5 font-semibold">Status</th><th className="px-4 py-2.5 font-semibold text-right">Clock</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r: AttendanceRow) => {
                      const st = statusOf(r.status);
                      const clockedIn = r.status === "on_shift" || (r.status === "late" && !r.last_out) || (!!r.first_in && !r.last_out);
                      const canPunch = isToday && !["rest_day", "holiday", "leave"].includes(r.status);
                      return (
                        <tr key={r.employee_id} className="border-t border-surface-border hover:bg-surface/50">
                          <td className="px-5 py-2">
                            <div className="flex items-center gap-2.5">
                              <div className="w-8 h-8 rounded-lg bg-primary-50 text-primary-600 grid place-items-center text-[11px] font-bold flex-shrink-0">{initials(r.name)}</div>
                              <div className="min-w-0">
                                <div className="font-medium text-gray-800 truncate">{r.name}</div>
                                <div className="text-[11px] text-gray-400 truncate">{r.employee_number || "—"} · {r.department || r.province || "—"}</div>
                              </div>
                            </div>
                          </td>
                          <td className="px-3 py-2 tabular-nums text-gray-500">{r.planned_hours ? `${r.planned_hours}h` : "—"}</td>
                          <td className="px-3 py-2 tabular-nums font-medium text-gray-800">{r.actual_hours ? `${r.actual_hours}h` : "—"}</td>
                          <td className="px-3 py-2 tabular-nums text-gray-600">{fmtTime(r.first_in)}{r.late_minutes > 0 && <span className="text-amber-600 text-[11px] ml-1">+{r.late_minutes}m</span>}</td>
                          <td className="px-3 py-2 tabular-nums text-gray-600">{fmtTime(r.last_out)}</td>
                          <td className="px-3 py-2"><span className={st.cls}>{st.label}</span></td>
                          <td className="px-4 py-2 text-right">
                            {canPunch ? (clockedIn
                              ? <button className="btn-secondary !py-1 !px-2.5 text-xs inline-flex items-center gap-1 text-red-600" onClick={() => punch.mutate({ employee_id: r.employee_id, direction: "out" })} disabled={punch.isPending}><LogOut className="w-3.5 h-3.5" />Out</button>
                              : <button className="btn-secondary !py-1 !px-2.5 text-xs inline-flex items-center gap-1 text-primary-600" onClick={() => punch.mutate({ employee_id: r.employee_id, direction: "in" })} disabled={punch.isPending}><LogIn className="w-3.5 h-3.5" />In</button>
                            ) : <span className="text-[11px] text-gray-300">—</span>}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
        </div>

        {/* Side rail — exceptions + upcoming */}
        <div className="space-y-4">
          <div className="card !p-0">
            <div className="px-5 py-3 border-b border-surface-border flex items-center justify-between">
              <h3 className="font-display font-semibold text-gray-800">Overtime watch</h3>
              {a && a.anomalies.count > 0 && <span className="badge-amber inline-flex items-center gap-1"><AlertTriangle className="w-3 h-3" />{a.anomalies.count} spike{a.anomalies.count > 1 ? "s" : ""}</span>}
            </div>
            {!a?.overtime.top.length ? <div className="px-5 py-6"><Empty>No overtime in this window.</Empty></div> : (
              <div className="divide-y divide-surface-border max-h-[240px] overflow-y-auto">
                {a.overtime.top.map((r) => (
                  <div key={r.employee_id} className="flex items-center justify-between px-5 py-2.5">
                    <div className="min-w-0"><div className="font-medium text-gray-800 text-sm truncate">{r.name}</div><div className="text-[11px] text-gray-400">{r.department || "—"}</div></div>
                    <div className="flex items-center gap-2">{r.spike && <span className="badge-amber text-[10px]">spike</span>}<span className="tabular-nums font-semibold text-gray-800">{r.ot_hours}h</span></div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {a && a.upcoming_stat.count > 0 && (
            <div className="card">
              <h3 className="font-display font-semibold text-gray-800 mb-2 text-sm">Upcoming statutory holidays</h3>
              <div className="space-y-1.5">
                {a.upcoming_stat.holidays.slice(0, 5).map((hh) => (
                  <div key={hh.date + hh.name} className="flex items-center justify-between text-sm">
                    <span className="text-gray-700 truncate inline-flex items-center gap-1.5"><CalendarDays className="w-3.5 h-3.5 text-primary-400" />{hh.name}</span>
                    <span className="text-gray-400 text-xs tabular-nums">{short(hh.date)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          <Link to="/attendance-analytics" className="card flex items-center justify-between hover:border-primary-300 transition group">
            <div><div className="font-display font-semibold text-gray-800 text-sm">Deep analytics</div><div className="text-[11px] text-gray-400">Full absenteeism, punctuality &amp; OT trends</div></div>
            <ArrowRight className="w-4 h-4 text-gray-300 group-hover:text-primary-500 group-hover:translate-x-0.5 transition" />
          </Link>
        </div>
      </div>

      <p className="text-[11px] text-gray-400 mt-4">Supervisor cockpit — clock employees in/out, or correct via a regularization. Overtime &amp; stat-holiday pay roll into the pay run through approved timesheets. Provincial ESA figures are interim design references pending legal verification.</p>
    </div>
  );
}
