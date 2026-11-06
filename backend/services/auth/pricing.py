"""Country preset + plan pricing for the Lexora HRM platform console.

Lexora HRM is CANADA-ONLY: one country preset (CA) drives currency (CAD), locale (en-CA),
the 13 provinces / territories and the default time zone.

Plans are priced PER EMPLOYEE PER MONTH (PEPM) in Canadian dollars with a monthly minimum,
add-ons, an optional free band for very small teams and an annual-prepay discount. GST/HST is
added on the invoice by the customer's billing province (sales_tax_invoice.py).

The numbers below are only the SEED. The live catalogue lives in the `billing_plan_catalog` /
`billing_addon_catalog` tables and is edited by the platform superadmin
(PUT /auth/billing/catalog), so nothing here is a contract.
"""
from datetime import date

from shared.utils.org_time import local_today

PROVINCES = ["ON", "BC", "AB", "QC", "MB", "SK", "NS", "NB", "NL", "PE", "NT", "NU", "YT"]

# ISO-2 → presentation + payroll metadata.
COUNTRY_META = {
    "CA": {
        "label": "Canada", "currency": "CAD", "symbol": "$", "locale": "en-CA",
        "tax_label": "GST/HST", "tax_id_label": "CRA Business Number", "region_label": "Province",
        "regions": PROVINCES, "timezone": "America/Toronto", "billing_model": "pepm",
    },
}

SUPPORTED_COUNTRIES = list(COUNTRY_META.keys())

# The plan MINIMUM per month (CAD) — older screens that read one number per plan stay sane.
PLAN_PRICING = {
    "CA": {"starter": 49, "growth": 99, "enterprise": 249},
}

# PEPM plan SEED (editable in DB). `pepm` CAD per active employee per month;
# `min_monthly` floor; `free_upto_employees` + `free_months` = launch free band;
# `annual_discount_pct` on annual prepay; `custom` = quoted.
PEPM_PLANS = {
    "CA": {
        "starter": {"label": "Starter", "pepm": 6, "min_monthly": 49, "custom": False,
                    "includes": "Core HR + Canadian payroll (CPP/QPP, EI, federal + provincial tax), pay stubs, T4 / RL-1, ROE",
                    "free_upto_employees": 5, "free_months": 3, "annual_discount_pct": 15,
                    "max_employees": None, "sort": 1},
        "growth": {"label": "Growth", "pepm": 9, "min_monthly": 99, "custom": False,
                   "includes": "Starter + time & attendance, leave and stat holidays, approvals, hiring, performance",
                   "free_upto_employees": 0, "free_months": 0, "annual_discount_pct": 15,
                   "max_employees": None, "sort": 2},
        "enterprise": {"label": "Enterprise", "pepm": 14, "min_monthly": 249, "custom": True,
                       "includes": "Growth + multi-province entities, SSO / SCIM, API, dedicated support — quoted (Ask Lexi assistant is in every plan; agents that execute are the Agentic operations add-on)",
                       "free_upto_employees": 0, "free_months": 0, "annual_discount_pct": 15,
                       "max_employees": None, "sort": 3},
    },
}

# Add-on SEED. kind = pepm (× headcount) | flat (per month) | one_time.
# ``agentic``: the agents that EXECUTE — Payroll Auditor, HR Desk — approval-gated, shadow mode
# first. Any plan. The Q&A assistant stays bundled in every plan; only execution is paid.
AGENTIC_ADDON_KEY = "agentic"
ADDONS = {
    "CA": {
        AGENTIC_ADDON_KEY: {"label": "Agentic operations — Payroll Auditor, HR Desk (agents that execute, approval-gated)",
                            "kind": "pepm", "price": 3, "waived_on_annual": False, "sort": 0},
        "implementation": {"label": "Implementation & past-data migration (one-time)",
                           "kind": "one_time", "price": 1500, "waived_on_annual": True, "sort": 1},
        "biometric": {"label": "Biometric punch import (ZKTeco / eSSL devices)", "kind": "pepm", "price": 1.5,
                      "waived_on_annual": False, "sort": 2},
        "multi_province": {"label": "Multi-province entities (separate CRA payroll accounts)", "kind": "flat",
                           "price": 49, "waived_on_annual": False, "sort": 3},
    },
}


def country_meta(country: str | None = None) -> dict:
    return COUNTRY_META["CA"]


def currency_for(country: str | None = None) -> str:
    return "CAD"


def price_for(country: str | None, plan: str | None) -> int:
    table = PLAN_PRICING["CA"]
    return table.get((plan or "growth"), table.get("growth", 0))


def is_pepm(country: str | None = None) -> bool:
    return True


def billing_anchor(today: date | None = None) -> date:
    """Headcount is counted on the 1st of the billing month."""
    d = today or local_today()
    return d.replace(day=1)


def _r2(x: float) -> float:
    return round(float(x) + 1e-9, 2)


