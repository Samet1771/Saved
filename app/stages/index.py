import json
from pathlib import Path

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


def _json(v, default):
    return v if isinstance(v, (list, dict)) else json.loads(v or default)


def compose(item):
    tags = _json(item["tags"], "[]")
    details = _json(item["details"], "{}")
    timeline = " ".join(f"[{e.get('start')}-{e.get('end')}s] {e.get('event')}" for e in details.get("timeline") or [])
    ocr = " | ".join(item["ocr_text"].splitlines()) if item["ocr_text"] else None
    parts = [("Summary", item["summary"]), ("Description", item["description"]), ("Category", item["category"]),
             ("Tags", ", ".join(tags)), ("Timeline", timeline),
             ("Visual", _flat(details["visual"]) if details.get("visual") else None),
             ("Transcript", item["transcript_text"]), ("On-screen text (OCR)", ocr)]
    return "\n".join(f"{k}: {v}" for k, v in parts if v)[:8000]


def compute(item, d):
    """Embed the item's text document and its keyframes/photos in directory d; returns (text_vec, [visual_vec])."""
    d = Path(d)
    images = _json(item["keyframes"], "[]") + [m["file"] for m in _json(item["media"], "[]") if m["kind"] == "image"]
    images = [d / f for f in images if (d / f).exists()]
    doc = compose(item)
    text_vec = text_embed.embed([doc])[0] if doc else None
    return text_vec, visual_embed.embed_images(images) if images else []


def store(conn, item_id, text_vec, visual):
    if has_vec(conn):
        from sqlite_vec import serialize_float32
        conn.execute("DELETE FROM text_vec WHERE item_id=?", (item_id,))
        conn.execute("DELETE FROM visual_vec WHERE item_id=?", (item_id,))
        if text_vec:
            conn.execute("INSERT INTO text_vec(item_id, embedding) VALUES (?, ?)", (item_id, serialize_float32(text_vec)))
        conn.executemany("INSERT INTO visual_vec(embedding, item_id) VALUES (?, ?)",
                         [(serialize_float32(v), item_id) for v in visual])
    update_item(conn, item_id, status="indexed", error=None, failed_stage=None)


def process(conn, item):
    if has_vec(conn):
        store(conn, item["id"], *compute(item, item_dir(item["shortcode"])))
    else:
        store(conn, item["id"], None, [])
