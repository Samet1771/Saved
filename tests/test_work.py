import json

import pytest

from app import config, db, worker
from app.ml import llm
from app.stages import fetch
from app.stages.fetch import NetworkDown

from .conftest import add_item
from .test_worker import setup, status  # noqa: F401


@pytest.fixture
def hybrid(env, monkeypatch):
    monkeypatch.setenv("COMPUTE_MODE", "hybrid")
    s = config.reload()
    yield s


def media(kind="video"):
    return [{"kind": kind, "file": "0.mp4" if kind == "video" else "0.jpg", "duration": 9.0, "audio": True}]


def claim(client, stage, limit=5):
    r = client.post("/api/work/claim", json={"agent": "pc", "stage": stage, "limit": limit, "info": {"gpu": "test"}})
    assert r.status_code == 200, r.text
    return r.json()


def test_claim_needs_hybrid_mode(client):
    assert client.post("/api/work/claim", json={"agent": "pc", "stage": "extract"}).status_code == 409


def test_full_hybrid_round_trip(hybrid, client, conn):
    i = add_item(conn, "HYB", status="fetched", media=media(), source="ingest")
    add_item(conn, "OLD", status="fetched", media=media())
    got = claim(client, "extract", 1)
    assert [x["shortcode"] for x in got["items"]] == ["HYB"]
    assert got["items"][0]["media"][0]["file"] == "0.mp4"
    assert [x["shortcode"] for x in claim(client, "extract")["items"]] == ["OLD"]
    assert claim(client, "extract")["items"] == []

    fields = {"transcript": [{"start": 0, "end": 1, "text": "hello"}], "transcript_text": "hello", "lang": "en",
              "ocr_text": "SALE", "ocr_lines": [[0.5, "SALE"]], "keyframes": ["kf0_0.jpg"]}
    r = client.post(f"/api/work/{i}/extract", data={"fields": json.dumps(fields), "duration_s": "2.5"},
                    files=[("files", ("kf0_0.jpg", b"\xff\xd8jpeg", "image/jpeg"))])
    assert r.status_code == 200, r.text
    assert (hybrid.media_dir / "HYB" / "kf0_0.jpg").read_bytes() == b"\xff\xd8jpeg"
    assert client.post(f"/api/work/{i}/extract", data={"fields": "{}"}).status_code == 409

    got = claim(client, "enrich")
    assert got["items"][0]["transcript_text"] == "hello" and "humor" in got["categories"]
    r = client.post(f"/api/work/{i}/enrich", json={"result": {"category": "Humor", "tags": ["#Fun"], "summary": "s"},
                                                    "model": "pc:qwen", "duration_s": 9})
    assert r.status_code == 200
    row = conn.execute("SELECT category, tags, enriched_by FROM items WHERE id=?", (i,)).fetchone()
    assert (row[0], json.loads(row[1]), row[2]) == ("humor", ["fun"], "pc:qwen")

    claim(client, "index")
    assert client.post(f"/api/work/{i}/index", json={"text_vec": [0.1] * 3}).status_code == 422
    r = client.post(f"/api/work/{i}/index", json={"text_vec": [0.1] * db.TEXT_DIM, "visual": [[0.2] * db.VISUAL_DIM]})
    assert r.status_code == 200
    assert status(conn, i)["status"] == "indexed"
    if db.has_vec(conn):
        assert conn.execute("SELECT COUNT(*) FROM visual_vec WHERE item_id=?", (i,)).fetchone()[0] == 1
    msgs = [r[0] for r in conn.execute("SELECT message FROM events WHERE item_id=? ORDER BY id", (i,))]
    assert msgs[0].startswith("extract done") and msgs[-1].endswith("(PC)")

    a = client.get("/api/status").json()["compute"]
    assert a["mode"] == "hybrid" and a["agent"]["online"] and a["agent"]["info"] == {"gpu": "test"}


def test_keyframes_and_fields_are_checked(hybrid, client, conn):
    i = add_item(conn, "BAD", status="fetched", media=media())
    claim(client, "extract")
    bad = client.post(f"/api/work/{i}/extract", data={"fields": json.dumps({"keyframes": ["../x.jpg"]})},
                      files=[("files", ("../x.jpg", b"x", "image/jpeg"))])
    assert bad.status_code == 422
    assert client.post(f"/api/work/{i}/extract", data={"fields": json.dumps({"status": "indexed"})}).status_code == 422
    assert status(conn, i)["status"] == "fetched"


