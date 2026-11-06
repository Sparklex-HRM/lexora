"""Notification service — Slack / Teams connector routes (stream G3).

Outbound (mirroring notifications) is done by ``shared/utils/providers/mirror.py``
from inside ``notify()``; the config lives on the Integrations screen
(``/auth/integrations/slack`` / ``teams``). This module serves:

Public (the gateway lists these; the platform SIGNATURE is the credential):
    POST /notifications/chatops/slack/command     Slack slash command (X-Slack-Signature)
    POST /notifications/chatops/teams/command     Teams outgoing webhook (Authorization: HMAC …)
Signed-in:
    POST /notifications/chatops/link-code         a 6-letter code the person types once:  /lexora link ABC123
    GET  /notifications/chatops/commands          the command list for the screen

A Slack / Teams user is only trusted after that one-time link (stored in the
provider config as user_links = {platform user id: Lexora HRM user id}). Commands:
``leave balance`` · ``payslip`` · ``approve <id>``.
"""
import secrets
import sys
import time

import httpx
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select, text

sys.path.append("/app")
from shared.utils import identity  # noqa: E402
from shared.utils import platform_models as pm  # noqa: E402
from shared.utils.config import settings  # noqa: E402
from shared.utils.providers import chatops as C, store  # noqa: E402
from shared.utils.providers.models import IntegrationProvider  # noqa: E402

router = APIRouter(prefix="/notifications/chatops", tags=["chatops"])

_link_codes: dict[str, tuple[str, str, float]] = {}      # code → (org_id, user_id, expires)
LINK_TTL = 600


def _uid(request: Request) -> tuple[str, str]:
    org, uid = request.headers.get("x-org-id"), request.headers.get("x-user-id")
    if not org or not uid:
        raise HTTPException(401, "Not authenticated")
    return org, uid


@router.get("/commands")
async def commands(request: Request):
    _uid(request)
    return {"commands": [{"command": k, "what": v} for k, v in C.COMMANDS.items()],
            "link": "Type  /lexora link <code>  in Slack or Teams once, with a code from the button below."}


@router.post("/link-code")
async def link_code(request: Request):
    org, uid = _uid(request)
    now = time.time()
    for k in [k for k, v in _link_codes.items() if v[2] < now]:
        _link_codes.pop(k, None)
    code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))
    _link_codes[code] = (org, uid, now + LINK_TTL)
    return {"code": code, "expires_in_seconds": LINK_TTL, "how": f"In Slack or Teams type:  /lexora link {code}"}


async def _configs(key: str) -> list[dict]:
    async with pm.session() as s:
        rows = (await s.execute(select(IntegrationProvider).where(IntegrationProvider.provider == key,
                                                                  IntegrationProvider.enabled.is_(True)))).scalars().all()
    p = store.base.get(key)
    return [{"org_id": r.org_id, "config": store.decrypt_config(p, r.mode, r.config or {})} for r in rows]


async def _save_link(org_id: str, key: str, platform_user: str, lexora_user: str) -> None:
    cfg = await store.load(org_id, key)
    links = dict(cfg["config"].get("user_links") or {})
    links[platform_user] = lexora_user
    await store.save(org_id, key, mode=None, enabled=None, config={"user_links": links}, actor="chatops-link")


async def _handle(org_id: str, key: str, cfg: dict, platform_user: str, text_in: str) -> str:
    cmd, arg = C.parse_command(text_in)
    if cmd == "unknown" and text_in.strip().lower().startswith("link "):
        code = text_in.strip().split(" ", 1)[1].strip().upper()
        hit = _link_codes.pop(code, None)
        if not hit or hit[2] < time.time() or hit[0] != org_id:
            return "That link code is not valid or has expired. Get a new one from Lexora HRM → Notifications."
        await _save_link(org_id, key, platform_user, hit[1])
        return "Linked. You can now use /lexora leave balance, /lexora payslip and /lexora approve <id>."
    if cmd == "help":
        return C.help_text()
    if cmd == "unknown":
        return f"I did not understand \"{arg}\". " + C.help_text()
    lexora_user = (cfg.get("user_links") or {}).get(platform_user)
    if not lexora_user:
        return "Your chat account is not linked to Lexora HRM yet. In Lexora HRM open Notifications → \"Link Slack / Teams\" and type the code here as  /lexora link <code>."
    state = await identity.fetch_user_state(lexora_user)
    if not state or not state["is_active"] or state["org_id"] != org_id:
        return "Your Lexora HRM login is not active."
    if cmd == "payslip":
        from shared.utils.notify import absolute_link
        return f"Your payslips: {absolute_link('/my-pay')} (sign in to open)."
    if cmd == "leave_balance":
        if not state.get("employee_id"):
            return "Your login is not linked to an employee record."
        return await _leave_balance(org_id, state["employee_id"])
    if cmd == "approve":
        return await _approve(org_id, state, lexora_user, arg)
    return C.help_text()


