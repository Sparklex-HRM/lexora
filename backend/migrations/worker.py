"""Per-service schema worker — run ONE service's models in their own process.

Every service keeps its own ``models.py`` / ``database.py`` / ``main.py`` imported by bare
name (``import models``), so two services can never share one interpreter. The
migration tooling therefore runs this module once per service, as a subprocess:

    python -m migrations.worker <service> bootstrap   # exactly the DDL a service boot runs
    python -m migrations.worker <service> check       # models vs. live DB: missing tables/columns

``bootstrap`` runs the service's FastAPI lifespan START (create_all + its idempotent
``ADD COLUMN IF NOT EXISTS`` / index DDL + boot-time ``ensure`` helpers) and then shuts
down. Background ticks are NOT started (``asyncio.create_task`` is stubbed during the
lifespan), so a migration never sends mail, applies revisions or runs watchdogs.

``check`` prints one JSON line: {"service", "missing_tables", "missing_columns", "extra_columns"}
and exits 1 when the models need something the database lacks — the create_all trap
(create_all never ALTERs an existing table, so a new column silently never appears).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)

SERVICES = ["auth", "employee", "onboarding", "payroll", "time_attendance", "leave",
            "approvals", "notification", "ai_platform", "ops", "compliance", "integrations"]


def _load(service: str):
    if service not in SERVICES:
        raise SystemExit(f"unknown service {service!r}; one of {SERVICES}")
    svc_dir = os.path.join(BACKEND, "services", service)
    for p in (BACKEND, svc_dir):
        if p not in sys.path:
            sys.path.insert(0, p)
    os.chdir(svc_dir)
    # belt and braces: the env switches the ticks honour, in case one bypasses create_task
    for k, v in (("LEXORA_LIFECYCLE_TICK", "0"), ("EXIT_TICK_DISABLED", "1"),
                 ("ENGAGE_TICK_DISABLED", "1"), ("LEXORA_PRIVACY_SCHEDULER", "0")):
        os.environ[k] = v
    import main  # noqa: F401  (registers every model of the service on the shared Base)
    return main


async def _bootstrap(service: str) -> None:
    main = _load(service)
    loop = asyncio.get_running_loop()
    real = asyncio.create_task

    def _no_task(coro, *a, **kw):                     # schema only — no background work
        coro.close()
        fut = loop.create_future()
        fut.set_result(None)
        return fut

    asyncio.create_task = _no_task                   # type: ignore[assignment]
    try:
        async with main.app.router.lifespan_context(main.app):
            pass
        await _lazy_ensures()
    finally:
        asyncio.create_task = real                   # type: ignore[assignment]
        await _dispose(main)
    print(json.dumps({"service": service, "bootstrap": "ok"}))


async def _lazy_ensures() -> None:
    """Some modules add their columns on first use, not at boot (``ensure_schema(db)`` in
    ops/psa_routers.py, time_attendance/psa_lines.py). Run them too, so a fresh install
    has every column an old database picked up over time."""
    import inspect
    db = sys.modules.get("database")
    session_cls = getattr(db, "SessionLocal", None)
    if session_cls is None:
        return
    svc_dir = os.getcwd()
    for name, mod in sorted(sys.modules.items()):
        f = getattr(mod, "__file__", None) or ""
        fn = getattr(mod, "ensure_schema", None)
        if f.startswith(svc_dir) and fn is not None and inspect.iscoroutinefunction(fn):
            async with session_cls() as s:
                await fn(s)
                await s.commit()


async def _dispose(main) -> None:
    db = sys.modules.get("database")
    eng = getattr(db, "engine", None)
    if eng is not None:
        await eng.dispose()


async def _check(service: str) -> int:
    _load(service)
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from shared.utils.db import Base
    database = sys.modules.get("database")  # the service's own engine, if it has one

    md = _all_metadata(Base)

    def _cmp(sync_conn):
        # include_schemas: compare every schema the services use (all live in `public` today).
        ctx = MigrationContext.configure(sync_conn, opts={"compare_type": False, "include_schemas": True})
        return compare_metadata(ctx, md)

    engine = getattr(database, "engine", None)
    if engine is None:                               # approvals / notification use the shared engine
        from shared.utils import platform_models as pm
        engine = pm.get_engine()
    async with engine.connect() as conn:
        diffs = await conn.run_sync(_cmp)
    await engine.dispose()

    missing_t, missing_c, extra_c, missing_i = [], [], [], []
    for d in _flatten(diffs):
        kind = d[0]
        if kind == "add_table":
            missing_t.append(d[1].name)
        elif kind == "add_column":
            missing_c.append(f"{d[2]}.{d[3].name}")
        elif kind == "remove_column" and d[2] in md.tables:
            extra_c.append(f"{d[2]}.{d[3].name}")
        elif kind == "add_index" and d[1].table is not None and d[1].table.name not in missing_t:
            missing_i.append(f"{d[1].table.name}.{d[1].name}")
    # missing indexes are a WARNING (slower, not broken) — tables/columns fail the check
    out = {"service": service, "missing_tables": sorted(missing_t),
           "missing_columns": sorted(missing_c), "extra_columns": sorted(extra_c),
           "missing_indexes": sorted(missing_i)}
    print(json.dumps(out))
    return 1 if (missing_t or missing_c) else 0


def _all_metadata(base):
    """One MetaData holding every table of every declarative base loaded in this process
    (the service Base, shared PlatformBase, any module-local base)."""
    from sqlalchemy import MetaData
    from sqlalchemy.orm import DeclarativeBase

    seen, todo, out = set(), list(DeclarativeBase.__subclasses__()), MetaData()
    while todo:
        cls = todo.pop()
        todo.extend(cls.__subclasses__())
        meta = getattr(cls, "metadata", None)
        if meta is None or id(meta) in seen:
            continue
        seen.add(id(meta))
        for t in meta.tables.values():
            if t.key not in out.tables:
                t.to_metadata(out)
    return out


def _flatten(diffs):
    for d in diffs:
        if isinstance(d, list):          # modify_* groups
            yield from _flatten(d)
        else:
            yield d


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in ("bootstrap", "check"):
        print(__doc__)
        return 2
    service, cmd = argv
    if cmd == "bootstrap":
        asyncio.run(_bootstrap(service))
        return 0
    return asyncio.run(_check(service))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
