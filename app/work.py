"""Work API for hybrid mode: a PC agent claims items, computes a stage on its GPU and posts the results back."""
import json
import re
from datetime import datetime, timedelta, timezone
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from . import db
from .config import settings
from .stages import enrich, extract, index
from .stages.fetch import item_dir

router = APIRouter(prefix="/work")

STAGE_FROM = {"extract": "fetched", "enrich": "extracted", "index": "enriched"}
JSON_COLS = ("media", "transcript", "ocr_lines", "keyframes", "tags", "details", "hashtags")
ITEM_COLS = ("id", "shortcode", "source", "type", "status", "url", "author", "caption", "lang", "transcript_text",
             "ocr_text", "summary", "description", "category") + JSON_COLS
KEYFRAME = re.compile(r"^kf\d+_\d+\.jpg$")
MAX_KEYFRAME_BYTES = 2 * 1024 * 1024


def get_conn():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def item_payload(row):
    out = {}
    for k in ITEM_COLS:
        v = row[k]
        out[k] = json.loads(v) if k in JSON_COLS and v else ([] if k in ("media", "keyframes", "tags") else v)
    return out


def seen(conn, agent, info=None):
    db.set_setting(conn, "agent_name", agent[:80])
    db.set_setting(conn, "agent_seen", db.now())
    if info is not None:
        db.set_setting(conn, "agent_info", json.dumps(info)[:4000])


def agent_status(conn):
    name, at = db.get_setting(conn, "agent_name"), db.get_setting(conn, "agent_seen")
    online = False
    if at:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(at.replace("Z", "+00:00"))).total_seconds()
        online = age < settings.agent_timeout_s
    info = db.get_setting(conn, "agent_info")
    leased = conn.execute("SELECT COUNT(*) FROM items WHERE lease_until > ?", (db.now(),)).fetchone()[0]
    return {"name": name, "seen": at, "online": online, "info": json.loads(info) if info else None, "leased": leased}


def _leased(conn, item_id, stage):
    row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(404, "item not found")
    if row["status"] != STAGE_FROM[stage]:
        raise HTTPException(409, f"item is {row['status']}, not {STAGE_FROM[stage]}")
    return row


def _release(conn, item_id):
    conn.execute("UPDATE items SET lease_until=NULL, leased_by=NULL WHERE id=?", (item_id,))


def _done(conn, stage, item_id, duration):
    from .worker import done_message
    row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    db.add_event(conn, stage, done_message(stage, row) + " (PC)", "info", row, duration)
    conn.commit()


class ClaimBody(BaseModel):
    agent: str
    stage: Literal["extract", "enrich", "index"]
    limit: int = 1
    info: Optional[dict] = None


@router.post("/claim")
def claim(body: ClaimBody, conn=Depends(get_conn)):
    if settings.compute_mode != "hybrid":
        raise HTTPException(409, "the server is not in hybrid mode (COMPUTE_MODE=hybrid)")
    seen(conn, body.agent, body.info)
    if db.controls(conn)["worker_paused"]:
        return {"items": [], "paused": True}
    now = datetime.now(timezone.utc)
    until = _iso(now + timedelta(seconds=settings.work_lease_s))
    conn.execute("BEGIN IMMEDIATE")
    rows = conn.execute(
        "SELECT * FROM items WHERE status=? AND (lease_until IS NULL OR lease_until < ?)"
        " ORDER BY source='ingest' DESC, id LIMIT ?",
        (STAGE_FROM[body.stage], _iso(now), max(1, min(body.limit, 50)))).fetchall()
    conn.executemany("UPDATE items SET lease_until=?, leased_by=? WHERE id=?", [(until, body.agent, r["id"]) for r in rows])
    conn.commit()
    out = {"items": [item_payload(r) for r in rows], "lease_until": until}
    if body.stage == "enrich":
        out["categories"] = db.get_categories(conn)
    return out


class HeartbeatBody(BaseModel):
    agent: str
    info: Optional[dict] = None


