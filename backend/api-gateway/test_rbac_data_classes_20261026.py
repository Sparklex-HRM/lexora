"""Org RBAC 2026-10-26: an acting custom role's data classes reach the services (X-Acting-Data).

Run:  cd backend/api-gateway && PYTHONPATH=.. python -m pytest test_rbac_data_classes_20261026.py -q
"""
from types import SimpleNamespace

import rbac_gate as G
from shared.middleware.tenant import SPOOFABLE

PEOPLE_DESK = {"key": "people_desk", "name": "People desk", "base_role": "hr_manager", "reach": "org",
               "permissions": ["people.view", "data.personal"], "status": "active"}
PEOPLE_PAY = {**PEOPLE_DESK, "key": "people_pay", "permissions": ["people.view", "data.personal", "data.pay"]}


def st(*customs):
    return {"custom_roles": list(customs), "scope_values": {}}


def _req():
    return SimpleNamespace(state=SimpleNamespace(role="employee"), headers={})


def test_acting_custom_role_carries_its_data_classes():
    d = G.decide("GET", "/employees", "employee", st(PEOPLE_DESK), mode="all")
    assert not d.deny and d.acting_role == "hr_manager" and d.data_classes == ("personal",)
    r = _req()
    G.apply(r, d)
    assert r.state.role == "hr_manager" and r.state.acting_role == "people_desk" and r.state.acting_data == "personal"
    d2 = G.decide("GET", "/employees", "employee", st(PEOPLE_PAY), mode="all")
    r2 = _req()
    G.apply(r2, d2)
    assert r2.state.acting_data == "pay,personal"


def test_no_data_class_is_stamped_as_dash_and_system_role_stamps_nothing():
    leave = {"key": "leave_desk", "name": "Leave desk", "base_role": "hr_manager", "reach": "org",
             "permissions": ["leave.view"], "status": "active"}
    d = G.decide("GET", "/leave/requests", "employee", st(leave), mode="all")
    r = _req()
    G.apply(r, d)
    assert r.state.acting_data == "-"
    plain = G.decide("GET", "/employees", "hr_admin", st(), mode="all")
    r3 = _req()
    G.apply(r3, plain)
    assert not hasattr(r3.state, "acting_data")


def test_clients_cannot_send_the_header():
    assert "x-acting-data" in SPOOFABLE
