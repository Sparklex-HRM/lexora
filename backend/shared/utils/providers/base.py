"""Provider interface + registry (stream G3).

Every outside service Lexora HRM can talk to is a ``Provider``: a key, a label, the
fields its config screen shows (which of them are secrets), the modes it can
run in, and two async methods:

    status(cfg)  → {"state": configured | not_configured | error, "message": …}
    test(cfg)    → {"ok": bool, "message": …}  — a REAL call with a timeout, or an
                   honest "Not configured" when the credentials are missing.

HONESTY RULE: no provider ever reports "sent", "verified" or "signed" without the
outside system's own confirmation. A stub (interface only, no credentials) says
"Not configured" and refuses the action with ``NotConfigured``.
"""
from __future__ import annotations


class NotConfigured(Exception):
    """The provider has no usable credentials. Message is screen-ready."""

    def __init__(self, message: str = "Not configured"):
        super().__init__(message)
        self.message = message


class ProviderError(Exception):
    """The provider was called and refused / failed. Message is screen-ready."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class Field:
    __slots__ = ("key", "label", "secret", "required", "help", "kind")

    def __init__(self, key: str, label: str, *, secret: bool = False, required: bool = True,
                 help: str = "", kind: str = "text"):
        self.key, self.label, self.secret, self.required, self.help, self.kind = key, label, secret, required, help, kind

    def out(self) -> dict:
        return {"key": self.key, "label": self.label, "secret": self.secret, "required": self.required,
                "help": self.help, "kind": self.kind}


class Provider:
    key: str = ""
    label: str = ""
    group: str = ""                      # identity | messaging | documents | verification | money | compliance
    modes: dict[str, str] = {}           # mode key → label ("" when the provider has one mode)
    fields: dict[str, list[Field]] = {}  # mode → fields ("" mode for single-mode providers)
    stub: bool = False                   # interface + config only: the real call is not built
    note: str = ""                       # one honest sentence shown on the screen

    def fields_for(self, mode: str) -> list[Field]:
        return self.fields.get(mode) or self.fields.get("") or []

    def secret_keys(self, mode: str) -> set[str]:
        return {f.key for f in self.fields_for(mode) if f.secret}

    def missing(self, mode: str, cfg: dict) -> list[str]:
        return [f.label for f in self.fields_for(mode) if f.required and not (cfg or {}).get(f.key)]

    async def status(self, mode: str, cfg: dict, enabled: bool) -> dict:
        miss = self.missing(mode, cfg)
        if not enabled:
            return {"state": "not_configured", "message": "Switched off."}
        if miss:
            return {"state": "not_configured", "message": "Not configured: missing " + ", ".join(miss)}
        if self.stub:
            return {"state": "not_configured",
                    "message": "Credentials saved. The live call for this provider is not built yet; nothing is sent."}
        return {"state": "configured", "message": "Configured."}

    async def test(self, mode: str, cfg: dict) -> dict:
        miss = self.missing(mode, cfg)
        if miss:
            return {"ok": False, "message": "Not configured: missing " + ", ".join(miss)}
        if self.stub:
            return {"ok": False, "message": "Not built: this provider has a config screen only. No call was made."}
        return {"ok": False, "message": "This provider has no test call."}

    def describe(self) -> dict:
        return {"key": self.key, "label": self.label, "group": self.group, "stub": self.stub, "note": self.note,
                "docs_url": getattr(self, "docs_url", "") or "",
                "modes": [{"key": k, "label": v} for k, v in self.modes.items()],
                "fields": {m: [f.out() for f in fs] for m, fs in self.fields.items()}}


_REGISTRY: dict[str, Provider] = {}


def register(p: Provider) -> Provider:
    _REGISTRY[p.key] = p
    return p


def get(key: str) -> Provider | None:
    _load_all()
    return _REGISTRY.get(key)


def all_providers() -> list[Provider]:
    _load_all()
    return list(_REGISTRY.values())


_loaded = False


def _load_all():
    """Import the provider modules once (they register themselves)."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    import importlib
    for mod in ("esign", "bgv", "whatsapp", "chatops"):
        importlib.import_module(f"shared.utils.providers.{mod}")
