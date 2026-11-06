"""Auth service — org registration, login, current user."""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils import identity  # noqa: E402
from shared.utils.security import (create_access_token, create_refresh_token,  # noqa: E402
                                   decode_token, hash_password, verify_password)
from shared.utils.rbac_models import access_from, custom_roles_for  # noqa: E402  (org Roles & Permissions)
import ui_grant  # noqa: E402  (2026-10-24: ES256 signed UI grant)

from constants import MODULES, PLAN_MODULES, ROLES, resolve_modules  # noqa: E402
from codes import base_code  # noqa: E402
from shared.utils.org_time import province_tz  # noqa: E402
from pricing import (  # noqa: E402
    ADDONS, COUNTRY_META, PEPM_PLANS, PLAN_PRICING, SUPPORTED_COUNTRIES, country_meta,
    currency_for, price_for,
)


async def _gen_unique_code(db: AsyncSession, name: str) -> str:
    """A unique uppercase login code for a new org, derived from its name."""
    base = base_code(name)
    cand, i = base, 2
    while (await db.execute(select(Organization.id).where(
            func.upper(Organization.code) == cand))).scalar_one_or_none():
        cand = f"{base[:10]}{i}"
        i += 1
    return cand
from database import get_db  # noqa: E402
from models import LoginSession, Organization, User  # noqa: E402
from schemas import LoginRequest, RegisterOrg, TokenResponse  # noqa: E402
from security_policy import account_limiter, limiter, password_problems  # noqa: E402
import abuse  # noqa: E402
import account_security  # noqa: E402
import mfa  # noqa: E402
import subscription  # noqa: E402
from password_guard import breach_problems  # noqa: E402
import anti_automation as aa  # noqa: E402  (WASA 2026-10-21: our own proof-of-work CAPTCHA)


def direct_register_allowed() -> bool:
    """/auth/register-org (instant org, no e-mail check) is for local set-ups and the
    e2e scripts. In production (LEXORA_ENV=production) it is off unless
    LEXORA_DIRECT_REGISTER=1; the public front door is /auth/signup."""
    flag = os.getenv("LEXORA_DIRECT_REGISTER", "").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    if flag in ("1", "true", "yes", "on"):
        return True
    return os.getenv("LEXORA_ENV", "").strip().lower() != "production"


async def _mfa_row(db: AsyncSession, user_id):
    from saas_models import UserMfa
    return (await db.execute(select(UserMfa).where(UserMfa.user_id == user_id))).scalar_one_or_none()


async def finish_sign_in(user: User, db: AsyncSession, request: Request | None, method: str = "password"):
    """After the password (or SSO / sign-up) is proven: either tokens, or the MFA
    step this login still owes. Used by /login, /signup/verify and /register-org."""
    row = await _mfa_row(db, user.id)
    if row is not None and row.enabled:
        return {"mfa_required": True, "mfa_token": account_security.make_challenge(user, "verify", method),
                "org_id": str(user.org_id), "role": user.role}
    if mfa.required_for(user.role):
        return {"mfa_setup_required": True, "mfa_token": account_security.make_challenge(user, "enroll", method),
                "org_id": str(user.org_id), "role": user.role,
                "reason": "Two-step sign-in is required for your role. Set it up now with an authenticator app."}
    return await _tokens(user, db, request, method)

router = APIRouter(prefix="/auth", tags=["auth"])


