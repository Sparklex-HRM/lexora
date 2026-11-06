"""Auth service — two-step sign-in (TOTP MFA), recovery codes, signed-in sessions.

Public (credential = the short-lived MFA challenge token from /auth/login):
    POST /auth/mfa/challenge/verify          {mfa_token, code}    → tokens
    POST /auth/mfa/challenge/enroll/start    {mfa_token}          → secret + otpauth URI (+ QR SVG)
    POST /auth/mfa/challenge/enroll/finish   {mfa_token, code}    → recovery codes + tokens
Signed in:
    GET  /auth/mfa                          my MFA state
    POST /auth/mfa/setup | /enable | /disable | /recovery-codes
    POST /auth/mfa/reset/{user_id}          admin: clear a login's factor (lost phone)
    GET  /auth/sessions/active              where I am signed in (admins: ?user_id=)
    POST /auth/sessions/{sid}/revoke        end one session
    POST /auth/sessions/revoke-others       end every session except this one
    POST /auth/logout                       end this session
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from http_errors import refusal as _refusal  # noqa: E402  (2026-10-24: no raw exception text)
from shared.utils import crypto  # noqa: E402
from shared.utils.audit import record_audit  # noqa: E402
from shared.utils.security import verify_password  # noqa: E402

import abuse  # noqa: E402
import anti_automation as aa  # noqa: E402  (WASA 2026-10-21: proof-of-work CAPTCHA, purpose "mfa")
import account_security  # noqa: E402
import mfa  # noqa: E402
from database import get_db  # noqa: E402
from models import User  # noqa: E402
from saas_models import UserMfa, UserSession  # noqa: E402

router = APIRouter(prefix="/auth", tags=["account-security"])

ADMIN = {"superadmin", "org_admin"}


def _now():
    return datetime.now(timezone.utc)


def _aad(user_id) -> str:
    return f"mfa:{user_id}"


def _qr_svg(uri: str) -> str | None:
    """An SVG QR code when the optional `qrcode` package is installed; else None
    (the screen then shows the setup key to type into the app)."""
    try:
        import qrcode
        import qrcode.image.svg
        img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
        return img.to_string(encoding="unicode")
    except Exception:
        return None


async def _row(db: AsyncSession, user) -> UserMfa:
    r = (await db.execute(select(UserMfa).where(UserMfa.user_id == user.id))).scalar_one_or_none()
    if r is None:
        r = UserMfa(user_id=user.id, org_id=user.org_id, enabled=False)
        db.add(r)
        await db.flush()
    return r


def _setup_payload(user, secret: str) -> dict:
    uri = mfa.otpauth_uri(secret, user.email)
    return {"secret": secret, "secret_grouped": " ".join(secret[i:i + 4] for i in range(0, len(secret), 4)),
            "otpauth_uri": uri, "qr_svg": _qr_svg(uri), "digits": mfa.DIGITS, "period": mfa.STEP}


def _check_code(row: UserMfa, code: str, *, allow_recovery: bool = True) -> str | None:
    """'totp' | 'recovery' when the code is right (and consumes it), else None."""
    if not row or not row.enabled or not row.secret_enc:
        return None
    secret = crypto.decrypt(row.secret_enc, aad=_aad(row.user_id))
    step = mfa.verify_totp(secret, code, last_step=row.last_step)
    if step is not None:
        row.last_step = step
        row.last_used_at = _now()
        return "totp"
    if allow_recovery and mfa.looks_like_recovery(code):
        left = mfa.use_recovery(code, list(row.recovery_hashes or []))
        if left is not None:
            row.recovery_hashes = left
            row.last_used_at = _now()
            return "recovery"
    return None


def _throttle(key: str):
    wait = abuse.hit(abuse.mfa_limiter, key)
    if wait:
        raise HTTPException(429, f"Too many codes tried. Wait {max(wait // 60, 1)} minute(s).",
                            headers={"Retry-After": str(wait)})


async def _me(request: Request, db: AsyncSession) -> User:
    uid = request.headers.get("x-user-id")
    if not uid:
        raise HTTPException(401, "Not authenticated")
    u = (await db.execute(select(User).where(User.id == uuid.UUID(uid)))).scalar_one_or_none()
    if not u:
        raise HTTPException(401, "Not authenticated")
    return u


# ══════════════════════ public: the second sign-in step ══════════════════════

class ChallengeIn(BaseModel):
    mfa_token: str
    code: str | None = None
    captcha_token: str | None = None      # required on verify / enroll-finish (they sign in)


async def _challenge_user(db: AsyncSession, body: ChallengeIn, purposes: tuple[str, ...]):
    try:
        c = account_security.read_challenge(body.mfa_token, purposes)
    except ValueError as exc:
        raise _refusal(401, exc, "AUTH_MFA_STEP_EXPIRED", fallback="This sign-in step has expired. Sign in again.")
    u = (await db.execute(select(User).where(User.id == uuid.UUID(c["sub"])))).scalar_one_or_none()
    if not u or not u.is_active:
        raise HTTPException(401, "This sign-in step has expired. Sign in again.")
    return u, c


async def _complete(u: User, db: AsyncSession, request: Request, method: str):
    from routers import _log_session, _tokens
    u.last_login = _now()
    await db.commit()
    await _log_session(db, request, email=u.email, user=u, success=True, reason=method)
    return await _tokens(u, db, request, method)


@router.post("/mfa/challenge/verify")
async def challenge_verify(body: ChallengeIn, request: Request, db: AsyncSession = Depends(get_db)):
    u, c = await _challenge_user(db, body, ("verify",))
    aa.require_pow(body.captcha_token, request, purposes=("mfa",), account=u.email)
    _throttle(f"mfa|{u.id}")
    row = (await db.execute(select(UserMfa).where(UserMfa.user_id == u.id))).scalar_one_or_none()
    how = _check_code(row, body.code or "")
    if not how:
        from routers import _log_session            # nothing was changed on a wrong code
        await _log_session(db, request, email=u.email, user=u, success=False, reason="bad_mfa_code")
        aa.note_login_failure(u.email, request)      # more proof-of-work for the next try
        raise HTTPException(401, "That code is not right. Use the 6-digit code from your authenticator app, "
                                 "or one of your recovery codes.")
    out = await _complete(u, db, request, "mfa" if how == "totp" else "mfa_recovery")
    if how == "recovery":
        await record_audit(_Hdr(u), "auth.mfa.recovery_used", entity_type="user", entity_id=str(u.id),
                           summary=f"Recovery code used ({len(row.recovery_hashes or [])} left)")
    return out


@router.post("/mfa/challenge/enroll/start")
async def challenge_enroll_start(body: ChallengeIn, request: Request, db: AsyncSession = Depends(get_db)):
    u, _ = await _challenge_user(db, body, ("enroll",))
    _throttle(f"mfa-enrol|{u.id}")
    row = await _row(db, u)
    if row.enabled:
        raise HTTPException(409, "Two-step sign-in is already on for this login. Sign in again.")
    secret = mfa.new_secret()
    row.pending_secret_enc = crypto.encrypt(secret, aad=_aad(u.id))
    await db.commit()
    return _setup_payload(u, secret)


async def _confirm_pending(db: AsyncSession, u: User, code: str) -> list[str]:
    row = await _row(db, u)
    if not row.pending_secret_enc:
        raise HTTPException(409, "Start the set-up first")
    secret = crypto.decrypt(row.pending_secret_enc, aad=_aad(u.id))
    step = mfa.verify_totp(secret, code)
    if step is None:
        raise HTTPException(422, "That code is not right. Check the phone's clock and try the newest code.")
    codes = mfa.new_recovery_codes()
    row.secret_enc, row.pending_secret_enc = row.pending_secret_enc, None
    row.enabled, row.enrolled_at, row.last_step = True, _now(), step
    row.recovery_hashes = [mfa.hash_recovery(c) for c in codes]
    row.recovery_generated_at = _now()
    return codes


@router.post("/mfa/challenge/enroll/finish")
async def challenge_enroll_finish(body: ChallengeIn, request: Request, db: AsyncSession = Depends(get_db)):
    u, c = await _challenge_user(db, body, ("enroll",))
    aa.require_pow(body.captcha_token, request, purposes=("mfa",), account=u.email)
    _throttle(f"mfa-enrol|{u.id}")
    codes = await _confirm_pending(db, u, body.code or "")
    await db.commit()
    await record_audit(_Hdr(u), "auth.mfa.enable", entity_type="user", entity_id=str(u.id),
                       summary="Two-step sign-in turned on (at sign-in)")
    tokens = await _complete(u, db, request, "mfa")
    return {**tokens.model_dump(), "recovery_codes": codes}


class _Hdr:
    """Minimal request stand-in so record_audit can attribute a pre-token action."""
    def __init__(self, u: User):
        self.headers = {"x-org-id": str(u.org_id), "x-user-id": str(u.id), "x-user-email": u.email,
                        "x-role": u.role}


# ══════════════════════ signed in: manage my factor ══════════════════════════

@router.get("/mfa")
async def mfa_status(request: Request, db: AsyncSession = Depends(get_db)):
    u = await _me(request, db)
    row = (await db.execute(select(UserMfa).where(UserMfa.user_id == u.id))).scalar_one_or_none()
    return {"enabled": bool(row and row.enabled), "required": mfa.required_for(u.role),
            "required_roles": sorted(mfa.MFA_REQUIRED_ROLES), "enforced": mfa.enforced(),
            "enrolled_at": row.enrolled_at.isoformat() if row and row.enrolled_at else None,
            "last_used_at": row.last_used_at.isoformat() if row and row.last_used_at else None,
            "recovery_codes_left": len(row.recovery_hashes or []) if row and row.enabled else 0,
            "qr_available": _qr_svg("otpauth://totp/x?secret=A") is not None}


@router.post("/mfa/setup")
async def mfa_setup(request: Request, db: AsyncSession = Depends(get_db)):
    u = await _me(request, db)
    row = await _row(db, u)
    if row.enabled:
        raise HTTPException(409, "Two-step sign-in is already on")
    secret = mfa.new_secret()
    row.pending_secret_enc = crypto.encrypt(secret, aad=_aad(u.id))
    await db.commit()
    return _setup_payload(u, secret)


class CodeIn(BaseModel):
    code: str
    password: str | None = None


@router.post("/mfa/enable")
async def mfa_enable(body: CodeIn, request: Request, db: AsyncSession = Depends(get_db)):
    u = await _me(request, db)
    _throttle(f"mfa-enrol|{u.id}")
    codes = await _confirm_pending(db, u, body.code)
    await db.commit()
    await record_audit(request, "auth.mfa.enable", entity_type="user", entity_id=str(u.id),
                       summary="Two-step sign-in turned on")
    return {"enabled": True, "recovery_codes": codes}


@router.post("/mfa/disable")
async def mfa_disable(body: CodeIn, request: Request, db: AsyncSession = Depends(get_db)):
    u = await _me(request, db)
    if mfa.required_for(u.role):
        raise HTTPException(409, "Two-step sign-in is mandatory for your role and cannot be turned off")
    _throttle(f"mfa|{u.id}")
    row = (await db.execute(select(UserMfa).where(UserMfa.user_id == u.id))).scalar_one_or_none()
    if not body.password or not verify_password(body.password, u.hashed_password):
        raise HTTPException(400, "Current password is wrong")
    if not _check_code(row, body.code):
        raise HTTPException(422, "That code is not right")
    row.enabled, row.secret_enc, row.recovery_hashes = False, None, None
    await db.commit()
    await record_audit(request, "auth.mfa.disable", entity_type="user", entity_id=str(u.id),
                       summary="Two-step sign-in turned off")
    return {"enabled": False}


@router.post("/mfa/recovery-codes")
async def mfa_new_codes(body: CodeIn, request: Request, db: AsyncSession = Depends(get_db)):
    u = await _me(request, db)
    _throttle(f"mfa|{u.id}")
    row = (await db.execute(select(UserMfa).where(UserMfa.user_id == u.id))).scalar_one_or_none()
    if not _check_code(row, body.code, allow_recovery=False):
        raise HTTPException(422, "Enter the current 6-digit code from your app")
    codes = mfa.new_recovery_codes()
    row.recovery_hashes = [mfa.hash_recovery(c) for c in codes]
    row.recovery_generated_at = _now()
    await db.commit()
    await record_audit(request, "auth.mfa.recovery_regenerated", entity_type="user", entity_id=str(u.id),
                       summary="New recovery codes generated (old ones no longer work)")
    return {"recovery_codes": codes}


@router.post("/mfa/reset/{user_id}")
async def mfa_reset(user_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Admin: clear a login's factor (lost phone). The person enrols again at next sign-in."""
    role = request.headers.get("x-role", "")
    if role not in ADMIN:
        raise HTTPException(403, "Only org admins may reset two-step sign-in")
    try:
        target = (await db.execute(select(User).where(User.id == uuid.UUID(user_id)))).scalar_one_or_none()
    except ValueError:
        target = None
    if not target or (role != "superadmin" and str(target.org_id) != request.headers.get("x-org-id")):
        raise HTTPException(404, "Login not found")
    if str(target.id) == request.headers.get("x-user-id"):
        raise HTTPException(409, "You cannot reset your own two-step sign-in — ask another admin")
    row = (await db.execute(select(UserMfa).where(UserMfa.user_id == target.id))).scalar_one_or_none()
    if row:
        row.enabled, row.secret_enc, row.pending_secret_enc, row.recovery_hashes = False, None, None, None
        row.reset_by, row.reset_at = request.headers.get("x-user-id"), _now()
    n = await account_security.revoke_sessions(db, target.id, by=request.headers.get("x-user-id"), reason="mfa_reset")
    await db.commit()
    await record_audit(request, "auth.mfa.reset", entity_type="user", entity_id=str(target.id),
                       summary=f"Two-step sign-in reset for {target.email} ({n} session(s) ended)")
    return {"ok": True, "sessions_ended": n}


