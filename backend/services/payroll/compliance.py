"""Lexora HRM payroll — compliance registry.

The audit trail for the "verify before real pay" gate. Every statutory figure the
engine uses is listed here with the CRA source where it must be confirmed and a
verification status. As each figure is checked against the primary source, flip
its status to "verified" (and set verified_on). `compliance_report()` rolls this
up so the app can show exactly how much is still estimated.

This does NOT change any calculation — it makes the compliance state auditable and
visible instead of scattered across `VERIFY` comments in constants_ca.py.
"""
from constants_ca import STAT_2026
from constants_qc import STAT_QC_2026

_T4127 = "CRA T4127 — Payroll Deductions Formulas (2026 edition)"
_CPP = "CRA — CPP contribution rates, maximums and exemptions (2026)"
_EI = "CRA — EI premium rates and maximums (2026)"
_ON = "CRA T4127 Ch. Ontario + Ontario surtax / health premium tables"

# One row per statutory figure. `value` is pulled live from constants_ca so the
# registry can never drift from what the engine actually uses.
_c, _c2, _ei, _f = STAT_2026["cpp"], STAT_2026["cpp2"], STAT_2026["ei"], STAT_2026["federal"]


def _rows():
    rows = [
        ("cpp.rate", "CPP contribution rate", _c["rate"], _CPP),
        ("cpp.base_rate", "CPP base rate (earns K2 credit)", _c["base_rate"], _T4127),
        ("cpp.enhanced_rate", "CPP enhanced rate (income deduction)", _c["enhanced_rate"], _T4127),
        ("cpp.ympe", "Year's Max Pensionable Earnings (YMPE)", _c["ympe"], _CPP),
        ("cpp.basic_exemption", "CPP basic exemption", _c["basic_exemption"], _CPP),
        ("cpp.max_employee", "CPP max employee contribution", _c["max_employee"], _CPP),
        ("cpp2.rate", "CPP2 rate", _c2["rate"], _CPP),
        ("cpp2.yampe", "Year's Additional Max Pensionable Earnings (YAMPE)", _c2["yampe"], _CPP),
        ("cpp2.max_employee", "CPP2 max employee contribution", _c2["max_employee"], _CPP),
        ("ei.rate", "EI premium rate", _ei["rate"], _EI),
        ("ei.mie", "Max Insurable Earnings (MIE)", _ei["mie"], _EI),
        ("ei.employer_multiplier", "EI employer multiplier", _ei["employer_multiplier"], _EI),
        ("ei.max_employee", "EI max employee premium", _ei["max_employee"], _EI),
        ("federal.bpa", "Federal Basic Personal Amount (max)", _f["bpa"], _T4127),
        # federal.bpa_min / bpa_phaseout are NOT tracked here: CRA withholds the full
        # BPA at source (verified vs PDOC $230k/$300k); the phase-out is a T1 item.
        ("federal.cea", "Canada Employment Amount", _f["cea"], _T4127),
        ("federal.lowest_rate", "Federal lowest tax rate (credit rate)", _f["lowest_rate"], _T4127),
        ("federal.brackets", "Federal tax brackets", _f["brackets"], _T4127),
    ]
    for prov in ("ON", "BC", "AB"):
        p = STAT_2026[prov]
        rows.append((f"{prov}.bpa", f"{prov} Basic Personal Amount", p["bpa"], _T4127))
        rows.append((f"{prov}.lowest_rate", f"{prov} lowest tax rate", p["lowest_rate"], _T4127))
        rows.append((f"{prov}.brackets", f"{prov} tax brackets", p["brackets"], _T4127))
    rows.append(("ON.surtax", "Ontario surtax thresholds/rates", STAT_2026["ON"]["surtax"], _ON))
    rows.append(("ON.health_premium", "Ontario Health Premium schedule", "piecewise (see constants_ca)", _ON))
    return rows


