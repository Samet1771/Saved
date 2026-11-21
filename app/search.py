import logging
import re
import threading
import time

from .config import settings
from .db import has_vec

log = logging.getLogger("search")

RRF_K = 60
FTS_LIMIT = 200
TEXT_K = 50
VISUAL_K = 150
STOP = set("a an and are as at be by for from how i in is it me my of on or show that the this to video what when where which who with".split())

_lock = threading.Lock()
_vector_ok = True
_last_used = 0.0


def fts_query(q):
    tokens = [t for t in re.findall(r"\w+", q.lower()) if t not in STOP and len(t) > 1]
    return " OR ".join(f'"{t}"*' if len(t) > 2 else f'"{t}"' for t in dict.fromkeys(tokens)) or None


def fts_ranked(conn, q):
    expr = fts_query(q)
    if not expr:
        return []
    rows = conn.execute("SELECT rowid FROM items_fts WHERE items_fts MATCH ? ORDER BY bm25(items_fts) LIMIT ?",
                        (expr, FTS_LIMIT))
    return [r[0] for r in rows]


def _vectors_enabled(conn):
    return settings.search_models and _vector_ok and has_vec(conn)


def text_ranked(conn, q):
    from sqlite_vec import serialize_float32
    from .ml import text_embed
    global _last_used
    with _lock:
        vec = text_embed.embed([q], half=True, query=True)[0]
        _last_used = time.monotonic()
    rows = conn.execute("SELECT item_id FROM text_vec WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                        (serialize_float32(vec), TEXT_K))
    return [r[0] for r in rows]


def visual_ranked(conn, q):
    from sqlite_vec import serialize_float32
    from .ml import visual_embed
    global _last_used
    with _lock:
        vec = visual_embed.embed_text(q, half=True)
        _last_used = time.monotonic()
    rows = conn.execute("SELECT item_id FROM visual_vec WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                        (serialize_float32(vec), VISUAL_K))
    return list(dict.fromkeys(r[0] for r in rows))


def unload_if_idle():
    from .ml import text_embed, visual_embed
    with _lock:
        if _last_used and time.monotonic() - _last_used > settings.search_idle_unload:
            text_embed.unload()
            visual_embed.unload()
            log.info("search models unloaded after idle")
            return True
    return False


def rrf(lists, k=RRF_K):
    scores = {}
    for ranked in lists:
        for rank, item_id in enumerate(ranked, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


def search(conn, q):
    global _vector_ok
    lists = [fts_ranked(conn, q)]
    if _vectors_enabled(conn):
        for fn in (text_ranked, visual_ranked):
            try:
                lists.append(fn(conn, q))
            except ImportError as e:
                log.warning("vector search disabled, ML libs missing: %s", e)
                _vector_ok = False
                break
            except Exception:
                log.exception("vector search failed (%s)", fn.__name__)
    return rrf(lists)
