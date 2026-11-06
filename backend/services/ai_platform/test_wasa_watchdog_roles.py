"""WASA 2026-10-21: compliance-health (watchdog) findings carry per-person pay variance — the accountant
(payroll exports only) is refused; admins, HR and payroll keep access."""
import pytest
from fastapi import HTTPException

import wd_routers


class _Req:
    def __init__(self, role):
        self.headers = {"x-role": role, "x-org-id": "o1"}


@pytest.mark.parametrize("role", ["accountant", "employee", "manager"])
def test_refused(role):
    with pytest.raises(HTTPException) as e:
        wd_routers._office(_Req(role))
    assert e.value.status_code == 403


@pytest.mark.parametrize("role", ["org_admin", "hr_admin", "hr_manager", "payroll_admin", "superadmin"])
def test_allowed(role):
    assert wd_routers._office(_Req(role)) == role
