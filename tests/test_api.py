import json

from app.stages import enrich

from .conftest import FIXTURES, add_item


def test_auth(client):
    assert client.get("/api/items").status_code == 200
    assert client.get("/api/items", headers={"Authorization": "Bearer wrong"}).status_code == 401
    r = client.get("/api/status", headers={"Authorization": ""})
    assert r.status_code == 401 and r.json() == {"detail": "invalid or missing token"}


def test_ingest_validation_and_dedupe(client):
    r = client.post("/api/ingest", json={"url": "https://www.instagram.com/reels/DeF123/?igsh=MTIz"})
    assert r.status_code == 201 and r.json()["created"] is True and r.json()["status"] == "queued"
    item_id = r.json()["id"]
    r2 = client.post("/api/ingest", json={"url": "https://instagram.com/reel/DeF123"})
    assert r2.status_code == 200 and r2.json() == {"id": item_id, "status": "queued", "created": False}
    d = client.get(f"/api/items/{item_id}").json()
    assert d["url"] == "https://www.instagram.com/reel/DeF123/" and d["source"] == "ingest" and d["type"] == "reel"
    for bad in ("https://www.instagram.com/stories/x/1/", "https://youtube.com/watch?v=1", "nonsense"):
        r = client.post("/api/ingest", json={"url": bad})
        assert r.status_code == 422 and isinstance(r.json()["detail"], str)
    assert client.post("/api/ingest", json={}).status_code == 422


def test_ingest_promotes_pending_export_item(client, conn):
    item_id = add_item(conn, "EXP1", status="queued")
    r = client.post("/api/ingest", json={"url": "https://www.instagram.com/reel/EXP1/"})
    assert r.status_code == 200
    assert conn.execute("SELECT source FROM items WHERE id=?", (item_id,)).fetchone()[0] == "ingest"


def test_import_endpoint(client):
    r = client.post("/api/import", files={"file": ("saved_posts.json", (FIXTURES / "saved_posts.json").read_bytes())})
    assert r.json() == {"added": 3, "existing": 0, "collections": 0}
    r = client.post("/api/import", files={"file": ("x.txt", b"hello")})
    assert r.status_code == 422


def test_items_list_filter_sort(client, conn):
    a = add_item(conn, "A1", category="cooking", tags=["pasta", "recipe"], author="anna", saved_at="2025-01-01T00:00:00Z",
                 posted_at="2024-12-01T00:00:00Z", caption="x" * 300, thumb="thumb.jpg",
                 media=[{"kind": "video", "file": "0.mp4", "width": 720, "height": 1280, "duration": 31.2, "audio": True}])
    b = add_item(conn, "B2", type="photo", category="travel", tags=["beach"], author="bob", saved_at="2025-02-01T00:00:00Z")
    c = add_item(conn, "C3", status="failed", error="boom", tags=["pasta"], author="anna", saved_at="2024-06-01T00:00:00Z",
                 posted_at="2025-03-01T00:00:00Z")
    r = client.get("/api/items").json()
    assert r["total"] == 3 and [i["id"] for i in r["items"]] == [b, a, c]
    first = next(i for i in r["items"] if i["id"] == a)
    assert len(first["caption"]) == 200 and first["thumb_url"] == "/media/A1/thumb.jpg"
    assert first["media"] == [{"kind": "video", "url": "/media/A1/0.mp4", "width": 720, "height": 1280, "duration": 31.2}]
    assert "score" not in first
    ids = lambda **p: [i["id"] for i in client.get("/api/items", params=p).json()["items"]]
    assert ids(sort="saved_asc") == [c, a, b]
    assert ids(sort="posted_desc") == [c, a, b]
    assert ids(tag="pasta") == [a, c]
    assert ids(category="travel") == [b]
    assert ids(author="anna", status="failed") == [c]
    assert ids(type="photo") == [b]
    assert ids(limit=1, offset=1) == [a]
    assert client.get("/api/items", params={"sort": "bogus"}).status_code == 422
    d = client.get(f"/api/items/{a}").json()
    assert len(d["caption"]) == 300 and d["manual"] == {"category": False, "tags": False} and d["transcript"] == []
    assert client.get("/api/items/999").status_code == 404