async def _leave_balance(org_id: str, employee_id: str) -> str:
    try:
        async with pm.session() as s:
            rows = (await s.execute(text(
                "SELECT leave_type_name, leave_type_code, allocated, carried_over, used, pending FROM leave_balances "
                "WHERE org_id = CAST(:org AS uuid) AND employee_id = CAST(:emp AS uuid) AND fiscal_year = "
                "(SELECT MAX(fiscal_year) FROM leave_balances WHERE org_id = CAST(:org AS uuid) AND employee_id = CAST(:emp AS uuid))"),
                {"org": org_id, "emp": employee_id})).mappings().all()
    except Exception:  # noqa: BLE001
        return "Leave balances could not be read right now."
    if not rows:
        return "No leave balances on record for this year."
    parts = []
    for r in rows:
        avail = float(r["allocated"] or 0) + float(r["carried_over"] or 0) - float(r["used"] or 0) - float(r["pending"] or 0)
        parts.append(f"{r['leave_type_name'] or r['leave_type_code']}: {avail:g} left (used {float(r['used'] or 0):g})")
    return "Leave balance — " + " · ".join(parts)


async def _approve(org_id: str, state: dict, user_id: str, item_id: str) -> str:
    headers = {"x-org-id": org_id, "x-user-id": user_id, "x-role": state.get("role") or "employee",
               "x-employee-id": state.get("employee_id") or "", "x-user-email": "chatops"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.post(f"{settings.APPROVALS_URL}/approvals/items/{item_id}/decide",
                             json={"decision": "approve", "comment": "Approved from chat"}, headers=headers)
    except httpx.HTTPError:
        return "The approvals service could not be reached. Try again in a moment."
    if 200 <= r.status_code < 300:
        return f"Approved item {item_id}."
    try:
        detail = r.json().get("detail")
    except ValueError:
        detail = None
    return f"Could not approve {item_id}: {detail or ('HTTP ' + str(r.status_code))}"


@router.post("/slack/command")
async def slack_command(request: Request):
    body = await request.body()
    ts, sig = request.headers.get("x-slack-request-timestamp"), request.headers.get("x-slack-signature")
    match = None
    for c in await _configs("slack"):
        if C.verify_slack_signature(c["config"].get("signing_secret") or "", ts, body, sig):
            match = c
            break
    if not match:
        raise HTTPException(401, "Signature check failed. Slack is not configured for this request, or the signing secret is wrong.")
    form = dict(await request.form())
    user = str(form.get("user_id") or "")
    reply = await _handle(match["org_id"], "slack", match["config"], user, str(form.get("text") or ""))
    return {"response_type": "ephemeral", "text": reply}


@router.post("/teams/command")
async def teams_command(request: Request):
    body = await request.body()
    auth = request.headers.get("authorization")
    match = None
    for c in await _configs("teams"):
        if C.verify_teams_hmac(c["config"].get("outgoing_secret") or "", body, auth):
            match = c
            break
    if not match:
        raise HTTPException(401, "Signature check failed. Teams is not configured for this request, or the security token is wrong.")
    try:
        data = await request.json()
    except ValueError:
        raise HTTPException(400, "Not JSON")
    frm = data.get("from") or {}
    user = str(frm.get("aadObjectId") or frm.get("id") or "")
    import re
    text_in = re.sub(r"<at>.*?</at>", "", str(data.get("text") or "")).strip()
    reply = await _handle(match["org_id"], "teams", match["config"], user, text_in)
    return {"type": "message", "text": reply}