def months_between(start: date, anchor: date) -> int:
    return (anchor.year - start.year) * 12 + (anchor.month - start.month)


def pepm_invoice(country: str, plan: str, active_employees: int, addons: list[str] | None = None, *,
                 plans: dict | None = None, addon_catalog: dict | None = None,
                 pepm_override: float | None = None, customer_province: str | None = None,
                 period: date | None = None, annual: bool = False, org_created: date | None = None,
                 charge_one_time: bool = False) -> dict:
    """Invoice preview. Pure arithmetic — unit-tested.

    plan line     = headcount × pepm, raised to the plan minimum
    free band     = $0 plan line while headcount ≤ free_upto and org age < free_months
    annual prepay = −annual_discount_pct on the plan line (billed monthly-equivalent)
    add-ons       = pepm × headcount | flat | one_time (only when charge_one_time,
                    and waived on annual prepay if the add-on says so)
    GST/HST       = by the customer's billing province (sales_tax_invoice.tax_split)
    """
    country = "CA"
    plans = plans or PEPM_PLANS["CA"]
    addon_catalog = addon_catalog if addon_catalog is not None else ADDONS["CA"]
    spec = plans.get(plan or "growth") or plans.get("growth") or next(iter(plans.values()))
    sym = COUNTRY_META["CA"]["symbol"]
    n = max(int(active_employees or 0), 0)
    pepm = float(pepm_override) if pepm_override is not None else float(spec["pepm"])
    raw = _r2(n * pepm)
    minimum = float(spec.get("min_monthly") or 0)
    plan_amount = max(raw, minimum)
    anchor = billing_anchor(period)
    free_upto = int(spec.get("free_upto_employees") or 0)
    free_months = int(spec.get("free_months") or 0)
    in_free_band = bool(free_upto and n <= free_upto and org_created and
                        months_between(org_created, anchor) < free_months)
    discount_pct = float(spec.get("annual_discount_pct") or 0) if annual else 0.0
    lines = [{
        "code": "plan",
        "label": f"{spec['label']} plan — {n} active employee(s) × {sym}{pepm:g} PEPM",
        "qty": n, "unit": pepm, "amount": 0.0 if in_free_band else _r2(plan_amount),
        "minimum_applied": (raw < minimum) and not in_free_band, "minimum": minimum,
        "free_band": in_free_band,
    }]
    if in_free_band:
        lines[0]["note"] = f"Free: {free_upto} or fewer employees for the first {free_months} months"
    if discount_pct and not in_free_band:
        lines.append({"code": "annual_discount", "label": f"Annual prepay discount {discount_pct:g}%",
                      "qty": 1, "unit": -_r2(plan_amount * discount_pct / 100),
                      "amount": -_r2(plan_amount * discount_pct / 100)})
    for key in addons or []:
        a = addon_catalog.get(key)
        if not a:
            continue
        kind = a.get("kind", "flat")
        if kind == "pepm":
            lines.append({"code": key, "label": f"{a['label']} — {n} × {sym}{float(a['price']):g}", "qty": n,
                          "unit": float(a["price"]), "amount": _r2(n * float(a["price"]))})
        elif kind == "one_time":
            if not charge_one_time:
                continue
            waived = annual and bool(a.get("waived_on_annual"))
            lines.append({"code": key, "label": a["label"] + (" — waived (annual prepay)" if waived else ""),
                          "qty": 1, "unit": float(a["price"]), "amount": 0.0 if waived else _r2(a["price"]),
                          "one_time": True, "waived": waived})
        else:
            lines.append({"code": key, "label": a["label"], "qty": 1, "unit": float(a["price"]),
                          "amount": _r2(a["price"])})
    subtotal = _r2(sum(l["amount"] for l in lines))
    import sales_tax_invoice
    split = sales_tax_invoice.tax_split(subtotal, (customer_province or "").upper() or None)
    tax_lines = [{"code": t["code"], "label": t["label"], "amount": t["amount"]} for t in split["tax_lines"]]
    gst = split["gst"]
    return {
        "country": country, "currency": "CAD", "plan": plan, "plan_label": spec["label"],
        "custom_quote": bool(spec.get("custom")), "period": anchor.isoformat(),
        "period_label": anchor.strftime("%B %Y"), "headcount_as_of": anchor.isoformat(),
        "active_employees": n, "pepm": pepm, "billing_cycle": "annual" if annual else "monthly",
        "free_band": in_free_band, "annual_discount_pct": discount_pct,
        "lines": lines, "subtotal": subtotal, "tax_rate": split["rate_pct"], "tax_lines": tax_lines,
        "gst": gst, "total": _r2(subtotal + gst),
        "tax_province": split["province"], "tax_determined": split["supply"] != "undetermined",
        "seller_tax_configured": sales_tax_invoice.seller()["configured"],
    }
