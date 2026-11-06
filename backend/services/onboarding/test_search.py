"""Global search — onboarding (/onboarding/search): HR only, org-scoped.
Run:  cd backend/services/onboarding && PYTHONPATH=../../ python -m pytest -q test_search.py
"""
import asyncio
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

import database
import models  # noqa: F401
from models import OnboardingSession
from search_routes import router

ORG, OTHER = uuid.uuid4(), uuid.uuid4()


async def _seed():
    async with database.engine.begin() as c:
        await c.run_sync(database.Base.metadata.create_all)
    async with database.SessionLocal() as s:
        for org, first in ((ORG, "Anil"), (OTHER, "Anil")):
            s.add(OnboardingSession(org_id=org, invite_token=uuid.uuid4().hex, status="invited", first_name=first,
                                    last_name="Joshi", email=f"anil.{org.hex[:4]}@new.example", job_title="Analyst"))
        await s.commit()


asyncio.run(_seed())
app = FastAPI()
app.include_router(router)
client = TestClient(app)


def hits(role):
    r = client.get("/onboarding/search", params={"q": "anil"}, headers={"X-Org-Id": str(ORG), "X-Role": role})
    assert r.status_code == 200, r.text
    return r.json()["hits"]


def test_hr_only_and_org_scoped():
    h = hits("hr_admin")
    assert len(h) == 1 and h[0]["title"] == "Onboarding — Anil Joshi" and h[0]["url"].startswith("/onboarding")
    for role in ("employee", "manager", "payroll_admin", "accountant"):
        assert hits(role) == [], role