async def _tokens(user: User, db: AsyncSession, request: Request | None = None,
                  method: str = "password") -> TokenResponse:
    # Subscribed modules travel IN the JWT (same as RBAC permissions) so the
    # gateway can enforce the paywall on every request without a per-call lookup.
    # superadmin operates above the org layer → full module set.
    if user.role == "superadmin":
        modules = list(MODULES)
    else:
        org = (await db.execute(
            select(Organization).where(Organization.id == user.org_id))).scalar_one_or_none()
        modules = await subscription.org_modules(db, org)       # S4: plan modules are catalogue DATA
    # WASA #7: a password the person set THEMSELVES since the flag was raised
    # (a reset / invite link stamps password_changed_at) ends the forced change.
    if getattr(user, "must_change_password", False) and user.password_changed_at is not None:
        user.must_change_password = False
    pwchg = bool(getattr(user, "must_change_password", False))
    # S4: one revocable session row per sign-in; its id travels as `sid`.
    sid = await account_security.open_session(db, user, request, method)
    await db.commit()
    claims = {
        "sid": sid,
        "amr": method,
        "sub": str(user.id),
        "org_id": str(user.org_id),
        "email": user.email,
        "role": user.role,
        # The employee record this login maps to — lets services answer
        # "who are my reports?" for manager-scoped approvals.
        "employee_id": str(user.employee_id) if user.employee_id else None,
        # Tenancy scope (Phase 1 multi-branch): the gateway stamps X-Branch-Id from
        # these in Phase 2. "org" scope = all branches; "branch" = branch_id only.
        "branch_scope": getattr(user, "branch_scope", None) or "branch",
        "branch_id": str(user.branch_id) if user.branch_id else None,
        "permissions": ROLES.get(user.role, []),
        "modules": modules,
    }
    if pwchg:
        # The gateway refuses this token everywhere except the change-password routes.
        claims["pwchg"] = True
    access = create_access_token(claims)
    return TokenResponse(
        access_token=access,
        refresh_token=create_refresh_token({"sub": str(user.id), "org_id": str(user.org_id)}),
        org_id=str(user.org_id),
        role=user.role,
        password_change_required=pwchg,
        # 2026-10-24 (HMS Entersoft HIGH): ES256-signed role + permissions — the web UI trusts only this.
        ui_grant=await _ui_grant_for(db, user, decode_token(access)),
    )


async def _ui_grant_for(db: AsyncSession, user: User, claims: dict) -> str | None:
    """The signed UI grant for a verified access token's claims + the login's LIVE roles (the same rows the
    gateway honours). None when no signing key is configured (the web app then refuses the session)."""
    try:
        customs = await custom_roles_for(db, user.id) if user.role != "superadmin" else []
    except Exception:  # noqa: BLE001 — tables not created yet on an old database: system role only
        await db.rollback()
        customs = []
    return ui_grant.sign(claims, access_from(user.role, customs))


@router.post("/register-org", status_code=201)
async def register_org(body: RegisterOrg, request: Request, db: AsyncSession = Depends(get_db)):
    """Instant tenant + first org_admin, no e-mail check — for local set-ups and the
    e2e scripts (off in production, see direct_register_allowed). The public
    SaaS front door is /auth/signup (verify e-mail → trial org)."""
    if not direct_register_allowed():
        raise HTTPException(404, "Not found")
    aa.require_pow(body.captcha_token, request, purposes=("signup",), account=body.admin_email)
    wait = abuse.hit(abuse.signup_ip_limiter, f"register|{_client_ip(request)}") if os.getenv(
        "LEXORA_ENV", "").strip().lower() == "production" else 0
    if wait:
        raise HTTPException(429, "Too many sign-ups from this network. Try again later.",
                            headers={"Retry-After": str(wait)})
    exists = await db.execute(select(User).where(func.lower(User.email) == body.admin_email.lower()))
    if exists.scalars().first():
        raise HTTPException(400, "Email already registered")
    problems = password_problems(body.admin_password, email=body.admin_email) + breach_problems(body.admin_password)
    if problems:
        raise HTTPException(422, "Password needs: " + ", ".join(problems))

    if (body.country or "CA").upper() != "CA":
        raise HTTPException(422, "Lexora HRM is Canada-only: country must be CA")
    province = (body.province or "").upper()
    if province not in COUNTRY_META["CA"]["regions"]:
        raise HTTPException(422, "Choose a Canadian province or territory (ON, BC, AB, QC …)")
    org = Organization(name=body.org_name, legal_name=body.org_name,
                       country="CA", province=province, plan=body.plan,
                       timezone=province_tz(province))
    org.code = await _gen_unique_code(db, body.org_name)
    org.signup_source = "register_api"
    subscription.start_trial(org)
    db.add(org)
    await db.flush()  # get org.id

    admin = User(org_id=org.id, email=body.admin_email,
                 hashed_password=hash_password(body.admin_password),
                 first_name=body.admin_first_name, last_name=body.admin_last_name,
                 role="org_admin")
    db.add(admin)
    await db.commit()
    await db.refresh(admin)
    return await finish_sign_in(admin, db, request, "password")


