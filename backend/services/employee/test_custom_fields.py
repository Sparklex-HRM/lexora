"""Custom fields (D1): registry validation, value rules, role visibility — pure + TestClient.

Run:  cd backend/services/employee && PYTHONPATH=../../ python -m pytest -q test_custom_fields.py
"""
import uuid
from contextlib import asynccontextmanager

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

import database
import models  # noqa: F401
import exit_models  # noqa: F401
import hiring_models  # noqa: F401
import custom_fields_logic as L
from custom_fields_routers import init_custom_fields, router
from models import Employee
from shared.utils import identity

ORG = str(uuid.uuid4())
EMP, EMP2, MGR = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())


# ══════════════════════════ pure rules ═════════════════════════════════════════

def test_definition_validation():
    d = L.validate_definition({"entity": "employee", "label": "Blood group", "type": "select", "options": "O+, A+, B+",
                               "visible_to": ["hr", "manager"], "required": True}, is_new=True)
    assert d["key"] == "blood_group" and d["options"] == ["O+", "A+", "B+"] and d["visible_to"] == ["hr", "manager"]
    for bad in ({"entity": "car", "label": "x"}, {"entity": "employee", "label": ""}, {"entity": "employee", "label": "x", "key": "Bad Key"},
                {"entity": "employee", "label": "x", "type": "select", "options": []}, {"entity": "employee", "label": "x", "type": "lookup"},
                {"entity": "employee", "label": "x", "validation": {"regex": "("}}, {"entity": "employee", "label": "x", "visible_to": ["ceo"]},
                {"entity": "employee", "label": "x", "type": "number", "validation": {"min": 5, "max": 1}}):
        with pytest.raises(L.FieldError):
            L.validate_definition(bad, is_new=True)
    assert L.slug("Laptop Budget ($)") == "laptop_budget" and L.slug("2nd address") == "f_2nd_address"


def test_value_rules_per_type():
    txt = {"key": "pf_nominee", "label": "PF nominee", "type": "text", "validation": {"regex": "[A-Z ]+", "min": 2, "help": "capitals only"}}
    assert L.coerce_value(txt, " RAVI K ") == "RAVI K"
    with pytest.raises(L.FieldError, match="pattern"):
        L.coerce_value(txt, "ravi")
    num = {"key": "n", "label": "Laptop budget", "type": "number", "validation": {"min": 0, "max": 100000}}
    assert L.coerce_value(num, "50000") == 50000 and L.coerce_value(num, "12.5") == 12.5
    with pytest.raises(L.FieldError, match="at most"):
        L.coerce_value(num, 100001)
    dt = {"key": "d", "label": "Visa expiry", "type": "date", "validation": {"min": "2026-01-01"}}
    assert L.coerce_value(dt, "2026-10-16T00:00:00") == "2026-10-16"
    with pytest.raises(L.FieldError, match="before"):
        L.coerce_value(dt, "2025-12-31")
    sel = {"key": "s", "label": "Shirt", "type": "select", "options": ["S", "M", "L"]}
    assert L.coerce_value(sel, "M") == "M"
    with pytest.raises(L.FieldError):
        L.coerce_value(sel, "XL")
    ms = {"key": "m", "label": "Skills", "type": "multiselect", "options": ["py", "sql", "go"]}
    assert L.coerce_value(ms, "py, sql, py") == ["py", "sql"]
    with pytest.raises(L.FieldError):
        L.coerce_value(ms, ["py", "rust"])
    b = {"key": "b", "label": "Remote", "type": "boolean"}
    assert L.coerce_value(b, "yes") is True and L.coerce_value(b, False) is False
    req = {"key": "r", "label": "Must", "type": "text", "required": True}
    with pytest.raises(L.FieldError, match="required"):
        L.coerce_value(req, "")
    assert L.coerce_value({"key": "o", "label": "Opt", "type": "text"}, "") is None


def test_role_visibility_and_editing():
    defs = [{"key": "pub", "label": "Public", "type": "text", "visible_to": [], "editable_by": [], "active": True},
            {"key": "hr_only", "label": "HR only", "type": "text", "visible_to": ["hr"], "editable_by": ["hr_admin"], "active": True},
            {"key": "self", "label": "Self", "type": "text", "visible_to": [], "editable_by": ["employee", "hr"], "active": True},
            {"key": "old", "label": "Old", "type": "text", "visible_to": [], "editable_by": [], "active": False}]
    assert [d["key"] for d in L.visible_defs(defs, "employee")] == ["pub", "self"]
    assert [d["key"] for d in L.visible_defs(defs, "hr_staff")] == ["pub", "hr_only", "self"]
    assert L.can_edit(defs[0], "hr_manager") and not L.can_edit(defs[0], "manager") and L.can_edit(defs[0], "superadmin")
    assert L.can_edit(defs[1], "hr_admin") and not L.can_edit(defs[1], "hr_manager")
    assert L.can_edit(defs[2], "employee")
    data, changed = L.apply_values(defs, {"hr_only": "secret", "old": "x"}, {"self": "me"}, "employee")
    data, changed2 = L.apply_values(defs, data, {"pub": "hello"}, "hr_staff")
    assert data == {"hr_only": "secret", "old": "x", "pub": "hello", "self": "me"} and changed + changed2 == ["self", "pub"]
    with pytest.raises(L.FieldError, match="cannot change"):
        L.apply_values(defs, {}, {"hr_only": "x"}, "employee")
    with pytest.raises(L.FieldError, match="cannot change"):                     # empty editable_by = org admin + HR only
        L.apply_values(defs, {}, {"pub": "x"}, "employee")
    with pytest.raises(L.FieldError, match="not a custom field"):
        L.apply_values(defs, {}, {"old": "x"}, "hr_admin")
    assert L.visible_values(defs, data, "employee") == {"pub": "hello", "self": "me"}     # hr_only and old never leak
    assert L.visible_values(defs, data, "hr_admin") == {"hr_only": "secret", "pub": "hello", "self": "me"}


