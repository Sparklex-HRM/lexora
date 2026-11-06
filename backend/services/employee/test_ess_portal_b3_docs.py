"""Employee portal batch 3 — document centre: company policies + acknowledgement.
Run:  cd backend/services/employee && PYTHONPATH=../../ LEXORA_ENV=test python -m pytest -q test_ess_portal_b3_docs.py
"""
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from types import SimpleNamespace as NS

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient

import database
import ess_docs_logic as L
import ess_docs_models  # noqa: F401
import ess_docs_routers as R
import letters_models  # noqa: F401
import masters_models  # noqa: F401
import models  # noqa: F401
from routers import router as emp_router
from shared.utils import identity

ORG = str(uuid.uuid4())
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


# ── pure rules ────────────────────────────────────────────────────────────────

def _p(**over):
    base = dict(id=uuid.uuid4(), title="Code of Conduct", version_no=2, version_label="v2", status="published",
                ack_required=True, history=[{"version_label": "v1"}], effective_date=date(2026, 10, 1),
                published_at=datetime(2026, 10, 1, tzinfo=timezone.utc), published_by="hr@x", document_id=None,
                file_name=None)
    base.update(over)
    return NS(**base)


def test_validation_needs_title_label_and_content():
    assert "title" in L.validate_policy("", None, "v1", None, "text", None)
    assert "label" in L.validate_policy("CoC", None, " ", None, "text", None)
    assert "text or attach a PDF" in L.validate_policy("CoC", None, "v1", None, "  ", None)
    assert L.validate_policy("CoC", "Conduct", "v1", date(2026, 10, 1), None, str(uuid.uuid4())) is None


def test_status_and_version_labels():
    p = _p()
    assert L.my_status(p, False) == "pending" and L.my_status(p, True) == "acknowledged"
    assert L.my_status(_p(ack_required=False), False) == "not_required"
    assert L.my_status(_p(status="archived"), True) == "archived"
    assert L.label_taken(p, "V1") and L.label_taken(p, "v2") and not L.label_taken(p, "v3")
    h = L.history_entry(p)
    assert h["version_no"] == 2 and h["version_label"] == "v2" and h["effective_date"] == "2026-10-01"


def test_ack_rules():
    me = str(uuid.uuid4())
    p = _p()
    assert L.ack_check(p, self_id=None, body_employee_id=None, version_no=None)[0] == 400
    assert L.ack_check(p, self_id=me, body_employee_id=str(uuid.uuid4()), version_no=None)[0] == 403
    assert L.ack_check(p, self_id=me, body_employee_id=None, version_no=1)[0] == 409       # stale version
    assert L.ack_check(_p(ack_required=False), self_id=me, body_employee_id=None, version_no=None)[0] == 409
    assert L.ack_check(_p(status="archived"), self_id=me, body_employee_id=None, version_no=None)[0] == 404
    assert L.ack_check(p, self_id=me, body_employee_id=me, version_no=2) is None


def test_register_counts_only_current_version_and_csv_is_safe():
    a, b, c = (NS(id=uuid.uuid4(), employee_number=f"E{i}", first_name=n, last_name="X", department="=cmd", job_title=None)
               for i, n in enumerate(("Asha", "Bala", "Chitra")))
    acks = [NS(employee_id=a.id, version_no=2, version_label="v2", acknowledged_at=datetime.now(timezone.utc), user_agent="UA"),
            NS(employee_id=b.id, version_no=1, version_label="v1", acknowledged_at=datetime.now(timezone.utc), user_agent="UA")]
    reg = L.register(_p(), [a, b, c], acks)
    assert (reg["total"], reg["acknowledged"], reg["pending"], reg["percent"]) == (3, 1, 2, 33)
    assert [r["status"] for r in reg["rows"]] == ["pending", "pending", "acknowledged"]
    csv = L.register_csv(_p(), reg["rows"])
    assert "'=cmd" in csv and ",=cmd" not in csv
    assert L.counts(0, 0)["percent"] == 0


# ── endpoints on a real (SQLite) database ─────────────────────────────────────

NOTES: list = []


async def _fake_notify(org, **kw):
    NOTES.append(kw)


async def _no_audit(*a, **k):
    return True


