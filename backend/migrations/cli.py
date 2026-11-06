"""Lexora HRM schema migrations — one Alembic chain for the one shared Postgres.

    python -m migrations.cli upgrade [rev]     # default head; baseline is idempotent on an existing DB
    python -m migrations.cli current           # revision recorded in lexora_schema_version
    python -m migrations.cli history
    python -m migrations.cli check             # every service: models vs. DB (exit 1 on drift)
    python -m migrations.cli bootstrap         # boot-time DDL of every service, no Alembic
    python -m migrations.cli downgrade <rev>   # only for revisions that define a downgrade
    python -m migrations.cli revision -m "add x to y"   # new empty revision file

Why one chain (not one per service): all 11 services share ONE database and several
tables are written by more than one service (platform/audit tables via backend/shared),
so a single ordered history is the only one that can be reasoned about. Each service's
model code still runs in its own process (see migrations/worker.py).

Reads DATABASE_URL (postgresql+asyncpg://…). Wrapped by scripts/ops/migrate.sh.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from migrations.worker import SERVICES  # noqa: E402

VERSION_TABLE = "lexora_schema_version"


def run_worker(service: str, cmd: str) -> tuple[int, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = BACKEND + os.pathsep + env.get("PYTHONPATH", "")
    p = subprocess.run([sys.executable, "-m", "migrations.worker", service, cmd],
                       cwd=BACKEND, env=env, capture_output=True, text=True)
    out = (p.stdout or "").strip().splitlines()
    last = out[-1] if out else ""
    if p.returncode != 0 and (cmd == "bootstrap" or not last.startswith("{")):
        sys.stderr.write(f"[{service}] {cmd} failed (exit {p.returncode})\n{p.stderr[-4000:]}\n")
    return p.returncode, last


def bootstrap_all() -> None:
    """Every service's boot-time DDL, in dependency order. Idempotent."""
    for svc in SERVICES:
        code, line = run_worker(svc, "bootstrap")
        if code != 0:
            raise RuntimeError(f"bootstrap failed for service {svc}")
        print(f"  bootstrap {svc:16s} ok")


def check_all() -> int:
    bad = 0
    for svc in SERVICES:
        code, line = run_worker(svc, "check")
        try:
            res = json.loads(line)
        except ValueError:
            print(f"  check {svc:16s} ERROR (see stderr)")
            bad += 1
            continue
        miss = res["missing_tables"] + res["missing_columns"]
        status = "OK" if not miss else "DRIFT"
        print(f"  check {svc:16s} {status}  missing_tables={len(res['missing_tables'])} "
              f"missing_columns={len(res['missing_columns'])} db_only_columns={len(res['extra_columns'])} "
              f"missing_indexes={len(res.get('missing_indexes', []))}")
        for m in miss:
            print(f"      needs: {m}")
        for m in res.get("missing_indexes", []):
            print(f"      warn index: {m}")
        bad += 1 if code else 0
    return 1 if bad else 0


def _cfg():
    from alembic.config import Config
    cfg = Config(os.path.join(HERE, "alembic.ini"))
    cfg.set_main_option("script_location", HERE)
    return cfg


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "bootstrap":
        bootstrap_all()
        return 0
    if cmd == "check":
        return check_all()

    from alembic import command
    cfg = _cfg()
    if cmd == "upgrade":
        command.upgrade(cfg, rest[0] if rest else "head")
    elif cmd == "downgrade":
        if not rest:
            print("downgrade needs a target revision")
            return 2
        command.downgrade(cfg, rest[0])
    elif cmd == "current":
        command.current(cfg, verbose=False)
    elif cmd == "heads":
        command.heads(cfg, verbose=False)
    elif cmd == "history":
        command.history(cfg, verbose=False)
    elif cmd == "stamp":
        command.stamp(cfg, rest[0] if rest else "head")
    elif cmd == "revision":
        msg = rest[rest.index("-m") + 1] if "-m" in rest else "change"
        command.revision(cfg, message=msg)
    else:
        print(f"unknown command {cmd!r}\n{__doc__}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
