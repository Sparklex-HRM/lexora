"""``agentic_state(org)`` — is the Agentic operations add-on on for this org, and in which mode.

    from agentic_state import agentic_state
    st = await agentic_state(org)
    if not st["enabled"]: return            # add-on not bought (or paused by an admin)
    st["mode"]                              # "shadow" | "live"

Resolution
* add-on purchased  = ``agentic`` in ``organizations.addons`` (auth billing; chosen at sign-up, on the
  Plan & billing screen, or by the platform operator on the Tenants console);
* ``ai_agentic.enabled`` company setting: None (default) → follow the add-on; True / False → an
  explicit org-admin switch (False pauses the agents even though the add-on is paid for);
* ``mode`` defaults to shadow; live needs ``shadow_cycles_done >= LIVE_AFTER_SHADOW_CYCLES`` or an
  admin override with a written reason (``set_mode``).
Nested dicts (approvers / minutes_table / thresholds) are deep-merged over the registry defaults, so a
partial save never loses a default key. Never raises; on any failure the add-on reads as OFF.
"""
from __future__ import annotations

import copy
import logging
import sys

from sqlalchemy import select, text

sys.path.append("/app")
from shared.utils import platform_models as pm  # noqa: E402
from shared.utils.org_settings import GROUPS, get_setting, invalidate  # noqa: E402
from shared.utils.platform_models import OrgSetting, OrgSettingAudit  # noqa: E402

log = logging.getLogger("lexora.agentic")

ADDON_KEY = "agentic"
GROUP = "ai_agentic"
MODES = ("shadow", "live")
LIVE_AFTER_SHADOW_CYCLES = 2
MIN_OVERRIDE_REASON = 10


def defaults() -> dict:
    return copy.deepcopy(GROUPS[GROUP]["default"])


def _deep(base: dict, over) -> dict:
    out = copy.deepcopy(base)
    if not isinstance(over, dict):
        return out
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep(out[k], v)
        else:
            out[k] = v
    return out


async def org_addons(org: str) -> list[str]:
    """The add-on keys the org pays for (auth's ``organizations.addons``). [] when unknown."""
    try:
        async with pm.session() as s:
            r = (await s.execute(text("SELECT addons FROM organizations WHERE id = CAST(:org AS uuid)"), {"org": str(org)})).first()
        v = r[0] if r else None
        if isinstance(v, str):
            import json
            v = json.loads(v)
        return [str(x) for x in (v or [])]
    except Exception as e:                                   # noqa: BLE001
        log.debug("org_addons(%s) failed: %s", org, e)
        return []


async def raw_settings(org: str) -> dict:
    stored = await get_setting(org, GROUP, {}) or {}
    return _deep(defaults(), stored)


def can_go_live(st: dict) -> tuple[bool, str]:
    """Gate for shadow → live: two shadow cycles, or an admin override with a reason."""
    if int(st.get("shadow_cycles_done") or 0) >= LIVE_AFTER_SHADOW_CYCLES:
        return True, f"{st['shadow_cycles_done']} shadow cycle(s) done."
    ov = st.get("live_override") or {}
    if ov and len(str(ov.get("reason") or "").strip()) >= MIN_OVERRIDE_REASON:
        return True, f"Admin override by {ov.get('by') or 'an admin'}: {ov.get('reason')}"
    return False, (f"Live mode needs {LIVE_AFTER_SHADOW_CYCLES} completed shadow payroll cycles "
                   f"({int(st.get('shadow_cycles_done') or 0)} so far) or an org-admin override with a written reason.")


async def agentic_state(org: str) -> dict:
    """The one answer every agentic module reads. Never raises."""
    try:
        st = await raw_settings(org)
        addons = await org_addons(org)
    except Exception as e:                                   # noqa: BLE001
        log.warning("agentic_state(%s) failed: %s", org, e)
        st, addons = defaults(), []
    purchased = ADDON_KEY in addons
    flag = st.get("enabled")
    enabled = bool(flag) if flag is not None else purchased
    mode = st.get("mode") if st.get("mode") in MODES else "shadow"
    ok, why = can_go_live(st)
    if mode == "live" and not ok:
        mode = "shadow"                                      # a stale live flag never bypasses the gate
    return {**st, "enabled": enabled, "addon_purchased": purchased, "paused": purchased and flag is False,
            "mode": mode, "can_go_live": ok, "live_gate_note": why, "live_after_shadow_cycles": LIVE_AFTER_SHADOW_CYCLES}


