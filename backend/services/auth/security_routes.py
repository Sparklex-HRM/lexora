"""Auth service — launch security routes: password change + policy, session
audit, org data export (full backup zip), public login-footer contact.

`/auth/login-info` is reachable without a token because the gateway's public
list matches the "/auth/login" prefix; it exposes only the org's display name
and the Privacy Officer contact the org chose to publish (PIPEDA Principle 4.1 / 4.8).
"""
import os
import io
import json
import sys
import zipfile
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils.audit import record_audit  # noqa: E402
from shared.utils.security import hash_password, verify_password  # noqa: E402

from database import get_db  # noqa: E402
from models import LoginSession, Organization, User  # noqa: E402
from security_policy import MIN_LEN, POLICY_TEXT, account_limiter, password_problems  # noqa: E402
from shared.utils.ratelimit import make_login_limiter  # noqa: E402
import account_security  # noqa: E402
from password_guard import HISTORY_DEPTH, breach_problems, reused  # noqa: E402

router = APIRouter(prefix="/auth", tags=["security"])

ADMIN = {"superadmin", "org_admin", "hr_admin"}


def _role(request: Request) -> str:
    return request.headers.get("x-role", "") or ""


@router.get("/password-policy")
async def password_policy():
    return {"text": POLICY_TEXT, "min_length": MIN_LEN}


@router.get("/login-info")
async def login_info(org_code: str | None = None, db: AsyncSession = Depends(get_db)):
    """Public: org display name + published DPO / grievance contact for the login
    footer. Falls back to the Lexora HRM platform contact when the org has none."""
    # The platform's own contact comes from the server's settings — never a made-up address.
    # Unset → null, and the login footer says the contact is not published yet.
    platform = {"dpo_name": os.getenv("LEXORA_PLATFORM_DPO_NAME", "").strip() or None,
                "dpo_email": os.getenv("LEXORA_PLATFORM_DPO_EMAIL", "").strip() or None,
                "grievance_email": os.getenv("LEXORA_PLATFORM_GRIEVANCE_EMAIL", "").strip() or None,
                "role": "Data Processor"}
    out = {"org_code": (org_code or "").upper() or None, "org_name": None, "country": None,
           "contact": None, "platform_contact": platform, "password_policy": POLICY_TEXT,
           "law": "PIPEDA (Personal Information Protection and Electronic Documents Act)"}
    if not org_code:
        return out
    org = (await db.execute(select(Organization).where(
        func.upper(Organization.code) == org_code.strip().upper()))).scalar_one_or_none()
    if not org:
        return out
    out.update({"org_name": org.name, "country": org.country})
    try:
        row = (await db.execute(text(
            "SELECT dpo_name, dpo_email, dpo_phone, grievance_officer_name, grievance_officer_email "
            "FROM privacy_settings WHERE org_id = :o"), {"o": org.id})).first()
        if row and (row[0] or row[1] or row[3] or row[4]):
            out["contact"] = {"dpo_name": row[0], "dpo_email": row[1], "dpo_phone": row[2],
                              "grievance_officer_name": row[3], "grievance_officer_email": row[4]}
    except Exception:
        pass            # compliance tables not created yet → platform contact only
    return out


class ChangePassword(BaseModel):
    current_password: str
    new_password: str


# WASA 2026-10-21: guessing the current password through change-password is
# limited like a login — 5 wrong per user in 15 minutes → 429 for 15 minutes,
# and each wrong one also counts on the login's per-account lockout.
change_password_limiter = make_login_limiter(5, 900, 900, name="change_password")


async def _org_code(db: AsyncSession, org_id) -> str:
    try:
        return ((await db.execute(select(Organization.code).where(Organization.id == org_id))).scalar_one_or_none()
                or "").strip().upper()
    except Exception:  # noqa: BLE001
        return ""


