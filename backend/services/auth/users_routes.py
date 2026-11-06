"""Auth service — logins for employees (stream A).

HR / admin (gateway-authenticated):
    GET  /auth/users?employee_id=&q=&role=&status=      list logins
    POST /auth/users {employee_id, email, role, send_invite}
    GET  /auth/users/unlinked-employees                 employees with no login yet
    POST /auth/users/{user_id}/invite | /resend         (re)send the set-password link
    POST /auth/users/{user_id}/reset-password           send a reset link
    POST /auth/users/{user_id}/disable {reason}         ends open sessions too
    POST /auth/users/{user_id}/enable
    POST /auth/users/{user_id}/role {role}
    GET / PUT /auth/settings/identity

Public (the link token is the credential; rate-limited):
    GET / POST /auth/invite/{token}                     "set your password"
    POST       /auth/password/forgot {email, org_code}
    GET / POST /auth/password/reset/{token}

The rules themselves live in shared/utils/identity.py so that other services
(exit, hiring, onboarding) run exactly the same code.
"""
import os
import sys
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils import identity  # noqa: E402
from shared.utils.audit import record_audit  # noqa: E402
from shared.utils.ratelimit import RateLimiter, client_ip, make_limiter  # noqa: E402  (Redis-shared when REDIS_URL is set)
from shared.utils import rbac_grant  # noqa: E402  (2026-10-24: who may give which role)
from http_errors import PublicError  # noqa: E402

from database import get_db  # noqa: E402
from models import IdentitySettings, Organization, User  # noqa: E402
from security_policy import POLICY_TEXT, password_problems  # noqa: E402
import account_security  # noqa: E402
from password_guard import HISTORY_DEPTH, breach_problems, reused  # noqa: E402

router = APIRouter(prefix="/auth", tags=["users"])

# Who may manage logins. HR may not create, change or disable an Admin.
MANAGE_ROLES = {"superadmin", "org_admin", "hr_admin"}
TOP_ROLES = {"superadmin", "org_admin"}

# Public pages: 30 tries per 15 min per address; "forgot password" 5 per 15 min.
link_limiter = make_limiter(30, 900, name="auth_link")
forgot_limiter = make_limiter(5, 900, name="auth_forgot")


def _role(request: Request) -> str:
    return (request.headers.get("x-role") or "").lower()


def _org(request: Request) -> str:
    org = request.headers.get("x-org-id")
    if not org:
        raise HTTPException(401, "Not authenticated")
    return org


def _manager(request: Request) -> str:
    """Org id, after checking the caller may manage logins."""
    org = _org(request)
    if _role(request) not in MANAGE_ROLES:
        raise HTTPException(403, "Only HR or an admin can manage logins")
    return org


def can_grant(actor_role: str, target_role: str) -> bool:
    """HR / Org Admin hand out every role below Org Admin. Org Admin itself is made ONLY by the Lexora HRM platform
    operator (Entersoft 2026-10-24 Medium — shared/utils/rbac_grant.system_role_refusal)."""
    if actor_role not in MANAGE_ROLES:
        return False
    return rbac_grant.system_role_refusal(actor_role, target_role) is None


def _refuse_grant(actor_role: str, target_role: str) -> None:
    r = rbac_grant.system_role_refusal(actor_role, target_role)
    if r is not None:
        raise PublicError(403, str(r), code=r.code)


