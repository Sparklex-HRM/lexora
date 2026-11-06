"""Shared rate limiters: counted in Redis (one budget across replicas), safe when Redis is down."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest  # noqa: E402

from shared.utils import ratelimit as R  # noqa: E402


class FakeRedis:
    """The handful of Redis commands the limiters use; `now` is a fake clock for TTLs."""

    def __init__(self):
        self.data, self.exp, self.now, self.down = {}, {}, 1000.0, False

    def _alive(self, k):
        if self.down:
            raise ConnectionError("redis down")
        if k in self.exp and self.exp[k] <= self.now:
            self.data.pop(k, None)
            self.exp.pop(k, None)

    def set(self, k, v, ex=None, nx=False):
        self._alive(k)
        if nx and k in self.data:
            return None
        self.data[k] = int(v)
        if ex:
            self.exp[k] = self.now + ex
        return True

    def incr(self, k):
        self._alive(k)
        self.data[k] = int(self.data.get(k, 0)) + 1
        return self.data[k]

    def ttl(self, k):
        self._alive(k)
        if k not in self.data:
            return -2
        return int(self.exp[k] - self.now) if k in self.exp else -1

    def get(self, k):
        self._alive(k)
        return self.data.get(k)

    def delete(self, *ks):
        for k in ks:
            self._alive(k)
            self.data.pop(k, None)
            self.exp.pop(k, None)

    def pipeline(self, transaction=True):
        return _Pipe(self)


class _Pipe:
    def __init__(self, r):
        self.r, self.ops = r, []

    def __getattr__(self, name):
        def q(*a, **k):
            self.ops.append((name, a, k))
            return self
        return q

    def execute(self):
        return [getattr(self.r, n)(*a, **k) for n, a, k in self.ops]


class FakeAsyncRedis(FakeRedis):
    def pipeline(self, transaction=True):
        return _APipe(self)


class _APipe(_Pipe):
    async def execute(self):  # noqa: D401
        return super().execute()


def test_two_replicas_share_one_budget():
    r = FakeRedis()
    a = R.RedisRateLimiter(3, 60, name="t1", client=r)      # replica A
    b = R.RedisRateLimiter(3, 60, name="t1", client=r)      # replica B, same Redis
    assert [a.hit("ip|1"), b.hit("ip|1"), a.hit("ip|1")] == [0, 0, 0]
    assert b.hit("ip|1") == 60                                # 4th hit anywhere is refused
    assert a.hit("ip|2") == 0                                 # other keys unaffected
    r.now += 61                                               # window over
    assert b.hit("ip|1") == 0


def test_names_namespace_keys():
    r = FakeRedis()
    x = R.RedisRateLimiter(1, 60, name="x", client=r)
    y = R.RedisRateLimiter(1, 60, name="y", client=r)
    assert x.hit("k") == 0 and y.hit("k") == 0 and x.hit("k") > 0


def test_redis_down_falls_back_to_in_process_limit_and_logs(caplog):
    r = FakeRedis()
    r.down = True
    lim = R.RedisRateLimiter(2, 60, name="down", client=r)
    with caplog.at_level("WARNING", logger="lexora.ratelimit"):
        assert lim.hit("k") == 0 and lim.hit("k") == 0
        assert lim.hit("k") > 0                                # still limited on this replica
    assert "Redis unavailable" in caplog.text
    assert caplog.text.count("Redis unavailable") == 1        # logged once per outage
    assert lim.breaker.open()


def test_real_client_against_dead_redis_does_not_raise():
    lim = R.RedisRateLimiter(5, 60, name="dead", url="redis://127.0.0.1:1/0")
    assert lim.hit("k") == 0
    assert lim.breaker.open()


def test_async_hit_uses_redis():
    ar = FakeAsyncRedis()
    lim = R.RedisRateLimiter(1, 30, name="async", aclient=ar)
    assert asyncio.run(lim.ahit("k")) == 0
    assert asyncio.run(lim.ahit("k")) == 30


def test_login_limiter_locks_across_replicas_and_reads_fail_open():
    r = FakeRedis()
    a = R.RedisLoginLimiter(3, 900, 600, name="login_t", client=r)
    b = R.RedisLoginLimiter(3, 900, 600, name="login_t", client=r)
    assert a.record_failure("e|ip") == 0 and b.record_failure("e|ip") == 0
    assert a.failures("e|ip") == 2
    assert b.record_failure("e|ip") == 600
    assert a.retry_after("e|ip") == 600                        # the other replica sees the lock
    a.record_success("e|ip")
    assert b.retry_after("e|ip") == 0
    r.down = True
    c = R.RedisLoginLimiter(3, 900, 600, name="login_t2", client=r)
    assert c.retry_after("x") == 0                             # outage never locks anyone out


def test_factory_picks_backend(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("LEXORA_RATELIMIT_BACKEND", raising=False)
    assert isinstance(R.make_limiter(5, 60, name="f"), R.RateLimiter)
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    assert isinstance(R.make_limiter(5, 60, name="f"), R.RedisRateLimiter)
    assert isinstance(R.make_login_limiter(5, 60, 60), R.RedisLoginLimiter)
    monkeypatch.setenv("LEXORA_RATELIMIT_BACKEND", "memory")
    assert isinstance(R.make_limiter(5, 60, name="f"), R.RateLimiter)


@pytest.mark.parametrize("cls", [R.RateLimiter])
def test_memory_limiter_unchanged(cls):
    t = [0.0]
    lim = cls(2, 10, clock=lambda: t[0])
    assert lim.hit("a") == 0 and lim.hit("a") == 0 and lim.hit("a") == 10
    t[0] = 11
    assert lim.hit("a") == 0
