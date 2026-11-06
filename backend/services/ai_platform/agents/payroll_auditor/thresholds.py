"""Payroll Auditor thresholds — org-editable, with presets.

Two sources, merged in this order:
1. the foundation's ``ai_agentic.thresholds`` (``net_delta_pct``, ``net_delta_floor_cad``, ``bulk_employees``, chase
   cadence …) — shared by every agent;
2. ``ai_agentic.payroll_auditor`` — this agent's own keys (preset, lead days, CAD floors …), saved through
   ``save_payroll_thresholds`` (an org admin; the router checks). A partial save never loses a default.

Presets: ``tight`` 2 % · ``normal`` 5 % · ``loose`` 10 % (about 2 % floods the list, about 10 % misses errors).
The preset sets ``net_delta_pct`` unless the org typed its own percentage. A line whose direct-deposit details
changed since the previous run uses HALF the percentage and no dollar floor.
These are review thresholds, not statutory figures.
"""
from __future__ import annotations

import copy
import sys

sys.path.append("/app")

import agentic_state as ST  # noqa: E402

PRESETS = {"tight": 2.0, "normal": 5.0, "loose": 10.0}

DEFAULTS: dict = {
    "preset": "normal",                 # tight | normal | loose | custom
    "lead_days": 3,                     # T-N: the scheduler starts the run N days before the pay date
    "approval_lead_days": 1,            # T-1: variance review + approval expected by then (timing KPI)
    "explanation_hours": 4,             # explanations due within N hours of the draft (KPI)
    "cost_centre_pct": 5.0,             # cost-centre roll-up change above this needs a written reason
    "deduction_floor_cad": 25.0,        # CPP / EI / QPIP / income-tax lines: ignore moves smaller than this ($)
    "tax_delta_pct": 20.0,              # income tax moved more than this % with no TD1 change
    "tax_floor_cad": 10.0,              # …and by at least this many dollars
    "rate_tolerance_cad": 1.00,         # CPP / EI / QPIP recomputed vs deducted: rounding + 27-pay-year tolerance ($)
    "vacation_tolerance_cad": 0.05,     # vacation accrued vs vacationable × rate
    "off_cycle_runs_per_quarter": 1,    # KPI: off-cycle runs per quarter
    "bank_change_pct_factor": 0.5,      # variance % multiplier when the direct-deposit details changed
    "chase_employee_hours": 24,
    "chase_manager_hours": 48,
    "chase_hr_hours": 24,
}

SAVABLE = tuple(DEFAULTS.keys())
INT_KEYS = ("lead_days", "approval_lead_days", "off_cycle_runs_per_quarter")


def effective(state: dict) -> dict:
    """The thresholds a run uses, from the org's agentic state (``agentic_state(org)`` result)."""
    base = {k: v for k, v in (state.get("thresholds") or {}).items()}
    own = copy.deepcopy(DEFAULTS)
    own.update({k: v for k, v in (state.get("payroll_auditor") or {}).items() if k in DEFAULTS})
    preset = own.get("preset") if own.get("preset") in PRESETS or own.get("preset") == "custom" else "normal"
    pct = float(base.get("net_delta_pct") or 5)
    if preset in PRESETS:
        pct = PRESETS[preset]
    out = {**base, **own, "preset": preset, "net_delta_pct": pct,
           "net_delta_floor_cad": float(base.get("net_delta_floor_cad") if base.get("net_delta_floor_cad") is not None else 1000),
           "bulk_employees": int(base.get("bulk_employees") or 25),
           "bank_change_pct": round(pct * float(own.get("bank_change_pct_factor") or 0.5), 3)}
    for k in INT_KEYS:
        out[k] = int(out.get(k) if out.get(k) is not None else DEFAULTS[k])
    return out


def clean(patch: dict, current: dict) -> dict:
    """Validate an admin's save of the agent's own keys."""
    out = {k: current.get(k, DEFAULTS[k]) for k in DEFAULTS}
    for k, v in (patch or {}).items():
        if k not in DEFAULTS:
            continue
        if k == "preset":
            if v not in PRESETS and v != "custom":
                raise ValueError("preset must be tight, normal, loose or custom")
            out[k] = v
            continue
        try:
            n = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"{k} must be a number")
        if n < 0:
            raise ValueError(f"{k} cannot be negative")
        if k.endswith("_pct") and n > 100:
            raise ValueError(f"{k} is a percentage (0–100)")
        if k in ("lead_days", "approval_lead_days") and n > 28:
            raise ValueError(f"{k} must be 0–28 days")
        out[k] = int(n) if n == int(n) else n
    return out


async def save_payroll_thresholds(org: str, patch: dict, *, actor_id: str | None, actor_email: str | None) -> dict:
    cur = await ST.raw_settings(org)
    cur["payroll_auditor"] = clean(patch or {}, cur.get("payroll_auditor") or {})
    if (patch or {}).get("net_delta_pct") is not None:                       # a custom % goes to the shared key
        try:
            pct = float(patch["net_delta_pct"])
        except (TypeError, ValueError):
            raise ValueError("net_delta_pct must be a number")
        if pct < 0 or pct > 100:
            raise ValueError("net_delta_pct is a percentage (0–100)")
        cur.setdefault("thresholds", {})["net_delta_pct"] = pct
        cur["payroll_auditor"]["preset"] = "custom"
    await ST._write(org, cur, actor_id, actor_email)
    return cur["payroll_auditor"]
