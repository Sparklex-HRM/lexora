"""Parallel-run reconciliation (pure): the incumbent's payroll register for a month
vs Lexora HRM's stubs for the same month — line by line per employee per component,
with a tolerance.
"""
from __future__ import annotations

COMPONENTS = {
    "gross": "Gross pay",
    "income_tax": "Income tax",
    "cpp": "CPP / QPP",
    "cpp2": "CPP2 / QPP2",
    "ei": "EI",
    "qpip": "QPIP",
    "pre_tax": "Pre-tax deductions",
    "after_tax": "After-tax deductions",
    "employer_cpp": "Employer CPP / QPP",
    "employer_ei": "Employer EI",
    "net": "Net pay",
}


def component_catalog(country: str = "CA") -> list[dict]:
    return [{"key": k, "label": v} for k, v in COMPONENTS.items()]


def stub_to_row(stub_detail: dict, employee_number: str) -> dict:
    """Flatten one Lexora HRM stub detail into comparable components (+ entries by code)."""
    d = stub_detail.get("deductions") or {}
    ec = stub_detail.get("employer_cost") or {}
    row = {"employee_code": (employee_number or "").strip().upper(),
           "gross": float(stub_detail.get("gross") or 0), "net": float(stub_detail.get("net_pay") or 0),
           "income_tax": float(d.get("income_tax") or 0), "cpp": float(d.get("cpp") or 0),
           "cpp2": float(d.get("cpp2") or 0), "ei": float(d.get("ei") or 0), "qpip": float(d.get("qpip") or 0),
           "pre_tax": float(d.get("pre_tax") or 0), "after_tax": float(d.get("after_tax") or 0),
           "employer_cpp": float(ec.get("cpp") or 0) + float(ec.get("cpp2") or 0),
           "employer_ei": float(ec.get("ei") or 0)}
    for e in (stub_detail.get("entries") or {}).get("earnings", []) or []:
        if e.get("code"):
            row[f"extra:{str(e['code']).upper()}"] = row.get(f"extra:{str(e['code']).upper()}", 0.0) + float(e.get("amount") or 0)
    for e in (stub_detail.get("entries") or {}).get("deductions", []) or []:
        if e.get("code"):
            row[f"extra:{str(e['code']).upper()}"] = row.get(f"extra:{str(e['code']).upper()}", 0.0) + float(e.get("amount") or 0)
    return row


def _num(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    s = str(v).strip().replace(",", "").replace("CA$", "").replace("$", "").replace("CAD", "")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        return -float(s) if neg else float(s)
    except ValueError:
        return None


def merge_lexora_rows(rows: list[dict]) -> dict[str, dict]:
    """Several stubs for one employee in the month (e.g. biweekly) → one summed row."""
    out: dict[str, dict] = {}
    for r in rows:
        code = r["employee_code"]
        t = out.setdefault(code, {"employee_code": code, "stubs": 0})
        t["stubs"] += 1
        for k, v in r.items():
            if k in ("employee_code", "stubs"):
                continue
            t[k] = round(t.get(k, 0.0) + float(v or 0), 2)
    return out


def compare(register_rows: list[dict], mapping: dict, lexora_rows: list[dict], tolerance: float = 1.0,
            currency: str = "CA$") -> dict:
    """mapping: {"employee_code": <their col>, "<component>": <their col>, ...}. Register rows
    for the same employee are summed (as the import from Jethro HR did). Returns per-employee, per-component lines."""
    emp_col = mapping.get("employee_code")
    comps = [k for k, col in mapping.items() if k != "employee_code" and col]
    theirs: dict[str, dict] = {}
    for r in register_rows:
        code = str(r.get(emp_col) or "").strip().upper() if emp_col else ""
        if not code:
            continue
        t = theirs.setdefault(code, {"employee_code": code, "rows": 0})
        t["rows"] += 1
        for k in comps:
            v = _num(r.get(mapping[k]))
            if v is not None:
                t[k] = round(t.get(k, 0.0) + v, 2)
    ours = merge_lexora_rows(lexora_rows)
    tol = float(tolerance or 0)
    lines, employees = [], []
    n_match = n_diff = 0
    max_abs = 0.0; sum_abs = 0.0
    for code in sorted(set(theirs) | set(ours)):
        a, b = theirs.get(code), ours.get(code)
        if a is None or b is None:
            employees.append({"employee_code": code, "status": "missing_in_lexora" if b is None else "missing_in_register",
                              "matched": 0, "mismatched": 0})
            continue
        em = ed = 0
        for k in comps:
            tv, ov = a.get(k), b.get(k, 0.0)
            if tv is None:
                lines.append({"employee_code": code, "component": k, "theirs": None, "ours": round(ov, 2),
                              "diff": None, "status": "not_in_register"})
                continue
            diff = round(ov - tv, 2)
            ok = abs(diff) <= tol
            lines.append({"employee_code": code, "component": k, "theirs": round(tv, 2), "ours": round(ov, 2),
                          "diff": diff, "status": "match" if ok else "mismatch"})
            if ok:
                em += 1; n_match += 1
            else:
                ed += 1; n_diff += 1
            max_abs = max(max_abs, abs(diff)); sum_abs += abs(diff)
        employees.append({"employee_code": code, "status": "match" if ed == 0 else "mismatch", "matched": em, "mismatched": ed,
                          "their_rows": a["rows"], "our_stubs": b["stubs"]})
    only_theirs = sum(1 for e in employees if e["status"] == "missing_in_lexora")
    only_ours = sum(1 for e in employees if e["status"] == "missing_in_register")
    matched_within = n_diff == 0 and only_theirs == 0 and only_ours == 0 and (n_match > 0)
    return {"tolerance": tol, "currency": currency, "components": comps,
            "employees": employees, "lines": lines,
            "summary": {"employees_compared": len(employees) - only_theirs - only_ours,
                        "missing_in_lexora": only_theirs, "missing_in_register": only_ours,
                        "lines_matched": n_match, "lines_mismatched": n_diff,
                        "max_abs_diff": round(max_abs, 2), "total_abs_diff": round(sum_abs, 2),
                        "matched_within_tolerance": matched_within,
                        "headline": (f"Parallel run matched within {currency}{tol:,.2f} — {n_match} lines, {len(employees)} employees"
                                     if matched_within else
                                     f"{n_diff} line(s) differ by more than {currency}{tol:,.2f}; {only_theirs} employee(s) only in the register, {only_ours} only in Lexora HRM")}}
