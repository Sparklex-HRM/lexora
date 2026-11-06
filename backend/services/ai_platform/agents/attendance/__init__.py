"""Attendance Agent — employee-lifecycle agent of the Agentic operations add-on (2026-10-26).

    from agents.attendance import install
    install(app)          # /ai/agents/attendance/* routes + executor + the daily scheduler hook

Daily, for the window up to yesterday (shadow by default; the agent never edits a punch, approves anything or sends
without the rails):
  AT-01 missing / single punches → the EMPLOYEE is asked to raise a regularisation (regularise_hours, default 48 h)
  AT-02 late marks at or above the monthly threshold → the manager (one item per employee per month)
  AT-05 regularisations pending with the manager (approve_hours window, default 72 h; past the window it escalates)
  AT-06 unapproved (pending) leave in the pay period → the manager
Every item in the open pay period carries the payroll CUT-OFF (pay date − the org's lead days) — resolving AT-01/05/06
before it means the pay run sees clean hours.
"""
from __future__ import annotations

AGENT = "attendance"
UI_LINK = "/ai/agents/attendance"


def install(app) -> None:
    from . import executors  # noqa: F401
    from .router import router
    from .run import install_hook
    app.include_router(router)
    install_hook()
