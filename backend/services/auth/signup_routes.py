"""Auth service — public self-serve sign-up (Canada) → verified e-mail → trial org.

    GET  /auth/signup/config              plans on offer (CAD), provinces, trial length, captcha site key
    POST /auth/signup                     start: validate, rate-limit, captcha, e-mail the link
    GET  /auth/signup/verify/{token}      who / what the link is for (does not use it)
    POST /auth/signup/verify/{token}      create the org (14-day trial, Canada defaults) + first
                                          admin, then sign in (or the MFA set-up step)
    GET  /auth/setup-checklist            signed in: what the new org still has to set up

E-mail honesty: the link is e-mailed when SMTP is configured. Without SMTP the
response says so; the link itself is returned ONLY when LEXORA_DEV_SHOW_LINKS=1
(local / test set-ups), never otherwise.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import sys
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils.audit import record_audit  # noqa: E402
from shared.utils.config import settings  # noqa: E402
from shared.utils.email import brand_html, send_email, smtp_configured  # noqa: E402
from shared.utils.enums import PROVINCES_ALL  # noqa: E402
from shared.utils.org_time import province_tz  # noqa: E402
from shared.utils.internal import internal_headers  # noqa: E402
from shared.utils.security import hash_password  # noqa: E402

import abuse  # noqa: E402
import anti_automation as aa  # noqa: E402  (WASA 2026-10-21: proof-of-work CAPTCHA, purpose "signup")
import account_security  # noqa: E402
import subscription  # noqa: E402
from constants import PLAN_MODULES  # noqa: E402
from database import get_db  # noqa: E402
from sales_tax_invoice import PROVINCE_NAMES  # noqa: E402
from models import BillingPlan, Organization, User  # noqa: E402
from password_guard import breach_problems  # noqa: E402
from saas_models import SignupRequest, UserMfa  # noqa: E402
from security_policy import POLICY_TEXT, password_problems  # noqa: E402

router = APIRouter(prefix="/auth", tags=["signup"])

LINK_HOURS = int(os.getenv("LEXORA_SIGNUP_LINK_HOURS", "24"))
TERMS_VERSION = os.getenv("LEXORA_TERMS_VERSION", "draft-2026-10")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _now():
    return datetime.now(timezone.utc)


def _aware(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def public_signup_enabled() -> bool:
    """Decision 2026-10-21: public self-serve sign-up is OFF unless LEXORA_PUBLIC_SIGNUP=1.
    New customers are opened by the Lexora HRM operator (platform "New tenant") or by invite,
    the same stance as the hospital app's register-org. The local e2e stack switches it on
    to exercise the flow."""
    return (os.getenv("LEXORA_PUBLIC_SIGNUP") or "").strip().lower() in ("1", "true", "yes", "on")


SIGNUP_OFF = "Self sign-up is not open. Contact Lexora HRM to open an account for your company."


def _require_signup_open():
    if not public_signup_enabled():
        raise HTTPException(404, SIGNUP_OFF)


def _dev_links() -> bool:
    return os.getenv("LEXORA_DEV_SHOW_LINKS", "").strip().lower() in ("1", "true", "yes")


def _link(raw: str) -> str:
    base = os.getenv("APP_BASE_URL", "http://localhost:5173").rstrip("/")
    return f"{base}/signup/verify/{raw}"


def _throttle(limiter, key: str):
    wait = abuse.hit(limiter, key)
    if wait:
        raise HTTPException(429, f"Too many attempts. Try again in {max(wait // 60, 1)} minute(s).",
                            headers={"Retry-After": str(wait)})


async def _self_serve_plans(db: AsyncSession) -> list[dict]:
    rows = (await db.execute(select(BillingPlan).where(
        BillingPlan.country == "CA", BillingPlan.active.is_(True)).order_by(BillingPlan.sort))).scalars().all()
    out = []
    for p in rows:
        if p.self_serve is False:
            continue
        out.append({"key": p.plan_key, "label": p.label, "pepm": float(p.pepm), "min_monthly": float(p.min_monthly),
                    "custom": p.custom, "includes": p.includes, "max_employees": p.max_employees,
                    "modules": p.modules or PLAN_MODULES.get(p.plan_key, []),
                    "trial_days": p.trial_days or subscription.TRIAL_DAYS})
    return out


SIGNUP_ADDONS = ("agentic",)      # add-ons a company may tick on the public sign-up (PEPM, any plan)


async def _self_serve_addons(db: AsyncSession) -> list[dict]:
    """The add-ons offered on the sign-up form, from the live catalogue (seeded by pricing.ADDONS)."""
    from billing import load_catalog
    _, cat = await load_catalog(db, "CA")
    out = []
    for key in SIGNUP_ADDONS:
        a = cat.get(key)
        if a and a.get("active", True) is not False:
            out.append({"key": key, "label": a["label"], "kind": a.get("kind", "pepm"), "price": float(a["price"]),
                        "agents": ["Payroll Auditor", "HR Desk"] if key == "agentic" else [],
                        "note": ("Starts in shadow mode: the agents show what they would have done for two payroll cycles "
                                 "before anything runs. Every action is approval-gated and logged.") if key == "agentic" else ""})
    return out


@router.get("/signup/config")
async def signup_config(db: AsyncSession = Depends(get_db)):
    if not public_signup_enabled():
        return {"enabled": False, "message": SIGNUP_OFF}
    return {"enabled": True, "country": "CA", "currency": "CAD", "plans": await _self_serve_plans(db),
            "addons": await _self_serve_addons(db),
            "provinces": [{"code": p, "name": PROVINCE_NAMES.get(p, p)} for p in PROVINCES_ALL],
            "trial_days": subscription.TRIAL_DAYS, "password_policy": POLICY_TEXT,
            "captcha": abuse.captcha_info(), "terms_version": TERMS_VERSION,
            "email_configured": smtp_configured()}


class SignupIn(BaseModel):
    org_name: str
    admin_email: str
    admin_password: str
    admin_first_name: str
    admin_last_name: str | None = None
    phone: str | None = None
    province: str
    plan: str = "growth"
    addons: list[str] = []                # e.g. ["agentic"] — "Add agentic operations" on the form
    employees_estimate: int | None = None
    accept_terms: bool = False
    captcha_token: str | None = None
    website: str | None = None            # honeypot: real people leave it empty


@router.post("/signup", status_code=202)
async def signup_start(body: SignupIn, request: Request, db: AsyncSession = Depends(get_db)):
    _require_signup_open()
    ip = account_security.client_ip(request)
    _throttle(abuse.signup_ip_limiter, f"signup-ip|{ip}")
    email = (body.admin_email or "").strip().lower()
    # Our own proof of work first; the Turnstile / hCaptcha hook below stays as an extra.
    aa.require_pow(body.captcha_token, request, purposes=("signup",), account=email or None)
    reply = {"ok": True, "email": email,
             "message": "Check your inbox: we sent a link to confirm your e-mail. It works for "
                        f"{LINK_HOURS} hours."}
    if body.website:                                    # bot filled the hidden field
        return reply
    if not _EMAIL_RE.match(email):
        raise HTTPException(422, "Enter a valid work e-mail")
    _throttle(abuse.signup_email_limiter, f"signup-email|{email}")
    if not await abuse.verify_captcha(body.captcha_token, ip):
        raise HTTPException(400, "Please complete the 'I am human' check")
    if not body.accept_terms:
        raise HTTPException(422, "Please accept the Terms of Service and Privacy Policy")
    name = (body.org_name or "").strip()
    if len(name) < 2 or len(name) > 200:
        raise HTTPException(422, "Enter your company name")
    if not (body.admin_first_name or "").strip():
        raise HTTPException(422, "Enter your first name")
    state = (body.province or "").strip().upper()
    if state not in PROVINCES_ALL:
        raise HTTPException(422, "Choose the province or territory where your company is registered")
    offered = {p["key"]: p for p in await _self_serve_plans(db)}
    if body.plan not in offered:
        raise HTTPException(422, f"Choose a plan: {', '.join(offered) or 'none is offered right now'}")
    problems = password_problems(body.admin_password, email=email) + breach_problems(body.admin_password)
    if problems:
        raise HTTPException(422, "Password needs: " + ", ".join(problems))
    offered_addons = {a["key"] for a in await _self_serve_addons(db)}
    bad_addons = [a for a in (body.addons or []) if a not in offered_addons]
    if bad_addons:
        raise HTTPException(422, f"Unknown add-on(s): {bad_addons}")
    addons = list(dict.fromkeys(body.addons or []))
    raw = secrets.token_urlsafe(32)
    db.add(SignupRequest(email=email, org_name=name, admin_first_name=body.admin_first_name.strip()[:100],
                         admin_last_name=(body.admin_last_name or "").strip()[:100] or None,
                         phone=(body.phone or "").strip()[:30] or None, state=state, plan=body.plan,
                         employees_estimate=body.employees_estimate, addons=addons or None,
                         hashed_password=hash_password(body.admin_password), token_hash=_hash(raw),
                         expires_at=_now() + timedelta(hours=LINK_HOURS), ip=ip,
                         user_agent=(request.headers.get("user-agent") or "")[:300],
                         accepted_terms_version=TERMS_VERSION))
    await db.commit()
    link = _link(raw)
    lines = [f"Hello {body.admin_first_name.strip()},",
             f"Confirm your e-mail to create {name} on Lexora HRM. Your {offered[body.plan]['trial_days']}-day "
             "free trial starts when you confirm.",
             f"The link works once and expires in {LINK_HOURS} hours. If you did not sign up, ignore this e-mail."]
    sent = await send_email(email, "Confirm your e-mail — Lexora HRM", "\n\n".join(lines + [link]),
                            html=brand_html("Confirm your e-mail", lines, "Confirm and start my trial", link))
    reply["email_sent"] = sent
    if not sent:
        reply["message"] = ("E-mail is not configured on this server, so the confirmation link could not "
                            "be sent. Contact Lexora HRM support to finish your sign-up.")
        if _dev_links():
            reply["dev_link"] = link
            reply["message"] = "E-mail is not configured (local set-up): open the link below to confirm."
    return reply


async def _pending(db: AsyncSession, token: str) -> SignupRequest:
    s = (await db.execute(select(SignupRequest).where(SignupRequest.token_hash == _hash(token or "")))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "This link is not valid. Sign up again.")
    if s.verified_at:
        raise HTTPException(410, "This link was already used. Sign in with your organization code.")
    if _aware(s.expires_at) < _now():
        raise HTTPException(410, "This link has expired. Sign up again.")
    return s


@router.get("/signup/verify/{token}")
async def signup_peek(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    _require_signup_open()
    _throttle(abuse.verify_limiter, f"verify|{account_security.client_ip(request)}")
    s = await _pending(db, token)
    return {"org_name": s.org_name, "email": s.email, "province": s.state, "plan": s.plan,
            "expires_at": _aware(s.expires_at).isoformat(), "trial_days": subscription.TRIAL_DAYS}


async def seed_canada_defaults(org_id) -> dict:
    """Canadian master data (departments, locations, leave types, provincial stat holidays) via the
    employee service's own idempotent /masters/seed-defaults. Best-effort: a failure is reported on
    the checklist (with a retry button), never blocks the sign-up."""
    try:
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.post(f"{settings.EMPLOYEE_URL}/masters/seed-defaults", headers=internal_headers(org_id, "auth"))
        if r.status_code < 300:
            return {"ok": True, "result": r.json()}
        return {"ok": False, "error": f"employee service answered {r.status_code}"}
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        return {"ok": False, "error": f"employee service unreachable ({exc.__class__.__name__})"}


@router.post("/signup/verify/{token}", status_code=201)
async def signup_verify(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    from routers import _gen_unique_code, _log_session, finish_sign_in
    _require_signup_open()
    _throttle(abuse.verify_limiter, f"verify|{account_security.client_ip(request)}")
    s = await _pending(db, token)
    # WASA: single use under a double submit. Claim the link with a guarded UPDATE
    # in THIS transaction: a second request racing past _pending blocks on the row
    # lock until the first commits, then matches no row → 410, and no second org.
    claimed = (await db.execute(
        update(SignupRequest).where(SignupRequest.id == s.id, SignupRequest.verified_at.is_(None))
        .values(verified_at=_now()).returning(SignupRequest.id)
        .execution_options(synchronize_session=False))).first()
    if claimed is None:
        await db.rollback()
        raise HTTPException(410, "This link was already used. Sign in with your organization code.")
    org = Organization(name=s.org_name, legal_name=s.org_name, country="CA", province=s.state,
                       timezone=province_tz(s.state), plan=s.plan, billing_province=s.state,
                       billing_email=s.email, signup_source="self_serve",
                       addons=list(s.addons or []) or None)     # add-on purchased ⇒ agentic on, shadow mode (ai_platform/agentic_state.py)
    org.code = await _gen_unique_code(db, s.org_name)
    row = await subscription.plan_row(db, "CA", s.plan)
    subscription.start_trial(org, days=(row.trial_days if row is not None and row.trial_days else None))
    db.add(org)
    await db.flush()
    admin = User(org_id=org.id, email=s.email, hashed_password=s.hashed_password,
                 first_name=s.admin_first_name, last_name=s.admin_last_name, role="org_admin",
                 branch_scope="org", password_changed_at=_now())
    db.add(admin)
    s.org_id = org.id                    # verified_at was set by the claim above
    await subscription.record_change(db, org, from_status=None, from_plan=None, source="signup",
                                     note=f"self-serve sign-up, terms {s.accepted_terms_version}", actor=s.email)
    await db.commit()
    await db.refresh(admin)
    seeded = await seed_canada_defaults(org.id)
    hdr = {"x-org-id": str(org.id), "x-user-id": str(admin.id), "x-user-email": admin.email, "x-role": "org_admin"}
    await record_audit(type("R", (), {"headers": hdr})(), "org.signup", entity_type="organization",
                       entity_id=str(org.id), summary=f"Self-serve sign-up: {org.name} ({org.code}), "
                       f"{subscription.TRIAL_DAYS}-day trial on {org.plan}",
                       meta={"province": s.state, "plan": s.plan, "addons": list(s.addons or []), "canada_defaults": seeded.get("ok"),
                             "terms_version": s.accepted_terms_version})
    await _log_session(db, request, email=admin.email, user=admin, success=True, reason="signup")
    out = await finish_sign_in(admin, db, request, "signup")
    payload = out if isinstance(out, dict) else out.model_dump()
    return {**payload, "org_code": org.code, "org_name": org.name,
            "trial_ends_at": org.trial_ends_at.isoformat() if org.trial_ends_at else None,
            "canada_defaults": seeded}


# ── setup checklist ──────────────────────────────────────────────────────────

async def _count(db: AsyncSession, sql: str, org_id) -> int:
    try:
        return int((await db.execute(text(sql), {"o": org_id})).scalar() or 0)
    except Exception:
        await db.rollback()
        return -1                          # table not there (service not booted) → unknown


@router.get("/setup-checklist")
async def setup_checklist(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        org_id = uuid.UUID(request.headers.get("x-org-id") or "")
    except ValueError:
        raise HTTPException(401, "Not authenticated")
    org = (await db.execute(select(Organization).where(Organization.id == org_id))).scalar_one_or_none()
    if not org:
        raise HTTPException(404, "Organization not found")
    try:
        uid = uuid.UUID(request.headers.get("x-user-id") or "")
    except ValueError:
        uid = None
    mfa_on = bool(uid and (await db.execute(select(UserMfa).where(
        UserMfa.user_id == uid, UserMfa.enabled.is_(True)))).scalar_one_or_none())
    # read the org BEFORE the counts: a failed count rolls back and expires it
    company_done = bool(org.cra_business_number and org.billing_email)
    plan_done = (org.subscription_status or "active") == "active"
    trial_ends = org.trial_ends_at.isoformat() if org.trial_ends_at else None
    eff_status = subscription.effective_status(org.subscription_status, org.access_until)
    leave =await _count(db, "SELECT count(*) FROM master_leave_types WHERE org_id = :o", org_id)
    holidays = await _count(db, "SELECT count(*) FROM master_stat_holidays WHERE org_id = :o", org_id)
    emps = await _count(db, "SELECT count(*) FROM employees WHERE org_id = :o AND is_active = TRUE", org_id)
    users = await _count(db, "SELECT count(*) FROM users WHERE org_id = :o", org_id)
    items = [
        {"key": "canada_defaults", "label": "Leave types and provincial statutory holidays", "done": leave > 0 and holidays > 0,
         "link": "/masters", "action": "seed_defaults"},
        {"key": "mfa", "label": "Turn on two-step sign-in for your login", "done": mfa_on, "link": "/security/account"},
        {"key": "company", "label": "Company details: legal name, CRA Business Number, billing e-mail",
         "done": company_done, "link": "/settings/entities"},
        {"key": "first_employee", "label": "Add your first employees (or import a sheet)", "done": emps > 0, "link": "/employees"},
        {"key": "invite", "label": "Invite a colleague (HR or payroll admin)", "done": users > 1, "link": "/users"},
        {"key": "plan", "label": "Choose a plan before the trial ends",
         "done": plan_done, "link": "/plan-billing"},
    ]
    done = sum(1 for i in items if i["done"])
    return {"items": items, "done": done, "total": len(items), "pct": round(100 * done / len(items)),
            "trial_ends_at": trial_ends, "status": eff_status}


@router.post("/setup-checklist/seed-defaults")
async def retry_seed(request: Request, db: AsyncSession = Depends(get_db)):
    if request.headers.get("x-role") not in ("org_admin", "hr_admin", "superadmin"):
        raise HTTPException(403, "Only admins may seed the default master data")
    try:
        org_id = uuid.UUID(request.headers.get("x-org-id") or "")
    except ValueError:
        raise HTTPException(401, "Not authenticated")
    org = (await db.execute(select(Organization).where(Organization.id == org_id))).scalar_one_or_none()
    if not org:
        raise HTTPException(404, "Organization not found")
    return await seed_canada_defaults(org.id)
