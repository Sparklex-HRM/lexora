"""Grant rules for org Roles & Permissions (2026-10-24): no escalation, Org Admin platform-only, least privilege."""
from shared.utils import rbac_catalogue as cat
from shared.utils import rbac_grant as G
from shared.utils.rbac_models import access_from

HR = cat.system_permissions("hr_admin")
ORG = cat.system_permissions("org_admin")
#: a delegable permission the Payroll Admin base carries. In Lexora (no Books / payouts module) HR Admin holds every
#: Payroll Admin permission, so the escalation case is a NARROWED HR granter (a custom HR role) that lacks it.
EXTRA = "payroll.run"
HR_NARROW = [p for p in HR if p != EXTRA]


def _def(base, reach, perms, granter="org_admin", gperms=None, greach="org"):
    return G.definition_refusal(base, reach, perms, granter_role=granter,
                                granter_perms=ORG if gperms is None else gperms, granter_reach=greach)


def test_leave_desk_role_is_fine():
    assert _def("hr_manager", "org", ["leave.view", "leave.approve"]) is None


def test_org_admin_is_never_a_base_and_platform_perms_never_delegated():
    r = _def("org_admin", "org", ["leave.view"])
    assert r and r.code == "RBAC_BASE_PLATFORM_ONLY"
    r = _def("superadmin", "org", ["leave.view"])
    assert r and r.code == "RBAC_BASE_PLATFORM_ONLY"
    r = _def("hr_admin", "org", ["platform.view"], granter="superadmin")
    assert r and r.code == "RBAC_NON_DELEGABLE"
    for p in ("users.create", "roles.edit", "roles.create"):
        r = _def("hr_admin", "org", [p])
        assert r and r.code == "RBAC_NON_DELEGABLE", p
    assert _def("hr_admin", "org", ["roles.view"]) is None          # read-only role screen may be delegated


def test_granter_cannot_give_what_they_do_not_have():
    # the granter lacks EXTRA: a role carrying it is refused
    assert EXTRA in cat.system_permissions("payroll_admin") and EXTRA not in HR_NARROW
    r = _def("payroll_admin", "org", [EXTRA], granter="hr_admin", gperms=HR_NARROW)
    assert r and r.code == "RBAC_ESCALATION"
    # ... but an Org Admin may
    assert _def("payroll_admin", "org", [EXTRA]) is None


def test_reach_never_wider_than_the_granter():
    r = _def("hr_manager", "org", ["leave.view"], granter="hr_admin", gperms=HR, greach="team")
    assert r and r.code == "RBAC_REACH_ESCALATION"


def test_role_cannot_exceed_its_acting_system_role():
    # the services still check the acting role: a manager-based (team) role cannot carry payroll runs
    r = _def("manager", "team", ["payroll.run"])
    assert r and r.code == "RBAC_OVER_BASE"
    r = _def("accountant", "org", ["data.pay"])                     # accountant never sees salary
    assert r and r.code == "RBAC_OVER_BASE"


def test_reach_by_base_and_narrow_reach_modules():
    assert _def("employee", "org", ["leave.view"]).code == "RBAC_REACH_BASE"
    assert _def("manager", "org", ["leave.view"]).code == "RBAC_REACH_BASE"
    assert _def("hr_admin", "department", ["people.view", "data.personal"]) is None
    r = _def("hr_admin", "department", ["leave.view"])
    assert r and r.code == "RBAC_REACH_NARROW_MODULE"


def test_unknown_or_empty_permissions():
    assert _def("hr_admin", "org", []).code == "RBAC_NO_PERMISSIONS"
    assert _def("hr_admin", "org", ["leave.fly"]).code == "RBAC_UNKNOWN_PERMISSION"
    assert _def("hr_admin", "galaxy", ["leave.view"]).code == "RBAC_REACH_INVALID"
    assert _def("ceo", "org", ["leave.view"]).code == "RBAC_BASE_INVALID"


def _assign(granter="org_admin", gid="g", gperms=None, greach="org", target="t", trole="employee", role=None):
    return G.assign_refusal(granter_role=granter, granter_id=gid, granter_perms=ORG if gperms is None else gperms,
                            granter_reach=greach, target_id=target, target_system_role=trole,
                            role=role or {"permissions": ["leave.view"], "reach": "org", "status": "active"})


def test_assign_rules():
    assert _assign() is None
    assert _assign(gid="same", target="same").code == "RBAC_SELF_ASSIGN"
    assert _assign(trole="org_admin").code == "RBAC_ORG_ADMIN_PLATFORM_ONLY"       # even by an org_admin
    assert _assign(granter="superadmin", trole="org_admin") is None
    assert _assign(role={"permissions": ["leave.view"], "reach": "org", "status": "retired"}).code == "RBAC_ROLE_RETIRED"
    big = {"permissions": [EXTRA], "reach": "org", "status": "active"}
    assert _assign(granter="hr_admin", gperms=HR_NARROW, role=big).code == "RBAC_ESCALATION"


def test_edit_rules():
    big = {"permissions": [EXTRA], "reach": "org"}
    assert G.edit_refusal(granter_role="hr_admin", granter_perms=HR_NARROW, granter_reach="org", role=big).code == "RBAC_ESCALATION"
    assert G.edit_refusal(granter_role="org_admin", granter_perms=ORG, granter_reach="org", role=big) is None


def test_system_role_grants_org_admin_platform_only():
    assert G.system_role_refusal("org_admin", "org_admin").code == "AUTH_ROLE_PLATFORM_ONLY"
    assert G.system_role_refusal("hr_admin", "org_admin").code == "AUTH_ROLE_PLATFORM_ONLY"
    assert G.system_role_refusal("superadmin", "org_admin") is None
    assert G.system_role_refusal("org_admin", "superadmin").code == "AUTH_ROLE_SUPERADMIN"
    assert G.system_role_refusal("org_admin", "hr_admin") is None
    assert G.system_role_refusal("manager", "employee").code == "AUTH_ROLE_DENIED"
    assert G.system_manage_refusal("org_admin", "org_admin").code == "AUTH_ORG_ADMIN_PLATFORM_ONLY"
    assert G.system_manage_refusal("superadmin", "org_admin") is None
    assert G.system_manage_refusal("org_admin", "hr_admin") is None


def test_manage_refusal():
    assert G.manage_refusal("org_admin") is None and G.manage_refusal("hr_admin") is None
    assert G.manage_refusal("hr_manager").code == "RBAC_MANAGE_DENIED"
    assert G.manage_refusal("payroll_admin").code == "RBAC_MANAGE_DENIED"


def test_access_union_and_reach():
    customs = [{"key": "leave_desk", "name": "Leave desk", "base_role": "hr_manager", "reach": "org",
                "permissions": ["leave.view", "leave.approve"], "status": "active"},
               {"key": "old", "name": "Old", "base_role": "hr_manager", "reach": "org",
                "permissions": ["hiring.view"], "status": "retired"}]
    a = access_from("employee", customs)
    assert a["reach"] == "org" and "leave.approve" in a["perms"] and "hiring.view" not in a["perms"] or \
        "hiring.view" in cat.system_permissions("employee")
    assert a["ui_roles"] == ["employee", "hr_manager"]
    assert a["roles"] == ["employee", "custom:leave_desk"]
    assert access_from("employee", [])["reach"] == "own"
