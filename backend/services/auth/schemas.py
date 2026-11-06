"""Auth service — request/response schemas."""
from pydantic import BaseModel, EmailStr


class RegisterOrg(BaseModel):
    org_name: str
    country: str = "CA"           # Canada-only — anything else is refused
    province: str = "ON"          # one of the 13 province / territory codes (ON, BC, AB, QC …)
    plan: str = "growth"          # starter | growth | enterprise — gates the nav modules
    admin_email: EmailStr
    admin_password: str
    admin_first_name: str | None = None
    admin_last_name: str | None = None
    captcha_token: str | None = None   # WASA 2026-10-21: our own proof-of-work CAPTCHA (purpose "signup")


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    org_code: str | None = None   # required for tenant users; omitted for superadmin
    captcha_token: str | None = None   # WASA 2026-10-21: proof-of-work CAPTCHA (purpose "login")


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    org_id: str
    role: str
    # WASA #7: the password was chosen by someone else (platform "New tenant") —
    # the web shows the forced "Choose a new password" step; the token carries pwchg.
    password_change_required: bool = False
    # 2026-10-24: ES256 compact JWS (role + permissions + modules) the web app verifies with a pinned key
    ui_grant: str | None = None
