"""``/ai/agents/channels/whatsapp`` — the agentic WhatsApp chase channel (2026-10-26; shared/utils/whatsapp_channel.py).

  GET  /ai/agents/channels/whatsapp                 config (no credential values exist to show) + readiness + plan explanation (office)
  PUT  /ai/agents/channels/whatsapp {value}         org admin; validated by whatsapp_channel.clean_config (token_env = env var NAME only)
  POST /ai/agents/channels/whatsapp/preview {employee_id, reminder_no}
                                                    DRY RUN: consent decision + channel plan + the template message that WOULD be
                                                    sent (number masked). Never sends.
"""
from __future__ import annotations

import sys

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

sys.path.append("/app")
from shared.utils import platform_models as pm  # noqa: E402
from shared.utils import whatsapp_channel as WA  # noqa: E402
from shared.utils.org_settings import invalidate  # noqa: E402
from shared.utils.platform_models import OrgSetting, OrgSettingAudit  # noqa: E402

import agentic_log as LOG  # noqa: E402
from agents import lifecycle_common as LC  # noqa: E402
from agents.lifecycle_common import guard  # noqa: E402  (role-family + permission guard; named import so the authz AST sees it)

router = APIRouter(prefix="/ai/agents/channels", tags=["agentic-channels"])


class PutIn(BaseModel):
    value: dict


class PreviewIn(BaseModel):
    employee_id: str = Field(min_length=1, max_length=64)
    reminder_no: int = Field(default=2, ge=1, le=20)


async def _save(org: str, cfg: dict, actor_id: str | None, actor_email: str | None) -> dict:
    async with pm.session() as s:
        row = (await s.execute(select(OrgSetting).where(OrgSetting.org_id == str(org), OrgSetting.key == WA.GROUP))).scalar_one_or_none()
        old = dict(row.value or {}) if row else {}
        new = {**old, "whatsapp": cfg}
        if row is None:
            row = OrgSetting(org_id=str(org), key=WA.GROUP, value=new)
            s.add(row)
        row.value, row.updated_by, row.updated_at = new, actor_email, pm.now_utc()
        if old != new:
            s.add(OrgSettingAudit(org_id=str(org), key=WA.GROUP, old_value=old, new_value=new, actor_id=actor_id, actor_email=actor_email))
        await s.commit()
    invalidate(str(org))
    return cfg


@router.get("/whatsapp")
async def get_whatsapp(request: Request):
    org = LC.org_of(request)
    role = guard(request, "ai.view", "The agent channels are for the org admin, HR admin and payroll admin")
    cfg = await WA.load_config(org)
    return {"value": cfg, "readiness": WA.readiness(cfg), "can_edit": role in ("org_admin", "superadmin"), "consent_event": WA.CONSENT_EVENT,
            "order": ["in_app", "email", "whatsapp"],
            "note": ("Reminders go in-app and by e-mail first; WhatsApp is added from reminder number 'after_reminders' only for employees "
                     "who opted in to agent reminders on their current number, with your approved Meta template. Without the full "
                     "configuration nothing is sent — the log says 'would send'.")}


@router.put("/whatsapp")
async def put_whatsapp(body: PutIn, request: Request):
    org = LC.org_of(request)
    guard(request, "ai.admin", "Only an org admin can change the WhatsApp channel")
    cur = await WA.load_config(org)
    try:
        cfg = WA.clean_config(body.value, cur)
    except (ValueError, TypeError) as e:
        raise HTTPException(422, str(e))
    saved = await _save(org, cfg, request.headers.get("x-user-id"), request.headers.get("x-user-email"))
    await LOG.append_event(org, event_type="channel.whatsapp.saved", agent_name="attendance", tool_name="channels.whatsapp",
                           approver_user_id=request.headers.get("x-user-id"), tool_input={"enabled": saved["enabled"], "provider": saved["provider"],
                                                                                          "templates": saved["templates"], "after_reminders": saved["after_reminders"]},
                           risk_tier=0, policy_decision="allow")
    return {"saved": True, "value": saved, "readiness": WA.readiness(saved)}


@router.post("/whatsapp/preview")
async def preview_whatsapp(body: PreviewIn, request: Request):
    org = LC.org_of(request)
    guard(request, "ai.view", "The agent channels are for the org admin, HR admin and payroll admin")
    cfg = await WA.load_config(org)
    consent = await WA.consent_for(org, body.employee_id)
    plan = WA.channel_plan(cfg, consent_ok=consent["ok"], reminder_no=body.reminder_no)
    msg = None
    if "whatsapp" in plan:
        try:
            msg = WA.template_message(cfg, to=consent["phone"], kind=WA.CONSENT_EVENT, params=["Payroll Auditor", str(body.reminder_no)])
            msg = {**msg, "to": WA.mask(consent["phone"])}
        except ValueError as e:
            msg = {"error": str(e)}
    return {"plan": plan, "consent": {"ok": consent["ok"], "reason": consent.get("reason"), "to": WA.mask(consent.get("phone"))},
            "readiness": WA.readiness(cfg), "would_send": msg, "sent": False}