def pick_login(matches: list, org_id, has_code: bool = False):
    """Which of the logins sharing an e-mail is signing in.

    With an org code → the login in THAT org (or, failing that, a superadmin,
    who may type any code). Without a code → only a superadmin can match; a
    tenant login is returned only so the caller can say "code is required"."""
    if org_id is not None:
        for u in matches:
            if u.org_id == org_id:
                return u
    for u in matches:
        if u.role == "superadmin":
            return u
    if not has_code and len(matches) == 1:
        return matches[0]
    return None


async def _sso_password_allowed(org_id: str, role: str) -> bool:
    """G3 hook: False only when the org switched 'SSO required' on and the role is not an admin."""
    try:
        import sso
        from shared.utils import platform_models as _pm
        from shared.utils.providers.models import SsoConfig
        async with _pm.session() as s:
            cfg = (await s.execute(select(SsoConfig).where(SsoConfig.org_id == org_id))).scalar_one_or_none()
        return sso.password_login_allowed(cfg, role)
    except Exception:          # noqa: BLE001 — never lock everyone out because the SSO table is unreachable
        return True


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]


async def _log_session(db: AsyncSession, request: Request, *, email: str, user: User | None,
                       success: bool, reason: str | None = None):
    """Best-effort sign-in record for the session audit screen."""
    try:
        db.add(LoginSession(org_id=user.org_id if user else None, user_id=user.id if user else None,
                            email=email.lower(), role=user.role if user else None,
                            ip=_client_ip(request), user_agent=(request.headers.get("user-agent") or "")[:300],
                            success=success, reason=reason))
        await db.commit()
    except Exception:
        await db.rollback()


