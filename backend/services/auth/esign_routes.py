"""Auth service — internal e-sign (stream G3).

HR / admin (gateway JWT):
    GET  /auth/esign/envelopes?status=         POST /auth/esign/envelopes (multipart PDF + signer)
    GET  /auth/esign/envelopes/{id}             GET /auth/esign/envelopes/{id}/pdf?signed=1
    POST /auth/esign/envelopes/{id}/resend      POST /auth/esign/envelopes/{id}/cancel
Signer (public; the link token is the credential, rate-limited):
    GET  /auth/esign/public/{token}             GET  /auth/esign/public/{token}/pdf
    POST /auth/esign/public/{token}/send-code   POST /auth/esign/public/{token}/sign {code, typed_name, agree}
    POST /auth/esign/public/{token}/decline {reason}

Honest statuses: ``otp_status`` is ``sent`` only when the mail server / WhatsApp
provider accepted the code; ``signed`` only after the code matched and the PDF
was stamped. The screen states this is an electronic signature record (simple e-signature with an e-mailed one-time code).
"""
import hashlib
import os
import sys
from datetime import timedelta

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select

sys.path.append("/app")
from shared.utils import events, identity  # noqa: E402
from shared.utils import platform_models as pm  # noqa: E402
from shared.utils.audit import record_audit  # noqa: E402
from shared.utils.email import brand_html, send_email  # noqa: E402
from shared.utils.notify import notify  # noqa: E402
from shared.utils.providers import esign as E  # noqa: E402
from shared.utils.providers import mirror, store  # noqa: E402
from shared.utils.providers.models import EsignEnvelope  # noqa: E402
from shared.utils.ratelimit import RateLimiter, client_ip, make_limiter  # noqa: E402  (Redis-shared when REDIS_URL is set)

router = APIRouter(prefix="/auth/esign", tags=["esign"])
HR_ROLES = {"superadmin", "org_admin", "hr_admin", "hr_manager"}
MAX_PDF = 8 * 1024 * 1024
OTP_MINUTES, OTP_MAX_TRIES = 10, 5
pub_limiter = make_limiter(60, 900, name="esign_public")
code_limiter = make_limiter(5, 600, name="esign_code")


def _hr(request: Request) -> str:
    org = request.headers.get("x-org-id")
    if not org:
        raise HTTPException(401, "Not authenticated")
    if (request.headers.get("x-role") or "") not in HR_ROLES:
        raise HTTPException(403, "Only HR or an admin can send documents for signature")
    return org


def _ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return (fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else ""))[:64]


def _out(e: EsignEnvelope, *, full: bool = False) -> dict:
    d = {"id": e.id, "title": e.title, "kind": e.kind, "provider": e.provider, "employee_id": e.employee_id,
         "signer_name": e.signer_name, "signer_email": e.signer_email, "signer_phone": e.signer_phone,
         "status": e.status, "pdf_sha256": e.pdf_sha256, "signed_sha256": e.signed_sha256,
         "otp_channel": e.otp_channel, "otp_status": e.otp_status, "typed_name": e.typed_name,
         "signer_ip": e.signer_ip, "signed_at": pm.iso(e.signed_at), "declined_reason": e.declined_reason,
         "created_by": e.created_by, "created_at": pm.iso(e.created_at), "link_expires_at": pm.iso(e.link_expires_at)}
    if full:
        d["events"] = list(e.events or [])
        d["signer_user_agent"] = e.signer_user_agent
        d["legal_note"] = E.LEGAL_NOTE
    return d


def _event(e: EsignEnvelope, what: str, ip: str = "") -> None:
    e.events = list(e.events or []) + [{"at": pm.now_utc().strftime("%Y-%m-%d %H:%M:%S UTC"), "what": what, "ip": ip}]


def _link(raw: str) -> str:
    return f"{os.getenv('APP_BASE_URL', 'http://localhost:5173').rstrip('/')}/sign/{raw}"


