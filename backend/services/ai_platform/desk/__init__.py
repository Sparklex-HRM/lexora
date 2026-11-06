"""HR Desk engine (stream D, 2026-10-25) — deterministic core behind the HR Desk agent.

    from desk import install, ensure_tables
    install(app)              # mounts /ai/desk/* (desk.routers) + registers the agent's scheduler hooks
    await ensure_tables()     # boot DDL for the desk_* tables (additive; Alembic 0016_desk re-runs it)

Modules: policy_models · policy_store · router (hard router) · answer (grounded contract) · payslip_diff ·
handoff · audit · metrics · engine (orchestrator = the phase-2 API) · tools (catalogue additions) · settings ·
dispatch (the employee chat path: hard router → pay change → policy → tools → abstain) · agent (runs, value
ledger, idle-session close, weekly report tick) · payroll_meta (RunMeta from payroll data) · report.
"""
from __future__ import annotations


def install(app) -> None:
    from desk.routers import router
    app.include_router(router)
    try:
        from desk import agent as _agent
        _agent.register_hooks()                              # idle-session run close + weekly report tick (add-on orgs only)
    except Exception:                                        # noqa: BLE001 — the desk answers even if the agentic rails are missing
        pass


async def ensure_tables(retries: int = 5) -> None:
    from shared.utils import platform_models as pm
    from desk.policy_models import DESK_COLUMN_DDL, DESK_TABLES
    await pm.create_tables(DESK_TABLES, retries=retries)
    # phase-2 columns on tables 0016_desk already created (Postgres only; SQLite test DBs come from the models)
    try:
        from sqlalchemy import text
        eng = pm.get_engine()
        if eng is not None and eng.dialect.name == "postgresql":
            async with eng.begin() as conn:
                for stmt in DESK_COLUMN_DDL:
                    await conn.execute(text(stmt))
    except Exception as e:                                   # noqa: BLE001
        print(f"[ai_platform] HR Desk phase-2 columns not added: {e}")