@router.post("/heartbeat")
def heartbeat(body: HeartbeatBody, conn=Depends(get_conn)):
    seen(conn, body.agent, body.info)
    return {"mode": settings.compute_mode, "paused": db.controls(conn)["worker_paused"]}


class ReleaseBody(BaseModel):
    ids: List[int]


@router.post("/release")
def release(body: ReleaseBody, conn=Depends(get_conn)):
    conn.executemany("UPDATE items SET lease_until=NULL, leased_by=NULL WHERE id=?", [(i,) for i in body.ids])
    conn.commit()
    return {"released": len(body.ids)}


@router.post("/{item_id}/extract")
async def put_extract(item_id: int, fields: str = Form(...), duration_s: Optional[float] = Form(None),
                      files: List[UploadFile] = File(default=[]), conn=Depends(get_conn)):
    row = _leased(conn, item_id, "extract")
    try:
        data = json.loads(fields)
    except ValueError:
        raise HTTPException(422, "fields must be JSON")
    allowed = {"transcript", "transcript_text", "lang", "ocr_text", "ocr_lines", "keyframes"}
    if set(data) - allowed:
        raise HTTPException(422, f"unknown fields: {sorted(set(data) - allowed)}")
    names = [f.filename for f in files]
    if any(not KEYFRAME.match(n or "") for n in names) or set(data.get("keyframes") or []) - set(names):
        raise HTTPException(422, "keyframes must be uploaded as kf<media>_<n>.jpg files")
    d = item_dir(row["shortcode"])
    d.mkdir(parents=True, exist_ok=True)
    for f in files:
        blob = await f.read(MAX_KEYFRAME_BYTES + 1)
        if len(blob) > MAX_KEYFRAME_BYTES:
            raise HTTPException(413, f"{f.filename} is too large")
        (d / f.filename).write_bytes(blob)
    extract.store(conn, item_id, {k: data.get(k) for k in allowed})
    _release(conn, item_id)
    _done(conn, "extract", item_id, duration_s)
    return {"status": "extracted"}


class EnrichBody(BaseModel):
    result: dict
    model: Optional[str] = None
    duration_s: Optional[float] = None


@router.post("/{item_id}/enrich")
def put_enrich(item_id: int, body: EnrichBody, conn=Depends(get_conn)):
    row = _leased(conn, item_id, "enrich")
    enrich.apply_result(conn, row, body.result, body.model)
    _release(conn, item_id)
    _done(conn, "enrich", item_id, body.duration_s)
    return {"status": "enriched"}


class IndexBody(BaseModel):
    text_vec: Optional[List[float]] = None
    visual: List[List[float]] = []
    duration_s: Optional[float] = None


@router.post("/{item_id}/index")
def put_index(item_id: int, body: IndexBody, conn=Depends(get_conn)):
    _leased(conn, item_id, "index")
    if body.text_vec is not None and len(body.text_vec) != db.TEXT_DIM:
        raise HTTPException(422, f"text_vec must have {db.TEXT_DIM} values")
    if any(len(v) != db.VISUAL_DIM for v in body.visual):
        raise HTTPException(422, f"visual vectors must have {db.VISUAL_DIM} values")
    index.store(conn, item_id, body.text_vec, body.visual)
    _release(conn, item_id)
    _done(conn, "index", item_id, body.duration_s)
    return {"status": "indexed"}


class FailBody(BaseModel):
    stage: Literal["extract", "enrich", "index"]
    error: str
    duration_s: Optional[float] = None


@router.post("/{item_id}/fail")
def put_fail(item_id: int, body: FailBody, conn=Depends(get_conn)):
    row = _leased(conn, item_id, body.stage)
    db.update_item(conn, item_id, status="failed", failed_stage=body.stage, error=f"PC: {body.error}"[:1000], attempts=0)
    _release(conn, item_id)
    db.add_event(conn, body.stage, f"failed on the PC: {body.error.splitlines()[0][:200] if body.error else ''}",
                 "error", row, body.duration_s)
    conn.commit()
    return {"status": "failed"}
