"""Insert fake, fully indexed demo items with generated media so the web UI has data.

Usage:  DATA_DIR=./data-demo python scripts/seed_demo.py [--reset]
"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.config import settings  # noqa: E402
from app.stages.extract import keyframes  # noqa: E402
from app.stages.fetch import make_thumb, probe, run  # noqa: E402

LOREM = ("Step one, boil the water and salt it well. Step two, cook the pasta two minutes less than the box says. "
         "Step three, toss it with the sauce and a splash of pasta water.")

ITEMS = [
    dict(sc="DEMOreel01", type="reel", media=[("video", "testsrc2", 720, 1280, 8)], author="chef_anna",
         category="cooking", tags=["pasta", "recipe", "italian", "quick dinner"],
         caption="15 minute carbonara 🍝 no cream, ever. #pasta #recipe #italianfood",
         summary="A quick carbonara recipe made without cream.", transcript=LOREM, lang="en",
         ocr="NO CREAM\nREAL CARBONARA", collections=["Recipes"]),
    dict(sc="DEMOreel02", type="reel", media=[("video", "mandelbrot", 720, 1280, 6)], author="gezgin.ali",
         category="travel", tags=["istanbul", "bosphorus", "ferry", "sunset"],
         caption="Boğaz'da gün batımı vapur keyfi #istanbul #travel", lang="tr",
         summary="A sunset ferry ride on the Bosphorus in Istanbul.",
         transcript="Bugün vapurla karşıya geçiyoruz, gün batımı harika.", ocr="KADIKÖY - KARAKÖY",
         collections=["Travel"]),
    dict(sc="DEMOphoto1", type="photo", media=[("image", "color=c=0x2d6a4f", 1080, 1350, 0)], author="plantlady",
         category="home & interior", tags=["plants", "monstera", "care tips"],
         caption="Monstera care cheat sheet: bright indirect light, water when top 5 cm is dry.",
         summary="A care cheat sheet for monstera house plants.", ocr="BRIGHT INDIRECT LIGHT\nWATER WEEKLY",
         collections=[]),
    dict(sc="DEMOcarou1", type="carousel", author="design.daily",
         media=[("image", "color=c=0xe76f51", 1080, 1080, 0), ("image", "color=c=0x264653", 1080, 1080, 0),
                ("video", "testsrc", 1080, 1080, 5)],
         category="art & design", tags=["typography", "color palette", "branding"],
         caption="5 color palettes for your next brand. Swipe →", summary="Five color palettes for branding projects.",
         ocr="PALETTE 1\nPALETTE 2", collections=["Travel"]),
    dict(sc="DEMOreel03", type="reel", media=[("video", "smptebars", 720, 1280, 7)], author="liftwithme",
         category="fitness", tags=["workout", "home workout", "core"],
         caption="10 min core workout, no equipment #fitness", summary="A ten-minute core workout without equipment.",
         transcript="Plank for thirty seconds, then side plank, then dead bugs.", lang="en", collections=[]),
    dict(sc="DEMOvideo1", type="video", media=[("video", "rgbtestsrc", 1280, 720, 6)], author="techtalks",
         category="tech", tags=["linux", "homelab", "docker"],
         caption="Why I run everything in Docker on my home server", summary="A video about running a homelab with Docker.",
         transcript="Containers make it easy to rebuild the whole server from a compose file.", lang="en",
         collections=[]),
    dict(sc="DEMOqueued", status="queued", type="reel", media=[], author="someone", collections=[]),
    dict(sc="DEMOfailed", status="failed", type="reel", media=[], author="someone_else", error="RuntimeError: demo failure",
         failed_stage="fetch", collections=[]),
]


def gen(src, w, h, dur, dst):
    if dur:
        run("ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"{src}=size={w}x{h}:rate=30", "-t", str(dur),
            "-f", "lavfi", "-i", f"sine=frequency=330:duration={dur}", "-t", str(dur), "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-preset", "veryfast", "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(dst))
    else:
        run("ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"{src}:size={w}x{h}", "-frames:v", "1", str(dst))


def main():
    if "--reset" in sys.argv and settings.db_path.exists():
        for p in (settings.db_path, Path(f"{settings.db_path}-wal"), Path(f"{settings.db_path}-shm")):
            p.unlink(missing_ok=True)
    conn = db.open_db()
    for i, it in enumerate(ITEMS):
        if conn.execute("SELECT 1 FROM items WHERE shortcode=?", (it["sc"],)).fetchone():
            continue
        d = settings.media_dir / it["sc"]
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
        media, frames = [], []
        for n, (kind, src, w, h, dur) in enumerate(it["media"]):
            name = f"{n}.mp4" if kind == "video" else f"{n}.jpg"
            gen(src, w, h, dur, d / name)
            media.append({"kind": kind, "file": name, **probe(d / name)})
            if kind == "video":
                frames += keyframes(d / name, media[-1]["duration"], f"kf{n}_", 3)
        if media:
            make_thumb(d / media[0]["file"], d / "thumb.jpg", at=1 if media[0]["kind"] == "video" else None)
        transcript = []
        if it.get("transcript"):
            words = it["transcript"].split(". ")
            transcript = [{"start": k * 2.5, "end": k * 2.5 + 2.4, "text": s.strip()} for k, s in enumerate(words)]
        ts = db.now()
        cur = conn.execute(
            "INSERT INTO items(shortcode, url, type, status, source, author, caption, hashtags, posted_at, saved_at, thumb,"
            " media, keyframes, transcript, transcript_text, lang, ocr_text, category, tags, summary, error, failed_stage,"
            " manual_pos, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (it["sc"], f"https://www.instagram.com/{'p' if it['type'] in ('photo', 'carousel') else 'reel'}/{it['sc']}/",
             it["type"], it.get("status", "indexed"), "export", it["author"], it.get("caption"),
             json.dumps([t.strip("#").lower() for t in (it.get("caption") or "").split() if t.startswith("#")]),
             f"2025-0{1 + i % 9}-1{i}T10:00:00Z", f"2025-0{2 + i % 8}-0{1 + i}T12:00:00Z",
             "thumb.jpg" if media else None, json.dumps(media), json.dumps(frames),
             json.dumps(transcript), it.get("transcript"), it.get("lang"), it.get("ocr"), it.get("category"),
             json.dumps(it.get("tags", [])), it.get("summary"), it.get("error"), it.get("failed_stage"), i, ts, ts))
        for name in it["collections"]:
            conn.execute("INSERT OR IGNORE INTO collections(name, created_at) VALUES (?, ?)", (name, ts))
            cid = conn.execute("SELECT id FROM collections WHERE name=?", (name,)).fetchone()[0]
            conn.execute("INSERT OR IGNORE INTO collection_items(collection_id, item_id, pos) VALUES"
                         " (?, ?, (SELECT COALESCE(MAX(pos), 0) + 1 FROM collection_items WHERE collection_id=?))",
                         (cid, cur.lastrowid, cid))
        conn.commit()
        print("seeded", it["sc"])
    conn.execute("INSERT OR IGNORE INTO proposed_categories(name, count) VALUES ('gardening', 2)")
    conn.commit()
    print(f"done: {settings.db_path}")


if __name__ == "__main__":
    main()
