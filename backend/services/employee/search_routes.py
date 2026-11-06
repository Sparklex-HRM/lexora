"""Global search — the employee service's records (2026-10-20, spec project_jethro_global_search).

    GET /employees/search?q=&types=&limit=

Types answered here, each with the SAME visibility rule as its own list endpoint:

    employee      every role: the people-directory card (work facts only — directory_routes.py).
                  Full-record scope (access.py: HR/payroll/admin = all, manager = self + reports,
                  employee = self) additionally matches the phone's last digits; nobody gets
                  salary / ids / personal data back from search.
    candidate     HR only (hiring_routers.list_candidates)              module talent
    requisition   HR = all; others = raised by them / hiring manager    module talent
    offer         HR only (hiring_offers.list_offers)                   module talent
    letter        HR + payroll = all issued letters; others = own issued + visible letters
    exit          HR + payroll = org; manager = direct reports + own; employee = own
    asset         HR + payroll = register; manager = team + own; employee = own
    request       ESS requests: HR = all; manager = own + direct reports; employee = own
    policy        published policies for everyone; HR also drafts

Mounted BEFORE /employees/{employee_id} (main.py) so "search" is never read as an id.
"""
from __future__ import annotations

import re
import sys
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils import search as S  # noqa: E402

import access  # noqa: E402
from database import get_db  # noqa: E402
from models import Employee  # noqa: E402

router = APIRouter(prefix="/employees", tags=["search"])

TYPES = ("employee", "candidate", "requisition", "offer", "letter", "exit", "asset", "request", "policy")
TALENT_TYPES = {"candidate", "requisition", "offer"}

HR_HIRING = {"superadmin", "org_admin", "hr_admin", "hr_manager"}                       # hiring_logic.HR_ROLES
HR_LETTERS = {"superadmin", "org_admin", "hr_admin", "hr_manager", "payroll_admin"}     # career_common.HR_ROLES
HR_EXIT = {"superadmin", "org_admin", "hr_admin", "hr_manager", "hr", "payroll_admin", "payroll"}   # exit hr_like
HR_ESS = {"superadmin", "org_admin", "hr_admin", "hr_manager"}                          # ess_requests_logic.HR_ROLES

_PG_TRGM: dict[str, bool] = {}


def _org(request: Request) -> uuid.UUID:
    try:
        return uuid.UUID(request.headers.get("x-org-id") or "")
    except ValueError:
        raise HTTPException(401, "Missing organization context")


def _uuid(v) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(v or ""))
    except ValueError:
        return None


def _name(first, last, preferred=None) -> str:
    return f"{(preferred or first or '').strip()} {(last or '').strip()}".strip()


