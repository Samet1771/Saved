import base64
import io
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from .. import db
from ..config import settings

LABEL = "local llama-server"
SERVER_BIN = "/opt/llama/llama-server"
LOCAL_PORT = 8091
HEALTH_TTL = 30
HEALTH_TIMEOUT = 2
START_TIMEOUT = 180
REMOTE_TIMEOUT = 300
LOCAL_TIMEOUT = 600

clock = time.monotonic
_remote = {"healthy": False, "checked_at": None, "at": None, "model": None}
_proc = None
_path = None
text_only = False
loads, load_s = 0, None

SYSTEM = """You analyse short social-media videos (Instagram reels) for a personal search archive.
You get frames sampled at 2 per second, each labelled with its timestamp, and an automatic speech transcript and automatic on-screen text (OCR)
(automatic speech recognition, which can mishear slang, names, lyrics and music; where they disagree, judge which
is more plausible from the frames and context, and trust readable on-screen text over the transcripts).
Rules:
- Describe only what is visible or audible. Never invent names, places, brands or events. Name a person only if the
  name is shown on screen or clearly spoken.
- Timeline: split the video into its distinct moments (scene changes, actions, reveals, punchlines, and for
  talking videos each new point the speaker makes), in order, with start/end seconds from the frame timestamps.
  Each event says what is seen AND, if someone speaks, what they say (paraphrased in English).
- Description: what the video is about, including the content of the speech (who says what, paraphrased in
  English), not just what it looks like.
- Visual extraction: list what is in the video concretely (people and what they look like/do, objects, setting,
  on-screen text exactly as written, format such as split-screen/meme/screen recording/vlog).
- If the video is a joke or meme, explain the joke in the description.
- Write everything in English, but quote on-screen text and speech in their original language.
- Tags: 3-8 short lowercase English topic tags. Category: one from the given list, or "other"."""

SCHEMA = {
    "type": "object",
    "properties": {
        "timeline": {"type": "array", "minItems": 1, "maxItems": 15, "items": {
            "type": "object",
            "properties": {"start": {"type": "number"}, "end": {"type": "number"},
                           "event": {"type": "string", "maxLength": 300}},
            "required": ["start", "end", "event"]}},
        "visual": {"type": "object", "properties": {
            "setting": {"type": "string", "maxLength": 200},
            "people": {"type": "array", "items": {"type": "string", "maxLength": 150}, "maxItems": 8},
            "objects": {"type": "array", "items": {"type": "string", "maxLength": 80}, "maxItems": 15},
            "on_screen_text": {"type": "array", "items": {"type": "string", "maxLength": 200}, "maxItems": 15},
            "format": {"type": "string", "maxLength": 100},
            "audio": {"type": "string", "maxLength": 200}},
            "required": ["setting", "people", "objects", "on_screen_text", "format", "audio"]},
        "description": {"type": "string", "maxLength": 900},
        "category": {"type": "string", "maxLength": 40},
        "tags": {"type": "array", "items": {"type": "string", "maxLength": 30}, "minItems": 3, "maxItems": 8},
        "summary": {"type": "string", "maxLength": 250},
    },
    "required": ["timeline", "visual", "description", "category", "tags", "summary"],
}


class RemoteError(Exception):
    pass


def default_file():
    return os.path.basename(settings.llm_model_path)


def model_path(conn=None):
    own = conn is None
    conn = conn or db.connect()
    try:
        name = db.get_setting(conn, "llm_model")
    finally:
        if own:
            conn.close()
    if name and (settings.models_dir / name).is_file():
        return str(settings.models_dir / name)
    return settings.llm_model_path


def active_file(conn=None):
    return os.path.basename(model_path(conn))


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _call(base, path, body=None, key=None, timeout=HEALTH_TIMEOUT):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, headers=headers, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.loads(res.read() or b"null")


def _served_model():
    try:
        data = _call(settings.llm_remote_url, "/v1/models", key=settings.llm_remote_key)
        name = data["data"][0]["id"]
    except Exception:
        return None
    return name.replace("\\", "/").rsplit("/", 1)[-1]


