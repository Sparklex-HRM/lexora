"""Phase-2 agents of the Agentic operations add-on (one sub-package per agent).

    agents.payroll_auditor   — Payroll Auditor (per pay period: inputs → 17 checks incl. CPP / QPP, EI, QPIP from the engine's
                               constants → chase → review pack; never changes pay)
    agents.onboarding        — Onboarding Agent (joining checklist: SIN, TD1, direct deposit, permits, letter draft)
    agents.exit              — Exit Agent (clearance, provincial final-pay clock, ROE reminder, exit letters)
    agents.attendance        — Attendance Agent (punch gaps, late marks, pending regularisations / leave)
    agents.pay_calendar      — shared helpers: chase owners, the open pay period and the pay schedule

Each sub-package exposes ``install(app)`` which the service's ``main.py`` calls once at import time:
it includes the agent's router, registers its executors and scheduler hook, and lists the tables it
owns for the boot-time ``create_tables``.
"""
