"""Lexora HRM — logins for employees (the contract other services import).

Two functions are the published contract:

    await ensure_login_for_employee(org_id, employee_id, email, role="employee")
    await disable_logins_for_employee(org_id, employee_id, reason)

Both talk straight to the shared Postgres (`users`, `user_tokens`) in their own
transaction, so any service can call them without an HTTP hop or a token. The
auth service's `/auth/users*` routes are thin wrappers over the same functions,
so there is one implementation of "create a login", "invite", "disable".

Rules enforced here (not in the callers):
  * one login per employee per org; e-mail unique per org (case-insensitive)
  * a new login has NO usable password — the person sets one through a
    single-use, expiring link (invite); only the SHA-256 of the link token is stored
  * disabling stamps `tokens_valid_after`, which the gateway checks on every
    request, so sessions already open stop working
  * every action lands in the tamper-evident audit trail

Roles: the product speaks of `employee, manager, hr, payroll, org_admin`. The
stored keys the existing services check are `hr_admin` / `payroll_admin`, so
`hr` and `payroll` are accepted everywhere as aliases and stored as those keys.
"""
import hashlib
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa

from shared.utils import audit
from shared.utils.db import make_engine, make_sessionmaker
from shared.utils.email import brand_html, send_email
from shared.utils.security import hash_password

def show_links() -> bool:
    """True only on a local test machine (LEXORA_DEV_SHOW_LINKS=1). Invite / reset / joining /
    signing links are then returned to the caller when the e-mail could not be sent. Production
    forces the switch off (shared/ops_runtime.enforce_production_config)."""
    return (os.getenv("LEXORA_DEV_SHOW_LINKS") or "").strip().lower() in ("1", "true", "yes")


# ── Roles ─────────────────────────────────────────────────────────────────────
ROLE_ALIASES = {"hr": "hr_admin", "payroll": "payroll_admin", "admin": "org_admin", "finance": "accountant"}
# accountant: finance reader — the payroll GL journal / register and the people directory; never runs payroll.
ASSIGNABLE_ROLES = ("employee", "manager", "hr_admin", "hr_manager", "payroll_admin", "accountant", "org_admin")
ROLE_LABELS = {
    "employee": "Employee", "manager": "Manager", "hr_admin": "HR", "hr_manager": "HR (approver)",
    "payroll_admin": "Payroll", "accountant": "Accountant", "org_admin": "Admin", "superadmin": "Platform admin",
}
# Roles that see every employee of the org (the rest see self / their reports).
ORG_WIDE_ROLES = {"superadmin", "org_admin", "hr_admin", "hr_manager", "payroll_admin", "accountant"}
ORG_SCOPE_ROLES = {"org_admin", "hr_admin", "hr_manager", "payroll_admin", "accountant"}

