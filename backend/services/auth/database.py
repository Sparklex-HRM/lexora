"""Auth service — DB wiring (shared async Base + engine)."""
import sys

sys.path.append("/app")
from shared.utils.db import Base, make_engine, make_sessionmaker  # noqa: E402

engine = make_engine()
SessionLocal = make_sessionmaker(engine)


async def get_db():
    async with SessionLocal() as session:
        yield session


async def init_db():
    from sqlalchemy import text
    from codes import next_code
    import models  # noqa: F401  (registers organizations/users/login_sessions/billing_invoices)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Multi-branch tenancy columns added after v0.1 (create_all won't ALTER).
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS branch_scope VARCHAR(10) DEFAULT 'branch'"))
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS branch_id UUID"))
        await conn.execute(text("ALTER TABLE organizations ADD COLUMN IF NOT EXISTS plan VARCHAR(20) DEFAULT 'growth'"))
        await conn.execute(text("ALTER TABLE organizations ADD COLUMN IF NOT EXISTS modules JSONB"))
        # Org login code — tenants sign in with it (superadmin is exempt).
        await conn.execute(text("ALTER TABLE organizations ADD COLUMN IF NOT EXISTS code VARCHAR(30)"))
        # Backfill any org that predates the column, then enforce uniqueness.
        taken = {c for (c,) in (await conn.execute(
            text("SELECT UPPER(code) FROM organizations WHERE code IS NOT NULL AND code <> ''"))).all()}
        missing = (await conn.execute(
            text("SELECT id, name FROM organizations WHERE code IS NULL OR code = ''"))).all()
        for oid, name in missing:
            code = next_code(name or "ORG", taken)
            taken.add(code)
            await conn.execute(text("UPDATE organizations SET code = :c WHERE id = :i"),
                               {"c": code, "i": oid})
        await conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS organizations_code_uk ON organizations (code)"))
        # PEPM billing fields + password-policy stamp.
        for col, ddl in (("addons", "JSONB"), ("pepm_override", "NUMERIC(10,2)"), ("gst_hst_number", "VARCHAR(15)"),
                         ("billing_email", "VARCHAR(200)"), ("billing_province", "VARCHAR(2)"),
                         ("billing_cycle", "VARCHAR(10) DEFAULT 'monthly'")):
            await conn.execute(text(f"ALTER TABLE organizations ADD COLUMN IF NOT EXISTS {col} {ddl}"))
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ"))
        await conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS billing_plan_catalog_uk ON billing_plan_catalog (country, plan_key)"))
        await conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS billing_addon_catalog_uk ON billing_addon_catalog (country, addon_key)"))
        # Logins for employees (stream A): additive columns on `users`.
        for col, ddl in (("password_set", "BOOLEAN DEFAULT TRUE"), ("invited_at", "TIMESTAMPTZ"),
                         ("disabled_at", "TIMESTAMPTZ"), ("disabled_reason", "VARCHAR(300)"),
                         ("disabled_by", "VARCHAR(64)"), ("tokens_valid_after", "TIMESTAMPTZ")):
            await conn.execute(text(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} {ddl}"))
        await conn.execute(text("UPDATE users SET password_set = TRUE WHERE password_set IS NULL"))
        # WASA #7 (2026-10-21): forced password change for operator-chosen passwords.
        await conn.execute(text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT FALSE"))
    await _identity_indexes()
    # The catalogue seed reads SaaS columns (billing_plan_catalog.modules …) that
    # init_saas adds with ALTER … IF NOT EXISTS. On an existing database the seed
    # ran first and crashed boot with UndefinedColumnError (found by the S3
    # migration check, 2026-10-15). init_saas is idempotent; main.py calls it again.
    from saas_db import init_saas  # local import: saas_db imports this module
    await init_saas()
    # Seed the editable plan / add-on catalogue once (superadmin edits it afterwards).
    await _seed_catalog()


async def _identity_indexes():
    """E-mail is unique PER ORG, and an employee has at most one login per org.

    Each step runs in its own transaction so that pre-existing duplicate data
    makes that one index fail (and get reported in the boot log) instead of
    stopping the service. The old GLOBAL unique index on users.email is dropped
    only AFTER the per-org one exists, so uniqueness is never left unguarded.
    """
    from sqlalchemy import text
    steps = [
        ("users_org_email_uk",
         "CREATE UNIQUE INDEX IF NOT EXISTS users_org_email_uk ON users (org_id, lower(email))"),
        ("users_org_employee_uk",
         "CREATE UNIQUE INDEX IF NOT EXISTS users_org_employee_uk ON users (org_id, employee_id) "
         "WHERE employee_id IS NOT NULL"),
        ("users_email_lower_idx", "CREATE INDEX IF NOT EXISTS users_email_lower_idx ON users (lower(email))"),
    ]
    ok = set()
    for name, ddl in steps:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(ddl))
            ok.add(name)
        except Exception as exc:                      # duplicates already in the table
            print(f"[auth] could not create index {name}: {exc.__class__.__name__} — "
                  f"fix the duplicate rows and restart", flush=True)
    if "users_org_email_uk" not in ok:
        return
    try:
        async with engine.begin() as conn:
            # Was created by `unique=True` on the column: a constraint on some
            # databases, a unique index (ix_users_email) on others.
            await conn.execute(text("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_email_key"))
            row = (await conn.execute(text(
                "SELECT indexdef FROM pg_indexes WHERE tablename = 'users' AND indexname = 'ix_users_email'"))).first()
            if row and "UNIQUE" in (row[0] or "").upper():
                await conn.execute(text("DROP INDEX IF EXISTS ix_users_email"))
                await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_users_email ON users (email)"))
    except Exception as exc:
        print(f"[auth] could not relax the global e-mail index: {exc.__class__.__name__}", flush=True)


async def _seed_catalog():
    from sqlalchemy import select
    from models import BillingAddon, BillingPlan
    from pricing import ADDONS, PEPM_PLANS
    async with SessionLocal() as s:
        have_p = {(r.country, r.plan_key) for r in (await s.execute(select(BillingPlan))).scalars().all()}
        have_a = {(r.country, r.addon_key) for r in (await s.execute(select(BillingAddon))).scalars().all()}
        for country, plans in PEPM_PLANS.items():
            for key, p in plans.items():
                if (country, key) not in have_p:
                    s.add(BillingPlan(country=country, plan_key=key, label=p["label"], pepm=p["pepm"],
                                      min_monthly=p["min_monthly"], custom=p["custom"], includes=p["includes"],
                                      free_upto_employees=p["free_upto_employees"], free_months=p["free_months"],
                                      annual_discount_pct=p["annual_discount_pct"], max_employees=p["max_employees"],
                                      sort=p["sort"]))
        for country, addons in ADDONS.items():
            for key, a in addons.items():
                if (country, key) not in have_a:
                    s.add(BillingAddon(country=country, addon_key=key, label=a["label"], kind=a["kind"],
                                       price=a["price"], waived_on_annual=a["waived_on_annual"], sort=a["sort"]))
        await s.commit()
