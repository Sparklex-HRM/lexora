"""Global search toolkit (shared/utils/search.py).
Run:  cd backend && python -m pytest -q shared/utils/test_search.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from shared.utils import search as S  # noqa: E402


def test_normalise_and_tokens():
    assert S.normalise("  Priya   Menon ") == "Priya Menon"
    assert S.tokens("Priya  MENON") == ["priya", "menon"]
    assert len(S.normalise("x" * 500)) == S.MAX_Q
    assert len(S.tokens("a b c d e f g")) == 5


def test_like_escapes_wildcards():
    assert S.like("50%") == "%50\\%%"
    assert S.like("a_b") == "%a\\_b%"


def test_clamp_and_types():
    assert S.clamp_limit(None) == S.DEFAULT_LIMIT and S.clamp_limit(999) == S.MAX_LIMIT and S.clamp_limit("x") == S.DEFAULT_LIMIT
    assert S.parse_types(None, ["a", "b"]) == {"a", "b"}
    assert S.parse_types("b, c", ["a", "b"]) == {"b"}
    assert S.parse_types("zzz", ["a"]) == set()


def test_score_ladder():
    assert S.score("KAV017", "KAV017") == 100
    assert S.score("pri", "Priya Menon") == 90
    assert S.score("menon", "Priya Menon") == 85
    assert S.score("men", "Priya Menon") == 75
    assert S.score("engineering", "Priya Menon", "Engineering") == 85
    assert 0 < S.score("priay", "Priya Menon") < 50          # typo still ranks, below exact hits
    assert S.score("zzzz", "Priya Menon") == 0


def test_finish_caps_per_type_and_orders():
    hits = [S.hit("a", i, f"t{i}", "", "/x", float(i)) for i in range(10)] + [S.hit("b", "z", "z", "", "/y", 0.0)]
    out = S.finish(hits, 3)
    assert [h["id"] for h in out] == ["9", "8", "7"]          # zero score dropped, best first, 3 per type


def test_modules_header():
    assert S.has_module({}, "payroll") is True                    # header absent → fail open (pre-claim token)
    assert S.has_module({"x-modules": "core,leave"}, "payroll") is False
    assert S.has_module({"x-modules": "core,payroll"}, "payroll") is True
