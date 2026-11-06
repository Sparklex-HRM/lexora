"""Router factory for the three lifecycle agents — the same workspace shape for each (office roles via
``lifecycle_common.guard``: permission ``ai.view`` / ``ai.create``; settings = ``ai.admin`` (rbac_catalogue keys), org admin).
The approver / actor is the signed-in person from the gateway headers — never a body field.

  POST /ai/agents/<slug>/run                 manual run (trigger "manual")
  GET  /ai/agents/<slug>/overview            state · settings · checks · latest run summary · counts · cases (items grouped)
                                             · would-have-done (shadow) · this agent's open approval cards
  GET  /ai/agents/<slug>/items?status&case   the item grid
  POST /ai/agents/<slug>/items/{id}/dismiss  {reason} — "does not apply" (closes the chase task, logged)
  GET  /ai/agents/<slug>/runs                this agent's runs
  GET/PUT /ai/agents/<slug>/settings         the agent's own keys (ai_agentic.<agent>)
"""
from __future__ import annotations

import sys
from typing import Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

sys.path.append("/app")

import agentic_proposals as PR  # noqa: E402
import agentic_runs as RUNS  # noqa: E402
import agentic_state as ST  # noqa: E402
from agentic_registry import REGISTRY  # noqa: E402

from agents import lifecycle_common as LC  # noqa: E402
from agents.lifecycle_common import guard  # noqa: E402  (role-family + permission guard; named import so the authz AST sees it)


class RunIn(BaseModel):
    note: str | None = Field(default=None, max_length=300)


class DismissIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


class SettingsIn(BaseModel):
    value: dict


def group_cases(items: list[dict]) -> list[dict]:
    cases: dict[str, dict] = {}
    for x in items:
        c = cases.setdefault(x["case_key"], {"case_key": x["case_key"], "case_label": x.get("case_label"), "employee_id": x.get("employee_id"),
                                             "employee_ref": x.get("employee_ref"), "items": [], "blocking": 0, "warning": 0, "info": 0, "next_due": None, "clocks": []})
        c["items"].append(x)
        c[x["severity"]] = c.get(x["severity"], 0) + 1
        if x.get("due_on") and x["severity"] != "info" and (c["next_due"] is None or x["due_on"] < c["next_due"]):
            c["next_due"] = x["due_on"]
        if x.get("clock"):
            c["clocks"].append({**x["clock"], "code": x["code"]})
    return sorted(cases.values(), key=lambda c: (-c["blocking"], c["next_due"] or "9999", -c["warning"], c["case_label"] or ""))


def make_router(*, agent: str, slug: str, run_fn: Callable[..., Awaitable[dict]], defaults: dict, checks: list, label: str) -> APIRouter:
    router = APIRouter(prefix=f"/ai/agents/{slug}", tags=[f"agentic-{slug}"])
    who = f"The {label} is for the org admin, HR admin and payroll admin"

    @router.post("/run")
    async def run_now(request: Request, body: RunIn | None = None):
        org = LC.org_of(request)
        guard(request, "ai.create", who)
        uid = LC.user_of(request)
        try:
            return await run_fn(org, trigger="manual", requested_by=uid)
        except Exception as e:                                   # noqa: BLE001
            LC.raise_http(e)

    @router.get("/overview")
    async def overview(request: Request):
        org = LC.org_of(request)
        role = guard(request, "ai.view", who)
        st = await ST.agentic_state(org)
        items = await LC.list_items(org, agent, status="open")
        runs = await RUNS.list_runs(org, agent=agent, limit=20)
        latest = runs[0] if runs else None
        inbox = [p for p in await PR.list_proposals(org, agent=agent, status="inbox", limit=200)]
        would = []
        if latest and latest.get("mode") == "shadow":
            would = [p for p in await PR.list_proposals(org, agent=agent, run_id=latest["id"], limit=300) if p["status"] == "shadow"]
        spec = REGISTRY[agent]
        return {"agent": agent, "role_name": spec.role_name, "does": spec.does, "you_approve": spec.you_approve, "never": list(spec.never),
                "tier_map": spec.tier_map,
                "state": {k: st[k] for k in ("enabled", "addon_purchased", "paused", "mode", "can_go_live", "live_gate_note")},
                "settings": LC.agent_settings(st, agent, defaults), "can_edit": role in ("org_admin", "superadmin"),
                "checks": [{"code": c, "name": n} for c, n in checks], "latest_run": latest, "runs": runs[:10],
                "counts": LC.summary_counts(items), "cases": group_cases(items), "inbox": inbox, "would_have_done": would[:200],
                "shadow_note": ("Shadow mode — nothing was sent; this is what I would have done." if st["mode"] == "shadow" else None)}

    @router.get("/items")
    async def items(request: Request, status: str | None = "open", case: str | None = None):
        org = LC.org_of(request)
        guard(request, "ai.view", who)
        return {"items": await LC.list_items(org, agent, status=status, case_key=case)}

    @router.post("/items/{item_id}/dismiss")
    async def dismiss(item_id: str, body: DismissIn, request: Request):
        org = LC.org_of(request)
        guard(request, "ai.create", who)
        uid = LC.user_of(request)
        try:
            return await LC.dismiss_item(org, agent, item_id, reason=body.reason, actor_user_id=uid)
        except Exception as e:                                   # noqa: BLE001
            LC.raise_http(e)

    @router.get("/runs")
    async def runs(request: Request):
        org = LC.org_of(request)
        guard(request, "ai.view", who)
        return {"items": await RUNS.list_runs(org, agent=agent, limit=100)}

    @router.get("/settings")
    async def get_settings(request: Request):
        org = LC.org_of(request)
        role = guard(request, "ai.view", who)
        st = await ST.agentic_state(org)
        return {"value": LC.agent_settings(st, agent, defaults), "defaults": defaults, "can_edit": role in ("org_admin", "superadmin")}

    @router.put("/settings")
    async def put_settings(body: SettingsIn, request: Request):
        org = LC.org_of(request)
        guard(request, "ai.admin", f"Only an org admin can change the {label} settings")
        try:
            saved = await LC.save_agent_settings(org, agent, body.value, defaults, actor_id=request.headers.get("x-user-id"),
                                                 actor_email=request.headers.get("x-user-email"))
        except ValueError as e:
            raise HTTPException(422, str(e))
        return {"saved": True, "value": saved}

    return router
