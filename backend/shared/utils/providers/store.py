"""Provider configuration store: read / write ``int_providers`` with secrets
encrypted at rest (shared/utils/crypto.py — the SIN scheme, never a second one).

    cfg = await load(org_id, "whatsapp")        # decrypted, for the connector
    pub = await public(org_id, "whatsapp")      # masked, for the screen
    await save(org_id, "whatsapp", mode, enabled, patch, actor)
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from shared.utils import crypto
from shared.utils import platform_models as pm
from shared.utils.providers import base
from shared.utils.providers.models import IntegrationProvider

log = logging.getLogger("lexora.providers")

KEEP = "••••••••"      # the screen sends this back to mean "leave the saved secret alone"


def mask_secret(v) -> str:
    if not v:
        return ""
    return KEEP


def encrypt_config(provider: base.Provider, mode: str, cfg: dict, previous: dict | None = None) -> dict:
    """Encrypt the secret fields of ``cfg``. A value of KEEP keeps the previous ciphertext."""
    secrets = provider.secret_keys(mode)
    out: dict = {}
    for k, v in (cfg or {}).items():
        if k in secrets:
            if v == KEEP:
                if previous and previous.get(k):
                    out[k] = previous[k]
                continue
            if v in (None, ""):
                continue
            out[k] = crypto.encrypt(str(v), aad=f"int:{provider.key}:{k}")
        else:
            out[k] = v
    return out


def decrypt_config(provider: base.Provider, mode: str, cfg: dict) -> dict:
    secrets = provider.secret_keys(mode)
    out: dict = {}
    for k, v in (cfg or {}).items():
        if k in secrets and v:
            try:
                out[k] = crypto.decrypt(v, aad=f"int:{provider.key}:{k}")
            except crypto.CryptoError:
                out[k] = None
        else:
            out[k] = v
    return out


def public_config(provider: base.Provider, mode: str, cfg: dict) -> dict:
    secrets = provider.secret_keys(mode)
    return {k: (mask_secret(v) if k in secrets else v) for k, v in (cfg or {}).items()}


async def _row(s, org_id: str, provider: str) -> IntegrationProvider | None:
    return (await s.execute(select(IntegrationProvider).where(
        IntegrationProvider.org_id == str(org_id), IntegrationProvider.provider == provider))).scalar_one_or_none()


async def load(org_id, provider_key: str) -> dict:
    """Decrypted config for a connector: {mode, enabled, config, provider}. Never raises."""
    p = base.get(provider_key)
    if not p:
        return {"mode": "", "enabled": False, "config": {}, "provider": None}
    try:
        async with pm.session() as s:
            r = await _row(s, org_id, provider_key)
    except Exception as e:                                   # noqa: BLE001
        log.warning("providers.load(%s) failed: %s", provider_key, e)
        r = None
    if not r:
        return {"mode": next(iter(p.modes), ""), "enabled": False, "config": {}, "provider": p}
    return {"mode": r.mode or next(iter(p.modes), ""), "enabled": bool(r.enabled),
            "config": decrypt_config(p, r.mode, r.config or {}), "provider": p}


async def public(org_id, provider_key: str) -> dict:
    """Screen shape: masked secrets + live status."""
    p = base.get(provider_key)
    if not p:
        return {}
    async with pm.session() as s:
        r = await _row(s, org_id, provider_key)
    mode = (r.mode if r and r.mode else next(iter(p.modes), ""))
    cfg = r.config if r else {}
    dec = decrypt_config(p, mode, cfg)
    st = await p.status(mode, dec, bool(r and r.enabled))
    return {**p.describe(), "mode": mode, "enabled": bool(r and r.enabled),
            "config": public_config(p, mode, cfg), "status": st["state"], "status_message": st["message"],
            "environment": st.get("environment"),                       # S5: sandbox | live | None
            "last_test_at": pm.iso(r.last_test_at) if r else None, "last_test_ok": r.last_test_ok if r else None,
            "last_error": r.last_error if r else None, "updated_at": pm.iso(r.updated_at) if r else None,
            "updated_by": r.updated_by if r else None}


async def save(org_id, provider_key: str, *, mode: str | None, enabled: bool | None, config: dict | None,
               actor: str | None) -> dict:
    p = base.get(provider_key)
    if not p:
        raise ValueError("Unknown provider")
    async with pm.session() as s:
        r = await _row(s, org_id, provider_key)
        if not r:
            r = IntegrationProvider(org_id=str(org_id), provider=provider_key, mode=next(iter(p.modes), ""),
                                    enabled=False, config={})
            s.add(r)
        if mode is not None:
            if p.modes and mode not in p.modes:
                raise ValueError("Unknown mode")
            if mode != r.mode:
                r.config = {}            # a different mode has different fields (and secrets)
            r.mode = mode
        if config is not None:
            allowed = {f.key for f in p.fields_for(r.mode)}
            clean = {k: v for k, v in config.items() if k in allowed}
            r.config = encrypt_config(p, r.mode, {**{k: v for k, v in (r.config or {}).items()
                                                      if k not in clean and k in allowed}, **clean},
                                      previous=r.config or {})
        if enabled is not None:
            r.enabled = bool(enabled)
        r.updated_by = actor
        r.updated_at = pm.now_utc()
        r.last_error = None
        await s.commit()
    return await public(org_id, provider_key)


async def record_test(org_id, provider_key: str, ok: bool, message: str) -> None:
    try:
        async with pm.session() as s:
            r = await _row(s, org_id, provider_key)
            if r:
                r.last_test_at, r.last_test_ok = pm.now_utc(), bool(ok)
                r.last_error = None if ok else (message or "")[:400]
                await s.commit()
    except Exception as e:                                   # noqa: BLE001
        log.warning("record_test failed: %s", e)


async def record_error(org_id, provider_key: str, message: str) -> None:
    try:
        async with pm.session() as s:
            r = await _row(s, org_id, provider_key)
            if r:
                r.last_error = (message or "")[:400]
                await s.commit()
    except Exception:                                        # noqa: BLE001
        pass
