"""Default letter templates — seeded once per org, then the org edits its own copy.

Wording is plain and carries NO statutory claim; an org's legal team owns the final
text. Every {{field}} here is on the letter engine's allow-list (tested).
"""

_SIGN_OFF = "\n\nYours sincerely,"

DEFAULT_TEMPLATES: list[dict] = [
    {"key": "offer_letter", "name": "Offer letter", "body": """# Offer of employment

Dear {{employee.full_name}},

We are pleased to offer you the position of **{{offer.position}}** with {{org.name}}.

| Item | Details |
| Position | {{offer.position}} |
| Work location | {{offer.location}} |
| Proposed start date | {{offer.joining_date}} |
| Annual salary | {{offer.annual_ctc}} |

This offer is valid until **{{offer.valid_until}}**. Please sign and return a copy of this letter to accept it.

A detailed employment agreement and salary annexure will be given to you on your first day.""" + _SIGN_OFF},

    {"key": "appointment_letter", "name": "Appointment letter", "body": """# Letter of appointment

Dear {{employee.full_name}},

We are pleased to appoint you as **{{employee.designation}}** in the {{employee.department}} department of {{org.name}}, with effect from **{{employee.joining_date}}**.

## Terms

- Employee number: {{employee.employee_number}}
- Employment type: {{employee.employment_type}}
- Annual salary: {{salary.annual_ctc}}. The break-up is given in the salary annexure.
- You will be on probation until {{employee.probation_end_date}}. Your confirmation will be communicated in writing.

Company policies, as changed from time to time, and the employment standards legislation of your province of employment apply to your employment.""" + _SIGN_OFF},

    {"key": "ctc_annexure", "name": "Salary annexure (pay break-up)", "body": """# Salary annexure

**Name:** {{employee.full_name}}

**Employee number:** {{employee.employee_number}}

**Designation:** {{employee.designation}}

**Effective from:** {{letter.effective_date}}

**Annual salary:** {{salary.annual_ctc}}

{{salary.breakup_table}}

Statutory deductions (CPP / QPP, EI and income tax) apply and are shown on your pay stub."""},

    {"key": "confirmation", "name": "Confirmation letter", "body": """# Confirmation of employment

Dear {{employee.full_name}},

We are pleased to inform you that you have completed your probation period. Your employment as **{{employee.designation}}** with {{org.name}} is confirmed with effect from **{{letter.effective_date}}**.

All other terms of your employment stay the same.

We wish you continued success.""" + _SIGN_OFF},

    {"key": "probation_extension", "name": "Probation extension letter", "body": """# Extension of probation

Dear {{employee.full_name}},

Your probation period, which was due to end on {{probation.end_date}}, is extended until **{{probation.new_end_date}}**.

**Reason:** {{probation.reason}}

Your performance will be reviewed again before the new date. All other terms of your employment stay the same.""" + _SIGN_OFF},

    {"key": "salary_revision", "name": "Salary revision letter", "body": """# Salary revision

Dear {{employee.full_name}},

We are pleased to inform you that your salary is revised with effect from **{{salary.effective_from}}**.

| Item | Amount |
| Annual salary before | {{salary.old_annual}} |
| Annual salary now | {{salary.new_annual}} |
| Increase per year | {{salary.increase_amount}} |

{{salary.breakup_table}}

All other terms of your employment stay the same. Please keep this letter confidential.""" + _SIGN_OFF},

    {"key": "promotion", "name": "Promotion letter", "body": """# Promotion

Dear {{employee.full_name}},

Congratulations. You are promoted from {{career.old_designation}} to **{{career.new_designation}}** with effect from **{{career.effective_date}}**.

If your salary changes with this promotion, a separate salary revision letter will be given to you.

All other terms of your employment stay the same.""" + _SIGN_OFF},

    {"key": "transfer", "name": "Transfer letter", "body": """# Transfer

Dear {{employee.full_name}},

You are transferred with effect from **{{career.effective_date}}**.

| Item | From | To |
| Department | {{career.old_department}} | {{career.new_department}} |
| Location | {{career.old_location}} | {{career.new_location}} |

All other terms of your employment stay the same.""" + _SIGN_OFF},

    {"key": "entity_transfer", "name": "Legal entity transfer letter", "body": """# Transfer of employment

Dear {{employee.full_name}},

With effect from **{{career.effective_date}}** your employment moves from {{career.old_entity}} to **{{career.new_entity}}**, a group company of {{org.name}}.

| Item | Details |
| Employee number | {{employee.employee_number}} |
| Designation | {{employee.designation}} |
| New employer | {{career.new_entity}} |
| Effective from | {{career.effective_date}} |

Your date of joining and continuity of service are carried over. Unless this letter says otherwise, your designation, salary and other terms stay the same.

(This is placeholder wording for your HR and legal team to replace with the terms that apply to your group.)""" + _SIGN_OFF},

    {"key": "resignation_acceptance", "name": "Resignation acceptance letter", "body": """# Acceptance of resignation

Dear {{employee.full_name}},

We have received your resignation dated {{exit.resignation_date}} and it is accepted.

Your last working day with {{org.name}} will be **{{exit.last_working_date}}**.

Please complete the handover and the exit clearance before that day. Your separation letter, final pay and Record of Employment (ROE) will follow once the clearance is complete.""" + _SIGN_OFF},

    {"key": "relieving", "name": "Separation letter", "body": """# Separation letter

Dear {{employee.full_name}},

This refers to your resignation dated {{exit.resignation_date}}. Your employment with {{org.name}} ends at the close of working hours on **{{exit.last_working_date}}**.

- Employee number: {{employee.employee_number}}
- Designation: {{employee.designation}}
- Start date: {{employee.joining_date}}

We thank you for your contribution and wish you the best."""  + _SIGN_OFF},

    {"key": "experience_certificate", "name": "Employment reference letter", "body": """# Employment reference

**To whom it may concern**

This is to certify that **{{employee.full_name}}** (employee number {{employee.employee_number}}) worked with {{org.name}} from {{employee.joining_date}} to {{exit.last_working_date}}.

The last designation held was **{{employee.designation}}** in the {{employee.department}} department.

This letter is issued at the request of the employee."""},

    {"key": "fnf_cover", "name": "Final pay cover letter", "body": """# Final pay

Dear {{employee.full_name}},

Your final pay from {{org.name}} is complete. Your last working day was {{exit.last_working_date}}.

**Net amount:** {{exit.fnf_amount}}

The final pay statement is attached; your Record of Employment (ROE) is filed with Service Canada. Please write to us if you have a question about it."""  + _SIGN_OFF},

    {"key": "employment_verification", "name": "Employment verification letter", "body": """# Employment verification

**To whom it may concern**

This is to certify that **{{employee.full_name}}** (employee number {{employee.employee_number}}) is employed with {{org.name}} as **{{employee.designation}}** since {{employee.joining_date}}.

As per our records the employee's address is: {{employee.address}}

This letter is issued at the request of the employee."""},
    # 2026-10-22 fixed-term contracts (appended at the end: tests index the list)
    {"key": "contract_renewal", "name": "Fixed-term contract renewal letter", "body": """# Renewal of your fixed-term contract

Dear {{employee.full_name}},

Your fixed-term contract as **{{employee.designation}}**, which was due to end on {{contract.old_end_date}}, is renewed for a further term from **{{contract.start_date}}** to **{{contract.end_date}}** (term {{contract.term_no}}).

All other terms of your contract stay the same. Your pay and benefits for the new term are as set out in your contract.""" + _SIGN_OFF},

    {"key": "contract_conversion", "name": "Conversion to permanent employment letter", "body": """# Conversion to permanent employment

Dear {{employee.full_name}},

We are pleased to confirm that your fixed-term contract, which was due to end on {{contract.old_end_date}}, is converted to permanent employment as **{{employee.designation}}** with {{org.name}} with effect from **{{contract.effective_date}}**.

Your continuous service from your start date is preserved. All other terms of your employment stay the same unless we write to you separately.""" + _SIGN_OFF},
]

DEFAULT_KEYS = [t["key"] for t in DEFAULT_TEMPLATES]
