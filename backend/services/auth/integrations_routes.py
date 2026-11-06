"""Auth service — the Integrations admin API (stream G3).

    GET  /auth/integrations                       every provider with status + SSO/SCIM/keys/webhooks summary
    GET  /auth/integrations/audit                 what changed (audit trail, action integration.* / auth.sso.*)
    GET  /auth/integrations/{key}                 one provider: fields, masked config, status
    PUT  /auth/integrations/{key} {mode, enabled, config}
    POST /auth/integrations/{key}/test            a real call behind a timeout, or an honest "not configured"
BGV (HR):   GET /auth/bgv/meta  GET /auth/bgv/checks  POST /auth/bgv/checks  PATCH /auth/bgv/checks/{id}
"""
import sys

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from http_errors import refusal as _refusal  # noqa: E402  (2026-10-24: no raw exception text)
from shared.utils import identity  # noqa: E402
from shared.utils import platform_models as pm  # noqa: E402
from shared.utils.audit import AuditEvent, record_audit  # noqa: E402
from shared.utils.providers import base, bgv as B, store  # noqa: E402
from shared.utils.providers.base import NotConfigured, ProviderError  # noqa: E402
from shared.utils.providers.models import ApiKey, BgvCheck, ScimToken, SsoConfig, Webhook  # noqa: E402

from database import get_db  # noqa: E402

router = APIRouter(prefix="/auth", tags=["integrations"])
HR_ROLES = {"superadmin", "org_admin", "hr_admin", "hr_manager"}


def _org(request: Request) -> str:
    org = request.headers.get("x-org-id")
    if not org:
        raise HTTPException(401, "Not authenticated")
    return org


def _admin(request: Request) -> str:
    org = _org(request)
    if (request.headers.get("x-role") or "") not in ("org_admin", "superadmin"):
        raise HTTPException(403, "Only an admin can change integrations")
    return org


def _hr(request: Request) -> str:
    org = _org(request)
    if (request.headers.get("x-role") or "") not in HR_ROLES:
        raise HTTPException(403, "Only HR or an admin can open this")
    return org


# ── providers ────────────────────────────────────────────────────────────────

@router.get("/integrations")
async def overview(request: Request):
    org = _admin(request)
    items = [await store.public(org, p.key) for p in base.all_providers()]
    async with pm.session() as s:
        sso_cfg = (await s.execute(select(SsoConfig).where(SsoConfig.org_id == org))).scalar_one_or_none()
        n_tokens = len((await s.execute(select(ScimToken.id).where(ScimToken.org_id == org, ScimToken.revoked_at.is_(None)))).all())
        n_keys = len((await s.execute(select(ApiKey.id).where(ApiKey.org_id == org, ApiKey.revoked_at.is_(None)))).all())
        n_hooks = len((await s.execute(select(Webhook.id).where(Webhook.org_id == org, Webhook.enabled.is_(True)))).all())
    import sso as SSO
    sso_state = ("configured" if sso_cfg and sso_cfg.enabled and not SSO.missing_for(sso_cfg) else "not_configured")
    return {"providers": items,
            "sso": {"status": sso_state, "protocol": sso_cfg.protocol if sso_cfg else None,
                    "required": bool(sso_cfg and sso_cfg.required), "display_name": sso_cfg.display_name if sso_cfg else ""},
            "scim": {"status": "configured" if n_tokens else "not_configured", "active_tokens": n_tokens},
            "api_keys": {"active": n_keys}, "webhooks": {"active": n_hooks},
            "cra": {"status": "not_built",
                    "message": "No CRA / Service Canada portal robot (by design): Lexora HRM builds the T4 / RL-1 XML, "
                               "ROE web-form XML and PD7A remittance figures; you file and pay through My Business "
                               "Account, ROE Web and your bank yourself."}}