# Verification ledger — status per figure. Everything starts "estimated"; an
# operator flips a key to "verified" here once confirmed against the source.
# Format: key -> {"status": "verified", "verified_on": "2026-01-15", "by": "name"}
_PDOC = {"status": "verified", "verified_on": "2026-08-26", "by": "CRA PDOC reconciliation (9 cases)"}
VERIFIED: dict = {
    # Confirmed by the 9-case CRA PDOC reconciliation (ON/BC/AB, weekly/biweekly/
    # monthly, $28k-$145k, below/above YMPE, both ON surtax tiers, bonus method).
    # Each figure below is exercised by a case that matched CRA to the cent, or is
    # displayed directly by PDOC. See pdoc_cases.py + verify_pdoc.py.
    "cpp.rate": _PDOC, "cpp.base_rate": _PDOC, "cpp.enhanced_rate": _PDOC,
    "cpp.ympe": _PDOC, "cpp.basic_exemption": _PDOC, "cpp.max_employee": _PDOC,
    "cpp2.rate": _PDOC, "cpp2.yampe": _PDOC, "cpp2.max_employee": _PDOC,
    "ei.rate": _PDOC, "ei.mie": _PDOC, "ei.employer_multiplier": _PDOC, "ei.max_employee": _PDOC,
    "federal.bpa": _PDOC, "federal.cea": _PDOC, "federal.lowest_rate": _PDOC,
    "federal.brackets": _PDOC,   # all 5 bands: cases A ($230k, br4) + B ($300k, br5)
    "ON.bpa": _PDOC, "ON.lowest_rate": _PDOC, "ON.surtax": _PDOC, "ON.health_premium": _PDOC,
    "ON.brackets": _PDOC,        # all bands: C5 ($145k) + A ($230k) + B ($300k)
    "BC.bpa": _PDOC, "BC.lowest_rate": _PDOC,
    "BC.brackets": _PDOC,        # all 7 BC bands: C3 ($78k) + Case C ($300k)
    "AB.bpa": _PDOC, "AB.lowest_rate": _PDOC,
    "AB.brackets": _PDOC,        # all 6 AB bands: C4 ($56k) + Case D ($400k)
    # ALL 28 withholding constants verified vs CRA PDOC (14 cases, 2026-08-26).
    # Out of scope (not withholding constants): federal BPA phase-out (T1 item),
    # Quebec/other provinces, 27-pay biweekly year.
}


def compliance_report() -> dict:
    """Roll-up of the statutory-constant verification state."""
    out = []
    for key, label, value, source in _rows():
        v = VERIFIED.get(key, {})
        out.append({
            "key": key, "label": label,
            "value": str(value), "source": source,
            "status": v.get("status", "estimated"),
            "verified_on": v.get("verified_on"), "by": v.get("by"),
        })
    verified = sum(1 for c in out if c["status"] == "verified")
    total = len(out)
    return {
        "total": total, "verified": verified, "estimated": total - verified,
        "ready_for_real_pay": verified == total,
        "as_of_edition": "2026",
        "constants": out,
    }


def pdoc_status() -> dict:
    """How many CRA-PDOC golden cases have confirmed expected values."""
    try:
        from pdoc_cases import CASES
    except Exception:
        return {"total": 0, "confirmed": 0, "pending": 0}
    confirmed = sum(1 for c in CASES if c.get("pdoc"))
    return {"total": len(CASES), "confirmed": confirmed, "pending": len(CASES) - confirmed}


def gate_status() -> dict:
    """The 'verify before real pay' gate. OPEN until every statutory constant is
    verified against CRA AND every PDOC golden case is confirmed. A real pay run is
    blocked while the gate is open (test runs must acknowledge it explicitly)."""
    c = compliance_report()
    p = pdoc_status()
    reasons = []
    if c["verified"] < c["total"]:
        reasons.append(f"{c['estimated']} of {c['total']} statutory constants still ESTIMATED — verify against CRA T4127 (125th ed.) + CPP/EI primary tables")
    if p["confirmed"] < p["total"]:
        reasons.append(f"{p['pending']} of {p['total']} CRA-PDOC golden cases NOT confirmed — run each in CRA PDOC and fill pdoc_cases.py")
    gate_open = bool(reasons)
    return {
        "gate_open": gate_open, "ready_for_real_pay": not gate_open,
        "constants_verified": c["verified"], "constants_total": c["total"],
        "pdoc_confirmed": p["confirmed"], "pdoc_total": p["total"],
        "reasons": reasons,
    }


