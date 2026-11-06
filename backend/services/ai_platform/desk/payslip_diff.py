"""Deterministic pay-stub diff (Canada) — the ENGINE explains a pay change; the model only narrates it.

Input: the caller's two pay stubs (period P and the one before) as ``PayFacts`` (built from the payroll service's
stub detail with ``facts_from_stub``) plus run metadata (a new TD1, a salary revision effective date). Output: a
structure with one line per component, each delta tied to the RULE behind it, the net delta and plain summary
facts. ``validate_narration`` guarantees the model's prose contains no dollar figure that is not in the structure;
``render_plain`` is the templated fallback.

Rule codes (``rule_text`` is plain words, ``refs`` point at the pay stub / the Verification Center):
  HOURS             hours paid changed (hourly pay: hours × rate)
  OVERTIME          overtime hours / pay this period (provincial overtime threshold)
  STAT_HOLIDAY      statutory-holiday pay this period
  VACATION_PAYOUT   vacation pay paid out this period (instead of accrued)
  ONE_OFF           bonus / retro / other one-time payment this period
  NEW_COMPONENT     a component present this period only
  COMPONENT_ENDED   a component present last period only
  RETRO_REVISION    retroactive pay for a salary revision effective BEFORE this period (RunMeta.arrears_from)
  REVISION          fixed pay changed with a salary revision
  REVISION_PRORATED pay changed with a revision effective INSIDE the period (part-period at each rate)
  CPP_MAX_REACHED   CPP stopped or fell because the year's maximum contribution was reached
  CPP2_STARTED      CPP2 / QPP2 started: year-to-date pensionable earnings passed the YMPE
  EI_MAX_REACHED    EI stopped or fell because the year's maximum insurable earnings were reached
  FOLLOWS_GROSS     CPP / EI / income tax moved with the period's earnings (same rates)
  TD1_CHANGE        income tax changed because a new TD1 claim amount is in force
  BONUS_METHOD      income tax on a bonus is worked out with the CRA bonus method (the bonus is taxed in this period)
  UNEXPLAINED       a delta no rule accounts for → payroll should check (hand-off candidate)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

TOLERANCE = 2.0                    # $ — rounding noise is not a change
# 2026 employee maximums the payroll engine uses (CRA PDOC-verified; Verification Center keys cpp.max_employee …)
CPP_MAX, CPP2_MAX, EI_MAX = 4230.45, 416.00, 1123.07

DEDUCTION_LABELS = {"cpp": "CPP", "cpp2": "CPP2", "ei": "EI premium", "qpip": "QPIP premium", "income_tax": "Income tax",
                    "pre_tax": "Pre-tax deductions (RRSP, union dues …)", "after_tax": "After-tax deductions"}
QC_LABELS = {"cpp": "QPP", "cpp2": "QPP2"}
EARNING_LABELS = {"REGULAR": "Regular pay", "REG": "Regular pay", "OT": "Overtime", "OT2": "Double-time overtime", "STAT": "Statutory holiday pay",
                  "VACP": "Vacation pay (paid out)", "BONUS": "Bonus", "RETRO": "Retroactive pay", "COMM": "Commission", "SHIFT": "Shift premium"}
ONE_OFF_CODES = {"BONUS", "RETRO", "COMM", "AWARD", "SEVERANCE", "TERMPAY", "INCENTIVE"}


@dataclass
class PayFacts:
    period: str                                   # pay date YYYY-MM-DD (or YYYY-MM)
    gross: float
    net: float
    earnings: dict[str, float] = field(default_factory=dict)      # code → amount (REGULAR + entries by code)
    hours: dict[str, float] = field(default_factory=dict)         # code → hours (REGULAR, OT, STAT …) when the stub carries them
    deductions: dict[str, float] = field(default_factory=dict)    # cpp, cpp2, ei, qpip, income_tax, pre_tax, after_tax
    ytd: dict[str, float] = field(default_factory=dict)           # cpp, cpp2, ei, pensionable, insurable (after this stub)
    province: str | None = None
    td1_federal: float | None = None
    td1_provincial: float | None = None
    bonus_taxable: float = 0.0
    run_id: str | None = None
    link: str | None = None
    labels: dict[str, str] = field(default_factory=dict)


@dataclass
class RunMeta:
    td1_changed: bool = False
    td1_note: str = ""                            # e.g. "your federal TD1 claim changed from $16,452 to $19,000"
    revision_effective: str | None = None         # YYYY-MM-DD when a salary revision applied
    revision_prorated: bool = False               # effective date INSIDE the period → part-period at each rate
    arrears_from: str | None = None               # revision effective BEFORE the period, paid retroactively now (RETRO_REVISION)
    revision_note: str = ""                       # plain words, from payroll data (never invented)


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def facts_from_stub(detail: dict, summary: dict | None = None) -> PayFacts:
    """Normalise the payroll service's stub detail (calc.py output + the routers wrapper) into PayFacts."""
    summary = summary or {}
    detail = detail or {}
    ded = detail.get("deductions") or {}
    tax = detail.get("tax_detail") or {}
    period = str(summary.get("pay_date") or (detail.get("period") or {}).get("pay_date") or "")[:10]
    gross = _f(detail.get("gross") or summary.get("gross"))
    earnings: dict[str, float] = {}
    hours: dict[str, float] = {}
    labels: dict[str, str] = {}
    entry = detail.get("entries") or {}
    for x in (entry.get("earnings") if isinstance(entry, dict) else None) or []:
        code = str(x.get("code") or "EXTRA").upper()
        amt = _f(x.get("amount"))
        if amt:
            earnings[code] = round(earnings.get(code, 0.0) + amt, 2)
            labels.setdefault(code, x.get("name") or EARNING_LABELS.get(code, code.title()))
        if x.get("hours"):
            hours[code] = round(hours.get(code, 0.0) + _f(x.get("hours")), 2)
    rest = round(gross - sum(v for k, v in earnings.items() if k != "REG"), 2)
    if "REG" in earnings:
        earnings["REGULAR"] = earnings.pop("REG")
        labels["REGULAR"] = labels.pop("REG", "Regular pay")
    elif rest > 0.005:
        earnings["REGULAR"] = rest
        labels["REGULAR"] = "Regular pay"
    if "REG" in hours:
        hours["REGULAR"] = hours.pop("REG")
    deductions = {k: _f(ded.get(k)) for k in ("cpp", "cpp2", "ei", "qpip", "income_tax", "pre_tax", "after_tax")}
    td1 = detail.get("td1") or {}
    return PayFacts(period=period, gross=gross, net=_f(detail.get("net_pay") or summary.get("net_pay")), earnings=earnings, hours=hours,
                    deductions=deductions, ytd={k: _f(v) for k, v in (detail.get("ytd") or {}).items()},
                    province=((detail.get("employee") or {}).get("province") or detail.get("province")),
                    td1_federal=td1.get("federal"), td1_provincial=td1.get("provincial"), bonus_taxable=_f(tax.get("bonus_taxable")),
                    run_id=str(detail.get("pay_run_id") or summary.get("pay_run_id") or "") or None,
                    link=f"/my-pay?stub={summary.get('stub_id')}" if summary.get("stub_id") else None, labels=labels)


