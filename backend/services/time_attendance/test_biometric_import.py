"""Biometric parser tests. Run from backend/services/time_attendance:
    python -m pytest test_biometric_import.py -q
"""
import os
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
for p in (os.path.dirname(os.path.dirname(_HERE)), _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from biometric_import import (detect_columns, norm_code, parse_date, parse_direction, parse_table, parse_time,  # noqa: E402
                              webhook_events)


def test_norm_code_variants():
    assert norm_code("0001") == norm_code("1") == norm_code("1.0") == "1"
    assert norm_code("EMP007") == norm_code("emp7") == "EMP7"


def test_date_and_time_formats():
    assert parse_date("03/10/2026").isoformat() == "2026-10-03"
    assert parse_date("2026-10-03").isoformat() == "2026-10-03"
    assert parse_date("03-Oct-26").isoformat() == "2026-10-03"
    assert parse_time("09:05").strftime("%H:%M") == "09:05"
    assert parse_time("6:15 PM").strftime("%H:%M") == "18:15"
    assert parse_time("--") is None


def test_direction_words():
    assert parse_direction("In") == "in" and parse_direction("C/Out") == "out"
    assert parse_direction("0") == "in" and parse_direction("1") == "out"
    assert parse_direction("Check-In") == "in" and parse_direction("") is None


def test_essl_summary_csv():
    csv = (b"Emp Code,Name,Date,In Time,Out Time\n"
           b"0001,Asha,03/10/2026,09:02,18:10\n"
           b"0002,Ravi,03/10/2026,09:30,--\n"
           b"0003,Night,03/10/2026,22:00,06:00\n")
    events, errors, cols = parse_table(csv, "essl.csv")
    assert cols["emp"] == "Emp Code" and cols["in"] == "In Time"
    assert not errors
    by = {(e["code"], e["direction"]): e["at"] for e in events}
    assert by[("1", "in")] == datetime(2026, 10, 3, 9, 2) and by[("1", "out")] == datetime(2026, 10, 3, 18, 10)
    assert ("2", "out") not in by                                         # missing out punch skipped
    assert by[("3", "out")] == datetime(2026, 10, 4, 6, 0)                # night shift rolls to next day


def test_zkteco_punch_log_alternates_when_no_direction():
    csv = (b"UserID,Time\n"
           b"7,2026-10-03 09:00:00\n"
           b"7,2026-10-03 13:00:00\n"
           b"7,2026-10-03 13:45:00\n"
           b"7,2026-10-03 18:00:00\n"
           b"7,2026-10-03 09:00:00\n")   # exact duplicate row
    events, errors, cols = parse_table(csv, "zk.csv")
    assert not errors and cols["dt"] == "Time"
    assert [e["direction"] for e in events] == ["in", "out", "in", "out"]
    assert len(events) == 4                                              # duplicate removed


def test_matrix_log_with_status_column_and_header_offset():
    csv = (b"Attendance report\n\n"
           b"Employee Code;Employee Name;Punch Date;Punch Time;In/Out\n"
           b"M101;Priya;03-10-2026;09:12:00;In\n"
           b"M101;Priya;03-10-2026;17:58:00;Out\n"
           b"M101;Priya;bad-date;17:58:00;Out\n")
    events, errors, cols = parse_table(csv, "matrix.csv")
    assert cols["date"] == "Punch Date" and cols["dt"] == "Punch Time"
    assert len(events) == 2 and events[0]["direction"] == "in" and events[1]["direction"] == "out"
    assert len(errors) == 1 and "Row 6" in errors[0]      # file line number


def test_detect_columns_tolerates_case_and_spaces():
    c = detect_columns(["EMPCODE", "Att Date", "INTIME", "OUT TIME", "Device ID"])
    assert c == {"emp": "EMPCODE", "date": "Att Date", "in": "INTIME", "out": "OUT TIME", "device": "Device ID"}


def test_webhook_payload_shapes():
    ev = webhook_events({"punches": [{"emp_code": "12", "at": "2026-10-03T09:00:00", "direction": "in"},
                                     {"emp_code": "12", "at": "2026-10-03 18:00:00"}]})
    assert [e["direction"] for e in ev] == ["in", "out"]
    ev2 = webhook_events([{"UserID": "0012", "PunchTime": "03/10/2026 09:00:00", "Status": "0"}])
    assert ev2[0]["code"] == "12" and ev2[0]["direction"] == "in"
    assert webhook_events({"nope": 1}) == []


# ── Integration 2026-10-23: webhook secret stored hashed, shown once ─────────
def test_device_secret_hashed_and_never_listed():
    import uuid as _uuid
    from biometric_import import BiometricDevice, _dev_out, _new_secret, hash_secret, secret_ok
    d = BiometricDevice(id=_uuid.uuid4(), org_id=_uuid.uuid4(), device_id="CQZ1", name="Gate", vendor="zkteco",
                        timezone="America/Toronto", active=True)
    plain = _new_secret(d)
    assert d.secret.startswith("sha256:") and plain not in d.secret
    assert secret_ok(plain, d.secret) and not secret_ok(plain + "x", d.secret) and not secret_ok("", d.secret)
    listed = _dev_out(d)
    assert listed["secret"] is None and listed["secret_set"] is True and plain not in str(listed) and d.secret not in str(listed)
    assert _dev_out(d, plain)["secret"] == plain                       # create / rotate response only
    assert secret_ok("legacy-plain", "legacy-plain")                    # pre-hash rows keep working until hashed
    assert hash_secret("legacy-plain") != "legacy-plain"
