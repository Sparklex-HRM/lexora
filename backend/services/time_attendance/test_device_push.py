"""G3 device push tests (no DB, no network). Run from backend/services/time_attendance:
    python -m pytest test_device_push.py -q
"""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

_HERE = os.path.dirname(os.path.abspath(__file__))
for p in (os.path.dirname(os.path.dirname(_HERE)), _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("LEXORA_RATELIMIT_BACKEND", "memory")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import device_push as dp  # noqa: E402
from database import get_db  # noqa: E402

UTC = timezone.utc


# ── pure helpers ──────────────────────────────────────────────────────────────
def test_serial_and_token_shapes():
    assert dp.valid_serial("CQZ7224460123") and dp.valid_serial("ess-01.a")
    assert not dp.valid_serial("") and not dp.valid_serial("a") and not dp.valid_serial("x' OR 1=1")
    assert not dp.valid_serial("A" * 41)
    t = dp.new_token()
    assert t.startswith("dp_") and dp.TOKEN_RE.match(t)
    h = dp.hash_token(t)
    assert dp.token_ok(t, h) and not dp.token_ok(t + "x", h) and not dp.token_ok(t, None)
    assert not dp.token_ok("plain", dp.hash_token("plain"))      # wrong shape never matches


def test_ip_allow_list():
    cidrs = dp.clean_cidrs(["203.0.113.7", "198.51.100.0/24", " "])
    assert cidrs == ["203.0.113.7/32", "198.51.100.0/24"]
    assert dp.ip_allowed("203.0.113.7", cidrs) and dp.ip_allowed("198.51.100.99", cidrs)
    assert not dp.ip_allowed("203.0.113.8", cidrs) and not dp.ip_allowed("garbage", cidrs)
    assert not dp.ip_allowed("203.0.113.7", [])                   # empty list allows nobody
    with pytest.raises(ValueError):
        dp.clean_cidrs(["0.0.0.0/0"])
    with pytest.raises(ValueError):
        dp.clean_cidrs(["not-an-ip"])


def test_parse_attlog_zkteco_lines():
    body = ("1001\t2026-10-23 09:01:05\t0\t1\t0\t0\n"
            "0042\t2026-10-23 18:30:00\t1\t15\n"
            "77\t2026-10-23 13:00:00\t255\t1\n"
            "\n"
            "bad line\n"
            "'; DROP\t2026-10-23 10:00:00\t0\t1\n")
    ev, errs = dp.parse_attlog(body)
    assert [(e["code"], e["direction"]) for e in ev] == [("1001", "in"), ("42", "out"), ("77", None)]
    assert ev[0]["at"] == datetime(2026, 10, 23, 9, 1, 5)
    assert len(errs) == 2


def test_adms_options_upload_attendance_only():
    txt = dp.adms_options("CQZ1", "123")
    assert txt.startswith("GET OPTION FROM: CQZ1\n")
    assert "TransFlag=TransData AttLog" in txt and "ATTLOGStamp=123" in txt and "Realtime=1" in txt
    assert "OpLog" not in txt and "FP" not in txt                   # no user / template upload requested


def test_parse_adms_info_keeps_firmware_only():
    assert dp.parse_adms_info("Ver 8.0.4.2-20210429,25,40,1200,192.168.1.201,10,7") == \
        {"firmware": "Ver 8.0.4.2-20210429", "users": 25, "attlogs": 1200}
    assert dp.parse_adms_info(None) == {}


def test_cosec_event_only_user_allowed_counts():
    base = {"evt_id": "101", "field-1": "12", "date-dd": "23", "date-mm": "10", "date-yyyy": "2026",
            "time-hh": "09", "time-mm": "02", "time-ss": "03", "seq-no": "88"}
    e = dp.parse_cosec_event(base)
    assert e["code"] == "12" and e["at"] == datetime(2026, 10, 23, 9, 2, 3) and e["direction"] is None and e["seq"] == "88"
    assert dp.parse_cosec_event({**base, "field-5": "KAV007"})["code"] == "KAV7"
    assert dp.parse_cosec_event({**base, "evt_id": "151"}) is None         # user denied
    assert dp.parse_cosec_event({**base, "field-1": "0"}) is None          # exit switch / unknown user
    assert dp.parse_cosec_event({**base, "date-mm": "13"}) is None


def test_direction_modes():
    ev = [{"code": "1", "at": datetime(2026, 1, 1, 9), "direction": "out"}, {"code": "1", "at": datetime(2026, 1, 1, 10), "direction": None}]
    assert [(e["direction"], e["auto"]) for e in dp.apply_direction_mode(ev, "device")] == [("out", False), (None, True)]
    assert [e["auto"] for e in dp.apply_direction_mode(ev, "alternate")] == [True, True]
    assert {e["direction"] for e in dp.apply_direction_mode(ev, "in")} == {"in"}


def test_http_events_do_not_guess_direction():
    ev = dp.http_events([{"emp_code": "0012", "at": "2026-10-23 09:00:00"},
                         {"UserID": "13", "PunchTime": "2026-10-23 09:05:00", "Status": "out"}, "junk"])
    assert [(e["code"], e["direction"]) for e in ev] == [("12", None), ("13", "out")]


def _t(h, m, s=0):
    return datetime(2026, 10, 23, h, m, s, tzinfo=UTC)


def test_plan_batch_duplicates_bounce_and_out_of_order():
    existing = [(_t(3, 30), "in"), (_t(8, 0), "out")]
    new = [{"at": _t(3, 30), "direction": "in"},              # exact resend → duplicate
           {"at": _t(3, 30, 40), "direction": "in"},          # same minute + direction → duplicate
           {"at": _t(8, 0, 30), "direction": None},           # 30 s after a punch → bounce
           {"at": _t(6, 0), "direction": None},               # earlier than the newest stored → out of order (kept)
           {"at": _t(9, 0), "direction": None},
           {"at": _t(9, 0, 20), "direction": None}]           # double tap inside the batch → bounce
    r = dp.plan_batch(existing, new, 60)
    assert r["duplicates"] == 2 and r["bounced"] == 2 and r["out_of_order"] == 1
    assert [e["at"] for e in r["accept"]] == [_t(6, 0), _t(9, 0)]
    assert dp.plan_batch([], [{"at": _t(9, 0), "direction": None}, {"at": _t(9, 0, 20), "direction": None}], 0)["bounced"] == 0


def test_derive_auto_alternates_and_respects_fixed():
    rows = [{"direction": "in", "auto": True}, {"direction": "in", "auto": True}, {"direction": "in", "auto": False},
            {"direction": "in", "auto": True}]
    assert dp.derive_auto(rows) == ["in", "out", "in", "out"]


def test_drift_estimate():
    now = _t(10, 0)
    assert dp.estimate_drift([_t(10, 3)], now) == 180                # device 3 min fast
    assert dp.estimate_drift([_t(9, 59, 50)], now) == -10            # upload delay / slow clock (estimate)
    assert dp.estimate_drift([_t(8, 0)], now) is None                # backlog, not drift
    assert dp.estimate_drift([], now) is None


def test_device_status():
    now = _t(12, 0)
    assert dp.device_status(False, now, 30, now) == "disabled"
    assert dp.device_status(True, None, 30, now) == "never"
    assert dp.device_status(True, now - timedelta(minutes=10), 30, now) == "online"
    assert dp.device_status(True, now - timedelta(minutes=31), 30, now) == "offline"


# ── authenticate (fake DB) ────────────────────────────────────────────────────
class _Res:
    def __init__(self, v):
        self.v = v

    def scalar_one_or_none(self):
        return self.v


class FakeDB:
    def __init__(self, *values):
        self.values = list(values)

    async def execute(self, *_a, **_k):
        return _Res(self.values.pop(0) if self.values else None)

    async def commit(self):
        pass

    async def rollback(self):
        pass


ORG = uuid.uuid4()


def _push(**kw):
    tok = kw.pop("token", None)
    base = dict(id=uuid.uuid4(), org_id=ORG, device_row_id=uuid.uuid4(), serial_key="CQZ1", protocol="adms", enabled=True,
                token_hash=dp.hash_token(tok) if tok else None, allowed_ips=[], reject_day=None, rejects_today=0,
                last_reject=None, last_reject_at=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _dev(p, **kw):
    return SimpleNamespace(**{"id": p.device_row_id, "org_id": p.org_id, "active": True, "device_id": "CQZ1",
                              "timezone": "America/Toronto", "name": "Gate 1", **kw})


def _auth(db, **kw):
    return asyncio.run(dp.authenticate(db, **kw))


@pytest.fixture(autouse=True)
def _entitled(monkeypatch):
    """Default: the device's org holds the time module (the entitlement rule has its own tests)."""
    async def ok(_db, _org, _module):
        return None
    monkeypatch.setattr(dp, "org_module_refusal", ok)


def test_auth_refused_when_org_lacks_time_module(monkeypatch):
    seen = {}

    async def no(_db, org, module):
        seen.update(org=org, module=module)
        return "module 'time' not subscribed"
    monkeypatch.setattr(dp, "org_module_refusal", no)
    p = _push(allowed_ips=["203.0.113.0/24"])
    with pytest.raises(dp.Refused) as e:
        _auth(FakeDB(p, _dev(p)), ip="203.0.113.9", protocol="adms", serial="CQZ1", token=None)
    assert "not subscribed" in e.value.why and e.value.push is p
    assert seen == {"org": ORG, "module": "time"}                     # org from the REGISTRATION, module = time


def test_auth_plain_iclock_needs_allow_list_and_matching_ip():
    p = _push(allowed_ips=["203.0.113.0/24"])
    got, dev = _auth(FakeDB(p, _dev(p)), ip="203.0.113.9", protocol="adms", serial="cqz1", token=None)
    assert got is p and dev.org_id == ORG                            # org comes from OUR registration
    with pytest.raises(dp.Refused):
        _auth(FakeDB(p, _dev(p)), ip="198.51.100.1", protocol="adms", serial="CQZ1", token=None)
    with pytest.raises(dp.Refused) as e:
        _auth(FakeDB(_push(allowed_ips=[]), None), ip="203.0.113.9", protocol="adms", serial="CQZ1", token=None)
    assert "allow-list" in e.value.why
    with pytest.raises(dp.Refused):                                    # unknown serial
        _auth(FakeDB(None), ip="203.0.113.9", protocol="adms", serial="NOPE1", token=None)


def test_auth_token_path_checks_serial_protocol_enabled_and_device():
    tok = dp.new_token()
    p = _push(token=tok, protocol="cosec")
    got, _ = _auth(FakeDB(p, _dev(p)), ip="1.2.3.4", protocol="cosec", serial="CQZ1", token=tok)
    assert got is p
    for kw, db in [
        (dict(serial="OTHER1"), FakeDB(p)),                            # token of another device
        (dict(serial="CQZ1", protocol="adms"), FakeDB(p)),             # wrong protocol
        (dict(serial="CQZ1"), FakeDB(_push(token=tok, protocol="cosec", enabled=False))),
        (dict(serial="CQZ1"), FakeDB(p, _dev(p, active=False))),       # device switched off
        (dict(serial="CQZ1"), FakeDB(p, _dev(p, org_id=uuid.uuid4()))),  # registration / device org mismatch
    ]:
        args = {"ip": "1.2.3.4", "protocol": "cosec", "token": tok, **kw}
        with pytest.raises(dp.Refused):
            _auth(db, **args)
    with pytest.raises(dp.Refused):
        _auth(FakeDB(None), ip="1.2.3.4", protocol="cosec", serial="CQZ1", token=dp.new_token())
    with pytest.raises(dp.Refused):
        _auth(FakeDB(), ip="1.2.3.4", protocol="cosec", serial="CQZ1", token="dp_short")


def test_auth_ip_list_also_applies_on_token_path():
    tok = dp.new_token()
    p = _push(token=tok, protocol="http", allowed_ips=["10.0.0.0/8"])
    with pytest.raises(dp.Refused):
        _auth(FakeDB(p, _dev(p)), ip="203.0.113.1", protocol="http", serial="CQZ1", token=tok)


# ── routes (authenticate / ingest patched) ───────────────────────────────────
@pytest.fixture()
def client(monkeypatch):
    app = FastAPI()
    app.include_router(dp.iclock)
    app.include_router(dp.push)

    async def _db():
        yield FakeDB()
    app.dependency_overrides[get_db] = _db
    calls = {"ingest": []}
    p = _push(allowed_ips=["127.0.0.1/32"], device_info={}, last_batch={}, offline_alerted_at=None, last_seen_at=None,
              last_ip=None, firmware=None, updated_at=None)
    dev = _dev(p)

    async def fake_auth(db, *, ip, protocol, serial, token):
        if serial and serial.upper() == "BAD1":
            raise dp.Refused("unknown serial")
        p.protocol = protocol
        return p, dev

    async def fake_ingest(db, p_, dev_, events, **kw):
        calls["ingest"].append(events)
        return {"received": len(events), "written": len(events), "duplicates": 0, "bounced": 0, "out_of_order": 0,
                "unmatched": 0, "future": 0, "too_old": 0, "unmatched_codes": []}

    monkeypatch.setattr(dp, "authenticate", fake_auth)
    monkeypatch.setattr(dp, "ingest", fake_ingest)
    monkeypatch.setattr(dp, "refusals", dp.make_login_limiter(30, 600, 600, name="t_ref_" + uuid.uuid4().hex))
    monkeypatch.setattr(dp, "ip_limiter", dp.make_limiter(1200, 600, name="t_ip_" + uuid.uuid4().hex))
    monkeypatch.setattr(dp, "serial_limiter", dp.make_limiter(300, 600, name="t_sn_" + uuid.uuid4().hex))
    return TestClient(app), calls, p


def test_iclock_handshake_attlog_and_discarded_tables(client):
    c, calls, p = client
    r = c.get("/iclock/cdata", params={"SN": "CQZ1", "options": "all", "pushver": "2.4.1"})
    assert r.status_code == 200 and "TransFlag=TransData AttLog" in r.text
    r = c.post("/iclock/cdata", params={"SN": "CQZ1", "table": "ATTLOG", "Stamp": "9"},
               content=b"1001\t2026-10-23 09:01:05\t0\t1\n1002\t2026-10-23 09:02:00\t0\t1\n")
    assert r.text == "OK: 2" and len(calls["ingest"]) == 1 and p.device_info["attlog_stamp"] == "9"
    r = c.post("/iclock/cdata", params={"SN": "CQZ1", "table": "OPERLOG"}, content=b"USER PIN=1\tName=Asha\tPri=0\n")
    assert r.text == "OK" and len(calls["ingest"]) == 1               # user / template data never ingested
    assert c.get("/iclock/getrequest", params={"SN": "CQZ1", "INFO": "Ver 6.6,3,0,10,1.2.3.4"}).text == "OK"
    assert p.firmware == "Ver 6.6"
    assert c.post("/iclock/devicecmd", params={"SN": "CQZ1"}, content=b"ID=1&Return=0").text == "OK"


def test_unknown_device_forbidden_then_locked_out(client):
    c, calls, _ = client
    for _ in range(30):
        r = c.get("/iclock/getrequest", params={"SN": "BAD1"})
        assert r.status_code == 403 and r.text == "Forbidden"          # same answer whatever failed
    assert c.get("/iclock/getrequest", params={"SN": "CQZ1"}).status_code == 429   # address locked out
    assert not calls["ingest"]


def test_attlog_body_cap(client, monkeypatch):
    c, calls, _ = client
    monkeypatch.setattr(dp, "ATTLOG_MAX_BYTES", 10)
    r = c.post("/iclock/cdata", params={"SN": "CQZ1", "table": "ATTLOG"}, content=b"1\t2026-10-23 09:00:00\t0\n" * 5)
    assert r.status_code == 413 and not calls["ingest"]


def test_cosec_login_poll_setevent(client):
    c, calls, _ = client
    tok = dp.new_token()
    q = {"device-type": "7", "serial-no": "CQZ1"}
    r = c.get(f"/time/device-push/cosec/{tok}/login", params=q)
    assert r.text == "poll-interval=30 poll-duration=5 poll-count=3 status=1 format=0"
    assert c.get(f"/time/device-push/cosec/{tok}/poll", params=q).text == "cmd-avlbl=0 cnfg-avlbl=0 status=1"
    ev = {**q, "seq-no": "5", "evt_id": "101", "field-1": "12", "date-dd": "23", "date-mm": "10", "date-yyyy": "2026",
          "time-hh": "9", "time-mm": "0", "time-ss": "0"}
    assert c.get(f"/time/device-push/cosec/{tok}/setevent", params=ev).text == "status=1"
    assert c.get(f"/time/device-push/cosec/{tok}/setevent", params={**ev, "evt_id": "151"}).text == "status=1"
    assert len(calls["ingest"]) == 1
    assert c.get(f"/time/device-push/cosec/{tok}/login", params={"device-type": "9", "serial-no": "CQZ1"}).status_code == 403


def test_http_push_answers_counts_only(client):
    c, calls, _ = client
    tok = dp.new_token()
    r = c.post(f"/time/device-push/http/{tok}", json={"serial": "CQZ1", "punches": [{"emp_code": "12", "at": "2026-10-23 09:00:00"}]})
    assert r.status_code == 200 and set(r.json()) == {"ok", "received", "written", "duplicates", "unmatched", "rejected"}
    assert c.post(f"/time/device-push/http/{tok}", json={"serial": "BAD1"}).status_code == 403
    assert c.post(f"/time/device-push/http/{tok}", content=b"[1,2]", headers={"content-type": "application/json"}).status_code == 400