INVITE = "invite"
RESET = "reset"
DEFAULT_INVITE_HOURS = 72
DEFAULT_RESET_MINUTES = 60

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class IdentityError(ValueError):
    """A rule was broken. `.status` is the HTTP status a route should return."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def normalise_role(role: str | None) -> str:
    """`hr` → `hr_admin`, `payroll` → `payroll_admin`. Raises on an unknown role."""
    r = (role or "employee").strip().lower()
    r = ROLE_ALIASES.get(r, r)
    if r not in ASSIGNABLE_ROLES:
        raise IdentityError(f"Unknown role '{role}'. Use one of: employee, manager, hr, payroll, org_admin", 422)
    return r


def clean_email(email: str | None) -> str:
    e = (email or "").strip().lower()
    if not _EMAIL_RE.match(e) or len(e) > 200:
        raise IdentityError("Enter a valid e-mail address", 422)
    return e


# ── Tables (Core, own MetaData — the auth service owns the ORM models) ────────
_md = sa.MetaData()
_TS = sa.DateTime(timezone=True)

users = sa.Table(
    "users", _md,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("org_id", sa.Uuid),
    sa.Column("email", sa.String(200)),
    sa.Column("hashed_password", sa.String),
    sa.Column("first_name", sa.String(100)),
    sa.Column("last_name", sa.String(100)),
    sa.Column("role", sa.String(40)),
    sa.Column("employee_id", sa.Uuid),
    sa.Column("branch_scope", sa.String(10)),
    sa.Column("branch_id", sa.Uuid),
    sa.Column("is_active", sa.Boolean),
    sa.Column("created_at", _TS),
    sa.Column("last_login", _TS),
    sa.Column("password_changed_at", _TS),
    sa.Column("password_set", sa.Boolean),
    sa.Column("invited_at", _TS),
    sa.Column("disabled_at", _TS),
    sa.Column("disabled_reason", sa.String(300)),
    sa.Column("disabled_by", sa.String(64)),
    sa.Column("tokens_valid_after", _TS),
)

user_tokens = sa.Table(
    "user_tokens", _md,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("org_id", sa.Uuid),
    sa.Column("user_id", sa.Uuid),
    sa.Column("purpose", sa.String(12)),
    sa.Column("token_hash", sa.String(64)),
    sa.Column("expires_at", _TS),
    sa.Column("used_at", _TS),
    sa.Column("created_at", _TS),
    sa.Column("created_by", sa.String(64)),
    sa.Column("sent_to", sa.String(200)),
)

organizations = sa.Table(
    "organizations", _md,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("name", sa.String(200)),
    sa.Column("code", sa.String(30)),
    sa.Column("country", sa.String(2)),
    sa.Column("province", sa.String(2)),
    sa.Column("is_active", sa.Boolean),
)

identity_settings = sa.Table(
    "identity_settings", _md,
    sa.Column("org_id", sa.Uuid, primary_key=True),
    sa.Column("auto_login_on_onboarding", sa.Boolean),
    sa.Column("invite_expiry_hours", sa.Integer),
    sa.Column("reset_expiry_minutes", sa.Integer),
    sa.Column("updated_at", _TS),
    sa.Column("updated_by", sa.String(64)),
)

# Read-only view of the employee service's table (same database) — for names
# on the Users screen and "who are my reports".
employees_ref = sa.Table(
    "employees", _md,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("org_id", sa.Uuid),
    sa.Column("employee_number", sa.String(30)),
    sa.Column("first_name", sa.String(100)),
    sa.Column("last_name", sa.String(100)),
    sa.Column("email", sa.String(200)),
    sa.Column("work_email", sa.String(200)),
    sa.Column("status", sa.String(20)),
    sa.Column("department", sa.String(120)),
    sa.Column("job_title", sa.String(120)),
    sa.Column("manager_id", sa.Uuid),
    sa.Column("is_active", sa.Boolean),
)

SETTING_DEFAULTS = {
    "auto_login_on_onboarding": True,          # approve a joiner → login created + invite sent
    "invite_expiry_hours": DEFAULT_INVITE_HOURS,
    "reset_expiry_minutes": DEFAULT_RESET_MINUTES,
}

_sessionmaker = None


def _sm():
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = make_sessionmaker(make_engine())
    return _sessionmaker


def now() -> datetime:
    return datetime.now(timezone.utc)


def aware(dt: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; treat them as UTC."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _uuid(v) -> uuid.UUID:
    if isinstance(v, uuid.UUID):
        return v
    try:
        return uuid.UUID(str(v))
    except (ValueError, AttributeError, TypeError):
        raise IdentityError("That record id is not valid", 422)


# ── Link tokens (pure) ────────────────────────────────────────────────────────
def hash_token(raw: str) -> str:
    return hashlib.sha256((raw or "").encode()).hexdigest()


def new_token() -> tuple[str, str]:
    """(raw token for the link, hash to store). The raw value is never stored."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def token_problem(row, at: datetime | None = None) -> str | None:
    """Why a link token cannot be used: 'invalid' | 'used' | 'expired'. None = OK."""
    if row is None:
        return "invalid"
    get = row.get if isinstance(row, dict) else (lambda k: getattr(row, k, None))
    if get("used_at") is not None:
        return "used"
    exp = aware(get("expires_at"))
    if exp is None or exp <= (at or now()):
        return "expired"
    return None


