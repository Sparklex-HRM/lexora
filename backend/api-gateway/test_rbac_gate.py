"""Gateway enforcement of the permission catalogue + custom roles (2026-10-24).

Run:  cd backend/api-gateway && PYTHONPATH=.. python -m pytest test_rbac_gate.py -q
"""
import rbac_gate as G

LEAVE_DESK = {"key": "leave_desk", "name": "Leave desk", "base_role": "hr_manager", "reach": "org",
              "permissions": ["leave.view"], "status": "active"}
UUID = "1b2c3d4e-0000-0000-0000-000000000000"


def st(*customs, scope_values=None):
    return {"custom_roles": list(customs), "scope_values": scope_values or {}}


def test_custom_role_with_only_leave_view_views_leave_and_is_refused_payroll():
    # an employee login + a custom role carrying only leave.view
    d = G.decide("GET", "/leave/requests", "employee", st(LEAVE_DESK), mode="all")
    assert not d.deny and d.acting_role == "hr_manager" and d.acting_key == "leave_desk"   # org-wide leave list
    d = G.decide("GET", "/payroll/runs", "employee", st(LEAVE_DESK), mode="all")
    assert d.deny and d.code == "RBAC_DENIED"
    d = G.decide("POST", "/payroll/runs", "employee", st(LEAVE_DESK), mode="all")
    assert d.deny
    # leave.view does not approve: the employee acts as itself (the service then refuses someone else's request)
    d = G.decide("POST", f"/leave/requests/{UUID}/approve", "employee", st(LEAVE_DESK), mode="all")
    assert not d.deny and d.acting_role is None


def test_custom_mode_even_refuses_for_custom_logins_and_passes_others():
    assert G.decide("GET", "/payroll/runs", "employee", st(LEAVE_DESK), mode="custom").deny
    assert not G.decide("GET", "/payroll/runs", "employee", st(), mode="custom").deny        # service decides
    assert not G.decide("GET", "/payroll/runs", "employee", st(LEAVE_DESK), mode="off").deny


def test_system_role_outside_the_contract_is_refused_at_the_gateway():
    assert G.decide("GET", "/payroll/runs", "employee", st(), mode="all").deny
    assert G.decide("GET", "/payroll/ca/remittances", "manager", st(), mode="all").deny
    assert not G.decide("GET", "/payroll/runs", "payroll_admin", st(), mode="all").deny
    assert not G.decide("GET", "/payroll/ca/remittances", "org_admin", st(), mode="all").deny


def test_unknown_routes_self_routes_and_superadmin_pass():
    assert not G.decide("GET", "/payroll/not-in-the-table/x/y/z", "employee", st(LEAVE_DESK), mode="all").deny
    assert not G.decide("GET", "/auth/me", "employee", st(LEAVE_DESK), mode="all").deny
    assert not G.decide("GET", "/search", "employee", st(LEAVE_DESK), mode="all").deny
    assert not G.decide("GET", "/payroll/runs", "superadmin", st(), mode="all").deny


def test_widest_reach_wins_and_ties_keep_the_system_role():
    # hr_admin already reaches the whole org: a custom role never replaces it there
    d = G.decide("GET", "/leave/requests", "hr_admin", st(LEAVE_DESK), mode="all")
    assert not d.deny and d.acting_role is None


def test_custom_role_must_hold_the_data_scope_too():
    pay_view = {"key": "pay_view", "name": "Pay viewer", "base_role": "payroll_admin", "reach": "org",
                "permissions": ["payroll.view"], "status": "active"}
    assert G.decide("GET", "/payroll/runs", "employee", st(pay_view), mode="all").deny       # no data.pay
    pay_view2 = {**pay_view, "permissions": ["payroll.view", "data.pay"]}
    d = G.decide("GET", "/payroll/runs", "employee", st(pay_view2), mode="all")
    assert not d.deny and d.acting_role == "payroll_admin"
    assert G.decide("POST", "/payroll/runs", "employee", st(pay_view2), mode="all").deny    # view is not run


def test_retired_role_gives_nothing():
    retired = {**LEAVE_DESK, "status": "retired"}
    d = G.decide("GET", "/leave/requests", "employee", st(retired), mode="all")
    assert not d.deny and d.acting_role is None          # the employee's own (narrowed) answer only


def test_team_reach_acts_as_manager():
    team = {"key": "team_leave", "name": "Team leave", "base_role": "hr_manager", "reach": "team",
            "permissions": ["leave.view", "leave.approve"], "status": "active"}
    d = G.decide("GET", "/leave/requests", "employee", st(team), mode="all")
    assert d.acting_role == "manager"


def test_narrow_reach_only_on_people_routes_and_needs_a_value():
    dept = {"key": "dept_hr", "name": "Dept HR", "base_role": "hr_admin", "reach": "department",
            "permissions": ["people.view", "data.personal"], "status": "active"}
    d = G.decide("GET", "/employees", "employee", st(dept, scope_values={"department": "Engineering"}), mode="all")
    assert d.acting_role == "hr_admin" and d.data_scope == "department:Engineering"
    d = G.decide("GET", f"/employees/{UUID}", "employee", st(dept, scope_values={"department": "Engineering"}),
                 mode="all")
    assert d.data_scope == "department:Engineering"
    # no department on the login's employee record -> the role cannot act (fail closed): own answer only
    d = G.decide("GET", "/employees", "employee", st(dept, scope_values={}), mode="all")
    assert d.acting_role is None
    # a route the employee service does not narrow: the narrow role never acts there
    d = G.decide("GET", f"/employees/{UUID}/documents", "employee",
                 st(dept, scope_values={"department": "Engineering"}), mode="all")
    assert d.acting_role is None


class _Req:
    def __init__(self, role="employee", branch="b1", view=None):
        class S:
            pass
        self.state = S()
        self.state.role, self.state.branch_id = role, branch
        self.headers = {"x-view-branch": view} if view else {}


def test_apply_restamps_role_and_scope():
    r = _Req()
    G.apply(r, G.Decision(acting_role="hr_manager", acting_key="leave_desk", reach="org"))
    assert r.state.role == "hr_manager" and r.state.acting_role == "leave_desk" and r.state.branch_id is None
    r = _Req()
    G.apply(r, G.Decision(acting_role="hr_admin", acting_key="k", reach="location", data_scope="location:b9"))
    assert r.state.branch_id == "b9" and r.state.data_scope == "location:b9"
    r = _Req()
    G.apply(r, G.PASS)
    assert r.state.role == "employee" and not hasattr(r.state, "acting_role")
