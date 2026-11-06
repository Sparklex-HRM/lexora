"""Payroll Auditor — agent of the Agentic operations add-on (Canada, restored 2026-10-26).

    from agents.payroll_auditor import install, PAYROLL_AUDITOR_TABLES
    install(app)                                   # router + executors + scheduler hook (main.py, at import time)
    await pm.create_tables(PAYROLL_AUDITOR_TABLES) # lifespan (additive; the findings table)

Module map
  thresholds.py  presets tight / normal / loose + the agent's own org-editable keys (ai_agentic.payroll_auditor), CAD floors
  statutory.py   CPP / CPP2 · QPP / QPP2 · EI (incl. the Quebec rate) · QPIP · vacation minimums — from the payroll engine's
                 own constants (never typed here); the Verification Center supplies the verified flags
  inputs.py      READ-ONLY collection (SELECT via watchdogs.rows; engine dry run + GL journal over HTTP), one pay frequency per run
  checks.py      the deterministic checks PA-01 … PA-17 → findings
  explain.py     deterministic variance linking + templated reasons; the model may only rephrase (validated)
  pack.py        findings table, review pack, confirm / dismiss / note / explain actions
  executors.py   payroll.chase_input (0) · mark_variance_explained (0) · lock_inputs (1) · send_for_approval (2) · ready_to_release (2)
                 — every executor only opens a work item or records a decision; the agent never changes pay
  run.py         run_payroll_audit + triggers (T-N per pay frequency, timesheet lock, shadow-cycle completion)
  router.py      /ai/agents/payroll-auditor/*
"""
from __future__ import annotations

from .models import PAYROLL_AUDITOR_TABLES, PayrollFinding  # noqa: F401

AGENT = "payroll_auditor"
UI_LINK = "/ai/agents/payroll-auditor"


def install(app) -> None:
    from . import executors  # noqa: F401  (registers the executors)
    from .router import router
    from .run import install_hook
    app.include_router(router)
    install_hook()


__all__ = ["install", "PAYROLL_AUDITOR_TABLES", "PayrollFinding", "AGENT", "UI_LINK"]
