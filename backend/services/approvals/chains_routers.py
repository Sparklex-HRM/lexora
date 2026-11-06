"""`/approvals/workflows` — the no-code approval-chain builder (org admin / HR head).

  GET  /approvals/workflows                 every request type with its chain (defaults seeded)
  GET  /approvals/workflows/meta            request types, context fields, operators, roles, people
  GET  /approvals/workflows/{request_type}
  PUT  /approvals/workflows/{request_type}  {name, description, definition, active}  → validated, version+1, audited
  POST /approvals/workflows/{request_type}/reset        back to the one-step default
  POST /approvals/workflows/{request_type}/simulate     {context, definition?} → who would approve
  GET  /approvals/workflows/{request_type}/runs         recent runs (what actually happened)
"""
import sys

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.audit_trail import audit_change  # noqa: E402
from shared.utils import approval_chains as chains  # noqa: E402
from shared.utils import approvals_client as ac  # noqa: E402
from shared.utils import directory  # noqa: E402
from shared.utils import platform_models as pm  # noqa: E402
from shared.utils.approval_chains import ApprovalChain, ApprovalRun  # noqa: E402
from shared.utils.roles import has_role, require_roles  # noqa: E402

import chains_runtime as rt  # noqa: E402
from database import get_db  # noqa: E402

router = APIRouter(prefix="/approvals/workflows", tags=["approval-workflows"])
EDIT_ROLES = ("org_admin", "hr_admin")
VIEW_ROLES = ("org_admin", "hr", "payroll")


def _org(request: Request) -> str:
    org = request.headers.get("x-org-id")
    if not org:
        raise HTTPException(401, "Missing organization context")
    return org


def _actor(request: Request) -> str:
    return request.headers.get("x-user-email") or request.headers.get("x-user-id") or "system"


def _out(c: ApprovalChain) -> dict:
    d = c.definition or chains.default_definition()
    return {"id": c.id, "request_type": c.request_type, "label": chains.type_label(c.request_type), "name": c.name,
            "description": c.description or "", "definition": d, "version": c.version, "is_default": bool(c.is_default),
            "active": bool(c.active), "updated_by": c.updated_by, "updated_at": pm.iso(c.updated_at),
            "steps": len(d.get("steps") or []), "auto_rules": len(d.get("auto_approve") or []),
            "context_fields": chains.REQUEST_TYPES.get(c.request_type, {}).get("fields", [])}


@router.get("/meta")
async def meta(request: Request):
    org = _org(request)
    require_roles(request, VIEW_ROLES, "see approval workflows")
    users = await directory.org_users(org)
    return {"request_types": [{"key": k, "label": v["label"], "fields": v["fields"]} for k, v in chains.REQUEST_TYPES.items()],
            "context_fields": [{"key": k, "label": v} for k, v in chains.CONTEXT_FIELD_LABELS.items()],
            "operators": [{"op": o, "label": chains.OP_LABELS[o]} for o in chains.OPS],
            "roles": [{"key": k, "label": v} for k, v in chains.ROLE_CHOICES],
            "people": sorted(({"id": u["id"], "name": directory.display_name(u), "role": u.get("role") or ""}
                              for u in users if u.get("role") != "superadmin"), key=lambda x: x["name"].lower()),
            "can_edit": has_role(request.headers.get("x-role"), EDIT_ROLES)}


