"""Gateway flood guard: budget per client address, shared via Redis, safe when Redis is down."""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for p in (os.path.dirname(_HERE), _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from ratelimit_mw import GatewayRateLimit  # noqa: E402
from shared.utils import ratelimit as R  # noqa: E402
from shared.utils.test_ratelimit import FakeAsyncRedis  # noqa: E402


def _app(limiter, rpm=3):
    app = FastAPI()

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/employees")
    def emps():
        return []
    app.add_middleware(GatewayRateLimit, rpm=rpm, limiter=limiter)
    return TestClient(app)


def test_budget_shared_by_two_gateway_replicas():
    r = FakeAsyncRedis()
    a = _app(R.RedisRateLimiter(3, 60, name="gw", aclient=r))
    b = _app(R.RedisRateLimiter(3, 60, name="gw", aclient=r))
    assert [a.get("/employees").status_code, b.get("/employees").status_code, a.get("/employees").status_code] == [200] * 3
    resp = b.get("/employees")
    assert resp.status_code == 429 and resp.headers["retry-after"] == "60"
    assert a.get("/health").status_code == 200                # health is never counted


def test_redis_down_still_limits_per_replica():
    r = FakeAsyncRedis()
    r.down = True
    c = _app(R.RedisRateLimiter(2, 60, name="gw_down", aclient=r))
    assert [c.get("/employees").status_code for _ in range(3)] == [200, 200, 429]


def test_rpm_zero_switches_it_off():
    c = _app(None, rpm=0)
    assert all(c.get("/employees").status_code == 200 for _ in range(5))