TOKEN_MESSAGES = {
    "invalid": "This link is not valid. Ask your HR team for a new one.",
    "used": "This link was already used. Sign in, or ask for a new link.",
    "expired": "This link has expired. Ask your HR team for a new one.",
}


def link_for(purpose: str, raw: str) -> str:
    base = os.getenv("APP_BASE_URL", "http://localhost:5173").rstrip("/")
    return f"{base}/{'set-password' if purpose == INVITE else 'reset-password'}/{raw}"


def user_status(row) -> str:
    """active | invited (no password yet) | disabled."""
    get = row.get if isinstance(row, dict) else (lambda k: getattr(row, k, None))
    if not get("is_active"):
        return "disabled"
    if get("password_set") is False:
        return "invited"
    return "active"


# ── Audit shim ────────────────────────────────────────────────────────────────
class _Actor:
    """Looks enough like a Request for `record_audit` (it only reads headers)."""

    def __init__(self, org_id, actor: dict | None):
        a = actor or {}
        self.headers = {k: v for k, v in {
            "x-org-id": str(org_id) if org_id else "",
            "x-user-id": str(a.get("user_id") or "") or None,
            "x-user-email": a.get("email") or "system",
            "x-role": a.get("role") or "system",
        }.items() if v}


def actor_from(request) -> dict:
    """Build the `actor` dict from a gateway-stamped request."""
    h = request.headers
    return {"user_id": h.get("x-user-id"), "email": h.get("x-user-email"), "role": h.get("x-role")}


async def _audit(org_id, actor, action, user_id, summary, meta=None):
    await audit.record_audit(_Actor(org_id, actor), action, entity_type="user",
                             entity_id=str(user_id) if user_id else None, summary=summary, meta=meta)


# ── Reads ─────────────────────────────────────────────────────────────────────
async def get_settings(org_id) -> dict:
    out = dict(SETTING_DEFAULTS)
    try:
        async with _sm()() as s:
            row = (await s.execute(sa.select(identity_settings).where(
                identity_settings.c.org_id == _uuid(org_id)))).mappings().first()
    except Exception:
        return out                      # table not created yet → defaults
    if row:
        for k in SETTING_DEFAULTS:
            if row.get(k) is not None:
                out[k] = row[k]
    return out


async def org_info(org_id) -> dict | None:
    """Display name + country of an org (None if unknown)."""
    try:
        async with _sm()() as s:
            row = (await s.execute(sa.select(organizations).where(
                organizations.c.id == _uuid(org_id)))).mappings().first()
    except Exception:
        return None
    return dict(row) if row else None


async def org_country(org_id) -> str | None:
    o = await org_info(org_id)
    c = ((o or {}).get("country") or "").upper()
    return c or None


async def fetch_user_state(user_id) -> dict | None:
    """What the gateway needs on every request: is the login still good?"""
    async with _sm()() as s:
        row = (await s.execute(sa.select(
            users.c.id, users.c.is_active, users.c.tokens_valid_after, users.c.role,
            users.c.employee_id, users.c.org_id).where(users.c.id == _uuid(user_id)))).mappings().first()
    if not row:
        return None
    state = {"is_active": bool(row["is_active"]), "tokens_valid_after": aware(row["tokens_valid_after"]),
             "role": row["role"], "employee_id": str(row["employee_id"]) if row["employee_id"] else None,
             "org_id": str(row["org_id"]) if row["org_id"] else None}
    # 2026-10-24 org Roles & Permissions: the login's ACTIVE custom roles (+ the values a department / location /
    # entity reach narrows by), read live so a role change applies within the gateway's cache window.
    state["custom_roles"] = await _custom_roles_state(row["id"]) if row["role"] != "superadmin" else []
    if any(c.get("reach") in ("department", "location", "entity") for c in state["custom_roles"]):
        state["scope_values"] = await _scope_values(row["id"], row["employee_id"])
    return state