# ═══ QUEBEC compliance registry ══════════════════════════════════════════════
# The QC analogue of the ROC registry above. QC runs its OWN statutory system
# (QPP/QPP2, QPIP, reduced EI, Québec provincial tax, 16.5% federal abatement) and
# is verified against Revenu Québec WebRAS (provincial) + CRA PDOC-Quebec (federal),
# NOT PDOC alone. Every STAT_QC_2026 figure is listed with its source; only the
# figures a WebRAS-displayed value or a verify_qc.py case actually confirmed are
# marked verified — the rest stay ESTIMATED so the QC gate stays OPEN. This does
# NOT change any calculation; it makes the QC compliance state auditable.
_WEBRAS = "Revenu Québec WebRAS 2026.01 (TP-1015 withholding formulas)"
_RQ_QPP = "Retraite Québec — QPP rates, maximums and exemptions (2026)"
_RQ_QPIP = "Revenu Québec — QPIP (RQAP) premium rates and max insurable (2026)"
_RQ_EI = "CRA — EI premium rate/MIE (2026), REDUCED Quebec rate"
_RQ_TAX = "Revenu Québec — TP-1015 provincial tax brackets / personal amounts (2026)"
_PDOC_QC = "CRA PDOC — Quebec (federal tax with 16.5% abatement + reduced EI)"

_q = STAT_QC_2026
_qpp, _qpp2, _qpip = _q["qpp"], _q["qpp2"], _q["qpip"]
_eiq, _qded, _qtax = _q["ei_qc"], _q["qc_employment_deduction"], _q["qc_tax"]


def _qc_rows():
    """One row per Québec statutory figure, values pulled live from STAT_QC_2026 so
    the registry can never drift from what calculate_paystub_qc actually uses."""
    return [
        ("qpp.rate", "QPP contribution rate", _qpp["rate"], _RQ_QPP),
        ("qpp.base_rate", "QPP base rate (earns the tax credit)", _qpp["base_rate"], _WEBRAS),
        ("qpp.enhanced_rate", "QPP enhanced rate (income deduction)", _qpp["enhanced_rate"], _WEBRAS),
        ("qpp.basic_exemption", "QPP basic exemption", _qpp["basic_exemption"], _WEBRAS),
        ("qpp.ympe", "Year's Max Pensionable Earnings (YMPE)", _qpp["ympe"], _RQ_QPP),
        ("qpp.max_employee", "QPP max employee contribution", _qpp["max_employee"], _RQ_QPP),
        ("qpp2.rate", "QPP2 rate (YMPE→YAMPE band)", _qpp2["rate"], _RQ_QPP),
        ("qpp2.yampe", "Year's Additional Max Pensionable Earnings (YAMPE)", _qpp2["yampe"], _RQ_QPP),
        ("qpp2.max_employee", "QPP2 max employee contribution", _qpp2["max_employee"], _RQ_QPP),
        ("qpip.rate", "QPIP employee premium rate", _qpip["rate"], _WEBRAS),
        ("qpip.employer_rate", "QPIP employer premium rate", _qpip.get("employer_rate"), _RQ_QPIP),
        ("qpip.max_insurable", "QPIP max insurable earnings", _qpip["max_insurable"], _RQ_QPIP),
        ("qpip.max_employee", "QPIP max employee premium", _qpip["max_employee"], _RQ_QPIP),
        ("ei_qc.rate", "EI premium rate (reduced Quebec rate)", _eiq["rate"], _PDOC_QC),
        ("ei_qc.mie", "EI Max Insurable Earnings (MIE)", _eiq["mie"], _RQ_EI),
        ("ei_qc.employer_multiplier", "EI employer multiplier", _eiq["employer_multiplier"], _RQ_EI),
        ("ei_qc.max_employee", "EI max employee premium", _eiq["max_employee"], _RQ_EI),
        ("qc_employment_deduction.max_annual", "Québec deduction for employment income (annual cap)",
         _qded["max_annual"], _WEBRAS),
        # qc_employment_deduction.rate is NOT tracked: WebRAS displays the CAPPED
        # amount ($1,450.02/yr) which binds well below the incomes tested, so the 6%
        # rate is never exercised — the confirmed figure is the cap, not the rate.
        ("qc_tax.bpa", "Québec Basic Personal Amount", _qtax["bpa"], _WEBRAS),
        ("qc_tax.lowest_rate", "Québec lowest tax rate (personal-credit rate)", _qtax["lowest_rate"], _RQ_TAX),
        ("qc_tax.br1_threshold", "Québec tax bracket-1 upper threshold", _qtax["brackets"][0][0], _WEBRAS),
        ("qc_tax.brackets_upper", "Québec upper tax brackets (br2+ thresholds & rates)",
         _qtax["brackets"][1:], _RQ_TAX),
        ("federal_qc_abatement", "Federal Quebec abatement (reduces federal tax)",
         _q["federal_qc_abatement"], _PDOC_QC),
    ]


