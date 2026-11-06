"""Auth service — subscription payment provider interface.

Interface (`BillingProvider`): create_customer, create_subscription, update_subscription,
cancel_subscription, fetch_subscription, verify_webhook. Lexora HRM ships WITHOUT an online
card-payment adapter (a Canadian processor such as Stripe or Moneris is not built yet), so
`get_provider()` returns `NotConfigured`, whose every call raises ProviderNotConfigured —
screens show "Online payment is not set up — pay by invoice (EFT / cheque)" and nothing
pretends to have charged anyone. Tests may inject a fake provider with `set_provider()`.
"""
from __future__ import annotations


class ProviderNotConfigured(RuntimeError):
    pass


class ProviderError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status, self.body = status, body


class BillingProvider:
    name = "none"
    configured = False
    mode = "not_configured"

    async def create_customer(self, *, name: str, email: str, contact: str | None = None,
                              notes: dict | None = None) -> dict:
        raise ProviderNotConfigured(self.name)

    async def create_subscription(self, *, plan_id: str, quantity: int, total_count: int,
                                  customer_id: str | None = None, notes: dict | None = None) -> dict:
        raise ProviderNotConfigured(self.name)

    async def update_subscription(self, subscription_id: str, *, plan_id: str | None = None,
                                  quantity: int | None = None, at_cycle_end: bool = False) -> dict:
        raise ProviderNotConfigured(self.name)

    async def cancel_subscription(self, subscription_id: str, *, at_cycle_end: bool = True) -> dict:
        raise ProviderNotConfigured(self.name)

    async def fetch_subscription(self, subscription_id: str) -> dict:
        raise ProviderNotConfigured(self.name)

    def verify_webhook(self, raw_body: bytes, signature: str | None) -> bool:
        return False

    def public_info(self) -> dict:
        return {"provider": self.name, "configured": self.configured, "mode": self.mode}


class NotConfigured(BillingProvider):
    name = "none"


_override: BillingProvider | None = None


def set_provider(p: BillingProvider | None) -> None:
    """Tests inject a provider."""
    global _override
    _override = p


def get_provider() -> BillingProvider:
    if _override is not None:
        return _override
    return NotConfigured()
