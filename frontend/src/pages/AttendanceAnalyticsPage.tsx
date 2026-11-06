import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { BarChart3, Users, Clock, TrendingUp, AlertTriangle, CalendarDays, Timer } from "lucide-react";
import {
  ResponsiveContainer, ComposedChart, Area, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, BarChart,
} from "recharts";
import { PageHeader, StatTile, Spinner, Empty } from "../components/ui";
import { timeApi } from "../api/endpoints";
import { addDaysISO, todayISO } from "../lib/datetime";   // 2026-10-25: UTC store, org-zone today

const PRIMARY = "#4f46e5", AMBER = "#d97706", GREEN = "#059669", RED = "#e11d48", SKY = "#0ea5e9";
const shift = (iso: string, d: number) => addDaysISO(iso, d);
const short = (iso: string) => new Date(iso + "T00:00:00").toLocaleDateString("en-CA", { month: "short", day: "numeric" });

export default function AttendanceAnalyticsPage() {
  const [days, setDays] = useState(28);
  const end = todayISO();
  const start = shift(end, -(days - 1));
  const { data, isLoading } = useQuery({ queryKey: ["ta", "analytics", start, end], queryFn: () => timeApi.analytics(start, end) });

  const daily = (data?.daily || []).map((d) => ({ ...d, label: short(d.date) }));
  const dept = data?.by_department || [];
  const ab = data?.absenteeism;

  return (
    <div>
      <PageHeader title="Attendance Analytics" icon={BarChart3} subtitle="Absenteeism, punctuality & overtime trends"
        actions={
          <select className="input !py-1.5 !w-auto text-sm" value={days} onChange={(e) => setDays(+e.target.value)}>
            <option value={14}>Last 14 days</option><option value={28}>Last 28 days</option><option value={90}>Last 90 days</option>
          </select>
        } />

      {isLoading ? <div className="grid place-items-center py-24"><Spinner className="h-8 w-8" /></div>
        : !data ? <Empty>No analytics available.</Empty>
        : (
          <>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
              <StatTile label="Employees tracked" value={data.headline.employees_tracked} sub={`${data.headline.attendance_days} attendance days`} icon={Users} accent />
              <StatTile label="Avg daily hours" value={`${data.headline.avg_daily_hours}h`} sub={`${data.headline.total_hours}h total`} icon={Clock} />
              <StatTile label="Absenteeism" value={`${ab?.rate_pct}%`} sub={`${ab?.absent_days}/${ab?.scheduled_days} scheduled`} icon={CalendarDays} />
              <StatTile label="Overtime" value={`${data.overtime.ot_hours}h`} sub={`${data.overtime.employees_with_ot} employees${data.overtime.ot2_hours ? ` · ${data.overtime.ot2_hours}h @2×` : ""}`} icon={TrendingUp} />
            </div>

            {/* Punctuality strip */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
              <StatTile label="Late arrivals" value={ab?.late_days || 0} sub={`${ab?.late_rate_pct}% of days`} icon={Timer} />
              <StatTile label="Avg lateness" value={`${ab?.avg_late_min || 0}m`} />
              <StatTile label="OT anomalies" value={data.anomalies.count} sub="spikes flagged" icon={AlertTriangle} />
              <StatTile label="Upcoming stat" value={data.upcoming_stat.count} sub="next 21 days" icon={CalendarDays} />
            </div>

            {/* Daily attendance + OT trend */}
            <div className="card mb-4">
              <h3 className="font-display font-semibold text-gray-800 mb-3">Daily attendance &amp; overtime</h3>
              {!daily.length ? <Empty>No attendance in this window.</Empty> : (
                <ResponsiveContainer width="100%" height={260}>
                  <ComposedChart data={daily} margin={{ top: 6, right: 8, left: -18, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.2)" />
                    <XAxis dataKey="label" tick={{ fontSize: 11, fill: "#8890ad" }} interval="preserveStartEnd" />
                    <YAxis tick={{ fontSize: 11, fill: "#8890ad" }} />
                    <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid #e3e6f0" }} />
                    <Legend wrapperStyle={{ fontSize: 12 }} />
                    <Area type="monotone" dataKey="present" name="Present" stroke={PRIMARY} fill={PRIMARY} fillOpacity={0.14} strokeWidth={2} />
                    <Bar dataKey="ot" name="OT hours" fill={AMBER} radius={[3, 3, 0, 0]} barSize={14} />
                    <Bar dataKey="late" name="Late" fill={RED} radius={[3, 3, 0, 0]} barSize={10} />
                  </ComposedChart>
                </ResponsiveContainer>
              )}
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              {/* By department */}
              <div className="card">
                <h3 className="font-display font-semibold text-gray-800 mb-3">Hours by department</h3>
                {!dept.length ? <Empty>No department data.</Empty> : (
                  <ResponsiveContainer width="100%" height={240}>
                    <BarChart data={dept} layout="vertical" margin={{ top: 4, right: 12, left: 8, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.2)" horizontal={false} />
                      <XAxis type="number" tick={{ fontSize: 11, fill: "#8890ad" }} />
                      <YAxis type="category" dataKey="department" width={92} tick={{ fontSize: 11, fill: "#535b78" }} />
                      <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid #e3e6f0" }} />
                      <Legend wrapperStyle={{ fontSize: 12 }} />
                      <Bar dataKey="hours" name="Hours" fill={SKY} radius={[0, 4, 4, 0]} barSize={13} />
                      <Bar dataKey="ot_hours" name="OT" fill={AMBER} radius={[0, 4, 4, 0]} barSize={13} />
                    </BarChart>
                  </ResponsiveContainer>
                )}
              </div>

              {/* Top OT + anomalies */}
              <div className="card !p-0">
                <div className="px-5 py-3 border-b border-surface-border flex items-center justify-between">
                  <h3 className="font-display font-semibold text-gray-800">Top overtime</h3>
                  {data.anomalies.count > 0 && <span className="badge-amber inline-flex items-center gap-1"><AlertTriangle className="w-3 h-3" />{data.anomalies.count} spike{data.anomalies.count > 1 ? "s" : ""}</span>}
                </div>
                {!data.overtime.top.length ? <Empty>No overtime in this window.</Empty> : (
                  <div className="divide-y divide-surface-border">
                    {data.overtime.top.map((r) => (
                      <div key={r.employee_id} className="flex items-center justify-between px-5 py-2.5">
                        <div className="min-w-0">
                          <div className="font-medium text-gray-800 text-sm truncate">{r.name}</div>
                          <div className="text-[11px] text-gray-400">{r.department || "—"}</div>
                        </div>
                        <div className="flex items-center gap-2">
                          {r.spike && <span className="badge-amber text-[10px]">spike</span>}
                          <span className="tabular-nums font-semibold text-gray-800">{r.ot_hours}h</span>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>

            {/* Upcoming stat holidays */}
            {data.upcoming_stat.count > 0 && (
              <div className="card mt-4">
                <h3 className="font-display font-semibold text-gray-800 mb-2">Upcoming statutory holidays</h3>
                <div className="flex flex-wrap gap-2">
                  {data.upcoming_stat.holidays.map((hh) => (
                    <span key={hh.date + hh.name} className="inline-flex items-center gap-1.5 rounded-lg bg-primary-50 text-primary-700 px-3 py-1.5 text-sm">
                      <CalendarDays className="w-3.5 h-3.5" />{hh.name} · {short(hh.date)}
                    </span>
                  ))}
                </div>
                <p className="text-[11px] text-gray-400 mt-2">Stat-holiday pay eligibility runs for ~{data.upcoming_stat.active_employees} active employees when timesheets are generated.</p>
              </div>
            )}
            <p className="text-[11px] text-gray-400 mt-4">Overtime figures come from the provincial employment-standards rules; absenteeism compares attended vs scheduled workdays (excluding weekly offs &amp; holidays). This is the same data the AI watchdogs poll.</p>
          </>
        )}
    </div>
  );
}