def test_patch_manual_flags_survive_llm(client, conn):
    item_id = add_item(conn, "P1", category="other", tags=["a", "b", "c"], status="extracted")
    d = client.patch(f"/api/items/{item_id}", json={"tags": ["Pasta ", "#Recipe", "pasta"]}).json()
    assert d["tags"] == ["pasta", "recipe"] and d["manual"] == {"category": False, "tags": True}
    d = client.patch(f"/api/items/{item_id}", json={"category": "cooking"}).json()
    assert d["category"] == "cooking" and d["manual"]["category"] is True
    row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    enrich.apply_result(conn, row, {"category": "Travel", "tags": ["beach", "sea", "sun"], "summary": "A beach."})
    d = client.get(f"/api/items/{item_id}").json()
    assert (d["category"], d["tags"], d["summary"], d["status"]) == ("cooking", ["pasta", "recipe"], "A beach.", "enriched")
    d = client.patch(f"/api/items/{item_id}", json={"category": None}).json()
    assert d["manual"]["category"] is False and d["category"] == "cooking"


def test_enrich_unknown_category_proposed(client, conn):
    item_id = add_item(conn, "E1", status="extracted")
    row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    enrich.apply_result(conn, row, {"category": "Woodworking", "tags": ["Wood", "#Saw", "wood", "chisel"], "summary": "s"})
    d = client.get(f"/api/items/{item_id}").json()
    assert d["category"] == "other" and d["tags"] == ["wood", "saw", "chisel"]
    cats = client.get("/api/categories").json()
    assert cats["proposed"] == [{"name": "woodworking", "count": 1}] and "cooking" in cats["categories"]
    cats = client.put("/api/categories", json={"categories": ["cooking", "Woodworking", "other", " "]}).json()
    assert cats == {"categories": ["cooking", "Woodworking"], "proposed": []}


def test_collections_and_manual_order(client, conn):
    a, b, c = (add_item(conn, s, thumb="thumb.jpg") for s in ("A", "B", "C"))
    col = client.post("/api/collections", json={"name": "Recipes"})
    assert col.status_code == 201
    cid = col.json()["id"]
    assert client.post("/api/collections", json={"name": "Recipes"}).status_code == 409
    col = client.post(f"/api/collections/{cid}/items", json={"item_ids": [b, a, b, 999]}).json()
    assert col == {"id": cid, "name": "Recipes", "count": 2, "cover_url": "/media/B/thumb.jpg"}
    client.post(f"/api/collections/{cid}/items", json={"item_ids": [c]})
    ids = lambda **p: [i["id"] for i in client.get("/api/items", params=p).json()["items"]]
    assert ids(collection=cid, sort="manual") == [b, a, c]
    assert client.post("/api/order/move", json={"item_id": c, "after_id": None, "collection_id": cid}).status_code == 204
    assert ids(collection=cid, sort="manual") == [c, b, a]
    client.post("/api/order/move", json={"item_id": c, "after_id": a, "collection_id": cid})
    assert ids(collection=cid, sort="manual") == [b, a, c]
    client.post("/api/order/move", json={"item_id": a, "after_id": c, "collection_id": None})
    glob = ids(sort="manual")
    assert glob.index(a) == glob.index(c) + 1
    assert client.post("/api/order/move", json={"item_id": a, "after_id": 999}).status_code == 404
    assert client.patch(f"/api/collections/{cid}", json={"name": "Food"}).json()["name"] == "Food"
    item = client.get(f"/api/items/{a}").json()
    assert item["collections"] == [{"id": cid, "name": "Food"}]
    assert client.delete(f"/api/collections/{cid}/items/{a}").status_code == 204
    assert client.get("/api/collections").json()[0]["count"] == 2
    facets = client.get("/api/facets").json()
    assert facets["collections"] == [{"id": cid, "name": "Food", "count": 2}]
    assert client.delete(f"/api/collections/{cid}").status_code == 204
    assert client.get("/api/collections").json() == []
    assert client.get(f"/api/items/{a}").status_code == 200


