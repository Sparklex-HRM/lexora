"""Org Roles & Permissions + signed UI grant — end-to-end through the gateway (MAPLE demo org).

    PYTHONIOENCODING=utf-8 python -X utf8 scripts/e2e_rbac.py          # gateway http://localhost:8200
    LEXORA_GATEWAY=http://host:8200 python -X utf8 scripts/e2e_rbac.py

Needs the rebuilt auth-service (with UI_GRANT_PRIVATE_KEY, kid dev locally) + api-gateway + employee-service.
Self-cleaning: the custom role "E2E leave viewer" is reused on every run (restored if retired), given to
chloe.tremblay for the checks and taken away + retired at the end (a role that was ever assigned is never deleted —
its history stays). PASS / FAIL per step, exit 1 on any FAIL.
"""
from __future__ import annotations

import base64
import json
import os
import sys

import requests

GW = os.getenv("LEXORA_GATEWAY", "http://localhost:8200").rstrip("/")
ORG, PW = "MAPLE", os.getenv("LEXORA_E2E_PASSWORD", "Maple@Demo2026")
ROLE_NAME = "E2E leave viewer"
RESULTS: list[tuple[str, str]] = []
TOK: dict[str, dict] = {}


def login(user: str, fresh: bool = False) -> dict:
    if fresh or user not in TOK:
        body = {"org_code": ORG, "email": f"{user}@maple.example", "password": PW}
        sys.path.insert(0, os.path.dirname(__file__))
        import _captcha
        _captcha.attach(GW, "/auth/login", body)
        r = requests.post(f"{GW}/auth/login", json=body, timeout=30)
        r.raise_for_status()
        TOK[user] = r.json()
    return TOK[user]


def H(user: str) -> dict:
    return {"Authorization": f"Bearer {login(user)['access_token']}"}


def call(user, m, path, **kw):
    return requests.request(m, f"{GW}{path}", headers=H(user), timeout=30, **kw)


def b64json(seg: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))


def step(name):
    def deco(fn):
        try:
            note = fn() or ""
            RESULTS.append((name, "PASS"))
            print(f"PASS  {name}" + (f"  — {note}" if note else ""), flush=True)
        except Exception as ex:  # noqa: BLE001
            RESULTS.append((name, f"FAIL {ex}"))
            print(f"FAIL  {name}  — {str(ex)[:600]}", flush=True)
        return fn
    return deco


CTX: dict = {}