# ══════════════════════════ the API ════════════════════════════════════════════

@asynccontextmanager
async def _lifespan(app):
    async with database.engine.begin() as conn:
        await conn.run_sync(database.Base.metadata.create_all)
        for t in (identity.organizations, identity.users):
            await conn.run_sync(lambda c, t=t: t.create(c, checkfirst=True))
        await conn.execute(sa.insert(identity.organizations).values(
            id=uuid.UUID(ORG), name="Maple Logistics", code="MAPLE", country="CA", province="ON", is_active=True))
        for tbl in ("employees", "hire_candidates", "employee_assets"):
            try:                                   # SQLite: no IF NOT EXISTS; the payroll table does not exist here at all
                await conn.execute(text(f"ALTER TABLE {tbl} ADD COLUMN custom_data JSON"))
            except Exception:                      # noqa: BLE001
                pass
        # one dict shape for every row: a multi-row insert takes its column list from the FIRST dict
        await conn.execute(sa.insert(Employee).values(
            [dict(id=uuid.UUID(MGR), org_id=uuid.UUID(ORG), employee_number="EMP-0001", first_name="Team", last_name="Lead", status="active", manager_id=None, is_active=True),
             dict(id=uuid.UUID(EMP), org_id=uuid.UUID(ORG), employee_number="EMP-0002", first_name="Staff", last_name="One", status="active", manager_id=uuid.UUID(MGR), is_active=True),
             dict(id=uuid.UUID(EMP2), org_id=uuid.UUID(ORG), employee_number="EMP-0003", first_name="Staff", last_name="Two", status="active", manager_id=None, is_active=True)]))
    await init_custom_fields(database.engine)
    yield


app = FastAPI(lifespan=_lifespan)
app.include_router(router)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def H(role="hr_admin", emp=None):
    h = {"X-Org-Id": ORG, "X-Role": role, "X-User-Id": str(uuid.uuid4()), "X-User-Email": f"{role}@maple.test"}
    if emp:
        h["X-Employee-Id"] = emp
    return h


def test_definitions_crud_and_roles(client):
    r = client.post("/employees/custom-fields/definitions", json={"entity": "employee", "label": "Blood group", "type": "select",
                                                                   "options": ["O+", "A+"], "required": True, "show_in_grid": True}, headers=H())
    assert r.status_code == 201, r.text
    bg = r.json()
    assert bg["key"] == "blood_group" and bg["options"] == ["O+", "A+"]
    r = client.post("/employees/custom-fields/definitions", json={"entity": "employee", "label": "Blood group", "type": "text"}, headers=H())
    assert r.status_code == 409
    r = client.post("/employees/custom-fields/definitions", json={"entity": "employee", "label": "Laptop budget", "type": "number",
                                                                   "validation": {"min": 0, "max": 100000}, "visible_to": ["hr", "payroll"],
                                                                   "editable_by": ["hr_admin"]}, headers=H())
    assert r.status_code == 201
    lb = r.json()
    r = client.post("/employees/custom-fields/definitions", json={"entity": "employee", "label": "T-shirt", "type": "select", "options": ["S", "M"],
                                                                   "editable_by": ["employee", "hr"]}, headers=H())
    assert r.status_code == 201
    assert client.post("/employees/custom-fields/definitions", json={"entity": "employee", "label": "x"}, headers=H("hr_manager")).status_code == 403
    assert client.post("/employees/custom-fields/definitions", json={"entity": "employee", "label": "x", "type": "select"}, headers=H()).status_code == 422
    # type cannot change once values may exist; key is fixed
    r = client.put(f"/employees/custom-fields/definitions/{lb['id']}", json={"label": "Laptop budget ($)", "type": "date"}, headers=H())
    assert r.status_code == 422
    r = client.put(f"/employees/custom-fields/definitions/{lb['id']}", json={"label": "Laptop budget ($)", "type": "number", "key": "other",
                                                                              "validation": {"min": 0, "max": 100000},
                                                                              "visible_to": ["hr", "payroll"], "editable_by": ["hr_admin"]}, headers=H())
    assert r.status_code == 200 and r.json()["key"] == "laptop_budget" and r.json()["label"] == "Laptop budget ($)"
    # an employee lists only what they may see; the setup role sees everything incl. inactive
    keys = [d["key"] for d in client.get("/employees/custom-fields/definitions?entity=employee", headers=H("employee", EMP)).json()["items"]]
    assert keys == ["blood_group", "t_shirt"]
    assert client.get("/employees/custom-fields/meta", headers=H("employee")).json()["can_setup"] is False