def _http(exc: identity.IdentityError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


async def _target(db: AsyncSession, org: str, user_id: str, request: Request) -> User:
    try:
        u = (await db.execute(select(User).where(
            User.id == identity._uuid(user_id), User.org_id == identity._uuid(org)))).scalar_one_or_none()
    except identity.IdentityError as exc:
        raise _http(exc)
    if not u:
        raise HTTPException(404, "Login not found")
    if u.role in TOP_ROLES and _role(request) not in TOP_ROLES:
        raise HTTPException(403, "Only an admin can change another admin's login")
    # 2026-10-24: an Org Admin login is edited / reset / disabled / re-roled only by the platform operator.
    r = rbac_grant.system_manage_refusal(_role(request), u.role)
    if r is not None and str(u.id) != str(request.headers.get("x-user-id") or ""):   # self: the route's own 409s
        raise PublicError(403, str(r), code=r.code)
    return u


def _emp_fields(e: dict | None) -> dict:
    if not e:
        return {"employee_name": None, "employee_number": None, "employee_status": None, "department": None}
    return {"employee_name": f"{e.get('first_name') or ''} {e.get('last_name') or ''}".strip(),
            "employee_number": e.get("employee_number"), "employee_status": e.get("status"),
            "department": e.get("department")}


def _row(u: User) -> dict:
    return {c.name: getattr(u, c.name) for c in identity.users.c if hasattr(u, c.name)}


# ───────────────────────────── HR / admin ─────────────────────────────────────

@router.get("/users")
async def list_users(request: Request, employee_id: str | None = None, q: str | None = None,
                     role: str | None = None, status: str | None = None,
                     db: AsyncSession = Depends(get_db)):
    """HR / admin: every login of the org. Anyone else: only their own login."""
    org = _org(request)
    stmt = select(User).where(User.org_id == identity._uuid(org), User.role != "superadmin")
    if _role(request) not in MANAGE_ROLES:
        uid = request.headers.get("x-user-id")
        if not uid:
            raise HTTPException(403, "Only HR or an admin can list logins")
        stmt = stmt.where(User.id == identity._uuid(uid))
    if employee_id:
        try:
            stmt = stmt.where(User.employee_id == identity._uuid(employee_id))
        except identity.IdentityError as exc:
            raise _http(exc)
    if role:
        try:
            stmt = stmt.where(User.role == identity.normalise_role(role))
        except identity.IdentityError as exc:
            raise _http(exc)
    users = (await db.execute(stmt.order_by(User.created_at.desc()))).scalars().all()
    emps = await identity.employees_brief(org)
    out = []
    for u in users:
        row = identity.public_user(_row(u))
        row.update(_emp_fields(emps.get(row["employee_id"]) if row["employee_id"] else None))
        out.append(row)
    if status:
        out = [r for r in out if r["status"] == status]
    if q:
        s = q.strip().lower()
        out = [r for r in out if s in " ".join(str(r.get(k) or "") for k in (
            "name", "email", "employee_name", "employee_number", "role_label", "department")).lower()]
    return {"total": len(out), "items": out, "roles": [
        {"key": k, "label": identity.ROLE_LABELS[k]} for k in identity.ASSIGNABLE_ROLES
        if can_grant(_role(request), k)]}


@router.get("/users/unlinked-employees")
async def unlinked_employees(request: Request, db: AsyncSession = Depends(get_db)):
    """Employees who have no login yet — the picker on 'Create login'."""
    org = _manager(request)
    taken = {str(i) for (i,) in (await db.execute(select(User.employee_id).where(
        User.org_id == identity._uuid(org), User.employee_id.is_not(None)))).all()}
    out = []
    for eid, e in (await identity.employees_brief(org)).items():
        if eid in taken or (e.get("status") or "") == "terminated":
            continue
        out.append({"id": eid, **_emp_fields(e), "email": e.get("work_email") or e.get("email"),
                    "job_title": e.get("job_title")})
    out.sort(key=lambda r: (r["employee_name"] or "").lower())
    return out


class UserCreate(BaseModel):
    employee_id: str | None = None
    email: str | None = None
    role: str = "employee"
    send_invite: bool = True
    first_name: str | None = None
    last_name: str | None = None


@router.post("/users", status_code=201)
async def create_user(body: UserCreate, request: Request, db: AsyncSession = Depends(get_db)):
    """Create a login for an employee (idempotent: an employee who already has a
    login gets that login back with `created: false`)."""
    org = _manager(request)
    try:
        role = identity.normalise_role(body.role)
    except identity.IdentityError as exc:
        raise _http(exc)
    first, last, email = body.first_name, body.last_name, body.email
    if not can_grant(_role(request), role) and not await _links_existing_admin(db, org, role, body, request):
        _refuse_grant(_role(request), role)
        raise HTTPException(403, "Only an admin can create an admin login")
    actor = identity.actor_from(request)
    try:
        if body.employee_id:
            emp = (await identity.employees_brief(org)).get(str(identity._uuid(body.employee_id)))
            if emp:
                first, last = first or emp.get("first_name"), last or emp.get("last_name")
                email = email or emp.get("work_email") or emp.get("email")
            if not email:
                raise HTTPException(422, "This employee has no e-mail on file. Type the e-mail to use for the login.")
            res = await identity.ensure_login_for_employee(
                org, body.employee_id, email, role, send_invite=body.send_invite,
                first_name=first, last_name=last, actor=actor)
        else:
            res = await _create_unlinked(db, org, email, role, first, last, body.send_invite, actor)
    except identity.IdentityError as exc:
        raise _http(exc)
    u = (await db.execute(select(User).where(User.id == identity._uuid(res["user_id"])))).scalar_one()
    return {**res, "user": identity.public_user(_row(u))}


async def _links_existing_admin(db, org, role, body, request) -> bool:
    """The one Org Admin case an org may still do itself: link its OWN existing Org Admin login (same e-mail) to its
    employee record (the demo seed does this). No new admin is made and no role changes."""
    if role != "org_admin" or _role(request) != "org_admin" or not body.employee_id:
        return False
    try:
        me_id, org_id, emp_id = (identity._uuid(request.headers.get("x-user-id") or ""), identity._uuid(org),
                                 identity._uuid(body.employee_id))
    except identity.IdentityError:
        return False
    me = (await db.execute(select(User).where(User.id == me_id, User.org_id == org_id))).scalar_one_or_none()
    if not me or me.role != "org_admin":
        return False
    emp = (await identity.employees_brief(org)).get(str(emp_id)) or {}
    mail = (body.email or emp.get("work_email") or emp.get("email") or "").strip().lower()
    return bool(mail) and mail == (me.email or "").lower() and me.employee_id in (None, emp_id)


async def _create_unlinked(db, org, email, role, first, last, send_invite, actor) -> dict:
    """A login that is not an employee (e.g. an outside payroll accountant)."""
    import secrets
    import uuid as _uuid
    from shared.utils.security import hash_password
    email = identity.clean_email(email)
    dup = (await db.execute(select(User).where(
        User.org_id == identity._uuid(org), func.lower(User.email) == email))).scalars().first()
    if dup:
        raise identity.IdentityError("That e-mail already has a login in this organization", 409)
    u = User(id=_uuid.uuid4(), org_id=identity._uuid(org), email=email, role=role, first_name=first,
             last_name=last, hashed_password=hash_password(secrets.token_urlsafe(48)), password_set=False,
             branch_scope="org" if role in identity.ORG_SCOPE_ROLES else "branch", is_active=True)
    db.add(u)
    await db.commit()
    await identity._audit(org, actor, "auth.user.create", u.id,
                          f"Login created for {email} ({identity.ROLE_LABELS.get(role, role)})", {"role": role})
    out = {"user_id": str(u.id), "created": True, "linked": False, "status": "invited", "email": email,
           "role": role, "invited": False, "email_sent": False}
    if send_invite:
        sent = await identity.send_link(org, u.id, identity.INVITE, actor=actor)
        out.update(invited=True, email_sent=sent["email_sent"])
        if sent.get("link"):
            out["link"] = sent["link"]
    return out


async def _send(request: Request, user_id: str, purpose: str, db: AsyncSession) -> dict:
    org = _manager(request)
    u = await _target(db, org, user_id, request)
    # Someone who never set a password needs the invite link, not a reset.
    if purpose == identity.RESET and u.password_set is False:
        purpose = identity.INVITE
    try:
        return await identity.send_link(org, u.id, purpose, actor=identity.actor_from(request))
    except identity.IdentityError as exc:
        raise _http(exc)


@router.post("/users/{user_id}/invite")
async def invite_user(user_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    return await _send(request, user_id, identity.INVITE, db)


@router.post("/users/{user_id}/resend")
async def resend_invite(user_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    return await _send(request, user_id, identity.INVITE, db)


@router.post("/users/{user_id}/reset-password")
async def admin_reset(user_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    return await _send(request, user_id, identity.RESET, db)


class DisableIn(BaseModel):
    reason: str | None = None


@router.post("/users/{user_id}/disable")
async def disable_user(user_id: str, request: Request, body: DisableIn = DisableIn(),
                       db: AsyncSession = Depends(get_db)):
    """Switch a login off. Sessions already open stop working at the gateway."""
    org = _manager(request)
    u = await _target(db, org, user_id, request)
    if str(u.id) == str(request.headers.get("x-user-id") or ""):
        raise HTTPException(409, "You cannot disable your own login")
    if u.role == "org_admin" and u.is_active:
        others = (await db.execute(select(func.count()).select_from(User).where(
            User.org_id == u.org_id, User.role == "org_admin", User.is_active.is_(True),
            User.id != u.id))).scalar() or 0
        if not others:
            raise HTTPException(409, "This is the only active admin. Make another admin first.")
    try:
        return await identity.set_user_active(org, u.id, False, body.reason, actor=identity.actor_from(request))
    except identity.IdentityError as exc:
        raise _http(exc)


@router.post("/users/{user_id}/enable")
async def enable_user(user_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = _manager(request)
    u = await _target(db, org, user_id, request)
    try:
        return await identity.set_user_active(org, u.id, True, actor=identity.actor_from(request))
    except identity.IdentityError as exc:
        raise _http(exc)


class RoleIn(BaseModel):
    role: str


@router.post("/users/{user_id}/role")
async def change_role(user_id: str, body: RoleIn, request: Request, db: AsyncSession = Depends(get_db)):
    """Change a login's role. The person is signed out so the new role applies cleanly."""
    org = _manager(request)
    u = await _target(db, org, user_id, request)
    try:
        role = identity.normalise_role(body.role)
    except identity.IdentityError as exc:
        raise _http(exc)
    _refuse_grant(_role(request), role)
    if str(u.id) == str(request.headers.get("x-user-id") or ""):
        raise HTTPException(409, "You cannot change your own role")
    if u.role == "org_admin" and role != "org_admin":
        others = (await db.execute(select(func.count()).select_from(User).where(
            User.org_id == u.org_id, User.role == "org_admin", User.is_active.is_(True),
            User.id != u.id))).scalar() or 0
        if not others:
            raise HTTPException(409, "This is the only active admin. Make another admin first.")
    old = u.role
    u.role = role
    u.branch_scope = "org" if role in identity.ORG_SCOPE_ROLES else "branch"
    u.tokens_valid_after = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(u)
    await record_audit(request, "auth.user.role", entity_type="user", entity_id=str(u.id),
                       summary=f"Role of {u.email} changed: {identity.ROLE_LABELS.get(old, old)} → "
                               f"{identity.ROLE_LABELS.get(role, role)}", meta={"from": old, "to": role})
    return identity.public_user(_row(u))


# ───────────────────────────── settings ───────────────────────────────────────

class SettingsIn(BaseModel):
    auto_login_on_onboarding: bool | None = None
    invite_expiry_hours: int | None = None
    reset_expiry_minutes: int | None = None


@router.get("/settings/identity")
async def get_identity_settings(request: Request):
    return {**(await identity.get_settings(_org(request))), "password_policy": POLICY_TEXT}


@router.put("/settings/identity")
async def put_identity_settings(body: SettingsIn, request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    if _role(request) not in TOP_ROLES:
        raise HTTPException(403, "Only an admin can change login settings")
    patch = body.model_dump(exclude_none=True)
    if "invite_expiry_hours" in patch and not (1 <= patch["invite_expiry_hours"] <= 720):
        raise HTTPException(422, "Invite link life must be between 1 and 720 hours")
    if "reset_expiry_minutes" in patch and not (10 <= patch["reset_expiry_minutes"] <= 1440):
        raise HTTPException(422, "Reset link life must be between 10 and 1440 minutes")
    row = (await db.execute(select(IdentitySettings).where(
        IdentitySettings.org_id == identity._uuid(org)))).scalar_one_or_none()
    if not row:
        row = IdentitySettings(org_id=identity._uuid(org), **identity.SETTING_DEFAULTS)
        db.add(row)
    for k, v in patch.items():
        setattr(row, k, v)
    row.updated_at = datetime.now(timezone.utc)
    row.updated_by = request.headers.get("x-user-id")
    await db.commit()
    await record_audit(request, "auth.settings.identity", entity_type="organization", entity_id=org,
                       summary="Login settings changed", meta=patch)
    return {**(await identity.get_settings(org)), "password_policy": POLICY_TEXT}


# ───────────────────────────── public: links ──────────────────────────────────

def _throttle(limiter: RateLimiter, key: str):
    wait = limiter.hit(key)
    if wait:
        raise HTTPException(429, f"Too many tries. Wait {max(wait // 60, 1)} minute(s) and try again.",
                            headers={"Retry-After": str(wait)})


class PasswordIn(BaseModel):
    password: str


async def _peek(token: str, purpose: str, request: Request) -> dict:
    _throttle(link_limiter, f"peek|{client_ip(request)}")
    try:
        return {**(await identity.peek_token(token, purpose)), "password_policy": POLICY_TEXT}
    except identity.IdentityError as exc:
        raise _http(exc)


async def _use(token: str, purpose: str, body: PasswordIn, request: Request) -> dict:
    _throttle(link_limiter, f"use|{client_ip(request)}")
    try:
        who = await identity.peek_token(token, purpose)
        problems = password_problems(body.password, email=who["email"]) + breach_problems(body.password)
        if problems:
            raise HTTPException(422, "Password needs: " + ", ".join(problems))
        if purpose == identity.RESET:            # S4: no reuse of the last N passwords
            from database import SessionLocal
            async with SessionLocal() as s:
                tok = await identity.find_token(s, token, purpose)
                u = (await s.execute(select(User).where(User.id == tok["user_id"]))).scalar_one_or_none() if tok else None
                if u is not None:
                    if reused(body.password, await account_security.history_hashes(s, u)):
                        raise HTTPException(422, f"Password needs: not one of your last {HISTORY_DEPTH} passwords")
                    await account_security.remember_password(s, u.id, u.hashed_password)
                    await s.commit()
        return await identity.consume_token(token, purpose, body.password)
    except identity.IdentityError as exc:
        raise _http(exc)


@router.get("/invite/{token}")
async def invite_info(token: str, request: Request):
    """Who this 'set your password' link is for (name, e-mail, company)."""
    return await _peek(token, identity.INVITE, request)


@router.post("/invite/{token}")
async def invite_accept(token: str, body: PasswordIn, request: Request):
    return await _use(token, identity.INVITE, body, request)


@router.get("/password/reset/{token}")
async def reset_info(token: str, request: Request):
    return await _peek(token, identity.RESET, request)


@router.post("/password/reset/{token}")
async def reset_accept(token: str, body: PasswordIn, request: Request):
    return await _use(token, identity.RESET, body, request)


class ForgotIn(BaseModel):
    email: str
    org_code: str | None = None
    captcha_token: str | None = None      # WASA 2026-10-21: proof-of-work CAPTCHA (purpose "forgot")


FORGOT_REPLY = "If that e-mail has a login, we have sent a link to reset the password."
_forgot_tasks: set = set()                # keeps the out-of-line sends referenced until they finish


def _dev_links() -> bool:
    return os.getenv("LEXORA_DEV_SHOW_LINKS", "").lower() in ("1", "true", "yes")


async def _forgot_lookup_and_send(db: AsyncSession, email: str, code: str) -> dict:
    """Find the login and send its reset (or first-time invite) link. {} when
    there is nothing to send — the caller never tells the difference."""
    org = (await db.execute(select(Organization).where(
        func.upper(Organization.code) == code))).scalar_one_or_none()
    if not org or not org.is_active:
        return {}
    u = (await db.execute(select(User).where(
        User.org_id == org.id, func.lower(User.email) == email, User.is_active.is_(True)))).scalars().first()
    if not u:
        return {}
    purpose = identity.INVITE if u.password_set is False else identity.RESET
    try:
        return await identity.send_link(org.id, u.id, purpose,
                                        actor={"user_id": u.id, "email": u.email, "role": u.role})
    except identity.IdentityError:
        return {}


async def _forgot_in_background(email: str, code: str) -> None:
    try:
        from database import SessionLocal
        async with SessionLocal() as s:
            await _forgot_lookup_and_send(s, email, code)
    except Exception:  # noqa: BLE001 — never surfaces to the caller
        import logging
        logging.getLogger("auth.forgot").exception("forgot-password send failed")


@router.post("/password/forgot")
async def forgot_password(body: ForgotIn, request: Request, db: AsyncSession = Depends(get_db)):
    """Self-service reset. Always answers the same way, so it cannot be used to
    find out which e-mails have a login.

    WASA 2026-10-21 (#13): proof-of-work CAPTCHA first, then three limits — 5 per
    e-mail+IP, 3 per e-mail from any IP, 10 per IP for any e-mail (15 minutes,
    429 + Retry-After). The account lookup and the e-mail run OUT OF LINE, so the
    response time does not say whether the account exists."""
    import asyncio
    import anti_automation as aa
    email = (body.email or "").strip().lower()
    ip = client_ip(request)
    aa.require_pow(body.captcha_token, request, purposes=("forgot",), account=email or None)
    _throttle(forgot_limiter, f"{email}|{ip}")
    if email:
        _throttle(aa.forgot_email_limiter, email)
    if ip:
        _throttle(aa.forgot_ip_limiter, ip)
    aa.note_forgot_request(email, request)            # raises the proof-of-work for the next one
    out = {"ok": True, "message": FORGOT_REPLY}
    code = (body.org_code or "").strip().upper()
    if not email or not code:
        return out
    if _dev_links():
        # Local set-ups have no mail server. With LEXORA_DEV_SHOW_LINKS=1 (never in
        # production) the send runs inline and the link is returned so a tester can
        # finish the flow.
        sent = await _forgot_lookup_and_send(db, email, code)
        if sent.get("link"):
            out["dev_link"] = sent["link"]
        return out
    t = asyncio.create_task(_forgot_in_background(email, code))
    _forgot_tasks.add(t)
    t.add_done_callback(_forgot_tasks.discard)
    return out
