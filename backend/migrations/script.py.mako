"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

Rules (docs/ops/DEPLOY_RUNBOOK.md §Migrations):
  * additive first — add nullable columns / new tables; backfill; tighten in a LATER release
  * idempotent where Postgres allows it (ADD COLUMN IF NOT EXISTS, CREATE INDEX IF NOT EXISTS)
  * big tables: CREATE INDEX CONCURRENTLY needs `with op.get_context().autocommit_block():`
  * also add the column to the service's model so create_all-built dev DBs match
"""
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