@router.post("/login")
async def login(body: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    # Rate limit: 5 failures per email+IP in 15 min → locked 15 min (429).
    key = f"{body.email.lower()}|{_client_ip(request)}"
    # S4: a second, per-ACCOUNT lock (any IP) stops slow distributed guessing.
    acct_key = f"{body.email.lower()}|{(body.org_code or '').strip().upper()}"
    wait = limiter.retry_after(key) or account_limiter.retry_after(acct_key)
    if wait:
        await _log_session(db, request, email=body.email, user=None, success=False, reason="locked")
        raise HTTPException(429, f"Too many failed sign-ins — try again in {max(wait // 60, 1)} minute(s)",
                            headers={"Retry-After": str(wait)})
    # WASA 2026-10-21: the proof-of-work CAPTCHA is checked BEFORE the password.
    aa.require_pow(body.captcha_token, request, purposes=("login",), account=body.email)
    # E-mail is unique PER ORG, so the same address may exist in two orgs: the
    # organization code picks the org first, then the e-mail picks the login.
    # The superadmin (platform operator) sits above the org layer and signs in
    # without a code.
    code = (body.org_code or "").strip()
    matches = (await db.execute(select(User).where(
        func.lower(User.email) == body.email.lower()))).scalars().all()
    org = None
    if code:
        org = (await db.execute(select(Organization).where(
            func.upper(Organization.code) == code.upper()))).scalar_one_or_none()
    user = pick_login(matches, org.id if org else None, has_code=bool(code))
    if not user or user.password_set is False or not verify_password(body.password, user.hashed_password):
        limiter.record_failure(key)
        account_limiter.record_failure(acct_key)
        aa.note_login_failure(body.email, request)          # more proof-of-work next time
        wrong_org = bool(code) and bool(matches) and not user
        await _log_session(db, request, email=body.email, user=user or (matches[0] if matches else None),
                           success=False, reason="bad_org_code" if wrong_org else "bad_password")
        raise HTTPException(401, "Invalid credentials for that organization code" if wrong_org
                            else "Invalid credentials")
    if user.role != "superadmin":
        if not code:
            raise HTTPException(400, "Organization code is required")
        if not org.is_active:
            await _log_session(db, request, email=body.email, user=user, success=False, reason="org_suspended")
            raise HTTPException(403, "This organization is suspended — contact Lexora HRM support")
    # Said only AFTER the password matched, so it cannot be used to probe e-mails.
    if not user.is_active:
        await _log_session(db, request, email=body.email, user=user, success=False, reason="disabled")
        raise HTTPException(403, "This login has been disabled. Contact your HR team.")
    # G3: "SSO required" — password sign-in stays open for admins only (break-glass).
    if user.role != "superadmin" and not await _sso_password_allowed(str(user.org_id), user.role):
        await _log_session(db, request, email=body.email, user=user, success=False, reason="sso_required")
        raise HTTPException(403, "Your company requires single sign-on. Use the \"Sign in with\" button.")
    limiter.record_success(key)
    account_limiter.record_success(acct_key)
    aa.note_login_success(body.email)
    # The gateway refuses tokens issued in or before the second of the login's
    # cut-off (`tokens_valid_after`: password just set, role just changed, …).
    # Wait out that second so the token issued now is unambiguously newer.
    cutoff = identity.aware(user.tokens_valid_after)
    if cutoff is not None:
        gap = int(cutoff.timestamp()) + 1 - datetime.now(timezone.utc).timestamp()
        if 0 < gap <= 1.5:
            await asyncio.sleep(gap)
    # S4: a login with MFA (or a role that must enrol) gets the second step first;
    # last_login + the success row are written when that step completes.
    out = await finish_sign_in(user, db, request, "password")
    if isinstance(out, dict):
        return out
    user.last_login = datetime.now(timezone.utc)
    await db.commit()
    await _log_session(db, request, email=body.email, user=user, success=True)
    return out


@router.get("/challenge")
async def challenge(request: Request, purpose: str = "login", account: str | None = None):
    """PUBLIC (gateway public list): a signed proof-of-work challenge for the
    login / mfa / forgot / signup forms, plus whether the check is on at all
    ({"enabled", "mode": "pow"|"off", "interactive"}). Stateless: nothing is
    stored until a solved token is presented."""
    if purpose not in ("login", "mfa", "forgot", "signup"):
        raise HTTPException(422, "purpose must be login, mfa, forgot or signup")
    acct = (account or "").strip()[:200] or None
    return aa.challenge_reply(purpose, acct, request)


@router.get("/me")
async def me(request: Request, db: AsyncSession = Depends(get_db)):
    uid = request.headers.get("x-user-id")
    if not uid:
        raise HTTPException(401, "Not authenticated")
    try:
        uid_v = uuid.UUID(str(uid))
    except ValueError:
        raise HTTPException(401, "Not authenticated")
    res = await db.execute(select(User).where(User.id == uid_v))
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")
    org = (await db.execute(
        select(Organization).where(Organization.id == user.org_id))).scalar_one_or_none()
    mfa_row = await _mfa_row(db, user.id)
    tok = ui_grant.claims_of_bearer(request.headers.get("authorization"))
    try:
        customs = await custom_roles_for(db, user.id) if user.role != "superadmin" else []
    except Exception:  # noqa: BLE001 — rbac tables missing on an old database: system role only
        await db.rollback()
        customs = []
    acc = access_from(user.role, customs)
    sub = None
    if org is not None and user.role != "superadmin":
        sub = {"status": subscription.effective_status(org.subscription_status, org.access_until),
               "mode": subscription.access_mode(org.subscription_status, org.access_until),
               "trial_ends_at": org.trial_ends_at.isoformat() if org.trial_ends_at else None,
               "banner": subscription.banner(org.subscription_status, org.access_until)}
    return {"subscription": sub,
            "mfa": {"enabled": bool(mfa_row and mfa_row.enabled), "required": mfa.required_for(user.role)},
            "modules": await subscription.org_modules(db, org) if user.role != "superadmin" else list(MODULES),
            "id": str(user.id), "org_id": str(user.org_id), "email": user.email,
            "role": user.role, "first_name": user.first_name, "last_name": user.last_name,
            "employee_id": str(user.employee_id) if user.employee_id else None,
            "branch_scope": getattr(user, "branch_scope", None) or "branch",
            "branch_id": str(user.branch_id) if user.branch_id else None,
            "org_name": org.name if org else None,
            "province": org.province if org else None,
            "country": org.country if org else "CA",
            "org_code": org.code if org else None,
            "plan": (org.plan if org else "growth"),
            "password_change_required": bool(getattr(user, "must_change_password", False)),
            # 2026-10-24: effective catalogue permissions + the signed grant bound to the caller's bearer token —
            # the web app takes role / permissions / modules ONLY from `ui_grant` (frontend/src/lib/uiGrant.ts).
            "permissions": acc["perms"], "roles": acc["roles"], "ui_roles": acc["ui_roles"],
            "custom_roles": acc["custom"], "reach": acc["reach"],
            "ui_grant": ui_grant.sign(tok, acc) if tok and tok.get("sub") == str(user.id) else None}


@router.get("/subscription")
async def get_subscription(request: Request, db: AsyncSession = Depends(get_db)):
    """The caller org's plan + resolved modules — powers the module gating."""
    org_id = request.headers.get("x-org-id")
    org = (await db.execute(select(Organization).where(Organization.id == org_id))).scalar_one_or_none() if org_id else None
    return {"plan": org.plan if org else "growth",
            "modules": resolve_modules(org.plan if org else None, org.modules if org else None),
            "all_modules": MODULES, "plans": PLAN_MODULES}


@router.patch("/subscription")
async def set_subscription(body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    """Set an org's plan and/or explicit module list. Superadmin only (the Lexora HRM
    operator). Targets the caller's org, or `org_id` in the body for cross-org."""
    if request.headers.get("x-role") != "superadmin":
        raise HTTPException(403, "Only the platform operator can change subscriptions")
    target = body.get("org_id") or request.headers.get("x-org-id")
    org = (await db.execute(select(Organization).where(Organization.id == target))).scalar_one_or_none()
    if not org:
        raise HTTPException(404, "Organization not found")
    if "plan" in body and body["plan"] in PLAN_MODULES:
        org.plan = body["plan"]
    if "modules" in body:
        org.modules = body["modules"]      # explicit override list (or null to fall back to plan)
    await db.commit()
    await db.refresh(org)
    return {"org_id": str(org.id), "plan": org.plan,
            "modules": resolve_modules(org.plan, org.modules)}


# ══════════════════════════ Lexora HRM platform admin (superadmin) ══════════════════

def _require_super(request: Request):
    if request.headers.get("x-role") != "superadmin":
        raise HTTPException(403, "Platform administration is for the Lexora HRM operator only")


@router.get("/platform/orgs")
async def platform_orgs(request: Request, db: AsyncSession = Depends(get_db)):
    """Every tenant with its plan, resolved modules and user count — the Lexora HRM
    operator's cross-tenant view (above the org layer)."""
    _require_super(request)
    orgs = (await db.execute(select(Organization).order_by(Organization.created_at.desc()))).scalars().all()
    counts = {str(oid): n for oid, n in (await db.execute(
        select(User.org_id, func.count(User.id)).group_by(User.org_id))).all()}
    admin_by_org: dict = {}
    for u in (await db.execute(select(User).where(User.role == "org_admin"))).scalars().all():
        admin_by_org.setdefault(str(u.org_id), u.email)
    out = [{"id": str(o.id), "name": o.name, "code": o.code, "plan": o.plan,
            "modules": resolve_modules(o.plan, o.modules), "explicit_modules": o.modules,
            "country": o.country, "province": o.province, "is_active": o.is_active,
            "addons": o.addons or [], "agentic": "agentic" in (o.addons or []),
            "currency": currency_for(o.country), "price": price_for(o.country, o.plan),
            "user_count": counts.get(str(o.id), 0), "admin_email": admin_by_org.get(str(o.id)),
            "created_at": o.created_at.isoformat() if o.created_at else None} for o in orgs]
    return {"orgs": out, "total": len(out), "all_modules": MODULES, "plans": PLAN_MODULES,
            "countries": COUNTRY_META, "plan_pricing": PLAN_PRICING,
            "pepm_plans": PEPM_PLANS, "addons": ADDONS,
            "total_users": sum(counts.values())}


class TenantCreate(BaseModel):
    org_name: str
    country: str = "CA"           # Canada-only (anything else is refused)
    province: str = "ON"          # one of the 13 provinces / territories
    plan: str = "growth"
    admin_email: str
    admin_first_name: str = "Admin"
    admin_last_name: str = ""
    admin_password: str


@router.post("/platform/orgs", status_code=201)
async def platform_create_org(body: TenantCreate, request: Request, db: AsyncSession = Depends(get_db)):
    """Provision a new tenant + its first org_admin — the Lexora HRM operator's front door."""
    _require_super(request)
    if (await db.execute(select(User).where(
            func.lower(User.email) == body.admin_email.lower()))).scalars().first():
        raise HTTPException(400, "That admin email is already registered")
    # WASA #7: the same policy + breached-password check as every other password.
    problems = password_problems(body.admin_password, email=body.admin_email) + breach_problems(body.admin_password)
    if problems:
        raise HTTPException(422, "Password needs: " + ", ".join(problems))
    country = (body.country or "CA").upper()
    if country not in SUPPORTED_COUNTRIES:
        raise HTTPException(400, f"Unsupported country '{country}' — Lexora HRM is Canada-only (CA)")
    province = (body.province or "").upper()
    if province not in country_meta(country)["regions"]:
        raise HTTPException(422, "Choose a Canadian province or territory (ON, BC, AB, QC …)")
    org = Organization(name=body.org_name, legal_name=body.org_name, country=country,
                       province=province, timezone=province_tz(province),
                       plan=(body.plan if body.plan in PLAN_MODULES else "growth"))
    org.code = await _gen_unique_code(db, body.org_name)
    db.add(org)
    await db.flush()
    admin = User(org_id=org.id, email=body.admin_email,
                 hashed_password=hash_password(body.admin_password),
                 first_name=body.admin_first_name, last_name=body.admin_last_name,
                 role="org_admin", branch_scope="org",
                 # WASA #7: the operator typed this password → the admin must choose
                 # their own at first sign-in (password_changed_at stays empty until then).
                 must_change_password=True, password_changed_at=None)
    db.add(admin)
    await db.commit()
    await db.refresh(org)
    return {"id": str(org.id), "name": org.name, "code": org.code, "country": org.country,
            "currency": currency_for(org.country), "price": price_for(org.country, org.plan),
            "plan": org.plan, "admin_email": body.admin_email,
            "modules": resolve_modules(org.plan, org.modules)}


@router.patch("/platform/orgs/{org_id}")
async def platform_update_org(org_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    """Set a tenant's plan / explicit modules / active status."""
    _require_super(request)
    org = (await db.execute(select(Organization).where(Organization.id == org_id))).scalar_one_or_none()
    if not org:
        raise HTTPException(404, "Organization not found")
    if "plan" in body and body["plan"] in PLAN_MODULES:
        org.plan = body["plan"]
    if "modules" in body:
        org.modules = body["modules"]      # explicit list, or null → plan default
    if "is_active" in body:
        org.is_active = bool(body["is_active"])
    if "agentic" in body:
        # Platform-console switch for the Agentic operations add-on (2026-10-25): adds / removes the
        # ``agentic`` add-on key — the ai_platform reads organizations.addons (agentic_state); on → shadow mode.
        from pricing import AGENTIC_ADDON_KEY
        cur = [a for a in (org.addons or []) if a != AGENTIC_ADDON_KEY]
        org.addons = (cur + [AGENTIC_ADDON_KEY]) if bool(body["agentic"]) else (cur or None)
    await db.commit()
    await db.refresh(org)
    return {"id": str(org.id), "name": org.name, "plan": org.plan, "addons": org.addons or [],
            "agentic": "agentic" in (org.addons or []),
            "modules": resolve_modules(org.plan, org.modules), "is_active": org.is_active}


# ── Org Admin logins: the platform operator only (Entersoft 2026-10-24 Medium) ─────────────────────────────
class PlatformAdminIn(BaseModel):
    email: str
    first_name: str = "Admin"
    last_name: str = ""


class PlatformRoleIn(BaseModel):
    role: str


async def _tenant(db: AsyncSession, org_id: str) -> Organization:
    try:
        oid = uuid.UUID(str(org_id))
    except ValueError:
        raise HTTPException(422, "That organisation id is not valid")
    org = (await db.execute(select(Organization).where(Organization.id == oid))).scalar_one_or_none()
    if not org:
        raise HTTPException(404, "Organization not found")
    return org


@router.get("/platform/orgs/{org_id}/users")
async def platform_org_users(org_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """A tenant's logins (no secrets) — so the operator can pick whom to make / unmake Org Admin."""
    _require_super(request)
    org = await _tenant(db, org_id)
    rows = (await db.execute(select(User).where(User.org_id == org.id, User.role != "superadmin")
                             .order_by(User.role, User.email))).scalars().all()
    return [{"id": str(u.id), "email": u.email, "name": " ".join(x for x in (u.first_name, u.last_name) if x),
             "role": u.role, "is_active": bool(u.is_active)} for u in rows]


@router.post("/platform/orgs/{org_id}/admins", status_code=201)
async def platform_add_org_admin(org_id: str, body: PlatformAdminIn, request: Request, db: AsyncSession = Depends(get_db)):
    """Create an Org Admin login in a tenant and send its set-password link (the operator never sets the password)."""
    _require_super(request)
    org = await _tenant(db, org_id)
    try:
        email = identity.clean_email(body.email)
    except identity.IdentityError as exc:
        raise HTTPException(exc.status, str(exc))
    if (await db.execute(select(User).where(User.org_id == org.id, func.lower(User.email) == email))).scalars().first():
        raise HTTPException(409, "That e-mail already has a login in this organisation. Change its role instead.")
    import secrets
    u = User(id=uuid.uuid4(), org_id=org.id, email=email, role="org_admin", branch_scope="org",
             first_name=body.first_name[:100], last_name=body.last_name[:100],
             hashed_password=hash_password(secrets.token_urlsafe(48)), password_set=False, is_active=True)
    db.add(u)
    await db.commit()
    actor = identity.actor_from(request)
    sent = await identity.send_link(str(org.id), u.id, identity.INVITE, actor=actor)
    from shared.utils.audit import record_audit
    await record_audit(_OrgRequest(request, str(org.id)), "platform.org_admin.create", entity_type="user",
                       entity_id=str(u.id), summary=f"Org Admin login created for {email} by the platform operator",
                       meta={"email": email})
    return {"user_id": str(u.id), "email": email, "role": "org_admin", "email_sent": sent.get("email_sent"),
            **({"link": sent["link"]} if sent.get("link") else {})}


@router.post("/platform/orgs/{org_id}/users/{user_id}/role")
async def platform_set_role(org_id: str, user_id: str, body: PlatformRoleIn, request: Request,
                            db: AsyncSession = Depends(get_db)):
    """Make / unmake an Org Admin (or any system role) in a tenant. The person is signed out."""
    _require_super(request)
    org = await _tenant(db, org_id)
    try:
        role = identity.normalise_role(body.role)
        uid = uuid.UUID(str(user_id))
    except (identity.IdentityError, ValueError):
        raise HTTPException(422, "Pick a valid login and role")
    if role == "superadmin":
        raise HTTPException(422, "The platform role cannot be given to an organisation login")
    u = (await db.execute(select(User).where(User.id == uid, User.org_id == org.id))).scalar_one_or_none()
    if not u:
        raise HTTPException(404, "Login not found")
    if u.role == "org_admin" and role != "org_admin":
        others = (await db.execute(select(func.count()).select_from(User).where(
            User.org_id == org.id, User.role == "org_admin", User.is_active.is_(True), User.id != u.id))).scalar() or 0
        if not others:
            raise HTTPException(409, "This is the tenant's only active Org Admin. Make another one first.")
    old = u.role
    u.role = role
    u.branch_scope = "org" if role in identity.ORG_SCOPE_ROLES else "branch"
    u.tokens_valid_after = datetime.now(timezone.utc)
    await db.commit()
    from shared.utils.audit import record_audit
    await record_audit(_OrgRequest(request, str(org.id)), "platform.user.role", entity_type="user", entity_id=str(u.id),
                       summary=f"Role of {u.email} changed by the platform operator: {old} -> {role}",
                       meta={"from": old, "to": role})
    return {"id": str(u.id), "email": u.email, "role": u.role}


class _OrgRequest:
    """record_audit reads the org from X-Org-Id — write the platform action into the TENANT's chain."""

    def __init__(self, request: Request, org_id: str):
        self.headers = {**{k.lower(): v for k, v in request.headers.items()}, "x-org-id": org_id}

