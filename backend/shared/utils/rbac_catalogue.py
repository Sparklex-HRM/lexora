"""Lexora HRM — the permission catalogue (org Roles & Permissions, go-live wrap-up 2026-10-24).

ONE place that says what a permission is and which permission every API route needs.

    permission key  = "<module>.<action>"        e.g. "leave.view", "payroll.run", "statutory.file"
                    | "data.<class>"             sensitive data scopes: data.pay, data.identity,
                                                 data.personal, data.audit

Modules (MODULES) own API path prefixes; the LONGEST prefix wins. Actions (ACTIONS) are derived
from the HTTP method and the path words (route_action). The data scopes come from the data class
the access contract (security/authz/policy.yaml) gives the route: salary / payslip / tax -> data.pay,
identity -> data.identity, people -> data.personal, audit -> data.audit.

The real route registry (security/authz/routes.json, built by importing every service) and the
contract are compiled by security/authz/build_rbac_catalogue.py into rbac_routes.json next to this
file: for every reachable route -> required permissions + the system roles the contract allows.
The permission sets of the SYSTEM roles are derived from the same compile (a system role holds the
permissions of every route the contract opens to it), so the screen, the grant rules and the
gateway all read one table. A unit test fails when a route has no module, the compile is stale,
or a permission appears that the catalogue does not know.

Pure module: no FastAPI, no database — imported by the gateway, the auth service and the tests.
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path

# ── modules ───────────────────────────────────────────────────────────────────
# key, label, menu group, API prefixes, extra actions (beyond the 7 standard ones), description
MODULES: list[dict] = [
    {"key": "people", "label": "Employees & directory", "group": "People",
     "prefixes": ["/employees"], "desc": "Employee records, profiles, documents, ESS requests"},
    {"key": "masters", "label": "Masters & org structure", "group": "People",
     "prefixes": ["/masters", "/employees/entities", "/employees/custom-fields", "/employees/form-options",
                  "/employees/next-number"],
     "desc": "Departments, job titles, grades, locations, leave types, pay components, custom fields"},
    {"key": "career", "label": "Career changes, probation & fixed-term", "group": "People",
     "prefixes": ["/employees/career", "/employees/fixed-term"], "desc": "Promotions, transfers, probation, contract renewals"},
    {"key": "assets", "label": "Assets", "group": "People", "prefixes": ["/assets", "/exit/assets"],
     "desc": "Asset register, issue / return"},
    {"key": "onboarding", "label": "Onboarding", "group": "People", "prefixes": ["/onboarding"],
     "desc": "Joiner invites, onboarding sessions, checklists"},
    {"key": "verification", "label": "Background checks", "group": "People",
     "prefixes": ["/auth/bgv"],
     "desc": "Background checks (criminal record, references, credentials)"},
    {"key": "exit", "label": "Exits, final pay & ROE", "group": "People",
     "prefixes": ["/exit", "/payroll/exit", "/payroll/final-pay"], "desc": "Resignations, clearance, final pay"},
    {"key": "letters", "label": "Letters & e-sign", "group": "People",
     "prefixes": ["/letters", "/auth/esign"], "desc": "Letter templates, issued letters, e-sign"},
    {"key": "hiring", "label": "Hiring", "group": "Talent", "prefixes": ["/hiring"],
     "desc": "Requisitions, candidates, interviews, offers"},
    {"key": "performance", "label": "Performance & goals", "group": "Talent", "prefixes": ["/performance"],
     "desc": "Goals, OKRs, reviews, 360 feedback, PIPs, 1:1s"},
    {"key": "compensation", "label": "Compensation & revisions", "group": "Talent",
     "prefixes": ["/performance/comp", "/performance/increment-proposals", "/employees/compensation",
                  "/payroll/revisions"],
     "desc": "Salary revisions, comp cycles, increment proposals"},
    {"key": "leave", "label": "Leave", "group": "Time", "prefixes": ["/leave"],
     "desc": "Leave requests, balances, encashment, holidays"},
    {"key": "attendance", "label": "Attendance, shifts & timesheets", "group": "Time", "prefixes": ["/time", "/iclock"],
     "desc": "Punches, regularisation, rosters, timesheets, devices"},
    {"key": "payroll", "label": "Payroll", "group": "Payroll", "prefixes": ["/payroll"],
     "extra": ["run"], "desc": "Pay runs, payslips, salary structures, pay entries"},
    {"key": "statutory", "label": "Year-end & remittances", "group": "Payroll",
     "prefixes": ["/payroll/compliance", "/payroll/t4", "/payroll/rl1", "/payroll/roe", "/payroll/remittance-schedule",
                  "/payroll/employer-health-tax", "/payroll/verification"],
     "extra": ["file"], "desc": "T4 / RL-1 slips, ROE, CRA remittance schedule, employer health tax"},
    {"key": "approvals", "label": "Approvals & workflows", "group": "Workflow", "prefixes": ["/approvals"],
     "desc": "Approval inbox, delegations, approval chains"},
    {"key": "reports", "label": "Reports & analytics", "group": "Insights",
     "prefixes": ["/reports", "/engage/analytics", "/engage/insights"], "desc": "Reports, report builder, dashboards"},
    {"key": "ai", "label": "AI assistant & agents", "group": "Insights", "prefixes": ["/ai"],
     "desc": "Assistant, copilot, watchdogs, AI usage"},
    {"key": "engage", "label": "Engagement & helpdesk", "group": "Engage", "prefixes": ["/engage"],
     "desc": "Feed, kudos, surveys, helpdesk, positions"},
    {"key": "learning", "label": "Learning", "group": "Engage", "prefixes": ["/engage/lms"],
     "desc": "Courses, assignments, completions"},
    {"key": "projects", "label": "Projects & PSA", "group": "Operations", "prefixes": ["/ops", "/time/psa"],
     "desc": "Projects, clients, billable time"},
    {"key": "compliance", "label": "Compliance & safety", "group": "Compliance", "prefixes": ["/compliance"],
     "desc": "WCB / WSIB, health & safety register"},
    {"key": "audit", "label": "Audit trails", "group": "Compliance",
     "prefixes": ["/compliance/audit", "/settings/audit", "/notifications/deliveries"],
     "desc": "Audit log, notification delivery log"},
    {"key": "notifications", "label": "Notifications & templates", "group": "Settings", "prefixes": ["/notifications"],
     "desc": "In-app notifications, e-mail templates"},
    {"key": "migration", "label": "Data import", "group": "Settings",
     "prefixes": ["/payroll/migration", "/employees/import-canada"],
     "desc": "Imports, opening YTD balances, parallel runs"},
    {"key": "settings", "label": "Company settings", "group": "Settings",
     "prefixes": ["/settings", "/auth", "/auth/settings", "/auth/setup-checklist", "/auth/entities", "/auth/subscription",
                  "/auth/billing", "/auth/org-export"],
     "desc": "Company profile, legal entities, numbering, plan & billing"},
    {"key": "integrations", "label": "Integrations, SSO & API keys", "group": "Settings",
     "prefixes": ["/integrations", "/auth/api-keys", "/auth/webhooks", "/auth/integrations", "/auth/sso", "/auth/scim",
                  "/notifications/chatops", "/notifications/whatsapp", "/approvals/whatsapp"],
     "desc": "Provider keys, SSO, SCIM, webhooks, WhatsApp, chat apps"},
    {"key": "users", "label": "Users & logins", "group": "Settings", "prefixes": ["/auth/users", "/auth/mfa/reset"],
     "desc": "Create / invite / disable logins, system role of a login"},
    {"key": "roles", "label": "Roles & permissions", "group": "Settings", "prefixes": ["/auth/rbac"],
     "desc": "Custom roles, role assignments, effective permissions"},
    {"key": "platform", "label": "Lexora platform (operator only)", "group": "Platform",
     "prefixes": ["/auth/platform"], "platform_only": True,
     "desc": "Tenants, plans — the Lexora HRM operator only"},
]
MODULE_KEYS = [m["key"] for m in MODULES]
MODULE_BY_KEY = {m["key"]: m for m in MODULES}

ACTIONS = ["view", "create", "edit", "approve", "delete", "export", "admin"]
ACTION_LABELS = {"view": "View", "create": "Create", "edit": "Edit", "approve": "Approve", "delete": "Delete",
                 "export": "Export", "admin": "Admin / settings", "run": "Run payroll", "file": "File / submit"}

# Sensitive data scopes (from the access contract's data class)
DATA_SCOPES = [
    {"key": "data.pay", "label": "See salary, pay & tax", "desc": "Salary, payslips, pay registers, tax withheld"},
    {"key": "data.identity", "label": "See SIN & bank", "desc": "Social Insurance Number and bank accounts"},
    {"key": "data.personal", "label": "See other employees' personal records",
     "desc": "Profiles, documents, HR case records of other people"},
    {"key": "data.audit", "label": "See audit trails", "desc": "Who changed what, when"},
]
CLASS_TO_DATA = {"salary": "data.pay", "payslip": "data.pay", "tax": "data.pay", "identity": "data.identity",
                 "people": "data.personal", "audit": "data.audit"}
#: "people" class routes need data.personal only in the modules that hold HR RECORDS of a person (profile,
#: documents, career, exit, letters, onboarding, checks, reviews / PIPs, safety cases). Leave, attendance, approvals
#: etc. are governed by their own module permission (leave.view shows leave requests).
PERSONAL_MODULES = frozenset({"people", "career", "exit", "letters", "onboarding", "verification", "performance",
                              "compliance", "hiring"})

#: Implicit for every signed-in login (own account, own inbox, public pages) — never shown as a tick box.
SELF = "self.use"
#: Routes every login may call whatever its roles (each answer is role-checked inside): the global search box.
SELF_PATHS = frozenset({"/search"})

#: Data reach of a role, narrow -> wide. A custom role's scope may never be wider than its granter's.
DATA_REACH = ["own", "team", "department", "location", "entity", "org"]
REACH_RANK = {k: i for i, k in enumerate(DATA_REACH)}
REACH_LABELS = {"own": "Own records", "team": "Own team (direct reports)", "department": "Own department",
                "location": "Own location / branch", "entity": "Own legal entity", "org": "Whole organisation"}
#: On a contract "scoped" route these roles are answered with their own / team rows only (policy.yaml header), so
#: they hold the module action there but not the route's data scope.
SCOPED_NARROW = frozenset({"employee", "manager", "accountant"})
#: department / location / entity reach is enforced only where the service narrows by it (People list +
#: profile — employee service, X-Data-Scope). A role with that reach may carry only these permissions.
NARROW_REACH = ("department", "location", "entity")
NARROW_REACH_PERMS = frozenset({"people.view", "data.personal"})
NARROW_REACH_ROUTES = frozenset({("GET", "/employees"), ("GET", "/employees/{employee_id}")})

# ── system roles ──────────────────────────────────────────────────────────────
SYSTEM_ROLES = [
    {"key": "org_admin", "label": "Org Admin", "reach": "org",
     "desc": "Everything in the organisation. Created and changed only by the Lexora HRM platform operator."},
    {"key": "hr_admin", "label": "HR Admin", "reach": "org", "desc": "People, talent, leave & time, HR settings"},
    {"key": "hr_manager", "label": "HR Manager", "reach": "org", "desc": "Onboarding, approvals, helpdesk, letters"},
    {"key": "payroll_admin", "label": "Payroll Admin", "reach": "org", "desc": "Payroll, year-end slips, remittances"},
    {"key": "accountant", "label": "Accountant", "reach": "org", "desc": "Finance reader; names-only people directory"},
    {"key": "manager", "label": "Manager", "reach": "team", "desc": "Own direct reports: approvals, team views"},
    {"key": "employee", "label": "Employee", "reach": "own", "desc": "Own records (self-service)"},
]
SYSTEM_ROLE_KEYS = [r["key"] for r in SYSTEM_ROLES]
SYSTEM_REACH = {r["key"]: r["reach"] for r in SYSTEM_ROLES}
#: System roles a custom role may be built on (org_admin never: only the platform manages it).
CUSTOM_BASES = ("hr_admin", "hr_manager", "payroll_admin", "accountant", "manager", "employee")
#: Only the Lexora HRM platform operator may create / assign / change these (Entersoft 2026-10-24 Medium).
PLATFORM_ONLY_ROLES = frozenset({"superadmin", "org_admin"})


def acting_role(base_role: str, reach: str) -> str:
    """The system role a custom role acts as on a request: own reach -> employee, team -> manager,
    else its base role (department / location / entity narrow that base role's answers)."""
    if reach == "own":
        return "employee"
    if reach == "team":
        return "manager" if base_role != "employee" else "employee"
    return base_role


# ── permissions ───────────────────────────────────────────────────────────────
def module_actions(module_key: str) -> list[str]:
    m = MODULE_BY_KEY[module_key]
    return ACTIONS + list(m.get("extra") or [])


def all_permissions() -> list[str]:
    out = [f"{m['key']}.{a}" for m in MODULES for a in module_actions(m["key"])]
    return out + [d["key"] for d in DATA_SCOPES]


ALL_PERMISSIONS = frozenset(all_permissions())
#: Never on a custom role (least privilege): the platform's, and managing logins / roles (a custom role that
#: could create logins or roles would hand out its BASE role's power, which can exceed the role itself).
NON_DELEGABLE = frozenset(
    {p for p in ALL_PERMISSIONS if p.startswith("platform.")}
    | {f"users.{a}" for a in ACTIONS if a != "view"}
    | {f"roles.{a}" for a in ACTIONS if a != "view"})


def module_for(path: str) -> str | None:
    """Owning module of an API path: the longest module prefix on a "/" boundary."""
    best, best_len = None, -1
    for m in MODULES:
        for pre in m["prefixes"]:
            p = pre.rstrip("/")
            if (path == p or path.startswith(p + "/")) and len(p) > best_len:
                best, best_len = m["key"], len(p)
    return best


_EXT = re.compile(r"\.(zip|pdf|csv|xlsx|xls|xml|txt|json)$", re.I)
_EXPORT_WORDS = {"export", "exports", "download", "downloads", "csv", "xlsx", "xls", "pdf", "zip", "print", "file",
                 "files", "bundle", "txt", "xml"}
_APPROVE_WORDS = {"approve", "approved", "approval", "decide", "decision", "reject", "sign", "signoff", "finalize",
                  "finalise", "lock", "unlock", "release", "void", "reverse", "verify", "reopen"}
_ADMIN_WORDS = {"settings", "setting", "config", "configuration", "policy", "policies", "rules", "rule", "templates",
                "template", "seed", "number", "posting", "chains", "workflows", "go", "test"}
_READONLY_POST = {"preview", "validate", "check", "simulate", "search", "calculate", "estimate", "dry"}
_RUN_WORDS = {"runs", "run", "emi"}
_PAY_MODULES = {"payroll"}


def _literals(path: str) -> list[str]:
    return [s for s in path.strip("/").split("/") if s and not s.startswith("{")]


def _tokens(seg: str) -> set[str]:
    return {t for t in re.split(r"[-_.]", seg.lower()) if t}


def route_action(method: str, path: str, module: str | None = None) -> str:
    """The action a route needs (view / create / edit / approve / delete / export / admin, or the module
    extras run / file / post)."""
    method = method.upper()
    module = module or module_for(path)
    lits = _literals(path)
    segs = path.strip("/").split("/")
    last = lits[-1] if lits else ""
    last_tok = _tokens(last)
    ends_param = bool(segs) and segs[-1].startswith("{")
    all_tok: set[str] = set()
    for s in lits[1:]:
        all_tok |= _tokens(s)
    is_export = bool(_EXT.search(path)) or bool(last_tok & _EXPORT_WORDS)
    if method == "GET":
        if is_export:
            return "export"
        return "view"
    if method == "DELETE":
        return "delete"
    if method == "POST" and last_tok & _READONLY_POST and not is_export:
        return "view"
    if module in _PAY_MODULES and method == "POST" and (last_tok & _RUN_WORDS) and not ends_param:
        return "run"
    if module == "payroll" and method == "POST" and path in ("/payroll/runs",):
        return "run"
    if last_tok & _APPROVE_WORDS or (ends_param and segs[-1] in ("{decision}", "{action}")):
        return "approve"
    if all_tok & _ADMIN_WORDS:
        return "admin"
    if module == "statutory":
        return "export" if is_export else "file"
    if is_export:
        return "export"
    return "create" if method == "POST" else "edit"


def route_permissions(method: str, path: str, data_class: str | None, scope: str | None) -> list[str]:
    """Permissions a route needs. [] for self / public / internal routes (implicit SELF)."""
    scope = (scope or "org").lower()
    if scope in ("self", "public", "internal") or path in SELF_PATHS:
        return []
    module = module_for(path)
    if scope == "platform":
        module = "platform"
    if module is None:
        raise ValueError(f"no module for {method} {path}")
    act = route_action(method, path, module)
    if act not in module_actions(module):
        act = "create" if method.upper() == "POST" else ("view" if method.upper() == "GET" else "edit")
    perms = [f"{module}.{act}"]
    dp = CLASS_TO_DATA.get((data_class or "").lower())
    if dp == "data.personal" and module not in PERSONAL_MODULES:
        dp = None
    if dp:
        perms.append(dp)
    return perms


# ── the compiled route table (security/authz/build_rbac_catalogue.py) ─────────
TABLE_PATH = Path(os.getenv("LEXORA_RBAC_TABLE", str(Path(__file__).with_name("rbac_routes.json"))))


@lru_cache(maxsize=1)
def table() -> dict:
    """{"routes": [{"m","p","perms","roles","scope"}], "system_roles": {role: [perms]}, "source": {...}}"""
    try:
        return json.loads(TABLE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"routes": [], "system_roles": {}, "source": {}}


def system_permissions(role: str) -> frozenset[str]:
    if role == "superadmin":
        return frozenset(ALL_PERMISSIONS)
    return frozenset(table().get("system_roles", {}).get(role, []))


class RouteIndex:
    """Fast (method, concrete path) -> compiled route row. Exact literal segments beat {params};
    a trailing {name:path} parameter swallows the rest of the path."""

    def __init__(self, rows: list[dict]):
        self._by: dict[tuple, list[tuple[tuple, re.Pattern, dict]]] = {}
        self._tail: dict[tuple, list[tuple[tuple, re.Pattern, dict]]] = {}
        for r in rows:
            segs = r["p"].strip("/").split("/")
            head = segs[0] if segs else ""
            tail = bool(segs) and segs[-1].startswith("{") and segs[-1].endswith(":path}")
            parts = []
            for s in segs:
                if s.startswith("{") and s.endswith(":path}"):
                    parts.append(".+")
                elif s.startswith("{"):
                    parts.append("[^/]+")
                else:
                    parts.append(re.escape(s))
            rx = re.compile("^" + "/".join(parts) + "$")
            lit = sum(1 for s in segs if not s.startswith("{"))
            spec = (lit, len(r["p"]))
            if tail:
                self._tail.setdefault((r["m"].upper(), head), []).append((spec, rx, r))
            else:
                self._by.setdefault((r["m"].upper(), head, len(segs)), []).append((spec, rx, r))
        for v in list(self._by.values()) + list(self._tail.values()):
            v.sort(key=lambda x: x[0], reverse=True)

    def find(self, method: str, path: str) -> dict | None:
        p = path.strip("/")
        segs = p.split("/")
        head = segs[0] if segs else ""
        for _spec, rx, row in self._by.get((method.upper(), head, len(segs)), ()):
            if rx.match(p):
                return row
        for _spec, rx, row in self._tail.get((method.upper(), head), ()):
            if rx.match(p):
                return row
        return None


@lru_cache(maxsize=1)
def route_index() -> RouteIndex:
    return RouteIndex(table().get("routes", []))


def catalogue_view() -> dict:
    """What the Roles & Permissions screen shows: modules x actions, data scopes, reaches, system roles."""
    return {
        "modules": [{"key": m["key"], "label": m["label"], "group": m["group"], "desc": m.get("desc", ""),
                     "actions": module_actions(m["key"]), "platform_only": bool(m.get("platform_only"))}
                    for m in MODULES],
        "action_labels": ACTION_LABELS,
        "data_scopes": DATA_SCOPES,
        "reaches": [{"key": k, "label": REACH_LABELS[k], "narrow": k in NARROW_REACH} for k in DATA_REACH],
        "narrow_reach_permissions": sorted(NARROW_REACH_PERMS),
        "non_delegable": sorted(NON_DELEGABLE),
        "custom_bases": list(CUSTOM_BASES),
        "route_count": len(table().get("routes", [])),
    }
