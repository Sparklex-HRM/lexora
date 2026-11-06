"""Employee portal batch 3 — company feed (spec item 12): pure rules + every /engage/feed route's role checks.
Run:  cd backend/services/ops && python -m pytest -q test_ess_portal_b3_feed.py
"""
import json
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.sql import operators as ops
from sqlalchemy.sql.elements import BinaryExpression, BindParameter, BooleanClauseList, Null, True_, False_

import feed_logic as F
import feed_routers as FR
from database import get_db
from engage_models import Kudos
from feed_models import FeedComment, FeedPost, FeedPrefs, FeedReaction, FeedVote

ORG = str(uuid.uuid4())
OTHER_ORG = str(uuid.uuid4())
PRIYA, VIKRAM, HRH, NEW = (str(uuid.uuid4()) for _ in range(4))
TODAY = date(2026, 10, 16)
NOW = datetime(2026, 10, 16, 6, 0, tzinfo=timezone.utc)

ROSTER = {
    PRIYA: {"id": PRIYA, "first_name": "Priya", "last_name": "Menon", "department": "Engineering", "work_location": "Chennai",
            "date_of_birth": date(1994, 10, 16), "hire_date": date(2023, 10, 14), "status": "active"},
    VIKRAM: {"id": VIKRAM, "first_name": "Vikram", "last_name": "Rao", "department": "Engineering", "work_location": "Bengaluru",
             "date_of_birth": date(1988, 3, 2), "hire_date": date(2019, 1, 7), "status": "active"},
    HRH: {"id": HRH, "first_name": "Anita", "last_name": "Iyer", "department": "Human Resources", "work_location": "Chennai",
          "date_of_birth": date(1985, 10, 12), "hire_date": date(2015, 6, 1), "status": "active"},
    NEW: {"id": NEW, "first_name": "Rahul", "last_name": "Das", "department": "Sales", "work_location": "Mumbai",
          "date_of_birth": None, "hire_date": date(2026, 10, 13), "status": "onboarding"},
}


# ══════════════════════════ pure rules ════════════════════════════════════════

def test_auto_events_birthday_anniversary_new_joiner_and_privacy():
    ev = F.auto_events(list(ROSTER.values()), TODAY, "Maple Logistics Inc.")
    keys = {e["auto_key"] for e in ev}
    assert f"birthday:{PRIYA}:2026" in keys and f"birthday:{HRH}:2026" in keys     # 16 Oct today, 12 Oct in window
    assert f"birthday:{VIKRAM}:2026" not in keys                                     # March — out of window
    assert f"anniversary:{PRIYA}:2026" in keys and f"new_joiner:{NEW}" in keys
    ann = next(e for e in ev if e["auto_key"] == f"anniversary:{PRIYA}:2026")
    assert ann["years"] == 3 and ann["body"] == "Priya Menon completes 3 years at Maple Logistics. Congratulations!"
    bday = next(e for e in ev if e["auto_key"] == f"birthday:{PRIYA}:2026")
    assert bday["day_month"] == "16 October"
    blob = json.dumps(ev, default=str)
    assert "1994" not in blob and "1985" not in blob and " 32" not in bday["body"]   # never the year / age
    assert all(e["kind"] != "anniversary" or e["subject_employee_id"] != NEW for e in ev)  # 0 years: no anniversary


def test_auto_events_opt_out_inactive_and_feb29():
    prefs = {PRIYA: {"birthday": True, "anniversary": True}}
    keys = {e["auto_key"] for e in F.auto_events(list(ROSTER.values()), TODAY, "Maple", prefs)}
    assert f"birthday:{PRIYA}:2026" not in keys and f"anniversary:{PRIYA}:2026" not in keys
    gone = {**ROSTER[VIKRAM], "status": "terminated", "date_of_birth": date(1990, 10, 15)}
    assert F.auto_events([gone], TODAY, "Maple") == []
    leap = {"id": "x", "first_name": "L", "date_of_birth": date(1996, 2, 29), "status": "active"}
    ev = F.auto_events([leap], date(2027, 3, 1), "Maple")
    assert ev and ev[0]["event_date"] == date(2027, 2, 28) and ev[0]["day_month"] == "28 February"
    # year boundary: window spans 2026→2027
    ny = {"id": "y", "first_name": "N", "date_of_birth": date(1990, 12, 30), "status": "active"}
    assert [e["auto_key"] for e in F.auto_events([ny], date(2027, 1, 3), "K")] == ["birthday:y:2026"]


