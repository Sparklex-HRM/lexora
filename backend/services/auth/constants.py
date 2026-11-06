"""Lexora HRM — roles & permissions (RBAC embedded in the JWT)."""

ROLES = {
    "superadmin":     ["*"],
    "org_admin":      ["org:*", "user:*", "employee:*", "payroll:*", "report:read"],
    "hr_admin":       ["employee:*", "onboarding:*", "leave:*", "time:*", "approval:*", "report:read"],
    "hr_manager":     ["employee:read", "onboarding:*", "leave:approve", "time:approve", "approval:*"],
    "payroll_admin":  ["payroll:*", "employee:read", "report:read"],
    # The accounts person: reads reports and the names-only directory, acts on approvals; no payroll, no people edits.
    "accountant":     ["employee:read", "report:read", "approval:act"],
    "manager":        ["employee:read", "leave:approve", "time:approve", "approval:act"],
    "employee":       ["self:read", "self:write", "leave:request", "time:submit"],
}

ADMIN_ROLES = {"superadmin", "org_admin", "hr_admin"}


# ── Subscriptions & modules (Phase 4) ─────────────────────────────────────────
# The canonical module keys the sidebar/routes gate on. "core" is always on.
MODULES = ["core", "payroll", "time", "leave", "benefits", "talent", "ops",
           "analytics", "ai", "compliance"]            # enterprise = every module

# Plan → default enabled modules. An org's explicit `modules` list overrides this
# (so add-ons / trials are just flag edits). Illustrative tiers, not final pricing.
PLAN_MODULES = {
    "starter":    ["core", "payroll"],
    "growth":     ["core", "payroll", "time", "leave", "benefits", "ops", "compliance"],
    "enterprise": MODULES,
}


def resolve_modules(plan: str | None, modules: list | None) -> list:
    """Enabled modules for an org: an explicit list wins; else the plan default;
    'core' is always included."""
    base = list(modules) if modules else PLAN_MODULES.get((plan or "growth"), PLAN_MODULES["growth"])
    if "core" not in base:
        base = ["core"] + base
    return base
