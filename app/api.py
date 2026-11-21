import asyncio
import json
import logging
import mimetypes
import os
import re
import secrets
import shutil
import threading
import time
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from typing import Literal, Optional

from fastapi import APIRouter, Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from . import auth as accounts
from . import db, importer
from .config import in_batch_window, settings, valid_window
from .ml import llm
from .search import search, unload_if_idle

log = logging.getLogger("api")

for ext, mt in ((".js", "text/javascript"), (".mjs", "text/javascript"), (".css", "text/css"),
                (".svg", "image/svg+xml"), (".webmanifest", "application/manifest+json"), (".json", "application/json")):
    mimetypes.add_type(mt, ext)

PREV_STATUS = {"fetch": "queued", "extract": "fetched", "enrich": "extracted", "index": "enriched"}
SAFE_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")


@asynccontextmanager
async def lifespan(_app):
    conn = db.open_db()
    if settings.auth_disabled:
        log.warning("AUTH_DISABLED is set: authentication is OFF")
    elif not accounts.has_account(conn):
        log.warning("No account yet. Create one: docker compose exec api python -m app.auth set-password")
    conn.close()
    task = asyncio.create_task(_idle_unloader())
    yield
    task.cancel()


async def _idle_unloader():
    while True:
        await asyncio.sleep(60)
        await asyncio.to_thread(unload_if_idle)


app = FastAPI(title="Saved", lifespan=lifespan)
if settings.cors_origins:
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"],
                       allow_headers=["Authorization", "Content-Type", "Range"], expose_headers=["Content-Range"])


@app.exception_handler(RequestValidationError)
async def validation_error(_request, exc):
    msg = "; ".join(f"{'.'.join(str(p) for p in e['loc'][1:]) or e['loc'][0]}: {e['msg']}" for e in exc.errors())
    return JSONResponse({"detail": msg}, status_code=422)


def token_ok(token):
    if settings.auth_disabled:
        return True
    if not token:
        return False
    if settings.api_token and secrets.compare_digest(token.encode(), settings.api_token.encode()):
        return True
    conn = db.connect()
    try:
        return accounts.session_ok(conn, token)
    finally:
        conn.close()


def bearer(authorization):
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def auth(authorization: Optional[str] = Header(None)):
    if not token_ok(bearer(authorization)):
        raise HTTPException(401, "invalid or missing token", headers={"WWW-Authenticate": "Bearer"})


def get_conn():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


api = APIRouter(prefix="/api", dependencies=[Depends(auth)])

LOGIN_WINDOW_S = 300
LOGIN_MAX_FAILS = 5
_login_fails = {}
_login_lock = threading.Lock()


def _recent_fails(ip):
    cutoff = time.monotonic() - LOGIN_WINDOW_S
    with _login_lock:
        fails = [t for t in _login_fails.get(ip, []) if t > cutoff]
        _login_fails[ip] = fails
        return len(fails)


class LoginBody(BaseModel):
    username: str
    password: str
    label: Optional[str] = None


@app.post("/api/login")
def login(body: LoginBody, request: Request, conn=Depends(get_conn)):
    ip = request.client.host if request.client else "?"
    if _recent_fails(ip) >= LOGIN_MAX_FAILS:
        raise HTTPException(429, "Too many failed sign-ins. Try again in a few minutes.")
    if not accounts.has_account(conn):
        raise HTTPException(409, "No account yet. On the server run: docker compose exec api python -m app.auth set-password")
    if not accounts.check_login(conn, body.username.strip(), body.password):
        with _login_lock:
            _login_fails.setdefault(ip, []).append(time.monotonic())
        raise HTTPException(401, "Wrong username or password")
    label = body.label or (request.headers.get("user-agent") or "")[:80]
    return {"token": accounts.create_session(conn, label), "username": accounts.username(conn)}


@api.post("/logout")
def logout(authorization: Optional[str] = Header(None), conn=Depends(get_conn)):
    accounts.revoke(conn, bearer(authorization) or "")
    return {"ok": True}


@api.get("/account")
def account(conn=Depends(get_conn)):
    return {"username": accounts.username(conn)}


class PasswordBody(BaseModel):
    current_password: str
    new_password: str


@api.put("/account/password")
def change_password(body: PasswordBody, authorization: Optional[str] = Header(None), conn=Depends(get_conn)):
    if not accounts.has_account(conn) or not accounts.verify_password(body.current_password, db.get_setting(conn, "auth_hash")):
        raise HTTPException(403, "The current password is wrong")
    if len(body.new_password) < accounts.MIN_PASSWORD:
        raise HTTPException(422, f"Use at least {accounts.MIN_PASSWORD} characters")
    accounts.set_account(conn, accounts.username(conn), body.new_password)
    accounts.revoke_others(conn, bearer(authorization))
    return {"ok": True}


class TokenBody(BaseModel):
    label: str = "Siri Shortcut"


@api.post("/tokens")
def create_token(body: TokenBody, conn=Depends(get_conn)):
    return {"token": accounts.create_session(conn, body.label)}


