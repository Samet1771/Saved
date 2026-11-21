from types import SimpleNamespace

import pytest

from app import db, worker
from app.ml import llm
from app.stages import fetch
from app.stages.fetch import Terminal

from .conftest import add_item


class FakeModel:
    def __init__(self):
        self.unloads = 0

    def unload(self):
        self.unloads += 1


class FakeStage:
    def __init__(self, name, next_status, log, fail=None):
        self.name, self.next_status, self.log, self.fail = name, next_status, log, fail
        self.model = FakeModel()
        self.MODELS = [self.model]

    def process(self, conn, item):
        self.log.append((self.name, item["shortcode"]))
        err = self.fail.get(item["shortcode"])
        if err:
            raise err
        db.update_item(conn, item["id"], status=self.next_status)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def setup(conn, monkeypatch):
    log, fail = [], {}
    stages = [(n, s, FakeStage(n, nxt, log, fail)) for n, s, nxt in (
        ("fetch", "queued", "fetched"), ("extract", "fetched", "extracted"),
        ("enrich", "extracted", "enriched"), ("index", "enriched", "indexed"))]
    clock = Clock()
    monkeypatch.setattr(fetch, "disk_free_ok", lambda: True)
    remote = {"up": True}
    monkeypatch.setattr(llm, "remote_healthy", lambda force=False: remote["up"])
    w = worker.Worker(conn, stages=stages, clock=clock, sleep=lambda s: None)
    return SimpleNamespace(w=w, log=log, fail=fail, stages={n: m for n, _, m in stages}, clock=clock, remote=remote)


def status(conn, item_id):
    return conn.execute("SELECT status, error, failed_stage FROM items WHERE id=?", (item_id,)).fetchone()


def test_ingest_runs_all_stages_and_unloads(setup, conn, monkeypatch):
    monkeypatch.setattr(worker, "in_batch_window", lambda: False)
    i = add_item(conn, "ING", status="queued", source="ingest")
    e = add_item(conn, "EXP", status="fetched")
    assert setup.w.tick() is True
    assert setup.log == [("fetch", "ING"), ("extract", "ING"), ("enrich", "ING"), ("index", "ING")]
    assert status(conn, i)["status"] == "indexed"
    assert all(m.model.unloads == 1 for m in setup.stages.values())
    assert setup.w.tick() is False
    assert status(conn, e)["status"] == "fetched"


def test_batches_only_in_window(setup, conn, monkeypatch):
    window = {"open": False}
    monkeypatch.setattr(worker, "in_batch_window", lambda: window["open"])
    a = add_item(conn, "A", status="fetched")
    b = add_item(conn, "B", status="fetched")
    c = add_item(conn, "C", status="extracted")
    setup.remote["up"] = False
    assert setup.w.tick() is False and setup.log == []
    setup.remote["up"] = True
    assert setup.w.tick() is True and setup.log == [("enrich", "C")]
    window["open"] = True
    assert setup.w.tick() is True
    assert setup.log[1:] == [("extract", "A"), ("enrich", "A"), ("extract", "B"), ("enrich", "B"),
                             ("index", "A"), ("index", "B"), ("index", "C")]
    assert {status(conn, x)["status"] for x in (a, b, c)} == {"indexed"}
    assert setup.stages["extract"].model.unloads == 1 and setup.stages["index"].model.unloads == 1


def test_fetch_throttle_and_failures(setup, conn, monkeypatch):
    monkeypatch.setattr(worker, "in_batch_window", lambda: False)
    a = add_item(conn, "A", status="queued")
    b = add_item(conn, "B", status="queued")
    c = add_item(conn, "C", status="queued")
    setup.fail.update(B=Terminal("private", "login required"), C=RuntimeError("network down"))
    assert setup.w.tick() is True and status(conn, a)["status"] == "fetched"
    assert 20 <= setup.w.next_fetch - setup.clock.t <= 40
    assert setup.w.tick() is False and len(setup.log) == 1
    setup.clock.t += 40
    setup.w.tick()
    assert tuple(status(conn, b)) == ("private", "login required", "fetch")
    setup.clock.t += 40
    setup.w.tick()
    st = status(conn, c)
    assert st["status"] == "failed" and "network down" in st["error"] and st["failed_stage"] == "fetch"


