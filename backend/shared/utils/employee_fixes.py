"""Lexora HRM — pure decision rules for the manager back-fill script and for auto
employee numbers. No database access here, so every rule is unit-tested.

  scripts/backfill_manager_id.py    → match_manager()
  employee service (auto number)    → format_number() / next_free_number()
"""
import re

FIX = "fix"          # the script may write this on APPLY=1
REVIEW = "review"    # a person must decide — never written automatically
OK = "ok"


def _has(v) -> bool:
    return v is not None and str(v).strip() != ""

# ── reporting manager back-fill ───────────────────────────────────────────────
def _norm(s) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[.,]", " ", str(s or ""))).strip().lower()


def name_keys(emp: dict) -> set[str]:
    """Every way an employee may have been typed into the old free-text box."""
    first, last = _norm(emp.get("first_name")), _norm(emp.get("last_name"))
    pref, mid = _norm(emp.get("preferred_name")), _norm(emp.get("middle_name"))
    keys = set()
    if first and last:
        keys |= {f"{first} {last}", f"{last} {first}"}
        if mid:
            keys.add(f"{first} {mid} {last}")
        if pref:
            keys.add(f"{pref} {last}")
    if _has(emp.get("employee_number")):
        keys.add(_norm(emp["employee_number"]))
    for k in ("email", "work_email"):
        if _has(emp.get(k)):
            keys.add(str(emp[k]).strip().lower())
    return keys


def match_manager(text: str | None, employee_id, candidates: list[dict]) -> dict:
    """Find the ONE employee a free-text manager name points to.

    Returns {action: fix|review|ok, manager_id, manager_name, reason}. It is a
    `fix` only when exactly one other active employee of the org matches."""
    want = _norm(text)
    if not want:
        return {"action": OK, "manager_id": None, "manager_name": None, "reason": "no manager typed"}
    hits = [c for c in candidates
            if str(c.get("id")) != str(employee_id)
            and (want in name_keys(c) or str(text).strip().lower() in name_keys(c))]
    if len(hits) == 1:
        h = hits[0]
        return {"action": FIX, "manager_id": str(h["id"]),
                "manager_name": f"{h.get('first_name') or ''} {h.get('last_name') or ''}".strip(),
                "reason": "exactly one employee matches"}
    if not hits:
        return {"action": REVIEW, "manager_id": None, "manager_name": None,
                "reason": f"nobody in the organization is called '{text}'"}
    return {"action": REVIEW, "manager_id": None, "manager_name": None,
            "reason": f"{len(hits)} employees match '{text}' — pick one on the employee form"}


# ── employee numbers ──────────────────────────────────────────────────────────
def format_number(prefix: str | None, n: int, padding: int = 4) -> str:
    """EMP + 7 → EMP-0007. A prefix that already ends in a separator keeps it
    (KT/ + 7 → KT/0007). An empty prefix gives just the padded number."""
    p = (prefix or "").strip()
    pad = max(1, min(int(padding or 4), 10))
    body = f"{int(n):0{pad}d}"
    if not p:
        return body
    return f"{p}{body}" if not p[-1].isalnum() else f"{p}-{body}"


def suffix_of(prefix: str | None, number: str | None) -> int | None:
    """The running number inside an employee number of this prefix, else None."""
    p = (prefix or "").strip()
    s = (number or "").strip()
    if p and not s.upper().startswith(p.upper()):
        return None
    m = re.fullmatch(r"[\-_/ ]?(\d{1,10})", s[len(p):])
    return int(m.group(1)) if m else None


def next_free_number(prefix: str | None, padding: int, counter: int | None, taken: set[str]) -> tuple[str, int]:
    """(employee number to use, counter value to store afterwards).

    Starts from the stored counter, or — the first time — one past the highest
    number already used with this prefix. Skips anything already taken (a
    number typed by hand earlier), so a manual override can never collide."""
    upper = {str(t).strip().upper() for t in taken if t}
    used = [x for x in (suffix_of(prefix, t) for t in taken) if x is not None]
    n = max(int(counter or 0), (max(used) + 1) if (used and not counter) else 1, 1)
    while format_number(prefix, n, padding).upper() in upper:
        n += 1
    return format_number(prefix, n, padding), n + 1
