"""GET /time/rules: every one of the 13 provinces / territories has its name; a non-Canadian code is refused."""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import routers as R

REQ = SimpleNamespace(headers={"x-org-id": "11111111-1111-1111-1111-111111111111"})
PROVS = ("ON", "BC", "AB", "QC", "MB", "SK", "NS", "NB", "NL", "PE", "NT", "NU", "YT")


def _rules(p):
    return asyncio.new_event_loop().run_until_complete(R.rules(REQ, province=p, year=2026))


@pytest.mark.parametrize("prov", PROVS)
def test_every_province_has_a_label_and_rules(prov):
    out = _rules(prov)
    assert out["province"] == prov and out["province_label"] != prov
    assert out["overtime"]["weekly_ot_after"]


def test_non_canadian_code_is_refused():
    with pytest.raises(HTTPException) as ex:
        _rules("KA")
    assert ex.value.status_code == 422
