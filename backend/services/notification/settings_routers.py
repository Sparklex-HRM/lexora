"""Company settings on the server — `/settings` (served by the notification service).

* `GET  /settings`              every group the caller may read
* `GET  /settings/{key}`        one group or one field (`numbering.employee_prefix`)
* `PUT  /settings/{group}`      org admin only; every change lands in the audit trail
* `GET  /settings/audit`        org admin
* `GET/PUT/DELETE /settings/logo`   the logo, stored as bytes in the database

Registration numbers are stored and shown exactly as typed — nothing here checks them
against any authority. Payroll calendars, pay groups, statutory establishment details
and holidays keep living on their own screens and are NOT copied here.
"""
import base64
import binascii
import re
import sys

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils import docstore  # noqa: E402
from shared.utils.email import dangerous_url  # noqa: E402
from shared.utils import org_settings as os_  # noqa: E402
from shared.utils import platform_models as pm  # noqa: E402
from shared.utils.platform_models import OrgLogo, OrgSetting, OrgSettingAudit  # noqa: E402

from database import get_db  # noqa: E402

router = APIRouter(prefix="/settings", tags=["settings"])

LOGO_TYPES = {"image/png", "image/jpeg", "image/webp"}      # SVG refused (script-capable); bytes sniffed
_LOGO_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/svg+xml": "svg"}
LOGO_MAX_BYTES = 512 * 1024
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PREFIX = re.compile(r"^[A-Za-z0-9/_-]{0,12}$")


def _org(request: Request) -> str:
    org = request.headers.get("x-org-id")
    if not org:
        raise HTTPException(401, "Missing organization context")
    return org


def _role(request: Request) -> str:
    return request.headers.get("x-role", "") or ""


def _s(v, n=200) -> str:
    return str(v if v is not None else "").strip()[:n]


def _days(v, name) -> list[int]:
    if not isinstance(v, list):
        raise ValueError(f"{name} must be a list of days")
    out = sorted({int(x) for x in v})
    if any(x < 0 or x > 6 for x in out):
        raise ValueError(f"{name}: days run from 0 (Monday) to 6 (Sunday)")
    return out


def clean(group: str, value) -> dict:
    """Validate + normalise one group's value. Raises ValueError with a plain message."""
    if not isinstance(value, dict):
        raise ValueError("The value must be an object")
    if group == "company":
        addresses = []
        for a in (value.get("addresses") or [])[:10]:
            if not isinstance(a, dict):
                continue
            row = {k: _s(a.get(k)) for k in ("label", "line1", "line2", "city", "state", "postal_code", "country")}
            if any(row.values()):
                addresses.append(row)
        email = _s(value.get("email"))
        if email and not _EMAIL.match(email):
            raise ValueError("Company e-mail does not look like an e-mail address")
        if dangerous_url(value.get("website")):
            raise ValueError("Website must be a web address such as https://example.com")
        return {"legal_name": _s(value.get("legal_name")), "trade_name": _s(value.get("trade_name")),
                "phone": _s(value.get("phone"), 40), "email": email, "website": _s(value.get("website")),
                "addresses": addresses}
    if group == "registrations":
        items = []
        for it in (value.get("items") or [])[:30]:
            if isinstance(it, dict) and (_s(it.get("label")) or _s(it.get("value"))):
                items.append({"label": _s(it.get("label"), 80), "value": _s(it.get("value"), 80)})
        return {"items": items}
    if group == "locale":
        country, currency = _s(value.get("country"), 10).upper(), _s(value.get("currency"), 10).upper()
        if country and country != "CA":
            raise ValueError("Lexora HRM is Canada-only: the country is CA")
        if currency and currency != "CAD":
            raise ValueError("Payroll currency is CAD (Canadian dollars)")
        try:
            month = int(value.get("fy_start_month") or 0)
        except (TypeError, ValueError):
            month = 0
        if not 1 <= month <= 12:
            raise ValueError("Financial year start month must be 1 to 12")
        out = {"country": country, "currency": currency, "fy_start_month": month}
        tz = _s(value.get("timezone"), 64)
        if tz:                       # optional; unset = the default America/Toronto (shared/utils/org_time.py)
            from shared.utils.org_time import valid_timezone
            if not valid_timezone(tz):
                raise ValueError("Time zone must be a name like America/Toronto")
            out["timezone"] = tz
        return out
    if group == "work_week":
        working, offs = _days(value.get("working_days") or [], "Working days"), _days(value.get("weekly_offs") or [], "Weekly offs")
        if set(working) & set(offs):
            raise ValueError("A day cannot be both a working day and a weekly off")
        if not working:
            raise ValueError("Pick at least one working day")
        return {"working_days": working, "weekly_offs": offs}
    if group == "work_hours":
        start, end = _s(value.get("start"), 5), _s(value.get("end"), 5)
        if not _HHMM.match(start) or not _HHMM.match(end):
            raise ValueError("Start and end time must look like 09:00")
        try:
            hours, brk = float(value.get("hours_per_day") or 0), int(value.get("break_minutes") or 0)
        except (TypeError, ValueError):
            raise ValueError("Hours per day and break minutes must be numbers")
        if not 0 < hours <= 24:
            raise ValueError("Hours per day must be more than 0 and at most 24")
        if not 0 <= brk <= 480:
            raise ValueError("Break minutes must be 0 to 480")
        return {"start": start, "end": end, "hours_per_day": hours, "break_minutes": brk}
    if group == "notification_sender":
        name, reply = _s(value.get("sender_name"), 100), _s(value.get("reply_to"))
        if "\n" in name or "\r" in name or "<" in name or ">" in name:
            raise ValueError("Sender name cannot contain line breaks or angle brackets")
        if reply and not _EMAIL.match(reply):
            raise ValueError("Reply-to does not look like an e-mail address")
        return {"sender_name": name, "reply_to": reply}
    if group == "numbering":
        out = {}
        for k in ("employee_prefix", "requisition_prefix", "offer_prefix", "letter_prefix",
                  "revision_prefix", "exit_prefix"):
            v = _s(value.get(k), 40)
            if not _PREFIX.match(v):
                raise ValueError("A prefix may use letters, digits, - _ / and at most 12 characters")
            out[k] = v
        try:
            pad = int(value.get("padding") or 0)
        except (TypeError, ValueError):
            pad = 0
        if not 1 <= pad <= 10:
            raise ValueError("Number of digits must be 1 to 10")
        out["padding"] = pad
        return out
    if group == "approvals":
        try:
            days = int(value.get("escalation_days"))
        except (TypeError, ValueError):
            raise ValueError("Reminder days must be a whole number")
        if not 0 <= days <= 60:
            raise ValueError("Reminder days must be 0 (off) to 60")
        return {"escalation_days": days}
    raise ValueError("Unknown settings group")