def test_fts_search(client, conn):
    a = add_item(conn, "S1", caption="Creamy carbonara pasta in 10 minutes", tags=["pasta", "recipe"])
    b = add_item(conn, "S2", caption="Sunset at the beach", transcript_text="we are making pasta later")
    add_item(conn, "S3", caption="Dog skateboarding", ocr_text="SKATE PARK")
    add_item(conn, "S4", caption="Kahvaltı tarifi", summary="A Turkish breakfast recipe.")
    r = client.get("/api/items", params={"q": "pasta recipe"}).json()
    assert r["items"][0]["id"] == a and r["total"] == 3 and b in [i["id"] for i in r["items"]]
    assert all("score" in i for i in r["items"])
    assert [i["shortcode"] for i in client.get("/api/items", params={"q": "skate"}).json()["items"]] == ["S3"]
    assert [i["shortcode"] for i in client.get("/api/items", params={"q": "tarif"}).json()["items"]] == ["S4"]
    assert client.get("/api/items", params={"q": "pasta", "tag": "recipe"}).json()["total"] == 1
    assert client.get("/api/items", params={"q": "the"}).json() == {"total": 0, "items": []}
    client.patch(f"/api/items/{b}", json={"tags": ["volleyball"]})
    assert [i["id"] for i in client.get("/api/items", params={"q": "volleyball"}).json()["items"]] == [b]


def test_facets_status_retry_delete(client, conn, env):
    a = add_item(conn, "F1", category="cooking", tags=["pasta"], author="anna")
    f = add_item(conn, "F2", status="failed", failed_stage="enrich", error="llm broke", attempts=2)
    p = add_item(conn, "F3", status="private", error="login required")
    facets = client.get("/api/facets").json()
    assert facets["categories"] == [{"name": "cooking", "count": 1}] and facets["tags"] == [{"name": "pasta", "count": 1}]
    assert {"name": "reel", "count": 3} in facets["types"]
    st = client.get("/api/status").json()
    assert st["counts"] == {"indexed": 1, "failed": 1, "private": 1}
    assert st["disk"]["paused"] is False and st["worker"]["stage"] == "idle" and st["worker"]["in_batch_window"] is True
    assert {e["id"] for e in st["last_errors"]} == {f, p}
    assert client.post(f"/api/items/{f}/retry").json()["status"] == "extracted"
    assert client.post(f"/api/items/{p}/retry").json()["status"] == "queued"
    assert client.post(f"/api/items/{a}/retry").status_code == 409
    d = env.media_dir / "F1"
    d.mkdir(parents=True)
    (d / "0.mp4").write_bytes(b"x")
    assert client.delete(f"/api/items/{a}").status_code == 204
    assert not d.exists() and client.get(f"/api/items/{a}").status_code == 404
    env.min_free_gb = 10 ** 9
    assert client.get("/api/status").json()["disk"]["paused"] is True


def test_static_fallback(client, env):
    assert client.get("/").status_code == 404
    env.web_dir.mkdir()
    (env.web_dir / "index.html").write_text("<h1>vault</h1>")
    (env.web_dir / "app.js").write_text("export {}")
    assert client.get("/", headers={"Authorization": ""}).text == "<h1>vault</h1>"
    assert client.get("/collections/3").text == "<h1>vault</h1>"
    r = client.get("/app.js")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript")
    assert client.get("/missing.css").status_code == 404
    assert client.get("/api/nope").status_code == 404
    assert json.loads(client.get("/api/nope").text) == {"detail": "not found"}