def test_ingest_skips_throttle(setup, conn, monkeypatch):
    monkeypatch.setattr(worker, "in_batch_window", lambda: False)
    add_item(conn, "A", status="queued")
    setup.w.tick()
    i1 = add_item(conn, "I1", status="queued", source="ingest")
    i2 = add_item(conn, "I2", status="queued", source="ingest")
    setup.w.tick()
    setup.w.tick()
    assert status(conn, i1)["status"] == "indexed" and status(conn, i2)["status"] == "indexed"


def test_ingest_preempts_batch(setup, conn, monkeypatch):
    monkeypatch.setattr(worker, "in_batch_window", lambda: True)
    add_item(conn, "A", status="fetched")
    add_item(conn, "B", status="fetched")
    orig = setup.stages["extract"].process

    def process(c, item):
        orig(c, item)
        if item["shortcode"] == "A":
            add_item(c, "ING", status="queued", source="ingest")
    setup.stages["extract"].process = process
    setup.w.tick()
    assert setup.log == [("extract", "A")] and setup.stages["extract"].model.unloads == 1
    setup.w.tick()
    assert setup.log[1:5] == [("fetch", "ING"), ("extract", "ING"), ("enrich", "ING"), ("index", "ING")]


def test_disk_guard_pauses_fetch(setup, conn, monkeypatch):
    monkeypatch.setattr(worker, "in_batch_window", lambda: False)
    monkeypatch.setattr(fetch, "disk_free_ok", lambda: False)
    q = add_item(conn, "Q", status="queued")
    i = add_item(conn, "I", status="queued", source="ingest")
    f = add_item(conn, "F", status="fetched", source="ingest")
    assert setup.w.tick() is True
    assert setup.log == [("extract", "F"), ("enrich", "F"), ("index", "F")]
    assert setup.w.tick() is False
    assert status(conn, q)["status"] == "queued" and status(conn, i)["status"] == "queued"
    assert conn.execute("SELECT paused FROM worker_status").fetchone()[0] == 1
    monkeypatch.setattr(fetch, "disk_free_ok", lambda: True)
    setup.w.tick()
    assert status(conn, i)["status"] == "indexed"
    assert conn.execute("SELECT paused FROM worker_status").fetchone()[0] == 0


def test_crash_recovery_gives_up_after_attempts(setup, conn, monkeypatch):
    monkeypatch.setattr(worker, "in_batch_window", lambda: True)
    a = add_item(conn, "A", status="fetched", attempts=worker.MAX_ATTEMPTS)
    b = add_item(conn, "B", status="fetched", attempts=1)
    setup.w.tick()
    st = status(conn, a)
    assert st["status"] == "failed" and st["failed_stage"] == "extract" and "3 times" in st["error"]
    assert status(conn, b)["status"] == "indexed"
    assert conn.execute("SELECT attempts FROM items WHERE id=?", (b,)).fetchone()[0] == 0


def test_run_stops_on_flag(setup, conn, monkeypatch):
    monkeypatch.setattr(worker, "in_batch_window", lambda: False)
    calls = []

    def sleep(s):
        calls.append(s)
        setup.w.stop = True
    setup.w.sleep = sleep
    setup.w.run()
    assert calls and conn.execute("SELECT stage FROM worker_status").fetchone()[0] == "idle"


def test_batch_window_parsing(env):
    from datetime import datetime
    from app.config import in_batch_window
    env.batch_window = "01:00-07:00"
    assert in_batch_window(datetime(2025, 1, 1, 3, 0)) and not in_batch_window(datetime(2025, 1, 1, 8, 0))
    env.batch_window = "23:00-02:00"
    assert in_batch_window(datetime(2025, 1, 1, 23, 30)) and in_batch_window(datetime(2025, 1, 1, 1, 0))
    assert not in_batch_window(datetime(2025, 1, 1, 12, 0))
    env.batch_window = "always"
    assert in_batch_window()


def test_classify():
    assert fetch.classify("ERROR: [Instagram] X: This account is private") == "private"
    assert fetch.classify("HttpError: '404 Not Found' for 'https://www.instagram.com/p/X/'") == "gone"
    assert fetch.classify("Connection reset by peer") is None