def run():
    @step("login + /auth/me carry an ES256 ui_grant bound to the token")
    def _():
        t = login("admin")
        g = t.get("ui_grant")
        assert isinstance(g, str) and g.count(".") == 2, f"no ui_grant on login (is UI_GRANT_PRIVATE_KEY set?) {str(t)[:200]}"
        h, p, _s = g.split(".")
        hdr, body = b64json(h), b64json(p)
        assert hdr["alg"] == "ES256" and hdr["typ"] == "jethro-ui-grant", hdr
        tok = b64json(t["access_token"].split(".")[1])
        assert body["sub"] == tok["sub"] and body["sid"] == tok.get("sid") and body["role"] == "org_admin", body
        me = call("admin", "GET", "/auth/me").json()
        assert me.get("ui_grant") and b64json(me["ui_grant"].split(".")[1])["sub"] == tok["sub"]
        return f"kid {hdr['kid']}, {len(body['perms'])} permissions"

    @step("catalogue: modules x actions, read-only system roles; employee refused")
    def _():
        c = call("admin", "GET", "/auth/rbac/catalogue").json()
        keys = {m["key"] for m in c["modules"]}
        assert {"leave", "payroll", "statutory"} <= keys and "books" not in keys, keys
        sysr = {r["key"]: r for r in c["system_roles"]}
        assert sysr["org_admin"]["can_clone"] is False
        r = call("chloe.tremblay", "GET", "/auth/rbac/catalogue")
        assert r.status_code == 403, r.status_code
        return f"{len(c['modules'])} modules, {c['route_count']} routes"

    @step("create (or reuse) the custom role 'E2E leave viewer' = leave.view only")
    def _():
        roles = call("admin", "GET", "/auth/rbac/roles").json()["custom"]
        r = next((x for x in roles if x["name"] == ROLE_NAME), None)
        body = {"name": ROLE_NAME, "description": "e2e_rbac.py", "base_role": "hr_manager", "reach": "org",
                "permissions": ["leave.view"]}
        if r:
            if r["status"] != "active":
                assert call("admin", "POST", f"/auth/rbac/roles/{r['id']}/restore").status_code == 200
            x = call("admin", "PUT", f"/auth/rbac/roles/{r['id']}", json=body)
        else:
            x = call("admin", "POST", "/auth/rbac/roles", json=body)
        assert x.status_code in (200, 201), x.text
        CTX["role"] = x.json()
        users = call("admin", "GET", "/auth/rbac/users", params={"q": "chloe.tremblay@maple.example"}).json()["items"]
        CTX["chloe"] = users[0]["user_id"]

    @step("a role never exceeds its base (settings.edit on a Payroll Admin base); nobody can build on Org Admin")
    def _():
        r = call("hr", "POST", "/auth/rbac/roles", json={"name": "E2E over base", "base_role": "payroll_admin",
                                                             "reach": "org", "permissions": ["settings.edit"]})
        assert r.status_code == 422 and r.json().get("code") == "RBAC_OVER_BASE", r.text
        r = call("admin", "POST", "/auth/rbac/roles", json={"name": "E2E admin", "base_role": "org_admin",
                                                         "reach": "org", "permissions": ["leave.view"]})
        assert r.status_code == 403 and r.json().get("code") == "RBAC_BASE_PLATFORM_ONLY", r.text

    @step("Org Admin login cannot be created by an Org Admin (platform operator only)")
    def _():
        r = call("admin", "POST", "/auth/users", json={"email": "e2e.second.admin@maple.example", "role": "org_admin",
                                                    "send_invite": False})
        assert r.status_code == 403 and r.json().get("code") == "AUTH_ROLE_PLATFORM_ONLY", r.text

    @step("before the role: chloe (employee) is refused the org-wide payroll runs")
    def _():
        r = call("chloe.tremblay", "GET", "/payroll/runs")
        assert r.status_code == 403, r.status_code

    @step("give the role to chloe; her new grant carries leave.view + hr_manager screens")
    def _():
        r = call("admin", "POST", f"/auth/rbac/roles/{CTX['role']['id']}/members", json={"user_ids": [CTX["chloe"]]})
        assert r.status_code == 200, r.text
        pv = call("admin", "GET", f"/auth/rbac/users/{CTX['chloe']}").json()
        assert "leave.view" in pv["perms"] and "hr_manager" in pv["ui_roles"], pv["ui_roles"]
        me = call("chloe.tremblay", "GET", "/auth/me").json()
        g = b64json(me["ui_grant"].split(".")[1])
        assert "leave.view" in g["custom_perms"] and "hr_manager" in g["ui_roles"], g

    @step("custom role with only leave.view: views org-wide leave, still refused payroll + approving")
    def _():
        import time
        time.sleep(2.5)                                   # gateway login-state cache (2 s)
        r = call("chloe.tremblay", "GET", "/leave/requests")
        assert r.status_code == 200, r.text[:300]
        rows = r.json() if isinstance(r.json(), list) else r.json().get("items", [])
        people = {x.get("employee_id") for x in rows if isinstance(x, dict)}
        assert len(people) > 1, f"expected other people's leave, got {len(people)} employee(s)"
        r = call("chloe.tremblay", "GET", "/payroll/runs")
        assert r.status_code == 403 and r.json().get("code") == "RBAC_DENIED", r.text[:200]
        return f"{len(rows)} leave rows across {len(people)} people"

    @step("retire the role: chloe is back to her own leave only")
    def _():
        import time
        assert call("admin", "DELETE", f"/auth/rbac/roles/{CTX['role']['id']}/members/{CTX['chloe']}").status_code == 200
        assert call("admin", "POST", f"/auth/rbac/roles/{CTX['role']['id']}/retire").status_code == 200
        time.sleep(2.5)
        r = call("chloe.tremblay", "GET", "/leave/requests")
        rows = r.json() if isinstance(r.json(), list) else r.json().get("items", [])
        people = {x.get("employee_id") for x in rows if isinstance(x, dict)}
        assert len(people) <= 1, people
        h = call("admin", "GET", "/auth/rbac/history", params={"role_id": CTX["role"]["id"]}).json()
        acts = {x["action"] for x in h}
        assert {"user.assign", "user.unassign", "role.retire"} <= acts, acts

    @step("malformed ids answer 422 / 404, not 500")
    def _():
        for path in ("/auth/rbac/roles/not-a-uuid", "/payroll/runs/not-a-uuid", "/employees/not-a-uuid"):
            r = call("admin", "GET", path)
            assert r.status_code in (404, 422), f"{path} -> {r.status_code}"

    fails = [n for n, v in RESULTS if v != "PASS"]
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(run())