def media_url(shortcode, name):
    return f"/media/{shortcode}/{name}"


def item_collections(conn, ids):
    out = {i: [] for i in ids}
    if ids:
        rows = conn.execute(
            f"SELECT ci.item_id, c.id, c.name FROM collection_items ci JOIN collections c ON c.id = ci.collection_id"
            f" WHERE ci.item_id IN ({','.join('?' * len(ids))}) ORDER BY c.name", ids)
        for r in rows:
            out[r[0]].append({"id": r[1], "name": r[2]})
    return out


def summary(row, collections, score=None, full=False):
    sc = row["shortcode"]
    caption = row["caption"]
    d = {
        "id": row["id"], "shortcode": sc, "url": row["url"], "type": row["type"], "status": row["status"],
        "source": row["source"], "author": row["author"],
        "caption": caption if full or caption is None else caption[:200],
        "summary": row["summary"], "category": row["category"], "tags": json.loads(row["tags"]),
        "thumb_url": media_url(sc, row["thumb"]) if row["thumb"] else None,
        "media": [{"kind": m["kind"], "url": media_url(sc, m["file"]), "width": m.get("width"),
                   "height": m.get("height"), "duration": m.get("duration")} for m in json.loads(row["media"])],
        "saved_at": row["saved_at"], "posted_at": row["posted_at"], "collections": collections,
    }
    if score is not None:
        d["score"] = round(score, 6)
    return d


def detail(conn, item_id):
    row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(404, "item not found")
    d = summary(row, item_collections(conn, [item_id])[item_id], full=True)
    d.update({
        "transcript": json.loads(row["transcript"]), "ocr_text": row["ocr_text"], "lang": row["lang"],
        "error": row["error"], "hashtags": json.loads(row["hashtags"]),
        "manual": {"category": bool(row["manual_category"]), "tags": bool(row["manual_tags"])},
        "enriched_by": row["enriched_by"], "description": row["description"],
    })
    return d


SORTS = {
    "saved_desc": "i.saved_at DESC, i.id DESC",
    "saved_asc": "i.saved_at ASC, i.id ASC",
    "posted_desc": "i.posted_at IS NULL, i.posted_at DESC, i.id DESC",
}


@api.get("/items")
def list_items(
    q: Optional[str] = None, category: Optional[str] = None, tag: Optional[str] = None,
    collection: Optional[int] = None, author: Optional[str] = None, type: Optional[str] = None,
    status: Optional[str] = None,
    sort: Optional[Literal["saved_desc", "saved_asc", "posted_desc", "manual", "relevance"]] = None,
    offset: int = Query(0, ge=0), limit: int = Query(60, ge=1, le=500), conn=Depends(get_conn),
):
    where, args = ["1=1"], []
    if category:
        where.append("i.category = ?"); args.append(category)
    if tag:
        where.append("EXISTS (SELECT 1 FROM json_each(i.tags) WHERE value = ?)"); args.append(tag)
    if collection is not None:
        where.append("ci.collection_id = ?"); args.append(collection)
    if author:
        where.append("i.author = ?"); args.append(author)
    for col, val in (("type", type), ("status", status)):
        if val:
            vals = [v for v in val.split(",") if v]
            where.append(f"i.{col} IN ({','.join('?' * len(vals))})"); args += vals
    join = "JOIN collection_items ci ON ci.item_id = i.id" if collection is not None else ""
    q = (q or "").strip()
    scores = None
    if q:
        ranked = search(conn, q)
        scores = dict(ranked)
        if not ranked:
            return {"total": 0, "items": []}
        where.append(f"i.id IN ({','.join('?' * len(ranked))})"); args += [r[0] for r in ranked]
    base = f"FROM items i {join} WHERE {' AND '.join(where)}"
    if q and sort in (None, "relevance"):
        ids = [r[0] for r in conn.execute(f"SELECT i.id {base}", args)]
        ids.sort(key=lambda i: (-scores[i], i))
        total, page = len(ids), ids[offset:offset + limit]
    else:
        if sort == "manual":
            order = "ci.pos, i.id" if collection is not None else "i.manual_pos IS NULL, i.manual_pos, i.id DESC"
        else:
            order = SORTS.get(sort or "saved_desc", SORTS["saved_desc"])
        total = conn.execute(f"SELECT COUNT(*) {base}", args).fetchone()[0]
        page = [r[0] for r in conn.execute(f"SELECT i.id {base} ORDER BY {order} LIMIT ? OFFSET ?", [*args, limit, offset])]
    rows = {r["id"]: r for r in conn.execute(
        f"SELECT * FROM items WHERE id IN ({','.join('?' * len(page))})", page)} if page else {}
    cols = item_collections(conn, page)
    return {"total": total, "items": [summary(rows[i], cols[i], scores[i] if scores else None) for i in page]}


@api.get("/items/{item_id}")
def get_item(item_id: int, conn=Depends(get_conn)):
    return detail(conn, item_id)