async def _trgm(db: AsyncSession) -> bool:
    """pg_trgm present? (migration 0010 creates it). SQLite → False."""
    if db.bind.dialect.name != "postgresql":
        return False
    if "ok" not in _PG_TRGM:
        try:
            r = (await db.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'"))).first()
            _PG_TRGM["ok"] = bool(r)
        except Exception:  # noqa: BLE001
            _PG_TRGM["ok"] = False
    return _PG_TRGM["ok"]


# ── employees ────────────────────────────────────────────────────────────────

# Roles the frontend's /employees/:id profile route admits (App.tsx). An accountant reads every row
# server-side (identity.ORG_WIDE_ROLES) but has no profile screen — they get the directory card.
PROFILE_ROLES = {"superadmin", "org_admin", "hr_admin", "hr_manager", "manager", "payroll_admin", "employee"}


def employee_url(e: Employee, in_scope: bool, role: str = "") -> str:
    """Own scope (and a role with the profile screen) → the profile; else the directory, highlighted on the employee no."""
    return f"/employees/{e.id}" if in_scope and role in PROFILE_ROLES else f"/employees?hl={e.employee_number or ''}"


async def _employees(db: AsyncSession, request: Request, org: uuid.UUID, q: str, toks: list[str], limit: int) -> list[dict]:
    v = await access.viewer(request, db)
    me = str(v.get("self_id") or "")
    base = select(Employee).where(Employee.org_id == org, Employee.is_active.is_(True))
    if v["scope"] != access.ALL:
        base = base.where(Employee.status != "terminated")          # the directory never lists leavers
    branch = _uuid(request.headers.get("x-branch-id"))
    if branch:
        base = base.where(Employee.branch_id == branch)
    cols = [Employee.first_name, Employee.last_name, Employee.preferred_name, Employee.employee_number,
            Employee.work_email, Employee.department, Employee.job_title, Employee.manager_name,
            Employee.work_location, Employee.branch_name]
    rows = list((await db.execute(base.where(S.ilike_any(cols, toks)).limit(200))).scalars().all())
    seen = {r.id for r in rows}
    phone_hit: set = set()
    digits = re.sub(r"\D", "", q)
    if len(digits) >= 4 and digits == q.replace(" ", ""):
        # phone last-4 — ONLY inside the viewer's full-record scope (same as GET /employees)
        pq = access.restrict(base.where(Employee.phone.like(f"%{digits[-10:]}")), v)
        for e in (await db.execute(pq.limit(50))).scalars().all():
            phone_hit.add(e.id)
            if e.id not in seen:
                rows.append(e)
                seen.add(e.id)
    if not rows and len(q) >= 4 and await _trgm(db):
        # typo tolerance on names (pg_trgm word_similarity, GIN index from migration 0010)
        full = func.lower(func.coalesce(Employee.first_name, "") + " " + func.coalesce(Employee.last_name, ""))
        tq = base.where(func.word_similarity(q.lower(), full) > 0.45).order_by(
            func.word_similarity(q.lower(), full).desc()).limit(limit)
        rows = list((await db.execute(tq)).scalars().all())
    out = []
    for e in rows:
        in_scope = access.can_see(v, e)
        name = _name(e.first_name, e.last_name, e.preferred_name)
        sc = S.score(q, name, e.employee_number, e.first_name, e.last_name, e.work_email, e.department,
                     e.job_title, e.manager_name, e.work_location)
        if e.id in phone_hit:
            sc = max(sc, 80.0)
        if not sc:
            sc = 30.0                                                # trigram found it — keep it, low
        if (e.employee_number or "").lower() == q.lower():
            sc = 100.0
        sub = S.subtitle(e.employee_number, e.department, e.job_title, e.work_location,
                         f"phone ••{(e.phone or '')[-4:]}" if e.id in phone_hit else None)
        badge = "you" if me and str(e.id) == me else ("left" if e.status == "terminated" else None)
        out.append(S.hit("employee", e.id, name, sub, employee_url(e, in_scope, v["role"]), sc, badge))
    return out


# ── hiring ───────────────────────────────────────────────────────────────────

async def _candidates(db, org, q, toks, role) -> list[dict]:
    if role not in HR_HIRING:
        return []
    from hiring_models import HireCandidate as C
    rows = (await db.execute(select(C).where(
        C.org_id == org, C.is_active.is_(True), C.merged_into.is_(None),
        S.ilike_any([C.first_name, C.last_name, C.email, C.phone, C.current_title, C.current_employer, C.city], toks)
    ).limit(100))).scalars().all()
    return [S.hit("candidate", c.id, _name(c.first_name, c.last_name),
                  S.subtitle(c.current_title, c.current_employer, c.city, c.email),
                  f"/hiring/candidates?hl={c.email or _name(c.first_name, c.last_name)}",
                  S.score(q, _name(c.first_name, c.last_name), c.email, c.current_title, c.current_employer))
            for c in rows]


async def _requisitions(db, org, q, toks, role, me, user_id) -> list[dict]:
    from hiring_models import HireRequisition as R
    qy = select(R).where(R.org_id == org, S.ilike_any(
        [R.req_number, R.designation, R.department, R.location, R.hiring_manager_name, R.status], toks))
    if role not in HR_HIRING:
        conds = []
        meu = _uuid(me)
        if meu:
            conds.append(R.hiring_manager_id == meu)
        if user_id:
            conds.append(R.created_by == user_id)
        if not conds:
            return []
        qy = qy.where(or_(*conds))
    rows = (await db.execute(qy.limit(100))).scalars().all()
    return [S.hit("requisition", r.id, f"{r.req_number or ''} {r.designation or ''}".strip(),
                  S.subtitle(r.department, r.location, f"{r.openings} opening(s)" if r.openings else None,
                             r.hiring_manager_name),
                  f"/hiring/requisitions?hl={r.req_number or r.designation or ''}",
                  S.score(q, r.req_number, r.designation, r.department, r.location, r.hiring_manager_name),
                  r.status)
            for r in rows]


async def _offers(db, org, q, toks, role) -> list[dict]:
    if role not in HR_HIRING:
        return []
    from hiring_models import HireCandidate as C, HireOffer as O
    rows = (await db.execute(select(O, C).join(C, C.id == O.candidate_id).where(
        O.org_id == org, S.ilike_any([O.designation, O.department, O.location, C.first_name, C.last_name, C.email], toks)
    ).limit(100))).all()
    return [S.hit("offer", o.id, f"Offer — {_name(c.first_name, c.last_name)}",
                  S.subtitle(o.designation, o.department, o.joining_date.isoformat() if o.joining_date else None),
                  f"/hiring/offers?hl={_name(c.first_name, c.last_name)}",
                  S.score(q, _name(c.first_name, c.last_name), o.designation, o.department), o.status)
            for o, c in rows]


# ── letters / exits / assets / requests / policies ───────────────────────────

async def _letters(db, org, q, toks, role, me) -> list[dict]:
    from letters_models import IssuedLetter as L
    qy = select(L, Employee).outerjoin(Employee, Employee.id == L.employee_id).where(
        L.org_id == org, S.ilike_any([L.number, L.template_name, L.recipient_name, Employee.first_name,
                                      Employee.last_name, Employee.employee_number], toks))
    hr = role in HR_LETTERS
    if not hr:
        meu = _uuid(me)
        if not meu:
            return []
        qy = qy.where(L.employee_id == meu, L.status == "issued", L.visible_to_employee.is_(True))
    rows = (await db.execute(qy.order_by(L.created_at.desc()).limit(100))).all()
    out = []
    for l, e in rows:
        who = _name(e.first_name, e.last_name) if e else (l.recipient_name or "")
        out.append(S.hit("letter", l.id, f"{l.template_name or 'Letter'} — {who}".strip(" —"),
                         S.subtitle(l.number, l.letter_date.isoformat() if l.letter_date else None,
                                    e.employee_number if e else None),
                         f"/letters?hl={l.number or who}" if hr else f"/my-letters?hl={l.number or ''}",
                         S.score(q, l.number, l.template_name, who, e.employee_number if e else None), l.status))
    return out


async def _exits(db, org, q, toks, role, me) -> list[dict]:
    from exit_models import ExitCase as X
    qy = select(X).where(X.org_id == org, S.ilike_any(
        [X.case_number, X.employee_name, X.employee_number, X.department, X.job_title, X.exit_type, X.status], toks))
    if role not in HR_EXIT:
        meu = _uuid(me)
        if not meu:
            return []
        qy = qy.where(or_(X.employee_id == meu, X.manager_id == meu) if role == "manager" else X.employee_id == meu)
    rows = (await db.execute(qy.order_by(X.created_at.desc()).limit(100))).scalars().all()
    return [S.hit("exit", x.id, f"Exit {x.case_number or ''} — {x.employee_name or ''}".strip(),
                  S.subtitle(x.employee_number, x.department, x.exit_type,
                             f"LWD {x.lwd.isoformat()}" if x.lwd else None),
                  f"/exits/{x.id}", S.score(q, x.case_number, x.employee_name, x.employee_number, x.department),
                  x.status)
            for x in rows]


async def _assets(db, org, q, toks, role, me) -> list[dict]:
    """item / tag / holder name match (model columns). category · serial · status live in columns
    added by assets_routers' boot DDL — read best-effort for the subtitle (Postgres only)."""
    from exit_models import EmployeeAsset as A
    qy = select(A, Employee).outerjoin(Employee, Employee.id == A.employee_id).where(
        A.org_id == org, S.ilike_any([A.item, A.tag, Employee.first_name, Employee.last_name], toks))
    meu = _uuid(me)
    office = role in HR_HIRING or role == "payroll_admin"
    if not office:
        if not meu:
            return []
        if role == "manager":
            qy = qy.where(or_(A.employee_id == meu, Employee.manager_id == meu))
        else:
            qy = qy.where(A.employee_id == meu)
    rows = (await db.execute(qy.limit(100))).all()
    extras: dict = {}
    if rows and db.bind.dialect.name == "postgresql":
        try:
            from assets_routers import _extras
            extras = await _extras(db, [a.id for a, _e in rows])
        except Exception:  # noqa: BLE001 — subtitle only
            extras = {}
    out = []
    for a, e in rows:
        x = extras.get(str(a.id), {})
        mine = bool(meu) and a.employee_id == meu
        who = _name(e.first_name, e.last_name) if e else None
        url = f"/engage/my-assets?hl={a.tag or a.item}" if (mine and not office and role != "manager")             else f"/engage/assets?hl={a.tag or a.item}"
        out.append(S.hit("asset", a.id, f"{a.item or 'Asset'}{f' ({a.tag})' if a.tag else ''}",
                         S.subtitle(x.get("category"), x.get("serial"), who, x.get("location")),
                         url, S.score(q, a.tag, a.item, who), x.get("status") or ("returned" if a.returned_on else None)))
    return out


async def _requests(db, org, q, toks, role, me) -> list[dict]:
    from ess_requests_models import EssRequest as R
    qy = select(R).where(R.org_id == org, S.ilike_any(
        [R.type_name, R.category, R.employee_name, R.employee_number, R.department, R.status], toks))
    meu = _uuid(me)
    if role not in HR_ESS:
        if not meu:
            return []
        qy = qy.where(or_(R.employee_id == meu, R.manager_id == meu) if role == "manager" else R.employee_id == meu)
    rows = (await db.execute(qy.order_by(R.created_at.desc()).limit(100))).scalars().all()
    out = []
    for r in rows:
        mine = bool(meu) and r.employee_id == meu
        out.append(S.hit("request", r.id, f"{r.type_name or 'Request'} — {r.employee_name or ''}".strip(" —"),
                         S.subtitle(r.employee_number, r.category, r.created_at.date().isoformat() if r.created_at else None),
                         "/my-requests" if mine else "/approvals",
                         S.score(q, r.type_name, r.employee_name, r.employee_number, r.category), r.status))
    return out


async def _policies(db, org, q, toks, role) -> list[dict]:
    from ess_docs_models import EmpPolicyDoc as P
    qy = select(P).where(P.org_id == org, S.ilike_any([P.title, P.category, P.version_label], toks))
    if role not in HR_ESS:
        qy = qy.where(P.status == "published")
    else:
        qy = qy.where(P.status != "archived")
    rows = (await db.execute(qy.limit(50))).scalars().all()
    return [S.hit("policy", p.id, p.title, S.subtitle(p.category, p.version_label,
                                                       p.effective_date.isoformat() if p.effective_date else None),
                  f"/documents?tab=policies&hl={p.title}", S.score(q, p.title, p.category), p.status)
            for p in rows]


@router.get("/search")
async def search(request: Request, q: str = "", types: str | None = None, limit: int | None = None,
                 db: AsyncSession = Depends(get_db)):
    org = _org(request)
    role = (request.headers.get("x-role") or "").lower()
    if not role:
        raise HTTPException(401, "Sign in again — no role on this request")
    qn = S.normalise(q)
    toks = S.tokens(qn)
    lim = S.clamp_limit(limit)
    if not toks or role == "superadmin":
        return S.response(qn, [], lim, "employee")
    want = S.parse_types(types, TYPES)
    if not S.has_module(request.headers, "talent"):
        want -= TALENT_TYPES
    me = request.headers.get("x-employee-id") or ""
    if not me and role not in access.ORG_WIDE:
        me = str(await access.self_employee_id(request, db) or "")
    user_id = request.headers.get("x-user-id") or ""
    hits: list[dict] = []
    if "employee" in want:
        hits += await _employees(db, request, org, qn, toks, lim)
    if "candidate" in want:
        hits += await _candidates(db, org, qn, toks, role)
    if "requisition" in want:
        hits += await _requisitions(db, org, qn, toks, role, me, user_id)
    if "offer" in want:
        hits += await _offers(db, org, qn, toks, role)
    if "letter" in want:
        hits += await _letters(db, org, qn, toks, role, me)
    if "exit" in want:
        hits += await _exits(db, org, qn, toks, role, me)
    if "asset" in want:
        hits += await _assets(db, org, qn, toks, role, me)
    if "request" in want:
        hits += await _requests(db, org, qn, toks, role, me)
    if "policy" in want:
        hits += await _policies(db, org, qn, toks, role)
    return S.response(qn, hits, lim, "employee")