def _missing_table(exc: Exception) -> bool:
    msg = str(getattr(exc, "orig", exc)).lower()
    return "does not exist" in msg or "no such table" in msg or "undefinedtable" in type(
        getattr(exc, "orig", exc)).__name__.lower()


async def _custom_roles_state(user_id) -> list[dict]:
    """Active custom roles of a login. A database WITHOUT the rbac tables yet (auth not upgraded) = none; any
    other failure propagates (the gateway fails CLOSED)."""
    from shared.utils.rbac_models import custom_roles_for
    try:
        async with _sm()() as s:
            return await custom_roles_for(s, user_id)
    except (sa.exc.ProgrammingError, sa.exc.OperationalError) as exc:
        if _missing_table(exc):
            return []
        raise


async def _scope_values(user_id, employee_id) -> dict:
    """department / branch (location) / legal entity of the login's employee record."""
    out = {"department": None, "location": None, "entity": None}
    async with _sm()() as s:
        b = (await s.execute(sa.select(users.c.branch_id).where(users.c.id == user_id))).scalar()
        out["location"] = str(b) if b else None
        if employee_id:
            r = (await s.execute(sa.text(
                "SELECT department, branch_id, entity_id FROM employees WHERE id = CAST(:e AS uuid)"),
                {"e": str(employee_id)})).mappings().first()
            if r:
                out["department"] = r["department"] or None
                out["location"] = str(r["branch_id"]) if r["branch_id"] else out["location"]
                out["entity"] = str(r["entity_id"]) if r["entity_id"] else None
    return out


async def employee_id_for_user(user_id) -> str | None:
    try:
        st = await fetch_user_state(user_id)
    except Exception:
        return None
    return (st or {}).get("employee_id")


async def employees_brief(org_id) -> dict[str, dict]:
    """employee id → {employee_number, first_name, last_name, email, status, …}.
    Empty when the employee table is not reachable — callers treat it as optional."""
    try:
        async with _sm()() as s:
            rows = (await s.execute(sa.select(employees_ref).where(
                employees_ref.c.org_id == _uuid(org_id),
                employees_ref.c.is_active.is_(True)))).mappings().all()
    except Exception:
        return {}
    return {str(r["id"]): dict(r) for r in rows}


async def logins_for_employee(org_id, employee_id) -> list[dict]:
    async with _sm()() as s:
        rows = (await s.execute(sa.select(users).where(
            users.c.org_id == _uuid(org_id), users.c.employee_id == _uuid(employee_id)))).mappings().all()
    return [public_user(r) for r in rows]


def public_user(r) -> dict:
    """The safe, screen-ready shape of a login (never the password hash)."""
    def iso(v):
        v = aware(v)
        return v.isoformat() if v else None
    return {
        "id": str(r["id"]), "email": r["email"], "first_name": r["first_name"], "last_name": r["last_name"],
        "name": " ".join(x for x in (r["first_name"], r["last_name"]) if x) or r["email"],
        "role": r["role"], "role_label": ROLE_LABELS.get(r["role"], r["role"]),
        "employee_id": str(r["employee_id"]) if r["employee_id"] else None,
        "status": user_status(r), "is_active": bool(r["is_active"]),
        "password_set": r["password_set"] is not False,
        "last_login": iso(r["last_login"]), "created_at": iso(r["created_at"]),
        "invited_at": iso(r["invited_at"]), "disabled_at": iso(r["disabled_at"]),
        "disabled_reason": r["disabled_reason"],
    }


