import json
import os
import time
from types import SimpleNamespace

import pytest

from app import api as api_mod
from app import db, worker
from app.ml import llm
from app.stages import fetch
from app.stages.fetch import RateLimited, Terminal

from .conftest import TOKEN, add_item
from .test_worker import Clock, FakeStage

RESTRICTED = ("DownloadError: ERROR: [Instagram] Df3jWGLMKt0: This content isn't available to everyone: "
              "It can't be seen by certain audiences.")
GALLERY_LOGIN = ("RuntimeError: gallery-dl: [instagram][error] HTTP redirect to login page "
                 "(https://www.instagram.com/accounts/login/)")


def events(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM events ORDER BY id")]


def test_add_event_and_bounding(conn, monkeypatch):
    item = {"id": 7, "shortcode": "ABC"}
    eid = db.add_event(conn, "extract", "extract done: 4.0 s audio", item=item, duration=12.345)
    e = events(conn)[0]
    assert e["id"] == eid and e["shortcode"] == "ABC" and e["item_id"] == 7 and e["duration_s"] == 12.35
    assert e["level"] == "info" and e["at"].endswith("Z")
    monkeypatch.setattr(db, "EVENTS_KEEP", 5)
    for i in range(12):
        db.add_event(conn, "fetch", f"m{i}")
    rows = events(conn)
    assert len(rows) == 5 and [r["message"] for r in rows] == [f"m{i}" for i in range(7, 12)]


def test_events_paging(client, conn):
    for i in range(30):
        db.add_event(conn, "fetch", f"m{i}")
    r = client.get("/api/events?limit=10").json()
    assert [e["message"] for e in r["events"]] == [f"m{i}" for i in range(20, 30)]
    assert r["last_id"] == r["events"][-1]["id"]
    first = client.get("/api/events?after_id=0&limit=12").json()
    assert [e["message"] for e in first["events"]] == [f"m{i}" for i in range(12)]
    nxt = client.get(f"/api/events?after_id={first['last_id']}&limit=200").json()
    assert len(nxt["events"]) == 18 and nxt["events"][0]["id"] == first["last_id"] + 1
    empty = client.get(f"/api/events?after_id={nxt['last_id']}").json()
    assert empty == {"events": [], "last_id": nxt["last_id"]}
    assert client.get("/api/events", headers={"Authorization": ""}).status_code == 401


def parse_sse(text):
    out = []
    for block in text.strip().split("\n\n"):
        msg = {}
        for line in block.splitlines():
            k, _, v = line.partition(": ")
            msg[k] = v
        if "data" in msg:
            out.append({"id": msg.get("id"), "event": msg.get("event", "message"), "data": json.loads(msg["data"])})
    return out


def test_sse_stream_initial_events(client, conn, monkeypatch):
    monkeypatch.setattr(api_mod, "SSE_MAX_POLLS", 1)
    for i in range(60):
        db.add_event(conn, "fetch", f"m{i}", item={"id": i, "shortcode": f"S{i}"})
    assert client.get("/api/events/stream", headers={"Authorization": ""}).status_code == 401
    client.headers.pop("Authorization")
    with client.stream("GET", f"/api/events/stream?t={TOKEN}") as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        body = "".join(r.iter_text())
    msgs = parse_sse(body)
    logs = [m for m in msgs if m["event"] == "message"]
    assert len(logs) == 50 and logs[0]["data"]["message"] == "m10" and logs[-1]["data"]["message"] == "m59"
    assert logs[-1]["id"] == str(logs[-1]["data"]["id"])
    status = [m for m in msgs if m["event"] == "status"]
    assert status and "progress" in status[0]["data"] and "counts" in status[0]["data"]
    with client.stream("GET", f"/api/events/stream?t={TOKEN}", headers={"Last-Event-ID": logs[-3]["id"]}) as r:
        again = [m for m in parse_sse("".join(r.iter_text())) if m["event"] == "message"]
    assert [m["data"]["message"] for m in again] == ["m58", "m59"]


def test_status_progress_and_eta(client, conn, env):
    for i in range(4):
        add_item(conn, f"Q{i}", status="queued")
    for i in range(2):
        add_item(conn, f"F{i}", status="fetched")
    add_item(conn, "E0", status="extracted")
    add_item(conn, "I0", status="indexed")
    add_item(conn, "X0", status="failed", failed_stage="enrich")
    add_item(conn, "P0", status="private")
    add_item(conn, "N0", status="needs_login")
    it = {"id": 1, "shortcode": "Q0"}
    for stage, dur in (("fetch", 4.0), ("fetch", 6.0), ("extract", 30.0), ("enrich", 10.0), ("index", 2.0)):
        db.add_event(conn, stage, f"{stage} done", item=it, duration=dur)
    db.add_event(conn, "extract", "loaded whisper in 9.0 s", duration=9.0)
    conn.execute("UPDATE events SET at='2000-01-01T00:00:00Z' WHERE message='fetch done' AND duration_s=4.0")
    conn.commit()
    s = client.get("/api/status").json()
    p = s["progress"]
    assert p["fetch"] == {"done": 5, "total": 9, "remaining": 4, "done_last_hour": 1, "avg_s": 5.0}
    assert p["extract"]["remaining"] == 6 and p["extract"]["avg_s"] == 30.0 and p["extract"]["done"] == 3
    assert p["enrich"]["remaining"] == 7 and p["index"]["remaining"] == 7 and p["index"]["done"] == 1
    processing = 4 * 5 + 6 * 30 + 7 * 10 + 7 * 2
    fetching = 4 * ((env.fetch_delay_min + env.fetch_delay_max) / 2 + 5)
    assert s["eta_seconds"] == round(max(processing, fetching)) == 284
    assert s["worker"]["fetch_backoff_until"] is None
    assert any(e["status"] == "needs_login" for e in s["last_errors"])
    conn.execute("DELETE FROM events WHERE stage='index'")
    conn.commit()
    assert client.get("/api/status").json()["eta_seconds"] is None


def test_eta_fetch_bound():
    prog = {s: {"remaining": 0, "avg_s": None} for s in ("extract", "enrich", "index")}
    prog["fetch"] = {"remaining": 100, "avg_s": 2.0}
    assert api_mod.eta_seconds(prog) == round(100 * ((api_mod.settings.fetch_delay_min + api_mod.settings.fetch_delay_max) / 2 + 2))


@pytest.mark.parametrize("message, cookies, expected", [
    (RESTRICTED, False, "restricted"),
    (RESTRICTED, True, "restricted"),
    (GALLERY_LOGIN, False, "needs_login"),
    (GALLERY_LOGIN, True, None),
    ("ERROR: [Instagram] X: Requested content is not available, rate-limit reached or login required", False, "rate_limited"),
    ("ERROR: [Instagram] X: HTTP Error 429: Too Many Requests", False, "rate_limited"),
    ("ERROR: [Instagram] X: Unable to download webpage: HTTP Error 429", False, "rate_limited"),
    ("ERROR: [Instagram] X: Please wait a few minutes before you try again.", True, "rate_limited"),
    ("ERROR: [Instagram] X: login required", False, "rate_limited"),
    ("ERROR: [Instagram] C429abc: Connection reset by peer", False, None),
    ("gallery-dl: [instagram][error] LoginRequired: private profile", False, "private"),
    ("HttpError: '404 Not Found' for 'https://www.instagram.com/p/X/'", False, "gone"),
    ("Connection reset by peer", False, None),
])
def test_classify_table(message, cookies, expected):
    assert fetch.classify(message, cookies) == expected


@pytest.mark.parametrize("message, cookies, outcome", [
    (RESTRICTED.split(": ", 1)[1], False, ("private", fetch.RESTRICTED_MSG)),
    (GALLERY_LOGIN.split(": ", 1)[1], False, ("needs_login", fetch.NEEDS_LOGIN_MSG)),
    (GALLERY_LOGIN.split(": ", 1)[1], True, RuntimeError),
    ("ERROR: [Instagram] X: rate-limit reached or login required", False, RateLimited),
])
def test_fetch_process_outcomes(conn, env, monkeypatch, message, cookies, outcome):
    if cookies:
        env.cookies_path.parent.mkdir(parents=True, exist_ok=True)
        env.cookies_path.write_text("# cookies")

    def boom(item, d, cookiefile):
        raise RuntimeError(message)
    monkeypatch.setattr(fetch, "_fetch", boom)
    i = add_item(conn, "S1", status="queued")
    row = conn.execute("SELECT * FROM items WHERE id=?", (i,)).fetchone()
    if isinstance(outcome, tuple):
        with pytest.raises(Terminal) as e:
            fetch.process(conn, row)
        assert (e.value.status, str(e.value)) == outcome
    else:
        with pytest.raises(outcome) as e:
            fetch.process(conn, row)
        if outcome is RuntimeError:
            assert "redirect to login page" in str(e.value)


@pytest.fixture
def setup(conn, monkeypatch):
    log, fail = [], {}
    stages = [(n, s, FakeStage(n, nxt, log, fail)) for n, s, nxt in (
        ("fetch", "queued", "fetched"), ("extract", "fetched", "extracted"),
        ("enrich", "extracted", "enriched"), ("index", "enriched", "indexed"))]
    clock = Clock()
    monkeypatch.setattr(fetch, "disk_free_ok", lambda: True)
    monkeypatch.setattr(worker, "in_batch_window", lambda: False)
    monkeypatch.setattr(llm, "remote_healthy", lambda force=False: True)
    w = worker.Worker(conn, stages=stages, clock=clock, sleep=lambda s: None)
    return SimpleNamespace(w=w, log=log, fail=fail, clock=clock)


def row(conn, i):
    return conn.execute("SELECT * FROM items WHERE id=?", (i,)).fetchone()


def backoff_until(conn):
    return conn.execute("SELECT fetch_backoff_until FROM worker_status").fetchone()[0]


def test_backoff_schedule_and_reset(setup, conn):
    w, clock = setup.w, setup.clock
    a = add_item(conn, "A", status="queued")
    b = add_item(conn, "B", status="queued")
    setup.fail["A"] = RateLimited("rate-limit reached")
    setup.fail["B"] = RateLimited("rate-limit reached")
    delays = []
    for _ in range(5):
        clock.t = max(clock.t, w.next_fetch) + 0.1
        if w.backoff_until:
            clock.t = w.backoff_until + 0.1
        start = clock.t
        assert w.tick() is True
        delays.append(round(w.backoff_until - start))
        assert backoff_until(conn) is not None
        clock.t += 60
        assert w.tick() is False
    assert delays == [900, 1800, 3600, 7200, 7200]
    assert {row(conn, a)["status"], row(conn, b)["status"]} <= {"queued", "failed"}
    setup.fail.clear()
    c = add_item(conn, "C", status="queued")
    clock.t = w.backoff_until + 0.1
    w.tick()
    assert w.backoff_level == 0 and w.backoff_until is None and backoff_until(conn) is None
    msgs = [e["message"] for e in events(conn)]
    assert "backoff: pausing fetch for 15 min" in msgs and "backoff: pausing fetch for 2.0 h" in msgs
    assert "backoff over, fetching again" in msgs and "fetch works again, backoff reset" in msgs
    fetched = [x for x in (a, b, c) if row(conn, x)["status"] == "fetched"]
    assert fetched


def test_rate_limited_item_requeued_then_failed(setup, conn):
    w, clock = setup.w, setup.clock
    a = add_item(conn, "A", status="queued")
    setup.fail["A"] = RateLimited("rate-limit reached")
    for hits in (1, 2):
        w.tick()
        r = row(conn, a)
        assert (r["status"], r["rate_limited"]) == ("queued", hits)
        clock.t = w.backoff_until + 0.1
    w.tick()
    r = row(conn, a)
    assert r["status"] == "failed" and "rate-limited 3 times" in r["error"] and r["failed_stage"] == "fetch"


def test_rate_limited_moves_item_behind_others(setup, conn):
    w, clock = setup.w, setup.clock
    a = add_item(conn, "A", status="queued")
    b = add_item(conn, "B", status="queued")
    setup.fail["A"] = RateLimited("login required")
    w.tick()
    clock.t = w.backoff_until + 0.1
    w.tick()
    assert setup.log == [("fetch", "A"), ("fetch", "B")] and row(conn, b)["status"] == "fetched"
    assert w.backoff_level == 0


def test_ingest_rate_limited_fails_normally(setup, conn):
    w, clock = setup.w, setup.clock
    q = add_item(conn, "Q", status="queued")
    w.backoff_until = clock.t + 900
    w.backoff_level = 1
    i = add_item(conn, "I", status="queued", source="ingest")
    setup.fail["I"] = RateLimited("rate-limit reached")
    until = w.backoff_until
    assert w.tick() is True
    r = row(conn, i)
    assert r["status"] == "failed" and r["error"] == "rate-limit reached"
    assert w.backoff_until == until and row(conn, q)["status"] == "queued"
    assert setup.log == [("fetch", "I")]


def test_needs_login_requeued_when_cookies_appear(setup, conn, env):
    w = setup.w
    n1 = add_item(conn, "N1", status="needs_login", failed_stage="fetch", error=fetch.NEEDS_LOGIN_MSG)
    n2 = add_item(conn, "N2", status="needs_login", failed_stage="fetch")
    p = add_item(conn, "P", status="private")
    w.check_cookies()
    assert row(conn, n1)["status"] == "needs_login"
    env.cookies_path.parent.mkdir(parents=True, exist_ok=True)
    env.cookies_path.write_text("# cookies")
    w.check_cookies()
    assert row(conn, n1)["status"] == "queued" and row(conn, n1)["error"] is None
    assert row(conn, n2)["status"] == "queued" and row(conn, p)["status"] == "private"
    assert any("re-queued 2 items" in e["message"] for e in events(conn))
    conn.execute("UPDATE items SET status='needs_login' WHERE id=?", (n1,))
    conn.commit()
    w.check_cookies()
    assert row(conn, n1)["status"] == "needs_login"
    st = env.cookies_path.stat()
    os.utime(env.cookies_path, (st.st_atime, st.st_mtime + 10))
    w.tick()
    assert row(conn, n1)["status"] != "needs_login"


def test_retry_needs_login(client, conn):
    i = add_item(conn, "N", status="needs_login", failed_stage="fetch", error="x")
    r = client.post(f"/api/items/{i}/retry")
    assert r.status_code == 200 and r.json()["status"] == "queued"


def test_worker_records_item_events(setup, conn, monkeypatch):
    monkeypatch.setattr(worker, "in_batch_window", lambda: True)
    a = add_item(conn, "A", status="fetched", media=[{"kind": "video", "file": "0.mp4", "duration": 42.7, "audio": True}],
                 lang="en")
    add_item(conn, "B", status="fetched")
    add_item(conn, "C", status="queued")
    setup.fail.update(B=ValueError("bad frame\nsecond line"), C=Terminal("private", fetch.RESTRICTED_MSG))
    setup.w.tick()
    ev = events(conn)
    by = {(e["stage"], e["shortcode"]): e for e in ev if e["shortcode"]}
    assert by[("extract", "A")]["message"] == "extract done: 42.7 s audio, lang en"
    assert by[("extract", "A")]["duration_s"] is not None and by[("extract", "A")]["item_id"] == a
    assert by[("extract", "B")]["level"] == "error" and by[("extract", "B")]["message"] == "failed: ValueError: bad frame"
    assert by[("fetch", "C")]["level"] == "warn" and by[("fetch", "C")]["message"] == "private: restricted: needs login"
    msgs = [e["message"] for e in ev]
    assert "batch extract: 2 items" in msgs and "batch extract finished: 2 done" in msgs


def test_model_load_and_unload_events(setup, conn, monkeypatch):
    monkeypatch.setattr(worker, "in_batch_window", lambda: False)

    class Model:
        __name__ = "app.ml.whisper"
        loads, load_s, _on = 0, None, False

        def loaded(self):
            return self._on

        def load(self):
            self._on, self.loads, self.load_s = True, self.loads + 1, 1.25

        def unload(self):
            self._on = False

    m = Model()
    stage = setup.w.stages[1][2]
    stage.MODELS = [m]
    orig = stage.process
    stage.process = lambda c, item: (m.load(), orig(c, item))
    add_item(conn, "I", status="fetched", source="ingest")
    setup.w.tick()
    msgs = [(e["stage"], e["message"], e["duration_s"]) for e in events(conn) if "load" in e["message"]]
    assert msgs == [("extract", "loaded whisper in 1.2 s", 1.25), ("extract", "unloaded whisper", None)]


def test_disk_guard_events(setup, conn, monkeypatch):
    monkeypatch.setattr(fetch, "disk_free_ok", lambda: False)
    setup.w.tick()
    monkeypatch.setattr(fetch, "disk_free_ok", lambda: True)
    setup.w.tick()
    ev = events(conn)
    assert ev[0]["level"] == "warn" and "fetching paused" in ev[0]["message"]
    assert ev[1]["message"] == "disk ok: fetching resumed"


def test_backoff_restored_after_restart(conn):
    until = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 600))
    db.set_fetch_backoff(conn, until)
    clock = Clock()
    w = worker.Worker(conn, clock=clock)
    assert 590 <= w.backoff_until - clock.t <= 601
    db.set_fetch_backoff(conn, "2000-01-01T00:00:00Z")
    w = worker.Worker(conn, clock=clock)
    assert w.backoff_until is None and backoff_until(conn) is None