def test_values_roles_and_audit_shape(client):
    # HR head writes; validation applies
    r = client.put(f"/employees/custom-fields/values/employee/{EMP}", json={"values": {"blood_group": "O+", "laptop_budget": "60000", "t_shirt": "M"}}, headers=H())
    assert r.status_code == 200, r.text
    assert r.json()["values"] == {"blood_group": "O+", "laptop_budget": 60000, "t_shirt": "M"} and r.json()["changed"] == ["blood_group", "laptop_budget", "t_shirt"]
    r = client.put(f"/employees/custom-fields/values/employee/{EMP}", json={"values": {"laptop_budget": 999999}}, headers=H())
    assert r.status_code == 422 and "at most" in r.json()["detail"]
    r = client.put(f"/employees/custom-fields/values/employee/{EMP}", json={"values": {"blood_group": "Z"}}, headers=H())
    assert r.status_code == 422
    # the employee sees only the visible keys and may change t_shirt on THEIR record only
    r = client.get(f"/employees/custom-fields/values/employee/{EMP}", headers=H("employee", EMP))
    assert r.status_code == 200 and r.json()["values"] == {"blood_group": "O+", "t_shirt": "M"} and r.json()["can_edit"] is True
    assert client.get(f"/employees/custom-fields/values/employee/{EMP2}", headers=H("employee", EMP)).status_code == 403
    r = client.put(f"/employees/custom-fields/values/employee/{EMP}", json={"values": {"t_shirt": "S"}}, headers=H("employee", EMP))
    assert r.status_code == 200 and r.json()["values"]["t_shirt"] == "S"
    r = client.put(f"/employees/custom-fields/values/employee/{EMP}", json={"values": {"blood_group": "A+"}}, headers=H("employee", EMP))
    assert r.status_code == 422 and "cannot change" in r.json()["detail"]
    # a manager reads a direct report but not a stranger; hr_manager cannot edit the hr_admin-only field
    assert client.get(f"/employees/custom-fields/values/employee/{EMP}", headers=H("manager", MGR)).status_code == 200
    assert client.get(f"/employees/custom-fields/values/employee/{EMP2}", headers=H("manager", MGR)).status_code == 403
    r = client.put(f"/employees/custom-fields/values/employee/{EMP}", json={"values": {"laptop_budget": 1}}, headers=H("hr_manager"))
    assert r.status_code == 422
    # required reporting for the record with nothing filled
    r = client.get(f"/employees/custom-fields/values/employee/{EMP2}", headers=H("hr_manager"))
    assert r.json()["missing_required"] == ["blood_group"]
    # bulk for grids: office sees everything, a manager only direct reports (+ self)
    r = client.get(f"/employees/custom-fields/values/employee?ids={EMP},{EMP2}", headers=H("payroll_admin"))
    assert r.status_code == 200 and r.json()["values"] == {EMP: {"blood_group": "O+", "laptop_budget": 60000, "t_shirt": "S"}}
    r = client.get("/employees/custom-fields/values/employee", headers=H("manager", MGR))
    assert set(r.json()["values"]) == {EMP} and "laptop_budget" not in r.json()["values"][EMP]
    assert client.get("/employees/custom-fields/values/employee", headers=H("employee", EMP)).status_code == 403
    # deactivating keeps the stored value but hides the field
    did = next(d["id"] for d in client.get("/employees/custom-fields/definitions?entity=employee", headers=H()).json()["items"] if d["key"] == "t_shirt")
    assert client.delete(f"/employees/custom-fields/definitions/{did}", headers=H()).json()["active"] is False
    r = client.get(f"/employees/custom-fields/values/employee/{EMP}", headers=H())
    assert "t_shirt" not in r.json()["values"] and r.json()["values"]["blood_group"] == "O+"


def test_candidate_and_asset_entities(client):
    r = client.post("/employees/custom-fields/definitions", json={"entity": "candidate", "label": "Notice period (days)", "type": "number"}, headers=H())
    assert r.status_code == 201
    r = client.post("/employees/custom-fields/definitions", json={"entity": "asset", "label": "Insured till", "type": "date"}, headers=H())
    assert r.status_code == 201
    assert client.get(f"/employees/custom-fields/values/candidate/{uuid.uuid4()}", headers=H()).status_code == 404
    assert client.get(f"/employees/custom-fields/values/vehicle/{uuid.uuid4()}", headers=H()).status_code == 404
    assert client.get("/employees/custom-fields/values/candidate", headers=H("manager", MGR)).json()["values"] == {}   # candidates: office only
