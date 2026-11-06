"""Org RBAC 2026-10-26: reports / report builder show money only when an acting custom role carries data.pay.
Run:  cd backend/services/approvals && PYTHONPATH=../../ python -m pytest -q test_rbac_data_20261026.py
"""
from types import SimpleNamespace

import report_builder as rb
import report_builder_routers as RBR
import reports_routers as RR


def req(role, **h):
    headers = {"x-role": role, "x-org-id": "o", "x-user-id": "u", **{k.replace("_", "-"): v for k, v in h.items()}}
    return SimpleNamespace(headers=headers)


def test_system_roles_unchanged():
    for role in ("org_admin", "payroll_admin"):
        assert RR.can_see_pay(RR._role(req(role)))
        assert rb.can_see_pay(RBR._who(req(role))["role"])
    assert not RR.can_see_pay(RR._role(req("hr_admin")))
    assert not RR.can_see_pay(RR._role(req("accountant")))


def test_custom_role_acting_as_payroll_needs_data_pay():
    no_pay = req("payroll_admin", x_acting_role="pay_desk", x_acting_data="personal")
    assert not RR.can_see_pay(RR._role(no_pay))
    assert RR.may_open(RR.OFFICE, RR._role(no_pay))                        # still opens office reports
    w = RBR._who(no_pay)
    assert w["role"] == "payroll" and not rb.can_see_pay(w["role"]) and rb.is_office(w["role"])
    with_pay = req("payroll_admin", x_acting_role="pay_desk", x_acting_data="pay,personal")
    assert RR.can_see_pay(RR._role(with_pay)) and rb.can_see_pay(RBR._who(with_pay)["role"])
