import { Link } from "react-router-dom";
import { cur } from "../../../api/lifecycle";
import { REGION_NAMES } from "../../../api/regions";
import { PAYROLL_ACCESS } from "../../../store/authStore";
import type { TabProps } from "../EmployeeProfilePage";
import { Field, Grid, Section, fmtDate, titleCase } from "./_bits";
import { useCan } from "../../../config/permissions";   // 2026-10-26: UI gates = permission + role (custom roles)

/** SIN / TD1 claims / CPP-EI exemptions. Nothing is invented: a value the
 *  system does not hold reads "Not on file". */
export default function StatutoryTab({ profile }: TabProps) {
  const e = profile.employee;
  const regions = REGION_NAMES.CA;
  const notOnFile = <span className="text-gray-400">Not on file</span>;
  const statEdit = useCan(["statutory.edit", "payroll.edit"], PAYROLL_ACCESS);   // the grant decides (2026-10-26)

  return (
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
      <Section title="SIN and work status (masked)">
        <Grid cols={3}>
          <Field label="SIN" mono>{e.sin_masked || (e.has_sin ? "On file" : notOnFile)}</Field>
          <Field label="Work eligibility">{titleCase(e.work_eligibility) || notOnFile}</Field>
          <Field label="Permit type">{e.work_permit_type}</Field>
          <Field label="Permit expiry">{e.work_permit_expiry ? fmtDate(e.work_permit_expiry) : null}</Field>
          <Field label="Province of employment">{regions[e.province_of_employment] || e.province_of_employment}</Field>
        </Grid>
      </Section>
      <Section title="Tax (TD1) and source deductions">
        <Grid cols={3}>
          <Field label="TD1 federal claim">{e.td1_federal_claim != null ? cur(e.td1_federal_claim, "CAD") : notOnFile}</Field>
          <Field label="TD1 provincial claim">{e.td1_provincial_claim != null ? cur(e.td1_provincial_claim, "CAD") : notOnFile}</Field>
          <Field label="Extra tax per pay">{e.additional_tax_per_period ? cur(e.additional_tax_per_period, "CAD") : "None"}</Field>
          <Field label="CPP">{e.cpp_exempt ? <span className="badge-amber">Exempt</span> : "Deducted"}</Field>
          <Field label="EI">{e.ei_exempt ? <span className="badge-amber">Exempt</span> : "Deducted"}</Field>
          <Field label="Vacation pay %">{e.vacation_percent != null ? `${e.vacation_percent}%` : null}</Field>
        </Grid>
      </Section>
      {statEdit && (
        <Section title="Where to act" className="xl:col-span-2">
          <div className="flex flex-wrap gap-2 text-xs">
            <Link to="/payroll/canada/remittances" className="btn-secondary !py-1">CRA remittances</Link>
            <Link to="/payroll/canada/statutory-values" className="btn-secondary !py-1">Statutory values</Link>
            <Link to="/payroll/canada/year-end" className="btn-secondary !py-1">Year-end (T4 / RL-1)</Link>
          </div>
        </Section>
      )}
    </div>
  );
}
