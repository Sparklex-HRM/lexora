"""Global search — Engage (/engage/search): tickets HR = all, payroll agent = non-confidential + own,
everyone else = own; courses HR = all, others = assigned; ops policies = OPS roles + ops module.
Run:  cd backend/services/ops && python -m pytest -q test_search.py
"""
import asyncio
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles


@compiles(JSONB, "sqlite")
def _j(element, compiler, **kw):
    return "JSON"


import search_routes as SR  # noqa: E402
from database import get_db  # noqa: E402
from engage_models import Course, CourseAssignment, Ticket  # noqa: E402
from models import Policy  # noqa: E402

ORG = uuid.uuid4()
PRIYA, RAVI = uuid.uuid4(), uuid.uuid4()
engine = create_async_engine("sqlite+aiosqlite:///:memory:")
Session = async_sessionmaker(engine, expire_on_commit=False)


async def _db():
    async with Session() as s:
        yield s


async def _seed():
    async with engine.begin() as c:
        for t in (Ticket.__table__, Course.__table__, CourseAssignment.__table__, Policy.__table__):
            await c.run_sync(lambda conn, t=t: t.create(conn))
    async with Session() as s:
        s.add(Ticket(org_id=ORG, number="HD-0001", employee_id=PRIYA, employee_name="Priya Menon", category="payroll",
                     subject="Payslip query for September", status="open"))
        s.add(Ticket(org_id=ORG, number="HD-0002", employee_id=RAVI, employee_name="Ravi Kumar", category="grievance",
                     subject="Grievance about payslip deductions", status="open", confidential=True))
        c1 = Course(org_id=ORG, title="Harassment awareness", category="compliance", active=True, mandatory=True)
        c2 = Course(org_id=ORG, title="Excel basics", category="skills", active=True)
        s.add_all([c1, c2])
        await s.flush()
        s.add(CourseAssignment(org_id=ORG, course_id=c1.id, employee_id=PRIYA, employee_name="Priya Menon"))
        s.add(Policy(org_id=ORG, name="Travel policy", code="POL-TRV", category="travel", is_active=True))
        await s.commit()


app = FastAPI()
app.include_router(SR.router)
app.dependency_overrides[get_db] = _db
asyncio.run(_seed())
client = TestClient(app)


def H(role, emp=None, modules=None):
    h = {"X-Org-Id": str(ORG), "X-Role": role, "X-User-Id": "u-" + role}
    if emp:
        h["X-Employee-Id"] = str(emp)
    if modules is not None:
        h["X-Modules"] = ",".join(modules)
    return h


def nums(q, h, types):
    r = client.get("/engage/search", params={"q": q, "types": types}, headers=h)
    assert r.status_code == 200, r.text
    return sorted(x["title"].split(" — ")[0] for x in r.json()["hits"])


def test_tickets_scope():
    assert nums("payslip", H("hr_admin"), "ticket") == ["HD-0001", "HD-0002"]
    assert nums("payslip", H("payroll_admin"), "ticket") == ["HD-0001"]               # confidential hidden from the agent
    assert nums("payslip", H("employee", PRIYA), "ticket") == ["HD-0001"]
    assert nums("HD-0001", H("employee", RAVI), "ticket") == []
    r = client.get("/engage/search", params={"q": "HD-0001", "types": "ticket"}, headers=H("employee", PRIYA)).json()
    assert r["hits"][0]["url"].startswith("/engage/helpdesk?id=")


def test_courses_and_policies():
    assert nums("harassment", H("employee", PRIYA), "course") == ["Harassment awareness"]
    assert nums("excel", H("employee", PRIYA), "course") == []
    assert nums("excel", H("hr_admin"), "course") == ["Excel basics"]
    assert nums("travel", H("hr_admin"), "policy") == ["Travel policy"]
    assert nums("travel", H("employee", PRIYA), "policy") == []
    assert nums("travel", H("hr_admin", modules=["core"]), "policy") == []            # no ops module