def _r(x) -> float:
    return round(float(x or 0), 2)


def diff(prev: PayFacts | None, curr: PayFacts, meta: RunMeta | None = None) -> dict:
    """Line-by-line deltas with the rule behind each. ``prev`` None = first pay stub (every line is new)."""
    meta = meta or RunMeta()
    lines: list[dict] = []
    facts: list[str] = []
    pe = prev.earnings if prev else {}
    pd = prev.deductions if prev else {}
    refs_slip = [r for r in (curr.link, prev.link if prev else None) if r]
    qc = (curr.province or "").upper() == "QC"
    labels = {**DEDUCTION_LABELS, **(QC_LABELS if qc else {})}

    def add(component, label, p, c, code, text, refs=None, kind="earning"):
        d = _r(c - p)
        if abs(d) < TOLERANCE and code not in ("NEW_COMPONENT", "COMPONENT_ENDED"):
            return
        lines.append({"component": component, "label": label, "kind": kind, "prev": _r(p), "curr": _r(c), "delta": d,
                      "rule_code": code, "rule_text": text, "refs": refs or refs_slip})

    # ── earnings ─────────────────────────────────────────────────────────────
    for code in sorted(set(pe) | set(curr.earnings), key=lambda c: (c != "REGULAR", c)):
        p, c = float(pe.get(code, 0.0)), float(curr.earnings.get(code, 0.0))
        label = curr.labels.get(code) or (prev.labels.get(code) if prev else None) or EARNING_LABELS.get(code, code.title())
        if abs(_r(c - p)) < TOLERANCE:
            continue
        hp, hc = (prev.hours.get(code, 0.0) if prev else 0.0), curr.hours.get(code, 0.0)
        if code in ("OT", "OT2"):
            add(code, label, p, c, "OVERTIME", f"{label}: {hc:g} hour(s) this period vs {hp:g} last period, paid at the overtime rate your province's "
                "employment standards set above the overtime threshold.")
            continue
        if code == "STAT":
            add(code, label, p, c, "STAT_HOLIDAY", "Statutory holiday pay for the public holiday(s) in this pay period, under your province's rule.")
            continue
        if code == "VACP":
            add(code, label, p, c, "VACATION_PAYOUT", "Vacation pay was paid out this period (from your accrued vacation balance) instead of being accrued.")
            continue
        if code.startswith("RETRO"):
            if meta.arrears_from:
                add(code, label, p, c, "RETRO_REVISION", f"Retroactive pay: the salary revision effective {meta.arrears_from} was applied back to that date, "
                    "so the difference for the pay periods already paid is settled on this stub.")
            else:
                add(code, label, p, c, "ONE_OFF", f"{label} is a one-time retroactive payment this period.")
            continue
        if code in ONE_OFF_CODES:
            add(code, label, p, c, "ONE_OFF", f"{label} is a one-time payment this period; it is taxed in the period it is paid.")
            continue
        if p == 0 and c > 0:
            add(code, label, p, c, "NEW_COMPONENT", f"{label} is a new line on your pay stub this period.")
            continue
        if c == 0 and p > 0:
            add(code, label, p, c, "COMPONENT_ENDED", f"{label} was paid last period but not this period.")
            continue
        if code == "REGULAR" and (hp or hc) and abs(hc - hp) >= 0.25:
            add(code, label, p, c, "HOURS", f"Regular hours paid: {hc:g} this period vs {hp:g} last period (hours × your hourly rate).")
            continue
        if meta.revision_effective:
            if meta.revision_prorated:
                add(code, label, p, c, "REVISION_PRORATED", f"{label} changed part-way through the period: the salary revision effective {meta.revision_effective} "
                    "applies from that date, so this period is paid at the old rate up to it and at the new rate after it.")
            else:
                add(code, label, p, c, "REVISION", f"{label} changed with the salary revision effective {meta.revision_effective}.")
            continue
        add(code, label, p, c, "UNEXPLAINED", f"{label} changed and no rule in this pay run accounts for it — payroll should check.")

    gross_delta = _r(curr.gross - (prev.gross if prev else 0.0))
    gross_moved = abs(gross_delta) >= TOLERANCE

    # ── statutory deductions ──────────────────────────────────────────────────
    ytd = curr.ytd or {}
    for key, cap in (("cpp", CPP_MAX), ("cpp2", CPP2_MAX), ("ei", EI_MAX), ("qpip", None)):
        p, c = float(pd.get(key, 0)), float(curr.deductions.get(key, 0))
        if abs(_r(c - p)) < TOLERANCE:
            continue
        label = labels[key]
        at_cap = cap is not None and not qc and _f(ytd.get(key)) >= cap - 0.01
        if key == "cpp2" and p == 0 and c > 0:
            add(key.upper(), label, p, c, "CPP2_STARTED", f"{label} started: your pensionable earnings for the year passed the YMPE ($74,600), "
                "so the second contribution (4% up to the YAMPE) now applies.", kind="deduction")
        elif at_cap and c < p:
            add(key.upper(), label, p, c, "CPP_MAX_REACHED" if key != "ei" else "EI_MAX_REACHED",
                f"You reached the year's maximum {label} (${cap:,.2f}); this period only the remainder was deducted and nothing more is deducted until January.",
                refs_slip + ["/payroll/compliance"], kind="deduction")
        elif gross_moved:
            add(key.upper(), label, p, c, "FOLLOWS_GROSS", f"{label} is a fixed percentage of the period's earnings, so it moved with your gross pay.", kind="deduction")
        else:
            add(key.upper(), label, p, c, "UNEXPLAINED", f"{label} changed while your gross pay did not — payroll should check.", kind="deduction")
    # ── income tax ───────────────────────────────────────────────────────────
    p, c = float(pd.get("income_tax", 0)), float(curr.deductions.get("income_tax", 0))
    if abs(_r(c - p)) >= TOLERANCE:
        label = labels["income_tax"]
        if meta.td1_changed:
            add("INCOME_TAX", label, p, c, "TD1_CHANGE", f"Income tax changed because a new TD1 claim amount is in force ({meta.td1_note or 'your TD1 changed'}).",
                refs_slip + ["/my-profile"], kind="deduction")
        elif curr.bonus_taxable > 0:
            add("INCOME_TAX", label, p, c, "BONUS_METHOD", f"A bonus of ${curr.bonus_taxable:,.2f} is taxed this period with the CRA bonus method "
                "(tax on the bonus is worked out on your annual income including it).", kind="deduction")
        elif gross_moved:
            add("INCOME_TAX", label, p, c, "FOLLOWS_GROSS", "Income tax is worked out on the period's taxable earnings with the CRA formulas, "
                "so it moved with your gross pay (tax is progressive, so it can move by more than the gross did).", kind="deduction")
        else:
            add("INCOME_TAX", label, p, c, "UNEXPLAINED", "Income tax changed while your gross pay and TD1 did not — payroll should check.", kind="deduction")
    for key in ("pre_tax", "after_tax"):
        p, c = float(pd.get(key, 0)), float(curr.deductions.get(key, 0))
        if abs(_r(c - p)) >= TOLERANCE:
            add(key.upper(), labels[key], p, c, "NEW_COMPONENT" if p == 0 else "COMPONENT_ENDED" if c == 0 else "UNEXPLAINED",
                "These are the voluntary / plan deductions on your record (group RRSP, union dues, benefits) — see the pay stub lines.", kind="deduction")

    net_delta = _r(curr.net - (prev.net if prev else 0.0))
    unexplained = _r(sum(x["delta"] for x in lines if x["rule_code"] == "UNEXPLAINED"))
    if prev:
        facts.insert(0, f"Net pay ${curr.net:,.2f} on {curr.period} vs ${prev.net:,.2f} on {prev.period}: {'down' if net_delta < 0 else 'up'} ${abs(net_delta):,.2f}.")
        facts.insert(1, f"Gross ${curr.gross:,.2f} vs ${prev.gross:,.2f}: {'down' if gross_delta < 0 else 'up'} ${abs(gross_delta):,.2f}.")
    else:
        facts.insert(0, f"First pay stub on record: net pay ${curr.net:,.2f}, gross ${curr.gross:,.2f} on {curr.period}.")
    lines.sort(key=lambda x: -abs(x["delta"]))
    return {"period": curr.period, "prev_period": prev.period if prev else None, "lines": lines, "net_delta": net_delta,
            "gross_delta": gross_delta, "province": curr.province, "unexplained_delta": unexplained,
            "needs_payroll_review": abs(unexplained) > max(20.0, 0.01 * max(curr.gross, 1.0)),
            "summary_facts": facts, "run_id": curr.run_id, "links": refs_slip}


