"""Lexora HRM — link the old free-text "Reports to" names to real employees.

Why: manager approval queues read `employees.manager_id`, but the old forms
only saved the typed name (`manager_name`), so `manager_id` was never set.

DRY-RUN by default — it only prints. Set APPLY=1 to write.

    DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5442/lexora \
      PYTHONPATH=backend python scripts/backfill_manager_id.py
    APPLY=1 DATABASE_URL=... PYTHONPATH=backend python scripts/backfill_manager_id.py
    ORG_CODE=MAPLE ...        (optional: one organization only)

What it changes on APPLY=1: for an employee with a typed manager name and no
`manager_id`, when EXACTLY ONE other active employee of the same organization
matches that text (first + last name in either order, preferred name + last
name, employee number or e-mail), it sets `manager_id` and tidies
`manager_name` to that person's name. No match or several matches → listed
under REVIEW and left alone. Running it again finds nothing left to link.
Rules + tests: backend/shared/utils/employee_fixes.py (match_manager).
"""
import asyncio
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from shared.utils.employee_fixes import FIX, REVIEW, match_manager  # noqa: E402

APPLY = os.getenv("APPLY", "").strip().lower() in ("1", "true", "yes")
ORG_CODE = os.getenv("ORG_CODE", "").strip().upper()

SQL = """
SELECT e.id, e.org_id, e.employee_number, e.first_name, e.last_name, e.middle_name, e.preferred_name,
       e.email, e.work_email, e.manager_id, e.manager_name, e.status, o.code AS org_code
FROM employees e LEFT JOIN organizations o ON o.id = e.org_id
WHERE e.is_active
ORDER BY o.code, e.employee_number
"""


async def main() -> int:
    url = os.getenv("DATABASE_URL")
    if not url:
        print("Set DATABASE_URL (e.g. postgresql+asyncpg://postgres:postgres@localhost:5442/lexora)")
        return 2
    engine = create_async_engine(url)
    fixes, reviews, todo = [], [], 0
    async with engine.begin() as conn:
        rows = [dict(r) for r in (await conn.execute(text(SQL))).mappings().all()]
        by_org = defaultdict(list)
        for r in rows:
            if r["status"] != "terminated":
                by_org[r["org_id"]].append(r)
        for r in rows:
            if ORG_CODE and (r["org_code"] or "").upper() != ORG_CODE:
                continue
            if r["manager_id"] or not (r["manager_name"] or "").strip():
                continue
            todo += 1
            d = match_manager(r["manager_name"], r["id"], by_org[r["org_id"]])
            who = f"{r['org_code'] or '?':12} {r['employee_number'] or '-':12} {r['first_name']} {r['last_name']}"
            if d["action"] == FIX:
                fixes.append((r["id"], d["manager_id"], d["manager_name"],
                              f"  {who:54.54} '{r['manager_name']}' -> {d['manager_name']}"))
            elif d["action"] == REVIEW:
                reviews.append(f"  {who:54.54} '{r['manager_name']}': {d['reason']}")

        print(f"{todo} employee(s) have a typed manager name and no linked manager"
              f"{' in ' + ORG_CODE if ORG_CODE else ''}.")
        print(f"\nFIX — {len(fixes)} row(s) {'being linked' if APPLY else 'would be linked'}:")
        for *_, line in fixes:
            print(line)
        print(f"\nREVIEW — {len(reviews)} row(s) need a person (pick the manager on the employee form):")
        for line in reviews:
            print(line)
        if APPLY and fixes:
            for emp_id, mid, mname, _ in fixes:
                await conn.execute(text(
                    "UPDATE employees SET manager_id = CAST(:m AS uuid), manager_name = :n "
                    "WHERE id = :i AND manager_id IS NULL"), {"m": mid, "n": mname, "i": emp_id})
            print(f"\nAPPLIED: manager linked on {len(fixes)} row(s).")
        elif fixes:
            print("\nDRY-RUN: nothing was written. Run again with APPLY=1 to apply the FIX rows.")
        else:
            print("\nNothing to link.")
    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
