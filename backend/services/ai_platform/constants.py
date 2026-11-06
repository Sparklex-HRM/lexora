"""Lexora HRM — AI agent catalog.

A LEAN, task-specific watchdog fleet (~14 agents), not a 546-style org chart.
Each agent does one HR/payroll job and proposes; a human approves. Mirrors the
Medfidelity ai-platform philosophy (SQL-poll watchdogs) but sized for HR.

Lexora HRM is Canada-only: country-neutral agents (time/leave/people/cost) plus
the Canadian statutory watchdogs (CRA remittance, CPP/EI caps, ROE, stat holiday).
"""

# key, name, domain, cadence, description

# ── country-neutral watchdogs ──
_NEUTRAL = [
    ("overtime_anomaly",    "Overtime Anomaly",      "Time",       "weekly",  "Spots timesheet / overtime spikes versus an employee's history."),
    ("leave_balance_guard", "Leave Balance Guard",   "Leave",      "daily",   "Catches negative or over-accrued vacation / sick balances."),
    ("cost_drift",          "Cost Drift",            "Cost",       "each run","Alerts on payroll cost jumps period-over-period."),
    ("onboarding_chaser",   "Onboarding Chaser",     "People",     "daily",   "Nudges incomplete new-hire checklists toward 'active'."),
    ("missing_data_scan",   "Missing-Data Scan",     "People",     "daily",   "Finds employees missing email, location, or pay data."),
    ("classification_check","Classification Check",  "Compliance", "monthly", "Flags contractor-vs-employee misclassification risk."),
    ("paygroup_calendar",   "Pay Calendar Guard",    "Payroll",    "weekly",  "Ensures every pay group has a valid upcoming pay calendar."),
    ("anniversary",         "Milestones",            "People",     "daily",   "Surfaces work anniversaries and probation-end dates."),
]

_COPILOT = [
    ("hr_copilot",          "Lexi (HR Copilot)",     "AI",         "on demand","Natural-language answers grounded in your live HR data."),
]

# ── Canada statutory watchdogs ──
_CA = [
    ("payroll_preflight",   "Payroll Preflight",     "Payroll",    "each run","Flags missing SIN / TD1 / banking before a pay run locks."),
    ("remittance_watchdog", "Remittance Watchdog",   "Compliance", "daily",   "Warns before a CRA source-deduction remittance due date."),
    ("cpp_ei_cap_monitor",  "CPP/EI Cap Monitor",    "Payroll",    "each run","Tracks employees approaching the annual CPP / CPP2 / EI maximums."),
    ("roe_deadline",        "ROE Deadline",          "Compliance", "daily",   "Tracks the 5-day Record of Employment window on separations."),
    ("stat_holiday",        "Stat-Holiday Watcher",  "Time",       "weekly",  "Provincial statutory-holiday pay eligibility watcher."),
]

AGENTS = _CA + _NEUTRAL + _COPILOT

DOMAINS = ["People", "Payroll", "Compliance", "Time", "Leave", "Cost", "AI"]


def agents_for(country: str | None = None) -> list:
    """The agent fleet. Lexora HRM is Canada-only, so this is always the CA fleet
    (the argument is kept for call-site compatibility)."""
    return AGENTS
