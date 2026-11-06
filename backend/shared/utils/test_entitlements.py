"""Entitlement rule used by login-less device push (integration 2026-10-23)."""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(os.path.dirname(_HERE))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from shared.utils import entitlements as ent  # noqa: E402

NOW = datetime(2026, 10, 23, tzinfo=timezone.utc)


def test_plan_default_and_override():
    assert ent.refusal_for({"is_active": True, "plan": "growth"}, "time", NOW) is None
    assert "not subscribed" in ent.refusal_for({"is_active": True, "plan": "starter"}, "time", NOW)
    assert ent.refusal_for({"is_active": True, "plan": "starter", "modules": ["payroll", "time"]}, "time", NOW) is None
    assert "not subscribed" in ent.refusal_for({"is_active": True, "plan": "growth", "modules": ["payroll"]}, "time", NOW)
    # plan catalogue row (DB) wins over the constant when the org has no explicit list
    assert "not subscribed" in ent.refusal_for({"is_active": True, "plan": "growth", "plan_modules": ["payroll"]}, "time", NOW)


def test_inactive_missing_and_read_only():
    assert ent.refusal_for(None, "time", NOW) == "organisation not found"
    assert ent.refusal_for({"is_active": False, "plan": "enterprise"}, "time", NOW) == "organisation inactive"
    assert ent.refusal_for({"is_active": True, "plan": "enterprise", "subscription_status": "expired"}, "time", NOW)
    past = NOW - timedelta(days=1)
    assert ent.refusal_for({"is_active": True, "plan": "enterprise", "subscription_status": "trialing",
                            "access_until": past}, "time", NOW)
    assert ent.refusal_for({"is_active": True, "plan": "enterprise", "subscription_status": "trialing",
                            "access_until": NOW + timedelta(days=3)}, "time", NOW) is None


def test_lookup_failure_fails_closed():
    class Boom:
        async def execute(self, *a, **k):
            raise RuntimeError("db down")

        async def rollback(self):
            pass
    ent.forget()
    assert ent.org_module_refusal and asyncio.run(ent.org_module_refusal(Boom(), "00000000-0000-0000-0000-000000000001", "time"))


def test_copies_match_auth_and_gateway():
    auth = os.path.join(_BACKEND, "services", "auth", "constants.py")
    gw = os.path.join(_BACKEND, "api-gateway", "saas_gate.py")
    ns: dict = {}
    exec(compile(open(auth, encoding="utf-8").read(), auth, "exec"), ns)
    assert ns["MODULES"] == ent.MODULES and ns["PLAN_MODULES"] == ent.PLAN_MODULES
    src = open(gw, encoding="utf-8").read()
    assert 'READ_ONLY_STATUSES = {"cancelled", "expired"}' in src and 'TIMED_STATUSES = {"trialing", "past_due"}' in src
