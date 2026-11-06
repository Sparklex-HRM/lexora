"""Exit Agent — employee-lifecycle agent of the Agentic operations add-on (2026-10-26).

    from agents.exit import install
    install(app)          # /ai/agents/exit/* routes + executors + the scheduler hook

What it finishes (shadow by default; never computes money, never pays on its own):
  inputs.py    READ-ONLY: open exit cases (exit_cases), clearance items (exit_clearance_items), the employee's province, the
               pay schedule from the last pay run (pay period of the last day + next pay day), exit letters
  checks.py    EX-01 notice + last working day · EX-02 clearance per department (IT, finance, admin, manager, HR) chased until
               signed · EX-03 final pay inside the provincial clock (Ontario ESA s.11(5); other provinces shown as an estimate)
               · EX-04 separation + employment reference letters · EX-05 ROE within 5 days after the pay period of the last day
  executors.py exit.chase (0) · exit.run_final_pay (2 — two DIFFERENT approvers; the payroll final-pay run computes the vacation
               payout, recoveries and ROE draft from the exit case's own pre-fill) · exit.draft_exit_letters (1 — drafts, HR issues)
"""
from __future__ import annotations

AGENT = "exit"
UI_LINK = "/ai/agents/exit"


def install(app) -> None:
    from . import executors  # noqa: F401
    from .router import router
    from .run import install_hook
    app.include_router(router)
    install_hook()
