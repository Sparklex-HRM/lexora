"""Lexora HRM — JWT + password primitives (single source of truth)."""
import datetime as _dt
import uuid

import bcrypt
import jwt

from shared.utils.config import settings


# ---- passwords ----------------------------------------------------------------
def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode()[:72], bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode()[:72], hashed.encode())
    except (ValueError, TypeError):
        return False


# ---- JWT ----------------------------------------------------------------------
def create_access_token(claims: dict) -> str:
    now = _dt.datetime.now(_dt.timezone.utc)
    payload = {
        **claims,
        "type": "access",
        "iat": now,
        "exp": now + _dt.timedelta(minutes=settings.ACCESS_EXPIRE_MIN),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALG)


def create_refresh_token(claims: dict) -> str:
    now = _dt.datetime.now(_dt.timezone.utc)
    payload = {
        **claims,
        "type": "refresh",
        "iat": now,
        "exp": now + _dt.timedelta(days=settings.REFRESH_EXPIRE_DAYS),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALG)


def decode_token(token: str) -> dict:
    """Raises jwt.PyJWTError on invalid/expired."""
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALG])
