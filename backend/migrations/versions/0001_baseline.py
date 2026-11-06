"""Baseline — the Lexora HRM schema exactly as the services build it at boot (squashed 2026-10-26).

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-26

Lexora HRM starts from a FRESH database, so the Lexora HRM chain (0001 … 0018) was squashed into
this one revision when the Canada-only fork was cut. Upgrade:

  1. runs every service's boot-time DDL (SQLAlchemy create_all + each service's idempotent
     ``ADD COLUMN IF NOT EXISTS`` / ``CREATE INDEX IF NOT EXISTS`` + boot ensures), one service per
     process (migrations/worker.py) — creates every table;
  2. pg_trgm + the trigram GIN indexes the global-search ILIKEs use (was 0010_global_search);
  3. the append-only triggers of the agentic audit tables (was 0015_agentic), when the AI
     platform still defines them.

Idempotent; re-running it on a database that already has the schema only fills what is missing.
Not reversible (there is nothing "before" the baseline). Later schema changes get their own
revisions on top of this one.
"""
revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

# (index name, table, column) — trigram GIN indexes for the global search box
TRGM_INDEXES = [
    ("ix_trgm_employees_first_name", "public.employees", "first_name"),
    ("ix_trgm_employees_last_name", "public.employees", "last_name"),
    ("ix_trgm_employees_number", "public.employees", "employee_number"),
    ("ix_trgm_employees_work_email", "public.employees", "work_email"),
    ("ix_trgm_hire_candidates_first", "public.hire_candidates", "first_name"),
    ("ix_trgm_hire_candidates_last", "public.hire_candidates", "last_name"),
    ("ix_trgm_hire_candidates_email", "public.hire_candidates", "email"),
    ("ix_trgm_leave_requests_emp", "public.leave_requests", "employee_name"),
    ("ix_trgm_eng_tickets_subject", "public.eng_tickets", "subject"),
    ("ix_trgm_eng_tickets_number", "public.eng_tickets", "number"),
    ("ix_trgm_exit_cases_emp", "public.exit_cases", "employee_name"),
    ("ix_trgm_issued_letters_number", "public.issued_letters", "number"),
    ("ix_trgm_apr_items_title", "public.apr_items", "title"),
]


def upgrade() -> None:
    import os
    import sys

    from alembic import op
    from migrations.cli import bootstrap_all

    bootstrap_all()

    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    for name, table, expr in TRGM_INDEXES:
        # only when the table exists ($q$ quoting keeps the expression's own quotes intact)
        op.execute(
            f"DO $$ BEGIN IF to_regclass('{table}') IS NOT NULL THEN "
            f"EXECUTE $q$CREATE INDEX IF NOT EXISTS {name} ON {table} USING gin (({expr}) gin_trgm_ops)$q$; "
            f"END IF; END $$;")

    svc = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "services", "ai_platform")
    if svc not in sys.path:
        sys.path.insert(0, svc)
    try:
        from agentic_models import APPEND_ONLY_DDL
    except ImportError:
        APPEND_ONLY_DDL = []
    for stmt in APPEND_ONLY_DDL:
        op.execute(stmt)


def downgrade() -> None:
    raise RuntimeError("0001_baseline cannot be downgraded — restore a backup instead (scripts/ops/restore.sh)")