class ItemPatch(BaseModel):
    category: Optional[str] = None
    tags: Optional[list[str]] = None


@api.patch("/items/{item_id}")
def patch_item(item_id: int, body: ItemPatch, conn=Depends(get_conn)):
    from .stages.enrich import clean_tags
    detail(conn, item_id)
    fields = {}
    if "category" in body.model_fields_set:
        cat = (body.category or "").strip()
        fields.update({"category": cat, "manual_category": 1} if cat else {"manual_category": 0})
    if "tags" in body.model_fields_set:
        fields.update({"tags": clean_tags(body.tags), "manual_tags": 1} if body.tags is not None else {"manual_tags": 0})
    if fields:
        db.update_item(conn, item_id, **fields)
    return detail(conn, item_id)


@api.post("/items/{item_id}/retry")
def retry_item(item_id: int, conn=Depends(get_conn)):
    row = conn.execute("SELECT status, failed_stage FROM items WHERE id=?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(404, "item not found")
    if row["status"] == "failed":
        new = PREV_STATUS.get(row["failed_stage"], "queued")
    elif row["status"] in ("private", "gone", "needs_login"):
        new = "queued"
    else:
        raise HTTPException(409, f"item is {row['status']}, not failed")
    db.update_item(conn, item_id, status=new, error=None, failed_stage=None, attempts=0)
    return detail(conn, item_id)


@api.delete("/items/{item_id}", status_code=204)
def delete_item(item_id: int, conn=Depends(get_conn)):
    row = conn.execute("SELECT shortcode FROM items WHERE id=?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(404, "item not found")
    if db.has_vec(conn):
        conn.execute("DELETE FROM text_vec WHERE item_id=?", (item_id,))
        conn.execute("DELETE FROM visual_vec WHERE item_id=?", (item_id,))
    conn.execute("DELETE FROM items WHERE id=?", (item_id,))
    conn.commit()
    if SAFE_NAME.match(row["shortcode"]):
        shutil.rmtree(settings.media_dir / row["shortcode"], ignore_errors=True)
    return Response(status_code=204)


@api.get("/facets")
def facets(conn=Depends(get_conn)):
    def pairs(sql):
        return [{"name": r[0], "count": r[1]} for r in conn.execute(sql)]
    return {
        "categories": pairs("SELECT category, COUNT(*) FROM items WHERE category IS NOT NULL GROUP BY 1 ORDER BY 2 DESC, 1"),
        "tags": pairs("SELECT j.value, COUNT(*) FROM items i, json_each(i.tags) j GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 200"),
        "collections": [{"id": r[0], "name": r[1], "count": r[2]} for r in conn.execute(
            "SELECT c.id, c.name, COUNT(ci.item_id) FROM collections c LEFT JOIN collection_items ci"
            " ON ci.collection_id = c.id GROUP BY c.id ORDER BY c.name")],
        "authors": pairs("SELECT author, COUNT(*) FROM items WHERE author IS NOT NULL GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 100"),
        "types": pairs("SELECT type, COUNT(*) FROM items WHERE type IS NOT NULL GROUP BY 1 ORDER BY 2 DESC"),
    }


def categories_payload(conn):
    cats = db.get_categories(conn)
    lower = {c.lower() for c in cats}
    proposed = [{"name": r[0], "count": r[1]} for r in conn.execute(
        "SELECT name, count FROM proposed_categories ORDER BY count DESC, name") if r[0].lower() not in lower]
    return {"categories": cats, "proposed": proposed}


class CategoriesBody(BaseModel):
    categories: list[str]


@api.get("/categories")
def get_categories(conn=Depends(get_conn)):
    return categories_payload(conn)


@api.put("/categories")
def put_categories(body: CategoriesBody, conn=Depends(get_conn)):
    cats = list(dict.fromkeys(c.strip() for c in body.categories if c.strip() and c.strip().lower() != "other"))
    conn.execute("DELETE FROM categories")
    conn.executemany("INSERT INTO categories(name, pos) VALUES (?, ?)", [(c, i) for i, c in enumerate(cats)])
    conn.executemany("DELETE FROM proposed_categories WHERE lower(name) = lower(?)", [(c,) for c in cats])
    conn.commit()
    return categories_payload(conn)


def collection_out(conn, cid):
    r = conn.execute("SELECT c.id, c.name, COUNT(ci.item_id) FROM collections c LEFT JOIN collection_items ci"
                     " ON ci.collection_id = c.id WHERE c.id = ? GROUP BY c.id", (cid,)).fetchone()
    if not r:
        raise HTTPException(404, "collection not found")
    cover = conn.execute("SELECT i.shortcode, i.thumb FROM collection_items ci JOIN items i ON i.id = ci.item_id"
                         " WHERE ci.collection_id = ? AND i.thumb IS NOT NULL ORDER BY ci.pos LIMIT 1", (cid,)).fetchone()
    return {"id": r[0], "name": r[1], "count": r[2], "cover_url": media_url(cover[0], cover[1]) if cover else None}


class NameBody(BaseModel):
    name: str


class ItemIds(BaseModel):
    item_ids: list[int]


def clean_name(name):
    name = name.strip()
    if not name:
        raise HTTPException(422, "name must not be empty")
    return name


@api.get("/collections")
def list_collections(conn=Depends(get_conn)):
    return [collection_out(conn, r[0]) for r in conn.execute("SELECT id FROM collections ORDER BY name")]


@api.post("/collections", status_code=201)
def create_collection(body: NameBody, conn=Depends(get_conn)):
    name = clean_name(body.name)
    if conn.execute("SELECT 1 FROM collections WHERE name=?", (name,)).fetchone():
        raise HTTPException(409, "collection already exists")
    cid = conn.execute("INSERT INTO collections(name, created_at) VALUES (?, ?)", (name, db.now())).lastrowid
    conn.commit()
    return collection_out(conn, cid)


@api.patch("/collections/{cid}")
def rename_collection(cid: int, body: NameBody, conn=Depends(get_conn)):
    collection_out(conn, cid)
    name = clean_name(body.name)
    if conn.execute("SELECT 1 FROM collections WHERE name=? AND id<>?", (name, cid)).fetchone():
        raise HTTPException(409, "collection already exists")
    conn.execute("UPDATE collections SET name=? WHERE id=?", (name, cid))
    conn.commit()
    return collection_out(conn, cid)


@api.delete("/collections/{cid}", status_code=204)
def delete_collection(cid: int, conn=Depends(get_conn)):
    collection_out(conn, cid)
    conn.execute("DELETE FROM collections WHERE id=?", (cid,))
    conn.commit()
    return Response(status_code=204)


@api.post("/collections/{cid}/items")
def add_to_collection(cid: int, body: ItemIds, conn=Depends(get_conn)):
    collection_out(conn, cid)
    for item_id in dict.fromkeys(body.item_ids):
        conn.execute(
            "INSERT OR IGNORE INTO collection_items(collection_id, item_id, pos)"
            " SELECT ?, id, (SELECT COALESCE(MAX(pos), 0) + 1 FROM collection_items WHERE collection_id=?)"
            " FROM items WHERE id=?", (cid, cid, item_id))
    conn.commit()
    return collection_out(conn, cid)


@api.delete("/collections/{cid}/items/{item_id}", status_code=204)
def remove_from_collection(cid: int, item_id: int, conn=Depends(get_conn)):
    collection_out(conn, cid)
    conn.execute("DELETE FROM collection_items WHERE collection_id=? AND item_id=?", (cid, item_id))
    conn.commit()
    return Response(status_code=204)


class MoveBody(BaseModel):
    item_id: int
    after_id: Optional[int] = None
    collection_id: Optional[int] = None


@api.post("/order/move", status_code=204)
def move(body: MoveBody, conn=Depends(get_conn)):
    if body.after_id == body.item_id:
        raise HTTPException(422, "after_id must differ from item_id")
    if body.collection_id is None:
        ids = [r[0] for r in conn.execute("SELECT id FROM items ORDER BY manual_pos IS NULL, manual_pos, id DESC")]
    else:
        collection_out(conn, body.collection_id)
        ids = [r[0] for r in conn.execute("SELECT item_id FROM collection_items WHERE collection_id=? ORDER BY pos, item_id",
                                          (body.collection_id,))]
    if body.item_id not in ids or (body.after_id is not None and body.after_id not in ids):
        raise HTTPException(404, "item not found in this order")
    ids.remove(body.item_id)
    ids.insert(0 if body.after_id is None else ids.index(body.after_id) + 1, body.item_id)
    if body.collection_id is None:
        conn.executemany("UPDATE items SET manual_pos=? WHERE id=?", list(enumerate(ids)))
    else:
        conn.executemany("UPDATE collection_items SET pos=? WHERE collection_id=? AND item_id=?",
                         [(p, body.collection_id, i) for p, i in enumerate(ids)])
    conn.commit()
    return Response(status_code=204)


class IngestBody(BaseModel):
    url: str


@api.post("/ingest")
def ingest(body: IngestBody, conn=Depends(get_conn)):
    parsed = importer.parse_url(body.url)
    if not parsed:
        raise HTTPException(422, "not an Instagram post, reel or video URL")
    code, url, guess = parsed
    row = conn.execute("SELECT id, status, source FROM items WHERE shortcode=?", (code,)).fetchone()
    if row:
        if row["source"] != "ingest" and row["status"] in ("queued", "fetched", "extracted", "enriched"):
            conn.execute("UPDATE items SET source='ingest' WHERE id=?", (row["id"],))
            conn.commit()
        return JSONResponse({"id": row["id"], "status": row["status"], "created": False}, status_code=200)
    ts = db.now()
    top = conn.execute("SELECT COALESCE(MIN(manual_pos), 0) - 1 FROM items").fetchone()[0]
    cid = conn.execute(
        "INSERT INTO items(shortcode, url, type, source, saved_at, manual_pos, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?)", (code, url, guess, "ingest", ts, top, ts, ts)).lastrowid
    conn.commit()
    return JSONResponse({"id": cid, "status": "queued", "created": True}, status_code=201)


@api.post("/import")
async def import_export(file: UploadFile = File(...), conn=Depends(get_conn)):
    data = await file.read()
    try:
        return importer.import_file(conn, data, file.filename)
    except (ValueError, KeyError, json.JSONDecodeError) as e:
        raise HTTPException(422, f"could not parse export: {e}")


_media_size = {"at": 0.0, "bytes": 0}


def media_bytes():
    if time.time() - _media_size["at"] > 60:
        total = 0
        for root, _dirs, files in os.walk(settings.media_dir):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
        _media_size.update(at=time.time(), bytes=total)
    return _media_size["bytes"]


PIPELINE = ["fetch", "extract", "enrich", "index"]
STAGE_INPUT = ["queued", "fetched", "extracted", "enriched", "indexed"]
SKIPPED = ("private", "gone", "needs_login")


def progress(conn, now=None):
    now = now or time.time()
    hour_ago = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 3600))
    rows = conn.execute("SELECT status, failed_stage, COUNT(*) FROM items GROUP BY 1, 2").fetchall()
    total = sum(n for st, _, n in rows if st not in SKIPPED)
    out = {}
    for i, stage in enumerate(PIPELINE):
        done = sum(n for st, fs, n in rows if (st in STAGE_INPUT and STAGE_INPUT.index(st) > i)
                   or (st == "failed" and fs in PIPELINE and PIPELINE.index(fs) > i))
        remaining = sum(n for st, _, n in rows if st in STAGE_INPUT and STAGE_INPUT.index(st) <= i)
        last_hour = conn.execute("SELECT COUNT(*) FROM events WHERE stage=? AND level='info' AND item_id IS NOT NULL"
                                 " AND duration_s IS NOT NULL AND at >= ?", (stage, hour_ago)).fetchone()[0]
        avg = conn.execute("SELECT AVG(duration_s) FROM (SELECT duration_s FROM events WHERE stage=?"
                           " AND item_id IS NOT NULL AND duration_s IS NOT NULL ORDER BY id DESC LIMIT 50)",
                           (stage,)).fetchone()[0]
        out[stage] = {"done": done, "total": total, "remaining": remaining, "done_last_hour": last_hour,
                      "avg_s": round(avg, 2) if avg is not None else None}
    return out