# ══════════════════════════ writes (org admin / superadmin only — the router checks) ═════════

async def _write(org: str, value: dict, actor_id: str | None, actor_email: str | None) -> dict:
    async with pm.session() as s:
        row = (await s.execute(select(OrgSetting).where(OrgSetting.org_id == str(org), OrgSetting.key == GROUP))).scalar_one_or_none()
        old = _deep(defaults(), row.value if row else None)
        if row is None:
            row = OrgSetting(org_id=str(org), key=GROUP, value=value)
            s.add(row)
        row.value = value
        row.updated_by, row.updated_at = actor_email, pm.now_utc()
        if old != value:
            s.add(OrgSettingAudit(org_id=str(org), key=GROUP, old_value=old, new_value=value, actor_id=actor_id, actor_email=actor_email))
        await s.commit()
    invalidate(str(org))
    return value


def clean_settings(v: dict, current: dict) -> dict:
    """Validate an admin's save. Mode is NOT set here (see set_mode); shadow_cycles_done is agent-owned."""
    out = _deep(current, {})
    if "enabled" in v:
        out["enabled"] = None if v["enabled"] is None else bool(v["enabled"])
    appr = v.get("approvers") or {}
    for k in ("tier1_roles", "tier2_roles"):
        if k in appr:
            roles = [str(r)[:40] for r in (appr.get(k) or []) if r][:12]
            if not roles:
                raise ValueError(f"{k} needs at least one role")
            out["approvers"][k] = roles
    for k, val in (v.get("minutes_table") or {}).items():
        if k in out["minutes_table"]:
            try:
                m = float(val)
            except (TypeError, ValueError):
                raise ValueError(f"minutes for {k} must be a number")
            if m < 0 or m > 600:
                raise ValueError(f"minutes for {k} must be between 0 and 600")
            out["minutes_table"][k] = m if m != int(m) else int(m)
    for k, val in (v.get("thresholds") or {}).items():
        if k in out["thresholds"]:
            try:
                n = float(val)
            except (TypeError, ValueError):
                raise ValueError(f"threshold {k} must be a number")
            if n < 0:
                raise ValueError(f"threshold {k} cannot be negative")
            out["thresholds"][k] = n if n != int(n) else int(n)
    if "escalation_roles" in v:
        out["escalation_roles"] = [str(r)[:40] for r in (v.get("escalation_roles") or []) if r][:12] or current["escalation_roles"]
    return out


async def save_settings(org: str, v: dict, *, actor_id: str | None, actor_email: str | None) -> dict:
    cur = await raw_settings(org)
    return await _write(org, clean_settings(v or {}, cur), actor_id, actor_email)


async def set_mode(org: str, mode: str, *, actor_id: str | None, actor_email: str | None, reason: str = "") -> dict:
    """shadow → live needs the gate (2 shadow cycles) or a reason ≥ 10 chars (recorded as the override).
    live → shadow is always allowed (and clears a stale override)."""
    if mode not in MODES:
        raise ValueError("mode must be shadow or live")
    cur = await raw_settings(org)
    if mode == "live":
        ok, _ = can_go_live(cur)
        if not ok:
            if len((reason or "").strip()) < MIN_OVERRIDE_REASON:
                raise PermissionError(can_go_live(cur)[1])
            cur["live_override"] = {"by": actor_email or actor_id, "reason": reason.strip()[:400], "at": pm.iso(pm.now_utc())}
    cur["mode"] = mode
    return await _write(org, cur, actor_id, actor_email)


async def bump_shadow_cycles(org: str) -> int:
    """Agents call this when a shadow payroll cycle completes (phase 2)."""
    cur = await raw_settings(org)
    cur["shadow_cycles_done"] = int(cur.get("shadow_cycles_done") or 0) + 1
    await _write(org, cur, None, "agent")
    return cur["shadow_cycles_done"]
