import base64
import io
import json
import urllib.error

import pytest
from PIL import Image

from app import db, worker
from app.ml import llm
from app.stages import enrich, index
from app.stages.fetch import item_dir

from .conftest import add_item
from .test_worker import Clock, FakeStage

REMOTE = "http://gpu:8099"
LOCAL = f"http://127.0.0.1:{llm.LOCAL_PORT}"
RESULT = {"description": "A man cooks pasta in a small kitchen. He jokes about the mess.", "category": "Cooking",
          "tags": ["Pasta", "kitchen", "#comedy"], "summary": "A funny pasta recipe."}


class FakeResponse:
    def __init__(self, data):
        self.data = json.dumps(data).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.data


class FakeServers:
    def __init__(self):
        self.up = {REMOTE: True, LOCAL: True}
        self.fail_chat = {}
        self.calls = []
        self.result = RESULT

    def __call__(self, req, timeout=None):
        base, path = req.full_url.split("/", 3)[:3], "/" + req.full_url.split("/", 3)[3]
        base = "/".join(base)
        body = json.loads(req.data) if req.data else None
        self.calls.append({"base": base, "path": path, "method": req.get_method(), "timeout": timeout,
                           "auth": req.get_header("Authorization"), "body": body})
        if not self.up.get(base):
            raise urllib.error.URLError("connection refused")
        if path == "/health":
            return FakeResponse({"status": "ok"})
        if path == "/v1/models":
            return FakeResponse({"object": "list", "data": [{"id": "C:\\llm\\models\\Qwen3.5-4B-Q4_K_M.gguf"}]})
        if base in self.fail_chat:
            raise self.fail_chat[base]
        return FakeResponse({"choices": [{"message": {"content": json.dumps(self.result)}}]})

    def chats(self, base=None):
        return [c for c in self.calls if c["path"] == "/v1/chat/completions" and (base is None or c["base"] == base)]

    def count(self, base, path):
        return sum(1 for c in self.calls if c["base"] == base and c["path"] == path)


class FakePopen:
    instances = []

    def __init__(self, cmd, **_kw):
        self.cmd, self.terminated, self.killed, self.returncode = cmd, False, False, None
        FakePopen.instances.append(self)

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def kill(self):
        self.killed = True