@router.get("/integrations/audit")
async def audit_list(request: Request, limit: int = 300, db: AsyncSession = Depends(get_db)):
    org = _admin(request)
    rows = (await db.execute(select(AuditEvent).where(
        AuditEvent.org_id == identity._uuid(org),
        or_(AuditEvent.action.like("integration.%"), AuditEvent.action.like("auth.sso%")))
        .order_by(AuditEvent.seq.desc()).limit(max(1, min(limit, 1000))))).scalars().all()
    return {"items": [{"id": str(r.id), "at": r.ts.isoformat() if r.ts else None, "actor": r.actor_email,
                       "role": r.actor_role, "action": r.action, "entity_type": r.entity_type,
                       "entity_id": r.entity_id, "summary": r.summary, "meta": r.meta} for r in rows]}


@router.get("/integrations/{key}")
async def get_provider(key: str, request: Request):
    org = _admin(request)
    if not base.get(key):
        raise HTTPException(404, "Unknown provider")
    return await store.public(org, key)


class ProviderIn(BaseModel):
    mode: str | None = None
    enabled: bool | None = None
    config: dict | None = None


@router.put("/integrations/{key}")
async def put_provider(key: str, body: ProviderIn, request: Request):
    org = _admin(request)
    p = base.get(key)
    if not p:
        raise HTTPException(404, "Unknown provider")
    try:
        out = await store.save(org, key, mode=body.mode, enabled=body.enabled, config=body.config,
                               actor=request.headers.get("x-user-email"))
    except ValueError as e:
        raise _refusal(422, e, "INTEGRATION_CONFIG_INVALID", fallback="The provider settings are not valid.")
    changed = sorted((body.config or {}).keys())
    secrets_changed = [k for k in changed if k in p.secret_keys(out["mode"]) and (body.config or {}).get(k) not in (store.KEEP, "", None)]
    await record_audit(request, "integration.provider.update", entity_type="provider", entity_id=key,
                       summary=f"{p.label}: settings changed" + (f" (mode {out['mode']})" if out["mode"] else ""),
                       meta={"enabled": out["enabled"], "fields": changed, "secrets_replaced": secrets_changed})
    return out


@router.post("/integrations/{key}/test")
async def test_provider(key: str, request: Request):
    org = _admin(request)
    p = base.get(key)
    if not p:
        raise HTTPException(404, "Unknown provider")
    cfg = await store.load(org, key)
    try:
        res = await p.test(cfg["mode"], cfg["config"])
    except (NotConfigured, ProviderError) as e:
        res = {"ok": False, "message": e.message}
    except Exception as e:  # noqa: BLE001
        res = {"ok": False, "message": f"The test failed ({type(e).__name__})."}
    await store.record_test(org, key, res["ok"], res["message"])
    await record_audit(request, "integration.provider.test", entity_type="provider", entity_id=key,
                       summary=f"{p.label}: test {'passed' if res['ok'] else 'failed'} — {res['message']}"[:300])
    return res


# ── BGV ──────────────────────────────────────────────────────────────────────

def _bgv_out(b: BgvCheck) -> dict:
    return {"id": b.id, "employee_id": b.employee_id, "employee_name": b.employee_name, "check_type": b.check_type,
            "check_type_label": B.CHECK_TYPES.get(b.check_type, b.check_type), "mode": b.mode, "vendor": b.vendor,
            "reference": b.reference, "status": b.status, "status_label": B.STATUSES.get(b.status, b.status),
            "result_note": b.result_note, "document_id": b.document_id, "requested_at": pm.iso(b.requested_at),
            "completed_at": pm.iso(b.completed_at), "recorded_by": b.recorded_by, "updated_at": pm.iso(b.updated_at)}


@router.get("/bgv/meta")
async def bgv_meta(request: Request):
    org = _hr(request)
    emps = await identity.employees_brief(org)
    return {"check_types": [{"key": k, "label": v} for k, v in B.CHECK_TYPES.items()],
            "statuses": [{"key": k, "label": v} for k, v in B.STATUSES.items()],
            "employees": sorted([{"id": i, "name": f"{e.get('first_name') or ''} {e.get('last_name') or ''}".strip(),
                                  "employee_number": e.get("employee_number")} for i, e in emps.items()],
                                key=lambda x: x["name"].lower()),
            "provider": await store.public(org, "bgv")}


