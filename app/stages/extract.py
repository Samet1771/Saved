import json
import re
import tempfile
from pathlib import Path

from ..config import settings
from ..db import update_item
from ..ml import ocr as ocr_ml
from ..ml import whisper
from .fetch import item_dir, run

MODELS = [whisper, ocr_ml]


def keyframes(video, duration, dst_prefix, n):
    duration = duration or 0
    out = []
    for i in range(n):
        t = duration * (i + 0.5) / n if duration else 0
        dst = video.parent / f"{dst_prefix}{i}.jpg"
        run("ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1", "-q:v", "3", str(dst))
        if dst.exists():
            out.append(dst.name)
        if not duration:
            break
    return out


def sample_frames(video, dst_dir):
    """Frames at settings.vlm_fps, scaled to settings.vlm_frame_width; returns [(t, path)]."""
    dst_dir = Path(dst_dir)
    run("ffmpeg", "-y", "-v", "error", "-i", str(video), "-vf",
        f"fps={settings.vlm_fps:g},scale={settings.vlm_frame_width}:-2", "-q:v", "3", str(dst_dir / "%05d.jpg"))
    return [(i / settings.vlm_fps, f) for i, f in enumerate(sorted(dst_dir.glob("*.jpg")))]


def ocr_lines(frames):
    """[(t, path)] -> [[t, line]] with each distinct line at the time it first appears."""
    seen, out = set(), []
    for t, path in frames:
        for line in (ocr_ml.ocr(path) or "").splitlines():
            line = re.sub(r"\s+", " ", line).strip()
            key = re.sub(r"\W+", "", line.lower())
            if len(key) < 3 or key in seen:
                continue
            seen.add(key)
            out.append([round(t, 1) if t is not None else None, line])
    return out


def compute(item, d):
    """Run speech and OCR on the item's media in directory d; returns the fields to store (keyframes are written to d)."""
    d = Path(d)
    media = json.loads(item["media"]) if isinstance(item["media"], str) else item["media"]
    segments, lang, frames, lines = [], None, [], []
    for idx, m in enumerate(media):
        path = d / m["file"]
        if m["kind"] == "image":
            lines += [[None, x] for _, x in ocr_lines([(None, path)])]
            continue
        if m.get("audio"):
            segs, lang_ = whisper.transcribe(path)
            lang = lang or lang_
            segments += [{**s, "media": idx} for s in segs] if len(media) > 1 else segs
        frames += keyframes(path, m.get("duration"), f"kf{idx}_", settings.keyframes)
        with tempfile.TemporaryDirectory() as tmp:
            lines += ocr_lines(sample_frames(path, tmp))
    ocr_text = ocr_ml.dedupe_lines(x for _, x in lines)
    return {"transcript": segments, "transcript_text": " ".join(s["text"] for s in segments) or None, "lang": lang,
            "ocr_text": ocr_text or None, "ocr_lines": lines, "keyframes": frames}


def store(conn, item_id, fields):
    update_item(conn, item_id, status="extracted", error=None, failed_stage=None, **fields)


def process(conn, item):
    store(conn, item["id"], compute(item, item_dir(item["shortcode"])))