@router.get("")
async def list_chains(request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    require_roles(request, VIEW_ROLES, "see approval workflows")
    await rt.seed_defaults(db, org)
    rows = (await db.execute(select(ApprovalChain).where(ApprovalChain.org_id == org))).scalars().all()
    order = list(chains.REQUEST_TYPES)
    rows.sort(key=lambda c: (order.index(c.request_type) if c.request_type in order else 999, c.request_type))
    return {"items": [_out(c) for c in rows], "can_edit": has_role(request.headers.get("x-role"), EDIT_ROLES)}


async def _chain(db, org: str, request_type: str, create: bool = True) -> ApprovalChain:
    c = (await db.execute(select(ApprovalChain).where(ApprovalChain.org_id == org, ApprovalChain.request_type == request_type))).scalar_one_or_none()
    if not c and create:
        key = request_type.strip().lower()
        if not key or len(key) > 40 or not key.replace("_", "").isalnum():
            raise HTTPException(422, "A request type key is letters, digits and underscores (max 40)")
        c = ApprovalChain(org_id=org, request_type=key, name=f"{chains.type_label(key)} — default",
                          definition=chains.default_definition(), version=1, is_default=True, active=True, updated_by="system")
        db.add(c)
        await db.commit()
    if not c:
        raise HTTPException(404, "No workflow for that request type")
    return c


@router.get("/{request_type}")
async def get_chain(request_type: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    require_roles(request, VIEW_ROLES, "see approval workflows")
    return _out(await _chain(db, org, request_type))


class ChainIn(BaseModel):
    name: str | None = None
    description: str | None = None
    definition: dict
    active: bool = True


@router.put("/{request_type}")
async def save_chain(request_type: str, body: ChainIn, request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    require_roles(request, EDIT_ROLES, "change approval workflows")
    users = {u["id"]: u for u in await directory.org_users(org)}
    try:
        definition = chains.validate_definition(body.definition, users=users)
    except chains.ChainError as e:
        raise HTTPException(422, str(e))
    c = await _chain(db, org, request_type)
    before = {"name": c.name, "description": c.description, "definition": c.definition, "active": c.active, "version": c.version}
    changed = definition != (c.definition or {}) or (body.name or c.name) != c.name or bool(body.active) != bool(c.active) \
        or (body.description if body.description is not None else c.description) != c.description
    if definition != (c.definition or {}):
        c.version = (c.version or 1) + 1
    c.definition = definition
    c.is_default = definition == chains.default_definition()
    c.name = (body.name or c.name).strip()[:160]
    if body.description is not None:
        c.description = body.description.strip()[:600] or None
    c.active = bool(body.active)
    c.updated_by, c.updated_at = _actor(request), pm.now_utc()
    await db.commit()
    if changed:
        await audit_change(request, "approvals.workflow.save", entity_type="approval_workflow", entity_id=c.request_type,
                           before=before, after={"name": c.name, "description": c.description, "definition": c.definition,
                                                 "active": c.active, "version": c.version},
                           summary=f"Approval workflow '{c.name}' ({c.request_type}) saved as version {c.version}")
    return _out(c)


@router.post("/{request_type}/reset")
async def reset_chain(request_type: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    require_roles(request, EDIT_ROLES, "change approval workflows")
    c = await _chain(db, org, request_type)
    before = {"definition": c.definition, "version": c.version}
    if c.definition != chains.default_definition():
        c.version = (c.version or 1) + 1
    c.definition, c.is_default, c.active = chains.default_definition(), True, True
    c.updated_by, c.updated_at = _actor(request), pm.now_utc()
    await db.commit()
    await audit_change(request, "approvals.workflow.reset", entity_type="approval_workflow", entity_id=c.request_type,
                       before=before, after={"definition": c.definition, "version": c.version},
                       summary=f"Approval workflow {c.request_type} reset to the default (version {c.version})")
    return _out(c)


class SimulateIn(BaseModel):
    context: dict = {}
    definition: dict | None = None        # unsaved definition to try out; omitted = the saved one
    requester_employee_id: str | None = None


@router.post("/{request_type}/simulate")
async def simulate(request_type: str, body: SimulateIn, request: Request, db: AsyncSession = Depends(get_db)):
    """'Who would approve a $25,000 offer from Sales?' — resolves the chain against a context."""
    org = _org(request)
    require_roles(request, VIEW_ROLES, "see approval workflows")
    definition = body.definition
    if definition is not None:
        try:
            definition = chains.validate_definition(definition)
        except chains.ChainError as e:
            raise HTTPException(422, str(e))
    ctx = dict(body.context or {})
    if body.requester_employee_id:
        ctx["employee_id"] = body.requester_employee_id
    caller = {"approver_roles": ["org_admin", "hr"], "approver_manager_of_employee_id": ctx.get("employee_id")}
    resolved, chain = await ac.resolve_chain_run(org, request_type, chains.normalise_metadata(ctx), caller=caller, definition=definition)
    return {"request_type": request_type, "context": ctx, "auto_approved": resolved["auto_approved"],
            "auto_rule": resolved.get("auto_rule"), "skipped": resolved.get("skipped") or [],
            "steps": [{"name": s["name"], "mode": s["mode"], "approvers": [a.get("label") for a in s["approvers"]],
                       "escalate_after_hours": s.get("escalate_after_hours") or 0, "sla_hours": s.get("sla_hours") or 0}
                      for s in resolved["steps"]],
            "lines": chains.explain(resolved),
            "note": "The seeded default step 'Approver named by the screen' is shown here as 'Org admin / HR / manager' — "
                    "in a real request it is whoever the owning screen names." if not chain or chain.is_default else ""}


# ══════════════════════════ read-only resolve for ANY signed-in user (D3 "who approves?") ══
# GET /approvals/chains/resolve?request_type=expense&amount=25000&department=Sales[&days=3&entity=...]
#   → {request_type, context, auto_approved, auto_rule, steps: [{name, mode, approvers: [labels], escalate_after_hours,
#      sla_hours}], lines: [plain sentences], skipped: [...]} — resolved for the CALLER's own employee record
#      (their manager line, their department unless overridden). No definitions are returned.
# GET /approvals/chains?request_type=<t>   and   GET /approvals/chains/<t>   → the same resolve with no extra context.
public = APIRouter(prefix="/approvals/chains", tags=["approval-workflows"])
_ALIASES = {"days": "leave_days", "entity": "entity_id", "type": "claim_type", "dept": "department"}


async def _resolve_for_caller(request: Request, request_type: str, params: dict) -> dict:
    org = _org(request)
    key = (request_type or "").strip().lower()
    if not key:
        raise HTTPException(422, "request_type is required")
    ctx: dict = {}
    for k, v in (params or {}).items():
        if k in ("request_type", "kind") or v in (None, ""):
            continue
        ctx[_ALIASES.get(k, k)] = v
    me_emp = request.headers.get("x-employee-id") or ""
    if me_emp:
        ctx.setdefault("employee_id", me_emp)
        if "department" not in ctx:
            emp = next((e for e in await directory.org_employees(org) if e["id"] == me_emp), None)
            if emp and emp.get("department"):
                ctx["department"] = emp["department"]
    caller = {"approver_roles": ["org_admin", "hr"], "approver_manager_of_employee_id": ctx.get("employee_id")}
    resolved, chain = await ac.resolve_chain_run(org, key, chains.normalise_metadata(ctx), caller=caller)
    return {"request_type": key, "label": chains.type_label(key), "context": ctx, "chain_name": chain.name if chain else "Default",
            "is_default": (chain.is_default if chain else True), "auto_approved": resolved["auto_approved"],
            "auto_rule": resolved.get("auto_rule"), "skipped": resolved.get("skipped") or [],
            "steps": [{"name": s["name"], "mode": s["mode"], "approvers": [a.get("label") for a in s["approvers"]],
                       "escalate_after_hours": s.get("escalate_after_hours") or 0, "sla_hours": s.get("sla_hours") or 0}
                      for s in resolved["steps"]],
            "lines": chains.explain(resolved)}


@public.get("/resolve")
async def resolve_public(request: Request, request_type: str = ""):
    return await _resolve_for_caller(request, request_type, dict(request.query_params))


@public.get("")
async def resolve_public_list(request: Request, request_type: str = ""):
    if not request_type:
        return {"request_types": [{"key": k, "label": v["label"], "fields": v["fields"]} for k, v in chains.REQUEST_TYPES.items()]}
    return await _resolve_for_caller(request, request_type, dict(request.query_params))


@public.get("/{request_type}")
async def resolve_public_one(request_type: str, request: Request):
    return await _resolve_for_caller(request, request_type, dict(request.query_params))


@router.get("/{request_type}/runs")
async def runs(request_type: str, request: Request, limit: int = 50, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    require_roles(request, VIEW_ROLES, "see approval workflows")
    rows = (await db.execute(select(ApprovalRun).where(ApprovalRun.org_id == org, ApprovalRun.kind == request_type)
                             .order_by(ApprovalRun.started_at.desc()).limit(max(1, min(limit, 200))))).scalars().all()
    return {"items": [{**(rt.run_detail(r) or {}), "ref_id": r.ref_id, "item_id": r.item_id, "cycle": r.cycle,
                       "closed_at": pm.iso(r.closed_at)} for r in rows]}