# ── Tokens (DB) ───────────────────────────────────────────────────────────────
async def issue_token(s, *, org_id, user_id, purpose: str, ttl: timedelta,
                      created_by: str | None = None, sent_to: str | None = None) -> str:
    """Create a fresh single-use link token. Older unused links of the same kind
    for this login stop working (so 'resend' never leaves two live links)."""
    t = now()
    await s.execute(sa.update(user_tokens).where(
        user_tokens.c.user_id == user_id, user_tokens.c.purpose == purpose,
        user_tokens.c.used_at.is_(None)).values(used_at=t))
    raw, h = new_token()
    await s.execute(sa.insert(user_tokens).values(
        id=uuid.uuid4(), org_id=org_id, user_id=user_id, purpose=purpose, token_hash=h,
        expires_at=t + ttl, created_at=t, created_by=str(created_by) if created_by else None,
        sent_to=sent_to))
    return raw


async def find_token(s, raw: str, purpose: str | None = None):
    q = sa.select(user_tokens).where(user_tokens.c.token_hash == hash_token(raw))
    if purpose:
        q = q.where(user_tokens.c.purpose == purpose)
    return (await s.execute(q)).mappings().first()


async def peek_token(raw: str, purpose: str) -> dict:
    """For the 'set your password' page: who is this link for? Raises if unusable."""
    async with _sm()() as s:
        tok = await find_token(s, raw, purpose)
        problem = token_problem(tok)
        if problem:
            raise IdentityError(TOKEN_MESSAGES[problem], 410 if problem != "invalid" else 404)
        u = (await s.execute(sa.select(users).where(users.c.id == tok["user_id"]))).mappings().first()
        if not u or not u["is_active"]:
            raise IdentityError("This login is not active. Contact your HR team.", 403)
        org = (await s.execute(sa.select(organizations).where(
            organizations.c.id == u["org_id"]))).mappings().first()
    return {"email": u["email"], "first_name": u["first_name"], "org_name": org["name"] if org else None,
            "org_code": org["code"] if org else None, "purpose": purpose,
            "expires_at": aware(tok["expires_at"]).isoformat()}


async def consume_token(raw: str, purpose: str, new_password: str) -> dict:
    """Use a link once: set the password, mark the link used, end older sessions."""
    async with _sm()() as s:
        tok = await find_token(s, raw, purpose)
        problem = token_problem(tok)
        if problem:
            raise IdentityError(TOKEN_MESSAGES[problem], 410 if problem != "invalid" else 404)
        u = (await s.execute(sa.select(users).where(users.c.id == tok["user_id"]))).mappings().first()
        if not u or not u["is_active"]:
            raise IdentityError("This login is not active. Contact your HR team.", 403)
        t = now()
        # Guarded update: only the first caller flips used_at (single use under a race).
        res = await s.execute(sa.update(user_tokens).where(
            user_tokens.c.id == tok["id"], user_tokens.c.used_at.is_(None)).values(used_at=t))
        if not res.rowcount:
            raise IdentityError(TOKEN_MESSAGES["used"], 410)
        await s.execute(sa.update(users).where(users.c.id == u["id"]).values(
            hashed_password=hash_password(new_password), password_set=True,
            password_changed_at=t, tokens_valid_after=t))
        await s.commit()
        org = (await s.execute(sa.select(organizations).where(
            organizations.c.id == u["org_id"]))).mappings().first()
    await _audit(u["org_id"], {"user_id": u["id"], "email": u["email"], "role": u["role"]},
                 "auth.password.set" if purpose == INVITE else "auth.password.reset", u["id"],
                 "Password set from invite link" if purpose == INVITE else "Password reset from link")
    return {"ok": True, "email": u["email"], "org_code": org["code"] if org else None}