# ── narration guard ───────────────────────────────────────────────────────────

_NUM = re.compile(r"(?<![\w.])(?:\$|c\$|cad)?\s?(\d[\d,]*(?:\.\d+)?)(?![\w.])", re.IGNORECASE)


def _num_forms(v: float) -> set[str]:
    forms = set()
    for x in (v, abs(v)):
        forms.add(f"{x:.2f}"); forms.add(f"{x:,.2f}"); forms.add(f"{x:g}"); forms.add(f"{x:.0f}"); forms.add(f"{x:,.0f}")
        forms.add(f"{x:,.1f}"); forms.add(f"{x:.1f}")
    return {f.replace(",", "") for f in forms} | forms


def allowed_numbers(structure: dict) -> set[str]:
    vals: list[float] = []
    for ln in structure.get("lines") or []:
        vals += [ln["prev"], ln["curr"], ln["delta"]]
    for k in ("net_delta", "gross_delta", "unexplained_delta"):
        if structure.get(k) is not None:
            vals.append(float(structure[k]))
    # figures quoted inside rule texts / facts are engine-made, so they are allowed too
    for s in list(structure.get("summary_facts") or []) + [ln.get("rule_text") or "" for ln in structure.get("lines") or []]:
        for m in _NUM.finditer(s):
            try:
                vals.append(float(m.group(1).replace(",", "")))
            except ValueError:
                pass
    out: set[str] = set()
    for v in vals:
        out |= _num_forms(v)
    return out


