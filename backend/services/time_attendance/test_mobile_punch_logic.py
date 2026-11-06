"""Mobile punch rules (Stream G4) — pure, no database.
Run:  cd backend/services/time_attendance && python -m pytest -q test_mobile_punch_logic.py
"""
from datetime import datetime, timedelta, timezone

import pytest

import mobile_punch_logic as ML

HAMILTON = {"id": "f1", "name": "Hamilton plant", "lat": 43.2557, "lng": -79.8711, "radius_m": 150, "active": True}
TORONTO = {"id": "f2", "name": "Toronto office", "lat": 43.6532, "lng": -79.3832, "radius_m": 100, "active": True}


def test_haversine_known_distance():
    # Hamilton → Toronto is about 58 km by great circle
    assert 55_000 < ML.haversine_m(43.2557, -79.8711, 43.6532, -79.3832) < 62_000
    assert ML.haversine_m(43.2557, -79.8711, 43.2557, -79.8711) == 0


def test_pick_fence_prefers_employee_work_location_then_nearest():
    near_toronto = (43.6537, -79.3829)
    f, d = ML.pick_fence(*near_toronto, [HAMILTON, TORONTO])
    assert f["name"] == "Toronto office" and d < 100
    f, d = ML.pick_fence(*near_toronto, [HAMILTON, TORONTO], preferred_name="hamilton plant")
    assert f["name"] == "Hamilton plant" and d > 50_000
    assert ML.pick_fence(None, None, [HAMILTON]) == (None, None)
    assert ML.pick_fence(43.25, -79.87, [{**HAMILTON, "active": False}]) == (None, None)


def test_inside_fence_is_allowed_without_flags():
    v = ML.evaluate_punch({}, lat=43.2558, lng=-79.8710, accuracy_m=20, mock_location=False, fence=HAMILTON,
                          distance_m=15, device_state="active", has_selfie=True)
    assert v["allowed"] and v["flags"] == [] and v["inside_fence"] is True


def test_outside_fence_flag_vs_refuse():
    kw = dict(lat=43.27, lng=-79.85, accuracy_m=20, mock_location=False, fence=HAMILTON, distance_m=900,
              device_state="active", has_selfie=True)
    v = ML.evaluate_punch({"outside_fence": "flag"}, **kw)
    assert v["allowed"] and "outside_fence" in v["flags"] and v["inside_fence"] is False
    v = ML.evaluate_punch({"outside_fence": "refuse"}, **kw)
    assert not v["allowed"] and "900 m from Hamilton plant" in v["refusal"]


def test_mock_location_refused_by_default_and_flag_only_when_allowed():
    kw = dict(lat=43.2558, lng=-79.8710, accuracy_m=10, mock_location=True, fence=HAMILTON, distance_m=5,
              device_state="active", has_selfie=True)
    v = ML.evaluate_punch({}, **kw)
    assert not v["allowed"] and "mock location" in v["refusal"]
    v = ML.evaluate_punch({"refuse_mock": False}, **kw)
    assert v["allowed"] and "mock_location" in v["flags"]


def test_poor_accuracy_flag_or_refuse():
    kw = dict(lat=43.2558, lng=-79.8710, accuracy_m=650, mock_location=False, fence=HAMILTON, distance_m=5,
              device_state="active", has_selfie=True)
    v = ML.evaluate_punch({"max_accuracy_m": 200}, **kw)
    assert v["allowed"] and "poor_accuracy" in v["flags"]
    v = ML.evaluate_punch({"max_accuracy_m": 200, "refuse_poor_accuracy": True}, **kw)
    assert not v["allowed"] and "650 m" in v["refusal"]
    v = ML.evaluate_punch({"max_accuracy_m": 0}, **kw)
    assert "poor_accuracy" not in v["flags"]


def test_device_binding_blocks_a_new_phone_until_hr_approves():
    kw = dict(lat=43.2558, lng=-79.8710, accuracy_m=10, mock_location=False, fence=HAMILTON, distance_m=5, has_selfie=True)
    assert not ML.evaluate_punch({}, device_state="pending", **kw)["allowed"]
    assert not ML.evaluate_punch({}, device_state="new", **kw)["allowed"]
    assert ML.evaluate_punch({}, device_state="active", **kw)["allowed"]
    assert ML.evaluate_punch({"device_binding": False}, device_state="new", **kw)["allowed"]


def test_selfie_required_mode():
    kw = dict(lat=43.2558, lng=-79.8710, accuracy_m=10, mock_location=False, fence=HAMILTON, distance_m=5, device_state="active")
    assert ML.evaluate_punch({"selfie": "required"}, has_selfie=False, **kw)["refusal"].startswith("A selfie is required")
    assert ML.evaluate_punch({"selfie": "optional"}, has_selfie=False, **kw)["allowed"]
    assert ML.evaluate_punch({"selfie": "required"}, has_selfie=True, **kw)["allowed"]


def test_no_location_is_only_a_flag():
    v = ML.evaluate_punch({"outside_fence": "refuse"}, lat=None, lng=None, accuracy_m=None, mock_location=False,
                          fence=None, distance_m=None, device_state=None, has_selfie=False)
    assert v["allowed"] and v["flags"] == ["no_location"] and v["inside_fence"] is None


def test_late_sync_uses_device_time_and_threshold():
    now = datetime(2026, 10, 13, 10, 0, tzinfo=timezone.utc)
    assert ML.late_sync(now - timedelta(minutes=1), now, 2) == (False, 1)
    assert ML.late_sync(now - timedelta(minutes=45), now, 2) == (True, 45)
    assert ML.late_sync(now + timedelta(minutes=5), now, 2) == (False, 0)      # phone clock ahead
    assert ML.late_sync(None, now, 2) == (False, 0)


def test_parse_iso_accepts_z_and_naive():
    d = ML.parse_iso("2026-10-13T09:30:00Z")
    assert d.tzinfo is not None and d.hour == 9
    assert ML.parse_iso("2026-10-13T09:30:00").tzinfo is not None
    assert ML.parse_iso("not a date") is None and ML.parse_iso(None) is None


def test_fingerprint_is_stable_short_and_never_raw():
    a = ML.device_fingerprint("Mozilla/5.0 (Linux; Android 14) 390x844")
    assert a == ML.device_fingerprint("Mozilla/5.0 (Linux; Android 14) 390x844") and len(a) == 24
    assert "Mozilla" not in a


def test_clean_settings_validates():
    s = ML.clean_settings({"outside_fence": "refuse", "selfie": "required", "max_accuracy_m": "100"})
    assert s["outside_fence"] == "refuse" and s["max_accuracy_m"] == 100 and s["device_binding"] is True
    with pytest.raises(ValueError):
        ML.clean_settings({"outside_fence": "block"})
    with pytest.raises(ValueError):
        ML.clean_settings({"selfie_max_kb": 99999})
    with pytest.raises(ValueError):
        ML.clean_settings({"late_sync_minutes": -1})


def test_clamp_coord():
    assert ML.clamp_coord("43.25571234", -90, 90) == 43.255712
    assert ML.clamp_coord(91, -90, 90) is None and ML.clamp_coord("x", -90, 90) is None


def test_flag_labels_are_plain_words():
    assert ML.flag_labels(["outside_fence", "synced_late", "zzz"]) == ["Outside site", "Synced late", "zzz"]
