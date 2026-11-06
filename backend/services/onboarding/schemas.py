"""Onboarding service — request schemas."""
from pydantic import BaseModel


class SessionCreate(BaseModel):
    """HR seeds the position + pay, then a token link is generated."""
    first_name: str
    last_name: str
    email: str | None = None
    employee_number: str | None = None
    # Position (step: position)
    job_title: str | None = None
    department: str | None = None
    work_location: str | None = None
    employment_type: str | None = "full_time"
    address_country: str | None = None           # IN | CA — blank → the organization's country
    province_of_employment: str | None = None    # blank → the organization's state / province
    hire_date: str | None = None
    manager_id: str | None = None                # picked from the employee list
    manager_name: str | None = None
    grade: str | None = None
    probation_end_date: str | None = None
    # 2026-10-24 forms wrap-up: HR-side job fields the employee record already stores (all in intake.HR_EXTRA)
    work_email: str | None = None
    division: str | None = None
    cost_center: str | None = None
    branch_id: str | None = None
    branch_name: str | None = None
    # Compensation (step: compensation)
    pay_type: str | None = "salary"
    annual_salary: float | None = None
    hourly_rate: float | None = None
    pay_frequency: str | None = None             # blank → bi-weekly
    standard_hours_per_week: float | None = 40
    overtime_eligible: bool | None = True
    vacation_percent: float | None = 4
    # Invite window
    invite_expiry_days: int | None = 14


class StepPatch(BaseModel):
    """The self-service wizard saves one step at a time (fields merged into data)."""
    step: str
    data: dict = {}
    complete: bool = True


class ConsentIn(BaseModel):
    consent_type: str
    version: str | None = None
    signed_name: str | None = None


class DocumentIn(BaseModel):
    doc_type: str
    file_name: str | None = None
    storage_url: str | None = None
    content_type: str | None = None
    size_bytes: int | None = None


class ReviewIn(BaseModel):
    note: str | None = None


class TaskIn(BaseModel):
    title: str | None = None
    owner: str | None = None
    due_date: str | None = None
    done: bool | None = None
    note: str | None = None


class TemplateItem(BaseModel):
    title: str
    owner: str | None = None
    due_offset_days: int = 0


class TemplateIn(BaseModel):
    items: list[TemplateItem]
