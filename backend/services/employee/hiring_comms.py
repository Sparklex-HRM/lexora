"""Hiring D2 — candidate communications: templated e-mail / WhatsApp at stage changes,
interview scheduling and reminders. Opt-in is respected; the outcome logged is the TRUE one.

  e-mail    shared.utils.email.send_email (SMTP from the env; "not_configured" without it)
  WhatsApp  shared.utils.providers.whatsapp.send_template with the org's S5 config
            (Integrations, WhatsApp) — "not_configured" until credentials + an approved template exist
Candidates are not employees, so their opt-in lives in hire_candidate_comms (ticked on the
apply form) — never in the employee WhatsApp opt-in table.

  GET  /hiring/comms/templates            the org's templates (defaults filled in)
  PUT  /hiring/comms/templates            save one or many  [{event, channel, enabled, subject, body, whatsapp_template}]
  POST /hiring/comms/preview              render a template with sample values
  GET  /hiring/comms/log?candidate_id=    what was sent (or not, and why)
  PUT  /hiring/candidates/{id}/comms      HR records opt-in / opt-out
"""
import logging
import sys
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils.email import send_email, smtp_configured  # noqa: E402

import hiring_d2_logic as D  # noqa: E402
import hiring_integrations as ext  # noqa: E402
from database import get_db  # noqa: E402
from hiring_common import get_actor, now, one, ser, uid  # noqa: E402
from hiring_d2_models import HireCandidateComms, HireCommsLog, HireCommsTemplate  # noqa: E402
from hiring_models import HireApplication, HireCandidate, HireRequisition  # noqa: E402

log = logging.getLogger("lexora.hiring.comms")
router = APIRouter(prefix="/hiring", tags=["hiring-comms"])
CHANNELS = ("email", "whatsapp")


# ── templates ───────────────────────────────────────────────────────────────
async def templates_for(db: AsyncSession, org) -> dict[tuple[str, str], dict]:
    rows = (await db.execute(select(HireCommsTemplate).where(HireCommsTemplate.org_id == org))).scalars().all()
    saved = {(r.event, r.channel): r for r in rows}
    out = {}
    for ev, (subj, body) in D.DEFAULT_COMMS.items():
        for ch in CHANNELS:
            r = saved.get((ev, ch))
            out[(ev, ch)] = {
                "event": ev, "channel": ch, "label": D.COMMS_EVENTS.get(ev, ev),
                "enabled": bool(r.enabled) if r else (ch == "email"),      # WhatsApp off until a template name is set
                "subject": (r.subject if r and r.subject else subj),
                "body": (r.body if r and r.body else body),
                "whatsapp_template": (r.whatsapp_template if r else None), "is_default": r is None,
                "updated_at": r.updated_at.isoformat() if r and r.updated_at else None,
            }
    return out