def eta_seconds(prog):
    if any(p["remaining"] and p["avg_s"] is None for p in prog.values()):
        return None
    processing = sum(p["remaining"] * (p["avg_s"] or 0) for p in prog.values())
    fetch = prog["fetch"]
    fetching = fetch["remaining"] * ((settings.fetch_delay_min + settings.fetch_delay_max) / 2 + (fetch["avg_s"] or 0))
    return round(max(processing, fetching))


_cpu_prev = {"total": None, "idle": None, "usec": None, "at": None}


def _read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def _kv(text):
    return {k: int(v) for k, v in (line.split()[:2] for line in (text or "").splitlines() if len(line.split()) >= 2)
            if v.isdigit()}


def _cgroup_stats(out, cg):
    """CPU and RAM of the whole cgroup mounted at cg (the LXC running Docker), not just this container."""
    usec = _kv(_read(f"{cg}/cpu.stat")).get("usage_usec")
    cur = _read(f"{cg}/memory.current")
    if usec is None or cur is None:
        return False
    cpus = _read(f"{cg}/cpuset.cpus.effective")
    if cpus and cpus.strip():
        n = 0
        for part in cpus.strip().split(","):
            lo, _, hi = part.partition("-")
            n += int(hi or lo) - int(lo) + 1
        out["cpus"] = n
    now = time.monotonic()
    if _cpu_prev["usec"] is not None and now > _cpu_prev["at"]:
        busy = (usec - _cpu_prev["usec"]) / ((now - _cpu_prev["at"]) * 1e6 * (out["cpus"] or 1))
        out["cpu_percent"] = round(max(0.0, min(1.0, busy)) * 100, 1)
    _cpu_prev.update(usec=usec, at=now)
    stat = _kv(_read(f"{cg}/memory.stat"))
    out["mem_used_bytes"] = int(cur) - stat.get("file", 0)
    limit = (_read(f"{cg}/memory.max") or "max").strip()
    out["mem_total_bytes"] = settings.system_mem_bytes or (int(limit) if limit.isdigit() else None)
    return True


