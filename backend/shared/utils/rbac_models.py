"""Lexora HRM — org custom roles, role assignments and their change log (2026-10-24).

Owned (created / written) by the auth service — `import shared.utils.rbac_models` registers the tables on the
shared Base, so auth's boot create_all makes them. The gateway READS them (identity.fetch_user_state) to honour
custom roles on every request.

    rbac_roles        a custom role of one org: name, base system role, data reach, permission list, status
    rbac_user_roles   login <-> custom role (a login keeps its ONE system role in users.role, plus 0..n of these)
    rbac_changes      every create / edit / retire / restore / delete / assign / unassign — written in the SAME
                      transaction as the change (the tamper-evident audit_events chain gets a copy, best effort)

Generic column types (Uuid / JSON / DateTime) so the auth unit tests run on SQLite.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from shared.utils.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RbacRole(Base):
    __tablename__ = "rbac_roles"
    __table_args__ = (sa.UniqueConstraint("org_id", "key", name="rbac_roles_org_key_uk"),)

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, index=True, nullable=False)
    key: Mapped[str] = mapped_column(sa.String(64), nullable=False)          # slug, unique per org
    name: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text)
    base_role: Mapped[str] = mapped_column(sa.String(30), nullable=False)    # system role it is built on
    reach: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="org")
    permissions: Mapped[list] = mapped_column(sa.JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(sa.String(12), nullable=False, default="active")   # active | retired
    cloned_from: Mapped[str | None] = mapped_column(sa.String(64))
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
    created_by: Mapped[str | None] = mapped_column(sa.String(200))
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now)
    updated_by: Mapped[str | None] = mapped_column(sa.String(200))
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now)


class RbacUserRole(Base):
    __tablename__ = "rbac_user_roles"
    __table_args__ = (sa.UniqueConstraint("user_id", "role_id", name="rbac_user_roles_uk"),)

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, index=True, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, index=True, nullable=False)
    role_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, index=True, nullable=False)
    assigned_by: Mapped[str | None] = mapped_column(sa.String(200))
    assigned_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now)


class RbacChange(Base):
    __tablename__ = "rbac_changes"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, index=True, nullable=False)
    at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now, index=True)
    actor_id: Mapped[str | None] = mapped_column(sa.String(64))
    actor_email: Mapped[str | None] = mapped_column(sa.String(200))
    actor_role: Mapped[str | None] = mapped_column(sa.String(40))
    action: Mapped[str] = mapped_column(sa.String(40), nullable=False)       # role.create | role.edit | … | user.assign
    role_id: Mapped[str | None] = mapped_column(sa.String(64), index=True)
    role_key: Mapped[str | None] = mapped_column(sa.String(64))
    user_id: Mapped[str | None] = mapped_column(sa.String(64), index=True)
    summary: Mapped[str | None] = mapped_column(sa.Text)
    before: Mapped[dict | None] = mapped_column(sa.JSON)
    after: Mapped[dict | None] = mapped_column(sa.JSON)


roles_t = RbacRole.__table__
user_roles_t = RbacUserRole.__table__
changes_t = RbacChange.__table__


def role_dict(r) -> dict:
    """Plain dict of a rbac_roles row (ORM object or mapping)."""
    g = (lambda k: getattr(r, k)) if not hasattr(r, "keys") else (lambda k: r[k])
    return {"id": str(g("id")), "key": g("key"), "name": g("name"), "description": g("description"),
            "base_role": g("base_role"), "reach": g("reach"), "permissions": list(g("permissions") or []),
            "status": g("status"), "version": g("version"), "cloned_from": g("cloned_from")}


async def custom_roles_for(session, user_id, *, active_only: bool = True) -> list[dict]:
    """The custom roles assigned to one login (active ones only by default)."""
    uid = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
    q = (sa.select(roles_t).join(user_roles_t, user_roles_t.c.role_id == roles_t.c.id)
         .where(user_roles_t.c.user_id == uid))
    if active_only:
        q = q.where(roles_t.c.status == "active")
    rows = (await session.execute(q.order_by(roles_t.c.name))).mappings().all()
    return [role_dict(r) for r in rows]


def access_from(system_role: str, custom_roles: list[dict]) -> dict:
    """What a login may do: {role, roles, ui_roles, perms, reach, custom} — the ui_grant body and the
    effective-permission preview. Pure (no database)."""
    from shared.utils import rbac_catalogue as cat
    from shared.utils import rbac_grant as g
    role = (system_role or "").strip().lower()
    active = [c for c in custom_roles or [] if (c.get("status") or "active") == "active"]
    ui_roles = [role] + [cat.acting_role(c["base_role"], c["reach"]) for c in active]
    seen: list[str] = []
    for r in ui_roles:
        if r and r not in seen:
            seen.append(r)
    return {"role": role, "roles": [role] + [f"custom:{c['key']}" for c in active], "ui_roles": seen,
            "perms": sorted(g.effective_permissions(role, active)),
            "custom_perms": sorted({p for c in active for p in (c.get("permissions") or [])}),
            "reach": g.reach_of(role, [c["reach"] for c in active]),
            "custom": [{"key": c["key"], "name": c["name"], "base_role": c["base_role"], "reach": c["reach"],
                        "acting_role": cat.acting_role(c["base_role"], c["reach"])} for c in active]}
