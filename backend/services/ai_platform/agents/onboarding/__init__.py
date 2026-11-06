"""Onboarding Agent — employee-lifecycle agent of the Agentic operations add-on (2026-10-26).

    from agents.onboarding import install
    install(app)          # /ai/agents/onboarding/* routes + executors + the scheduler hook (main.py, at import time)

What it finishes, end to end (shadow by default; every outbound step is a proposal on the foundation rails):
  inputs.py    READ-ONLY: accepted offers (hire_offers), joiners (onboarding_sessions + employees joined in the window),
               documents vault (employee_documents), consents (onboarding_consents), letters (issued_letters), assets,
               onboarding tasks, LMS assignments. Encrypted SIN / account numbers are never decrypted — presence only.
  checks.py    the joining checklist OB-00 … OB-14 (photo ID, address + postal code, education, direct deposit
               (institution / transit / account), SIN inside the CRA 3-day window, work-permit expiry for temporary
               residents, TD1 federal + provincial, vault verification, appointment letter, IT / Admin tasks, induction,
               date of joining). Blocking items are what the pay run needs (bank, SIN, DOJ) or a legal stop (expired permit).
  executors.py onboarding.chase (0, opens the deduplicated task) · onboarding.draft_appointment_letter (1 — HR approves,
               the executor asks the letters service for a DRAFT; HR issues it from Letters, the agent never issues)
  run.py       run_onboarding + triggers (daily, and a new accepted offer / joiner since the last run)
  router.py    /ai/agents/onboarding/*
"""
from __future__ import annotations

AGENT = "onboarding"
UI_LINK = "/ai/agents/onboarding"


def install(app) -> None:
    from . import executors  # noqa: F401  (registers the executors)
    from .router import router
    from .run import install_hook
    app.include_router(router)
    install_hook()