def system_stats():
    """CPU/RAM of the server running Docker (an LXC's cgroup when CGROUP_DIR is mounted), plus host load and uptime."""
    out = {"cpu_percent": None, "cpus": os.cpu_count(), "load": None, "mem_total_bytes": None,
           "mem_used_bytes": None, "uptime_s": None, "api_rss_mb": db.rss_mb()}
    if not (settings.cgroup_dir and _cgroup_stats(out, settings.cgroup_dir)):
        stat = _read("/proc/stat")
        if stat:
            vals = [int(x) for x in stat.splitlines()[0].split()[1:]]
            total, idle = sum(vals[:8]), vals[3] + (vals[4] if len(vals) > 4 else 0)
            if _cpu_prev["total"] is not None and total > _cpu_prev["total"]:
                busy = 1 - (idle - _cpu_prev["idle"]) / (total - _cpu_prev["total"])
                out["cpu_percent"] = round(max(0.0, min(1.0, busy)) * 100, 1)
            _cpu_prev.update(total=total, idle=idle)
        kb = _kv((_read("/proc/meminfo") or "").replace(":", ""))
        if "MemTotal" in kb and "MemAvailable" in kb:
            out["mem_total_bytes"] = settings.system_mem_bytes or kb["MemTotal"] * 1024
            out["mem_used_bytes"] = (kb["MemTotal"] - kb["MemAvailable"]) * 1024
    load = _read("/proc/loadavg")
    if load:
        out["load"] = [float(x) for x in load.split()[:3]]
    up = _read("/proc/uptime")
    if up:
        out["uptime_s"] = int(float(up.split()[0]))
    return out


