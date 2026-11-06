"""Auth service — S4 additive schema (new tables via create_all + ADD COLUMN IF NOT
EXISTS on existing ones). Nothing is dropped or rewritten; existing orgs keep a
NULL subscription_status, which every reader treats as "active"."""
from sqlalchemy import text

import saas_models  # noqa: F401  (registers the new tables on Base)
from database import Base, SessionLocal, engine

_ORG_COLS = [
    ("subscription_status", "VARCHAR(20)"), ("trial_ends_at", "TIMESTAMPTZ"), ("access_until", "TIMESTAMPTZ"),
    ("current_period_end", "TIMESTAMPTZ"), ("past_due_since", "TIMESTAMPTZ"),
    ("cancel_at_period_end", "BOOLEAN DEFAULT FALSE"), ("seats", "INTEGER"), ("provider", "VARCHAR(20)"),
    ("provider_customer_id", "VARCHAR(60)"), ("provider_subscription_id", "VARCHAR(60)"),
    ("billing_address", "VARCHAR(400)"), ("signup_source", "VARCHAR(20)"),
]
_PLAN_COLS = [("modules", "JSONB"), ("trial_days", "INTEGER"), ("self_serve", "BOOLEAN DEFAULT TRUE"),
              ("provider_plan_id", "VARCHAR(60)")]
_INVOICE_COLS = [("kind", "VARCHAR(16)"), ("paid_at", "TIMESTAMPTZ"), ("provider_payment_id", "VARCHAR(60)"),
                 ("provider_invoice_id", "VARCHAR(60)")]


async def init_saas():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if conn.dialect.name != "postgresql":
            return
        for col, ddl in _ORG_COLS:
            await conn.execute(text(f"ALTER TABLE organizations ADD COLUMN IF NOT EXISTS {col} {ddl}"))
        for col, ddl in _PLAN_COLS:
            await conn.execute(text(f"ALTER TABLE billing_plan_catalog ADD COLUMN IF NOT EXISTS {col} {ddl}"))
        for col, ddl in _INVOICE_COLS:
            await conn.execute(text(f"ALTER TABLE billing_invoices ADD COLUMN IF NOT EXISTS {col} {ddl}"))
        # 2026-10-25 agentic add-on chosen at sign-up (Alembic 0015_agentic re-runs this boot DDL)
        await conn.execute(text("ALTER TABLE signup_requests ADD COLUMN IF NOT EXISTS addons JSONB"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_organizations_provider_sub "
                                "ON organizations (provider_subscription_id)"))
    # Unique invoice numbers (GST/HST invoice serials). Own transaction: old duplicate data
    # only skips the index (reported), never stops the service.
    try:
        async with engine.begin() as conn:
            if conn.dialect.name == "postgresql":
                await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS billing_invoices_number_uk "
                                        "ON billing_invoices (number) WHERE number IS NOT NULL"))
    except Exception as exc:
        print(f"[auth] billing_invoices_number_uk not created: {exc.__class__.__name__}", flush=True)
    await _seed_plan_entitlements()


async def _seed_plan_entitlements():
    """Copy the constant plan → module map into the catalogue rows that have none,
    so entitlements are DATA from here on (the operator edits them)."""
    from sqlalchemy import select

    from constants import PLAN_MODULES
    from models import BillingPlan
    async with SessionLocal() as s:
        for row in (await s.execute(select(BillingPlan))).scalars().all():
            if not row.modules and row.plan_key in PLAN_MODULES:
                row.modules = list(PLAN_MODULES[row.plan_key])
        await s.commit()