async def _send_link(purpose: str, raw: str, to: str, first_name: str | None,
                     org_name: str | None, hours: float) -> bool:
    link = link_for(purpose, raw)
    hi = f"Hi {(first_name or 'there').strip()},"
    company = org_name or "your company"
    life = f"{int(hours)} hours" if hours >= 1 else f"{int(hours * 60)} minutes"
    if purpose == INVITE:
        subject = f"Set your password — {company}"
        lines = [hi, f"{company} has opened a login for you.",
                 f"Choose your password with the button below. The link works once and expires in {life}."]
        cta = "Set my password"
    else:
        subject = f"Reset your password — {company}"
        lines = [hi, "A password reset was requested for your login.",
                 f"The link works once and expires in {life}. If you did not ask for this, ignore this e-mail."]
        cta = "Reset my password"
    text = "\n\n".join(lines + [link])
    return await send_email(to, subject, text, html=brand_html(subject, lines, cta, link))


async def send_link(org_id, user_id, purpose: str, *, actor: dict | None = None) -> dict:
    """Issue + e-mail a fresh invite / reset link for an existing login."""
    cfg = await get_settings(org_id)
    hours = cfg["invite_expiry_hours"] if purpose == INVITE else cfg["reset_expiry_minutes"] / 60
    async with _sm()() as s:
        u = (await s.execute(sa.select(users).where(
            users.c.id == _uuid(user_id), users.c.org_id == _uuid(org_id)))).mappings().first()
        if not u:
            raise IdentityError("Login not found", 404)
        if not u["is_active"]:
            raise IdentityError("This login is disabled. Enable it first.", 409)
        if purpose == INVITE and u["password_set"] is not False:
            raise IdentityError("This person has already set a password. Use Reset password instead.", 409)
        raw = await issue_token(s, org_id=u["org_id"], user_id=u["id"], purpose=purpose,
                                ttl=timedelta(hours=hours), created_by=(actor or {}).get("user_id"),
                                sent_to=u["email"])
        if purpose == INVITE:
            await s.execute(sa.update(users).where(users.c.id == u["id"]).values(invited_at=now()))
        await s.commit()
        org = (await s.execute(sa.select(organizations).where(
            organizations.c.id == u["org_id"]))).mappings().first()
    sent = await _send_link(purpose, raw, u["email"], u["first_name"], org["name"] if org else None, hours)
    await _audit(org_id, actor, "auth.user.invite" if purpose == INVITE else "auth.user.reset_link", u["id"],
                 f"{'Invite' if purpose == INVITE else 'Password reset'} link issued for {u['email']}",
                 {"email_sent": sent})
    out = {"ok": True, "user_id": str(u["id"]), "email": u["email"], "email_sent": sent,
           "expires_in_hours": round(hours, 2)}
    if not sent and show_links():
        # Decision 2026-10-21: the link is NEVER shown on screen when the e-mail fails — HR fixes
        # the mail set-up and presses Resend. Only a local test machine (LEXORA_DEV_SHOW_LINKS=1,
        # forced off in production by ops_runtime) gets it back so a tester can finish the flow.
        out["link"] = link_for(purpose, raw)
    return out