def test_idempotency_and_short_org():
    ev = F.auto_events(list(ROSTER.values()), TODAY, "Maple")
    first = F.missing_events(ev, set())
    assert len(first) == len(ev)
    assert F.missing_events(ev, {e["auto_key"] for e in ev}) == []
    assert F.short_org("Maple Logistics Incorporated") == "Maple Logistics"
    assert F.short_org("Maple") == "Maple" and F.short_org("") == "the company"
    assert F.day_month(date(1990, 1, 5)) == "5 January"


def test_post_poll_validation_and_votes():
    assert F.validate_post("post", "  ", None, None, NOW)[0]
    assert F.validate_post("post", "Hello", ["a"], None, NOW)[0] == "Only a poll has options"
    assert F.validate_post("poll", "Lunch?", ["Yes"], None, NOW)[0].startswith("A poll needs")
    assert F.validate_post("poll", "Lunch?", ["Yes", "yes"], None, NOW)[0] == "Poll options must be different"
    assert F.validate_post("poll", "Lunch?", ["A", "B"], NOW - timedelta(hours=1), NOW)[0] == "The poll must close in the future"
    e, opts = F.validate_post("poll", "Lunch?", ["A", "B", " "], NOW + timedelta(days=1), NOW)
    assert e is None and opts == [{"id": "o1", "label": "A"}, {"id": "o2", "label": "B"}]
    assert F.check_vote(opts, "o1", None, NOW, False) is None
    assert F.check_vote(opts, "o1", None, NOW, True) == "You have already voted in this poll"
    assert F.check_vote(opts, "o9", None, NOW, False) == "Pick one of the poll's options"
    assert F.check_vote(opts, "o1", NOW - timedelta(seconds=1), NOW, False) == "This poll has closed"
    r = F.poll_results(opts, [{"option_id": "o1", "voter_key": "a"}, {"option_id": "o1", "voter_key": "b"},
                              {"option_id": "o2", "voter_key": "c"}], "c", None, NOW)
    assert [o["count"] for o in r["options"]] == [2, 1] and r["my_vote"] == "o2" and r["total"] == 3 and r["options"][0]["pct"] == 67


def test_permissions_visibility_filters():
    assert F.can_delete("post", PRIYA, None, PRIYA, None) and not F.can_delete("post", PRIYA, None, VIKRAM, None)
    assert not F.can_delete("birthday", None, None, PRIYA, None) and not F.can_delete("kudos", PRIYA, None, PRIYA, None)
    assert F.can_moderate("hr_admin") and not F.can_moderate("manager") and not F.can_moderate("employee")
    assert F.check_audience("employee", ROSTER[PRIYA], "Sales", None)
    assert F.check_audience("employee", ROSTER[PRIYA], "engineering", "chennai") is None
    assert F.check_audience("hr_manager", ROSTER[HRH], "Sales", "Mumbai") is None
    item = {"kind": "post", "hidden": False, "audience_department": "Sales", "author_employee_id": NEW, "department": "Sales"}
    assert not F.visible_to(item, "employee", PRIYA, ROSTER[PRIYA]) and F.visible_to(item, "employee", NEW, ROSTER[NEW])
    assert F.visible_to(item, "org_admin", HRH, ROSTER[HRH])
    assert not F.visible_to({**item, "audience_department": None, "hidden": True}, "employee", NEW, ROSTER[NEW])
    assert F.matches_filters({"kind": "birthday", "department": "Sales"}, "sales", None, "celebrations")
    assert not F.matches_filters({"kind": "post", "department": "Sales"}, None, None, "celebrations")
    assert not F.matches_filters({"kind": "post", "location": "Pune"}, None, "Chennai", None)
    assert F.suppressed_by_optout({"kind": "birthday", "subject_employee_id": PRIYA}, {PRIYA: {"birthday": True}})
    assert not F.suppressed_by_optout({"kind": "post", "subject_employee_id": PRIYA}, {PRIYA: {"birthday": True}})
    assert F.check_hide_reason("no") and F.check_hide_reason("Off-topic") is None
    assert F.parse_item_id(f"kudos:{PRIYA}")[0] == "kudos" and F.parse_item_id("nope") is None
    order = F.order_items([{"created_at": "2026-10-01", "pinned": True}, {"created_at": "2026-10-10", "pinned": False}], True)
    assert order[0]["pinned"]


