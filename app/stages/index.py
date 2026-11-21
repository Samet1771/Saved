import json

from ..db import has_vec, update_item
from ..ml import text_embed, visual_embed
from .fetch import item_dir

MODELS = [text_embed, visual_embed]


def _flat(x):
    if isinstance(x, dict):
        return "\n".join(f"{k}: {_flat(v)}" for k, v in x.items() if v not in (None, "", [], {}))
    if isinstance(x, list):
        return "; ".join(_flat(i) for i in x)
    return str(x)


def compose(item):
    tags = json.loads(item["tags"] or "[]")
    details = json.loads(item["details"] or "{}")
    timeline = " ".join(f"[{e.get('start')}-{e.get('end')}s] {e.get('event')}" for e in details.get("timeline") or [])
    ocr = " | ".join(item["ocr_text"].splitlines()) if item["ocr_text"] else None
    parts = [("Summary", item["summary"]), ("Description", item["description"]), ("Category", item["category"]),
             ("Tags", ", ".join(tags)), ("Timeline", timeline),
             ("Visual", _flat(details["visual"]) if details.get("visual") else None),
             ("Transcript", item["transcript_text"]), ("On-screen text (OCR)", ocr)]
    return "\n".join(f"{k}: {v}" for k, v in parts if v)[:8000]


def process(conn, item):
    if has_vec(conn):
        from sqlite_vec import serialize_float32
        d = item_dir(item["shortcode"])
        images = json.loads(item["keyframes"]) + [m["file"] for m in json.loads(item["media"]) if m["kind"] == "image"]
        images = [d / f for f in images if (d / f).exists()]
        doc = compose(item)
        text_vec = text_embed.embed([doc])[0] if doc else None
        visual = visual_embed.embed_images(images) if images else []
        conn.execute("DELETE FROM text_vec WHERE item_id=?", (item["id"],))
        conn.execute("DELETE FROM visual_vec WHERE item_id=?", (item["id"],))
        if text_vec:
            conn.execute("INSERT INTO text_vec(item_id, embedding) VALUES (?, ?)", (item["id"], serialize_float32(text_vec)))
        conn.executemany("INSERT INTO visual_vec(embedding, item_id) VALUES (?, ?)",
                         [(serialize_float32(v), item["id"]) for v in visual])
    update_item(conn, item["id"], status="indexed", error=None, failed_stage=None)
