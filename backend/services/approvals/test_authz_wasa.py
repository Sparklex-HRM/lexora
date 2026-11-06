"""WASA 2026-10-21 — the report builder is not the accountant's (finance reader only): the "finance" role family
would otherwise fold them onto a whole-company, pay-visible builder. Allowed roles unchanged."""
import pytest
from fastapi import HTTPException
from starlette.requests import Request

import report_builder_routers as RB


def _req(role: str) -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "query_string": b"",
                    "headers": [(b"x-role", role.encode()), (b"x-org-id", b"o"), (b"x-employee-id", b"e1")]})


@pytest.mark.parametrize("role", ["accountant", "employee"])
def test_builder_refuses_accountant_and_employee(role):
    with pytest.raises(HTTPException) as ex:
        RB._who(_req(role))
    assert ex.value.status_code == 403


@pytest.mark.parametrize("role,key", [("hr_admin", "hr"), ("payroll_admin", "payroll"), ("manager", "manager"),
                                      ("org_admin", "org_admin")])
def test_builder_allowed_roles_unchanged(role, key):
    assert RB._who(_req(role))["role"] == key


def test_reports_pay_not_for_accountant():
    """/reports PAY datasets (payroll cost, per-person salary revisions) — pay office only, not the accountant."""
    import reports_routers as RR
    assert RR.can_see_pay("accountant") is False
    assert not RR.may_open(RR.PAY, "accountant")
    for role in ("payroll_admin", "org_admin", "superadmin"):
        assert RR.can_see_pay(role) is True
    assert RR.can_see_pay("hr_admin") is False and RR.can_see_pay("manager") is False