@router.get("/comms/templates")
async def list_templates(request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.is_hr:
        raise HTTPException(403, "Only HR can see candidate message templates.")
    t = await templates_for(db, actor.org)
    wa = await _whatsapp_cfg(actor.org)
    return {"templates": list(t.values()), "placeholders": list(D.COMMS_PLACEHOLDERS),
            "email": {"configured": smtp_configured(),
                      "message": "E-mail is on." if smtp_configured() else "No SMTP server is set up: e-mails are logged as not configured."},
            "whatsapp": {"configured": bool(wa), "message": ("WhatsApp is configured (Integrations). Put the approved template name on each message." if wa
                                                              else "WhatsApp is not configured: set it up under Integrations, WhatsApp. Messages are logged as not configured.")},
            "can_edit": actor.is_hr_head}


@router.put("/comms/templates")
async def save_templates(body: dict | list, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.is_hr_head:
        raise HTTPException(403, "Only an HR head or an org admin can change candidate messages.")
    items = body if isinstance(body, list) else (body.get("items") or [body])
    saved = 0
    for it in items:
        ev, ch = it.get("event"), it.get("channel")
        if ev not in D.DEFAULT_COMMS or ch not in CHANNELS:
            raise HTTPException(422, "Unknown message event or channel.")
        bad = D.unknown_placeholders(it.get("subject")) + D.unknown_placeholders(it.get("body"))
        if bad:
            raise HTTPException(422, "Unknown placeholder: " + ", ".join("{{" + b + "}}" for b in bad))
        row = (await db.execute(select(HireCommsTemplate).where(
            HireCommsTemplate.org_id == actor.org, HireCommsTemplate.event == ev, HireCommsTemplate.channel == ch))).scalar_one_or_none()
        if not row:
            row = HireCommsTemplate(org_id=uid(actor.org), event=ev, channel=ch)
            db.add(row)
        if "enabled" in it:
            row.enabled = bool(it["enabled"])
        if "subject" in it:
            row.subject = (str(it.get("subject") or "").strip()[:200]) or None
        if "body" in it:
            row.body = (str(it.get("body") or "").strip()[:4000]) or None
        if "whatsapp_template" in it:
            row.whatsapp_template = (str(it.get("whatsapp_template") or "").strip()[:120]) or None
        row.updated_by, row.updated_at = actor.user_id, now()
        saved += 1
    await db.commit()
    return {"saved": saved}


@router.post("/comms/preview")
async def preview(body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.is_hr:
        raise HTTPException(403, "Only HR can preview candidate messages.")
    org = await ext.org_by_id(db, actor.org)
    values = {"first_name": "Asha", "job_title": "Staff Nurse", "org_name": org.get("name") or "Your company",
              "stage": "Interview", "interview_time": "20 Oct 2026, 10:30 EDT", "interview_mode": "Video call",
              "join_link": "https://meet.example/abc", "link": "https://example.com/interview/abc", "round_name": "Technical round"}
    return {"subject": D.render(body.get("subject"), values), "body": D.render(body.get("body"), values),
            "unknown": D.unknown_placeholders(body.get("subject")) + D.unknown_placeholders(body.get("body"))}


# ── opt-in ──────────────────────────────────────────────────────────────────
async def prefs_for(db: AsyncSession, org, candidate_id) -> dict:
    row = (await db.execute(select(HireCandidateComms).where(
        HireCandidateComms.org_id == org, HireCandidateComms.candidate_id == candidate_id))).scalar_one_or_none()
    if not row:
        return {"email_opt_in": True, "whatsapp_opt_in": False, "whatsapp_consent_text": None}
    return {"email_opt_in": bool(row.email_opt_in), "whatsapp_opt_in": bool(row.whatsapp_opt_in),
            "whatsapp_consent_text": row.whatsapp_consent_text}


async def set_prefs(db: AsyncSession, org, candidate_id, *, email_opt_in=None, whatsapp_opt_in=None, consent_text=None):
    row = (await db.execute(select(HireCandidateComms).where(
        HireCandidateComms.org_id == org, HireCandidateComms.candidate_id == candidate_id))).scalar_one_or_none()
    if not row:
        row = HireCandidateComms(org_id=uid(org), candidate_id=candidate_id)
        db.add(row)
    if email_opt_in is not None:
        row.email_opt_in = bool(email_opt_in)
    if whatsapp_opt_in is not None:
        row.whatsapp_opt_in = bool(whatsapp_opt_in)
        if whatsapp_opt_in and consent_text:
            row.whatsapp_consent_text = consent_text
    row.updated_at = now()
    return row


@router.put("/candidates/{cand_id}/comms")
async def put_prefs(cand_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.is_hr:
        raise HTTPException(403, "Only HR can change a candidate's contact preferences.")
    c = await one(db, HireCandidate, cand_id, actor.org, "Candidate")
    await set_prefs(db, actor.org, c.id, email_opt_in=body.get("email_opt_in"), whatsapp_opt_in=body.get("whatsapp_opt_in"),
                    consent_text=f"Recorded by HR ({actor.name}) on {now():%d %b %Y}")
    await db.commit()
    return await prefs_for(db, actor.org, c.id)


@router.get("/comms/log")
async def comms_log(request: Request, candidate_id: str | None = None, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.is_hr:
        raise HTTPException(403, "Only HR can see the message log.")
    q = select(HireCommsLog).where(HireCommsLog.org_id == actor.org)
    if candidate_id:
        q = q.where(HireCommsLog.candidate_id == uid(candidate_id, "Candidate"))
    rows = (await db.execute(q.order_by(HireCommsLog.at.desc()).limit(500))).scalars().all()
    return [ser(r) for r in rows]


# ── sending ─────────────────────────────────────────────────────────────────
async def _whatsapp_cfg(org) -> dict | None:
    """The org's S5 WhatsApp config when it is switched on and complete; else None."""
    try:
        from shared.utils.providers import store
        cfg = await store.load(org, "whatsapp")
        if not cfg.get("enabled") or not cfg.get("provider"):
            return None
        if cfg["provider"].missing(cfg["mode"], cfg["config"]):
            return None
        return cfg
    except Exception as e:                                   # noqa: BLE001
        log.warning("whatsapp config read failed: %s", e)
        return None


async def send_event(db: AsyncSession, org, candidate: HireCandidate, app: HireApplication | None, event: str,
                     values: dict | None = None) -> list[dict]:
    """Tell the candidate about `event` on every channel they agreed to. Best-effort,
    never raises; one log row per channel with the real outcome. Caller commits."""
    out = []
    try:
        if event not in D.DEFAULT_COMMS:
            return out
        tpls = await templates_for(db, org)
        prefs = await prefs_for(db, org, candidate.id)
        org_row = await ext.org_by_id(db, str(org))
        req = None
        if app is not None:
            req = (await db.execute(select(HireRequisition).where(HireRequisition.id == app.requisition_id))).scalar_one_or_none()
        vals = {"first_name": candidate.first_name, "job_title": (req.designation if req else ""),
                "org_name": org_row.get("name") or "", "stage": "", "interview_time": "", "interview_mode": "",
                "join_link": "", "link": "", "round_name": "", **(values or {})}
        wa_cfg = None
        for ch in CHANNELS:
            t = tpls[(event, ch)]
            addr = candidate.email if ch == "email" else candidate.phone
            opt = prefs["email_opt_in"] if ch == "email" else prefs["whatsapp_opt_in"]
            if ch == "whatsapp" and t["enabled"] and opt and addr:
                wa_cfg = wa_cfg or await _whatsapp_cfg(org)
            configured = smtp_configured() if ch == "email" else bool(wa_cfg and (t["whatsapp_template"] or wa_cfg["config"].get("template_notify")))
            status, reason = D.comms_decision(ch, opt, addr, configured, t["enabled"])
            subject = D.render(t["subject"], vals)
            msg_id = None
            if status == "ok":
                try:
                    if ch == "email":
                        ok = await send_email(addr, subject, D.render(t["body"], vals))
                        status, reason = ("sent", None) if ok else ("failed", "The mail server did not accept the message.")
                    else:
                        from shared.utils.providers import whatsapp as WA
                        tpl = t["whatsapp_template"] or wa_cfg["config"].get("template_notify")
                        res = await WA.send_template(wa_cfg["config"], wa_cfg["mode"], to=addr, template=tpl,
                                                     params=[subject[:200], vals.get("link") or vals.get("join_link") or ""],
                                                     lang=(wa_cfg["config"].get("template_lang") or "en"))
                        status, msg_id = res.get("status", "sent"), res.get("message_id")
                except Exception as e:                       # noqa: BLE001
                    status, reason = "failed", f"{type(e).__name__}: {str(e)[:300]}"
            row = HireCommsLog(org_id=uid(org), candidate_id=candidate.id, application_id=(app.id if app is not None else None),
                               event=event, channel=ch, to_masked=D.mask_contact(addr), status=status, reason=reason,
                               provider_message_id=msg_id, subject=subject[:200] or None)
            db.add(row)
            out.append({"channel": ch, "status": status, "reason": reason})
        await db.flush()
    except Exception as e:                                   # noqa: BLE001
        log.warning("send_event(%s) failed: %s", event, e)
    return out


async def after_stage_move(db: AsyncSession, app: HireApplication, to_stage: str, reason: str | None) -> None:
    """Hook called by hiring_routers.move_application after the guarded stage write."""
    try:
        c = (await db.execute(select(HireCandidate).where(HireCandidate.id == app.candidate_id))).scalar_one_or_none()
        if c is not None:
            from hiring_logic import STAGE_LABEL
            await send_event(db, app.org_id, c, app, to_stage, {"stage": STAGE_LABEL.get(to_stage, to_stage)})
    except Exception as e:                                   # noqa: BLE001
        log.warning("after_stage_move failed: %s", e)


def fmt_when(d: datetime | None, tz_name: str = "America/Toronto") -> str:
    if not d:
        return ""
    try:
        from zoneinfo import ZoneInfo
        return d.astimezone(ZoneInfo(tz_name)).strftime("%d %b %Y, %H:%M %Z")
    except Exception:                                        # noqa: BLE001
        return d.isoformat()
