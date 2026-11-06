"""Auth service — subscription state, trial, read-only mode and plan entitlements.

States (organizations.subscription_status):

    trialing   self-serve sign-up; full access until trial_ends_at
    active     paid / provider subscription active
    past_due   a charge failed; full access for GRACE_DAYS, then read-only
    cancelled  the subscription ended; read-only
    expired    the trial ended without a subscription; read-only
    NULL       org created before self-serve billing (platform / API) → "active"

READ-ONLY never locks anyone out: sign-in, every GET (pay stubs, T4 / RL-1, ROE,
reports, exports), statutory downloads produced by POST, privacy requests (PIPEDA
access / correction) and the billing / account screens keep working.
Only creating or changing HR / payroll data is refused with 402
`subscription_read_only`. The gateway enforces it (api-gateway/saas_gate.py)
from organizations.access_until + subscription_status; the rule there is the same
as `access_mode()` below (both are unit-tested against the same cases).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from constants import PLAN_MODULES, resolve_modules

TRIAL_DAYS = int(os.getenv("LEXORA_TRIAL_DAYS", "14"))
GRACE_DAYS = int(os.getenv("LEXORA_PAST_DUE_GRACE_DAYS", "7"))

STATUSES = ("trialing", "active", "past_due", "cancelled", "expired")
READ_ONLY_STATUSES = {"cancelled", "expired"}
TIMED_STATUSES = {"trialing", "past_due"}        # full access until access_until


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def access_mode(status: str | None, access_until: datetime | None, now: datetime | None = None) -> str:
    """'full' or 'read_only'. Same rule as the gateway's saas_gate.read_only()."""
    now = now or _now()
    st = (status or "active").lower()
    if st in READ_ONLY_STATUSES:
        return "read_only"
    if st in TIMED_STATUSES:
        au = _aware(access_until)
        if au is not None and now >= au:
            return "read_only"
    return "full"


def effective_status(status: str | None, access_until: datetime | None, now: datetime | None = None) -> str:
    """What the screens call it: a trial whose time ran out reads 'expired'."""
    st = (status or "active").lower()
    if st == "trialing" and access_mode(st, access_until, now) == "read_only":
        return "expired"
    return st


def start_trial(org, now: datetime | None = None, days: int | None = None) -> None:
    now = now or _now()
    org.subscription_status = "trialing"
    org.trial_ends_at = now + timedelta(days=days if days is not None else TRIAL_DAYS)
    org.access_until = org.trial_ends_at


def mark_active(org, period_end: datetime | None = None) -> None:
    org.subscription_status = "active"
    org.access_until = None
    org.past_due_since = None
    if period_end:
        org.current_period_end = period_end


def mark_past_due(org, now: datetime | None = None) -> None:
    now = now or _now()
    if org.subscription_status != "past_due":
        org.past_due_since = now
        org.access_until = now + timedelta(days=GRACE_DAYS)
    org.subscription_status = "past_due"


def mark_cancelled(org) -> None:
    org.subscription_status = "cancelled"
    org.access_until = None


def banner(status: str | None, access_until: datetime | None, now: datetime | None = None) -> dict | None:
    """The yellow / red strip the app shows on every screen (None = nothing to say)."""
    now = now or _now()
    st = effective_status(status, access_until, now)
    mode = access_mode(status, access_until, now)
    au = _aware(access_until)
    days = None if au is None else max((au - now).days + (1 if (au - now).seconds else 0), 0)
    if mode == "read_only":
        what = {"expired": "Your free trial has ended", "cancelled": "Your subscription has ended",
                "past_due": "Your payment is overdue"}.get(st, "Your subscription is inactive")
        return {"level": "error", "mode": mode, "status": st,
                "text": f"{what}. Lexora HRM is in READ-ONLY mode: you can still sign in, view and download "
                        "payslips, registers, statutory files and reports, but cannot add or change data. "
                        "Choose a plan in Plan & billing to continue.", "days_left": 0}
    if st == "trialing":
        return {"level": "warning" if (days or 0) <= 3 else "info", "mode": mode, "status": st, "days_left": days,
                "text": f"Free trial: {days} day(s) left. Choose a plan in Plan & billing to keep full access."}
    if st == "past_due":
        return {"level": "warning", "mode": mode, "status": st, "days_left": days,
                "text": f"Your last payment failed. Full access continues for {days} more day(s); "
                        "update the payment in Plan & billing."}
    return None


# ── Entitlements (plans stored as DATA in billing_plan_catalog) ──────────────

async def plan_row(db: AsyncSession, country: str | None, plan: str | None):
    from models import BillingPlan
    return (await db.execute(select(BillingPlan).where(
        BillingPlan.country == "CA",
        BillingPlan.plan_key == (plan or "growth")))).scalar_one_or_none()


async def org_modules(db: AsyncSession, org) -> list:
    """Module list for an org: org override → plan row modules (DB) → constant."""
    if org is None:
        return resolve_modules(None, None)
    if org.modules:
        return resolve_modules(org.plan, org.modules)
    try:
        row = await plan_row(db, org.country, org.plan)
    except Exception:
        row = None
    return resolve_modules(org.plan, row.modules if row is not None and row.modules else None)


async def employee_limit(db: AsyncSession, org) -> int | None:
    """Max employees for the org's plan (None = unlimited)."""
    try:
        row = await plan_row(db, org.country, org.plan)
    except Exception:
        return None
    return row.max_employees if row is not None else None


async def employee_count(db: AsyncSession, org_id) -> int:
    try:
        return int((await db.execute(text(
            "SELECT count(*) FROM employees WHERE org_id = :o AND is_active = TRUE "
            "AND COALESCE(status, 'active') <> 'terminated'"), {"o": org_id})).scalar() or 0)
    except Exception:
        return 0


async def record_change(db: AsyncSession, org, *, from_status, from_plan, source: str,
                        note: str | None = None, actor: str | None = None) -> None:
    from saas_models import SubscriptionChange
    if from_status == org.subscription_status and from_plan == org.plan:
        return
    db.add(SubscriptionChange(org_id=org.id, from_status=from_status, to_status=org.subscription_status,
                              from_plan=from_plan, to_plan=org.plan, source=source, note=note, actor=actor))


async def expire_trials(db: AsyncSession, now: datetime | None = None) -> int:
    """Flip trials whose time ran out to 'expired' (the gateway already treats them
    as read-only from access_until; this keeps the stored status honest)."""
    from models import Organization
    now = now or _now()
    rows = (await db.execute(select(Organization).where(
        Organization.subscription_status == "trialing", Organization.access_until.is_not(None),
        Organization.access_until <= now))).scalars().all()
    for o in rows:
        o.subscription_status = "expired"
        await record_change(db, o, from_status="trialing", from_plan=o.plan, source="expiry",
                            note="trial ended without a subscription")
    if rows:
        await db.commit()
    return len(rows)


def plan_catalog_modules(plans_by_key: dict) -> dict:
    """{plan_key: modules} from catalogue dicts, falling back to the constants."""
    return {k: (v.get("modules") or PLAN_MODULES.get(k) or PLAN_MODULES["growth"]) for k, v in plans_by_key.items()}


async def count_orgs_by_status(db: AsyncSession) -> dict:
    from models import Organization
    rows = (await db.execute(select(Organization.subscription_status, func.count(Organization.id))
                             .group_by(Organization.subscription_status))).all()
    return {(s or "legacy_active"): n for s, n in rows}
