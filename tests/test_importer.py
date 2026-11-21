import io
import zipfile

from app import importer

from .conftest import FIXTURES


def test_parse_url():
    assert importer.parse_url("https://www.instagram.com/reels/AbC_-1/?igsh=x") == (
        "AbC_-1", "https://www.instagram.com/reel/AbC_-1/", "reel")
    assert importer.parse_url("instagram.com/p/XYZ") == ("XYZ", "https://www.instagram.com/p/XYZ/", "photo")
    assert importer.parse_url("https://www.instagram.com/someuser/reel/QQ1/")[0] == "QQ1"
    assert importer.parse_url("https://www.instagram.com/tv/TV1/")[2] == "video"
    for bad in ("https://www.instagram.com/explore/", "https://example.com/p/XYZ/", "https://www.instagram.com/p/", ""):
        assert importer.parse_url(bad) is None


def test_saved_posts(conn):
    res = importer.import_file(conn, FIXTURES / "saved_posts.json")
    assert res == {"added": 3, "existing": 0, "collections": 0}
    rows = {r["shortcode"]: r for r in conn.execute("SELECT * FROM items")}
    assert set(rows) == {"ABC", "XYZ", "TV1"}
    assert rows["ABC"]["author"] == "chef_anna"
    assert rows["ABC"]["saved_at"] == "2023-11-14T22:13:20Z"
    assert rows["XYZ"]["url"] == "https://www.instagram.com/p/XYZ/"
    assert rows["TV1"]["type"] == "video"
    assert all(r["status"] == "queued" and r["source"] == "export" for r in rows.values())


def test_collections_and_idempotence(conn):
    importer.import_file(conn, FIXTURES / "saved_posts.json")
    res = importer.import_file(conn, FIXTURES / "saved_collections.json")
    assert res == {"added": 1, "existing": 2, "collections": 2}
    members = {(r[0], r[1]) for r in conn.execute(
        "SELECT c.name, i.shortcode FROM collection_items ci JOIN collections c ON c.id=ci.collection_id"
        " JOIN items i ON i.id=ci.item_id")}
    assert members == {("Recipes", "XYZ"), ("Recipes", "ABC"), ("Yeni örnekler", "NEW1")}
    again = importer.import_file(conn, FIXTURES / "saved_collections.json")
    assert again == {"added": 0, "existing": 3, "collections": 2}
    assert conn.execute("SELECT COUNT(*) FROM collection_items").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 4


def test_zip_anywhere_inside(conn):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.write(FIXTURES / "saved_posts.json", "instagram-user-2025/your_instagram_activity/saved/saved_posts.json")
        z.write(FIXTURES / "saved_collections.json", "instagram-user-2025/your_instagram_activity/saved/saved_collections.json")
        z.writestr("instagram-user-2025/other/liked_posts.json", '{"likes_media_likes": [{"title": "x", "string_map_data":'
                   ' {"": {"href": "https://www.instagram.com/p/LIKED/", "timestamp": 1}}}]}')
    res = importer.import_file(conn, buf.getvalue(), "export.zip")
    assert res == {"added": 4, "existing": 0, "collections": 2}
    assert not conn.execute("SELECT 1 FROM items WHERE shortcode='LIKED'").fetchone()


def test_label_values_shape():
    data = {"saved_saved_media": [{"label_values": [
        {"label": "URL", "value": "https://www.instagram.com/reel/LV1/"}, {"label": "Owner", "value": "x"}],
        "timestamp": 1700000000}]}
    entries = importer.parse_json(data)
    assert [(e["shortcode"], e["saved_at"]) for e in entries] == [("LV1", "2023-11-14T22:13:20Z")]
