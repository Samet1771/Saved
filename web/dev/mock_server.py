"""Stdlib-only mock of the Saved API (see API.md) for UI development.

Usage:  python web/dev/mock_server.py [--port 8765] [--token dev]
Serves web/ at /, the API at /api/*, and generated placeholder media at /media/*.
"""
import argparse
import copy
import email.parser
import json
import mimetypes
import random
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
WEB = HERE.parent
MEDIA = HERE / "media"

VIDEOS = [
    ("v0.mp4", "testsrc2=size=360x640:rate=30", 440, 6),
    ("v1.mp4", "smptehdbars=size=360x640:rate=30", 523, 5),
    ("v2.mp4", "mandelbrot=size=360x640:rate=30", 659, 8),
    ("v3.mp4", "life=size=360x640:rate=15:mold=10:ratio=0.2:death_color=#1a1030:life_color=#ff7a59", 330, 7),
    ("v4.mp4", "cellauto=size=360x640:rate=30:rule=110", 392, 5),
    ("v5.mp4", "testsrc2=size=640x360:rate=30", 587, 6),  # landscape
]
IMAGES = [
    ("i0.jpg", "gradients=size=720x1280:seed=1:c0=0x7b2ff7:c1=0xf107a3"),
    ("i1.jpg", "gradients=size=720x1280:seed=2:c0=0x00c9a7:c1=0x003f5c"),
    ("i2.jpg", "gradients=size=1080x1080:seed=3:c0=0xffb347:c1=0xff5e62"),
    ("i3.jpg", "gradients=size=720x900:seed=4:c0=0x2193b0:c1=0x6dd5ed"),
    ("i4.jpg", "gradients=size=720x1280:seed=5:c0=0x232526:c1=0x9aa5b1"),
    ("i5.jpg", "gradients=size=1080x1080:seed=6:c0=0x8e2de2:c1=0x4a00e0"),
]


