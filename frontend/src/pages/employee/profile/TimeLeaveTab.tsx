import { useMemo } from "react";
import { Link } from "react-router-dom";
import { timeApi, leaveApi, type LeaveBalance, type LeaveRequest } from "../../../api/endpoints";
import type { TabProps } from "../EmployeeProfilePage";
import { Denied, EmptyLine, Field, Grid, Loading, Section, TD, TH, fmtDate, titleCase, tone, useTabQuery } from "./_bits";
import { monthStartISO, todayISO } from "../../../lib/datetime";   // 2026-10-25: UTC store, org-zone today


/** This month's attendance (from the time service) · leave balances per type ·
 *  the last leave requests. Each block fails on its own: a 403 shows one sentence. */
export default function TimeLeaveTab({ profile }: TabProps) {
  const e = profile.employee;
  const todayIso = todayISO();
  const monthStart = monthStartISO(todayIso);
  const monthLabel = new Date(monthStart + "T00:00:00").toLocaleDateString("en-GB", { month: "long", year: "numeric" });
  const self = e.is_self;

  const att = useTabQuery<any>(["profile-attendance", e.id, monthStart], () => timeApi.employeeAttendance(e.id, monthStart, todayIso));
  const bal = useTabQuery(["profile-leave-balances", e.id], () => leaveApi.balances(e.id));
  const reqs = useTabQuery<LeaveRequest[]>(["profile-leave-requests", e.id], () => leaveApi.requests({ employee_id: e.id }));

  const counts = useMemo(() => {
    const days: any[] = att.data?.days || [];
    const c = { present: 0, absent: 0, late: 0, on_leave: 0, holiday: 0, rest: 0 };
    for (const d of days) {
      if (d.is_holiday || d.status === "holiday") c.holiday++;
      else if (d.status === "present" || d.status === "on_shift") { c.present++; if ((d.late_minutes || 0) > 0) c.late++; }
      else if (d.status === "late") { c.present++; c.late++; }
      else if (d.status === "absent") c.absent++;
      else if (d.status === "on_leave" || d.status === "leave") c.on_leave++;
      else if (d.status === "rest_day" || d.status === "weekend") c.rest++;
    }
    return c;
  }, [att.data]);
  const recent = [...(reqs.data || [])].sort((a, b) => (b.start_date || "").localeCompare(a.start_date || "")).slice(0, 10);
  const balances: LeaveBalance[] = bal.data?.balances || [];

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <Section title={`Attendance · ${monthLabel}`} action={<Link to={self ? "/my-time" : "/attendance"} className="text-xs text-primary-600 font-medium hover:underline">{self ? "My time →" : "Attendance board →"}</Link>}>
          {att.isLoading ? <Loading /> : att.denied ? <Denied what="attendance" /> : att.failed ? <p className="text-sm text-gray-400">Time &amp; attendance is not available right now.</p>
            : !(att.data?.days || []).length ? <p className="text-sm text-gray-400">No attendance recorded this month yet.{self && <> Punch in from <Link to="/time-clock" className="text-primary-600 hover:underline">Time clock</Link>.</>}</p>
            : (
              <Grid cols={4}>
                <Field label="Days present"><span className="text-lg font-bold tabular-nums">{counts.present}</span></Field>
                <Field label="Late"><span className={`text-lg font-bold tabular-nums ${counts.late ? "text-amber-600" : ""}`}>{counts.late}</span></Field>
                <Field label="Absent"><span className={`text-lg font-bold tabular-nums ${counts.absent ? "text-red-600" : ""}`}>{counts.absent}</span></Field>
                <Field label="On leave"><span className="text-lg font-bold tabular-nums">{counts.on_leave}</span></Field>
                <Field label="Holidays">{counts.holiday}</Field>
                <Field label="Rest days">{counts.rest}</Field>
                <Field label="Hours worked"><span className="tabular-nums">{att.data.actual_total}</span> <span className="text-gray-400">/ {att.data.planned_total} planned</span></Field>
                <Field label="Days recorded">{att.data.days.length}</Field>
              </Grid>
            )}
        </Section>

        <Section title={`Leave balances${bal.data?.fiscal_year ? ` · FY ${bal.data.fiscal_year}` : ""}`} flush action={<Link to={self ? "/leave" : "/leave"} className="text-xs text-primary-600 font-medium hover:underline">Leave →</Link>}>
          {bal.isLoading ? <Loading /> : bal.denied ? <Denied what="leave balances" /> : bal.failed ? <p className="p-4 text-sm text-gray-400">Leave is not available right now.</p>
            : !balances.length ? <EmptyLine action={profile.permissions.can_hr_write ? "Allocate balances" : undefined} to="/leave">No leave balances allocated for this year.</EmptyLine>
            : (
              <table className="w-full text-xs">
                <thead className="bg-surface text-gray-400 text-[10px] uppercase"><tr>
                  <th className={TH}>Type</th><th className={`${TH} text-right`}>Entitled</th><th className={`${TH} text-right`}>Used</th><th className={`${TH} text-right`}>Pending</th><th className={`${TH} text-right`}>Left</th></tr></thead>
                <tbody>{balances.map((b) => (
                  <tr key={b.id} className="border-t border-surface-border">
                    <td className={TD}>{b.leave_type_name || b.leave_type_code}{!b.paid && <span className="text-gray-400"> (unpaid)</span>}</td>
                    <td className={`${TD} text-right tabular-nums`}>{b.entitled}</td>
                    <td className={`${TD} text-right tabular-nums`}>{b.used}</td>
                    <td className={`${TD} text-right tabular-nums`}>{b.pending || 0}</td>
                    <td className={`${TD} text-right tabular-nums font-semibold ${b.remaining <= 0 ? "text-red-600" : ""}`}>{b.remaining}</td>
                  </tr>))}</tbody>
              </table>
            )}
        </Section>
      </div>

      <Section title="Recent leave requests" flush action={<Link to={self ? "/my-requests" : "/approvals"} className="text-xs text-primary-600 font-medium hover:underline">{self ? "My requests →" : "Approvals →"}</Link>}>
        {reqs.isLoading ? <Loading /> : reqs.denied ? <Denied what="leave requests" /> : reqs.failed ? <p className="p-4 text-sm text-gray-400">Leave is not available right now.</p>
          : !recent.length ? <EmptyLine action={self ? "Apply for leave" : undefined} to="/leave">No leave requests yet.</EmptyLine>
          : (
            <table className="w-full text-xs min-w-[640px]">
              <thead className="bg-surface text-gray-400 text-[10px] uppercase"><tr>
                <th className={TH}>From</th><th className={TH}>To</th><th className={`${TH} text-right`}>Days</th><th className={TH}>Type</th><th className={TH}>Reason</th><th className={TH}>Status</th><th className={TH}>Reviewed by</th></tr></thead>
              <tbody>{recent.map((r) => (
                <tr key={r.id} className="border-t border-surface-border">
                  <td className={TD}>{fmtDate(r.start_date)}</td><td className={TD}>{fmtDate(r.end_date)}</td>
                  <td className={`${TD} text-right tabular-nums`}>{r.days}{r.half_day ? " (½)" : ""}</td>
                  <td className={TD}>{r.leave_type_name || r.leave_type_code}</td>
                  <td className={`${TD} !whitespace-normal max-w-[260px]`}>{r.reason || "—"}</td>
                  <td className={TD}><span className={tone(r.status)}>{titleCase(r.status)}</span></td>
                  <td className={TD}>{r.reviewed_by_name || "—"}</td>
                </tr>))}</tbody>
            </table>
          )}
      </Section>
    </div>
  );
}
