"""`/ai/attrition` — flight-risk signals. HR / admin see the company; a manager sees direct reports.

D7 additions:  GET /ai/attrition/calibration   factor exit-rates from this org's own leavers (≥ 50 exits), model status
               GET /ai/attrition/{employee_id}  one person: score, factors, "why" sentences
"""
import sys
from datetime import date

from fastapi import APIRouter, HTTPException, Request

sys.path.append("/app")
from shared.utils import directory  # noqa: E402
from shared.utils import platform_models as pm  # noqa: E402
from shared.utils.org_time import org_today  # noqa: E402

import attrition as A  # noqa: E402

router = APIRouter(prefix="/ai/attrition", tags=["attrition"])


def _who(request: Request) -> tuple[str, bool, str]:
    org = request.headers.get("x-org-id")
    role = request.headers.get("x-role", "") or ""
    me = request.headers.get("x-employee-id") or ""
    if not org:
        raise HTTPException(401, "Missing organization context")
    office = role in ("org_admin", "superadmin") or directory.role_in(role, ["hr"])
    if not office and role != "manager":
        raise HTTPException(403, "Flight-risk signals are for HR, org admins and managers")
    return org, office, me


@router.get("")
async def signals(request: Request, band: str | None = None, department: str | None = None):
    org, office, me = _who(request)
    today = await org_today(org)
    emps, ctx = await A.build_context(org, today)
    if not office:
        emps = [e for e in emps if me and str(e.get("manager_id") or "") == me]
        scope = "Your direct reports"
    else:
        scope = "Whole company"
    rows = [A.score_employee(e, ctx, today) for e in emps]
    if band:
        rows = [r for r in rows if r["band"] == band]
    if department:
        rows = [r for r in rows if (r.get("department") or "") == department]
    rows.sort(key=lambda r: (-r["score"], r["name"] or ""))
    bands = {"high": 0, "watch": 0, "low": 0}
    for r in rows:
        bands[r["band"]] += 1
    return {"as_of": pm.iso(pm.now_utc()), "scope": scope, "rows": rows, "bands": bands,
            "method": A.__doc__.strip(), "thresholds": A.DEFAULTS,
            "model": "rules only" + ("" if A.sklearn_available() else " (scikit-learn not installed)"),
            "label": "Heuristic — fixed points per rule, every factor shows the rows it came from. Not a prediction."}


@router.get("/calibration")
async def calibration(request: Request):
    """What this organisation's own exits say about each factor (descriptive; needs ≥ 50 exits on record)."""
    org, office, _ = _who(request)
    if not office:
        raise HTTPException(403, "Calibration is for HR and org admins")
    today = await org_today(org)
    hist = await A.calibration_history(org, today)
    return {"as_of": today.isoformat(), **A.calibrate(hist)}


@router.get("/{employee_id}")
async def one(employee_id: str, request: Request):
    org, office, me = _who(request)
    today = await org_today(org)
    emps, ctx = await A.build_context(org, today)
    e = next((x for x in emps if str(x.get("id")) == employee_id), None)
    if not e:
        raise HTTPException(404, "Employee not found or not active")
    if not office and str(e.get("manager_id") or "") != me:
        raise HTTPException(403, "Not one of your direct reports")
    r = A.score_employee(e, ctx, today)
    return {**r, "as_of": today.isoformat(), "thresholds": A.DEFAULTS, "model": "rules only"}
