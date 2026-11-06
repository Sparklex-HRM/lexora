"""Read-only lookups of employees / logins in the shared Lexora HRM database.

Phones and names are read at the moment they are needed and NOT copied into the
integration tables (only a masked phone + a hash for the WhatsApp opt-in).
Tests replace ``lookup`` with a stub.
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from shared.utils import platform_models as pm

log = logging.getLogger("lexora.integrations.people")

_SQL = text(
    "SELECT e.id AS id, e.employee_number, e.first_name, e.last_name, e.phone, e.email, e.date_of_birth, "
    "  (SELECT u.id FROM users u WHERE u.employee_id = e.id AND u.org_id = e.org_id LIMIT 1) AS user_id "
    "FROM employees e WHERE e.org_id = CAST(:org AS uuid) AND e.id = CAST(:emp AS uuid)")


async def _lookup(org_id: str, employee_id: str) -> dict | None:
    try:
        async with pm.session() as s:
            r = (await s.execute(_SQL, {"org": str(org_id), "emp": str(employee_id)})).mappings().first()
    except Exception as e:                                   # noqa: BLE001
        log.warning("employee lookup failed: %s", e)
        return None
    if not r:
        return None
    d = dict(r)
    d["id"] = str(d["id"])
    d["user_id"] = str(d["user_id"]) if d.get("user_id") else None
    d["name"] = f"{d.get('first_name') or ''} {d.get('last_name') or ''}".strip()
    dob = d.get("date_of_birth")
    d["birth_year"] = str(dob.year) if hasattr(dob, "year") else (str(dob)[:4] if dob else None)
    return d


lookup = _lookup


async def get(org_id: str, employee_id: str) -> dict | None:
    return await lookup(org_id, employee_id)
