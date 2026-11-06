"""org_time: the business date follows the org's Canadian wall clock (default Toronto), not UTC."""
import asyncio
import os
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest  # noqa: E402

from shared.utils import org_time as T  # noqa: E402

EDT = timedelta(hours=-4)
EST = timedelta(hours=-5)


def _utc(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


@pytest.fixture
def frozen(monkeypatch):
    def _set(t):
        monkeypatch.setattr(T, "_utcnow", lambda: t)
    return _set


def test_2130_toronto_is_still_today_while_utc_is_tomorrow(frozen):
    # 21:30 EDT on 14 Oct = 01:30 UTC on 15 Oct. UTC says the 15th; Toronto says the 14th.
    frozen(_utc(2026, 10, 15, 1, 30))
    assert T._utcnow().date() == date(2026, 10, 15)        # what UTC code would use
    assert T.local_today() == date(2026, 10, 14)
    assert T.local_now().utcoffset() == EDT


def test_midnight_boundary_follows_daylight_saving(frozen):
    frozen(_utc(2026, 10, 15, 3, 59))           # 23:59 EDT on the 14th
    assert T.local_today() == date(2026, 10, 14)
    frozen(_utc(2026, 10, 15, 4, 0))            # 00:00 EDT on the 15th
    assert T.local_today() == date(2026, 10, 15)
    frozen(_utc(2026, 12, 15, 4, 59))           # 23:59 EST on 14 Dec (standard time: UTC-5)
    assert T.local_today() == date(2026, 12, 14) and T.local_now().utcoffset() == EST


def test_month_rollover_at_toronto_midnight(frozen):
    frozen(_utc(2026, 11, 1, 3, 30))            # 23:30 EDT on 31 Oct
    assert T.month_bounds() == (date(2026, 10, 1), date(2026, 10, 31))
    frozen(_utc(2026, 11, 1, 4, 30))            # 00:30 EDT on 1 Nov
    assert T.month_bounds() == (date(2026, 11, 1), date(2026, 11, 30))
    assert T.month_bounds(date(2028, 2, 10)) == (date(2028, 2, 1), date(2028, 2, 29))


def test_to_local_date_and_day_bounds():
    assert T.to_local_date(_utc(2026, 10, 15, 2, 0)) == date(2026, 10, 14)
    assert T.to_local_date(None) is None
    s, e = T.day_bounds_utc(date(2026, 10, 15))
    assert s == _utc(2026, 10, 15, 4, 0) and e == _utc(2026, 10, 16, 4, 0)


def test_province_zones():
    assert T.province_tz("BC") == "America/Vancouver" and T.province_tz("nl") == "America/St_Johns"
    assert T.province_tz("SK") == "America/Regina" and T.province_tz("QC") == "America/Toronto"
    assert T.province_tz(None) == "America/Toronto" and T.province_tz("XX") == "America/Toronto"
    assert len(T.PROVINCE_TZ) == 13
    at = _utc(2026, 10, 15, 5, 0)                           # 01:00 Toronto, 22:00 Vancouver the day before
    assert T.today_for_org(T.province_tz("ON"), at=at) == date(2026, 10, 15)
    assert T.today_for_org(T.province_tz("BC"), at=at) == date(2026, 10, 14)
    nl = T.local_now(T.province_tz("NL"), at=at)
    assert nl.utcoffset() == timedelta(hours=-2, minutes=-30)   # Newfoundland daylight time


def test_bad_zone_falls_back_to_eastern_not_utc(frozen):
    frozen(_utc(2026, 10, 15, 1, 30))
    assert T.local_today("Not/AZone") == date(2026, 10, 14)
    assert not T.valid_timezone("Not/AZone")
    assert T.valid_timezone("America/Toronto")


def test_org_today_reads_setting_and_survives_failure(frozen, monkeypatch):
    frozen(_utc(2026, 10, 15, 1, 30))
    import shared.utils.org_settings as S

    async def setting_utc(org, key, default=None):
        return "UTC"
    monkeypatch.setattr(S, "get_setting", setting_utc)
    assert asyncio.run(T.org_today("o1")) == date(2026, 10, 15)

    async def setting_boom(org, key, default=None):
        raise RuntimeError("db down")
    monkeypatch.setattr(S, "get_setting", setting_boom)
    assert asyncio.run(T.org_today("o1")) == date(2026, 10, 14)
    assert asyncio.run(T.org_month_bounds("o1")) == (date(2026, 10, 1), date(2026, 10, 31))
    assert asyncio.run(T.org_today(None)) == date(2026, 10, 14)
    assert asyncio.run(T.org_now(None)).utcoffset() == EDT


# ── 2026-10-25 time-zone rule: instants UTC with an offset, naive = UTC ─────────
def test_today_for_org_late_evening_toronto_is_the_toronto_date():
    at = _utc(2026, 10, 31, 2, 0)                        # 22:00 EDT on 30 Oct; UTC is already the 31st
    assert T.today_for_org("America/Toronto", at=at) == date(2026, 10, 30)
    assert T.today_for_org(None, at=at) == date(2026, 10, 30)          # default zone = Toronto
    assert T.today_for_org("UTC", at=at) == date(2026, 10, 31)


def test_now_utc_and_as_utc():
    n = T.now_utc()
    assert n.tzinfo is not None and n.utcoffset() == timedelta(0)
    naive = datetime(2026, 10, 25, 4, 0)
    assert T.as_utc(naive) == _utc(2026, 10, 25, 4, 0)                # naive is UTC by the house rule
    est = datetime(2026, 10, 25, 0, 0, tzinfo=T.tz_for("America/Toronto"))
    assert T.as_utc(est) == _utc(2026, 10, 25, 4, 0)
    assert T.as_utc(None) is None


def test_iso_utc_always_has_an_offset():
    assert T.iso_utc(datetime(2026, 10, 25, 4, 0)) == "2026-10-25T04:00:00+00:00"
    assert T.iso_utc(datetime(2026, 10, 25, 0, 0, tzinfo=T.tz_for("America/Toronto"))) == "2026-10-25T04:00:00+00:00"
    assert T.iso_utc(None) is None


def test_to_org_tz_shows_toronto_wall_clock():
    d = T.to_org_tz(_utc(2026, 10, 25, 14, 0))
    assert (d.hour, d.minute, d.utcoffset()) == (10, 0, EDT)
    assert T.to_org_tz(None) is None


def test_fastapi_json_emits_offset_for_naive_datetimes():
    pytest.importorskip("fastapi")
    from fastapi.encoders import jsonable_encoder
    T.install_utc_json()
    out = jsonable_encoder({"at": datetime(2026, 10, 25, 4, 0), "aware": _utc(2026, 10, 25, 4, 0),
                            "day": date(2026, 10, 25)})
    assert out == {"at": "2026-10-25T04:00:00+00:00", "aware": "2026-10-25T04:00:00+00:00", "day": "2026-10-25"}


def test_org_tz_name(monkeypatch):
    import shared.utils.org_settings as S

    async def setting(org, key, default=None):
        return "America/Vancouver"
    monkeypatch.setattr(S, "get_setting", setting)
    assert asyncio.run(T.org_tz_name("o1")) == "America/Vancouver"
    assert asyncio.run(T.org_tz_name(None)) == "America/Toronto"