# Verification ledger — mark ONLY the WebRAS-displayed / verify_qc.py-confirmed
# figures verified. Everything else stays estimated (gate stays OPEN). NEVER
# fabricate a Revenu Québec / CRA constant — flip a key here only once a case in
# verify_qc.py or a WebRAS-displayed value confirms it.
_QCW = {"status": "verified", "verified_on": "2026-08-27",
        "by": "Revenu Québec WebRAS 2026.01 (verify_qc.py cases 1-2, $40k + $65k biweekly)"}
_QCP = {"status": "verified", "verified_on": "2026-08-27",
        "by": "CRA PDOC-Quebec ($65k biweekly, verify_qc.py case 2)"}
QC_VERIFIED: dict = {
    # QPP employee-side split — confirmed by WebRAS ($65k: 149.02 = (2500−134.62)×6.3%,
    # enhanced deduction 1%, exemption/26 = 134.62). Base = rate − enhanced.
    "qpp.rate": _QCW, "qpp.base_rate": _QCW, "qpp.enhanced_rate": _QCW,
    "qpp.basic_exemption": _QCW,
    # QPIP employee rate — WebRAS ($65k: 10.75 = 2500 × 0.43%).
    "qpip.rate": _QCW,
    # Reduced Quebec EI rate — PDOC-Quebec ($65k: 32.50 = 2500 × 1.30%).
    "ei_qc.rate": _QCP,
    # Québec provincial tax — WebRAS-displayed BPA; bracket-1 threshold pinned by the
    # $40k (bracket 1) + $65k (bracket 2 solve) cases; employment-deduction annual cap
    # (55.77/period × 26 = 1450.02).
    "qc_tax.bpa": _QCW, "qc_tax.br1_threshold": _QCW,
    "qc_employment_deduction.max_annual": _QCW,
    # Federal 16.5% Quebec abatement — PDOC-Quebec ($65k: fed 201.29 matched abated).
    "federal_qc_abatement": _QCP,
    # STILL ESTIMATED (NOT marked): qpp.ympe, qpp.max_employee, qpp2.*, qpip.employer_rate,
    # qpip.max_insurable, qpip.max_employee, ei_qc.mie, ei_qc.employer_multiplier,
    # ei_qc.max_employee, qc_tax.lowest_rate, qc_tax.brackets_upper — need high-income /
    # QPP2-active WebRAS cases (see the pending rows in qc_webras_cases.py).
}


def qc_compliance_report() -> dict:
    """Roll-up of the Québec statutory-constant verification state (mirrors
    compliance_report)."""
    out = []
    for key, label, value, source in _qc_rows():
        v = QC_VERIFIED.get(key, {})
        out.append({
            "key": key, "label": label,
            "value": str(value), "source": source,
            "status": v.get("status", "estimated"),
            "verified_on": v.get("verified_on"), "by": v.get("by"),
        })
    verified = sum(1 for c in out if c["status"] == "verified")
    total = len(out)
    return {
        "total": total, "verified": verified, "estimated": total - verified,
        "ready_for_real_pay": verified == total,
        "as_of_edition": "2026",
        "constants": out,
    }


def qc_webras_status() -> dict:
    """How many Québec verification cases have a recorded WebRAS result (confirmed)."""
    try:
        from qc_webras_cases import CASES
    except Exception:
        return {"total": 0, "confirmed": 0, "pending": 0}
    confirmed = sum(1 for c in CASES if c.get("webras") is not None)
    return {"total": len(CASES), "confirmed": confirmed, "pending": len(CASES) - confirmed}


