import json
import re
import tempfile
from pathlib import Path

from ..config import settings
from ..db import get_categories, update_item
from ..ml import llm
from .extract import sample_frames
from .fetch import item_dir

MODELS = [llm]


class Waiting(Exception):
    pass


def clean_tags(tags):
    out = []
    for t in tags or []:
        t = re.sub(r"\s+", " ", str(t).strip().lstrip("#").lower())
        if t and t not in out and len(t) <= 40:
            out.append(t)
    return out[:8]


def apply_result(conn, item, result, model=None):
    categories = get_categories(conn)
    lookup = {c.lower(): c for c in categories}
    raw = str(result.get("category") or "").strip()
    category = lookup.get(raw.lower())
    if category is None:
        category = "other"
        if raw and raw.lower() != "other":
            conn.execute("INSERT INTO proposed_categories(name, count) VALUES (?, 1)"
                         " ON CONFLICT(name) DO UPDATE SET count=count+1", (raw.lower(),))
    details = {k: result[k] for k in ("timeline", "visual") if result.get(k)}
    fields = {"status": "enriched", "summary": (result.get("summary") or "").strip() or None,
              "description": (result.get("description") or "").strip() or None,
              "details": details or None, "error": None, "failed_stage": None, "enriched_by": model}
    if not item["manual_category"]:
        fields["category"] = category
    if not item["manual_tags"]:
        fields["tags"] = clean_tags(result.get("tags"))
    update_item(conn, item["id"], **fields)


def frames(item, tmp):
    """[(label, path)]: videos at settings.vlm_fps with timestamps, photos as-is."""
    d = item_dir(item["shortcode"])
    media = json.loads(item["media"] or "[]")
    multi = len(media) > 1
    out = []
    for idx, m in enumerate(media):
        path = d / m["file"]
        if not path.is_file():
            continue
        if m["kind"] == "image":
            out.append((f"photo {idx + 1}", path))
            continue
        sub = tmp / str(idx)
        sub.mkdir()
        prefix = f"video {idx + 1}, " if multi else ""
        out += [(f"{prefix}t={t:.1f}s", f) for t, f in sample_frames(path, sub)]
    return out


def prompt_text(item, categories, nframes):
    media = json.loads(item["media"] or "[]")
    duration = sum(m.get("duration") or 0 for m in media if m["kind"] == "video")
    segments = json.loads(item["transcript"] or "[]")
    if not any(m.get("audio") for m in media):
        speech = "Speech transcript (automatic):\n(no audio)"
    else:
        body = "\n".join(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in segments) or "(no speech detected)"
        speech = f"Speech transcript (automatic, language {item['lang']}):\n{body}"
    lines = json.loads(item["ocr_lines"] or "[]")
    if lines:
        ocr = "\n".join(f"[{t:.1f}] {x}" if t is not None else f"[photo] {x}" for t, x in lines)
    else:
        ocr = item["ocr_text"] or "(none)"
    head = f"Video length: {duration:.1f} s, {nframes} frames." if duration else f"{nframes} photos."
    return (f"{head}\n{speech}\n\n"
            f"On-screen text (automatic OCR, first-seen time):\n{ocr}\n\n"
            f"Categories: {', '.join(categories)}, other")


def process(conn, item):
    local_ok = settings.llm_local_fallback and item["source"] == "ingest"
    if not llm.remote_healthy() and not local_ok:
        raise Waiting("remote GPU unreachable, waiting for it")
    categories = get_categories(conn)
    with tempfile.TemporaryDirectory() as tmp:
        pics = llm.pick_frames(frames(item, Path(tmp)))
        text = prompt_text(item, categories, len(pics))
        if llm.remote_healthy():
            try:
                result = llm.enrich(text, pics, "remote")
                return apply_result(conn, item, result, llm.enriched_by("remote"))
            except llm.RemoteError:
                llm.mark_remote_down()
                if not local_ok:
                    raise Waiting("remote GPU failed, waiting for it")
        result = llm.enrich(text, pics, "local")
        apply_result(conn, item, result, llm.enriched_by("local"))
