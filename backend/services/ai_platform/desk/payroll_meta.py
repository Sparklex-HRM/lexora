"""RunMeta from real payroll data (phase 2, P2-C) — what changed BETWEEN the two pay stubs the diff compares.

Sources (same data-access pattern as tools_registry: ``copilot._get`` as the caller, so service RBAC and
x-employee-id scoping hold — the caller only ever sees their own records):

  * the two stubs' ``td1`` block (the claim amounts each run actually used)
      → ``td1_changed`` + a plain note (figures from the stubs, never invented)
  * ``GET /employees/compensation/mine`` history → the salary revision whose effective date falls after the
      previous stub: inside the current period → ``revision_effective`` + ``revision_prorated``; before the
      current period (retroactive) → ``revision_effective`` + ``arrears_from`` (the diff tags the retro line
      RETRO_REVISION)
  * bonus / overtime / vacation-payout lines need no metadata: they are read from the stub's entries by the diff.

Never raises; on any failure the fields stay at their defaults (the diff then says UNEXPLAINED / plain ONE_OFF).
"""
from __future__ import annotations

import sys
from datetime import date

sys.path.append("/app")
from shared.utils.config import settings  # noqa: E402

import copilot as CP  # noqa: E402

from desk import payslip_diff as PD  # noqa: E402


def _d(v) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def _period_bounds(summary: dict | None, detail: dict | None) -> tuple[date | None, date | None]:
    """(period_start, period_end) of a stub — from the detail / summary period fields, else the pay date itself."""
    d = detail or {}
    s = summary or {}
    per = d.get("period") if isinstance(d.get("period"), dict) else {}
    start = _d(per.get("start") or d.get("period_start") or s.get("period_start"))
    end = _d(per.get("end") or d.get("period_end") or s.get("period_end"))
    if start and end:
        return start, end
    pay = _d(s.get("pay_date") or per.get("pay_date") or d.get("pay_date"))
    return (pay, pay) if pay else (None, None)


def _has_retro(detail: dict | None) -> bool:
    for x in (((detail or {}).get("entries") or {}).get("earnings") or []):
        if str((x or {}).get("code") or "").upper().startswith("RETRO") and float((x or {}).get("amount") or 0) > 0:
            return True
    return False


def _money(v) -> str:
    try:
        return f"${float(v):,.0f}"
    except (TypeError, ValueError):
        return "—"


def meta_from_stubs(cur_detail: dict | None, cur_summary: dict | None, prev_detail: dict | None, prev_summary: dict | None,
                    comp_history: list[dict] | None = None) -> PD.RunMeta:
    """Pure: the RunMeta from already-fetched records (unit-testable with fixtures)."""
    meta = PD.RunMeta()
    has_retro = _has_retro(cur_detail)
    notes: list[str] = []
    if prev_detail:
        tc, tp = (cur_detail or {}).get("td1") or {}, (prev_detail or {}).get("td1") or {}
        for key in ("federal", "provincial"):
            a, b = tp.get(key), tc.get(key)
            if a is not None and b is not None and abs(float(a) - float(b)) >= 1.0:
                meta.td1_changed = True
                notes.append(f"your {key} TD1 claim changed from {_money(a)} to {_money(b)}")
    cur_start, cur_end = _period_bounds(cur_summary, cur_detail)
    prev_start, prev_end = _period_bounds(prev_summary, prev_detail)
    prev_pay = _d((prev_summary or {}).get("pay_date")) or prev_end
    # salary revision after the previous stub (inside this period), or a retroactive one paid now
    best: date | None = None
    for h in comp_history or []:
        eff = _d(h.get("effective_from"))
        if not eff or (cur_end and eff > cur_end):
            continue
        if cur_start and eff >= cur_start:
            best = eff if (best is None or eff > best) else best
            continue
        created = _d(h.get("created_at"))
        recent = cur_start is not None and eff >= cur_start.replace(year=cur_start.year - 1)
        if recent and (has_retro or (prev_pay and created and created > prev_pay)):
            if best is None or (eff > best and not (cur_start and best >= cur_start)):
                best = eff
    if best:
        meta.revision_effective = best.isoformat()
        if cur_start and best > cur_start:
            meta.revision_prorated = True
            meta.revision_note = f"salary revision effective {best.isoformat()} — applied for the days from that date in this period"
        elif cur_start and best < cur_start:
            meta.arrears_from = best.isoformat()
            meta.revision_note = f"salary revision effective {best.isoformat()} applied retroactively — the difference is paid this period"
        else:
            meta.revision_note = f"salary revision effective {best.isoformat()}"
    if notes:
        meta.td1_note = "; ".join(notes)
    return meta


async def run_meta(ctx, cur_detail: dict | None, cur_summary: dict | None, prev_detail: dict | None, prev_summary: dict | None) -> PD.RunMeta:
    """Fetch the side records as the caller and build the RunMeta. Never raises."""
    hist = None
    try:
        if ctx.employee_id:
            comp, _ = await CP._get(f"{settings.EMPLOYEE_URL}/employees/compensation/mine", ctx.headers)
            hist = (comp or {}).get("history") or []
    except Exception:                                        # noqa: BLE001
        hist = None
    try:
        return meta_from_stubs(cur_detail, cur_summary, prev_detail, prev_summary, comp_history=hist if isinstance(hist, list) else None)
    except Exception:                                        # noqa: BLE001
        return PD.RunMeta()