# ══════════════════════════ routes (FakeDB that evaluates simple WHERE clauses) ═════

def _norm(v):
    return str(v) if isinstance(v, uuid.UUID) else v


def _rhs(right):
    if isinstance(right, Null):
        return None
    if isinstance(right, True_):
        return True
    if isinstance(right, False_):
        return False
    if isinstance(right, BindParameter):
        return right.value
    return right


def _match(obj, clause) -> bool:
    if clause is None:
        return True
    if isinstance(clause, BooleanClauseList):
        return all(_match(obj, c) for c in clause.clauses)
    if isinstance(clause, BinaryExpression):
        left = getattr(obj, clause.left.key, None)
        right = _rhs(clause.right)
        op = clause.operator
        lv = _norm(left)
        if op is ops.eq:
            return lv == _norm(right)
        if op is ops.ne:
            return lv != _norm(right)
        if op in (ops.is_, ops.is_not if hasattr(ops, "is_not") else ops.isnot):
            same = (left is None) if right is None else (bool(left) is right)
            return same if op is ops.is_ else not same
        if op is ops.in_op:
            return lv in {_norm(x) for x in (right or [])}
        if op is ops.lt:
            return left is not None and left < right
        if op is ops.gt:
            return left is not None and left > right
    if hasattr(clause, "clauses"):                    # OR etc. — be permissive
        return any(_match(obj, c) for c in clause.clauses)
    return True


class _R:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None