@asynccontextmanager
async def _lifespan(app):
    async with database.engine.begin() as conn:
        await conn.run_sync(database.Base.metadata.create_all)
        for t in (identity.organizations, identity.users):
            await conn.run_sync(lambda c, t=t: t.create(c, checkfirst=True))
        await conn.execute(sa.insert(identity.organizations).values(
            id=uuid.UUID(ORG), name="Docs Co", code="DOCS" + ORG[:4], country="CA", province="ON", is_active=True))
    yield


app = FastAPI(lifespan=_lifespan)
app.include_router(R.summary_router)
app.include_router(R.router)
app.include_router(emp_router)


@pytest.fixture(scope="module")
def client():
    mp = pytest.MonkeyPatch()
    mp.setattr(R, "notify", _fake_notify)
    mp.setattr(R, "record_audit", _no_audit)
    with TestClient(app) as c:
        yield c
    mp.undo()


def H(role="hr_admin", emp=None):
    h = {"X-Org-Id": ORG, "X-Role": role, "X-User-Id": str(uuid.uuid4()), "User-Agent": "pytest-browser/1.0"}
    if emp:
        h["X-Employee-Id"] = emp
    return h


@pytest.fixture(scope="module")
def people(client):
    out = {}
    for key, fn in (("priya", "Priya"), ("lead", "Eng"), ("hr", "Hari")):
        r = client.post("/employees", headers=H(), json={"first_name": fn, "last_name": "Test", "annual_salary": 600000,
                                                         "department": "Engineering"})
        assert r.status_code == 201, r.text
        out[key] = r.json()["id"]
    return out


def _publish(client, title, ack=True, **over):
    body = {"title": title, "category": "Conduct", "version_label": "v1", "effective_date": "2026-10-01",
            "body": f"{title} — the rules.", "ack_required": ack, **over}
    return client.post("/employees/ess/policies", headers=H(), json=body)


def test_only_hr_publishes(client, people):
    for role in ("employee", "manager", "payroll_admin"):
        r = client.post("/employees/ess/policies", headers=H(role, people["priya"]),
                        json={"title": "X", "version_label": "v1", "body": "b"})
        assert r.status_code == 403, role
    r = client.post("/employees/ess/policies/files", headers=H("employee", people["priya"]),
                    files={"file": ("p.pdf", PDF, "application/pdf")})
    assert r.status_code == 403


