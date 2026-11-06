"""Integrations service — shared helpers: identity, roles, calls to sibling services.

Roles use S1's one vocabulary (``shared.utils.roles``):
    HR_WRITE  org_admin + HR          WhatsApp opt-ins recorded by HR
    OFFICE    org_admin + HR + payroll  read statutory checklists / payout batches
    FINANCE   org_admin + payroll       move money (payouts), mark statutory dues paid
"""
from __future__ import annotations

import os

import httpx
from fastapi import HTTPException, Request

from shared.utils.config import settings
from shared.utils.internal import internal_headers
from shared.utils.roles import FINANCE, HR_WRITE, OFFICE, require_roles  # noqa: F401  (re-exported)

SERVICE = "integrations"
TRANSPORT: httpx.AsyncBaseTransport | None = None      # tests inject a MockTransport for sibling-service calls


def org_of(request: Request) -> str:
    org = request.headers.get("x-org-id")
    if not org:
        raise HTTPException(401, "Not authenticated")
    return org


def actor(request: Request) -> str:
    return request.headers.get("x-user-email") or request.headers.get("x-user-id") or ""


def app_base() -> str:
    return (os.getenv("APP_BASE_URL") or "http://localhost:5173").rstrip("/")


def public_api_base() -> str:
    """Where providers reach Lexora HRM webhooks (the gateway's public URL)."""
    return (os.getenv("LEXORA_PUBLIC_API_URL") or os.getenv("APP_BASE_URL", "http://localhost:8200") + "/api").rstrip("/")


async def sibling(method: str, base: str, path: str, org_id: str, *, role: str = "org_admin", json=None,
                  params: dict | None = None, timeout: float = 30.0) -> httpx.Response:
    """A call to another Lexora HRM service with the internal service identity (never the user's token)."""
    async with httpx.AsyncClient(timeout=timeout, transport=TRANSPORT) as c:
        return await c.request(method, f"{base.rstrip('/')}{path}", json=json, params=params,
                               headers=internal_headers(org_id, SERVICE, role))


def payroll_url() -> str:
    return settings.PAYROLL_URL


def employee_url() -> str:
    return settings.EMPLOYEE_URL


def upstream_error(r: httpx.Response, what: str) -> HTTPException:
    try:
        d = r.json().get("detail")
    except ValueError:
        d = None
    if isinstance(d, dict):
        d = d.get("message") or d.get("error")
    return HTTPException(502 if r.status_code >= 500 else (r.status_code if r.status_code in (400, 404, 409, 422) else 502),
                         f"{what}: {d or ('HTTP ' + str(r.status_code))}")
