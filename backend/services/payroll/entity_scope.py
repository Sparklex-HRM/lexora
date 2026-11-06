"""Payroll — legal-entity scoping (stream D4, multi-entity).

Group companies under one org: every employee belongs to ONE legal entity (employees.entity_id,
set by the employee service). Payroll keeps that fact on each pay stub (pay_stubs.entity_id,
snapshotted when the run executes) and on the run (pay_runs.entity_id — NULL = a run across
ALL entities), so registers, T4s and remittances can be split per entity.

    from entity_scope import (ensure_columns, entities, entity_of, request_entity,
                              split_by_entity, entity_label)

Reads legal_entities through shared.legal_entities (tolerant: no table → single-entity behaviour,
every helper returns None / the org row unchanged).
"""
from __future__ import annotations

import sys
import uuid
from collections import defaultdict

sys.path.append("/app")
from sqlalchemy import text  # noqa: E402

from shared import legal_entities as LE  # noqa: E402

# Columns this stream adds to EXISTING payroll tables (create_all never ALTERs).
COLUMN_DDL = [
    "ALTER TABLE pay_runs ADD COLUMN IF NOT EXISTS entity_id UUID",
    "ALTER TABLE pay_stubs ADD COLUMN IF NOT EXISTS entity_id UUID",
    "CREATE INDEX IF NOT EXISTS ix_pay_runs_entity_id ON pay_runs (entity_id)",
    "CREATE INDEX IF NOT EXISTS ix_pay_stubs_entity_id ON pay_stubs (entity_id)",
]


async def ensure_columns(conn) -> None:
    for ddl in COLUMN_DDL:
        await conn.execute(text(ddl))


def _uuid(v) -> str | None:
    try:
        return str(uuid.UUID(str(v)))
    except (ValueError, TypeError):
        return None


async def entities(org, fresh: bool = False) -> dict[str, dict]:
    """{entity_id: row} incl. inactive (old runs still resolve a name)."""
    return await LE.entity_map(org, fresh=fresh)


async def default_id(org) -> str | None:
    d = await LE.default_entity(org)
    return str(d["id"]) if d else None


def entity_label(ents: dict, entity_id) -> str:
    row = ents.get(str(entity_id)) if entity_id else None
    return LE.entity_label(row)


def request_entity(request) -> str:
    """?entity_id= / X-View-Entity → entity id, "" = all."""
    return LE.request_entity_id(request)


async def entity_of(e: dict, org, dflt: str | None = None) -> str | None:
    """The entity an employee dict (from the employee service) belongs to; unassigned → default."""
    eid = _uuid((e or {}).get("entity_id"))
    if eid:
        return eid
    return dflt if dflt is not None else await default_id(org)


def split_by_entity(emps: list[dict], dflt: str | None) -> dict[str | None, list[dict]]:
    out: dict[str | None, list[dict]] = defaultdict(list)
    for e in emps:
        out[_uuid(e.get("entity_id")) or dflt].append(e)
    return dict(out)


async def filter_employees(emps: list[dict], org, entity_id: str) -> list[dict]:
    """Employees of ONE entity ("" = everyone). Unassigned rows count as the default entity."""
    if not entity_id:
        return emps
    dflt = await default_id(org)
    return [e for e in emps if (_uuid(e.get("entity_id")) or dflt) == entity_id]


async def summary(org, stubs: list, ents: dict | None = None) -> list[dict]:
    """Per-entity roll-up of a run's stubs: [{entity_id, name, short_code, employees, gross, net}]."""
    ents = ents if ents is not None else await entities(org)
    dflt = await default_id(org)
    by: dict[str | None, dict] = {}
    for s in stubs:
        eid = str(s.entity_id) if getattr(s, "entity_id", None) else dflt
        row = by.setdefault(eid, {"entity_id": eid, "name": entity_label(ents, eid) or "—",
                                  "short_code": (ents.get(eid) or {}).get("short_code") if eid else None,
                                  "employees": 0, "gross": 0.0, "net": 0.0})
        row["employees"] += 1
        row["gross"] = round(row["gross"] + float(s.gross or 0), 2)
        row["net"] = round(row["net"] + float(s.net_pay or 0), 2)
    return sorted(by.values(), key=lambda r: (r["name"] or ""))
