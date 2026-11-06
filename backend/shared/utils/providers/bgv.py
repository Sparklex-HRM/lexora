"""Background verification (BGV).

``manual`` mode is fully working: HR records each check (type, vendor,
reference, result, uploaded report) on the BGV screen; the rows are the record.
``api`` mode is an interface + config screen for a vendor API (AuthBridge,
SpringVerify, …): ``order_check`` raises NotConfigured until a connector exists.
"""
from __future__ import annotations

from shared.utils.providers.base import Field, NotConfigured, Provider, register

CHECK_TYPES = {"identity": "Identity", "address": "Address", "education": "Education",
               "employment": "Past employment", "criminal": "Criminal record", "reference": "Reference",
               "credit": "Credit", "drug": "Drug test"}
STATUSES = {"requested": "Requested", "in_progress": "In progress", "clear": "Clear",
            "discrepancy": "Discrepancy", "failed": "Could not verify", "cancelled": "Cancelled"}
FINAL = {"clear", "discrepancy", "failed", "cancelled"}


class Bgv(Provider):
    key, label, group = "bgv", "Background verification", "verification"
    modes = {"manual": "Manual (HR records the vendor's result)", "api": "Vendor API"}
    fields = {"manual": [Field("default_vendor", "Default vendor name", required=False)],
              "api": [Field("api_url", "Vendor API URL"), Field("api_key", "API key", secret=True),
                      Field("webhook_secret", "Result webhook secret", secret=True, required=False)]}
    note = "Manual mode is built. Vendor-API mode is an interface + config only (no orders are placed)."

    async def status(self, mode, cfg, enabled):
        if mode == "manual":
            return {"state": "configured", "message": "Manual mode: HR records each check and its result."}
        miss = self.missing(mode, cfg)
        if miss:
            return {"state": "not_configured", "message": "Not configured: missing " + ", ".join(miss)}
        return {"state": "not_configured", "message": "Credentials saved. The vendor connector is not built; no orders are placed."}

    async def test(self, mode, cfg):
        if mode == "manual":
            return {"ok": True, "message": "Manual mode needs no connection."}
        return {"ok": False, "message": "Not built: vendor-API mode has a config screen only. No call was made."}


register(Bgv())


def valid_transition(old: str, new: str) -> bool:
    if new not in STATUSES:
        return False
    if old in FINAL and new != old:
        return new == "cancelled" and old != "cancelled" or False
    return True


async def order_check(cfg: dict, check_type: str, employee: dict) -> dict:
    raise NotConfigured("Vendor-API BGV is not configured. Record the check manually instead.")
