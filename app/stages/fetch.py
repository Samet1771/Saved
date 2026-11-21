import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone

from ..config import settings
from ..db import update_item

YTDLP_FORMAT = "bv*[height<=720][vcodec^=avc]+ba[acodec^=mp4a]/b[height<=720]/b"
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic"}


class Terminal(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class RateLimited(Exception):
    pass


RATE_LIMIT = re.compile(r"rate-?limit|\b429\b|too many requests|please wait a few minutes", re.I)
RESTRICTED_MSG = "restricted: needs login"
NEEDS_LOGIN_MSG = "needs login: Instagram only serves photo posts to logged-in users; add burner cookies"


def first_line(message):
    return next((ln.strip() for ln in str(message).splitlines() if ln.strip()), "")


def classify(message, has_cookies=False):
    m = message.lower()
    if "available to everyone" in m or "certain audiences" in m:
        return "restricted"
    if "redirect to login page" in m:
        return None if has_cookies else "needs_login"
    if RATE_LIMIT.search(m) or ("login required" in m and "gallery-dl" not in m):
        return "rate_limited"
    if any(s in m for s in ("private", "login_required", "loginrequired", "authorizationerror", "checkpoint")):
        return "private"
    if any(s in m for s in ("not found", "404", "does not exist", "has been removed", "deleted", "no longer available")):
        return "gone"
    return None


def item_dir(shortcode):
    return settings.media_dir / shortcode


def disk_free_ok():
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(settings.media_dir).free >= settings.min_free_gb * 1024 ** 3


def run(*args):
    p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode:
        raise RuntimeError(f"{args[0]} failed: {(p.stderr or p.stdout)[-500:]}")
    return p.stdout


def probe(path):
    data = json.loads(run("ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)))
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    dur = data.get("format", {}).get("duration")
    return {
        "width": v.get("width"), "height": v.get("height"),
        "duration": round(float(dur), 2) if dur and dur != "N/A" else None,
        "audio": any(s.get("codec_type") == "audio" for s in streams),
    }


def make_thumb(src, dst, at=None):
    args = ["ffmpeg", "-y", "-v", "error"]
    if at:
        args += ["-ss", str(at)]
    run(*args, "-i", str(src), "-frames:v", "1", "-vf", "scale='min(480,iw)':-2", "-q:v", "4", str(dst))


def _iso(ts):
    if ts is None:
        return None
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts.replace(" ", "T")).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            return None
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ytdlp(url, d, cookiefile):
    import yt_dlp
    opts = {
        "quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True,
        "format": YTDLP_FORMAT, "merge_output_format": "mp4",
        "outtmpl": {"default": str(d / "0.%(ext)s"), "thumbnail": str(d / "thumb_src.%(ext)s")},
        "writethumbnail": True, "socket_timeout": 30, "retries": 3,
    }
    if cookiefile:
        opts["cookiefile"] = str(cookiefile)
    with yt_dlp.YoutubeDL(opts) as y:
        try:
            info = y.extract_info(url, download=False)
        except yt_dlp.utils.DownloadError as e:
            if "no video" in str(e).lower():
                return None
            raise
        if info.get("_type") == "playlist" or not (info.get("formats") or info.get("url")):
            return None
        y.process_ie_result(info, download=True)
    return info


def _gallery(url, d, cookiefile):
    out = d / "gdl"
    args = [sys.executable, "-m", "gallery_dl", "--write-metadata", "-o", "videos=true", "-D", str(out)]
    if cookiefile:
        args += ["--cookies", str(cookiefile)]
    p = subprocess.run([*args, url], capture_output=True, text=True, encoding="utf-8", errors="replace")
    files = sorted(f for f in out.glob("*") if f.suffix.lower() in VIDEO_EXT | IMAGE_EXT) if out.exists() else []
    if not files:
        raise RuntimeError(f"gallery-dl: {(p.stderr or p.stdout or 'no files')[-500:]}")
    metas = []
    for f in files:
        mf = f.with_name(f.name + ".json")
        metas.append(json.loads(mf.read_text(encoding="utf-8")) if mf.exists() else {})
    order = sorted(range(len(files)), key=lambda i: (metas[i].get("num") or 0, files[i].name))
    names = []
    for n, i in enumerate(order):
        name = f"{n}{files[i].suffix.lower()}"
        files[i].replace(d / name)
        names.append(name)
    shutil.rmtree(out, ignore_errors=True)
    return names, metas[order[0]]


def _fetch(item, d, cookiefile):
    url = item["url"]
    info = _ytdlp(url, d, cookiefile)
    media = []
    if info:
        video = next(f for f in sorted(d.glob("0.*")) if f.suffix.lower() in VIDEO_EXT)
        media.append({"kind": "video", "file": video.name, **probe(video)})
        m = media[0]
        is_reel = "/reel/" in url or ((m["height"] or 0) > (m["width"] or 0) and (m["duration"] or 999) <= 180)
        meta = {
            "type": "reel" if is_reel else "video",
            "author": info.get("channel") or info.get("uploader_id") or info.get("uploader"),
            "caption": info.get("description"),
            "posted_at": _iso(info.get("timestamp")),
        }
    else:
        names, gm = _gallery(url, d, cookiefile)
        for name in names:
            kind = "video" if (d / name).suffix in VIDEO_EXT else "image"
            media.append({"kind": kind, "file": name, **probe(d / name)})
        meta = {
            "type": "carousel" if len(media) > 1 else ("photo" if media[0]["kind"] == "image" else
                                                       "reel" if "/reel/" in url else "video"),
            "author": gm.get("username") or gm.get("owner_id"),
            "caption": gm.get("description"),
            "posted_at": _iso(gm.get("post_date") or gm.get("date")),
        }
    thumb_src = next(d.glob("thumb_src.*"), None)
    first = d / media[0]["file"]
    if thumb_src:
        make_thumb(thumb_src, d / "thumb.jpg")
        thumb_src.unlink()
    else:
        make_thumb(first, d / "thumb.jpg", at=min(1.0, (media[0]["duration"] or 0) / 2) if media[0]["kind"] == "video" else None)
    return meta, media


def process(conn, item):
    d = item_dir(item["shortcode"])
    has_cookies = settings.cookies_path.exists()
    attempts = [None] + ([settings.cookies_path] if has_cookies else [])
    last = None
    for cookiefile in attempts:
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)
        try:
            meta, media = _fetch(item, d, cookiefile)
            break
        except Exception as e:
            last = e
    else:
        shutil.rmtree(d, ignore_errors=True)
        status = classify(str(last), has_cookies)
        if status == "rate_limited":
            raise RateLimited(first_line(last)[:500]) from last
        if status == "restricted":
            raise Terminal("private", RESTRICTED_MSG)
        if status == "needs_login":
            raise Terminal("needs_login", NEEDS_LOGIN_MSG)
        if status:
            raise Terminal(status, str(last)[:500])
        raise last
    caption = meta["caption"] or ""
    update_item(
        conn, item["id"], status="fetched", type=meta["type"], author=meta["author"] or item["author"],
        caption=caption or None, hashtags=sorted(set(t.lower() for t in re.findall(r"#(\w+)", caption))),
        posted_at=meta["posted_at"], thumb="thumb.jpg", media=media, error=None, failed_stage=None,
    )
