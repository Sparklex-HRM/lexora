"""Lexora HRM — rate limiters shared by every replica (Redis), with a safe fallback.

    from shared.utils.ratelimit import make_limiter, make_login_limiter, client_ip

    page_limiter = make_limiter(120, 300, name="onboarding_page")   # 120 hits / 5 min
    wait = page_limiter.hit(f"ip|{client_ip(request)}")             # 0 = allowed, else seconds
    wait = await page_limiter.ahit(key)                             # same, without blocking the loop

    login = make_login_limiter(5, 900, 900, name="login")           # failures → lock
    login.retry_after(key); login.record_failure(key); login.record_success(key)

Backend choice (``make_*``): Redis when ``REDIS_URL`` is set in the environment (it is in
docker-compose / k8s) or ``LEXORA_RATELIMIT_BACKEND=redis``; ``LEXORA_RATELIMIT_BACKEND=memory``
forces the in-process limiter. Without either (unit tests, a laptop) it is in-process.

Redis down: every limiter keeps an in-process twin. A Redis error is logged (once per
outage), Redis is skipped for ``COOLDOWN_SEC`` and the twin enforces the limit on this
replica — the old behaviour, looser across replicas but never "no limit". Pure READS
(``retry_after`` / ``failures``) fail OPEN (0) when Redis is unreachable, so an outage can
never lock people out.

Counting: fixed windows in Redis (``INCR`` + ``EXPIRE`` on the first hit, atomic in one
MULTI) — the wait returned is the window's remaining TTL.
"""
from __future__ import annotations

import logging
import os
import time
from collections import defaultdict, deque

log = logging.getLogger("lexora.ratelimit")

KEY_PREFIX = "lx:rl:"
COOLDOWN_SEC = 30
_SOCKET_TIMEOUT = float(os.getenv("LEXORA_REDIS_TIMEOUT_SEC", "0.3"))


class RateLimiter:
    """In-process sliding window (one replica). Also the fallback twin of the Redis limiter."""

    backend = "memory"

    def __init__(self, max_hits: int, window_sec: int, clock=time.time, name: str | None = None):
        self.max_hits, self.window, self.clock = max_hits, window_sec, clock
        self.name = name
        self._hits: dict[str, deque] = defaultdict(deque)

    def hit(self, key: str) -> int:
        """Record one request. Returns 0 if allowed, else seconds to wait."""
        t = self.clock()
        q = self._hits[key]
        while q and t - q[0] >= self.window:
            q.popleft()
        if len(q) >= self.max_hits:
            return max(int(self.window - (t - q[0])), 1)
        q.append(t)
        if len(self._hits) > 20000:          # crude memory bound
            for k in [k for k, v in self._hits.items() if not v or t - v[-1] >= self.window][:5000]:
                self._hits.pop(k, None)
        return 0

    async def ahit(self, key: str) -> int:
        return self.hit(key)

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)


# ── Redis plumbing ─────────────────────────────────────────────────────────────
_sync_clients: dict[str, object] = {}
_async_clients: dict[str, object] = {}


def _redis_url() -> str:
    from shared.utils.config import settings
    return os.getenv("REDIS_URL") or settings.REDIS_URL


def _sync_client(url: str):
    c = _sync_clients.get(url)
    if c is None:
        import redis
        c = redis.Redis.from_url(url, socket_timeout=_SOCKET_TIMEOUT, socket_connect_timeout=_SOCKET_TIMEOUT,
                                 decode_responses=True)
        _sync_clients[url] = c
    return c


def _async_client(url: str):
    c = _async_clients.get(url)
    if c is None:
        import redis.asyncio as aredis
        c = aredis.Redis.from_url(url, socket_timeout=_SOCKET_TIMEOUT, socket_connect_timeout=_SOCKET_TIMEOUT,
                                  decode_responses=True)
        _async_clients[url] = c
    return c


class _Breaker:
    """Skip Redis for COOLDOWN_SEC after an error; log once per outage."""

    def __init__(self, name: str, clock=time.monotonic):
        self.name, self.clock, self.down_until, self.logged = name, clock, 0.0, False

    def open(self) -> bool:
        return self.clock() < self.down_until

    def trip(self, err: Exception):
        self.down_until = self.clock() + COOLDOWN_SEC
        if not self.logged:
            log.warning("rate limiter %s: Redis unavailable (%s) — using the in-process limiter for %ss",
                        self.name, err.__class__.__name__, COOLDOWN_SEC)
            self.logged = True

    def ok(self):
        if self.logged:
            log.info("rate limiter %s: Redis is back", self.name)
        self.down_until, self.logged = 0.0, False


