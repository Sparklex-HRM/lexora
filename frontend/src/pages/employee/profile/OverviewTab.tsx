import { COUNTRY_NAME, REGION_NAMES } from "../../../api/regions";
import type { TabProps } from "../EmployeeProfilePage";
import { Field, Grid, Section, fmtDate, titleCase } from "./_bits";

/** Personal · contact · emergency · nominee · identity numbers (MASKED — the API
 *  never sends a full SIN / bank number here; exactly the masks the
 *  edit form shows as "Saved: XXXX1234"). */
export default function OverviewTab({ profile }: TabProps) {
  const e = profile.employee;
  const country = (e.address_country || e.country || "").toUpperCase();
  const regions = REGION_NAMES.CA;
  const canEdit = profile.permissions.can_edit;
  const notOnFile = <span className="text-gray-400">Not on file</span>;
  const address = [e.address_line1, e.address_line2].filter(Boolean).join(", ");

  return (
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
      <Section title="Personal">
        <Grid cols={3}>
          <Field label="First name">{e.first_name}</Field>
          <Field label="Middle name">{e.middle_name}</Field>
          <Field label="Last name">{e.last_name}</Field>
          <Field label="Preferred name">{e.preferred_name}</Field>
          <Field label="Date of birth">{e.date_of_birth ? fmtDate(e.date_of_birth) : null}</Field>
          <Field label="Gender">{e.gender}</Field>
          <Field label="Marital status">{titleCase(e.marital_status)}</Field>
          {e.pronouns && <Field label="Pronouns">{e.pronouns}</Field>}
        </Grid>
      </Section>

      <Section title="Contact">
        <Grid cols={3}>
          <Field label="Mobile">{e.phone}</Field>
          <Field label="Home phone">{e.home_phone}</Field>
          <Field label="Work e-mail">{e.work_email}</Field>
          <Field label="Personal e-mail">{e.email}</Field>
          <Field label="Address" wide>{address || null}</Field>
          <Field label="City">{e.city}</Field>
          <Field label="Postal code">{e.postal_code}</Field>
          <Field label="Province of residence">{e.province_of_residence ? regions[e.province_of_residence] || e.province_of_residence : null}</Field>
          <Field label="Country">{country ? COUNTRY_NAME[country] || country : null}</Field>
        </Grid>
      </Section>

      <Section title="Emergency contact">
        {e.emergency_name || e.emergency_phone ? (
          <Grid cols={3}>
            <Field label="Name">{e.emergency_name}</Field>
            <Field label="Relationship">{e.emergency_relationship}</Field>
            <Field label="Phone">{e.emergency_phone}</Field>
          </Grid>
        ) : <p className="text-sm text-gray-400">No emergency contact recorded.{canEdit && " Use Edit → Emergency to add one."}</p>}
        {(e.nominee_name || e.nominee_phone) && (
          <div className="mt-4 pt-3 border-t border-surface-border">
            <div className="text-[10px] font-semibold text-gray-400 uppercase tracking-wide mb-2">Nominee</div>
            <Grid cols={3}>
              <Field label="Name">{e.nominee_name}</Field>
              <Field label="Relationship">{e.nominee_relationship}</Field>
              <Field label="Phone">{e.nominee_phone}</Field>
            </Grid>
          </div>
        )}
      </Section>

      <Section title="SIN and work status (masked)">
        <Grid cols={3}>
          <Field label="SIN" mono>{e.sin_masked || (e.has_sin ? "On file" : notOnFile)}</Field>
          <Field label="Work eligibility">{titleCase(e.work_eligibility)}</Field>
          <Field label="Permit type">{e.work_permit_type}</Field>
          <Field label="Permit expiry">{e.work_permit_expiry ? fmtDate(e.work_permit_expiry) : null}</Field>
          <Field label="Bank account" mono>{e.bank_account_masked || (e.has_bank_account ? "On file" : notOnFile)}</Field>
          <Field label="Institution · transit" mono>{[e.bank_institution, e.bank_transit].filter(Boolean).join(" · ") || null}</Field>
        </Grid>
        <p className="text-[11px] text-gray-400 mt-3">
          Numbers are stored encrypted and always shown masked here.{e.identifiers_encrypted === false && " This record has not been encrypted yet."}
          {canEdit && " To change one, open Edit → SIN and tax."}
        </p>
      </Section>
    </div>
  );
}