# ── The published contract ───────────────────────────────────────────────────
async def ensure_login_for_employee(org_id, employee_id, email, role="employee", *,
                                    send_invite: bool = True, first_name: str | None = None,
                                    last_name: str | None = None, branch_id=None,
                                    actor: dict | None = None) -> dict:
    """Make sure this employee has exactly one login in this org. Idempotent.

    Returns {user_id, created, linked, status, email, role, invited, email_sent,
    link?}. `link` is present only when the invite could not be e-mailed.
    Raises IdentityError when the e-mail already belongs to another employee.
    """
    org, emp = _uuid(org_id), _uuid(employee_id)
    role = normalise_role(role)
    created = linked = False
    async with _sm()() as s:
        existing = (await s.execute(sa.select(users).where(
            users.c.org_id == org, users.c.employee_id == emp))).mappings().first()
        if existing:
            u = existing
        else:
            email = clean_email(email)
            by_mail = (await s.execute(sa.select(users).where(
                users.c.org_id == org, sa.func.lower(users.c.email) == email))).mappings().first()
            if by_mail and by_mail["employee_id"] and by_mail["employee_id"] != emp:
                raise IdentityError("That e-mail already has a login for another employee", 409)
            if by_mail:
                await s.execute(sa.update(users).where(users.c.id == by_mail["id"]).values(employee_id=emp))
                linked = True
                uid = by_mail["id"]
            else:
                uid = uuid.uuid4()
                await s.execute(sa.insert(users).values(
                    id=uid, org_id=org, email=email,
                    # Nobody knows this value — the person sets a real password via the link.
                    hashed_password=hash_password(secrets.token_urlsafe(48)),
                    first_name=first_name, last_name=last_name, role=role, employee_id=emp,
                    branch_scope="org" if role in ORG_SCOPE_ROLES else "branch",
                    branch_id=_uuid(branch_id) if branch_id else None,
                    is_active=True, password_set=False, created_at=now()))
                created = True
            await s.commit()
            u = (await s.execute(sa.select(users).where(users.c.id == uid))).mappings().first()
    out = {"user_id": str(u["id"]), "created": created, "linked": linked, "status": user_status(u),
           "email": u["email"], "role": u["role"], "invited": False, "email_sent": False}
    if created or linked:
        await _audit(org, actor, "auth.user.create" if created else "auth.user.link", u["id"],
                     f"Login {'created' if created else 'linked'} for {u['email']} ({ROLE_LABELS.get(u['role'], u['role'])})",
                     {"employee_id": str(emp), "role": u["role"]})
    if send_invite and u["is_active"] and u["password_set"] is False:
        sent = await send_link(org, u["id"], INVITE, actor=actor)
        out.update(invited=True, email_sent=sent["email_sent"])
        if sent.get("link"):
            out["link"] = sent["link"]
    return out


async def set_user_active(org_id, user_id, active: bool, reason: str | None = None, *,
                          actor: dict | None = None) -> dict:
    """Disable or enable ONE login. Disabling ends its open sessions and kills
    any unused invite / reset link."""
    org, uid = _uuid(org_id), _uuid(user_id)
    t = now()
    async with _sm()() as s:
        u = (await s.execute(sa.select(users).where(
            users.c.id == uid, users.c.org_id == org))).mappings().first()
        if not u:
            raise IdentityError("Login not found", 404)
        if active:
            await s.execute(sa.update(users).where(users.c.id == uid).values(
                is_active=True, disabled_at=None, disabled_reason=None, disabled_by=None))
        else:
            await s.execute(sa.update(users).where(users.c.id == uid).values(
                is_active=False, disabled_at=t, disabled_reason=(reason or "")[:300] or None,
                disabled_by=str((actor or {}).get("user_id") or "") or None, tokens_valid_after=t))
            await s.execute(sa.update(user_tokens).where(
                user_tokens.c.user_id == uid, user_tokens.c.used_at.is_(None)).values(used_at=t))
        await s.commit()
        u = (await s.execute(sa.select(users).where(users.c.id == uid))).mappings().first()
    await _audit(org, actor, "auth.user.enable" if active else "auth.user.disable", uid,
                 f"Login {'enabled' if active else 'disabled'} for {u['email']}"
                 + (f" — {reason}" if (reason and not active) else ""),
                 {"reason": reason} if not active else None)
    return public_user(u)


async def disable_logins_for_employee(org_id, employee_id, reason) -> dict:
    """Exit hook: switch off every login of this employee. Idempotent — returns
    {disabled: n, user_ids: [...]}; n is 0 when there was nothing to disable."""
    org, emp = _uuid(org_id), _uuid(employee_id)
    async with _sm()() as s:
        ids = [r[0] for r in (await s.execute(sa.select(users.c.id).where(
            users.c.org_id == org, users.c.employee_id == emp, users.c.is_active.is_(True)))).all()]
    for uid in ids:
        await set_user_active(org, uid, False, reason, actor={"email": "system", "role": "system"})
    return {"disabled": len(ids), "user_ids": [str(i) for i in ids]}
