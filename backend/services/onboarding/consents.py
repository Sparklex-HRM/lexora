"""Onboarding — consent types for the new-hire wizard (Canada, PIPEDA).

The joiner acknowledges the privacy notice and gives consent per purpose. The four purpose
consents (`privacy: True`) are the same keys as the compliance service's privacy centre
(compliance/privacy.py PURPOSES); on activation they are mirrored into its consent register so
consent coverage counts them. Texts are plain English; `NOTICE_VERSION` is the notice version
the hire agreed to.
"""

CONSENT_TYPES = [
    {"key": "privacy_pipeda", "label": "Privacy notice (PIPEDA)", "required": True, "privacy": False,
     "text": "I have read the employee privacy notice. It tells me what personal information the company collects, "
             "why, how long it is kept, and how to ask for access or a correction."},
    {"key": "payroll_processing", "label": "Consent — payroll processing", "required": True, "privacy": True,
     "text": "I agree that my salary, bank account, SIN, TD1 claim amounts and time records are used to calculate and pay me."},
    {"key": "government_reporting", "label": "Consent — government reporting", "required": True, "privacy": True,
     "text": "I understand my details are sent to the Canada Revenue Agency, Service Canada (Record of Employment), "
             "Revenu Québec (Québec only) and the provincial workers' compensation board as the law requires."},
    {"key": "benefits", "label": "Consent — benefits & beneficiaries", "required": False, "privacy": True,
     "text": "I agree that my details and my beneficiaries' details are used to enrol me in group benefits and retirement plans."},
    {"key": "background_check", "label": "Consent — background verification", "required": False, "privacy": True,
     "text": "I agree that my identity, education and past-employment documents are verified."},
    {"key": "direct_deposit", "label": "Direct deposit authorisation", "required": True, "privacy": False,
     "text": "I authorise my pay to be deposited to the bank account I provided (institution, transit and account number)."},
    {"key": "code_of_conduct", "label": "Code of conduct", "required": True, "privacy": False,
     "text": "I have read and will follow the code of conduct."},
    {"key": "ohsa", "label": "Health & safety policy", "required": True, "privacy": False,
     "text": "I have read the health and safety policy."},
    {"key": "harassment", "label": "Workplace violence & harassment policy", "required": True, "privacy": False,
     "text": "I have read the workplace violence and harassment policy."},
    {"key": "e_paystub", "label": "Electronic pay statements & tax slips", "required": False, "privacy": False,
     "text": "I agree to receive my pay statements and my T4 (and RL-1 in Québec) electronically."},
    {"key": "offer_acceptance", "label": "Offer acceptance", "required": True, "privacy": False,
     "text": "I accept the offer of employment on the terms provided."},
]

PRIVACY_PURPOSES = [c["key"] for c in CONSENT_TYPES if c.get("privacy")]
NOTICE_VERSION = "CA-2026.1"


def consent_types_for(country: str | None = None) -> list[dict]:
    return CONSENT_TYPES


def privacy_purposes_in(consents: list[str]) -> list[str]:
    """The privacy-centre purposes among a session's consent keys, in catalogue order."""
    keys = set(consents)
    return [k for k in PRIVACY_PURPOSES if k in keys]