# ══════════════════════ signed-in sessions ═══════════════════════════════════

def _sess_out(s: UserSession, current: str | None) -> dict:
    return {"id": str(s.id), "method": s.method, "ip": s.ip, "device": s.device, "user_agent": s.user_agent,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "expires_at": s.expires_at.isoformat() if s.expires_at else None,
            "revoked_at": s.revoked_at.isoformat() if s.revoked_at else None,
            "revoked_reason": s.revoked_reason, "current": str(s.id) == (current or "")}


@router.get("/sessions/active")
async def active_sessions(request: Request, user_id: str | None = None, include_ended: bool = False,
                          db: AsyncSession = Depends(get_db)):
    me = await _me(request, db)
    target = me
    if user_id and user_id != str(me.id):
        if request.headers.get("x-role") not in ADMIN:
            raise HTTPException(403, "Only org admins may see another login's sessions")
        target = (await db.execute(select(User).where(User.id == uuid.UUID(user_id)))).scalar_one_or_none()
        if not target or (request.headers.get("x-role") != "superadmin" and target.org_id != me.org_id):
            raise HTTPException(404, "Login not found")
    q = select(UserSession).where(UserSession.user_id == target.id)
    if not include_ended:
        q = q.where(UserSession.revoked_at.is_(None), UserSession.expires_at > _now())
    rows = (await db.execute(q.order_by(UserSession.created_at.desc()).limit(100))).scalars().all()
    cur = request.headers.get("x-session-id")
    return {"user_id": str(target.id), "email": target.email, "items": [_sess_out(s, cur) for s in rows]}


