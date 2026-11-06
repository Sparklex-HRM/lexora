"""Lexora HRM — create the FIRST platform operator (superadmin) on an empty production database.

Go-live 2026-10-24. Production starts with no demo data, so there is no `jethro@lexora.io`: the
operator who provisions customer tenants (Lexora Platform → Tenants → New tenant) is created
here, once, from the command line inside the auth container. Nothing else creates a superadmin.

    # VM (docker compose): password typed at the hidden prompt
    docker compose -f docker-compose.prod.yml run --rm auth-service python platform_admin.py create \\
        --email ops@yourcompany.ca --first-name Asha --last-name Rao
    # non-interactive (CI / k8s Job): password on stdin, never on the command line
    printf '%s\\n' "$PW" | python platform_admin.py create --email ops@yourcompany.ca --password-stdin
    python platform_admin.py list            # e-mails of existing superadmins (no secrets)

Rules
* Refuses when a superadmin already exists (add more operators with ``--additional``).
* The password must pass the same policy + breached-password check as every other login; it is
  never printed or logged.
* Two-step sign-in (TOTP) is mandatory for superadmin: the first sign-in asks to enrol an
  authenticator app (mfa.MFA_REQUIRED_ROLES).
* The operator lives in its own org "Lexora Platform" (code PLATFORM) — not in a customer org.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

sys.path.append("/app")

PLATFORM_ORG_NAME = "Lexora Platform"
PLATFORM_ORG_CODE = "PLATFORM"


async def _init_schema() -> None:
    """Tables exist after the first auth boot; on a database where the service never started yet the
    same idempotent boot DDL runs here. (Tests replace this with their own SQLite set-up.)"""
    from database import init_db
    await init_db()


def _read_password(args) -> str:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    if not sys.stdin.isatty():
        raise SystemExit("No terminal for the password prompt: pipe it with --password-stdin")
    pw = getpass.getpass("New superadmin password: ")
    if pw != getpass.getpass("Repeat the password: "):
        raise SystemExit("The two passwords differ — nothing was created")
    return pw


async def _list() -> int:
    from sqlalchemy import select
    from database import SessionLocal
    from models import User
    await _init_schema()
    async with SessionLocal() as s:
        rows = (await s.execute(select(User.email, User.is_active).where(User.role == "superadmin"))).all()
    for email, active in rows:
        print(f"{email}\t{'active' if active else 'disabled'}")
    print(f"{len(rows)} superadmin login(s)")
    return 0


async def create_superadmin(email: str, password: str, first_name: str = "Platform", last_name: str = "Admin",
                            additional: bool = False) -> dict:
    """Create the platform org (once) + a superadmin login. Returns ids (no secrets)."""
    from sqlalchemy import func, select
    from database import SessionLocal
    from models import Organization, User
    from password_guard import breach_problems
    from security_policy import password_problems
    from shared.utils.security import hash_password

    email = (email or "").strip().lower()
    if "@" not in email or email.endswith(".example"):
        raise ValueError("Give a real e-mail address for the operator")
    problems = password_problems(password, email=email) + breach_problems(password)
    if problems:
        raise ValueError("Password needs: " + ", ".join(problems))
    await _init_schema()
    async with SessionLocal() as s:
        supers = (await s.execute(select(func.count()).select_from(User).where(User.role == "superadmin"))).scalar_one()
        if supers and not additional:
            raise ValueError(f"{supers} superadmin login(s) already exist — use --additional to add another operator")
        if (await s.execute(select(User.id).where(func.lower(User.email) == email))).first():
            raise ValueError("That e-mail already has a login")
        org = (await s.execute(select(Organization).where(func.upper(Organization.code) == PLATFORM_ORG_CODE))
               ).scalar_one_or_none()
        if org is None:
            org = Organization(name=PLATFORM_ORG_NAME, legal_name=PLATFORM_ORG_NAME, code=PLATFORM_ORG_CODE,
                               country="CA", province="ON", timezone="America/Toronto", plan="enterprise",
                               is_active=True)
            if hasattr(org, "signup_source"):
                org.signup_source = "platform_bootstrap"
            if hasattr(org, "subscription_status"):
                org.subscription_status = "active"
            s.add(org)
            await s.flush()
        user = User(org_id=org.id, email=email, hashed_password=hash_password(password),
                    first_name=first_name, last_name=last_name, role="superadmin", branch_scope="org",
                    password_set=True, must_change_password=False)
        if hasattr(User, "password_changed_at"):
            from datetime import datetime, timezone
            user.password_changed_at = datetime.now(timezone.utc)
        s.add(user)
        await s.commit()
        return {"org_id": str(org.id), "org_code": org.code, "user_id": str(user.id), "email": email}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Create / list Lexora HRM platform operators (superadmin).")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--email", required=True)
    c.add_argument("--first-name", default="Platform")
    c.add_argument("--last-name", default="Admin")
    c.add_argument("--password-stdin", action="store_true", help="read the password from the first stdin line")
    c.add_argument("--additional", action="store_true", help="allow a second / further superadmin")
    sub.add_parser("list")
    args = p.parse_args(argv)
    if args.cmd == "list":
        return asyncio.run(_list())
    pw = _read_password(args)
    try:
        out = asyncio.run(create_superadmin(args.email, pw, args.first_name, args.last_name, args.additional))
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(f"superadmin created: {out['email']} (org {out['org_code']}). Sign in WITHOUT an org code; "
          "you will be asked to set up two-step sign-in.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
