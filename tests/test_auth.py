import pytest

from app import api as api_mod
from app import auth as accounts

from .conftest import TOKEN


@pytest.fixture(autouse=True)
def _reset_fails():
    api_mod._login_fails.clear()
    yield
    api_mod._login_fails.clear()


def make_account(conn, user="sam", pw="correct horse"):
    accounts.set_account(conn, user, pw)


def login(client, user="sam", pw="correct horse"):
    return client.post("/api/login", json={"username": user, "password": pw}, headers={"Authorization": ""})


def test_password_hash_roundtrip():
    h = accounts.hash_password("hunter22")
    assert h.startswith("scrypt$") and "hunter22" not in h
    assert accounts.verify_password("hunter22", h)
    assert not accounts.verify_password("hunter23", h)
    assert not accounts.verify_password("x", "garbage")


def test_login_without_account(client):
    r = login(client)
    assert r.status_code == 409 and "set-password" in r.json()["detail"]


def test_login_issues_a_working_session(client, conn):
    make_account(conn)
    r = login(client)
    assert r.status_code == 200 and r.json()["username"] == "sam"
    tok = r.json()["token"]
    assert client.get("/api/status", headers={"Authorization": f"Bearer {tok}"}).status_code == 200
    assert client.get(f"/api/events/stream?t=bad", headers={"Authorization": ""}).status_code == 401
    assert client.get("/api/account", headers={"Authorization": f"Bearer {tok}"}).json() == {"username": "sam"}
    assert client.post("/api/logout", headers={"Authorization": f"Bearer {tok}"}).status_code == 200
    assert client.get("/api/status", headers={"Authorization": f"Bearer {tok}"}).status_code == 401


def test_wrong_password_and_rate_limit(client, conn):
    make_account(conn)
    assert login(client, pw="nope").status_code == 401
    assert login(client, user="other").status_code == 401
    for _ in range(3):
        login(client, pw="nope")
    assert login(client).status_code == 429


def test_change_password_signs_out_other_sessions(client, conn):
    make_account(conn)
    a, b = login(client).json()["token"], login(client).json()["token"]
    ha = {"Authorization": f"Bearer {a}"}
    r = client.put("/api/account/password", json={"current_password": "wrong", "new_password": "newpassword"}, headers=ha)
    assert r.status_code == 403
    r = client.put("/api/account/password", json={"current_password": "correct horse", "new_password": "short"}, headers=ha)
    assert r.status_code == 422
    r = client.put("/api/account/password", json={"current_password": "correct horse", "new_password": "newpassword"}, headers=ha)
    assert r.status_code == 200
    assert client.get("/api/status", headers=ha).status_code == 200
    assert client.get("/api/status", headers={"Authorization": f"Bearer {b}"}).status_code == 401
    assert login(client, pw="newpassword").status_code == 200


def test_shortcut_token(client, conn):
    make_account(conn)
    tok = client.post("/api/tokens", json={"label": "Siri Shortcut"}).json()["token"]
    r = client.post("/api/ingest", json={"url": "https://www.instagram.com/reel/ABC123/"}, headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 201


def test_api_token_still_accepted(client):
    assert client.get("/api/status", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
