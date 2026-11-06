"""Canada statutory figures for the Payroll Auditor — taken from the PAYROLL ENGINE, never typed here.

The auditor re-checks CPP / CPP2 (QPP / QPP2 in Quebec), EI (the reduced Quebec EI rate), QPIP, the YTD maximums,
the YMPE / YAMPE band and the vacation-pay minimum. Every one of those figures comes from one of three places, in
this order, and the pack says which one was used:

1. the engine's own constants modules (``services/payroll/constants_ca.py`` + ``constants_qc.py``) loaded by file
   path when they are on disk (monorepo, tests, a combined image) — exactly the numbers ``calc.py`` uses;
2. the payroll service's Verification Center (``GET /payroll/compliance``) — the same constants rendered with their
   verified / estimated status (rest of Canada only; Quebec constants are not published there);
3. nothing — the statutory legs are then SKIPPED with the reason, never guessed.

Vacation-pay minimums: the org's own signed value ``vacation.<PROV>.rule`` (Payroll → Statutory values) wins; else
the engine's code reference ``constants_ca.VACATION_PAY`` (ON / BC / AB only); else the minimum is unknown for that
province and only the employee's own rate is checked.
"""
from __future__ import annotations

import importlib.util
import logging
import os
from pathlib import Path

log = logging.getLogger("lexora.agentic.payroll")

HERE = Path(__file__).resolve().parent
ENGINE_SOURCE = "payroll engine constants (constants_ca.py · constants_qc.py)"
HTTP_SOURCE = "payroll service Verification Center (GET /payroll/compliance)"
PROVINCES = ("AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT")
_CACHE: dict = {}


def _candidate_dirs() -> list[Path]:
    out = []
    env = (os.getenv("PAYROLL_ENGINE_DIR") or "").strip()
    if env:
        out.append(Path(env))
    out.append(HERE.parent.parent.parent / "payroll")            # backend/services/payroll (monorepo / tests)
    out.append(Path("/app/services/payroll"))                      # a combined image
    return out


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)                                   # both modules are plain data (no imports)
    return mod


def engine_modules() -> tuple | None:
    """(constants_ca, constants_qc) modules of the payroll engine, or None when they are not on disk."""
    if "mods" in _CACHE:
        return _CACHE["mods"]
    mods = None
    for d in _candidate_dirs():
        ca, qc = d / "constants_ca.py", d / "constants_qc.py"
        if ca.is_file() and qc.is_file():
            try:
                mods = (_load(ca, "lexora_pa_engine_constants_ca"), _load(qc, "lexora_pa_engine_constants_qc"))
                break
            except Exception as e:                                 # noqa: BLE001
                log.info("payroll engine constants not loadable from %s: %s", d, e)
    _CACHE["mods"] = mods
    return mods


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _roc_from(t: dict) -> dict:
    return {"cpp": dict(t.get("cpp") or {}), "cpp2": dict(t.get("cpp2") or {}), "ei": dict(t.get("ei") or {})}


def _tiers_from_code(rows) -> list[list[float]]:
    """constants_ca.VACATION_PAY rows are (upper_years_exclusive, rate) → [[from_years, percent], …]."""
    out, lower = [], 0.0
    for upper, rate in rows or []:
        out.append([float(lower), round(float(rate) * 100, 4)])
        lower = float(upper)
    return out


def from_engine(year: int) -> dict | None:
    mods = engine_modules()
    if not mods:
        return None
    ca, qc = mods
    tables = dict(getattr(ca, "_TABLES", {}) or {})
    if not tables:
        for y in range(year - 3, year + 2):
            try:
                tables[y] = ca.stat_tables(y)
            except Exception:                                      # noqa: BLE001
                continue
    t = tables.get(year)
    qct = getattr(qc, "STAT_QC_2026", None)
    vac = {p: {"tiers": _tiers_from_code(rows), "source": "engine code reference constants_ca.VACATION_PAY", "verified": False}
           for p, rows in (getattr(ca, "VACATION_PAY", {}) or {}).items()}
    return {"source": ENGINE_SOURCE, "year": year, "editions": sorted(tables), "edition_found": t is not None,
            "roc": _roc_from(t) if t else None,
            # the engine has ONE Quebec edition (2026); it applies when the rest-of-Canada edition for the year exists
            "qc": ({"qpp": dict(qct["qpp"]), "qpp2": dict(qct["qpp2"]), "qpip": dict(qct["qpip"]), "ei_qc": dict(qct["ei_qc"])}
                   if (qct and t) else None),
            "verified": {}, "vacation_min": vac}


def from_compliance(report: dict | None, year: int) -> dict | None:
    """Parse the Verification Center payload (rows key / value / status) into the same shape."""
    if not report or not isinstance(report.get("constants"), list):
        return None
    vals, verified = {}, {}
    for r in report["constants"]:
        k = str(r.get("key") or "")
        if k.split(".")[0] in ("cpp", "cpp2", "ei"):
            v = _f(r.get("value"))
            if v is not None:
                vals[k] = v
            verified[k] = (r.get("status") == "verified")
    try:
        edition = int(str(report.get("as_of_edition") or "")[:4])
    except ValueError:
        edition = None
    roc = {"cpp": {}, "cpp2": {}, "ei": {}}
    for k, v in vals.items():
        grp, _, name = k.partition(".")
        roc[grp][name] = v
    ok = edition == year and all(roc[g] for g in roc)
    return {"source": HTTP_SOURCE, "year": year, "editions": [edition] if edition else [], "edition_found": ok,
            "roc": roc if ok else None, "qc": None, "verified": verified, "vacation_min": {}}