def qc_gate_status() -> dict:
    """The Québec 'verify before real pay' gate (mirrors gate_status). OPEN until
    every QC statutory constant is confirmed against Revenu Québec WebRAS / PDOC-QC
    AND every qc_webras_case has a recorded WebRAS result. A real QC pay run is
    blocked while the gate is open (test runs must acknowledge it explicitly).
    Correctly OPEN today — most QC constants are still 2026 estimates."""
    c = qc_compliance_report()
    w = qc_webras_status()
    reasons = []
    if c["verified"] < c["total"]:
        est = ", ".join(x["key"] for x in c["constants"] if x["status"] == "estimated")
        reasons.append(f"{c['estimated']} of {c['total']} Québec statutory constants still "
                       f"ESTIMATED — verify via Revenu Québec WebRAS / PDOC-Quebec: {est}")
    if w["confirmed"] < w["total"]:
        reasons.append(f"{w['pending']} of {w['total']} WebRAS/PDOC-QC cases NOT confirmed — run "
                       "each in WebRAS (+ PDOC-Quebec for fed/EI) and fill qc_webras_cases.py")
    gate_open = bool(reasons)
    return {
        "gate_open": gate_open, "ready_for_real_pay": not gate_open,
        "constants_verified": c["verified"], "constants_total": c["total"],
        "webras_confirmed": w["confirmed"], "webras_total": w["total"],
        "reasons": reasons,
    }


# ═══ EXTENDED ROC provinces/territories (per-province gate) ═══════════════════
# ON/BC/AB are the CRA-PDOC-verified core (gate_status above); QC is its own
# system (qc_gate_status). Every OTHER province/territory is added to the engine
# (STAT_2026) but gated on ITS OWN verification, exactly like QC — so adding them
# can NEVER flip the ON/BC/AB gate open. A jurisdiction here is real-pay-ready only
# once ALL its statutory constants are confirmed against CRA T4127 (+ the NT/NU
# territorial Payroll Tax Act) via that province's PDOC cases. Everything starts
# ESTIMATED, so each new gate is correctly OPEN until the operator verifies it.
EXTENDED_PROVINCES = ("MB", "SK", "NS", "NB", "NL", "PE", "NT", "YT", "NU")

_TERR_PT = "GNWT / Government of Nunavut — Payroll Tax Act (2% employee payroll tax, primary source)"
# HONEST source label: the extended-province figures were cross-confirmed across ≥2
# reputable aggregators citing CRA/gov (taxtips.ca + PwC, with catax/Wealthsimple),
# NOT read off the CRA T4127 primary doc — so they are ESTIMATES pending CRA PDOC.
_EXT_EST = "2026 estimate — taxtips.ca + PwC Tax Summaries (cross-checked); VERIFY vs CRA T4127 / PDOC"


def _prov_ext_rows(prov: str):
    """Verification rows for ONE extended province, pulled live from STAT_2026 so
    the registry can't drift from the engine. Returns [] if the province isn't built
    into the tax table yet (so its gate reports 'not available' rather than crashing)."""
    prov = prov.upper()
    p = STAT_2026.get(prov)
    if not p:
        return []
    rows = [
        (f"{prov}.bpa", f"{prov} Basic Personal Amount", p.get("bpa"), _EXT_EST),
        (f"{prov}.lowest_rate", f"{prov} lowest tax rate", p.get("lowest_rate"), _EXT_EST),
        (f"{prov}.brackets", f"{prov} tax brackets", p.get("brackets"), _EXT_EST),
    ]
    if p.get("surtax") is not None:
        rows.append((f"{prov}.surtax", f"{prov} surtax thresholds/rates", p["surtax"], _EXT_EST))
    if p.get("payroll_tax_rate") is not None:
        rows.append((f"{prov}.payroll_tax_rate", f"{prov} territorial payroll-tax rate",
                     p["payroll_tax_rate"], _TERR_PT))
    return rows


# Verification ledger for the extended jurisdictions. NEVER fabricate a CRA/
# territorial constant — flip a key here to verified ONLY once a prov_pdoc_cases.py
# case (or a CRA-displayed value) confirms it. Empty = everything estimated = every
# extended gate OPEN (the safe default the operator unlocks province-by-province).
PROV_EXT_VERIFIED: dict = {}


def prov_compliance_report(prov: str) -> dict:
    """Roll-up of ONE extended province's statutory-constant verification state."""
    prov = prov.upper()
    rows = _prov_ext_rows(prov)
    out = []
    for key, label, value, source in rows:
        v = PROV_EXT_VERIFIED.get(key, {})
        out.append({"key": key, "label": label, "value": str(value), "source": source,
                    "status": v.get("status", "estimated"),
                    "verified_on": v.get("verified_on"), "by": v.get("by")})
    verified = sum(1 for c in out if c["status"] == "verified")
    total = len(out)
    return {"province": prov, "available": bool(rows),
            "total": total, "verified": verified, "estimated": total - verified,
            "ready_for_real_pay": bool(rows) and verified == total,
            "as_of_edition": "2026", "constants": out}