def run(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def generate_media():
    MEDIA.mkdir(parents=True, exist_ok=True)
    if not shutil.which("ffmpeg"):
        print("ffmpeg not found; media will 404", file=sys.stderr)
        return
    for name, src, freq, dur in VIDEOS:
        out = MEDIA / name
        if not out.exists():
            print("generating", name)
            run(["ffmpeg", "-y", "-f", "lavfi", "-i", src, "-f", "lavfi", "-i", f"sine=frequency={freq}",
                 "-t", str(dur), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "30",
                 "-c:a", "aac", "-movflags", "+faststart", str(out)])
        thumb = MEDIA / name.replace(".mp4", "_thumb.jpg")
        if not thumb.exists():
            run(["ffmpeg", "-y", "-ss", "1", "-i", str(out), "-frames:v", "1", "-q:v", "4", str(thumb)])
    for name, src in IMAGES:
        out = MEDIA / name
        if not out.exists():
            print("generating", name)
            run(["ffmpeg", "-y", "-f", "lavfi", "-i", src, "-frames:v", "1", "-q:v", "4", str(out)])


def probe_duration(name):
    for n, _, _, d in VIDEOS:
        if n == name:
            return float(d)
    return None


# ---------------------------------------------------------------- fake data
CATEGORIES = ["cooking", "fitness", "travel", "tech", "comedy", "music", "diy", "science"]
PROPOSED = [{"name": "gardening", "count": 3}, {"name": "finance", "count": 2}]
TAGS = {
    "cooking": ["pasta", "recipe", "quick", "vegan", "dessert", "turkish"],
    "fitness": ["workout", "mobility", "core", "running"],
    "travel": ["istanbul", "japan", "hiking", "beach"],
    "tech": ["homelab", "linux", "python", "keyboard"],
    "comedy": ["sketch", "standup", "pets"],
    "music": ["guitar", "piano", "cover"],
    "diy": ["woodworking", "3dprint", "cnc", "repair"],
    "science": ["physics", "space", "biology"],
}
AUTHORS = ["chef.maria", "liftwithlena", "nomad.kaan", "devdaily", "funnybones", "pianoparker",
           "makerspace.ist", "spacefacts", "the_quiet_cook", "urban.trails"]
WORDS = ("this is how you make the best version at home with only a few simple steps and no special "
         "tools needed just patience and a bit of practice save this for later and share it").split()
STATUSES = ["indexed"] * 26 + ["queued"] * 4 + ["fetched", "extracted", "enriched"] + ["failed"] * 3 + ["private", "gone"] + ["needs_login", "indexed"]

lock = threading.Lock()
DB = {}


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_db():
    rnd = random.Random(7)
    now = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
    items = {}
    for i in range(1, 41):
        status = STATUSES[i - 1]
        cat = CATEGORIES[i % len(CATEGORIES)]
        if i % 9 == 0:
            typ = "carousel"
        elif i % 7 == 0:
            typ = "photo"
        elif i % 11 == 0:
            typ = "video"
        else:
            typ = "reel"
        sc = "C" + "".join(rnd.choice("ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz0123456789") for _ in range(10))
        tags = rnd.sample(TAGS[cat], k=min(3, len(TAGS[cat])))
        caption = f"{cat.title()} idea #{i}: " + " ".join(rnd.choice(WORDS) for _ in range(rnd.randint(12, 60))) + " #" + " #".join(tags)
        media = []
        if status not in ("queued", "private", "gone") or i == 38:
            if typ == "carousel":
                for k in range(rnd.randint(3, 5)):
                    if k % 2 == 1:
                        v = VIDEOS[(i + k) % 5][0]
                        media.append({"kind": "video", "file": v, "width": 360, "height": 640, "duration": probe_duration(v)})
                    else:
                        im = IMAGES[(i + k) % len(IMAGES)][0]
                        media.append({"kind": "image", "file": im, "width": 720, "height": 1280})
            elif typ == "photo":
                im = IMAGES[i % len(IMAGES)][0]
                media.append({"kind": "image", "file": im, "width": 1080, "height": 1080})
            elif typ == "video":
                media.append({"kind": "video", "file": "v5.mp4", "width": 640, "height": 360, "duration": 6.0})
            else:
                v = VIDEOS[i % 5][0]
                media.append({"kind": "video", "file": v, "width": 360, "height": 640, "duration": probe_duration(v)})
        thumb = None
        if media:
            first = media[0]
            thumb = first["file"].replace(".mp4", "_thumb.jpg") if first["kind"] == "video" else first["file"]
        transcript = []
        dur = next((m["duration"] for m in media if m["kind"] == "video"), None)
        if dur and status == "indexed":
            t = 0.0
            while t < dur - 0.5:
                end = min(dur, t + rnd.uniform(1.0, 2.0))
                transcript.append({"start": round(t, 2), "end": round(end, 2),
                                   "text": " ".join(rnd.choice(WORDS) for _ in range(rnd.randint(4, 9))).capitalize() + "."})
                t = end
        saved = now - timedelta(days=i * 3, hours=rnd.randint(0, 23))
        items[i] = {
            "id": i, "shortcode": sc, "url": f"https://www.instagram.com/{'p' if typ in ('photo', 'carousel') else 'reel'}/{sc}/",
            "type": typ, "status": status, "author": AUTHORS[i % len(AUTHORS)],
            "caption_full": caption,
            "summary": (f"A short {cat} clip about {tags[0]}." if status in ("enriched", "indexed") else None),
            "category": cat if status in ("enriched", "indexed") else None,
            "tags": tags if status in ("enriched", "indexed") else [],
            "thumb": thumb, "media_raw": media,
            "saved_at": iso(saved), "posted_at": iso(saved - timedelta(days=rnd.randint(0, 30))),
            "transcript": transcript,
            "ocr_text": ("STEP 1  MIX\nSTEP 2  BAKE 20 MIN" if i % 3 == 0 and status == "indexed" else ""),
            "lang": "tr" if i % 6 == 0 else "en",
            "error": ({"failed": "fetch: HTTP 429 from instagram.com", "private": "account is private",
                       "gone": "post deleted (404)"}.get(status)),
            "manual": {"category": False, "tags": False},
            "source": "ingest" if i % 13 == 0 else "export",
            "enriched_by": ((OLD_MODEL if i % 4 == 0 else DEFAULT_MODEL) if status in ("enriched", "indexed") else None),
        }
    cols = {
        1: {"id": 1, "name": "Recipes", "items": [i for i in items if items[i]["category"] == "cooking"] + [5, 9]},
        2: {"id": 2, "name": "Workshop", "items": [i for i in items if items[i]["category"] == "diy"]},
        3: {"id": 3, "name": "Trip ideas", "items": [i for i in items if items[i]["category"] == "travel"]},
        4: {"id": 4, "name": "Watch later", "items": [2, 3, 7, 11, 19, 23, 31]},
    }
    for c in cols.values():
        c["items"] = list(dict.fromkeys(c["items"]))
    manual = sorted(items, key=lambda k: items[k]["saved_at"], reverse=True)
    DB.update(items=items, collections=cols, manual=manual, next_col=5,
              categories=list(CATEGORIES), proposed=list(PROPOSED), log=[], events=[], next_event=1,
              models={"active": DEFAULT_MODEL, "files": {DEFAULT_MODEL: 2_740_000_000, OLD_MODEL: 2_490_000_000,
                                                         "Qwen3.5-9B-Q4_K_M.gguf": 5_680_000_000},
                      "download": {"state": "idle", "repo": None, "file": None, "bytes": 0, "total": None, "error": None},
                      "started": 0.0})


DEFAULT_MODEL = "Qwen3.5-4B-Q4_K_M.gguf"
OLD_MODEL = "gemma-3-4b-it-Q4_K_M.gguf"
GGUF = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*\.gguf$")
FAKE_SPEED = 400_000_000


def models_payload():
    m = DB["models"]
    d = m["download"]
    if d["state"] == "downloading":
        d["bytes"] = min(d["total"], int((time.time() - m["started"]) * FAKE_SPEED))
        if d["file"].startswith("fail"):
            if d["bytes"] > d["total"] // 3:
                d.update(state="error", error="HTTPError: HTTP Error 500: fake failure")
        elif d["bytes"] >= d["total"]:
            d["state"] = "done"
            m["files"][d["file"]] = d["total"]
    other = sum(1 for it in DB["items"].values()
                if it["status"] in ("enriched", "indexed") and it["enriched_by"] != m["active"])
    return {"active": m["active"], "default": DEFAULT_MODEL,
            "models": [{"file": f, "size_bytes": n} for f, n in sorted(m["files"].items())],
            "download": dict(d), "other_model_items": other}


def media_url(item, file):
    return f"/media/{item['shortcode']}/{file}"


def summary(item, score=None):
    cols = [{"id": c["id"], "name": c["name"]} for c in DB["collections"].values() if item["id"] in c["items"]]
    out = {
        "id": item["id"], "shortcode": item["shortcode"], "url": item["url"], "type": item["type"],
        "status": item["status"], "author": item["author"], "caption": item["caption_full"][:200],
        "summary": item["summary"], "category": item["category"], "tags": list(item["tags"]),
        "thumb_url": media_url(item, item["thumb"]) if item["thumb"] else None,
        "media": [{k: v for k, v in dict(m, url=media_url(item, m["file"])).items() if k != "file"} for m in item["media_raw"]],
        "saved_at": item["saved_at"], "posted_at": item["posted_at"], "collections": cols,
    }
    if score is not None:
        out["score"] = round(score, 4)
    return out


def detail(item):
    out = summary(item)
    out.update(caption=item["caption_full"], transcript=item["transcript"], ocr_text=item["ocr_text"],
               lang=item["lang"], error=item["error"], manual=item["manual"], enriched_by=item["enriched_by"])
    return out


def collection_out(c):
    cover = None
    for iid in c["items"]:
        it = DB["items"].get(iid)
        if it and it["thumb"]:
            cover = media_url(it, it["thumb"])
            break
    return {"id": c["id"], "name": c["name"], "count": len(c["items"]), "cover_url": cover}


def list_items(qs):
    g = lambda k: (qs.get(k) or [None])[0]
    items = list(DB["items"].values())
    q = (g("q") or "").strip().lower()
    scores = {}
    if q:
        terms = [t for t in re.split(r"\W+", q) if t]
        for it in items:
            hay = " ".join([it["caption_full"], it["author"], it["category"] or "", " ".join(it["tags"]),
                            " ".join(s["text"] for s in it["transcript"])]).lower()
            s = sum(hay.count(t) for t in terms)
            if s:
                scores[it["id"]] = s / (60 + s)
        items = [it for it in items if it["id"] in scores]
    for key in ("category", "author", "type", "status"):
        v = g(key)
        if v:
            items = [it for it in items if (it[key] or "") == v]
    if g("tag"):
        items = [it for it in items if g("tag") in it["tags"]]
    col = None
    if g("collection"):
        col = DB["collections"].get(int(g("collection")))
        if not col:
            return None
        items = [it for it in items if it["id"] in col["items"]]
    sort = g("sort") or ("relevance" if q else "saved_desc")
    if sort == "relevance" and q:
        items.sort(key=lambda it: -scores[it["id"]])
    elif sort == "saved_asc":
        items.sort(key=lambda it: it["saved_at"])
    elif sort == "posted_desc":
        items.sort(key=lambda it: it["posted_at"], reverse=True)
    elif sort == "manual":
        order = col["items"] if col else DB["manual"]
        pos = {iid: n for n, iid in enumerate(order)}
        items.sort(key=lambda it: pos.get(it["id"], 1e9))
    else:
        items.sort(key=lambda it: it["saved_at"], reverse=True)
    offset = int(g("offset") or 0)
    limit = int(g("limit") or 60)
    page = items[offset:offset + limit]
    return {"total": len(items), "items": [summary(it, scores.get(it["id"]) if q else None) for it in page]}


def facets():
    from collections import Counter
    items = DB["items"].values()
    cat = Counter(it["category"] for it in items if it["category"])
    tag = Counter(t for it in items for t in it["tags"])
    auth = Counter(it["author"] for it in items)
    typ = Counter(it["type"] for it in items)
    f = lambda c, n: [{"name": k, "count": v} for k, v in c.most_common(n)]
    return {"categories": f(cat, 100), "tags": f(tag, 200), "authors": f(auth, 100), "types": f(typ, 10),
            "collections": [{"id": c["id"], "name": c["name"], "count": len(c["items"])} for c in DB["collections"].values()]}


EVENT_STAGES = ["fetch", "extract", "enrich", "index"]


def add_event(stage, message, level="info", item=None, duration=None):
    e = {"id": DB["next_event"], "at": iso(datetime.now(timezone.utc)), "level": level, "stage": stage,
         "item_id": item["id"] if item else None, "shortcode": item["shortcode"] if item else None,
         "message": message, "duration_s": duration}
    DB["next_event"] += 1
    DB["events"] = (DB["events"] + [e])[-2000:]
    return e


def fake_event(rnd):
    item = rnd.choice(list(DB["items"].values()))
    stage = rnd.choice(EVENT_STAGES)
    roll = rnd.random()
    if roll < 0.08:
        return add_event(stage, f"failed: RuntimeError: {stage} exploded on frame {rnd.randint(1, 99)}", "error", item,
                         round(rnd.uniform(1, 9), 2))
    if roll < 0.15:
        return add_event("fetch", "private: restricted: needs login", "warn", item, round(rnd.uniform(1, 4), 2))
    if roll < 0.18:
        return add_event(stage, f"batch {stage}: {rnd.randint(40, 900)} items")
    msg = {"fetch": f"fetch done: reel, {rnd.uniform(5, 60):.1f} s video",
           "extract": f"extract done: {rnd.uniform(5, 60):.1f} s audio, lang {rnd.choice(['en', 'tr'])}",
           "enrich": f"enrich done: {item['category'] or 'cooking'} · {', '.join(item['tags'][:3]) or 'pasta, recipe'}",
           "index": f"index done: {rnd.randint(1, 5)} frames"}[stage]
    dur = {"fetch": 6, "extract": 40, "enrich": 12, "index": 3}[stage] * rnd.uniform(0.5, 1.6)
    return add_event(stage, msg, "info", item, round(dur, 2))


def event_feeder():
    rnd = random.Random()
    while True:
        time.sleep(1)
        with lock:
            fake_event(rnd)


def progress(counts):
    order = ["queued", "fetched", "extracted", "enriched", "indexed"]
    total = sum(v for k, v in counts.items() if k not in ("private", "gone", "needs_login"))
    out = {}
    for i, stage in enumerate(EVENT_STAGES):
        durs = [e["duration_s"] for e in DB["events"] if e["stage"] == stage and e["item_id"] and e["duration_s"]][-50:]
        out[stage] = {"done": sum(counts.get(s, 0) for s in order[i + 1:]), "total": total,
                      "remaining": sum(counts.get(s, 0) for s in order[:i + 1]),
                      "done_last_hour": 12 + 7 * i, "avg_s": round(sum(durs) / len(durs), 2) if durs else None}
    return out


def status():
    from collections import Counter
    counts = Counter(it["status"] for it in DB["items"].values())
    for s in ("queued", "fetched", "extracted", "enriched", "indexed", "failed", "needs_login", "private", "gone"):
        counts.setdefault(s, 0)
    prog = progress(counts)
    eta = None
    if not any(p["remaining"] and p["avg_s"] is None for p in prog.values()):
        eta = round(max(sum(p["remaining"] * (p["avg_s"] or 0) for p in prog.values()), prog["fetch"]["remaining"] * 36))
    return {
        "counts": dict(counts),
        "disk": {"free_bytes": 21_400_000_000, "total_bytes": 58_000_000_000, "media_bytes": 3_900_000_000, "paused": False},
        "worker": {"stage": "enrich", "item_id": 31, "shortcode": DB["items"][31]["shortcode"],
                   "since": iso(datetime.now(timezone.utc) - timedelta(seconds=42)), "in_batch_window": True,
                   "llm_model": DB["models"]["active"], "fetch_backoff_until": DB.get("backoff_until")},
        "progress": prog, "eta_seconds": eta,
        "last_errors": [{"id": it["id"], "shortcode": it["shortcode"], "status": it["status"], "error": it["error"]}
                        for it in DB["items"].values() if it["status"] in ("failed", "needs_login", "private", "gone")],
    }


class Handler(SimpleHTTPRequestHandler):
    token = "dev"
    delay = 0.0

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(WEB), **kw)

    def log_message(self, fmt, *args):
        if self.path.startswith("/api"):
            sys.stdout.write("%s %s\n" % (self.command, self.path))
            sys.stdout.flush()

    # --------------------------------------------------------------- helpers
    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_empty(self, code=204):
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def err(self, code, msg):
        self.send_json({"detail": msg}, code)

    def body_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            return json.loads(raw or b"{}")
        except ValueError:
            return None

    def authed(self, qs):
        h = self.headers.get("Authorization", "")
        if h == f"Bearer {self.token}":
            return True
        return (qs.get("t") or [None])[0] == self.token

    # --------------------------------------------------------------- routing
    def do_GET(self):
        self.route("GET")

    def do_HEAD(self):
        self.route("HEAD")

    def do_POST(self):
        self.route("POST")

    def do_PUT(self):
        self.route("PUT")

    def do_PATCH(self):
        self.route("PATCH")

    def do_DELETE(self):
        self.route("DELETE")

    def route(self, method):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        if u.path == "/api/events/stream" and method == "GET":
            if not self.authed(qs):
                return self.err(401, "invalid token")
            return self.serve_events(qs)
        if u.path.startswith("/api/"):
            if not self.authed({}):
                return self.err(401, "invalid token")
            if self.delay:
                time.sleep(self.delay)
            with lock:
                DB["log"].append({"method": method, "path": self.path})
                return self.api(method, u.path, qs)
        if u.path.startswith("/media/"):
            if not self.authed(qs):
                return self.err(401, "invalid token")
            return self.serve_media(u.path.rsplit("/", 1)[-1], method == "HEAD")
        if u.path.startswith("/dev/media") or u.path.startswith("/dev/mock"):
            return self.err(404, "not found")
        if method in ("GET", "HEAD"):
            return super().do_GET() if method == "GET" else super().do_HEAD()
        return self.err(405, "method not allowed")

    def end_headers(self):
        if not self.path.startswith(("/api/", "/media/")):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def serve_events(self, qs):
        last = self.headers.get("Last-Event-ID") or (qs.get("after_id") or [None])[0]
        last = int(last) if last and str(last).isdigit() else None
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def frame(data, event=None, eid=None):
            head = (f"id: {eid}\n" if eid is not None else "") + (f"event: {event}\n" if event else "")
            return f"{head}data: {json.dumps(data)}\n\n"
        polls = 0
        try:
            self.wfile.write(b"retry: 3000\n\n")
            while True:
                with lock:
                    evs = DB["events"][-50:] if last is None else [e for e in DB["events"] if e["id"] > last]
                    st = status() if polls % 5 == 0 else None
                out = "".join(frame(e, eid=e["id"]) for e in evs)
                if evs:
                    last = evs[-1]["id"]
                elif last is None:
                    last = 0
                if st:
                    out += frame(st, "status")
                if out:
                    self.wfile.write(out.encode())
                    self.wfile.flush()
                polls += 1
                time.sleep(1)
        except OSError:
            pass

    def serve_media(self, name, head):
        f = MEDIA / name
        if not re.fullmatch(r"[\w.]+", name) or not f.is_file():
            return self.err(404, "no such media")
        size = f.stat().st_size
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        code = 200
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if not m:
                return self.err(416, "bad range")
            if m.group(1):
                start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
            else:
                start = max(0, size - int(m.group(2)))
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            code = 206
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "private, max-age=3600")
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if head:
            return
        with open(f, "rb") as fh:
            fh.seek(start)
            remaining = end - start + 1
            try:
                while remaining > 0:
                    chunk = fh.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
                pass

    def api(self, method, path, qs):
        parts = path.strip("/").split("/")[1:]
        items, cols = DB["items"], DB["collections"]

        if parts == ["items"] and method == "GET":
            res = list_items(qs)
            return self.send_json(res) if res is not None else self.err(404, "no such collection")
        if parts == ["items", "reenrich"] and method == "POST":
            only_other = (self.body_json() or {}).get("only_other_models", True)
            n = 0
            for it in items.values():
                if it["status"] in ("enriched", "indexed") and (not only_other or it["enriched_by"] != DB["models"]["active"]):
                    it["status"] = "extracted"
                    n += 1
            return self.send_json({"queued": n})
        if parts and parts[0] == "models":
            m = DB["models"]
            if parts == ["models"] and method == "GET":
                return self.send_json(models_payload())
            if parts == ["models", "active"] and method == "PUT":
                f = (self.body_json() or {}).get("file") or ""
                if not GGUF.match(f):
                    return self.err(422, "file must be a .gguf file name")
                if f not in m["files"]:
                    return self.err(404, "model file not found")
                m["active"] = f
                return self.send_json(models_payload())
            if parts == ["models", "download"] and method == "POST":
                b = self.body_json() or {}
                repo, path = str(b.get("repo") or ""), str(b.get("file") or "")
                name = path.split("/")[-1]
                if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
                    return self.err(422, "repo must look like owner/name")
                if not GGUF.match(name) or not all(re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", x) for x in path.split("/")):
                    return self.err(422, "file must be a .gguf path inside the repo")
                if name in m["files"]:
                    return self.err(409, "a model with this file name already exists")
                if models_payload()["download"]["state"] == "downloading":
                    return self.err(409, "a download is already running")
                if "huge" in name:
                    return self.err(507, "not enough free disk space for this model")
                m["download"] = {"state": "downloading", "repo": repo, "file": name, "bytes": 0,
                                 "total": 2_700_000_000, "error": None}
                m["started"] = time.time()
                return self.send_json(models_payload(), 202)
            if len(parts) == 2 and method == "DELETE":
                if parts[1] not in m["files"]:
                    return self.err(404, "model file not found")
                if parts[1] == m["active"]:
                    return self.err(409, "this is the active model; switch to another one first")
                del m["files"][parts[1]]
                return self.send_empty()
        if len(parts) >= 2 and parts[0] == "items":
            try:
                it = items[int(parts[1])]
            except (ValueError, KeyError):
                return self.err(404, "no such item")
            if len(parts) == 2 and method == "GET":
                return self.send_json(detail(it))
            if len(parts) == 2 and method == "PATCH":
                b = self.body_json()
                if b is None:
                    return self.err(422, "invalid json")
                if "category" in b:
                    it["category"] = b["category"] or None
                    it["manual"]["category"] = True
                if "tags" in b:
                    if not isinstance(b["tags"], list):
                        return self.err(422, "tags must be a list")
                    it["tags"] = [str(t).strip() for t in b["tags"] if str(t).strip()]
                    it["manual"]["tags"] = True
                return self.send_json(detail(it))
            if len(parts) == 2 and method == "DELETE":
                del items[it["id"]]
                DB["manual"].remove(it["id"])
                for c in cols.values():
                    if it["id"] in c["items"]:
                        c["items"].remove(it["id"])
                return self.send_empty()
            if parts[2:] == ["retry"] and method == "POST":
                if it["status"] not in ("failed", "needs_login", "private", "gone"):
                    return self.err(409, "item is not failed")
                it["status"], it["error"] = "queued", None
                return self.send_json(detail(it))
        if parts == ["facets"] and method == "GET":
            return self.send_json(facets())
        if parts == ["categories"]:
            if method == "PUT":
                b = self.body_json() or {}
                cats = b.get("categories")
                if not isinstance(cats, list):
                    return self.err(422, "categories must be a list")
                DB["categories"] = list(dict.fromkeys(str(c).strip().lower() for c in cats if str(c).strip()))
                DB["proposed"] = [p for p in DB["proposed"] if p["name"] not in DB["categories"]]
            return self.send_json({"categories": DB["categories"], "proposed": DB["proposed"]})
        if parts == ["collections"]:
            if method == "GET":
                return self.send_json([collection_out(c) for c in cols.values()])
            if method == "POST":
                name = ((self.body_json() or {}).get("name") or "").strip()
                if not name:
                    return self.err(422, "name required")
                if any(c["name"].lower() == name.lower() for c in cols.values()):
                    return self.err(409, "collection exists")
                cid = DB["next_col"]
                DB["next_col"] += 1
                cols[cid] = {"id": cid, "name": name, "items": []}
                return self.send_json(collection_out(cols[cid]), 201)
        if len(parts) >= 2 and parts[0] == "collections":
            c = cols.get(int(parts[1])) if parts[1].isdigit() else None
            if not c:
                return self.err(404, "no such collection")
            if len(parts) == 2 and method == "PATCH":
                name = ((self.body_json() or {}).get("name") or "").strip()
                if not name:
                    return self.err(422, "name required")
                c["name"] = name
                return self.send_json(collection_out(c))
            if len(parts) == 2 and method == "DELETE":
                del cols[c["id"]]
                return self.send_empty()
            if parts[2:] == ["items"] and method == "POST":
                ids = (self.body_json() or {}).get("item_ids") or []
                for iid in ids:
                    if iid in items and iid not in c["items"]:
                        c["items"].append(iid)
                return self.send_json(collection_out(c))
            if len(parts) == 4 and parts[2] == "items" and method == "DELETE":
                iid = int(parts[3])
                if iid in c["items"]:
                    c["items"].remove(iid)
                return self.send_empty()
        if parts == ["order", "move"] and method == "POST":
            b = self.body_json() or {}
            iid, after, cid = b.get("item_id"), b.get("after_id"), b.get("collection_id")
            order = cols[cid]["items"] if cid is not None and cid in cols else DB["manual"]
            if iid not in order or (after is not None and after not in order):
                return self.err(422, "item not in this order")
            order.remove(iid)
            order.insert(0 if after is None else order.index(after) + 1, iid)
            return self.send_empty()
        if parts == ["ingest"] and method == "POST":
            url = ((self.body_json() or {}).get("url") or "")
            m = re.search(r"instagram\.com/(?:p|reel|reels|tv)/([\w-]+)", url)
            if not m:
                return self.err(422, "not an instagram post url")
            for it in items.values():
                if it["shortcode"] == m.group(1):
                    return self.send_json({"id": it["id"], "status": it["status"], "created": False}, 200)
            iid = max(items) + 1
            items[iid] = {"id": iid, "shortcode": m.group(1), "url": url.split("?")[0], "type": "reel", "status": "queued",
                          "author": "unknown", "caption_full": "", "summary": None, "category": None, "tags": [],
                          "thumb": None, "media_raw": [], "saved_at": iso(datetime.now(timezone.utc)), "posted_at": None,
                          "transcript": [], "ocr_text": "", "lang": None, "error": None,
                          "manual": {"category": False, "tags": False}, "source": "ingest", "enriched_by": None}
            DB["manual"].insert(0, iid)
            return self.send_json({"id": iid, "status": "queued", "created": True}, 201)
        if parts == ["import"] and method == "POST":
            ctype = self.headers.get("Content-Type", "")
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n)
            if "multipart/form-data" not in ctype:
                return self.err(422, "multipart required")
            msg = email.parser.BytesParser().parsebytes(b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + raw)
            files = [p for p in msg.walk() if p.get_param("name", header="content-disposition") == "file"]
            if not files:
                return self.err(422, "field 'file' missing")
            return self.send_json({"added": 3, "existing": 37, "collections": 4})
        if parts == ["status"] and method == "GET":
            return self.send_json(status())
        if parts == ["events"] and method == "GET":
            limit = int((qs.get("limit") or ["200"])[0])
            after = (qs.get("after_id") or [None])[0]
            evs = DB["events"][-limit:] if after is None else [e for e in DB["events"] if e["id"] > int(after)][:limit]
            return self.send_json({"events": evs, "last_id": evs[-1]["id"] if evs else int(after or 0)})
        return self.err(404, "not found")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--token", default="dev")
    ap.add_argument("--delay", type=float, default=0.0, help="seconds of artificial API latency")
    ap.add_argument("--backoff", action="store_true", help="pretend fetching is paused by rate limiting")
    a = ap.parse_args()
    generate_media()
    build_db()
    rnd = random.Random(3)
    for _ in range(80):
        fake_event(rnd)
    if a.backoff:
        DB["backoff_until"] = iso(datetime.now(timezone.utc) + timedelta(minutes=27))
        add_event("fetch", "backoff: pausing fetch for 30 min", "warn", duration=1800)
    threading.Thread(target=event_feeder, daemon=True).start()
    Handler.token, Handler.delay = a.token, a.delay
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"Saved mock on http://{a.host}:{a.port}/  token={a.token}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