def merge_verified(block: dict, report: dict | None) -> dict:
    """Engine figures + the Verification Center's verified flags (when it answered)."""
    if report and isinstance(report.get("constants"), list):
        block["verified"] = {str(r.get("key")): (r.get("status") == "verified") for r in report["constants"] if r.get("key")}
    return block


def merge_org_vacation(block: dict, org_rules: dict) -> dict:
    """Org-signed vacation rules ``{PROV: {"tiers": [{after_years, percent}], "source"}}`` replace the code reference."""
    vm = dict(block.get("vacation_min") or {})
    for p, r in (org_rules or {}).items():
        tiers = []
        for t in (r.get("tiers") or []):
            a, pc = _f((t or {}).get("after_years")), _f((t or {}).get("percent"))
            if a is not None and pc is not None and pc > 0:
                tiers.append([a, pc])
        if tiers:
            vm[p.upper()] = {"tiers": sorted(tiers), "source": r.get("source") or "org value (Payroll → Statutory values)", "verified": bool(r.get("verified"))}
    block["vacation_min"] = vm
    return block


def empty(year: int, why: str) -> dict:
    return {"source": f"unavailable — {why}", "year": year, "editions": [], "edition_found": False, "roc": None, "qc": None,
            "verified": {}, "vacation_min": {}}


# ══════════════════════════ the plan an employee is on ══════════════════════════

def plan_for(province: str | None, stat: dict) -> dict | None:
    """The contribution plan for one line (rates + maximums), or None when the figures are unavailable.
    Quebec: QPP / QPP2 / QPIP and the reduced Quebec EI rate; every other province: CPP / CPP2 / EI.
    A maximum the engine leaves at 0 (not yet confirmed) is derived the way calc.py derives it."""
    prov = (province or "").upper()
    if prov == "QC":
        q = stat.get("qc")
        if not q:
            return None
        qpp, qpp2, qpip, ei = q["qpp"], q["qpp2"], q["qpip"], q["ei_qc"]

        def cap(d, key, fallback):
            v = _f(d.get(key)) or 0.0
            return v if v > 0 else fallback
        return {"plan": "QPP", "label_pension": "QPP", "label_pension2": "QPP2", "label_ei": "EI (Quebec rate)",
                "rate": float(qpp["rate"]), "basic_exemption": float(qpp["basic_exemption"]), "ympe": float(qpp["ympe"]),
                "max": cap(qpp, "max_employee", (float(qpp["ympe"]) - float(qpp["basic_exemption"])) * float(qpp["rate"])),
                "rate2": float(qpp2["rate"]), "yampe": float(qpp2["yampe"]),
                "max2": cap(qpp2, "max_employee", (float(qpp2["yampe"]) - float(qpp["ympe"])) * float(qpp2["rate"])),
                "ei_rate": float(ei["rate"]), "ei_max": cap(ei, "max_employee", float(ei["mie"]) * float(ei["rate"])),
                "qpip_rate": float(qpip["rate"]), "qpip_max": cap(qpip, "max_employee", float(qpip["max_insurable"]) * float(qpip["rate"])),
                "keys": {"rate": "qpp.rate", "ympe": "qpp.ympe", "max": "qpp.max_employee", "rate2": "qpp2.rate", "yampe": "qpp2.yampe",
                         "max2": "qpp2.max_employee", "ei_rate": "ei_qc.rate", "ei_max": "ei_qc.max_employee", "qpip_rate": "qpip.rate",
                         "qpip_max": "qpip.max_employee"}}
    r = stat.get("roc")
    if not r or not r.get("cpp") or not r.get("ei"):
        return None
    cpp, cpp2, ei = r["cpp"], r.get("cpp2") or {}, r["ei"]
    return {"plan": "CPP", "label_pension": "CPP", "label_pension2": "CPP2", "label_ei": "EI",
            "rate": float(cpp["rate"]), "basic_exemption": float(cpp["basic_exemption"]), "ympe": float(cpp["ympe"]),
            "max": float(cpp["max_employee"]), "rate2": float(cpp2.get("rate") or 0), "yampe": float(cpp2.get("yampe") or 0),
            "max2": float(cpp2.get("max_employee") or 0), "ei_rate": float(ei["rate"]), "ei_max": float(ei["max_employee"]),
            "qpip_rate": None, "qpip_max": None,
            "keys": {"rate": "cpp.rate", "ympe": "cpp.ympe", "max": "cpp.max_employee", "rate2": "cpp2.rate", "yampe": "cpp2.yampe",
                     "max2": "cpp2.max_employee", "ei_rate": "ei.rate", "ei_max": "ei.max_employee"}}


def is_verified(stat: dict, *keys: str) -> bool:
    v = stat.get("verified") or {}
    return bool(keys) and all(bool(v.get(k)) for k in keys)


def vacation_minimum(stat: dict, province: str | None, years: float) -> tuple[float | None, dict | None]:
    """(minimum percent, rule) for a province and completed years of service; (None, None) when unknown."""
    rule = (stat.get("vacation_min") or {}).get((province or "").upper())
    if not rule or not rule.get("tiers"):
        return None, None
    pct = None
    for after, p in sorted(rule["tiers"]):
        if years >= after:
            pct = p
    return pct, rule