def validate_narration(text: str, structure: dict, *, min_value: float = 100.0) -> tuple[bool, list[str]]:
    """Every dollar-sized figure (≥ min_value, or any $-prefixed figure) in the narration must appear in the structure.
    Small integers (hours, percentages, periods) are allowed."""
    allowed = allowed_numbers(structure)
    bad = []
    for m in _NUM.finditer(text or ""):
        raw = m.group(1)
        prefixed = m.group(0).strip()[:1] in ("$", "C", "c")
        try:
            v = float(raw.replace(",", ""))
        except ValueError:
            continue
        if re.fullmatch(r"(19|20)\d{2}", raw) or (v < min_value and not prefixed):
            continue
        if raw in allowed or raw.replace(",", "") in allowed or f"{v:.2f}" in allowed:
            continue
        bad.append(m.group(0).strip())
    return (not bad), bad


def render_plain(structure: dict) -> str:
    """Templated plain-words rendering — used when the narration fails validation or no model is available."""
    out = list(structure.get("summary_facts") or [])
    for ln in structure.get("lines") or []:
        arrow = "down" if ln["delta"] < 0 else "up"
        out.append(f"• {ln['label']}: ${ln['prev']:,.2f} → ${ln['curr']:,.2f} ({arrow} ${abs(ln['delta']):,.2f}). {ln['rule_text']}")
    if structure.get("needs_payroll_review"):
        out.append(f"${abs(structure['unexplained_delta']):,.2f} of the change is not explained by any rule in this run — I can raise a payroll ticket for you.")
    if structure.get("run_id"):
        out.append(f"Payroll run {structure['run_id']}.")
    return "\n".join(out)


def line_for(structure: dict, line: str) -> dict | None:
    """The diff line for a pay-stub component the person asked about (CPP, EI, income tax, overtime …)."""
    want = re.sub(r"[^a-z0-9]", "", (line or "").lower())
    alias = {"qpp": "cpp", "qpp2": "cpp2", "pension": "cpp", "canadapensionplan": "cpp", "employmentinsurance": "ei",
             "tax": "incometax", "federaltax": "incometax", "provincialtax": "incometax", "vacation": "vacp", "vacationpay": "vacp",
             "overtime": "ot", "statholiday": "stat", "regularpay": "regular", "salary": "regular"}
    want = alias.get(want, want)
    for ln in structure.get("lines") or []:
        comp = re.sub(r"[^a-z0-9]", "", ln["component"].lower())
        lab = re.sub(r"[^a-z0-9]", "", ln["label"].lower())
        if want and (want == comp or want in lab or comp.startswith(want)):
            return ln
    return None
