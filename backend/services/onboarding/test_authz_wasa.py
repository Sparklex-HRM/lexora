"""WASA 2026-10-21 — /onboarding/ping = HR (contract: /onboarding is HR)."""
import asyncio

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import routers


def _req(role):
    return Request({"type": "http", "method": "GET", "path": "/", "query_string": b"",
                    "headers": [(b"x-role", role.encode()), (b"x-org-id", b"o")]})


@pytest.mark.parametrize("role", ["employee", "manager", "accountant", "payroll_admin"])
def test_ping_denied(role):
    with pytest.raises(HTTPException) as ex:
        asyncio.run(routers.ping(_req(role)))
    assert ex.value.status_code == 403


def test_ping_hr_ok():
    assert asyncio.run(routers.ping(_req("hr_manager")))["ok"] is True
