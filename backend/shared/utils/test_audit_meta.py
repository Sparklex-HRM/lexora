"""record_audit must not lose a row because meta holds a Decimal / date / UUID (a pay run's
totals are Numeric → Decimal; before the integration fix every payroll.run.execute row was
silently dropped)."""
import decimal
import uuid
from datetime import date, datetime, timezone

from shared.utils.audit import compute_hash, jsonable_meta


def test_jsonable_meta_converts_db_types():
    u = uuid.uuid4()
    m = jsonable_meta({"total_gross": decimal.Decimal("75000.50"), "pay_date": date(2026, 9, 30),
                       "at": datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc), "id": u, "tags": {"a"},
                       "nested": {"x": decimal.Decimal("1")}})
    assert m == {"total_gross": 75000.5, "pay_date": "2026-09-30", "at": "2026-10-15T10:00:00+00:00",
                 "id": str(u), "tags": ["a"], "nested": {"x": 1.0}}


def test_jsonable_meta_none_and_plain():
    assert jsonable_meta(None) is None
    assert jsonable_meta({"a": 1, "b": "x"}) == {"a": 1, "b": "x"}


def test_hash_is_stable_over_normalised_meta():
    m = jsonable_meta({"v": decimal.Decimal("2.5")})
    p = {"org_id": "o", "seq": 1, "meta": m}
    assert compute_hash("0" * 64, p) == compute_hash("0" * 64, {"org_id": "o", "seq": 1, "meta": {"v": 2.5}})