def prov_pdoc_status(prov: str) -> dict:
    """How many CRA-PDOC cases for THIS extended province are confirmed. Cases live
    in prov_pdoc_cases.py (optional) — absent → 0/0, so the gate leans on constant
    verification alone until per-province golden cases are added."""
    prov = prov.upper()
    try:
        from prov_pdoc_cases import CASES
    except Exception:
        return {"total": 0, "confirmed": 0, "pending": 0}
    mine = [c for c in CASES if (c.get("province") or "").upper() == prov]
    confirmed = sum(1 for c in mine if c.get("pdoc"))
    return {"total": len(mine), "confirmed": confirmed, "pending": len(mine) - confirmed}


def prov_gate_status(prov: str) -> dict:
    """The per-province 'verify before real pay' gate for an extended jurisdiction
    (mirrors gate_status / qc_gate_status). OPEN until (a) the shared federal/CPP/EI
    core is verified, (b) EVERY one of this province's constants is confirmed, and
    (c) any province-specific PDOC cases are confirmed. A real pay run touching this
    province is blocked while its gate is open (test runs must acknowledge it)."""
    prov = prov.upper()
    rep = prov_compliance_report(prov)
    pd = prov_pdoc_status(prov)
    reasons = []
    if not rep["available"]:
        reasons.append(f"{prov} is not yet built into the engine (no 2026 tax table) — "
                       "cannot run real or test pay for it.")
    else:
        # The federal/CPP/EI core is shared with ON/BC/AB; require it verified too.
        base = gate_status()
        if base["gate_open"]:
            reasons.append("Federal/CPP/EI core not fully verified: " + "; ".join(base["reasons"]))
        if rep["verified"] < rep["total"]:
            est = ", ".join(x["key"] for x in rep["constants"] if x["status"] == "estimated")
            reasons.append(f"{rep['estimated']} of {rep['total']} {prov} statutory constants still "
                           f"ESTIMATED — verify against CRA T4127 (2026 ed.): {est}")
        if pd["total"] and pd["confirmed"] < pd["total"]:
            reasons.append(f"{pd['pending']} of {pd['total']} {prov} PDOC cases NOT confirmed — "
                           "run each in CRA PDOC and fill prov_pdoc_cases.py")
    gate_open = bool(reasons)
    return {
        "province": prov, "available": rep["available"],
        "gate_open": gate_open, "ready_for_real_pay": not gate_open,
        "constants_verified": rep["verified"], "constants_total": rep["total"],
        "pdoc_confirmed": pd["confirmed"], "pdoc_total": pd["total"],
        "reasons": reasons,
    }


# All Canadian jurisdictions and how each is gated, for the compliance dashboard.
_CORE_PROVINCES = ("ON", "BC", "AB")


def jurisdictions_overview() -> dict:
    """Every Canadian jurisdiction with its engine-coverage + real-pay state, so the
    compliance UI can show exactly how much of Canada is live vs gated vs not built."""
    items = []
    # Core ON/BC/AB share the one ROC gate (verified today).
    roc = gate_status()
    for prov in _CORE_PROVINCES:
        items.append({"code": prov, "system": "ROC", "built": True,
                      "ready_for_real_pay": roc["ready_for_real_pay"],
                      "state": "live" if roc["ready_for_real_pay"] else "gated"})
    # Quebec — its own system + gate.
    qc = qc_gate_status()
    items.append({"code": "QC", "system": "QC", "built": True,
                  "ready_for_real_pay": qc["ready_for_real_pay"],
                  "state": "live" if qc["ready_for_real_pay"] else "gated"})
    # Extended provinces/territories — each its own gate.
    for prov in EXTENDED_PROVINCES:
        g = prov_gate_status(prov)
        items.append({"code": prov, "system": "ROC-ext", "built": g["available"],
                      "ready_for_real_pay": g["ready_for_real_pay"],
                      "state": ("live" if g["ready_for_real_pay"] else
                                "gated" if g["available"] else "not_built")})
    live = sum(1 for i in items if i["state"] == "live")
    return {"total": len(items), "live": live,
            "gated": sum(1 for i in items if i["state"] == "gated"),
            "not_built": sum(1 for i in items if i["state"] == "not_built"),
            "jurisdictions": items}
