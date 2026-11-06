"""Lexora HRM — company settings: the key registry + a cached read helper.

    from shared.utils.org_settings import get_setting
    prefix = await get_setting(org_id, "numbering.employee_prefix", "EMP")
    offs   = await get_setting(org_id, "work_week.weekly_offs")

A key is ``<group>`` or ``<group>.<field>``. Values are stored one row per group in
``org_settings`` (owned by the notification service, which also serves
``GET /settings/{key}``). Reads go straight to the shared database and are cached in
this process for ``CACHE_SECONDS``; a write through the settings API clears the
cache of the process that served it — other processes catch up within the TTL.

Resolution order: stored value → the group's default below → the caller's default.
The helper never raises; on any failure it returns the default.
"""
import copy
import logging
import time

from sqlalchemy import select

from shared.utils import platform_models as pm

log = logging.getLogger("lexora.settings")

CACHE_SECONDS = 60

# Who may READ a group. Writing is always org_admin only.
READ_ALL = "all"            # every signed-in user of the org
READ_OFFICE = "office"      # org_admin + hr + payroll
READ_ADMIN = "admin"        # org_admin only

GROUPS: dict[str, dict] = {
    "company": {
        "label": "Company profile", "read": READ_ALL,
        "default": {"legal_name": "", "trade_name": "", "phone": "", "email": "", "website": "",
                    "addresses": []},   # [{label, line1, line2, city, state, postal_code, country}]
    },
    "registrations": {
        "label": "Registration numbers", "read": READ_OFFICE,
        "default": {"items": []},       # [{label, value}] — stored and shown exactly as typed
    },
    "locale": {
        "label": "Country, currency and financial year", "read": READ_ALL,
        # timezone: IANA name that decides the org's business "today" (shared/utils/org_time.py).
        "default": {"country": "CA", "currency": "CAD", "fy_start_month": 1, "timezone": "America/Toronto"},
    },
    "work_week": {
        "label": "Working week", "read": READ_ALL,
        # Python weekday numbers: Monday = 0 … Sunday = 6
        "default": {"working_days": [0, 1, 2, 3, 4], "weekly_offs": [5, 6]},
    },
    "work_hours": {
        "label": "Default work hours", "read": READ_ALL,
        "default": {"start": "09:00", "end": "18:00", "hours_per_day": 8, "break_minutes": 60},
    },
    "notification_sender": {
        "label": "E-mail sender", "read": READ_ADMIN,
        "default": {"sender_name": "", "reply_to": ""},
    },
    "numbering": {
        "label": "Numbering prefixes", "read": READ_ALL,
        "default": {"employee_prefix": "EMP", "requisition_prefix": "REQ", "offer_prefix": "OFR",
                    "letter_prefix": "LTR", "revision_prefix": "REV", "exit_prefix": "EXT",
                    "padding": 4},
    },
    "approvals": {
        "label": "Approvals", "read": READ_ALL,
        # Remind approvers when an item has waited this many days. 0 switches reminders off.
        "default": {"escalation_days": 3},
    },
    # G5 — AI watchdogs: the on/off switch per agent and the thresholds each one uses.
    # ``thresholds`` is {agent_key: {name: value}}; anything not set falls back to the
    # agent's own default (see services/ai_platform/watchdogs.py).
    "ai_watchdogs": {
        "label": "AI watchdogs", "read": READ_OFFICE,
        "default": {"enabled": True, "disabled_agents": [], "thresholds": {}, "notify": True},
    },
    # Agentic operations add-on (2026-10-25, project_jethro_agentic_ai). ``enabled`` None = follow the
    # purchased add-on (bought ⇒ on); True / False = an explicit admin switch. ``mode`` shadow = the
    # agents record "what I would have done" and never execute; live = proposals go to the approval
    # inbox and execute once approved. Live needs 2 shadow cycles or an admin override with a reason.
    # The helper is services/ai_platform/agentic_state.py (agentic_state(org)).
    "ai_agentic": {
        "label": "Agentic operations", "read": READ_OFFICE,
        "default": {
            "enabled": None, "mode": "shadow", "shadow_cycles_done": 0,
            "live_override": None,                   # {by, reason, at} when live was switched on early
            "approvers": {"tier1_roles": ["org_admin", "hr_admin", "payroll_admin"],
                          "tier2_roles": ["org_admin", "payroll_admin"]},
            # Proxy-value minutes per outcome — conservative defaults, editable by an admin, shown on the Value tab.
            "minutes_table": {"query_resolved": 10, "exception_confirmed": 15, "filing_prepared": 60,
                              "input_chased": 5, "variance_explained": 5, "reconciliation_done": 30,
                              "review_minutes_per_decision": 3},
            "thresholds": {"net_delta_pct": 5, "net_delta_floor_cad": 1000, "bulk_employees": 25,
                           "chase_every_hours": 24, "escalate_after_chases": 3, "proposal_expiry_hours": 72,
                           "session_grant_hours": 8},
            "escalation_roles": ["hr_admin", "org_admin"],
        },
    },
    # G5 — HR copilot: daily token budgets. 0 = no limit.
    "ai_copilot": {
        "label": "HR copilot", "read": READ_ALL,
        "default": {"enabled": True, "org_tokens_per_day": 300000, "user_tokens_per_day": 30000},
    },
    # HR Desk (stream D, 2026-10-25): abstain threshold, harassment-complaint committee ("ic") contact + member logins,
    # fixed-message overrides, SLA hours per routed class, helpline. Full defaults: services/ai_platform/desk/settings.py.
    # org_admin only: the IC member list decides who may open the sealed referral inbox.
    # Agentic chase channel (2026-10-26, shared/utils/whatsapp_channel.py): WhatsApp OFF by default; template names only;
    # `token_env` = the NAME of the env var that holds the Meta token — ⛔ never a token / secret value here.
    # Read/written through GET/PUT /ai/agents/channels/whatsapp (org admin).
    "notify": {
        "label": "Notification channels (agent reminders)", "read": READ_ADMIN,
        "default": {"whatsapp": {"enabled": False, "provider": "meta_cloud", "phone_number_id": "", "api_version": "v21.0", "token_env": "",
                                 "language": "en", "after_reminders": 2, "templates": {"agent_chase": ""}}},
    },
    "ai_desk": {
        "label": "HR Desk (Ask HR assistant)", "read": READ_ADMIN,
        "default": {"abstain_threshold": 0.35, "top_k": 5,
                    "ic": {"presiding_officer_name": "", "presiding_officer_contact": "", "members": [], "external_member_contact": ""},
                    "templates": {}, "sla_hours": {}, "helpline": {"name": "9-8-8 Suicide Crisis Helpline (Canada, 24/7, free)", "number": "Call or text 988"},
                    "handoff_unresolved_attempts": 2, "handoff_abstains": 2},
    },
}

