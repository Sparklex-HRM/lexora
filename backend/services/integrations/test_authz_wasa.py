"""WASA 2026-10-21 — WhatsApp event templates = admin (Integrations screen)."""
import asyncio

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import whatsapp_routes
from shared.utils.providers import store

ORG = "11111111-1111-1111-1111-111111111111"


def _req(role):
    return Request({"type": "http", "method": "GET", "path": "/", "query_string": b"",
                    "headers": [(b"x-role", role.encode()), (b"x-org-id", ORG.encode())]})


@pytest.mark.parametrize("role", ["employee", "manager", "accountant", "payroll_admin", "hr_admin", "hr_manager"])
def test_whatsapp_events_admin_only(role):
    with pytest.raises(HTTPException) as ex:
        asyncio.run(whatsapp_routes.events(_req(role)))
    assert ex.value.status_code == 403


def test_whatsapp_events_admin_ok(monkeypatch):
    async def load(org, key):
        return {"config": {}, "enabled": False, "mode": "sandbox"}
    monkeypatch.setattr(store, "load", load)
    out = asyncio.run(whatsapp_routes.events(_req("org_admin")))
    assert out["events"] and out["mode"] == "sandbox"
