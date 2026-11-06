"""Engagement — shared plumbing (identity, roster, settings, attachments). Stream G4."""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import HTTPException, Request
from sqlalchemy import select

sys.path.append("/app")
from shared.utils import docstore  # noqa: E402
from shared.utils.config import settings as cfg  # noqa: E402

import engage_logic as L  # noqa: E402
from engage_models import EngSettings  # noqa: E402

HR_ROLES = {"superadmin", "org_admin", "hr_admin", "hr_manager"}
AGENT_ROLES = HR_ROLES | {"payroll_admin"}          # people who work tickets


def now() -> datetime:
    return datetime.now(timezone.utc)


def org_of(request: Request) -> str:
    org = request.headers.get("x-org-id")
    if not org:
        raise HTTPException(401, "Missing organization context")
    return org


def role_of(request: Request) -> str:
    return (request.headers.get("x-role") or "").lower()


def uid_of(request: Request) -> str | None:
    return request.headers.get("x-user-id") or None


def emp_of(request: Request) -> str | None:
    return request.headers.get("x-employee-id") or None


def actor_of(request: Request) -> str:
    return request.headers.get("x-user-email") or uid_of(request) or "unknown"


def is_hr(request: Request) -> bool:
    return role_of(request) in HR_ROLES


def is_agent(request: Request) -> bool:
    return role_of(request) in AGENT_ROLES


def is_manager(request: Request) -> bool:
    return role_of(request) == "manager"


def require_hr(request: Request, what: str = "do this"):
    if not is_hr(request):
        raise HTTPException(403, f"Only HR can {what}")


def fwd(request: Request) -> dict:
    return {k: request.headers.get(k, "") for k in
            ("x-org-id", "x-user-id", "x-user-email", "x-role", "x-employee-id", "x-branch-id") if request.headers.get(k)}


async def employees(request: Request) -> dict[str, dict]:
    """id → employee (never raises — empty on failure)."""
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(f"{cfg.EMPLOYEE_URL}/employees", headers=fwd(request))
            if r.status_code == 200 and isinstance(r.json(), list):
                return {e["id"]: e for e in r.json()}
    except (httpx.ConnectError, httpx.TimeoutException, ValueError):
        pass
    return {}


def emp_name(e: dict | None) -> str | None:
    if not e:
        return None
    return f"{e.get('first_name') or ''} {e.get('last_name') or ''}".strip() or e.get("email")


def iso(v):
    return v.isoformat() if v is not None else None


def to_uuid(v, what="record") -> uuid.UUID:
    try:
        return uuid.UUID(str(v))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(404, f"{what.capitalize()} not found")


async def get_settings(db, org: str) -> EngSettings:
    row = (await db.execute(select(EngSettings).where(EngSettings.org_id == org))).scalar_one_or_none()
    if row is None:
        row = EngSettings(org_id=uuid.UUID(org), helpdesk_categories=list(L.DEFAULT_CATEGORIES),
                          anonymity_threshold=L.DEFAULT_ANONYMITY_THRESHOLD, kudos_values=list(L.DEFAULT_VALUES))
        db.add(row)
        await db.flush()
    return row


def settings_out(s: EngSettings) -> dict:
    return {"helpdesk_categories": s.helpdesk_categories or list(L.DEFAULT_CATEGORIES),
            "anonymity_threshold": s.anonymity_threshold or L.DEFAULT_ANONYMITY_THRESHOLD,
            "kudos_values": s.kudos_values or list(L.DEFAULT_VALUES),
            "kudos_needs_approval": bool(s.kudos_needs_approval),
            # D7
            "kudos_points_default": int(s.kudos_points_default if s.kudos_points_default is not None else 10),
            "manager_points_per_month": int(s.manager_points_per_month if s.manager_points_per_month is not None else 100),
            "lms_remind_days": int(s.lms_remind_days if s.lms_remind_days is not None else 3)}


async def store_doc(db, org: str, employee_id: str | None, file_name: str, data: bytes, doc_type: str, actor: str):
    try:
        cols = docstore.check_upload(file_name, data)
    except docstore.DocError as e:
        raise HTTPException(e.status, str(e))
    d = docstore.StoredDocument(org_id=uuid.UUID(org), employee_id=uuid.UUID(employee_id) if employee_id else None,
                                doc_type=doc_type, uploaded_by=actor, uploaded_by_name=actor, **cols)
    db.add(d)
    await db.flush()
    return d


async def load_doc(db, org: str, doc_id: str):
    d = (await db.execute(select(docstore.StoredDocument).where(
        docstore.StoredDocument.id == to_uuid(doc_id, "file"), docstore.StoredDocument.org_id == org,
        docstore.StoredDocument.deleted_at.is_(None)))).scalar_one_or_none()
    if not d:
        raise HTTPException(404, "File not found")
    return d
