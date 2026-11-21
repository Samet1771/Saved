import threading
import time
from collections import namedtuple

import pytest

from app import api, db
from app.ml import llm
from app.stages import enrich

from .conftest import add_item
from .test_llm import servers  # noqa: F401

DEFAULT = "Qwen3.5-4B-Q4_K_M.gguf"


@pytest.fixture(autouse=True)
def reset_download():
    api._download.update(state="idle", repo=None, file=None, bytes=0, total=None, error=None)
    yield
    llm.unload()


def model(env, name, size=10):
    env.models_dir.mkdir(parents=True, exist_ok=True)
    (env.models_dir / name).write_bytes(b"x" * size)


def test_active_model_resolution_and_fallback(env, conn, monkeypatch):
    assert llm.active_file(conn) == DEFAULT
    db.set_setting(conn, "llm_model", "other.gguf")
    assert llm.active_file(conn) == DEFAULT
    model(env, "other.gguf")
    assert llm.active_file(conn) == "other.gguf"
    assert llm.model_path() == str(env.models_dir / "other.gguf")
    (env.models_dir / "other.gguf").unlink()
    monkeypatch.setattr(env, "llm_model_path", "/elsewhere/custom.gguf")
    assert llm.active_file(conn) == "custom.gguf" and llm.default_file() == "custom.gguf"


def test_list_put_and_delete(client, env):
    for name in ("a.gguf", "b.gguf", "mmproj-F16.gguf", "qwen35-mmproj-F16.gguf", "c.gguf.part", "notes.txt"):
        model(env, name, 5)
    r = client.get("/api/models").json()
    assert r["active"] == DEFAULT and r["default"] == DEFAULT and r["download"]["state"] == "idle"
    assert r["models"] == [{"file": "a.gguf", "size_bytes": 5}, {"file": "b.gguf", "size_bytes": 5}]
    assert client.put("/api/models/active", json={"file": "missing.gguf"}).status_code == 404
    for bad in ("../a.gguf", "notes.txt", "a/b.gguf", ""):
        assert client.put("/api/models/active", json={"file": bad}).status_code == 422
    r = client.put("/api/models/active", json={"file": "b.gguf"})
    assert r.status_code == 200 and r.json()["active"] == "b.gguf"
    assert client.get("/api/status").json()["worker"]["llm_model"] == "b.gguf"
    r = client.delete("/api/models/b.gguf")
    assert r.status_code == 409 and isinstance(r.json()["detail"], str)
    assert client.delete("/api/models/missing.gguf").status_code == 404
    assert client.delete("/api/models/notes.txt").status_code == 404
    assert client.delete("/api/models/a.gguf").status_code == 204
    assert not (env.models_dir / "a.gguf").exists()


class FakeResponse:
    def __init__(self, headers=None, chunks=(), gate=None):
        self.headers, self.chunks, self.gate = headers or {}, list(chunks), gate

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, _n):
        if not self.chunks:
            return b""
        if len(self.chunks) == 1 and self.gate:
            self.gate.wait(5)
        return self.chunks.pop(0)


def fake_hf(monkeypatch, size=30, chunks=(b"a" * 10, b"b" * 10, b"c" * 10), gate=None, fail=None):
    calls = []

    def urlopen(req, timeout=None):
        calls.append((req.get_method(), req.full_url))
        if req.get_method() == "HEAD":
            return FakeResponse({"Content-Length": str(size)})
        if fail:
            raise fail
        return FakeResponse(chunks=chunks, gate=gate)

    monkeypatch.setattr(api, "urlopen", urlopen)
    return calls


def wait_state(client, *states):
    for _ in range(500):
        d = client.get("/api/models").json()["download"]
        if d["state"] in states:
            return d
        time.sleep(0.01)
    raise AssertionError(d)


def test_download_validation(client, env, monkeypatch):
    calls = fake_hf(monkeypatch)
    for repo, file in (("noslash", "x.gguf"), ("a/b/c", "x.gguf"), ("a b/c", "x.gguf"), ("../x", "x.gguf"),
                       ("o/r", "x.bin"), ("o/r", "../x.gguf"), ("o/r", "sub//x.gguf"), ("o/r", "/x.gguf"),
                       ("o/r", "sub/../x.gguf")):
        r = client.post("/api/models/download", json={"repo": repo, "file": file})
        assert r.status_code == 422, (repo, file)
    model(env, "x.gguf")
    assert client.post("/api/models/download", json={"repo": "o/r", "file": "sub/x.gguf"}).status_code == 409
    assert calls == []


