"""2026-10-24 forms wave: the biometric device form refuses a bad vendor / time zone.

A mistyped zone used to be saved and then silently treated as UTC by to_utc(), shifting every
punch from that device by 5h30. Run from backend/services/time_attendance:
    python -m pytest test_biometric_device_body.py -q
"""
import os
import sys

import pytest
from pydantic import ValidationError

_HERE = os.path.dirname(os.path.abspath(__file__))
for p in (os.path.dirname(os.path.dirname(_HERE)), _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from biometric_import import DeviceBody, valid_tz  # noqa: E402


def test_defaults_and_trim():
    b = DeviceBody(device_id="  CKJG201960001 ", name=" Main gate ")
    assert b.device_id == "CKJG201960001" and b.name == "Main gate"
    assert b.vendor == "essl" and b.timezone == "America/Toronto"


def test_valid_tz():
    assert valid_tz("America/Toronto") and valid_tz("UTC")
    assert not valid_tz("Canada/Nowhere") and not valid_tz("") and not valid_tz(None)


@pytest.mark.parametrize("kw", [
    {"timezone": "EST-5:00"},
    {"vendor": "hikvision"},
    {"device_id": "   "},
    {"name": ""},
    {"device_id": "X" * 61},
])
def test_bad_values_refused(kw):
    base = {"device_id": "SN1", "name": "Gate"}
    with pytest.raises(ValidationError):
        DeviceBody(**{**base, **kw})
