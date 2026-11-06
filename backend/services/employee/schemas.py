"""Employee service — schemas (with pay-frequency validation)."""
import sys
from datetime import date

from pydantic import BaseModel, field_validator

sys.path.append("/app")
from shared.utils.enums import EMPLOYMENT_TYPES, PAY_FREQUENCIES, PAY_TYPES  # noqa: E402
from ca_identity import CA_PROVINCES  # noqa: E402  (the 13 provinces / territories — Canada-only)


class EmployeeCreate(BaseModel):
    # Identity
    employee_number: str | None = None           # blank → next auto number for the org
    first_name: str
    last_name: str
    email: str | None = None
    date_of_birth: date | None = None
    gender: str | None = None
    preferred_name: str | None = None
    pronouns: str | None = None
    sin: str | None = None                       # stored to sin_encrypted (crypto in Phase 2)

    # Canadian work eligibility
    work_eligibility: str | None = None          # citizen | permanent_resident | work_permit | study_permit
    work_permit_expiry: date | None = None

    # Contact / address
    phone: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    postal_code: str | None = None
    address_country: str | None = "CA"   # Canada-only product — always CA

    # Emergency contact
    emergency_name: str | None = None
    emergency_relationship: str | None = None
    emergency_phone: str | None = None

    # Employment
    hire_date: date | None = None
    employment_type: str = "full_time"
    province_of_employment: str | None = None    # blank → the organization's province
    job_title: str | None = None
    department: str | None = None
    work_location: str | None = None
    branch_id: str | None = None
    branch_name: str | None = None
    entity_id: str | None = None                 # D4: legal entity (blank → the org's default entity)
    manager_id: str | None = None                # picked from the employee list
    manager_name: str | None = None
    grade: str | None = None
    father_name: str | None = None
    spouse_name: str | None = None
    status: str | None = None

    # Compensation
    pay_type: str = "salary"
    annual_salary: float | None = None
    hourly_rate: float | None = None
    standard_hours_per_week: float = 40
    pay_frequency: str = "biweekly"

    # Tax profile (TD1)
    td1_federal_claim: float | None = None
    td1_provincial_claim: float | None = None
    additional_tax_per_period: float | None = 0

    # Direct deposit
    bank_institution: str | None = None
    bank_transit: str | None = None
    bank_account_last4: str | None = None
    bank_account: str | None = None               # full account → bank_account_encrypted (crypto Phase 2)

    # Identity documents (stored encrypted; API returns masked by default)
    passport: str | None = None                   # S4: stored encrypted (passport_enc) + masked
    passport_expiry: date | None = None

    # Nominee — who may act on the employee's personal-information requests after death/incapacity
    nominee_name: str | None = None
    nominee_relationship: str | None = None
    nominee_phone: str | None = None

    # Extended intake (onboarding spec)
    middle_name: str | None = None
    marital_status: str | None = None
    work_email: str | None = None
    home_phone: str | None = None
    province_of_residence: str | None = None
    work_permit_type: str | None = None
    work_permit_number: str | None = None
    probation_end_date: date | None = None
    contract_start_date: date | None = None
    contract_end_date: date | None = None
    contract_notice_days: int | None = None
    contract_ref: str | None = None
    fte_percent: float | None = None
    overtime_eligible: bool | None = None
    vacation_percent: float | None = None
    division: str | None = None
    cost_center: str | None = None
    is_union: bool | None = None
    union_local: str | None = None
    cpp_exempt: bool | None = None
    ei_exempt: bool | None = None
    td1_claim_lines: dict | None = None
    td1_prov_claim_lines: dict | None = None

    @field_validator("pay_frequency")
    @classmethod
    def _freq(cls, v):
        if v not in PAY_FREQUENCIES:
            raise ValueError(f"pay_frequency must be one of {PAY_FREQUENCIES}")
        return v

    @field_validator("address_country")
    @classmethod
    def _country(cls, v):
        if v is None or str(v).strip() == "":
            return "CA"
        if str(v).strip().upper() != "CA":
            raise ValueError("address_country must be CA (Lexora HRM is Canada-only)")
        return "CA"

    @field_validator("province_of_employment", "province_of_residence")
    @classmethod
    def _prov(cls, v):
        if v is None or str(v).strip() == "":
            return None
        v = v.upper()
        if v not in CA_PROVINCES:
            raise ValueError(f"province must be a valid Canadian province/territory code {CA_PROVINCES}")
        return v

    @field_validator("employment_type")
    @classmethod
    def _etype(cls, v):
        if v not in EMPLOYMENT_TYPES:
            raise ValueError(f"employment_type must be one of {EMPLOYMENT_TYPES}")
        return v

    @field_validator("pay_type")
    @classmethod
    def _ptype(cls, v):
        if v not in PAY_TYPES:
            raise ValueError(f"pay_type must be one of {PAY_TYPES}")
        return v
