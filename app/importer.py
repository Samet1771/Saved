import io
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .db import now

URL_RE = re.compile(
    r"^(?:https?://)?(?:www\.|m\.)?(?:instagram\.com|instagr\.am)/(?:[A-Za-z0-9_.]+/)?(p|reel|reels|tv)/([A-Za-z0-9_-]+)",
    re.I,
)
EXPORT_NAME_RE = re.compile(r"saved_(posts|collections)[^/\\]*\.json$", re.I)
TYPE_GUESS = {"reel": "reel", "reels": "reel", "tv": "video", "p": "photo"}


def parse_url(url):
    m = URL_RE.match((url or "").strip())
    if not m:
        return None
    kind, code = m.group(1).lower(), m.group(2)
    path = {"reels": "reel"}.get(kind, kind)
    return code, f"https://www.instagram.com/{path}/{code}/", TYPE_GUESS[kind]


def _fix(s):
    if not isinstance(s, str):
        return s
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def _strings(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str):
                yield k, v
            else:
                yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


def _timestamp(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() == "timestamp" and isinstance(v, (int, float)) and v > 0:
                return v
        for v in obj.values():
            t = _timestamp(v)
            if t:
                return t
    elif isinstance(obj, list):
        for v in obj:
            t = _timestamp(v)
            if t:
                return t
    return None


def _header_name(entry):
    smd = entry.get("string_map_data") if isinstance(entry, dict) else None
    if isinstance(smd, dict):
        name = smd.get("Name")
        if isinstance(name, dict) and isinstance(name.get("value"), str) and not name.get("href"):
            return _fix(name["value"])
    return None


def _entry(entry, collection):
    urls = [parse_url(v) for _, v in _strings(entry)]
    urls = [u for u in urls if u]
    if not urls:
        return None
    title = entry.get("title") if isinstance(entry.get("title"), str) else None
    if not title:
        title = next((v for k, v in _strings(entry) if k == "value" and not parse_url(v)), None)
    ts = _timestamp(entry)
    code, url, guess = urls[0]
    return {
        "shortcode": code, "url": url, "type": guess, "author": _fix(title) or None,
        "saved_at": datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ts else None,
        "collection": collection,
    }


def parse_json(data):
    out = []

    def walk(obj):
        if isinstance(obj, list):
            collection = None
            for el in obj:
                if isinstance(el, dict):
                    e = _entry(el, collection)
                    if e:
                        out.append(e)
                        continue
                    name = _header_name(el)
                    if name:
                        collection = name
                        continue
                walk(el)
        elif isinstance(obj, dict):
            for v in obj.values():
                walk(v)

    walk(data)
    return out


def load_entries(path_or_bytes, filename=None):
    raw = path_or_bytes if isinstance(path_or_bytes, bytes) else Path(path_or_bytes).read_bytes()
    name = filename or (str(path_or_bytes) if not isinstance(path_or_bytes, bytes) else "")
    if raw[:2] == b"PK":
        entries = []
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            for n in sorted(z.namelist()):
                if EXPORT_NAME_RE.search(n):
                    entries += parse_json(json.loads(z.read(n).decode("utf-8-sig")))
        return entries
    if not name.lower().endswith(".json") and raw.lstrip()[:1] not in (b"{", b"["):
        raise ValueError("expected an Instagram export .zip or .json file")
    return parse_json(json.loads(raw.decode("utf-8-sig")))


def import_entries(conn, entries):
    seen, added, existing, collections = {}, 0, 0, set()
    top = conn.execute("SELECT COALESCE(MIN(manual_pos), 0) FROM items").fetchone()[0]
    for e in entries:
        code = e["shortcode"]
        if code not in seen:
            row = conn.execute("SELECT id, author, saved_at FROM items WHERE shortcode=?", (code,)).fetchone()
            if row:
                existing += 1
                if (not row["author"] and e["author"]) or (not row["saved_at"] and e["saved_at"]):
                    conn.execute("UPDATE items SET author=COALESCE(author, ?), saved_at=COALESCE(saved_at, ?) WHERE id=?",
                                 (e["author"], e["saved_at"], row["id"]))
                seen[code] = row["id"]
            else:
                top -= 1
                ts = now()
                cur = conn.execute(
                    "INSERT INTO items(shortcode, url, type, source, author, saved_at, manual_pos, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (code, e["url"], e["type"], "export", e["author"], e["saved_at"] or ts, top, ts, ts))
                added += 1
                seen[code] = cur.lastrowid
        elif e["saved_at"]:
            conn.execute("UPDATE items SET saved_at=MIN(saved_at, ?) WHERE id=?", (e["saved_at"], seen[code]))
        if e["collection"]:
            collections.add(e["collection"])
            conn.execute("INSERT OR IGNORE INTO collections(name, created_at) VALUES (?, ?)", (e["collection"], now()))
            cid = conn.execute("SELECT id FROM collections WHERE name=?", (e["collection"],)).fetchone()[0]
            conn.execute(
                "INSERT OR IGNORE INTO collection_items(collection_id, item_id, pos) VALUES"
                " (?, ?, (SELECT COALESCE(MAX(pos), 0) + 1 FROM collection_items WHERE collection_id=?))",
                (cid, seen[code], cid))
    conn.commit()
    return {"added": added, "existing": existing, "collections": len(collections)}


def import_file(conn, path_or_bytes, filename=None):
    return import_entries(conn, load_entries(path_or_bytes, filename))


if __name__ == "__main__":
    import sys
    from .db import open_db
    print(import_file(open_db(), sys.argv[1]))
