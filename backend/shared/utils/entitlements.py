"""Org entitlements (plan → modules, subscription read-only) readable by ANY service.

The gateway enforces the module paywall from the JWT `modules` claim. Requests that carry no
login (biometric device push: /iclock/*, /time/device-push/*, /time/biometric/webhook/*) never
pass that check, so the receiving service asks here instead, from the org bound by the device
registration. Same rules as services/auth (constants.resolve_modules + subscription.org_modules)
and api-gateway/saas_gate.read_only — test_entitlements.py asserts the copies stay equal.

Integration 2026-10-23.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from sqlalchemy import text

# ── Subscriptions & modules (Phase 4) ─────────────────────────────────────────
# The canonical module keys the sidebar/routes gate on. "core" is always on.
MODULES = ["core", "payroll", "time", "leave", "benefits", "talent", "ops",
           "analytics", "ai", "compliance"]            # enterprise = every module

# Plan → default enabled modules. An org's explicit `modules` list overrides this
# (so add-ons / trials are just flag edits). Illustrative tiers, not final pricing.
PLAN_MODULES = {
    "starter":    ["core", "payroll"],
    "growth":     ["core", "payroll", "time", "leave", "benefits", "ops", "compliance"],
    "enterprise": MODULES,
}

READ_ONLY_STATUSES = {"cancelled", "expired"}
TIMED_STATUSES = {"trialing", "past_due"}


def resolve_modules(plan: str | None, modules: list | None) -> list:
    """Enabled modules for an org: an explicit list wins; else the plan default;
    'core' is always included."""
    base = list(modules) if modules else PLAN_MODULES.get((plan or "growth"), PLAN_MODULES["growth"])
    if "core" not in base:
        base = ["core"] + base
    return base


def _aware(dt):
    if dt is None:
        return None
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def read_only(status: str | None, access_until, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    st = (status or "active").lower()
    if st in READ_ONLY_STATUSES:
        return True
    if st in TIMED_STATUSES:
        au = _aware(access_until)
        return au is not None and now >= au
    return False


def refusal_for(row: dict | None, module: str, now: datetime | None = None) -> str | None:
    """Pure rule: None = allowed, else the reason (logged only, never sent to the caller)."""
    if row is None:
        return "organisation not found"
    if row.get("is_active") is False:
        return "organisation inactive"
    mods = resolve_modules(row.get("plan"), row.get("modules") or row.get("plan_modules"))
    if module not in mods:
        return f"module '{module}' not subscribed"
    if read_only(row.get("subscription_status"), row.get("access_until"), now):
        return "subscription read-only"
    return None


_CACHE: dict = {}            # (org, module) → (expires, reason)
TTL_SEC = 30.0


async def org_module_refusal(db, org_id, module: str) -> str | None:
    """None when the org may use `module` for a WRITE; else a reason. Fails CLOSED when the
    org row cannot be read (a device push is a write with no login behind it)."""
    key = (str(org_id), module)
    hit = _CACHE.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    try:
        r = (await db.execute(text(
            "SELECT o.is_active, o.plan, o.modules, o.subscription_status, o.access_until, p.modules "
            "FROM organizations o LEFT JOIN billing_plan_catalog p "
            "  ON p.country = UPPER(COALESCE(o.country, 'CA')) AND p.plan_key = COALESCE(o.plan, 'growth') "
            "WHERE o.id = CAST(:o AS uuid) LIMIT 1"), {"o": str(org_id)})).first()
    except Exception:                                        # noqa: BLE001 — fail closed, not cached
        try:
            await db.rollback()
        except Exception:                                    # noqa: BLE001
            pass
        return "entitlement lookup failed"
    row = None if r is None else {"is_active": r[0], "plan": r[1], "modules": r[2], "subscription_status": r[3],
                                  "access_until": r[4], "plan_modules": r[5]}
    reason = refusal_for(row, module)
    _CACHE[key] = (time.monotonic() + TTL_SEC, reason)
    return reason


def forget() -> None:
    _CACHE.clear()