def status_payload(conn):
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    du = shutil.disk_usage(settings.media_dir)
    w = conn.execute("SELECT * FROM worker_status WHERE id=1").fetchone()
    sc = conn.execute("SELECT shortcode FROM items WHERE id=?", (w["item_id"],)).fetchone() if w["item_id"] else None
    prog = progress(conn)
    held = db.controls(conn)
    return {
        "counts": {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM items GROUP BY 1")},
        "disk": {"free_bytes": du.free, "total_bytes": du.total, "media_bytes": media_bytes(),
                 "paused": du.free < settings.min_free_gb * 1024 ** 3},
        "worker": {"stage": w["stage"], "item_id": w["item_id"], "shortcode": sc[0] if sc else None,
                   "since": w["since"], "heartbeat": w["heartbeat"],
                   "in_batch_window": in_batch_window(window=held["batch_window"]) if held["batch_window"] else in_batch_window(),
                   "llm_model": llm.active_file(conn), "fetch_backoff_until": w["fetch_backoff_until"],
                   "llm_backend": w["llm_backend"],
                   "llm_remote": {"url": settings.llm_remote_url or None, "healthy": bool(w["llm_remote_healthy"]),
                                  "checked_at": w["llm_remote_checked_at"], "model": w["llm_remote_model"]}},
        "progress": prog,
        "eta_seconds": eta_seconds(prog),
        "controls": {**held, "batch_window_default": settings.batch_window,
                     "batch_window_effective": held["batch_window"] or settings.batch_window},
        "system": {**system_stats(), "worker_rss_mb": w["rss_mb"]},
        "last_errors": [dict(r) for r in conn.execute(
            "SELECT id, shortcode, status, error FROM items WHERE status IN ('failed', 'private', 'gone', 'needs_login')"
            " ORDER BY updated_at DESC, id DESC LIMIT 20")],
    }


@api.get("/status")
def status(conn=Depends(get_conn)):
    return status_payload(conn)


class WorkerControls(BaseModel):
    paused: Optional[bool] = None
    fetch_paused: Optional[bool] = None
    batch_window: Optional[str] = None
    reset_batch_window: bool = False


@api.put("/worker")
def set_worker_controls(body: WorkerControls, conn=Depends(get_conn)):
    if body.batch_window is not None and not valid_window(body.batch_window):
        raise HTTPException(422, "batch_window must be HH:MM-HH:MM, always or never")
    if body.paused is not None:
        db.set_setting(conn, "worker_paused", "1" if body.paused else "0")
    if body.fetch_paused is not None:
        db.set_setting(conn, "fetch_paused", "1" if body.fetch_paused else "0")
    if body.reset_batch_window:
        conn.execute("DELETE FROM settings WHERE key='batch_window'")
        conn.commit()
    elif body.batch_window is not None:
        db.set_setting(conn, "batch_window", body.batch_window.strip().lower())
    return status_payload(conn)


class RetryFailedBody(BaseModel):
    include_skipped: bool = False


@api.post("/items/retry-failed")
def retry_failed(body: RetryFailedBody = RetryFailedBody(), conn=Depends(get_conn)):
    n = 0
    for stage, prev in PREV_STATUS.items():
        n += conn.execute("UPDATE items SET status=?, error=NULL, failed_stage=NULL, attempts=0, updated_at=?"
                          " WHERE status='failed' AND failed_stage=?", (prev, db.now(), stage)).rowcount
    n += conn.execute("UPDATE items SET status='queued', error=NULL, failed_stage=NULL, attempts=0, updated_at=?"
                      " WHERE status='failed' AND (failed_stage IS NULL OR failed_stage NOT IN (?, ?, ?, ?))",
                      (db.now(), *PREV_STATUS)).rowcount
    if body.include_skipped:
        n += conn.execute("UPDATE items SET status='queued', error=NULL, failed_stage=NULL, attempts=0, updated_at=?"
                          " WHERE status IN ('private', 'gone', 'needs_login')", (db.now(),)).rowcount
    conn.commit()
    return {"queued": n}


def events_after(conn, after_id, limit):
    if after_id is None:
        rows = conn.execute("SELECT * FROM (SELECT * FROM events ORDER BY id DESC LIMIT ?) ORDER BY id", (limit,))
    else:
        rows = conn.execute("SELECT * FROM events WHERE id > ? ORDER BY id LIMIT ?", (after_id, limit))
    return [db.event_dict(r) for r in rows]


@api.get("/events")
def list_events(after_id: Optional[int] = None, limit: int = Query(200, ge=1, le=1000), conn=Depends(get_conn)):
    events = events_after(conn, after_id, limit)
    return {"events": events, "last_id": events[-1]["id"] if events else (after_id or 0)}


REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class _KeepMethod(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            new.method = req.get_method()
        return new


urlopen = urllib.request.build_opener(_KeepMethod).open
_download = {"state": "idle", "repo": None, "file": None, "bytes": 0, "total": None, "error": None}
_download_lock = threading.Lock()


def gguf_name(name):
    return bool(SAFE_NAME.match(name)) and name.endswith(".gguf")


def current_models(conn):
    remote = conn.execute("SELECT llm_remote_model FROM worker_status WHERE id=1").fetchone()[0]
    return [f"local:{llm.active_file(conn)}", f"remote:{remote}" if remote else ""]


def models_payload(conn):
    settings.models_dir.mkdir(parents=True, exist_ok=True)
    active = llm.active_file(conn)
    files = sorted(p for p in settings.models_dir.iterdir()
                   if p.is_file() and p.name.endswith(".gguf") and "mmproj" not in p.name)
    with _download_lock:
        download = dict(_download)
    other = conn.execute("SELECT COUNT(*) FROM items WHERE status IN ('enriched', 'indexed')"
                         " AND (enriched_by IS NULL OR enriched_by NOT IN (?, ?))", current_models(conn)).fetchone()[0]
    return {"active": active, "default": llm.default_file(),
            "models": [{"file": p.name, "size_bytes": p.stat().st_size} for p in files],
            "download": download, "other_model_items": other}


class ActiveModelBody(BaseModel):
    file: str


class DownloadBody(BaseModel):
    repo: str
    file: str


class ReenrichBody(BaseModel):
    only_other_models: bool = True


@api.get("/models")
def get_models(conn=Depends(get_conn)):
    return models_payload(conn)


@api.put("/models/active")
def set_active_model(body: ActiveModelBody, conn=Depends(get_conn)):
    if not gguf_name(body.file):
        raise HTTPException(422, "file must be a .gguf file name")
    if not (settings.models_dir / body.file).is_file():
        raise HTTPException(404, "model file not found")
    db.set_setting(conn, "llm_model", body.file)
    return models_payload(conn)


def _run_download(url, part, final):
    try:
        with urlopen(urllib.request.Request(url), timeout=60) as res, open(part, "wb") as f:
            while True:
                chunk = res.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                with _download_lock:
                    _download["bytes"] += len(chunk)
        os.replace(part, final)
        with _download_lock:
            _download["state"] = "done"
    except Exception as e:
        log.exception("model download failed")
        part.unlink(missing_ok=True)
        with _download_lock:
            _download.update(state="error", error=f"{type(e).__name__}: {e}"[:500])


@api.post("/models/download", status_code=202)
def download_model(body: DownloadBody, conn=Depends(get_conn)):
    parts = body.file.split("/")
    name = parts[-1]
    if not REPO.match(body.repo) or ".." in body.repo.split("/"):
        raise HTTPException(422, "repo must look like owner/name")
    if not gguf_name(name) or not all(SAFE_NAME.match(p) for p in parts):
        raise HTTPException(422, "file must be a .gguf path inside the repo")
    settings.models_dir.mkdir(parents=True, exist_ok=True)
    final = settings.models_dir / name
    if final.exists():
        raise HTTPException(409, "a model with this file name already exists")
    with _download_lock:
        if _download["state"] == "downloading":
            raise HTTPException(409, "a download is already running")
    url = f"https://huggingface.co/{body.repo}/resolve/main/{body.file}"
    try:
        with urlopen(urllib.request.Request(url, method="HEAD"), timeout=30) as res:
            total = res.headers.get("Content-Length") or res.headers.get("X-Linked-Size")
    except urllib.error.HTTPError as e:
        raise HTTPException(404 if e.code in (401, 403, 404) else 502, f"Hugging Face answered {e.code} for this file")
    except (urllib.error.URLError, OSError) as e:
        raise HTTPException(502, f"could not reach Hugging Face: {e}")
    total = int(total) if total and total.isdigit() else None
    if total and shutil.disk_usage(settings.models_dir).free - total < settings.min_free_gb * 1024 ** 3:
        raise HTTPException(507, "not enough free disk space for this model")
    with _download_lock:
        if _download["state"] == "downloading":
            raise HTTPException(409, "a download is already running")
        _download.update(state="downloading", repo=body.repo, file=name, bytes=0, total=total, error=None)
    part = settings.models_dir / f"{name}.part"
    threading.Thread(target=_run_download, args=(url, part, final), daemon=True).start()
    return JSONResponse(models_payload(conn), status_code=202)


@api.delete("/models/{file}", status_code=204)
def delete_model(file: str, conn=Depends(get_conn)):
    path = settings.models_dir / file
    if not gguf_name(file) or not path.is_file():
        raise HTTPException(404, "model file not found")
    if file == llm.active_file(conn):
        raise HTTPException(409, "this is the active model; switch to another one first")
    path.unlink()
    return Response(status_code=204)


@api.post("/items/reenrich")
def reenrich(body: ReenrichBody = ReenrichBody(), conn=Depends(get_conn)):
    where, args = "status IN ('enriched', 'indexed')", []
    if body.only_other_models:
        where += " AND (enriched_by IS NULL OR enriched_by NOT IN (?, ?))"
        args += current_models(conn)
    n = conn.execute(f"UPDATE items SET status='extracted', source='export', error=NULL, failed_stage=NULL,"
                     f" attempts=0, updated_at=? WHERE {where}", [db.now(), *args]).rowcount
    conn.commit()
    return {"queued": n}


app.include_router(api)

SSE_POLL_S = 1.0
SSE_STATUS_EVERY = 5
SSE_MAX_POLLS = None


def sse(data, event=None, event_id=None):
    head = (f"id: {event_id}\n" if event_id is not None else "") + (f"event: {event}\n" if event else "")
    return f"{head}data: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.get("/api/events/stream")
async def event_stream(request: Request, t: Optional[str] = None, after_id: Optional[int] = None,
                       last_event_id: Optional[str] = Header(None)):
    if not token_ok(t or bearer(request.headers.get("authorization"))):
        raise HTTPException(401, "invalid or missing token")
    if last_event_id and last_event_id.isdigit():
        after_id = int(last_event_id)

    async def gen():
        conn = await asyncio.to_thread(db.connect)
        try:
            last = after_id
            first = await asyncio.to_thread(events_after, conn, last, 50 if last is None else 500)
            yield "retry: 3000\n\n"
            polls = 0
            batch = first
            while True:
                for e in batch:
                    yield sse(e, event_id=e["id"])
                    last = e["id"]
                if last is None:
                    last = 0
                if polls % SSE_STATUS_EVERY == 0:
                    yield sse(await asyncio.to_thread(status_payload, conn), "status")
                polls += 1
                if SSE_MAX_POLLS is not None and polls >= SSE_MAX_POLLS:
                    break
                await asyncio.sleep(SSE_POLL_S)
                if await request.is_disconnected():
                    break
                batch = await asyncio.to_thread(events_after, conn, last, 500)
        finally:
            conn.close()

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.api_route("/media/{shortcode}/{name}", methods=["GET", "HEAD"])
def media(shortcode: str, name: str, request: Request, t: Optional[str] = None):
    if not token_ok(t or bearer(request.headers.get("authorization"))):
        raise HTTPException(401, "invalid or missing token")
    if not (SAFE_NAME.match(shortcode) and SAFE_NAME.match(name)):
        raise HTTPException(404, "not found")
    path = settings.media_dir / shortcode / name
    if not path.is_file():
        raise HTTPException(404, "not found")
    size = path.stat().st_size
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=604800"}
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", (request.headers.get("range") or "").strip())
    if not m or not (m[1] or m[2]):
        return FileResponse(path, media_type=ctype, headers=headers)
    if m[1]:
        start, end = int(m[1]), min(int(m[2]) if m[2] else size - 1, size - 1)
    else:
        start, end = max(0, size - int(m[2])), size - 1
    if start > end or start >= size:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    length = end - start + 1
    headers.update({"Content-Range": f"bytes {start}-{end}/{size}", "Content-Length": str(length)})
    if request.method == "HEAD":
        return Response(status_code=206, headers=headers, media_type=ctype)

    def body():
        with open(path, "rb") as f:
            f.seek(start)
            left = length
            while left > 0:
                chunk = f.read(min(1 << 16, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk

    return StreamingResponse(body(), status_code=206, media_type=ctype, headers=headers)


@app.get("/{path:path}", include_in_schema=False)
def static(path: str):
    if path.split("/")[0] in ("api", "media"):
        raise HTTPException(404, "not found")
    web = settings.web_dir.resolve()
    if path:
        f = (web / path).resolve()
        if f.is_file() and f.is_relative_to(web):
            return FileResponse(f, headers={"Cache-Control": "no-cache"})
    index = web / "index.html"
    if index.is_file() and "." not in path.rsplit("/", 1)[-1]:
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    raise HTTPException(404, "web UI not found")
