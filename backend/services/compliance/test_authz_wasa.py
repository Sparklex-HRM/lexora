"""WASA 2026-10-21 — WCB rates / premium estimate = back office (org_admin + HR + payroll); /compliance/ping = HR."""
import asyncio
import uuid

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import main
import routers

ORG = str(uuid.uuid4())


def _req(role):
    return Request({"type": "http", "method": "GET", "path": "/", "query_string": b"",
                    "headers": [(b"x-role", role.encode()), (b"x-org-id", ORG.encode())]})


class _Res:
    def scalars(self):
        return self

    def all(self):
        return []


class FakeDB:
    async def execute(self, *_a, **_k):
        return _Res()


@pytest.mark.parametrize("role", ["employee", "manager", "accountant"])
def test_wcb_and_ping_denied(role):
    for call in (lambda: routers.get_wcb_rates(_req(role), 2026, FakeDB()),
                 lambda: routers.wcb_assessment(_req(role), 2026, FakeDB()),
                 lambda: main.ping(_req(role))):
        with pytest.raises(HTTPException) as ex:
            asyncio.run(call())
        assert ex.value.status_code == 403


@pytest.mark.parametrize("role", ["org_admin", "hr_admin", "hr_manager", "payroll_admin"])
def test_wcb_rates_office_ok(role):
    out = asyncio.run(routers.get_wcb_rates(_req(role), 2026, FakeDB()))
    assert out["provinces"]


def test_ping_hr_ok():
    assert asyncio.run(main.ping(_req("hr_admin")))["status"] == "live"
