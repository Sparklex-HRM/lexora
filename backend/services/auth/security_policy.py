"""Auth service — launch security basics: password policy + login rate limit.

Both are pure-Python and in-process (no Redis dependency) so they work on a
single-replica deployment; the limiter is keyed by email AND by client IP.
"""
import os
import re
import time
from collections import defaultdict

# ── Password policy ─────────────────────────────────────────────────────────
MIN_LEN = int(os.getenv("LEXORA_PASSWORD_MIN_LEN", "10"))
COMMON = {"password", "password1", "lexora@2026", "maple@2026", "welcome123", "admin123", "qwerty123",
          "12345678", "123456789", "1234567890", "iloveyou", "letmein"}


def password_problems(pw: str, *, email: str | None = None) -> list[str]:
    """Plain-English reasons a password is rejected. Empty list = OK."""
    pw = pw or ""
    out = []
    if len(pw) < MIN_LEN:
        out.append(f"at least {MIN_LEN} characters")
    if not re.search(r"[A-Z]", pw):
        out.append("one capital letter")
    if not re.search(r"[a-z]", pw):
        out.append("one small letter")
    if not re.search(r"\d", pw):
        out.append("one number")
    if not re.search(r"[^A-Za-z0-9]", pw):
        out.append("one symbol (e.g. @ # !)")
    if pw.lower() in COMMON:
        out.append("not a common password")
    if email and pw.lower() and email.split("@")[0].lower() in pw.lower() and len(email.split("@")[0]) >= 4:
        out.append("must not contain your email name")
    return out


POLICY_TEXT = (f"Passwords need {MIN_LEN}+ characters with a capital letter, a small letter, "
               "a number and a symbol, must not be a common or breached password, and must not "
               "repeat one of your last passwords.")


# ── Login rate limit ────────────────────────────────────────────────────────
MAX_FAILURES = int(os.getenv("LEXORA_LOGIN_MAX_FAILURES", "5"))
WINDOW_SEC = int(os.getenv("LEXORA_LOGIN_WINDOW_SEC", "900"))     # 15 minutes
LOCK_SEC = int(os.getenv("LEXORA_LOGIN_LOCK_SEC", "900"))


class LoginLimiter:
    def __init__(self, max_failures=MAX_FAILURES, window=WINDOW_SEC, lock=LOCK_SEC, clock=time.time):
        self.max_failures, self.window, self.lock, self.clock = max_failures, window, lock, clock
        self._fails: dict[str, list[float]] = defaultdict(list)
        self._locked_until: dict[str, float] = {}

    def _prune(self, key: str, now: float):
        self._fails[key] = [t for t in self._fails[key] if now - t < self.window]

    def retry_after(self, key: str) -> int:
        """Seconds until `key` may try again; 0 = not locked."""
        now = self.clock()
        until = self._locked_until.get(key, 0)
        if until > now:
            return int(until - now) or 1
        if until:
            self._locked_until.pop(key, None)
            self._fails.pop(key, None)
        return 0

    def record_failure(self, key: str) -> int:
        now = self.clock()
        self._prune(key, now)
        self._fails[key].append(now)
        if len(self._fails[key]) >= self.max_failures:
            self._locked_until[key] = now + self.lock
            return self.lock
        return 0

    def record_success(self, key: str):
        self._fails.pop(key, None)
        self._locked_until.pop(key, None)

    def failures(self, key: str) -> int:
        self._prune(key, self.clock())
        return len(self._fails[key])


# S1: lockouts are shared across replicas via Redis (in-process twin when Redis is down).
from shared.utils.ratelimit import make_login_limiter  # noqa: E402
limiter = make_login_limiter(MAX_FAILURES, WINDOW_SEC, LOCK_SEC, name="login")
# S4: per-ACCOUNT lock (e-mail + org code, any IP) — catches guessing spread over
# many addresses. Looser than the per-IP one so a shared office NAT is not hurt.
_ACCT = (int(os.getenv("LEXORA_ACCOUNT_MAX_FAILURES", "20")), 3600, int(os.getenv("LEXORA_ACCOUNT_LOCK_SEC", "1800")))
account_limiter = make_login_limiter(*_ACCT, name="login_account")
