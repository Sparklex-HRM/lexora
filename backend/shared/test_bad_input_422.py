"""WASA follow-up (2026-10-24): malformed ids / values -> 422 INVALID_INPUT, never a masked 500."""
import uuid

import sqlalchemy.exc as sx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from shared.ops_runtime import install_error_masking, is_client_input_error

app = FastAPI()
install_error_masking(app, "test")


@app.get("/uuid/{rid}")
async def by_uuid(rid: str):
    return {"id": str(uuid.UUID(rid))}


class _PgDataError(Exception):
    sqlstate = "22P02"


@app.get("/db")
async def db_error():
    raise sx.DataError("SELECT … WHERE id = $1", {}, _PgDataError("invalid input syntax for type uuid"))


@app.get("/boom")
async def boom():
    raise RuntimeError("secret table name")


@app.get("/integrity")
async def integrity():
    raise sx.IntegrityError("INSERT", {}, Exception("duplicate key"))


def test_bad_ids_are_422_with_a_code():
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/uuid/not-a-uuid")
    assert r.status_code == 422 and r.json()["code"] == "INVALID_INPUT" and r.json()["ref"]
    assert "hexadecimal" not in r.text
    r = c.get("/db")
    assert r.status_code == 422 and r.json()["code"] == "INVALID_INPUT" and "SELECT" not in r.text


def test_real_failures_stay_masked_500():
    c = TestClient(app, raise_server_exceptions=False)
    for path in ("/boom", "/integrity"):
        r = c.get(path)
        assert r.status_code == 500 and "secret" not in r.text and "duplicate" not in r.text and r.json()["ref"]


def test_classifier():
    assert is_client_input_error(ValueError("badly formed hexadecimal UUID string"))
    assert not is_client_input_error(ValueError("something else"))
    assert not is_client_input_error(KeyError("x"))
