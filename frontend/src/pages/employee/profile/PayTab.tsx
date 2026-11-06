import { useState } from "react";
import { Link } from "react-router-dom";
import { payrollApi, freqLabel } from "../../../api/endpoints";
import { revisionsApi, cur, pct, REASONS } from "../../../api/lifecycle";
import { SalaryTimeline } from "../../../components/lifecycle/EmployeeLifecyclePanel";
import PayslipModal from "../../../components/PayslipModal";
import { PAYROLL_ACCESS, useAuthStore } from "../../../store/authStore";
import type { TabProps } from "../EmployeeProfilePage";
import { Denied, EmptyLine, Field, Grid, Loading, Section, TD, TH, fmtDate, titleCase, tone, useTabQuery } from "./_bits";
import { can } from "../../../config/permissions";   // 2026-10-26: UI gates = permission + role (custom roles)

/** Current pay · salary history · revisions · payslips · bank (masked). */
export default function PayTab({ profile }: TabProps) {
  const e = profile.employee;
  const comp = profile.compensation;
  const currency = comp?.current?.currency || "CAD";
  const hourly = (comp?.current?.pay_type || e.pay_type) === "hourly";
  const annual = Number(comp?.current?.annual_amount ?? e.annual_salary) || 0;
  const rate = Number(comp?.current?.hourly_rate ?? e.hourly_rate) || 0;
  // the grant decides, not profile.permissions.role (= the ACTING role for a custom role) — 2026-10-26
  const user = useAuthStore((s) => s.user);
  const canRevise = can(user, ["compensation.create", "compensation.edit"], [...PAYROLL_ACCESS, "hr_manager"]);
  const payOffice = can(user, ["payroll.run", "payroll.edit"], PAYROLL_ACCESS);
  const [stub, setStub] = useState<string | null>(null);

  const revisions = useTabQuery<any[]>(["profile-revisions", e.id], () => revisionsApi.list({ employee_id: e.id }), canRevise);
  const slips = useTabQuery(["profile-payslips", e.id], () => payrollApi.payslips(e.id));
  const recent = [...(slips.data || [])].sort((a, b) => (b.pay_date || "").localeCompare(a.pay_date || "")).slice(0, 12);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <Section title="Current pay" action={canRevise && e.status !== "terminated" && <Link to="/salary-revisions" className="text-xs text-primary-600 font-medium hover:underline">Revise salary →</Link>}>
          <Grid cols={3}>
            {hourly ? <Field label="Hourly rate"><span className="font-semibold">{cur(rate, currency)}</span> / hour</Field>
              : <Field label="Annual salary"><span className="font-semibold">{cur(annual, currency)}</span></Field>}
            {!hourly && <Field label="Per pay period">{cur(annual / (e.pay_periods_per_year || 26), currency)}</Field>}
            <Field label="Pay type">{titleCase(e.pay_type)}</Field>
            <Field label="Pay frequency">{freqLabel(e.pay_frequency)}</Field>
            <Field label="Hours per week">{e.standard_hours_per_week || null}</Field>
            <Field label="Effective from">{fmtDate(comp?.current?.effective_from)}</Field>
            <Field label="Currency">{currency}</Field>
          </Grid>
        </Section>

        <Section title="Bank account (masked)">
            <Grid cols={3}>
              <Field label="Institution" mono>{e.bank_institution}</Field>
              <Field label="Transit" mono>{e.bank_transit}</Field>
              <Field label="Account" mono>{e.bank_account_masked || (e.has_bank_account ? "On file" : <span className="text-gray-400">Not on file</span>)}</Field>
            </Grid>
        </Section>
      </div>

      {comp ? <SalaryTimeline history={comp.history || []} currency={currency} /> : <Section title="Salary history"><Denied what="salary history" /></Section>}

      {canRevise && (
        <Section title="Salary revisions" flush action={<Link to="/salary-revisions" className="text-xs text-primary-600 font-medium hover:underline">All revisions →</Link>}>
          {revisions.isLoading ? <Loading /> : revisions.denied ? <Denied what="salary revisions" />
            : !(revisions.data || []).length ? <EmptyLine action="Start a revision" to="/salary-revisions">No salary revisions for this employee yet.</EmptyLine>
            : (
              <table className="w-full text-xs min-w-[720px]">
                <thead className="bg-surface text-gray-400 text-[10px] uppercase"><tr>
                  <th className={TH}>Effective</th><th className={`${TH} text-right`}>Old</th><th className={`${TH} text-right`}>New</th><th className={`${TH} text-right`}>Change</th><th className={TH}>Reason</th><th className={TH}>Status</th><th className={TH}>By</th></tr></thead>
                <tbody>{(revisions.data || []).map((r: any) => (
                  <tr key={r.id} className="border-t border-surface-border">
                    <td className={TD}>{fmtDate(r.effective_from)}</td>
                    <td className={`${TD} text-right tabular-nums`}>{cur(r.old, r.currency || currency)}</td>
                    <td className={`${TD} text-right tabular-nums font-semibold`}>{cur(r.new, r.currency || currency)}</td>
                    <td className={`${TD} text-right tabular-nums`}>{r.percent != null ? pct(r.percent) : "—"}</td>
                    <td className={TD}>{r.reason_label || REASONS[r.reason] || titleCase(r.reason) || "—"}</td>
                    <td className={TD}><span className={tone(r.status)}>{r.status}</span></td>
                    <td className={TD}>{r.created_by || "—"}</td>
                  </tr>))}</tbody>
              </table>
            )}
        </Section>
      )}

      <Section title="Payslips" flush>
        {slips.isLoading ? <Loading /> : slips.denied ? <Denied what="payslips" /> : slips.failed ? <p className="p-4 text-sm text-gray-400">Payroll is not available right now.</p>
          : !recent.length ? <EmptyLine action={payOffice ? "Run payroll" : undefined} to="/payroll">No payslips yet.</EmptyLine>
          : (
            <table className="w-full text-xs min-w-[560px]">
              <thead className="bg-surface text-gray-400 text-[10px] uppercase"><tr>
                <th className={TH}>Pay date</th><th className={TH}>Period end</th><th className={TH}>Frequency</th><th className={`${TH} text-right`}>Gross</th><th className={`${TH} text-right`}>Net pay</th><th className={TH}></th></tr></thead>
              <tbody>{recent.map((s) => (
                <tr key={s.stub_id} className="border-t border-surface-border">
                  <td className={TD}>{fmtDate(s.pay_date)}</td><td className={TD}>{fmtDate(s.period_end)}</td><td className={TD}>{freqLabel(s.pay_frequency)}</td>
                  <td className={`${TD} text-right tabular-nums`}>{cur(s.gross, currency)}</td>
                  <td className={`${TD} text-right tabular-nums font-semibold`}>{cur(s.net_pay, currency)}</td>
                  <td className={`${TD} text-right`}><button className="btn-secondary !py-0.5 !px-2 text-xs" onClick={() => setStub(s.stub_id)}>View</button></td>
                </tr>))}</tbody>
            </table>
          )}
      </Section>

      {stub && <PayslipModal stubId={stub} onClose={() => setStub(null)} />}
    </div>
  );
}