def test_fail_release_and_pause(hybrid, client, conn):
    i = add_item(conn, "F1", status="extracted", media=media())
    j = add_item(conn, "F2", status="extracted", media=media())
    claim(client, "enrich")
    r = client.post(f"/api/work/{i}/fail", json={"stage": "enrich", "error": "CUDA out of memory\ntrace"})
    assert r.status_code == 200
    assert tuple(status(conn, i)) == ("failed", "PC: CUDA out of memory\ntrace", "enrich")
    assert claim(client, "enrich")["items"] == []
    client.post("/api/work/release", json={"ids": [j]})
    assert [x["id"] for x in claim(client, "enrich")["items"]] == [j]
    client.post("/api/work/release", json={"ids": [j]})
    client.put("/api/worker", json={"paused": True})
    assert claim(client, "enrich") == {"items": [], "paused": True}


def test_hybrid_worker_only_fetches(hybrid, conn, monkeypatch):
    monkeypatch.setattr(fetch, "disk_free_ok", lambda: True)
    monkeypatch.setattr(worker, "in_batch_window", lambda: True)
    w = worker.Worker(conn, sleep=lambda s: None)
    assert [s[0] for s in w.stages] == ["fetch"]
    add_item(conn, "WAIT", status="fetched", source="ingest", media=media())
    assert w.tick() is False


def test_network_down_waits_instead_of_failing(setup, conn, monkeypatch):  # noqa: F811
    monkeypatch.setattr(worker, "in_batch_window", lambda: False)
    i = add_item(conn, "NET", status="queued", source="ingest")
    setup.fail["NET"] = NetworkDown("Failed to resolve 'www.instagram.com'")
    setup.w.tick()
    assert status(conn, i)["status"] == "queued"
    n = len(setup.log)
    setup.w.tick()
    assert len(setup.log) == n
    setup.clock.t += worker.NETWORK_RETRY_S + 1
    del setup.fail["NET"]
    setup.w.tick()
    assert status(conn, i)["status"] == "indexed"
    msgs = [r[0] for r in conn.execute("SELECT message FROM events ORDER BY id")]
    assert any(m.startswith("network down") for m in msgs) and "network is back" in msgs


def test_dns_error_is_network_down():
    assert fetch.NETWORK_DOWN.search("HTTPSConnection(host='www.instagram.com', port=443): Failed to resolve "
                                     "'www.instagram.com' ([Errno -3] Temporary failure in name resolution)")
    assert not fetch.NETWORK_DOWN.search("HTTP Error 404: Not Found")


def test_parallel_fetch_slots(setup, conn, monkeypatch):  # noqa: F811
    import threading

    from app.stages.fetch import RateLimited
    monkeypatch.setenv("FETCH_SLOTS", "3")
    config.reload()
    monkeypatch.setattr(worker, "in_batch_window", lambda: False)
    stages = [(n, s, m) for n, s, m in setup.w.stages]
    w = worker.Worker(conn, stages=stages, clock=setup.clock, sleep=lambda s: None)
    ids = [add_item(conn, f"P{i}", status="queued") for i in range(5)]
    barrier = threading.Barrier(3, timeout=5)
    fetch_stage = setup.stages["fetch"]
    orig = fetch_stage.process

    def concurrent(c, item):
        barrier.wait()
        return orig(c, item)
    monkeypatch.setattr(fetch_stage, "process", concurrent)
    assert w.maybe_fetch() is True
    assert len(w.inflight) == 3
    w.collect_fetches(wait=True)
    assert [status(conn, i)["status"] for i in ids] == ["fetched"] * 3 + ["queued"] * 2
    monkeypatch.setattr(fetch_stage, "process", orig)
    assert w.maybe_fetch() is False
    setup.clock.t += 41
    setup.fail.update({"P3": RateLimited("429 Too Many Requests"), "P4": RateLimited("429 Too Many Requests")})
    w.maybe_fetch()
    w.collect_fetches(wait=True)
    assert [status(conn, i)["status"] for i in ids[3:]] == ["queued", "queued"]
    assert w.backoff_level == 1
    assert w.maybe_fetch() is False
    w.pool.shutdown()