async def _stored(db, org, group):
    return (await db.execute(select(OrgSetting).where(
        OrgSetting.org_id == org, OrgSetting.key == group))).scalar_one_or_none()


@router.get("")
async def all_settings(request: Request, db: AsyncSession = Depends(get_db)):
    org, role = _org(request), _role(request)
    rows = {r.key: r for r in (await db.execute(select(OrgSetting).where(OrgSetting.org_id == org))).scalars().all()}
    groups = {}
    for key, g in os_.GROUPS.items():
        if not os_.can_read(key, role):
            continue
        r = rows.get(key)
        value = os_.merged(key, r.value if r else None)
        if key == "locale" and not value.get("country"):
            value.update(await os_._locale_from_org(org))
        groups[key] = {"label": g["label"], "value": value, "saved": r is not None,
                       "updated_by": r.updated_by if r else None,
                       "updated_at": pm.iso(r.updated_at) if r else None}
    logo = (await db.execute(select(OrgLogo.data).where(OrgLogo.org_id == org))).first()
    # An old SVG logo (accepted before 2026-10-21) is no longer served: the screen asks for a PNG / JPG.
    usable = bool(logo) and docstore.sniff_image(bytes(logo[0] or b"")) is not None
    return {"groups": groups, "can_edit": os_.can_write(role), "has_logo": usable,
            "logo_needs_reupload": bool(logo) and not usable}


@router.get("/audit")
async def audit(request: Request, key: str | None = None, limit: int = 200, db: AsyncSession = Depends(get_db)):
    if not os_.can_write(_role(request)):
        raise HTTPException(403, "Only an org admin can see the settings history")
    org = _org(request)
    q = select(OrgSettingAudit).where(OrgSettingAudit.org_id == org)
    if key:
        q = q.where(OrgSettingAudit.key == key)
    rows = (await db.execute(q.order_by(OrgSettingAudit.at.desc()).limit(max(1, min(limit, 1000))))).scalars().all()
    return [{"id": a.id, "key": a.key, "label": os_.GROUPS.get(a.key, {}).get("label", a.key),
             "changes": diff(a.old_value, a.new_value), "by": a.actor_email or "", "at": pm.iso(a.at)} for a in rows]


def diff(old, new) -> list[dict]:
    """Field-level before / after of one settings change (for the audit screen)."""
    old, new = old if isinstance(old, dict) else {}, new if isinstance(new, dict) else {}
    out = []
    for k in sorted(set(old) | set(new)):
        if old.get(k) != new.get(k):
            out.append({"field": k, "before": old.get(k), "after": new.get(k)})
    return out


# ── logo (bytes in the database) ──────────────────────────────────────────────

