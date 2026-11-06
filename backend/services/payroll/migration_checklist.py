"""Migration checklist — fixed load order + progress (pure).

Each step: key, title, how (what to do), link, `auto` = the fact that marks it done
(count-based) or None = manual tick. A step is `blocked` while an earlier required
step is not done, so the order is enforced on screen, not just suggested.
"""
from __future__ import annotations

STEPS = {
    "CA": [
        {"key": "settings", "title": "Company settings — CRA payroll account, remitter type", "link": "/payroll/canada/remittances", "auto": "ca_settings_done", "required": True,
         "how": "Enter the CRA payroll account and the remitter type CRA assigned you."},
        {"key": "stat_values", "title": "Canadian statutory values entered + verified", "link": "/payroll/canada/statutory-values", "auto": "ca_values_verified", "required": True,
         "how": "Every rate / threshold / due date you will rely on, with its source URL, marked verified."},
        {"key": "masters", "title": "Master data — pay components, departments, stat holidays", "link": "/masters", "auto": None, "required": True,
         "how": "Check the pay components (REG, OT, STAT, VACP, RRSP …) and the holiday calendar for your provinces."},
        {"key": "employees", "title": "Employees imported", "link": "/migration/canada?type=ca_employees", "auto": "ca_employees", "required": True,
         "how": "Upload the Employees file (code, name, province, pay frequency, salary / hourly rate, hire date)."},
        {"key": "td1", "title": "TD1 claims (federal + provincial) imported", "link": "/migration/canada?type=ca_td1", "auto": "ca_td1", "required": False,
         "how": "Upload TD1 claim amounts. Blank = basic personal amount is used."},
        {"key": "salary_history", "title": "Salary history imported (comp_history)", "link": "/migration/canada?type=ca_salary_history", "auto": "ca_salary_history", "required": False,
         "how": "Past salary rows with effective dates, so revisions and letters have history."},
        {"key": "ytd", "title": "YTD payroll per month (this calendar year)", "link": "/migration/canada?type=ca_ytd_monthly", "auto": "ca_ytd", "required": True,
         "how": "Month-wise gross / CPP / EI / tax from the old system — this makes the T4 and the CPP/EI caps right."},
        {"key": "leave", "title": "Leave balances imported", "link": "/migration/canada?type=ca_leave_balances", "auto": "ca_leave", "required": False,
         "how": "Opening balance + used days per leave type."},
        {"key": "contractors", "title": "Contractors (T4A) entered", "link": "/payroll/canada/contractors", "auto": "ca_contractors", "required": False,
         "how": "Add contractors paid this year and their payments to date."},
        {"key": "parallel", "title": "Parallel run matched within tolerance", "link": "/migration/parallel-run", "auto": "parallel_signed", "required": True,
         "how": "Upload last month's register from the old system, compare, sign off."},
        {"key": "golive", "title": "Go-live sign-off", "link": "/migration/checklist", "auto": None, "required": True,
         "how": "Tick when the first live run is approved."},
    ],
}


def progress(country: str, facts: dict, ticks: dict) -> dict:
    """facts: auto-key → truthy/count. ticks: step_key → {done, note, done_by, done_at}."""
    c = "CA"
    steps = []
    blocked = False
    done_n = 0
    for i, s in enumerate(STEPS[c], 1):
        t = ticks.get(s["key"]) or {}
        auto_val = facts.get(s["auto"]) if s["auto"] else None
        auto_done = bool(auto_val)
        done = auto_done or bool(t.get("done"))
        step = {**s, "order": i, "done": done, "auto_value": auto_val, "manual": bool(t.get("done")),
                "note": t.get("note"), "done_by": t.get("done_by"), "done_at": t.get("done_at"),
                "blocked": blocked and not done}
        steps.append(step)
        if done:
            done_n += 1
        elif s["required"]:
            blocked = True
    nxt = next((s for s in steps if not s["done"]), None)
    return {"country": c, "steps": steps, "done": done_n, "total": len(steps),
            "percent": round(100 * done_n / len(steps)) if steps else 0,
            "next_step": nxt["key"] if nxt else None, "complete": done_n == len(steps)}
