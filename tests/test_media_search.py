import pytest

from app import db, search

from .conftest import TOKEN, add_item


@pytest.fixture
def media_file(env):
    d = env.media_dir / "M1"
    d.mkdir(parents=True)
    data = bytes(range(256)) * 40
    (d / "0.mp4").write_bytes(data)
    return data


def test_media_range(client, media_file):
    size = len(media_file)
    r = client.get("/media/M1/0.mp4", headers={"Range": "bytes=100-199"})
    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes 100-199/{size}"
    assert r.headers["content-length"] == "100" and r.headers["accept-ranges"] == "bytes"
    assert r.headers["content-type"] == "video/mp4" and r.content == media_file[100:200]
    r = client.get("/media/M1/0.mp4", headers={"Range": "bytes=10000-"})
    assert r.status_code == 206 and r.content == media_file[10000:]
    r = client.get("/media/M1/0.mp4", headers={"Range": "bytes=-5"})
    assert r.content == media_file[-5:] and r.headers["content-range"] == f"bytes {size - 5}-{size - 1}/{size}"
    r = client.get("/media/M1/0.mp4", headers={"Range": f"bytes={size}-"})
    assert r.status_code == 416 and r.headers["content-range"] == f"bytes */{size}"
    r = client.get("/media/M1/0.mp4")
    assert r.status_code == 200 and r.content == media_file


def test_media_auth(client, media_file):
    assert client.get("/media/M1/0.mp4", headers={"Authorization": ""}).status_code == 401
    r = client.get(f"/media/M1/0.mp4?t={TOKEN}", headers={"Authorization": "", "Range": "bytes=0-9"})
    assert r.status_code == 206 and r.content == media_file[:10]
    assert client.get("/media/M1/0.mp4?t=nope", headers={"Authorization": ""}).status_code == 401
    assert client.get("/media/M1/missing.mp4").status_code == 404
    assert client.get("/media/M1/..%2Fx").status_code == 404


def test_rrf():
    fused = search.rrf([[1, 2, 3], [3, 1], [4]])
    scores = dict(fused)
    assert scores[1] == pytest.approx(1 / 61 + 1 / 62)
    assert scores[3] == pytest.approx(1 / 63 + 1 / 61)
    assert scores[4] == pytest.approx(1 / 61)
    assert [i for i, _ in fused] == [1, 3, 4, 2]
    assert search.rrf([]) == []


def test_fts_query():
    assert search.fts_query("Show me a dog on the skateboard") == '"dog"* OR "skateboard"*'
    assert search.fts_query("the a") is None
    assert search.fts_query('pasta" OR x') == '"pasta"*'


@pytest.mark.skipif(not db._load_vec(db.sqlite3.connect(":memory:")), reason="sqlite-vec not loadable")
def test_hybrid_with_vectors(client, conn, env, monkeypatch):
    from sqlite_vec import serialize_float32
    from app.ml import text_embed, visual_embed
    a = add_item(conn, "V1", caption="pasta recipe")
    b = add_item(conn, "V2", caption="nothing in common")
    c = add_item(conn, "V3", caption="also nothing")

    def vec(dim, hot):
        v = [0.0] * dim
        v[hot] = 1.0
        return v
    conn.execute("INSERT INTO text_vec(item_id, embedding) VALUES (?, ?)", (b, serialize_float32(vec(db.TEXT_DIM, 0))))
    conn.execute("INSERT INTO text_vec(item_id, embedding) VALUES (?, ?)", (a, serialize_float32(vec(db.TEXT_DIM, 1))))
    for hot in (0, 2, 3):
        conn.execute("INSERT INTO visual_vec(embedding, item_id) VALUES (?, ?)", (serialize_float32(vec(db.VISUAL_DIM, hot)), c))
    conn.execute("INSERT INTO visual_vec(embedding, item_id) VALUES (?, ?)", (serialize_float32(vec(db.VISUAL_DIM, 1)), b))
    conn.commit()
    monkeypatch.setattr(text_embed, "embed", lambda texts, half=False, query=False: [vec(db.TEXT_DIM, 0)])
    monkeypatch.setattr(visual_embed, "embed_text", lambda q, half=False: vec(db.VISUAL_DIM, 0))
    env.search_models = True
    lists = [search.fts_ranked(conn, "pasta"), search.text_ranked(conn, "pasta"), search.visual_ranked(conn, "pasta")]
    assert lists[0] == [a] and lists[1] == [b, a] and lists[2] == [c, b]
    r = client.get("/api/items", params={"q": "pasta"}).json()
    assert [i["id"] for i in r["items"]] == [a, b, c] or [i["id"] for i in r["items"]] == [b, a, c]
    assert r["total"] == 3
    assert client.delete(f"/api/items/{c}").status_code == 204
    assert conn.execute("SELECT COUNT(*) FROM visual_vec WHERE item_id=?", (c,)).fetchone()[0] == 0


def test_text_embedder_change_rebuilds_text_vectors(env, monkeypatch):
    if not db.vec_available and not db._load_vec(db.connect()):
        pytest.skip("sqlite-vec unavailable")
    conn = db.open_db()
    assert db.get_setting(conn, "text_embed_model") == env.text_model
    a = add_item(conn, "A", status="indexed")
    b = add_item(conn, "B", status="enriched")
    conn.close()
    monkeypatch.setattr(env, "text_model", "BAAI/bge-m3")
    monkeypatch.setattr(db, "TEXT_DIM", 1024)
    conn = db.open_db()
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='text_vec'").fetchone()[0]
    assert "float[1024]" in sql and db.get_setting(conn, "text_embed_model") == "BAAI/bge-m3"
    assert [r[0] for r in conn.execute("SELECT status FROM items ORDER BY id")] == ["enriched", "enriched"]
    conn.close()
    conn = db.open_db()
    add_item(conn, "C", status="indexed")
    conn.close()
    conn = db.open_db()
    assert conn.execute("SELECT status FROM items WHERE shortcode='C'").fetchone()[0] == "indexed"
    conn.close()
