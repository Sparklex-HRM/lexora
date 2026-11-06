"""API gateway — cheap "is this login still good?" check.

A JWT is valid until it expires (60 min by default), so on its own a disabled
user would keep working for up to an hour. On every authenticated request the
gateway therefore looks at the login's CURRENT state:

    * the login was deleted                      → 401
    * the login is disabled                      → 401
    * the token was issued before the login's
      `tokens_valid_after` (disable, password
      set / reset, role change)                  → 401

The state is one indexed primary-key read, cached in-process for a moment
(LEXORA_REVOCATION_TTL_SEC, default 2 — was 10 before 2026-10-21), so a disable takes
effect within that window on every gateway replica. The same read returns the login's live role
and employee id, which the gateway stamps — so a role change or a newly linked
employee applies without waiting for the token to expire.

If the database cannot be read the gateway FAILS CLOSED: 503 "Could not check your
session" (decision 2026-10-21 — a disabled login must never slip through while the
session store is unreachable). A failed read is never cached, so the next request
tries again. There is no switch to fail open.
"""
import os
import sys
import time

sys.path.append("/app")
from shared.utils import identity  # noqa: E402

TTL_SEC = min(float(os.getenv("LEXORA_REVOCATION_TTL_SEC", "2")), 10.0)   # never more than 10 s

MESSAGES = {
    "gone": "This login no longer exists. Sign in again.",
    "disabled": "This login has been disabled. Contact your HR team.",
    "stale": "Your session has ended. Sign in again.",
    "unavailable": "Could not check your session. Try again in a moment.",
}


def verdict(state: dict | None, token_iat) -> str | None:
    """Why a request must be refused: 'gone' | 'disabled' | 'stale'. None = allowed."""
    if state is None:
        return "gone"
    if not state.get("is_active"):
        return "disabled"
    cutoff = state.get("tokens_valid_after")
    if cutoff is not None:
        try:
            iat = int(token_iat)
        except (TypeError, ValueError):
            return "stale"                    # a token with no issue time cannot be trusted past a cut-off
        # JWT `iat` is whole seconds. A token issued in the SAME second as the
        # cut-off is refused too (it may predate it); sign-in waits out that
        # second so a fresh token always lands after the cut-off.
        if iat <= int(cutoff.timestamp()):
            return "stale"
    return None


class UserStateCache:
    """user id → login state, remembered for `ttl` seconds."""

    def __init__(self, loader=identity.fetch_user_state, ttl: float = TTL_SEC, clock=time.monotonic):
        self.loader, self.ttl, self.clock = loader, ttl, clock
        self._items: dict[str, tuple[float, dict | None]] = {}

    async def get(self, user_id: str) -> dict | None:
        t = self.clock()
        hit = self._items.get(user_id)
        if hit and hit[0] > t:
            return hit[1]
        state = await self.loader(user_id)
        if self.ttl > 0:
            if len(self._items) > 50000:
                self._items.clear()
            self._items[user_id] = (t + self.ttl, state)
        return state

    def forget(self, user_id: str | None = None):
        if user_id is None:
            self._items.clear()
        else:
            self._items.pop(user_id, None)


cache = UserStateCache()


async def check(user_id: str, token_iat) -> tuple[str | None, dict | None]:
    """(reason to refuse or None, live login state or None)."""
    try:
        state = await cache.get(str(user_id))
    except Exception:  # noqa: BLE001 — any failure to read the store refuses (fail closed)
        return "unavailable", None
    return verdict(state, token_iat), state