def remote_healthy(force=False):
    if not settings.llm_remote_url:
        _remote.update(healthy=False, checked_at=None, at=None, model=None)
        return False
    if not force and _remote["at"] is not None and clock() - _remote["at"] < HEALTH_TTL:
        return _remote["healthy"]
    try:
        _call(settings.llm_remote_url, "/health", key=settings.llm_remote_key)
        healthy = True
    except Exception:
        healthy = False
    if healthy and (not _remote["healthy"] or not _remote["model"]):
        _remote["model"] = _served_model()
    _remote.update(healthy=healthy, checked_at=_now(), at=clock())
    return healthy


def mark_remote_down():
    _remote.update(healthy=False, checked_at=_now(), at=clock())


def remote_state():
    return {"url": settings.llm_remote_url or None, "healthy": _remote["healthy"],
            "checked_at": _remote["checked_at"], "model": _remote["model"]}


def enriched_by(backend):
    if backend == "remote":
        return f"remote:{_remote['model'] or 'unknown'}"
    return f"local:{loaded_file()}"


def loaded_file():
    return os.path.basename(_path) if _proc is not None else None


def loaded():
    return _proc is not None


def _local_url():
    return f"http://127.0.0.1:{LOCAL_PORT}"


def load():
    global _proc, _path, text_only, loads, load_s
    path = model_path()
    if _proc is not None and (path != _path or _proc.poll() is not None):
        unload()
    if _proc is not None:
        return
    t = time.perf_counter()
    mmproj = settings.llm_mmproj_path
    text_only = not os.path.isfile(mmproj)
    cmd = [SERVER_BIN, "-m", path, *([] if text_only else ["--mmproj", mmproj]), "-t", str(settings.threads),
           "-c", str(settings.llm_ctx), "-np", "1", "--host", "127.0.0.1", "--port", str(LOCAL_PORT), "--no-webui"]
    log = open(settings.data_dir / "llama-server.log", "wb")
    try:
        _proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
    finally:
        log.close()
    _path = path
    deadline = time.perf_counter() + START_TIMEOUT
    while True:
        code = _proc.poll()
        if code is not None:
            unload()
            raise RuntimeError(f"llama-server exited with code {code} while loading (see llama-server.log)")
        try:
            _call(_local_url(), "/health")
            break
        except Exception:
            if time.perf_counter() > deadline:
                unload()
                raise RuntimeError(f"llama-server did not become healthy within {START_TIMEOUT} s")
            time.sleep(0.5)
    loads, load_s = loads + 1, time.perf_counter() - t


def unload():
    global _proc, _path
    proc, _proc, _path = _proc, None, None
    if proc is None:
        return
    proc.terminate()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def can_enrich():
    return remote_healthy() or settings.llm_local_fallback


def pick_frames(frames, limit=None):
    limit = limit or settings.vlm_max_frames
    if len(frames) <= limit:
        return list(frames)
    step = -(-len(frames) // limit)
    return list(frames[::step])


def encode_image(path):
    from PIL import Image
    width = settings.vlm_frame_width
    with Image.open(path) as im:
        im = im.convert("RGB")
        if im.width > width:
            im = im.resize((width, max(1, round(im.height * width / im.width))), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def request_body(text, frames):
    content = []
    for label, path in frames:
        content.append({"type": "text", "text": label})
        content.append({"type": "image_url", "image_url": {"url": encode_image(path)}})
    content.append({"type": "text", "text": text})
    return {
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}],
        "temperature": 0.2, "max_tokens": 3000,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {"type": "json_schema", "json_schema": {"name": "reel", "schema": SCHEMA}},
    }


def enrich(text, frames, backend):
    if backend == "local":
        load()
        base, key, timeout = _local_url(), None, LOCAL_TIMEOUT
        frames = [] if text_only else frames
    else:
        base, key, timeout = settings.llm_remote_url, settings.llm_remote_key, REMOTE_TIMEOUT
    body = request_body(text, pick_frames(frames))
    try:
        res = _call(base, "/v1/chat/completions", body, key, timeout)
    except urllib.error.HTTPError as e:
        if backend == "remote" and (e.code >= 500 or e.code in (401, 403, 404)):
            raise RemoteError(f"HTTP {e.code}") from e
        raise
    except (urllib.error.URLError, OSError) as e:
        if backend == "remote":
            raise RemoteError(str(e)) from e
        raise
    return json.loads(res["choices"][0]["message"]["content"])
