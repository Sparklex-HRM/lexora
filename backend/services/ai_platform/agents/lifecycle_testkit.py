"""Test kit for the lifecycle agents' tests (SQLite in memory, no network, no SMTP, no model). Not collected by pytest
(no ``test_`` prefix); the test modules import ``db`` (an autouse fixture) and the helpers."""
import asyncio
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SVC = os.path.abspath(os.path.join(HERE, ".."))
BACKEND = os.path.abspath(os.path.join(SVC, "..", ".."))
for p in (BACKEND, SVC):
    if p not in sys.path:
        sys.path.insert(0, p)
os.environ.pop("ANTHROPIC_API_KEY", None)
for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
    os.environ[k] = ""

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from shared.utils import org_settings  # noqa: E402
from shared.utils import platform_models as pm  # noqa: E402

import agentic_proposals as PR  # noqa: E402
import agentic_state as ST  # noqa: E402
from agents import lifecycle_common as LC  # noqa: E402,F401  (registers the item table on PlatformBase)

ORG = "55555555-5555-5555-5555-555555555555"
HR = PR.Approver("u-hr", "hr@x.ca", "hr_admin")
PAY = PR.Approver("u-pay", "pay@x.ca", "payroll_admin")
CEO = PR.Approver("u-ceo", "ceo@x.ca", "org_admin")
_LOOP: dict = {}

E1, E2, MGR, IT_USER = ("11111111-0000-0000-0000-000000000001", "11111111-0000-0000-0000-000000000002",
                        "11111111-0000-0000-0000-000000000099", "u-it")
LOGINS = {"by_employee": {E1: "u-e1", MGR: "u-mgr"}, "hr": ["u-hr"], "payroll": ["u-pay"], "org_admin": ["u-ceo"]}


def run(coro):
    return _LOOP["loop"].run_until_complete(coro)


@pytest.fixture(autouse=True)
def db(monkeypatch):
    _LOOP["loop"] = asyncio.new_event_loop()
    eng = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    sm = async_sessionmaker(eng, expire_on_commit=False)

    async def mk():
        async with eng.begin() as c:
            await c.run_sync(lambda cc: pm.PlatformBase.metadata.create_all(cc))
    run(mk())
    pm.set_sessionmaker(sm)
    org_settings.invalidate()

    async def fake_addons(org):
        return ["agentic"]
    monkeypatch.setattr(ST, "org_addons", fake_addons)
    yield {"sm": sm}
    pm.set_sessionmaker(None)
    org_settings.invalidate()
    run(eng.dispose())
    _LOOP["loop"].close()


def go_live():
    run(ST.set_mode(ORG, "live", actor_id="u-ceo", actor_email="ceo@x.ca", reason="Lifecycle agents pilot approved by the CEO"))
    org_settings.invalidate()


def tasks(agent: str):
    import agentic_tasks as TK
    return run(TK.list_tasks(ORG, agent=agent))


def assert_fits_columns():
    """Every VARCHAR(n) value the agents wrote fits n — SQLite does not enforce lengths, Postgres does (the case key
    once went into ai_agent_tasks.run_ref VARCHAR(36) and every LIVE chase failed on Postgres)."""
    from sqlalchemy import String, select
    import agentic_models as AM
    bad = []
    tables = [m for m in (getattr(AM, n) for n in dir(AM)) if isinstance(m, type) and hasattr(m, "__table__")] + [LC.LifecycleItem]

    async def scan():
        async with pm.session() as s:
            for m in tables:
                cols = [c for c in m.__table__.columns if isinstance(c.type, String) and c.type.length]
                if not cols:
                    continue
                try:
                    rows = (await s.execute(select(m))).scalars().all()
                except Exception:                                # noqa: BLE001 — table not created in this test
                    await s.rollback()
                    continue
                for r in rows:
                    for c in cols:
                        v = getattr(r, c.key, None)
                        if isinstance(v, str) and len(v) > c.type.length:
                            bad.append(f"{m.__tablename__}.{c.name} len {len(v)} > {c.type.length}: {v[:60]}")
    run(scan())
    assert not bad, bad