@router.get("/bgv/checks")
async def bgv_list(request: Request, employee_id: str | None = None, limit: int = 1000):
    org = _hr(request)
    q = select(BgvCheck).where(BgvCheck.org_id == org)
    if employee_id:
        q = q.where(BgvCheck.employee_id == employee_id)
    async with pm.session() as s:
        rows = (await s.execute(q.order_by(BgvCheck.requested_at.desc()).limit(limit))).scalars().all()
    return {"items": [_bgv_out(b) for b in rows]}


class BgvIn(BaseModel):
    employee_id: str
    check_type: str
    vendor: str = ""
    reference: str = ""
    result_note: str = ""
    status: str = "requested"


@router.post("/bgv/checks", status_code=201)
async def bgv_create(body: BgvIn, request: Request):
    org = _hr(request)
    emp = (await identity.employees_brief(org)).get(body.employee_id)
    if not emp:
        raise HTTPException(404, "Employee not found")
    if body.check_type not in B.CHECK_TYPES:
        raise HTTPException(422, "Pick a check type")
    if body.status not in B.STATUSES:
        raise HTTPException(422, "Unknown status")
    cfg = await store.load(org, "bgv")
    mode = cfg["mode"] or "manual"
    if mode == "api":
        try:
            await B.order_check(cfg["config"], body.check_type, emp)
        except NotConfigured as e:
            mode = "manual"
            note = e.message
    name = f"{emp.get('first_name') or ''} {emp.get('last_name') or ''}".strip()
    async with pm.session() as s:
        b = BgvCheck(org_id=org, employee_id=body.employee_id, employee_name=name, check_type=body.check_type, mode=mode,
                     vendor=(body.vendor or cfg["config"].get("default_vendor") or "").strip()[:120],
                     reference=body.reference.strip()[:120], status=body.status, result_note=body.result_note.strip(),
                     completed_at=pm.now_utc() if body.status in B.FINAL else None,
                     recorded_by=request.headers.get("x-user-email"))
        s.add(b)
        await s.commit()
        out = _bgv_out(b)
    await record_audit(request, "integration.bgv.create", entity_type="employee", entity_id=body.employee_id,
                       summary=f"BGV {B.CHECK_TYPES[body.check_type]} for {name}: {B.STATUSES[body.status]}",
                       meta={"vendor": out["vendor"], "reference": out["reference"]})
    return out


class BgvPatch(BaseModel):
    status: str | None = None
    vendor: str | None = None
    reference: str | None = None
    result_note: str | None = None
    document_id: str | None = None


@router.patch("/bgv/checks/{bid}")
async def bgv_update(bid: str, body: BgvPatch, request: Request):
    org = _hr(request)
    async with pm.session() as s:
        b = (await s.execute(select(BgvCheck).where(BgvCheck.id == bid, BgvCheck.org_id == org))).scalar_one_or_none()
        if not b:
            raise HTTPException(404, "Check not found")
        old = b.status
        if body.status is not None:
            if not B.valid_transition(b.status, body.status):
                raise HTTPException(409, f"A {B.STATUSES.get(b.status, b.status)} check cannot move to {B.STATUSES.get(body.status, body.status)}")
            b.status = body.status
            b.completed_at = pm.now_utc() if body.status in B.FINAL else None
        for k in ("vendor", "reference", "result_note", "document_id"):
            v = getattr(body, k)
            if v is not None:
                setattr(b, k, v.strip()[:120] if k != "result_note" else v.strip())
        b.recorded_by, b.updated_at = request.headers.get("x-user-email"), pm.now_utc()
        await s.commit()
        out = _bgv_out(b)
    await record_audit(request, "integration.bgv.update", entity_type="employee", entity_id=out["employee_id"],
                       summary=f"BGV {out['check_type_label']} for {out['employee_name']}: {B.STATUSES.get(old, old)} → {out['status_label']}",
                       meta={"document_id": out["document_id"]})
    return out