async def _send_link(e: EsignEnvelope, raw: str, org: str) -> bool:
    subject = f"Please sign: {e.title}"
    lines = [f"Hi {e.signer_name.split(' ')[0]},", f"A document is waiting for your electronic signature: {e.title}.",
             "Open the link, read the document and confirm with the one-time code we send you."]
    sent = False
    if e.signer_email:
        sent = await send_email(e.signer_email, subject, "\n\n".join(lines + [_link(raw)]),
                                html=brand_html(subject, lines, "Open and sign", _link(raw)))
    if e.employee_id:
        await notify(org, employee_ids=[e.employee_id], title=subject, link=f"/sign/{raw}", kind="letter", email=False)
    return sent


# ── HR / admin ───────────────────────────────────────────────────────────────

@router.get("/envelopes")
async def list_envelopes(request: Request, status: str | None = None, limit: int = 500):
    org = _hr(request)
    q = select(EsignEnvelope).where(EsignEnvelope.org_id == org)
    if status:
        q = q.where(EsignEnvelope.status == status)
    async with pm.session() as s:
        rows = (await s.execute(q.order_by(EsignEnvelope.created_at.desc()).limit(max(1, min(limit, 2000))))).scalars().all()
    return {"items": [_out(e) for e in rows], "legal_note": E.LEGAL_NOTE}


@router.post("/envelopes", status_code=201)
async def create_envelope(request: Request, file: UploadFile = File(...), title: str = Form(...),
                          kind: str = Form("letter"), employee_id: str = Form(""), signer_name: str = Form(""),
                          signer_email: str = Form(""), signer_phone: str = Form(""), provider: str = Form("esign_internal"),
                          send: bool = Form(True)):
    org = _hr(request)
    try:
        E.provider_can_sign(provider)
    except E.NotConfigured as ex:                 # screen-ready sentence by design
        raise HTTPException(409, ex.message)
    except Exception as ex:  # noqa: BLE001 — never echo an internal error (WASA 2026-10-21)
        from http_errors import failure
        raise failure(409, "This e-sign provider cannot be used right now. Choose the internal e-sign "
                           "or contact Lexora HRM support.", ex, "esign.provider_can_sign")
    data = await file.read()
    if not data or not E.is_pdf(data):
        raise HTTPException(422, "Upload a PDF file")
    if len(data) > MAX_PDF:
        raise HTTPException(413, "The PDF is too large. The limit is 8 MB.")
    title = (title or "").strip()[:200]
    if not title:
        raise HTTPException(422, "Give the document a title")
    emp = None
    if employee_id:
        emp = (await identity.employees_brief(org)).get(employee_id)
        if not emp:
            raise HTTPException(404, "Employee not found")
    name = (signer_name or "").strip() or (f"{emp.get('first_name') or ''} {emp.get('last_name') or ''}".strip() if emp else "")
    email = (signer_email or "").strip().lower() or ((emp or {}).get("work_email") or (emp or {}).get("email") or "")
    if not name:
        raise HTTPException(422, "Type the signer's name")
    if not email and not signer_phone:
        raise HTTPException(422, "The signer needs an e-mail address or a phone number for the one-time code")
    cfg = (await store.load(org, "esign_internal"))["config"]
    hours = int(cfg.get("link_hours") or 168)
    raw, h = E.new_link_token()
    e = EsignEnvelope(org_id=org, title=title, kind=(kind or "letter")[:40], provider="esign_internal",
                      employee_id=employee_id or None, signer_name=name[:200], signer_email=email or None,
                      signer_phone=(signer_phone or "").strip()[:30] or None, status="draft", pdf=data,
                      pdf_sha256=E.sha256_hex(data), link_hash=h,
                      link_expires_at=pm.now_utc() + timedelta(hours=hours),
                      otp_channel=(cfg.get("otp_channel") or "email"), created_by=request.headers.get("x-user-email"))
    _event(e, f"Created by {request.headers.get('x-user-email') or 'HR'}", _ip(request))
    sent = False
    if send:
        e.status = "sent"
        sent = await _send_link(e, raw, org)
        _event(e, "Signing link e-mailed" if sent else "Signing link issued (e-mail not sent: no mail server)")
    async with pm.session() as s:
        s.add(e)
        await s.commit()
        out = _out(e)
    await record_audit(request, "integration.esign.create", entity_type="esign", entity_id=out["id"],
                       summary=f"Document sent for e-sign: {title} → {name}", meta={"sha256": out["pdf_sha256"]})
    out.update(email_sent=sent)
    if send and not sent and identity.show_links():      # decision 2026-10-21: never on screen in production
        out["link"] = _link(raw)
    return out