@router.post("/change-password")
async def change_password(body: ChangePassword, request: Request, db: AsyncSession = Depends(get_db)):
    uid = request.headers.get("x-user-id")
    if not uid:
        raise HTTPException(401, "Not authenticated")
    import uuid as _uuid
    try:
        uid_v = _uuid.UUID(uid)
    except ValueError:
        raise HTTPException(401, "Not authenticated")
    wait = change_password_limiter.retry_after(str(uid_v))
    if wait:
        raise HTTPException(429, f"Too many wrong passwords. Wait {max(wait // 60, 1)} minute(s) and try again.",
                            headers={"Retry-After": str(wait)})
    user = (await db.execute(select(User).where(User.id == uid_v))).scalar_one_or_none()
    if not user:
        raise HTTPException(401, "Not authenticated")
    if not verify_password(body.current_password, user.hashed_password):
        # 400, not 401: a client that refreshes and retries on 401 would count twice.
        change_password_limiter.record_failure(str(uid_v))
        account_limiter.record_failure(f"{user.email.lower()}|{await _org_code(db, user.org_id)}")
        raise HTTPException(400, "Current password is wrong")
    change_password_limiter.record_success(str(uid_v))
    problems = password_problems(body.new_password, email=user.email) + breach_problems(body.new_password)
    if problems:
        raise HTTPException(422, "Password needs: " + ", ".join(problems))
    if verify_password(body.new_password, user.hashed_password):
        raise HTTPException(422, "New password must differ from the current one")
    if reused(body.new_password, await account_security.history_hashes(db, user)):
        raise HTTPException(422, f"Password needs: not one of your last {HISTORY_DEPTH} passwords")
    await account_security.remember_password(db, user.id, user.hashed_password)
    user.hashed_password = hash_password(body.new_password)
    user.password_changed_at = datetime.now(timezone.utc)
    forced = bool(getattr(user, "must_change_password", False))
    user.must_change_password = False
    # S4: a password change signs out every OTHER session of this login.
    # WASA #7: after a FORCED change the current session ends too — its token
    # carries pwchg — and a fresh token without pwchg is returned instead.
    await account_security.revoke_sessions(db, user.id,
                                           except_sid=None if forced else request.headers.get("x-session-id"),
                                           by=uid, reason="password_changed")
    await db.commit()
    await record_audit(request, "auth.password.change", entity_type="user", entity_id=uid,
                       summary="Password changed" + (" (first sign-in, forced)" if forced else ""))
    out = {"ok": True, "changed_at": user.password_changed_at.isoformat()}
    if forced:
        from routers import _tokens
        t = await _tokens(user, db, request, "password")
        out.update(t.model_dump())
    return out


@router.get("/sessions")
async def sessions(request: Request, q: str | None = None, success: bool | None = None,
                   skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_db)):
    """Session audit: admins see the whole org's sign-ins; everyone else their own."""
    import uuid as _uuid

    def _u(v):
        try:
            return _uuid.UUID(str(v))
        except (ValueError, TypeError):
            raise HTTPException(401, "Not authenticated")
    conds = []
    if _role(request) == "superadmin":
        pass
    elif _role(request) in ADMIN:
        conds.append(LoginSession.org_id == _u(request.headers.get("x-org-id")))
    else:
        conds.append(LoginSession.user_id == _u(request.headers.get("x-user-id")))
    if success is not None:
        conds.append(LoginSession.success.is_(success))
    if q:
        like = f"%{q}%"
        conds.append(or_(LoginSession.email.ilike(like), LoginSession.ip.ilike(like),
                         LoginSession.reason.ilike(like)))
    base = select(LoginSession)
    if conds:
        base = base.where(*conds)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0
    rows = (await db.execute(base.order_by(LoginSession.at.desc()).offset(skip).limit(min(limit, 500)))).scalars().all()
    return {"total": total, "items": [{
        "id": str(r.id), "email": r.email, "role": r.role, "ip": r.ip, "user_agent": r.user_agent,
        "success": r.success, "reason": r.reason, "at": r.at.isoformat() if r.at else None} for r in rows]}


# Tables with an org_id column are the org's data; sensitive identifier columns
# are already ciphertext on disk and are exported as stored.
_EXCLUDE_TABLES = {"audit_events"}


@router.get("/org-export")
async def org_export(request: Request, include_audit: bool = False, db: AsyncSession = Depends(get_db)):
    """Full org backup: one JSON file per table (rows where org_id = this org) in a zip."""
    if _role(request) not in {"superadmin", "org_admin"}:
        raise HTTPException(403, "Only org admins may export the organization's data")
    org = request.headers.get("x-org-id")
    tables = [r[0] for r in (await db.execute(text(
        "SELECT DISTINCT table_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND column_name = 'org_id' ORDER BY 1"))).all()]
    buf = io.BytesIO()
    manifest = {"org_id": org, "exported_at": datetime.now(timezone.utc).isoformat(), "tables": {}}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for t in tables:
            if t in _EXCLUDE_TABLES and not include_audit:
                continue
            rows = (await db.execute(text(f'SELECT * FROM "{t}" WHERE org_id = :o'), {"o": org})).mappings().all()
            data = [dict(r) for r in rows]
            z.writestr(f"{t}.json", json.dumps(data, default=str, indent=1))
            manifest["tables"][t] = len(data)
        orgrow = (await db.execute(text("SELECT * FROM organizations WHERE id = :o"), {"o": org})).mappings().first()
        z.writestr("organization.json", json.dumps(dict(orgrow) if orgrow else {}, default=str, indent=1))
        z.writestr("manifest.json", json.dumps(manifest, indent=1))
    buf.seek(0)
    await record_audit(request, "org.export", entity_type="organization", entity_id=org,
                       summary=f"Full org export ({sum(manifest['tables'].values())} rows, {len(manifest['tables'])} tables)")
    fname = f"jethro-org-export-{datetime.now(timezone.utc):%Y%m%d-%H%M}.zip"
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{fname}"'})