def test_publish_ack_new_version_and_track(client, people):
    up = client.post("/employees/ess/policies/files", headers=H(), files={"file": ("coc.pdf", PDF, "application/pdf")})
    assert up.status_code == 201, up.text
    png = client.post("/employees/ess/policies/files", headers=H(),
                      files={"file": ("x.png", b"\x89PNG\r\n\x1a\n" + b"0" * 20, "image/png")})
    assert png.status_code == 422                                           # policies are PDFs only
    coc = _publish(client, "Code of Conduct", document_id=up.json()["id"])
    assert coc.status_code == 201, coc.text
    pid = coc.json()["id"]
    assert _publish(client, "code of conduct").status_code == 409            # duplicate title
    tne = _publish(client, "Travel & expense policy", ack=False)
    assert tne.status_code == 201
    assert any("Code of Conduct" in n["title"] for n in NOTES) and all(n.get("email") is False for n in NOTES)

    # the employee reads and accepts — for herself only
    priya = H("employee", people["priya"])
    assert client.get(f"/employees/ess/policies/{pid}/file", headers=priya).content.startswith(b"%PDF")
    bad = client.post(f"/employees/ess/policies/{pid}/acknowledge", headers=priya, json={"employee_id": people["lead"]})
    assert bad.status_code == 403
    ok = client.post(f"/employees/ess/policies/{pid}/acknowledge", headers=priya, json={"version_no": 1})
    assert ok.status_code == 200 and ok.json()["my_status"] == "acknowledged"
    again = client.post(f"/employees/ess/policies/{pid}/acknowledge", headers=priya, json={})
    assert again.status_code == 200                                          # idempotent
    assert client.post(f"/employees/ess/policies/{tne.json()['id']}/acknowledge", headers=priya).status_code == 409

    # the employee cannot read the register, remind, or create versions
    assert client.get(f"/employees/ess/policies/{pid}/acks", headers=priya).status_code == 403
    assert client.get(f"/employees/ess/policies/{pid}/acks.csv", headers=priya).status_code == 403
    assert client.post(f"/employees/ess/policies/{pid}/remind", headers=priya).status_code == 403
    assert client.get("/employees/ess/policies", headers=priya, params={"manage": 1}).status_code == 403
    assert client.post(f"/employees/ess/policies/{pid}/versions", headers=H("manager", people["lead"]),
                       json={"version_label": "v2", "body": "x"}).status_code == 403

    reg = client.get(f"/employees/ess/policies/{pid}/acks", headers=H()).json()
    assert (reg["total"], reg["acknowledged"], reg["pending"]) == (3, 1, 2)
    row = next(r for r in reg["rows"] if r["employee_id"] == people["priya"])
    assert row["status"] == "acknowledged" and row["user_agent"] == "pytest-browser/1.0"
    csv = client.get(f"/employees/ess/policies/{pid}/acks.csv", headers=H(), params={"status": "pending"})
    assert csv.status_code == 200 and "Eng Test" in csv.text and "Priya Test" not in csv.text

    NOTES.clear()
    rem = client.post(f"/employees/ess/policies/{pid}/remind", headers=H())
    assert rem.json()["reminded"] == 2
    assert set(NOTES[0]["employee_ids"]) == {people["lead"], people["hr"]} and NOTES[0]["email"] is False

    # a new version: everyone acknowledges again, the old label cannot be reused
    assert client.post(f"/employees/ess/policies/{pid}/versions", headers=H(),
                       json={"version_label": "v1", "body": "again"}).status_code == 409
    v2 = client.post(f"/employees/ess/policies/{pid}/versions", headers=H(),
                     json={"version_label": "v2", "effective_date": "2026-10-16", "body": "Updated rules", "keep_file": True})
    assert v2.status_code == 200 and v2.json()["version_no"] == 2 and v2.json()["has_file"]
    mine = {p["id"]: p for p in client.get("/employees/ess/policies", headers=priya).json()}
    assert mine[pid]["my_status"] == "pending" and mine[pid]["previous_versions"][0]["version_label"] == "v1"
    stale = client.post(f"/employees/ess/policies/{pid}/acknowledge", headers=priya, json={"version_no": 1})
    assert stale.status_code == 409
    reg2 = client.get(f"/employees/ess/policies/{pid}/acks", headers=H()).json()
    assert reg2["acknowledged"] == 0 and reg2["pending"] == 3

    # summary counts
    s = client.get("/employees/ess/documents/summary", headers=priya).json()
    assert s["policies"] == 2 and s["policies_to_acknowledge"] == 1 and "hr" not in s
    sh = client.get("/employees/ess/documents/summary", headers=H()).json()
    assert sh["hr"]["pending_acknowledgements"] == 3

    # archive hides it from employees; HR still sees it under manage
    assert client.post(f"/employees/ess/policies/{pid}/archive", headers=H()).json()["status"] == "archived"
    assert pid not in {p["id"] for p in client.get("/employees/ess/policies", headers=priya).json()}
    assert client.get(f"/employees/ess/policies/{pid}", headers=priya).status_code == 404
    assert client.post(f"/employees/ess/policies/{pid}/acknowledge", headers=priya).status_code == 404
    managed = {p["id"]: p for p in client.get("/employees/ess/policies", headers=H(), params={"manage": 1}).json()}
    assert managed[pid]["status"] == "archived" and managed[pid]["acks"]["pending"] == 3
    assert client.post(f"/employees/ess/policies/{pid}/restore", headers=H()).json()["status"] == "published"


def test_unknown_or_other_org_policy_is_404(client, people):
    assert client.get(f"/employees/ess/policies/{uuid.uuid4()}", headers=H("employee", people["priya"])).status_code == 404
    assert client.get("/employees/ess/policies/not-a-uuid", headers=H()).status_code == 404
    other = dict(H(), **{"X-Org-Id": str(uuid.uuid4())})
    pid = client.get("/employees/ess/policies", headers=H()).json()[0]["id"]
    assert client.get(f"/employees/ess/policies/{pid}", headers=other).status_code == 404
