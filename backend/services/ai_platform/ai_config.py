"""Per-org AI configuration: bring-your-own key (encrypted at rest), model, caps, features.

Key resolution (``resolve_key``): the org's own key → the platform key
(``ANTHROPIC_API_KEY`` in the environment) → none. "None" is reported honestly as
"AI not configured"; the rules-based copilot still answers.
"""
import os
import sys

from sqlalchemy import select

sys.path.append("/app")
from shared.crypto_fields import FieldDecryptError, decrypt_field, encrypt_field  # noqa: E402
from shared.utils import platform_models as pm  # noqa: E402

from ai_models import AiOrgSettings  # noqa: E402
from metering import DEFAULT_PRICES_USD, MODELS, PRICE_SOURCE  # noqa: E402

DEFAULT_MODEL = "claude-opus-5-5"
EFFORTS = ("low", "medium", "high")
DEFAULT_FEATURES = {"qa": True, "actions": True, "mcp": True}

NOT_CONFIGURED = ("AI is not configured for your company: no API key on the AI settings page and no platform key. "
                  "Rules-based answers (leave balance, payslips, approvals, applying for leave) still work.")


def defaults(org_id: str) -> dict:
    return {"org_id": org_id, "has_org_key": False, "key_last4": None, "model": DEFAULT_MODEL, "effort": "medium",
            "features": dict(DEFAULT_FEATURES), "monthly_cap_tokens": 0, "monthly_cap_cad": 0.0, "cap_mode": "soft",
            "usd_cad_rate": None, "prices": {}, "updated_by": None, "updated_at": None}


def _public(row: AiOrgSettings | None, org_id: str) -> dict:
    d = defaults(org_id)
    if row is None:
        return d
    d.update({"has_org_key": bool(row.api_key_enc), "key_last4": row.key_last4, "model": row.model or DEFAULT_MODEL,
              "effort": row.effort if row.effort in EFFORTS else "medium",
              "features": {**DEFAULT_FEATURES, **(row.features or {})}, "monthly_cap_tokens": int(row.monthly_cap_tokens or 0),
              "monthly_cap_cad": float(row.monthly_cap_cad or 0), "cap_mode": row.cap_mode or "soft",
              "usd_cad_rate": row.usd_cad_rate, "prices": dict(row.prices or {}), "updated_by": row.updated_by,
              "updated_at": pm.iso(row.updated_at)})
    return d


async def load(org_id: str) -> dict:
    """Public view (no key material) + ``_key_enc`` for the resolver."""
    async with pm.session() as s:
        row = (await s.execute(select(AiOrgSettings).where(AiOrgSettings.org_id == org_id))).scalar_one_or_none()
    d = _public(row, org_id)
    d["_key_enc"] = row.api_key_enc if row else None
    return d


def platform_key() -> str | None:
    return (os.getenv("ANTHROPIC_API_KEY") or "").strip() or None


def resolve_key(cfg: dict) -> tuple[str | None, str | None]:
    """(api key, source) — source is 'org' | 'platform' | None. A bad org token falls back to the platform key."""
    enc = cfg.get("_key_enc")
    if enc:
        try:
            k = decrypt_field(enc)
            if k:
                return k, "org"
        except FieldDecryptError:
            pass
    pk = platform_key()
    return (pk, "platform") if pk else (None, None)


def sdk_available() -> bool:
    try:
        import anthropic  # noqa: F401
        return True
    except Exception:                                        # noqa: BLE001
        return False


def configured(cfg: dict) -> bool:
    return resolve_key(cfg)[0] is not None and sdk_available()


def status(cfg: dict) -> dict:
    key, src = resolve_key(cfg)
    ok = key is not None and sdk_available()
    return {"configured": ok, "key_source": src if ok else None, "model": cfg["model"] if ok else None, "effort": cfg["effort"],
            "features": cfg["features"], "message": "AI assistant is live." if ok else NOT_CONFIGURED,
            "sdk": sdk_available(), "models": MODELS, "price_source": PRICE_SOURCE,
            "default_prices_usd": {m: {"input_usd_per_mtok": p[0], "output_usd_per_mtok": p[1]} for m, p in DEFAULT_PRICES_USD.items()}}


async def save(org_id: str, body: dict, actor_email: str | None) -> dict:
    """Upsert. ``api_key``: new key (encrypted) · ``clear_key: true`` removes the org key · absent = unchanged."""
    async with pm.session() as s:
        row = (await s.execute(select(AiOrgSettings).where(AiOrgSettings.org_id == org_id))).scalar_one_or_none()
        if row is None:
            row = AiOrgSettings(org_id=org_id, features=dict(DEFAULT_FEATURES))
            s.add(row)
        key = (body.get("api_key") or "").strip()
        if key:
            row.api_key_enc, row.key_last4 = encrypt_field(key), key[-4:]
        elif body.get("clear_key"):
            row.api_key_enc, row.key_last4 = None, None
        if body.get("model"):
            row.model = str(body["model"])[:60]
        if body.get("effort") in EFFORTS:
            row.effort = body["effort"]
        if isinstance(body.get("features"), dict):
            row.features = {**DEFAULT_FEATURES, **{k: bool(v) for k, v in body["features"].items() if k in DEFAULT_FEATURES}}
        for f in ("monthly_cap_tokens",):
            if body.get(f) is not None:
                row.monthly_cap_tokens = max(0, int(body[f]))
        if body.get("monthly_cap_cad") is not None:
            row.monthly_cap_cad = max(0.0, float(body["monthly_cap_cad"]))
        if body.get("cap_mode") in ("soft", "hard"):
            row.cap_mode = body["cap_mode"]
        if "usd_cad_rate" in body:
            v = body.get("usd_cad_rate")
            row.usd_cad_rate = float(v) if v not in (None, "", 0, "0") else None
        if isinstance(body.get("prices"), dict):
            clean = {}
            for m, p in body["prices"].items():
                if isinstance(p, dict) and p.get("input_usd_per_mtok") not in (None, "") and p.get("output_usd_per_mtok") not in (None, ""):
                    clean[str(m)[:60]] = {"input_usd_per_mtok": float(p["input_usd_per_mtok"]), "output_usd_per_mtok": float(p["output_usd_per_mtok"])}
            row.prices = clean
        row.updated_by, row.updated_at = actor_email, pm.now_utc()
        await s.commit()
        return _public(row, org_id)