_cache: dict[tuple[str, str], tuple[float, object]] = {}


def invalidate(org_id: str | None = None) -> None:
    if org_id is None:
        _cache.clear()
        return
    for k in [k for k in _cache if k[0] == str(org_id)]:
        _cache.pop(k, None)


def split_key(key: str) -> tuple[str, list[str]]:
    parts = [p for p in (key or "").split(".") if p]
    return (parts[0] if parts else ""), parts[1:]


def default_for(group: str):
    g = GROUPS.get(group)
    return copy.deepcopy(g["default"]) if g else None


def merged(group: str, stored):
    """Stored value laid over the group's default (one level deep)."""
    base = default_for(group)
    if isinstance(base, dict) and isinstance(stored, dict):
        base.update(stored)
        return base
    return stored if stored is not None else base


def can_read(group: str, role: str | None) -> bool:
    from shared.utils.directory import role_in
    g = GROUPS.get(group)
    if not g:
        return False
    if g["read"] == READ_ALL:
        return bool(role)
    if g["read"] == READ_OFFICE:
        return role_in(role, ["org_admin", "hr", "payroll"]) or role == "superadmin"
    return role in ("org_admin", "superadmin")


def can_write(role: str | None) -> bool:
    return role in ("org_admin", "superadmin")


async def _load_group(org_id: str, group: str):
    ck = (str(org_id), group)
    hit = _cache.get(ck)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    async with pm.session() as s:
        row = (await s.execute(select(pm.OrgSetting).where(
            pm.OrgSetting.org_id == str(org_id), pm.OrgSetting.key == group))).scalar_one_or_none()
    value = merged(group, row.value if row else None)
    if group == "locale" and isinstance(value, dict) and not value.get("country"):
        value.update(await _locale_from_org(str(org_id)))
    _cache[ck] = (time.monotonic(), value)
    return value


COUNTRY_LOCALE = {"CA": {"currency": "CAD", "fy_start_month": 1, "timezone": "America/Toronto"}}


async def _locale_from_org(org_id: str) -> dict:
    """Until an admin saves the locale, take the country the org signed up with."""
    try:
        from sqlalchemy import text
        async with pm.session() as s:
            r = (await s.execute(text("SELECT country FROM organizations WHERE id = CAST(:org AS uuid)"),
                                 {"org": org_id})).first()
        country = (r[0] or "").upper() if r else ""
    except Exception:                                        # noqa: BLE001
        return {}
    if not country:
        return {}
    return {"country": country, **COUNTRY_LOCALE.get(country, {})}


async def get_setting(org_id, key, default=None):
    """Read one setting (``group`` or ``group.field[.field]``). Never raises."""
    group, path = split_key(key)
    try:
        value = await _load_group(str(org_id), group)
    except Exception as e:                                   # noqa: BLE001
        log.warning("get_setting(%s, %s) failed: %s", org_id, key, e)
        value = default_for(group)
    for p in path:
        if isinstance(value, dict) and p in value:
            value = value[p]
        else:
            return default
    if value is None or value == "":
        return default if default is not None else value
    return copy.deepcopy(value)