async def _get(org: str, eid: str) -> EsignEnvelope:
    async with pm.session() as s:
        e = (await s.execute(select(EsignEnvelope).where(EsignEnvelope.id == eid, EsignEnvelope.org_id == org))).scalar_one_or_none()
    if not e:
        raise HTTPException(404, "Document not found")
    return e


@router.get("/envelopes/{eid}")
async def get_envelope(eid: str, request: Request):
    return _out(await _get(_hr(request), eid), full=True)


@router.get("/envelopes/{eid}/pdf")
async def envelope_pdf(eid: str, request: Request, signed: bool = False):
    e = await _get(_hr(request), eid)
    data = e.signed_pdf if signed else e.pdf
    if not data:
        raise HTTPException(404, "There is no signed copy yet")
    name = f"{e.title[:60]}{'-signed' if signed else ''}.pdf".replace('"', "")
    return Response(content=data, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{name}"', "X-Content-Type-Options": "nosniff"})


@router.post("/envelopes/{eid}/resend")
async def resend(eid: str, request: Request):
    org = _hr(request)
    raw, h = E.new_link_token()
    async with pm.session() as s:
        e = (await s.execute(select(EsignEnvelope).where(EsignEnvelope.id == eid, EsignEnvelope.org_id == org))).scalar_one_or_none()
        if not e:
            raise HTTPException(404, "Document not found")
        if e.status not in ("draft", "sent"):
            raise HTTPException(409, f"This document is {e.status}; it cannot be sent again")
        e.link_hash, e.link_expires_at, e.status = h, pm.now_utc() + timedelta(hours=168), "sent"
        e.otp_hash, e.otp_attempts = None, 0
        sent = await _send_link(e, raw, org)
        _event(e, "Signing link sent again" + ("" if sent else " (e-mail not sent: no mail server)"), _ip(request))
        await s.commit()
    await record_audit(request, "integration.esign.resend", entity_type="esign", entity_id=eid, summary="Signing link sent again")
    return {"ok": True, "email_sent": sent, **({} if sent or not identity.show_links() else {"link": _link(raw)})}


class CancelIn(BaseModel):
    reason: str = ""


@router.post("/envelopes/{eid}/cancel")
async def cancel(eid: str, body: CancelIn, request: Request):
    org = _hr(request)
    async with pm.session() as s:
        e = (await s.execute(select(EsignEnvelope).where(EsignEnvelope.id == eid, EsignEnvelope.org_id == org))).scalar_one_or_none()
        if not e:
            raise HTTPException(404, "Document not found")
        if e.status == "signed":
            raise HTTPException(409, "A signed document cannot be cancelled")
        e.status, e.link_hash = "cancelled", None
        _event(e, f"Cancelled: {body.reason or 'no reason given'}", _ip(request))
        await s.commit()
        out = _out(e)
    await record_audit(request, "integration.esign.cancel", entity_type="esign", entity_id=eid,
                       summary=f"E-sign cancelled: {e.title}", meta={"reason": body.reason})
    return out


# ── signer (public) ──────────────────────────────────────────────────────────

def _throttle(limiter: RateLimiter, key: str):
    wait = limiter.hit(key)
    if wait:
        raise HTTPException(429, f"Too many tries. Wait {max(wait // 60, 1)} minute(s).", headers={"Retry-After": str(wait)})


async def _by_token(s, token: str) -> EsignEnvelope:
    h = hashlib.sha256((token or "").encode()).hexdigest()
    e = (await s.execute(select(EsignEnvelope).where(EsignEnvelope.link_hash == h))).scalar_one_or_none()
    if not e:
        raise HTTPException(404, "This signing link is not valid.")
    if e.status in ("cancelled",):
        raise HTTPException(410, "This document was withdrawn.")
    if e.link_expires_at and pm.aware(e.link_expires_at) < pm.now_utc() and e.status != "signed":
        raise HTTPException(410, "This signing link has expired. Ask HR to send it again.")
    return e


def _public(e: EsignEnvelope) -> dict:
    return {"title": e.title, "kind": e.kind, "signer_name": e.signer_name, "status": e.status,
            "contact": (("e-mail " + e.signer_email) if e.signer_email else ("phone ending " + (e.signer_phone or "")[-4:])),
            "otp_channel": e.otp_channel, "otp_status": e.otp_status, "pdf_sha256": e.pdf_sha256,
            "signed_at": pm.iso(e.signed_at), "legal_note": E.LEGAL_NOTE}


@router.get("/public/{token}")
async def public_info(token: str, request: Request):
    _throttle(pub_limiter, f"esign|{client_ip(request)}")
    async with pm.session() as s:
        e = await _by_token(s, token)
    return _public(e)


@router.get("/public/{token}/pdf")
async def public_pdf(token: str, request: Request):
    _throttle(pub_limiter, f"esign|{client_ip(request)}")
    async with pm.session() as s:
        e = await _by_token(s, token)
        if e.status != "signed":
            _event(e, "Document opened", _ip(request))
            await s.commit()
    data = e.signed_pdf if e.status == "signed" and e.signed_pdf else e.pdf
    return Response(content=data, media_type="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="document.pdf"', "X-Content-Type-Options": "nosniff"})


@router.post("/public/{token}/send-code")
async def send_code(token: str, request: Request):
    _throttle(code_limiter, f"esign-code|{client_ip(request)}")
    code = E.new_otp()
    async with pm.session() as s:
        e = await _by_token(s, token)
        if e.status != "sent":
            raise HTTPException(409, "This document is not waiting for a signature.")
        e.otp_hash, e.otp_expires_at, e.otp_attempts = E.hash_code(code, e.id), pm.now_utc() + timedelta(minutes=OTP_MINUTES), 0
        channel = e.otp_channel or "email"
        status, detail = "not_configured", ""
        if channel == "whatsapp" and e.signer_phone:
            wa = await store.load(e.org_id, "whatsapp")
            r = await mirror.send_whatsapp(e.org_id, wa, to=e.signer_phone, template=(wa["config"].get("template_otp") or ""),
                                           params=[code, e.title[:60]], kind="esign_otp")
            status, detail = ("sent" if r["status"] == "sent" else r["status"]), (r.get("error") or "")
        elif e.signer_email:
            ok = await send_email(e.signer_email, f"Your signing code: {code}",
                                  f"Your one-time code to sign '{e.title}' is {code}. It works for {OTP_MINUTES} minutes.")
            status = "sent" if ok else "not_configured"
            detail = "" if ok else "No mail server is set up on this installation."
        else:
            detail = "No contact on record for the one-time code."
        e.otp_status = status
        _event(e, f"One-time code {status}" + (f" ({detail})" if detail else ""), _ip(request))
        await s.commit()
    out = {"otp_status": status, "message": ("Code sent. It works for 10 minutes." if status == "sent"
                                             else f"The code could not be sent: {detail or 'provider not configured'}")}
    if status != "sent" and identity.show_links():
        out["dev_code"] = code          # test machines only — never in production
    return out


class SignIn(BaseModel):
    code: str
    typed_name: str
    agree: bool = False


@router.post("/public/{token}/sign")
async def sign(token: str, body: SignIn, request: Request):
    _throttle(pub_limiter, f"esign|{client_ip(request)}")
    if not body.agree:
        raise HTTPException(422, "Tick the box to confirm you agree to sign electronically.")
    typed = (body.typed_name or "").strip()
    if len(typed) < 2:
        raise HTTPException(422, "Type your full name.")
    async with pm.session() as s:
        e = await _by_token(s, token)
        if e.status != "sent":
            raise HTTPException(409, "This document is not waiting for a signature.")
        if not e.otp_hash or not e.otp_expires_at or pm.aware(e.otp_expires_at) < pm.now_utc():
            raise HTTPException(410, "Ask for a new code first — the last one has expired.")
        if e.otp_attempts >= OTP_MAX_TRIES:
            raise HTTPException(429, "Too many wrong codes. Ask for a new code.")
        if E.hash_code(body.code, e.id) != e.otp_hash:
            e.otp_attempts += 1
            _event(e, "Wrong code entered", _ip(request))
            await s.commit()
            raise HTTPException(401, "That code is not right.")
        now = pm.now_utc()
        ip, ua = _ip(request), (request.headers.get("user-agent") or "")[:300]
        via = f"one-time code to {e.signer_email or ('phone ending ' + (e.signer_phone or '')[-4:])}"
        _event(e, f"Code verified; signed by {typed}", ip)
        lines = E.signature_lines(signer_name=e.signer_name, typed_name=typed, verified_via=via, ip=ip, signed_at=now,
                                  doc_sha256=e.pdf_sha256, envelope_id=e.id)
        cert = {"title": e.title, "envelope_id": e.id, "signer_name": e.signer_name,
                "signer_contact": e.signer_email or e.signer_phone or "", "verified_via": via, "typed_name": typed,
                "signed_at": now.strftime("%Y-%m-%d %H:%M:%S"), "ip": ip, "user_agent": ua,
                "original_sha256": e.pdf_sha256, "events": e.events, "note": E.LEGAL_NOTE}
        try:
            signed_pdf = E.stamp_and_certify(e.pdf, block_lines=lines, certificate=cert)
        except Exception as ex:  # noqa: BLE001
            raise HTTPException(500, f"The signature block could not be stamped ({type(ex).__name__}). Nothing was signed.")
        e.signed_pdf, e.signed_sha256 = signed_pdf, E.sha256_hex(signed_pdf)
        e.status, e.signed_at, e.typed_name, e.signer_ip, e.signer_user_agent = "signed", now, typed, ip, ua
        e.otp_hash = None
        await s.commit()
        org, eid, title, emp = e.org_id, e.id, e.title, e.employee_id
    await identity._audit(org, {"email": "signer", "role": "system"}, "integration.esign.signed", eid,
                          f"E-signed: {title} by {typed}", {"sha256_signed": E.sha256_hex(signed_pdf), "ip": ip})
    await notify(org, roles=["hr_admin", "org_admin"], title=f"Signed: {title}", link="/integrations?tab=esign", kind="letter")
    await events.emit(org, "esign.signed", {"envelope_id": eid, "title": title, "employee_id": emp, "signed_by": typed})
    return {"ok": True, "status": "signed", "signed_sha256": E.sha256_hex(signed_pdf), "message": "Signed. You can download your copy."}


class DeclineIn(BaseModel):
    reason: str = ""


@router.post("/public/{token}/decline")
async def decline(token: str, body: DeclineIn, request: Request):
    _throttle(pub_limiter, f"esign|{client_ip(request)}")
    async with pm.session() as s:
        e = await _by_token(s, token)
        if e.status != "sent":
            raise HTTPException(409, "This document is not waiting for a signature.")
        e.status, e.declined_reason = "declined", (body.reason or "").strip()[:300] or None
        _event(e, f"Declined: {e.declined_reason or 'no reason given'}", _ip(request))
        await s.commit()
        org, eid, title = e.org_id, e.id, e.title
    await notify(org, roles=["hr_admin", "org_admin"], title=f"Declined to sign: {title}", link="/integrations?tab=esign", kind="letter")
    await events.emit(org, "esign.declined", {"envelope_id": eid, "title": title, "reason": body.reason})
    return {"ok": True, "status": "declined"}