class FakeDB:
    def __init__(self):
        self.rows: dict[type, list] = {}

    def put(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        if getattr(obj, "created_at", None) is None:
            obj.created_at = datetime.now(timezone.utc)
        for col in obj.__table__.columns:                 # mimic column defaults
            if getattr(obj, col.key, None) is None and col.default is not None and getattr(col.default, "is_scalar", False):
                setattr(obj, col.key, col.default.arg)
        lst = self.rows.setdefault(type(obj), [])
        if obj not in lst:
            lst.append(obj)
        return obj

    async def execute(self, stmt, *a, **k):
        ent = stmt.column_descriptions[0]["entity"]
        return _R([o for o in self.rows.get(ent, []) if _match(o, stmt.whereclause)])

    def add(self, obj):
        self.put(obj)

    async def delete(self, obj):
        self.rows[type(obj)].remove(obj)

    async def commit(self):
        pass

    async def rollback(self):
        pass

    async def refresh(self, o):
        pass


@pytest.fixture
def db(monkeypatch):
    d = FakeDB()

    async def roster(org):
        return ROSTER if org == ORG else {}

    async def today(org):
        return TODAY

    async def org_name(org):
        return "Maple Logistics Inc."

    async def quiet(*a, **k):
        return True
    monkeypatch.setattr(FR, "load_roster", roster)
    monkeypatch.setattr(FR, "org_today", today)
    monkeypatch.setattr(FR.directory, "org_name", org_name)
    monkeypatch.setattr(FR, "notify", quiet)
    monkeypatch.setattr(FR, "record_audit", quiet)
    FR._gen_cache.clear()
    return d


def _client(db) -> TestClient:
    app = FastAPI()
    app.include_router(FR.router)

    async def _db():
        yield db
    app.dependency_overrides[get_db] = _db
    return TestClient(app)


def H(role, emp, org=ORG):
    return {"x-org-id": org, "x-user-id": f"user-{emp or role}", "x-user-email": f"{role}@maple.example", "x-role": role,
            "x-employee-id": emp or ""}


def test_feed_requires_org_and_generates_auto_posts_once(db):
    c = _client(db)
    assert c.get("/engage/feed").status_code == 401
    r = c.get("/engage/feed", headers=H("employee", PRIYA))
    assert r.status_code == 200
    kinds = [i["kind"] for i in r.json()["items"]]
    assert "birthday" in kinds and "anniversary" in kinds and "new_joiner" in kinds
    n = len(db.rows[FeedPost])
    FR._gen_cache.clear()
    c.get("/engage/feed", headers=H("employee", PRIYA))
    assert len(db.rows[FeedPost]) == n                    # idempotent
    blob = r.text
    assert "1994" not in blob and "1985" not in blob and "date_of_birth" not in blob
    bd = next(i for i in r.json()["items"] if i["kind"] == "birthday" and i["subject"]["employee_id"] == PRIYA)
    assert bd["event_label"] == "16 October" and not bd["can_delete"] and not bd["can_moderate"]
    assert r.json()["departments"] == ["Engineering", "Human Resources", "Sales"]
    # another org sees none of it
    assert c.get("/engage/feed", headers=H("employee", None, OTHER_ORG)).json()["items"] == []


def test_opt_out_hides_existing_auto_post(db):
    c = _client(db)
    c.get("/engage/feed", headers=H("employee", PRIYA))
    assert c.get("/engage/feed/prefs", headers=H("employee", PRIYA)).json()["birthday"] is False
    r = c.put("/engage/feed/prefs", json={"birthday": True}, headers=H("employee", PRIYA))
    assert r.status_code == 200 and r.json()["birthday"] is True and r.json()["anniversary"] is False
    items = c.get("/engage/feed?kind=birthday", headers=H("employee", VIKRAM)).json()["items"]
    assert all(i["subject"]["employee_id"] != PRIYA for i in items) and any(i["subject"]["employee_id"] == HRH for i in items)
    assert c.put("/engage/feed/prefs", json={"birthday": True}, headers=H("org_admin", None)).status_code == 400


def test_post_poll_react_comment_delete_and_roles(db):
    c = _client(db)
    r = c.post("/engage/feed", json={"kind": "post", "body": "Release went out"}, headers=H("employee", PRIYA))
    assert r.status_code == 201 and r.json()["can_delete"] and r.json()["department"] == "Engineering"
    pid = r.json()["id"]
    assert c.post("/engage/feed", json={"kind": "post", "body": "x", "audience_department": "Sales"},
                  headers=H("employee", PRIYA)).status_code == 403
    assert c.post("/engage/feed", json={"kind": "post", "body": "x", "audience_department": "Sales"},
                  headers=H("hr_admin", HRH)).status_code == 201
    # reaction toggle
    assert c.post(f"/engage/feed/{pid}/react", json={"emoji": "party"}, headers=H("employee", VIKRAM)).status_code == 422
    r = c.post(f"/engage/feed/{pid}/react", json={"emoji": "celebrate"}, headers=H("employee", VIKRAM))
    assert r.json()["on"] and r.json()["reactions"]["celebrate"] == {"count": 1, "mine": True}
    r = c.post(f"/engage/feed/{pid}/react", json={"emoji": "celebrate"}, headers=H("employee", VIKRAM))
    assert not r.json()["on"] and r.json()["reactions"]["celebrate"]["count"] == 0
    # comments
    r = c.post(f"/engage/feed/{pid}/comments", json={"body": "Nice work"}, headers=H("employee", VIKRAM))
    assert r.status_code == 201
    cid = r.json()["id"]
    assert c.delete(f"/engage/feed/comments/{cid}", headers=H("employee", PRIYA)).status_code == 403
    assert c.post(f"/engage/feed/comments/{cid}/hide", json={"reason": "Off-topic"}, headers=H("employee", PRIYA)).status_code == 403
    assert c.post(f"/engage/feed/comments/{cid}/hide", json={"reason": ""}, headers=H("hr_manager", HRH)).status_code == 422
    r = c.post(f"/engage/feed/comments/{cid}/hide", json={"reason": "Off-topic"}, headers=H("hr_manager", HRH))
    assert r.status_code == 200 and r.json()["hidden_reason"] == "Off-topic"
    assert c.get(f"/engage/feed/{pid}/comments", headers=H("employee", PRIYA)).json()["comments"] == []
    assert len(c.get(f"/engage/feed/{pid}/comments", headers=H("hr_manager", HRH)).json()["comments"]) == 1
    c.post(f"/engage/feed/comments/{cid}/unhide", headers=H("hr_manager", HRH))
    assert c.delete(f"/engage/feed/comments/{cid}", headers=H("employee", VIKRAM)).status_code == 200
    # delete own post only
    assert c.delete(f"/engage/feed/{pid}", headers=H("employee", VIKRAM)).status_code == 403
    assert c.delete(f"/engage/feed/{pid}", headers=H("hr_admin", HRH)).status_code == 403
    assert c.delete(f"/engage/feed/{pid}", headers=H("employee", PRIYA)).status_code == 200
    assert c.get(f"/engage/feed/{pid}/comments", headers=H("employee", PRIYA)).status_code == 404
    # cross-org: invisible
    other = c.post("/engage/feed", json={"kind": "post", "body": "Hi"}, headers=H("employee", PRIYA)).json()["id"]
    assert c.post(f"/engage/feed/{other}/react", json={"emoji": "like"}, headers=H("employee", None, OTHER_ORG)).status_code == 404


def test_poll_vote_once_and_close(db):
    c = _client(db)
    closes = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    r = c.post("/engage/feed", json={"kind": "poll", "body": "Diwali lunch?", "options": ["Thali", "Biryani"], "closes_at": closes},
               headers=H("hr_admin", HRH))
    assert r.status_code == 201 and r.json()["poll"]["total"] == 0
    pid = r.json()["id"]
    r = c.post(f"/engage/feed/{pid}/vote", json={"option_id": "o2"}, headers=H("employee", PRIYA))
    assert r.status_code == 200 and r.json()["poll"]["my_vote"] == "o2"
    assert c.post(f"/engage/feed/{pid}/vote", json={"option_id": "o1"}, headers=H("employee", PRIYA)).status_code == 409
    assert c.post(f"/engage/feed/{pid}/vote", json={"option_id": "zz"}, headers=H("employee", VIKRAM)).status_code == 422
    feed = c.get("/engage/feed?kind=poll", headers=H("employee", VIKRAM)).json()["items"]
    assert feed[0]["poll"]["options"][1]["count"] == 1 and feed[0]["poll"]["my_vote"] is None
    next(p for p in db.rows[FeedPost] if str(p.id) == pid).poll_closes_at =datetime.now(timezone.utc) - timedelta(minutes=1)
    assert c.post(f"/engage/feed/{pid}/vote", json={"option_id": "o1"}, headers=H("employee", VIKRAM)).status_code == 409
    post = c.post("/engage/feed", json={"kind": "post", "body": "Not a poll"}, headers=H("employee", PRIYA)).json()["id"]
    assert c.post(f"/engage/feed/{post}/vote", json={"option_id": "o1"}, headers=H("employee", PRIYA)).status_code == 422


def test_hr_hide_pin_and_kudos_in_feed(db):
    c = _client(db)
    old = FeedPost(org_id=uuid.UUID(ORG), kind="post", author_employee_id=uuid.UUID(HRH), author_name="Anita Iyer",
                   body="Diwali week schedule", created_at=datetime(2026, 9, 1, tzinfo=timezone.utc))
    db.put(old)
    k = Kudos(org_id=uuid.UUID(ORG), from_employee_id=uuid.UUID(VIKRAM), from_name="Vikram Rao", to_employee_id=uuid.UUID(PRIYA),
              to_name="Priya Sharma", value_tag="Ownership", message="Saved the release", status="approved")
    db.put(k)
    db.put(Kudos(org_id=uuid.UUID(ORG), from_employee_id=uuid.UUID(VIKRAM), to_employee_id=uuid.UUID(PRIYA),
                 message="pending one", status="pending"))
    pid = str(old.id)
    assert c.post(f"/engage/feed/{pid}/pin", headers=H("employee", PRIYA)).status_code == 403
    assert c.post(f"/engage/feed/{pid}/pin", headers=H("manager", VIKRAM)).status_code == 403
    assert c.post(f"/engage/feed/{pid}/pin", headers=H("hr_admin", HRH)).json()["pinned"] is True
    items = c.get("/engage/feed?limit=5", headers=H("employee", PRIYA)).json()["items"]
    assert items[0]["id"] == pid and items[0]["pinned"]
    kud = [i for i in c.get("/engage/feed?kind=kudos", headers=H("employee", PRIYA)).json()["items"]]
    assert len(kud) == 1 and kud[0]["id"] == f"kudos:{k.id}" and kud[0]["kudos"]["value_tag"] == "Ownership"
    assert kud[0]["department"] == "Engineering" and not kud[0]["can_delete"]
    r = c.post(f"/engage/feed/kudos:{k.id}/react", json={"emoji": "like"}, headers=H("employee", PRIYA))
    assert r.status_code == 200 and r.json()["item_id"] == f"kudos:{k.id}"
    assert c.post(f"/engage/feed/kudos:{k.id}/comments", json={"body": "Well deserved"}, headers=H("employee", HRH)).status_code == 201
    assert c.post(f"/engage/feed/kudos:{k.id}/hide", json={"reason": "spam spam"}, headers=H("hr_admin", HRH)).status_code == 422
    # hide with reason → employees stop seeing it, HR still sees it with the reason
    assert c.post(f"/engage/feed/{pid}/hide", json={"reason": ""}, headers=H("hr_admin", HRH)).status_code == 422
    assert c.post(f"/engage/feed/{pid}/hide", json={"reason": "Outdated schedule"}, headers=H("hr_admin", HRH)).status_code == 200
    assert pid not in [i["id"] for i in c.get("/engage/feed", headers=H("employee", PRIYA)).json()["items"]]
    hr_items = c.get("/engage/feed", headers=H("org_admin", None)).json()["items"]
    hid = next(i for i in hr_items if i["id"] == pid)
    assert hid["hidden"] and hid["hidden_reason"] == "Outdated schedule"
    assert c.post(f"/engage/feed/{pid}/react", json={"emoji": "like"}, headers=H("employee", PRIYA)).status_code == 404
    assert c.post(f"/engage/feed/{pid}/unhide", headers=H("hr_admin", HRH)).json()["hidden"] is False
    assert c.post(f"/engage/feed/{pid}/archive", headers=H("hr_admin", HRH)).status_code == 404
    # filters
    assert all(i["location"] == "Mumbai" or i["audience"]["location"] == "Mumbai"
               for i in c.get("/engage/feed?location=Mumbai", headers=H("hr_admin", HRH)).json()["items"])
    assert c.get("/engage/feed?kind=nope", headers=H("employee", PRIYA)).status_code == 422