async def _revoke_one(request: Request, db: AsyncSession, sid: str, reason: str) -> dict:
    me = await _me(request, db)
    try:
        s = (await db.execute(select(UserSession).where(UserSession.id == uuid.UUID(sid)))).scalar_one_or_none()
    except ValueError:
        s = None
    if not s:
        raise HTTPException(404, "Session not found")
    if s.user_id != me.id:
        if request.headers.get("x-role") not in ADMIN or (
                request.headers.get("x-role") != "superadmin" and s.org_id != me.org_id):
            raise HTTPException(404, "Session not found")
    if not s.revoked_at:
        s.revoked_at, s.revoked_by, s.revoked_reason = _now(), str(me.id), reason
        await db.commit()
        await record_audit(request, "auth.session.revoke", entity_type="session", entity_id=sid,
                           summary=f"Session ended ({reason}) for user {s.user_id}")
    return {"ok": True, "id": sid}


@router.post("/sessions/revoke-others")
async def revoke_others(request: Request, db: AsyncSession = Depends(get_db)):
    me = await _me(request, db)
    n = await account_security.revoke_sessions(db, me.id, except_sid=request.headers.get("x-session-id"),
                                               by=str(me.id), reason="signed_out_elsewhere")
    await db.commit()
    await record_audit(request, "auth.session.revoke_others", entity_type="user", entity_id=str(me.id),
                       summary=f"Signed out of {n} other session(s)")
    return {"ok": True, "ended": n}


@router.post("/sessions/{sid}/revoke")
async def revoke_session(sid: str, request: Request, db: AsyncSession = Depends(get_db)):
    return await _revoke_one(request, db, sid, "revoked")


@router.post("/logout")
async def logout(request: Request, db: AsyncSession = Depends(get_db)):
    sid = request.headers.get("x-session-id")
    if not sid:
        return {"ok": True}
    return await _revoke_one(request, db, sid, "logout")