class LogoIn(BaseModel):
    content_type: str
    data_base64: str


@router.get("/logo")
async def get_logo(request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    row = (await db.execute(select(OrgLogo).where(OrgLogo.org_id == org))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "No logo uploaded")
    if docstore.sniff_image(bytes(row.data or b"")) is None:     # an old SVG / non-image row is never served
        raise HTTPException(404, "The saved logo is not a PNG, JPG or WEBP image. Upload it again.")
    headers = {"Cache-Control": "private, max-age=60", "X-Content-Type-Options": "nosniff",
               "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
               "Content-Disposition": f'inline; filename="logo.{_LOGO_EXT.get(row.content_type, "img")}"'}
    return Response(content=row.data, media_type=row.content_type, headers=headers)


@router.put("/logo")
async def put_logo(body: LogoIn, request: Request, db: AsyncSession = Depends(get_db)):
    if not os_.can_write(_role(request)):
        raise HTTPException(403, "Only an org admin can change the logo")
    org = _org(request)
    if body.content_type not in LOGO_TYPES:
        raise HTTPException(422, "The logo must be a PNG, JPG or WEBP image (SVG is not accepted)")
    try:
        data = base64.b64decode(body.data_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "The logo file could not be read")
    if not data:
        raise HTTPException(422, "The logo file is empty")
    if len(data) > LOGO_MAX_BYTES:
        raise HTTPException(413, "The logo must be 512 KB or smaller")
    try:                                    # the bytes decide the type; the declared type must match them
        docstore.check_image(data, body.content_type)
    except docstore.DocError as e:
        raise HTTPException(422, str(e))
    row = (await db.execute(select(OrgLogo).where(OrgLogo.org_id == org))).scalar_one_or_none()
    if row is None:
        row = OrgLogo(org_id=org, content_type=body.content_type, data=data)
        db.add(row)
    row.content_type, row.data = body.content_type, data
    row.updated_by, row.updated_at = request.headers.get("x-user-email"), pm.now_utc()
    db.add(OrgSettingAudit(org_id=org, key="logo", old_value=None,
                           new_value={"logo": f"uploaded ({len(data)} bytes, {body.content_type})"},
                           actor_id=request.headers.get("x-user-id"), actor_email=request.headers.get("x-user-email")))
    await db.commit()
    return {"has_logo": True, "bytes": len(data), "content_type": body.content_type}


@router.delete("/logo")
async def delete_logo(request: Request, db: AsyncSession = Depends(get_db)):
    if not os_.can_write(_role(request)):
        raise HTTPException(403, "Only an org admin can change the logo")
    org = _org(request)
    row = (await db.execute(select(OrgLogo).where(OrgLogo.org_id == org))).scalar_one_or_none()
    if row:
        await db.delete(row)
        db.add(OrgSettingAudit(org_id=org, key="logo", old_value={"logo": "present"}, new_value={"logo": "removed"},
                               actor_id=request.headers.get("x-user-id"),
                               actor_email=request.headers.get("x-user-email")))
        await db.commit()
    return {"has_logo": False}


# ── one key (the route other services call) ───────────────────────────────────

@router.get("/{key:path}")
async def get_one(key: str, request: Request):
    org, role = _org(request), _role(request)
    group, _path = os_.split_key(key)
    if group not in os_.GROUPS:
        raise HTTPException(404, "Unknown setting")
    if not os_.can_read(group, role):
        raise HTTPException(403, "You cannot read this setting")
    os_.invalidate(org)                      # the API always answers from the database
    return {"key": key, "value": await os_.get_setting(org, key)}


class SettingIn(BaseModel):
    value: dict


@router.put("/{group}")
async def put_one(group: str, body: SettingIn, request: Request, db: AsyncSession = Depends(get_db)):
    org, role = _org(request), _role(request)
    if group not in os_.GROUPS:
        raise HTTPException(404, "Unknown settings group")
    if not os_.can_write(role):
        raise HTTPException(403, "Only an org admin can change company settings")
    try:
        value = clean(group, body.value)
    except ValueError as e:
        raise HTTPException(422, str(e))
    row = await _stored(db, org, group)
    old = os_.merged(group, row.value if row else None)
    if row is None:
        row = OrgSetting(org_id=org, key=group, value=value)
        db.add(row)
    row.value = value
    row.updated_by, row.updated_at = request.headers.get("x-user-email"), pm.now_utc()
    if diff(old, value):
        db.add(OrgSettingAudit(org_id=org, key=group, old_value=old, new_value=value,
                               actor_id=request.headers.get("x-user-id"),
                               actor_email=request.headers.get("x-user-email")))
    await db.commit()
    os_.invalidate(org)
    return {"key": group, "value": value, "saved": True}