@pytest.fixture
def servers(env, monkeypatch):
    s = FakeServers()
    FakePopen.instances = []
    clock = Clock()
    monkeypatch.setattr(llm.urllib.request, "urlopen", s)
    monkeypatch.setattr(llm.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(llm, "clock", clock)
    monkeypatch.setattr(env, "llm_remote_url", REMOTE)
    monkeypatch.setattr(env, "llm_remote_key", "secret")
    env.models_dir.mkdir(parents=True, exist_ok=True)
    (env.models_dir / "Qwen3.5-4B-Q4_K_M.gguf").write_bytes(b"x")
    (env.models_dir / "qwen35-mmproj-F16.gguf").write_bytes(b"x")
    llm._remote.update(healthy=False, checked_at=None, at=None, model=None)
    s.clock = clock
    yield s
    llm.unload()
    llm._remote.update(healthy=False, checked_at=None, at=None, model=None)


def reel(conn, shortcode, frames=3, **fields):
    d = item_dir(shortcode)
    d.mkdir(parents=True, exist_ok=True)
    (d / "0.mp4").write_bytes(b"video")
    (d / "frames.txt").write_text(str(frames))
    base = {"status": "extracted", "author": "chef", "caption": "c" * 900, "lang": "en",
            "transcript": [{"start": 0.0, "end": 2.0, "text": "hello pasta"}], "transcript_text": "hello pasta",
            "ocr_text": "SALE", "ocr_lines": [[1.5, "SALE"]],
            "media": [{"kind": "video", "file": "0.mp4", "duration": frames / 2, "audio": True}], "keyframes": []}
    return add_item(conn, shortcode, **{**base, **fields})


@pytest.fixture(autouse=True)
def fake_frames(monkeypatch):
    def sample(video, dst):
        n = int((video.parent / "frames.txt").read_text())
        out = []
        for i in range(n):
            f = dst / f"{i:05d}.jpg"
            Image.new("RGB", (900, 1600), (i * 20 % 255, 80, 120)).save(f)
            out.append((i / 2, f))
        return out
    monkeypatch.setattr(enrich, "sample_frames", sample)


@pytest.fixture
def fallback(env, monkeypatch):
    monkeypatch.setattr(env, "llm_local_fallback", True)


def row(conn, i):
    return conn.execute("SELECT * FROM items WHERE id=?", (i,)).fetchone()


def make_worker(conn, monkeypatch, log):
    monkeypatch.setattr(worker.fetch, "disk_free_ok", lambda: True)
    monkeypatch.setattr(worker, "in_batch_window", lambda: False)
    stages = [("fetch", "queued", FakeStage("fetch", "fetched", log, {})),
              ("extract", "fetched", FakeStage("extract", "extracted", log, {})),
              ("enrich", "extracted", enrich),
              ("index", "enriched", FakeStage("index", "indexed", log, {}))]
    return worker.Worker(conn, stages=stages, clock=Clock(), sleep=lambda s: None)


def events(conn):
    return [r["message"] for r in conn.execute("SELECT message FROM events ORDER BY id")]


def test_health_cached_and_model_name(servers):
    assert llm.remote_healthy() is True
    assert llm.remote_healthy() is True
    assert servers.count(REMOTE, "/health") == 1 and servers.count(REMOTE, "/v1/models") == 1
    assert servers.calls[0]["timeout"] == 2 and servers.calls[0]["auth"] == "Bearer secret"
    st = llm.remote_state()
    assert st["url"] == REMOTE and st["healthy"] and st["model"] == "Qwen3.5-4B-Q4_K_M.gguf" and st["checked_at"]
    servers.up[REMOTE] = False
    servers.clock.t += 29
    assert llm.remote_healthy() is True
    servers.clock.t += 2
    assert llm.remote_healthy() is False and servers.count(REMOTE, "/health") == 2
    servers.up[REMOTE] = True
    assert llm.remote_healthy() is False
    assert llm.remote_healthy(force=True) is True


def test_remote_disabled_without_url(servers, env, monkeypatch):
    monkeypatch.setattr(env, "llm_remote_url", "")
    assert llm.remote_healthy() is False and servers.calls == []
    assert llm.remote_state()["url"] is None


def test_remote_request_body_and_result(servers, conn):
    i = reel(conn, "R1", frames=8)
    enrich.process(conn, row(conn, i))
    (chat,) = servers.chats()
    assert chat["base"] == REMOTE and chat["timeout"] == 300 and chat["auth"] == "Bearer secret"
    body = chat["body"]
    assert body["temperature"] == 0.2 and body["max_tokens"] == 3000
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    rf = body["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["name"] == "reel"
    assert set(rf["json_schema"]["schema"]["required"]) == {"timeline", "visual", "description", "category",
                                                             "tags", "summary"}
    content = body["messages"][1]["content"]
    pics = [p for p in content if p["type"] == "image_url"]
    assert len(pics) == 8
    assert [p["text"] for p in content[:4:2]] == ["t=0.0s", "t=0.5s"]
    url = pics[0]["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")
    im = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
    assert im.format == "JPEG" and im.size == (336, 597)
    text = content[-1]["text"]
    assert text.startswith("Video length: 4.0 s, 8 frames.")
    assert "Categories: cooking," in text and "[0.0-2.0] hello pasta" in text and "[1.5] SALE" in text
    assert "chef" not in text and "c" * 10 not in text
    r = row(conn, i)
    assert r["status"] == "enriched" and r["enriched_by"] == "remote:Qwen3.5-4B-Q4_K_M.gguf"
    assert r["description"] == RESULT["description"] and r["category"] == "cooking"
    assert json.loads(r["tags"]) == ["pasta", "kitchen", "comedy"]
    assert FakePopen.instances == []


def test_long_video_frames_are_thinned(servers, conn, env, monkeypatch):
    monkeypatch.setattr(env, "vlm_max_frames", 10)
    i = reel(conn, "LONG", frames=25)
    enrich.process(conn, row(conn, i))
    content = servers.chats()[0]["body"]["messages"][1]["content"]
    labels = [p["text"] for p in content if p["type"] == "text"][:-1]
    assert labels == [f"t={t:.1f}s" for t in (0, 1.5, 3, 4.5, 6, 7.5, 9, 10.5, 12)]


def test_timeline_and_visual_saved_and_indexed(servers, conn):
    servers.result = {**RESULT, "timeline": [{"start": 0, "end": 2, "event": "He drains pasta."}],
                      "visual": {"setting": "small kitchen", "people": ["a man"], "objects": ["pot"],
                                 "on_screen_text": ["SALE"], "format": "vlog", "audio": "speech"}}
    i = reel(conn, "TL")
    enrich.process(conn, row(conn, i))
    r = row(conn, i)
    assert json.loads(r["details"])["timeline"][0]["event"] == "He drains pasta."
    doc = index.compose(r)
    assert "Timeline: [0-2s] He drains pasta." in doc and "setting: small kitchen" in doc
    assert "Transcript: hello pasta" in doc and "On-screen text (OCR): SALE" in doc


def test_photo_post_sends_photos(servers, conn):
    d = item_dir("P1")
    d.mkdir(parents=True)
    for n in ("0.jpg", "1.jpg"):
        Image.new("RGB", (300, 300)).save(d / n)
    i = add_item(conn, "P1", type="carousel", status="extracted",
                 media=[{"kind": "image", "file": "0.jpg"}, {"kind": "image", "file": "1.jpg"}])
    enrich.process(conn, row(conn, i))
    pics = [p for p in servers.chats()[0]["body"]["messages"][1]["content"] if p["type"] == "image_url"]
    assert len(pics) == 2
    im = Image.open(io.BytesIO(base64.b64decode(pics[0]["image_url"]["url"].split(",", 1)[1])))
    assert im.size == (300, 300)


def test_remote_down_everything_waits_by_default(servers, conn, monkeypatch):
    w = make_worker(conn, monkeypatch, [])
    servers.up[REMOTE] = False
    e, i = reel(conn, "EXP"), reel(conn, "ING", source="ingest")
    assert w.tick() is False
    assert row(conn, e)["status"] == row(conn, i)["status"] == "extracted"
    assert FakePopen.instances == [] and servers.chats() == []
    servers.up[REMOTE] = True
    servers.clock.t += 31
    w.tick()
    assert row(conn, i)["status"] == "indexed" and row(conn, i)["enriched_by"].startswith("remote:")


def test_routing_remote_down_export_waits_ingest_uses_local(servers, conn, monkeypatch, fallback):
    log = []
    w = make_worker(conn, monkeypatch, log)
    servers.up[REMOTE] = False
    e = reel(conn, "EXP")
    i = reel(conn, "ING", source="ingest")
    assert w.tick() is True
    assert row(conn, e)["status"] == "extracted" and row(conn, e)["attempts"] == 0
    assert row(conn, i)["status"] == "indexed" and row(conn, i)["enriched_by"] == "local:Qwen3.5-4B-Q4_K_M.gguf"
    (proc,) = FakePopen.instances
    assert proc.cmd[0] == "/opt/llama/llama-server" and proc.terminated and not llm.loaded()
    cmd = " ".join(proc.cmd)
    assert "--mmproj" in cmd and "--port 8091" in cmd and "--host 127.0.0.1" in cmd and "--no-webui" in cmd
    (chat,) = servers.chats()
    assert chat["base"] == LOCAL and chat["timeout"] == 600 and chat["auth"] is None
    assert sum(p["type"] == "image_url" for p in chat["body"]["messages"][1]["content"]) == 3
    msgs = events(conn)
    assert any(m.startswith("remote GPU unreachable: http://gpu:8099") for m in msgs)
    assert any(m.startswith("loaded local llama-server (Qwen3.5-4B-Q4_K_M.gguf) in") for m in msgs)
    assert "unloaded local llama-server (Qwen3.5-4B-Q4_K_M.gguf)" in msgs

    assert w.tick() is False and row(conn, e)["status"] == "extracted" and len(FakePopen.instances) == 1
    servers.up[REMOTE] = True
    servers.clock.t += 31
    assert w.tick() is True
    assert row(conn, e)["status"] == "enriched" and row(conn, e)["enriched_by"].startswith("remote:")
    assert "remote GPU up: http://gpu:8099 serving Qwen3.5-4B-Q4_K_M.gguf" in events(conn)
    assert len(FakePopen.instances) == 1


def test_several_ingest_items_share_one_local_server(servers, conn, monkeypatch, fallback):
    w = make_worker(conn, monkeypatch, [])
    servers.up[REMOTE] = False
    ids = [reel(conn, f"I{n}", source="ingest") for n in range(3)]
    w.tick()
    assert all(row(conn, i)["status"] == "indexed" for i in ids)
    assert len(FakePopen.instances) == 1 and FakePopen.instances[0].terminated


def test_remote_healthy_ingest_never_starts_local(servers, conn, monkeypatch):
    w = make_worker(conn, monkeypatch, [])
    i = reel(conn, "ING", source="ingest")
    w.tick()
    assert row(conn, i)["enriched_by"] == "remote:Qwen3.5-4B-Q4_K_M.gguf" and FakePopen.instances == []


def test_remote_failure_mid_batch(servers, conn, monkeypatch, fallback):
    w = make_worker(conn, monkeypatch, [])
    a, b = reel(conn, "A"), reel(conn, "B")
    servers.fail_chat[REMOTE] = urllib.error.HTTPError(REMOTE, 503, "loading", {}, None)
    assert w.tick() is True
    for x in (a, b):
        assert (row(conn, x)["status"], row(conn, x)["error"], row(conn, x)["attempts"]) == ("extracted", None, 0)
    assert len(servers.chats(REMOTE)) == 1 and not llm.remote_state()["healthy"]
    assert w.tick() is False and len(servers.chats(REMOTE)) == 1

    servers.clock.t += 31
    i = reel(conn, "ING", source="ingest")
    w.tick()
    assert row(conn, i)["status"] == "indexed" and row(conn, i)["enriched_by"].startswith("local:")
    assert len(servers.chats(REMOTE)) == 2 and len(servers.chats(LOCAL)) == 1
    assert row(conn, a)["status"] == "extracted"

    servers.fail_chat.clear()
    servers.clock.t += 31
    w.tick()
    assert row(conn, a)["status"] == "enriched" and row(conn, b)["status"] == "enriched"


def test_remote_timeout_defers_export_item(servers, conn, monkeypatch):
    w = make_worker(conn, monkeypatch, [])
    a = reel(conn, "A")
    servers.fail_chat[REMOTE] = TimeoutError("timed out")
    w.tick()
    r = row(conn, a)
    assert (r["status"], r["error"], r["attempts"]) == ("extracted", None, 0)
    assert conn.execute("SELECT llm_remote_healthy FROM worker_status").fetchone()[0] == 0


def test_bad_model_output_fails_item_normally(servers, conn, monkeypatch):
    w = make_worker(conn, monkeypatch, [])
    a = reel(conn, "A")
    servers.result = "not json"
    w.tick()
    assert row(conn, a)["status"] == "failed" and row(conn, a)["failed_stage"] == "enrich"


def test_mmproj_missing_runs_text_only(servers, conn, env, monkeypatch, fallback):
    (env.models_dir / "qwen35-mmproj-F16.gguf").unlink()
    w = make_worker(conn, monkeypatch, [])
    servers.up[REMOTE] = False
    i = reel(conn, "ING", source="ingest")
    w.tick()
    assert row(conn, i)["status"] == "indexed"
    assert "--mmproj" not in FakePopen.instances[0].cmd
    content = servers.chats(LOCAL)[0]["body"]["messages"][1]["content"]
    assert [p["type"] for p in content] == ["text"]
    assert any("text-only: mmproj missing" in m for m in events(conn) if m.startswith("loaded"))


def test_local_server_restarts_when_active_model_changes(servers, conn, env):
    (env.models_dir / "b.gguf").write_bytes(b"x")
    llm.load()
    first = FakePopen.instances[0]
    llm.load()
    assert len(FakePopen.instances) == 1 and llm.loaded_file() == "Qwen3.5-4B-Q4_K_M.gguf"
    db.set_setting(conn, "llm_model", "b.gguf")
    llm.load()
    assert first.terminated and len(FakePopen.instances) == 2 and llm.loaded_file() == "b.gguf"
    assert FakePopen.instances[1].cmd[2].endswith("b.gguf")


def test_local_server_dies_while_loading(servers, conn, monkeypatch):
    servers.up[LOCAL] = False

    class Dying(FakePopen):
        def poll(self):
            return 1
    monkeypatch.setattr(llm.subprocess, "Popen", Dying)
    with pytest.raises(RuntimeError, match="exited with code 1"):
        llm.load()
    assert not llm.loaded()


def test_status_fields(servers, conn, client, monkeypatch):
    w = make_worker(conn, monkeypatch, [])
    s = client.get("/api/status").json()["worker"]
    assert s["llm_backend"] is None and s["llm_remote"]["healthy"] is False
    w.tick()
    s = client.get("/api/status").json()["worker"]
    assert s["llm_backend"] == "remote"
    assert s["llm_remote"]["url"] == REMOTE and s["llm_remote"]["healthy"] is True and s["llm_remote"]["checked_at"]
    assert s["llm_remote"]["model"] == "Qwen3.5-4B-Q4_K_M.gguf"
    servers.up[REMOTE] = False
    servers.clock.t += 31
    w.tick()
    s = client.get("/api/status").json()["worker"]
    assert s["llm_backend"] is None and s["llm_remote"]["healthy"] is False


def test_description_in_detail_fts_and_embedding_doc(client, conn):
    i = add_item(conn, "D1", caption="", summary="A summary.", description="A golden retriever surfs a wave.")
    d = client.get(f"/api/items/{i}").json()
    assert d["description"] == "A golden retriever surfs a wave."
    assert "description" not in client.get("/api/items").json()["items"][0]
    assert [x["id"] for x in client.get("/api/items", params={"q": "retriever"}).json()["items"]] == [i]
    db.update_item(conn, i, description="A cat plays piano.")
    assert client.get("/api/items", params={"q": "retriever"}).json()["total"] == 0
    assert client.get("/api/items", params={"q": "piano"}).json()["total"] == 1
    doc = index.compose(row(conn, i))
    assert doc.startswith("Summary: A summary.\nDescription: A cat plays piano.")


def test_migration_from_v3_rebuilds_fts(env, tmp_path):
    path = tmp_path / "old.db"
    conn = db.connect(path)
    for i, sql in enumerate(db.MIGRATIONS[:3], start=1):
        conn.executescript("BEGIN;" + sql + f";PRAGMA user_version={i};COMMIT;")
    add_item(conn, "OLD", caption="vintage motorbike restoration")
    db.init(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)
    assert conn.execute("SELECT description FROM items").fetchone()[0] is None
    hits = conn.execute("SELECT rowid FROM items_fts WHERE items_fts MATCH 'motorbike'").fetchall()
    assert len(hits) == 1
    conn.close()