class RedisRateLimiter:
    """Same contract as RateLimiter (``hit(key)`` → 0 or seconds to wait), counted in Redis so
    every replica shares one budget. ``client`` / ``aclient`` may be injected (tests)."""

    backend = "redis"

    def __init__(self, max_hits: int, window_sec: int, name: str | None = None, *,
                 url: str | None = None, client=None, aclient=None, clock=time.time):
        self.max_hits, self.window = int(max_hits), int(window_sec)
        self.name = name or f"{max_hits}per{window_sec}"
        self.url = url
        self._client, self._aclient = client, aclient
        self.fallback = RateLimiter(max_hits, window_sec, clock=clock, name=self.name)
        self.breaker = _Breaker(self.name)

    # the memory twin's dict — keeps old tests that clear it working
    @property
    def _hits(self):
        return self.fallback._hits

    def _key(self, key: str) -> str:
        return f"{KEY_PREFIX}{self.name}:{key}"

    def _c(self):
        return self._client if self._client is not None else _sync_client(self.url or _redis_url())

    def _ac(self):
        return self._aclient if self._aclient is not None else _async_client(self.url or _redis_url())

    def _verdict(self, count, ttl) -> int:
        if int(count) <= self.max_hits:
            return 0
        ttl = int(ttl) if ttl is not None else -1
        return ttl if ttl > 0 else self.window

    def hit(self, key: str) -> int:
        if self.breaker.open():
            return self.fallback.hit(key)
        k = self._key(key)
        try:
            p = self._c().pipeline(transaction=True)
            p.set(k, 0, ex=self.window, nx=True)
            p.incr(k)
            p.ttl(k)
            _, count, ttl = p.execute()
            self.breaker.ok()
            return self._verdict(count, ttl)
        except Exception as e:                                # noqa: BLE001 — any Redis failure
            self.breaker.trip(e)
            return self.fallback.hit(key)

    async def ahit(self, key: str) -> int:
        if self.breaker.open():
            return self.fallback.hit(key)
        k = self._key(key)
        try:
            p = self._ac().pipeline(transaction=True)
            p.set(k, 0, ex=self.window, nx=True)
            p.incr(k)
            p.ttl(k)
            _, count, ttl = await p.execute()
            self.breaker.ok()
            return self._verdict(count, ttl)
        except Exception as e:                                # noqa: BLE001
            self.breaker.trip(e)
            return self.fallback.hit(key)

    def reset(self, key: str) -> None:
        self.fallback.reset(key)
        if self.breaker.open():
            return
        try:
            self._c().delete(self._key(key))
        except Exception as e:                                # noqa: BLE001
            self.breaker.trip(e)


# ── failure lockout (sign-in) ──────────────────────────────────────────────────
class MemoryLoginLimiter:
    """N failures inside ``window`` → locked for ``lock`` seconds (in-process)."""

    backend = "memory"

    def __init__(self, max_failures: int, window: int, lock: int, clock=time.time, name: str | None = None):
        self.max_failures, self.window, self.lock, self.clock, self.name = max_failures, window, lock, clock, name
        self._fails: dict[str, list[float]] = defaultdict(list)
        self._locked_until: dict[str, float] = {}

    def _prune(self, key, now):
        self._fails[key] = [t for t in self._fails[key] if now - t < self.window]

    def retry_after(self, key: str) -> int:
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


class RedisLoginLimiter:
    """Drop-in for MemoryLoginLimiter (and services/auth/security_policy.LoginLimiter) shared
    across replicas. Reads fail OPEN when Redis is down; writes fall back to the memory twin."""

    backend = "redis"

    def __init__(self, max_failures: int, window: int, lock: int, name: str = "login", *,
                 url: str | None = None, client=None, clock=time.time):
        self.max_failures, self.window, self.lock, self.name, self.url = max_failures, window, lock, name, url
        self._client = client
        self.fallback = MemoryLoginLimiter(max_failures, window, lock, clock=clock, name=name)
        self.breaker = _Breaker(name)

    def _c(self):
        return self._client if self._client is not None else _sync_client(self.url or _redis_url())

    def _keys(self, key):
        return f"{KEY_PREFIX}{self.name}:fail:{key}", f"{KEY_PREFIX}{self.name}:lock:{key}"

    def retry_after(self, key: str) -> int:
        if self.breaker.open():
            return self.fallback.retry_after(key)
        try:
            ttl = self._c().ttl(self._keys(key)[1])
            self.breaker.ok()
            return int(ttl) if ttl and int(ttl) > 0 else 0
        except Exception as e:                                # noqa: BLE001 — fail open
            self.breaker.trip(e)
            return self.fallback.retry_after(key)

    def record_failure(self, key: str) -> int:
        if self.breaker.open():
            return self.fallback.record_failure(key)
        fk, lk = self._keys(key)
        try:
            p = self._c().pipeline(transaction=True)
            p.set(fk, 0, ex=self.window, nx=True)
            p.incr(fk)
            _, n = p.execute()
            if int(n) >= self.max_failures:
                self._c().set(lk, 1, ex=self.lock)
                self.breaker.ok()
                return self.lock
            self.breaker.ok()
            return 0
        except Exception as e:                                # noqa: BLE001
            self.breaker.trip(e)
            return self.fallback.record_failure(key)

    def record_success(self, key: str):
        self.fallback.record_success(key)
        if self.breaker.open():
            return
        try:
            self._c().delete(*self._keys(key))
        except Exception as e:                                # noqa: BLE001
            self.breaker.trip(e)

    def failures(self, key: str) -> int:
        if self.breaker.open():
            return self.fallback.failures(key)
        try:
            v = self._c().get(self._keys(key)[0])
            return int(v or 0)
        except Exception as e:                                # noqa: BLE001 — fail open
            self.breaker.trip(e)
            return self.fallback.failures(key)


# ── factories ──────────────────────────────────────────────────────────────────
def use_redis() -> bool:
    forced = (os.getenv("LEXORA_RATELIMIT_BACKEND") or "").strip().lower()
    if forced == "memory":
        return False
    if forced != "redis" and not os.getenv("REDIS_URL"):
        return False
    try:
        import redis  # noqa: F401
        return True
    except ImportError:
        log.warning("redis package missing — rate limits are per replica")
        return False


def make_limiter(max_hits: int, window_sec: int, name: str | None = None):
    """Redis-backed limiter when configured, else the in-process one. Same ``hit``/``ahit`` API."""
    if use_redis():
        return RedisRateLimiter(max_hits, window_sec, name=name)
    return RateLimiter(max_hits, window_sec, name=name)


def make_login_limiter(max_failures: int, window: int, lock: int, name: str = "login"):
    if use_redis():
        return RedisLoginLimiter(max_failures, window, lock, name=name)
    return MemoryLoginLimiter(max_failures, window, lock, name=name)


def client_ip(request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]