def test_download_streams_to_part_then_renames(client, env, monkeypatch):
    gate = threading.Event()
    calls = fake_hf(monkeypatch, gate=gate)
    r = client.post("/api/models/download", json={"repo": "unsloth/Qwen-GGUF", "file": "q4/m.gguf"})
    assert r.status_code == 202
    assert r.json()["download"] | {"bytes": 0} == {"state": "downloading", "repo": "unsloth/Qwen-GGUF", "file": "m.gguf",
                                                    "bytes": 0, "total": 30, "error": None}
    assert calls[0] == ("HEAD", "https://huggingface.co/unsloth/Qwen-GGUF/resolve/main/q4/m.gguf")
    for _ in range(500):
        if client.get("/api/models").json()["download"]["bytes"] == 20:
            break
        time.sleep(0.01)
    assert (env.models_dir / "m.gguf.part").exists() and not (env.models_dir / "m.gguf").exists()
    assert client.get("/api/models").json()["models"] == []
    r = client.post("/api/models/download", json={"repo": "o/r", "file": "other.gguf"})
    assert r.status_code == 409
    gate.set()
    d = wait_state(client, "done", "error")
    assert d["state"] == "done" and d["bytes"] == 30 and d["error"] is None
    assert (env.models_dir / "m.gguf").read_bytes() == b"a" * 10 + b"b" * 10 + b"c" * 10
    assert not (env.models_dir / "m.gguf.part").exists()
    assert client.get("/api/models").json()["models"] == [{"file": "m.gguf", "size_bytes": 30}]


def test_download_error_removes_part(client, env, monkeypatch):
    fake_hf(monkeypatch, fail=OSError("connection reset"))
    assert client.post("/api/models/download", json={"repo": "o/r", "file": "m.gguf"}).status_code == 202
    d = wait_state(client, "done", "error")
    assert d["state"] == "error" and "connection reset" in d["error"]
    assert list(env.models_dir.iterdir()) == []


def test_download_insufficient_disk(client, env, monkeypatch):
    calls = fake_hf(monkeypatch, size=10 * 1024 ** 3)
    du = namedtuple("du", "total used free")
    monkeypatch.setattr(api.shutil, "disk_usage", lambda _p: du(100 * 1024 ** 3, 0, 12 * 1024 ** 3))
    monkeypatch.setattr(env, "min_free_gb", 5.0)
    r = client.post("/api/models/download", json={"repo": "o/r", "file": "m.gguf"})
    assert r.status_code == 507 and isinstance(r.json()["detail"], str)
    assert [c[0] for c in calls] == ["HEAD"] and client.get("/api/models").json()["download"]["state"] == "idle"


def test_reenrich_counts_and_manual_fields_survive(client, conn, env, servers):  # noqa: F811
    model(env, "b.gguf")
    client.put("/api/models/active", json={"file": "b.gguf"})
    conn.execute("UPDATE worker_status SET llm_remote_model='Qwen3.5-4B-Q4_K_M.gguf'")
    conn.commit()
    a = add_item(conn, "A", status="indexed", enriched_by="b.gguf", category="cooking", tags=["pasta"], summary="old")
    add_item(conn, "B", status="enriched", enriched_by="local:b.gguf")
    add_item(conn, "G", status="indexed", enriched_by="remote:Qwen3.5-4B-Q4_K_M.gguf")
    add_item(conn, "N", status="indexed")
    add_item(conn, "Q", status="queued")
    add_item(conn, "X", status="extracted")
    m = add_item(conn, "M", status="indexed", enriched_by="remote:old.gguf", category="cooking",
                 tags=["pasta", "recipe"], manual_category=1, manual_tags=1, source="ingest")
    assert client.get("/api/models").json()["other_model_items"] == 3
    assert client.post("/api/items/reenrich", json={}).json() == {"queued": 3}
    st = {r[0]: r[1] for r in conn.execute("SELECT shortcode, status FROM items")}
    assert st == {"A": "extracted", "B": "enriched", "G": "indexed", "N": "extracted", "Q": "queued",
                  "X": "extracted", "M": "extracted"}
    d = client.get(f"/api/items/{a}").json()
    assert (d["category"], d["tags"], d["summary"], d["enriched_by"]) == ("cooking", ["pasta"], "old", "b.gguf")
    assert conn.execute("SELECT source FROM items WHERE id=?", (m,)).fetchone()[0] == "export"

    enrich.process(conn, conn.execute("SELECT * FROM items WHERE id=?", (m,)).fetchone())
    enrich.process(conn, conn.execute("SELECT * FROM items WHERE id=?", (a,)).fetchone())
    d = client.get(f"/api/items/{m}").json()
    assert (d["category"], d["tags"], d["summary"], d["status"], d["enriched_by"]) == (
        "cooking", ["pasta", "recipe"], "A funny pasta recipe.", "enriched", "remote:Qwen3.5-4B-Q4_K_M.gguf")
    assert d["manual"] == {"category": True, "tags": True} and d["description"].startswith("A man cooks")
    d = client.get(f"/api/items/{a}").json()
    assert (d["category"], d["tags"], d["enriched_by"]) == ("cooking", ["pasta", "kitchen", "comedy"], "remote:Qwen3.5-4B-Q4_K_M.gguf")

    assert client.post("/api/items/reenrich", json={"only_other_models": True}).json() == {"queued": 0}
    assert client.post("/api/items/reenrich", json={"only_other_models": False}).json() == {"queued": 4}
    assert client.post("/api/items/reenrich").json() == {"queued": 0}
