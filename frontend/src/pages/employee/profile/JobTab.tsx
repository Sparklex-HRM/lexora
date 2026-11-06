import { Link } from "react-router-dom";
import { ChevronRight } from "lucide-react";
import { REGION_NAMES } from "../../../api/regions";
import type { TabProps } from "../EmployeeProfilePage";
import { Avatar, EmptyLine, Field, Grid, Section, TD, TH, fmtDate, titleCase, tone } from "./_bits";

/** Current position · reporting line (manager chain + direct reports) · probation ·
 *  career-change history (promotion / transfer / re-designation …). */
export default function JobTab({ profile }: TabProps) {
  const e = profile.employee;
  const career = profile.career;
  const changes = profile.career_changes;
  const region = REGION_NAMES.CA[e.province_of_employment] || e.province_of_employment;
  const canCareer = profile.permissions.can_hr_write;          // HR only — not the pay office (2026-10-21)
  const prob = career?.probation_history || [];
  const link = (b: { employee_number: string }) => `/employees/${encodeURIComponent(b.employee_number)}?tab=job`;

  return (
    <div className="space-y-4">
      <Section title="Current position" action={canCareer && e.status !== "terminated" && <Link to="/career-changes" className="text-xs text-primary-600 font-medium hover:underline">Promote / transfer →</Link>}>
        <Grid cols={4}>
          <Field label="Designation">{e.job_title}</Field>
          <Field label="Department">{e.department}</Field>
          <Field label="Grade">{career?.grade || e.grade}</Field>
          <Field label="Employment type">{titleCase(e.employment_type)}</Field>
          <Field label="Division">{e.division}</Field>
          <Field label="Cost centre">{e.cost_center}</Field>
          <Field label="Work location">{e.work_location}</Field>
          <Field label="Branch">{e.branch_name}</Field>
          <Field label="Province of employment">{region}</Field>
          <Field label="Hours per week">{e.standard_hours_per_week || null}</Field>
          {e.fte_percent != null && <Field label="FTE">{`${e.fte_percent}%`}</Field>}
          <Field label="Date of joining">{fmtDate(e.hire_date)}</Field>
          {e.status === "terminated" && <Field label="Last working day">{fmtDate(e.termination_date)}</Field>}
          <Field label="Tenure">{profile.tenure.text}</Field>
          <Field label="Union">{e.is_union ? `Yes${e.union_local ? ` · ${e.union_local}` : ""}` : "No"}</Field>
          {e.overtime_eligible != null && <Field label="Overtime eligible">{e.overtime_eligible ? "Yes" : "No"}</Field>}
        </Grid>
      </Section>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <Section title="Reporting line">
          <div className="text-[10px] font-semibold text-gray-400 uppercase tracking-wide mb-1.5">Reports up to</div>
          {profile.manager_chain.length ? (
            <div className="flex flex-wrap items-center gap-1 text-sm">
              <span className="font-medium text-gray-800">{e.first_name} {e.last_name}</span>
              {profile.manager_chain.map((m) => (
                <span key={m.id} className="flex items-center gap-1">
                  <ChevronRight className="w-3.5 h-3.5 text-gray-300" />
                  <Link to={link(m)} className="text-primary-600 hover:underline">{m.name}</Link>
                  <span className="text-xs text-gray-400">{m.job_title || m.employee_number}</span>
                </span>
              ))}
            </div>
          ) : <p className="text-sm text-gray-400">{e.manager_name ? `"${e.manager_name}" is typed on the record but not linked to a person.` : "No manager set."}{profile.permissions.can_edit && " Use Edit → Job → Reports to."}</p>}

          <div className="text-[10px] font-semibold text-gray-400 uppercase tracking-wide mt-4 mb-1.5">Direct reports ({profile.reports.length})</div>
          {profile.reports.length ? (
            <ul className="divide-y divide-surface-border/60">
              {profile.reports.map((r) => (
                <li key={r.id} className="py-1.5 flex items-center gap-2.5">
                  <Avatar size="sm" seed={r.employee_number} first={r.name.split(" ")[0]} last={r.name.split(" ").slice(-1)[0]} />
                  <div className="min-w-0 flex-1">
                    <Link to={link(r)} className="text-sm font-medium text-gray-800 hover:text-primary-600">{r.name}</Link>
                    <div className="text-xs text-gray-400 truncate">{r.employee_number} · {r.job_title || "—"} · {r.department || "—"}</div>
                  </div>
                  <span className={tone(r.status)}>{titleCase(r.status)}</span>
                </li>
              ))}
            </ul>
          ) : <p className="text-sm text-gray-400">Nobody reports to this employee.</p>}
        </Section>

        <Section title="Probation and confirmation">
          {career ? (<>
            <Grid cols={3}>
              <Field label="Employment status">{career.employment_status ? <span className={career.employment_status === "confirmed" ? "badge-green" : "badge-amber"}>{titleCase(career.employment_status)}</span> : "Not set"}</Field>
              <Field label="Probation ends">{fmtDate(career.probation_end_date || e.probation_end_date)}</Field>
              <Field label="Confirmed on">{fmtDate(career.confirmation_date)}</Field>
            </Grid>
            {prob.length > 0 && (
              <div className="overflow-x-auto mt-3 -mx-4 border-t border-surface-border">
                <table className="w-full text-xs">
                  <thead className="bg-surface text-gray-400 text-[10px] uppercase"><tr><th className={TH}>When</th><th className={TH}>Step</th><th className={TH}>Details</th><th className={TH}>By</th></tr></thead>
                  <tbody>{prob.map((p: any, i: number) => (
                    <tr key={i} className="border-t border-surface-border">
                      <td className={TD}>{fmtDate(p.at)}</td>
                      <td className={TD}>{titleCase(p.action)}{p.recommendation ? `: ${titleCase(p.recommendation)}` : ""}</td>
                      <td className={`${TD} !whitespace-normal`}>{p.confirmation_date ? `Confirmed from ${fmtDate(p.confirmation_date)}` : p.new_end_date ? `End date ${fmtDate(p.old_end_date)} → ${fmtDate(p.new_end_date)}` : p.reason || p.exit_handover || "—"}</td>
                      <td className={TD}>{p.actor || "—"}</td>
                    </tr>))}</tbody>
                </table>
              </div>
            )}
            {!prob.length && <p className="text-xs text-gray-400 mt-3">No probation steps recorded yet.{canCareer && <> Manage it in <Link to="/probation" className="text-primary-600 hover:underline">Probation</Link>.</>}</p>}
          </>) : <p className="text-sm text-gray-400">Probation details are visible to HR and the employee.</p>}
        </Section>
      </div>

      <Section title="Career changes" flush action={canCareer && <Link to="/career-changes" className="text-xs text-primary-600 font-medium hover:underline">All career changes →</Link>}>
        {changes === null ? <p className="p-4 text-sm text-gray-400">Career history is visible to HR and the employee.</p>
          : !changes.length ? <EmptyLine action={canCareer ? "Record a promotion or transfer" : undefined} to="/career-changes">No promotions, transfers or re-designations recorded yet.</EmptyLine>
          : (
            <table className="w-full text-xs min-w-[720px]">
              <thead className="bg-surface text-gray-400 text-[10px] uppercase"><tr>
                <th className={TH}>Effective</th><th className={TH}>Type</th><th className={TH}>Change (old → new)</th><th className={TH}>Reason</th><th className={TH}>Status</th><th className={TH}>By</th><th className={TH}>Approved by</th></tr></thead>
              <tbody>{changes.map((c: any) => (
                <tr key={c.id} className="border-t border-surface-border">
                  <td className={TD}>{fmtDate(c.effective_date)}</td><td className={TD}>{c.type_label}</td>
                  <td className={`${TD} !whitespace-normal`}>{c.summary || "—"}</td><td className={TD}>{c.reason || "—"}</td>
                  <td className={TD}><span className={tone(c.status)}>{c.status}</span></td>
                  <td className={TD}>{c.created_by || "—"}</td><td className={TD}>{c.decided_by || "—"}</td>
                </tr>))}</tbody>
            </table>
          )}
      </Section>
    </div>
  );
}
