"""Global search — payroll records (2026-10-20).

    GET /payroll/search?q=&types=&limit=

payslip     employees / managers / accountants: their OWN payslips only (routers._own_pay_only —
            the same rule as GET /payroll/payslips; a manager never sees a report's payslip).
            Pay office (hr / payroll / admin): anyone's, by name or employee no. — only when the
            query names a person (a bare "payslip" lists nothing org-wide).
run         pay runs — PAYROLL_ACCESS roles (the Run Payroll menu).

Amounts are NEVER put in a hit (salary stays behind "Show salary" on the screens).
"""
from __future__ import annotations

import re
import sys
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import Column, MetaData, String, Table, Uuid, select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils import search as S  # noqa: E402

from database import get_db  # noqa: E402
from models import PayRun, PayStub  # noqa: E402

router = APIRouter(prefix="/payroll", tags=["search"])

TYPES = ("payslip", "run")
PAY_OFFICE = {"superadmin", "org_admin", "hr_admin", "hr_manager", "payroll_admin"}          # routers._PAY_OFFICE_ROLES
PAYROLL_ACCESS = {"superadmin", "org_admin", "hr_admin", "payroll_admin"}                    # frontend PAYROLL_ACCESS

# A read-only view of the employee service's table (same database).
# Own MetaData: never created by this service's create_all.
_emp = Table("employees", MetaData(), Column("id", Uuid), Column("org_id", Uuid), Column("first_name", String),
             Column("last_name", String), Column("employee_number", String), Column("manager_id", Uuid))

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
MONTH_NAME = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
PAYSLIP_WORDS = {"payslip", "payslips", "pay", "slip", "slips", "salary", "salaryslip", "wage", "pay-slip", "payroll"}
RUN_WORDS = {"run", "runs", "payroll", "pay", "cycle"}


def parse_query(q: str) -> dict:
    """Split the query into {months: {(y|None, m)}, words: set, person: [tokens]} (pure)."""
    toks = S.tokens(q)
    months: set = set()
    year = None
    person, words = [], set()
    for t in toks:
        m = re.fullmatch(r"(\d{4})-(\d{1,2})", t)
        if m and 1 <= int(m.group(2)) <= 12:
            months.add((int(m.group(1)), int(m.group(2))))
            continue
        if re.fullmatch(r"20\d{2}", t):
            year = int(t)
            continue
        if t[:3] in MONTHS and (len(t) == 3 or t in ("june", "july", "sept") or
                                t == ["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"][MONTHS[t[:3]] - 1]):
            months.add((None, MONTHS[t[:3]]))
            continue
        if t in PAYSLIP_WORDS | RUN_WORDS | {"of", "for", "my", "the"}:
            words.add(t)
            continue
        person.append(t)
    if year:
        months = {(year, m) for (_, m) in months} or {(year, None)}
    return {"months": months, "words": words, "person": person}


def month_ok(months: set, d: date | None) -> bool:
    if not months:
        return True
    if d is None:
        return False
    return any((y is None or y == d.year) and (m is None or m == d.month) for y, m in months)


def _d(v) -> date | None:
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _label(d: date | None) -> str:
    return f"{MONTH_NAME[d.month]} {d.year}" if d else ""


def _uuid(v):
    try:
        return uuid.UUID(str(v or ""))
    except ValueError:
        return None


async def _payslips(db, org, request, q, pq, role) -> list[dict]:
    from routers import _own_pay_only
    own = await _own_pay_only(request)
    st = PayStub
    name_col = st.detail[("employee", "name")].as_string()
    num_col = st.detail[("employee", "employee_number")].as_string()
    qy = select(st).where(st.org_id == org)
    if own is not None:
        ou = _uuid(own)
        if not ou:
            return []
        qy = qy.where(st.employee_id == ou)
        asked = bool(pq["words"] & PAYSLIP_WORDS) or bool(pq["months"])
        rows = (await db.execute(qy)).scalars().all()
        if pq["person"]:
            # a name in the query must be the caller's own — never a colleague's
            rows = [s for s in rows if all(t in f"{(s.detail.get('employee') or {}).get('name', '')} "
                                           f"{(s.detail.get('employee') or {}).get('employee_number', '')}".lower()
                                           for t in pq["person"])]
            asked = asked or bool(rows)
        if not asked:
            return []
    else:
        if not pq["person"]:
            return []
        qy = qy.where(S.ilike_any([name_col, num_col], pq["person"]))
        rows = (await db.execute(qy.limit(400))).scalars().all()
    out = []
    for s in rows:
        per = s.detail.get("period") or {}
        end = _d(per.get("end") or per.get("pay_date"))
        if not month_ok(pq["months"], end):
            continue
        emp = s.detail.get("employee") or {}
        mine = own is not None
        title = f"Payslip — {_label(end)}" + ("" if mine else f" · {emp.get('name') or ''}")
        url = f"/my-pay?hl={_label(end)}" if mine else f"/employees/{s.employee_id}?tab=pay"
        sc = 80.0 if mine else S.score(" ".join(pq["person"]), emp.get("name"), emp.get("employee_number"))
        if pq["months"]:
            sc += 10
        # recent first among equal scores: nudge by period
        sc += (end.toordinal() % 10000) / 100000 if end else 0
        out.append(S.hit("payslip", s.id, title, S.subtitle(emp.get("employee_number"), emp.get("department"),
                                                            f"pay date {per.get('pay_date')}" if per.get("pay_date") else None),
                         url, sc))
    return out


async def _runs(db, org, q, pq, role) -> list[dict]:
    if role not in PAYROLL_ACCESS:
        return []
    if not (pq["months"] or pq["words"] & RUN_WORDS) or pq["person"] and not pq["months"]:
        return []
    rows = (await db.execute(select(PayRun).where(PayRun.org_id == org).order_by(PayRun.pay_date.desc()).limit(120))).scalars().all()
    out = []
    for r in rows:
        if not month_ok(pq["months"], r.period_end):
            continue
        void = bool(((r.meta or {}).get("void") or {}))
        out.append(S.hit("run", r.id, f"Payroll run — {_label(r.period_end)}",
                         S.subtitle(f"{r.period_start.isoformat()} → {r.period_end.isoformat()}",
                                    f"pay date {r.pay_date.isoformat()}" if r.pay_date else None),
                         f"/payroll?hl={r.period_end.isoformat()}", (70.0 if pq["months"] else 55.0)
                         + (r.period_end.toordinal() % 10000) / 100000, "void" if void else r.status))
    return out[:12]


@router.get("/search")
async def search(request: Request, q: str = "", types: str | None = None, limit: int | None = None,
                 db: AsyncSession = Depends(get_db)):
    org = _uuid(request.headers.get("x-org-id"))
    if not org:
        raise HTTPException(401, "Missing organization context")
    role = (request.headers.get("x-role") or "").lower()
    qn, toks, lim = S.normalise(q), S.tokens(q), S.clamp_limit(limit)
    if not toks or not role or role == "superadmin":
        return S.response(qn, [], lim, "payroll")
    want = S.parse_types(types, TYPES)
    pq = parse_query(qn)
    hits: list[dict] = []
    if "payslip" in want:
        hits += await _payslips(db, org, request, qn, pq, role)
    if "run" in want:
        hits += await _runs(db, org, qn, pq, role)
    return S.response(qn, hits, lim, "payroll")
