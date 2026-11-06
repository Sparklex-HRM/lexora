"""Org RBAC 2026-10-26: field-level data classes for custom roles (shared/utils/roles.holds_data / request_role).
Run:  cd backend && python -m pytest shared/test_roles_data_classes_20261026.py -q
"""
from types import SimpleNamespace

from shared.utils import roles as R


def req(**h):
    return SimpleNamespace(headers={k.replace("_", "-"): v for k, v in h.items()})


def test_system_role_holds_every_class_unchanged():
    r = req(x_role="hr_admin")
    assert all(R.holds_data(r, c) for c in R.DATA_CLASSES)
    role = R.request_role(r)
    assert role == "hr_admin" and role.held is None and R.role_holds(role, "pay")
    assert R.role_holds("payroll_admin", "pay")                     # a plain string: role checks decide


def test_acting_custom_role_holds_only_its_classes():
    r = req(x_role="payroll_admin", x_acting_role="pay_viewer", x_acting_data="identity,personal")
    assert not R.holds_data(r, "pay") and R.holds_data(r, "identity") and R.holds_data(r, "personal")
    role = R.request_role(r)
    assert role == "payroll_admin" and {role: 1}["payroll_admin"] == 1       # compares / hashes like the role
    assert not R.role_holds(role, "pay") and R.role_holds(role, "identity")
    assert R.request_role(r, "payroll").held == frozenset({"identity", "personal"})


def test_fail_closed_without_the_header():
    r = req(x_role="hr_admin", x_acting_role="desk")
    assert not any(R.holds_data(r, c) for c in R.DATA_CLASSES)
    r2 = req(x_role="hr_admin", x_acting_role="desk", x_acting_data="-")
    assert not R.holds_data(r2, "pay")
