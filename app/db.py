import json
import logging
import sqlite3
from datetime import datetime, timezone

from .config import settings

log = logging.getLogger("db")

TEXT_DIMS = {"BAAI/bge-m3": 1024, "google/embeddinggemma-2": 768}
TEXT_DIM = settings.text_dim or TEXT_DIMS.get(settings.text_model, 1024)
VISUAL_DIM = 768

DEFAULT_CATEGORIES = [
    "cooking", "fitness", "travel", "humor", "music", "art & design", "tech", "science & education",
    "diy & crafts", "fashion & beauty", "home & interior", "animals", "sports", "finance & business",
    "motivation & self-improvement", "language learning",
]

MIGRATIONS = [
    """
    CREATE TABLE items (
        id INTEGER PRIMARY KEY,
        shortcode TEXT NOT NULL UNIQUE,
        url TEXT NOT NULL,
        type TEXT,
        status TEXT NOT NULL DEFAULT 'queued',
        source TEXT NOT NULL DEFAULT 'export',
        author TEXT,
        caption TEXT,
        hashtags TEXT NOT NULL DEFAULT '[]',
        posted_at TEXT,
        saved_at TEXT,
        thumb TEXT,
        media TEXT NOT NULL DEFAULT '[]',
        keyframes TEXT NOT NULL DEFAULT '[]',
        transcript TEXT NOT NULL DEFAULT '[]',
        transcript_text TEXT,
        lang TEXT,
        ocr_text TEXT,
        category TEXT,
        tags TEXT NOT NULL DEFAULT '[]',
        summary TEXT,
        manual_category INTEGER NOT NULL DEFAULT 0,
        manual_tags INTEGER NOT NULL DEFAULT 0,
        error TEXT,
        failed_stage TEXT,
        attempts INTEGER NOT NULL DEFAULT 0,
        manual_pos REAL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX items_status ON items(status, source);
    CREATE INDEX items_saved ON items(saved_at);
    CREATE TABLE collections (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL
    );
    CREATE TABLE collection_items (
        collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
        item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
        pos REAL NOT NULL,
        PRIMARY KEY (collection_id, item_id)
    );
    CREATE INDEX collection_items_item ON collection_items(item_id);
    CREATE TABLE categories (name TEXT PRIMARY KEY, pos INTEGER NOT NULL);
    CREATE TABLE proposed_categories (name TEXT PRIMARY KEY, count INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE worker_status (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        stage TEXT NOT NULL DEFAULT 'idle',
        item_id INTEGER,
        since TEXT,
        in_batch_window INTEGER NOT NULL DEFAULT 0,
        paused INTEGER NOT NULL DEFAULT 0,
        heartbeat TEXT
    );
    INSERT INTO worker_status (id) VALUES (1);
    CREATE VIRTUAL TABLE items_fts USING fts5(
        caption, transcript_text, ocr_text, summary, tags, author,
        content='items', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
    );
    CREATE TRIGGER items_ai AFTER INSERT ON items BEGIN
        INSERT INTO items_fts(rowid, caption, transcript_text, ocr_text, summary, tags, author)
        VALUES (new.id, new.caption, new.transcript_text, new.ocr_text, new.summary, new.tags, new.author);
    END;
    CREATE TRIGGER items_ad AFTER DELETE ON items BEGIN
        INSERT INTO items_fts(items_fts, rowid, caption, transcript_text, ocr_text, summary, tags, author)
        VALUES ('delete', old.id, old.caption, old.transcript_text, old.ocr_text, old.summary, old.tags, old.author);
    END;
    CREATE TRIGGER items_au AFTER UPDATE OF caption, transcript_text, ocr_text, summary, tags, author ON items BEGIN
        INSERT INTO items_fts(items_fts, rowid, caption, transcript_text, ocr_text, summary, tags, author)
        VALUES ('delete', old.id, old.caption, old.transcript_text, old.ocr_text, old.summary, old.tags, old.author);
        INSERT INTO items_fts(rowid, caption, transcript_text, ocr_text, summary, tags, author)
        VALUES (new.id, new.caption, new.transcript_text, new.ocr_text, new.summary, new.tags, new.author);
    END;
    """,
    """
    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
    ALTER TABLE items ADD COLUMN enriched_by TEXT;
    """,
    """
    CREATE TABLE events (
        id INTEGER PRIMARY KEY,
        at TEXT NOT NULL,
        level TEXT NOT NULL DEFAULT 'info',
        stage TEXT,
        item_id INTEGER,
        shortcode TEXT,
        message TEXT NOT NULL,
        duration_s REAL
    );
    CREATE INDEX events_stage ON events(stage, id);
    ALTER TABLE items ADD COLUMN rate_limited INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE worker_status ADD COLUMN fetch_backoff_until TEXT;
    """,
    """
    ALTER TABLE items ADD COLUMN description TEXT;
    ALTER TABLE worker_status ADD COLUMN llm_backend TEXT;
    ALTER TABLE worker_status ADD COLUMN llm_remote_healthy INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE worker_status ADD COLUMN llm_remote_checked_at TEXT;
    ALTER TABLE worker_status ADD COLUMN llm_remote_model TEXT;
    DROP TRIGGER items_ai;
    DROP TRIGGER items_ad;
    DROP TRIGGER items_au;
    DROP TABLE items_fts;
    CREATE VIRTUAL TABLE items_fts USING fts5(
        caption, transcript_text, ocr_text, summary, description, tags, author,
        content='items', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
    );
    CREATE TRIGGER items_ai AFTER INSERT ON items BEGIN
        INSERT INTO items_fts(rowid, caption, transcript_text, ocr_text, summary, description, tags, author)
        VALUES (new.id, new.caption, new.transcript_text, new.ocr_text, new.summary, new.description, new.tags,
                new.author);
    END;
    CREATE TRIGGER items_ad AFTER DELETE ON items BEGIN
        INSERT INTO items_fts(items_fts, rowid, caption, transcript_text, ocr_text, summary, description, tags, author)
        VALUES ('delete', old.id, old.caption, old.transcript_text, old.ocr_text, old.summary, old.description,
                old.tags, old.author);
    END;
    CREATE TRIGGER items_au AFTER UPDATE OF caption, transcript_text, ocr_text, summary, description, tags, author
    ON items BEGIN
        INSERT INTO items_fts(items_fts, rowid, caption, transcript_text, ocr_text, summary, description, tags, author)
        VALUES ('delete', old.id, old.caption, old.transcript_text, old.ocr_text, old.summary, old.description,
                old.tags, old.author);
        INSERT INTO items_fts(rowid, caption, transcript_text, ocr_text, summary, description, tags, author)
        VALUES (new.id, new.caption, new.transcript_text, new.ocr_text, new.summary, new.description, new.tags,
                new.author);
    END;
    INSERT INTO items_fts(items_fts) VALUES ('rebuild');
    """,
    """
    ALTER TABLE items ADD COLUMN ocr_lines TEXT;
    ALTER TABLE items ADD COLUMN details TEXT;
    """,
    """
    ALTER TABLE worker_status ADD COLUMN rss_mb INTEGER;
    """,
    """
    CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, label TEXT, created_at TEXT NOT NULL);
    """,
    """
    ALTER TABLE items ADD COLUMN lease_until TEXT;
    ALTER TABLE items ADD COLUMN leased_by TEXT;
    """,
]

EVENTS_KEEP = 2000

def vec_tables():
    return [
        f"CREATE VIRTUAL TABLE IF NOT EXISTS text_vec USING vec0(item_id INTEGER PRIMARY KEY, embedding float[{TEXT_DIM}] distance_metric=cosine)",
        f"CREATE VIRTUAL TABLE IF NOT EXISTS visual_vec USING vec0(embedding float[{VISUAL_DIM}] distance_metric=cosine, item_id INTEGER)",
    ]


def _sync_text_model(conn):
    """Recreate text_vec and re-index items when the text embedder changed since they were embedded."""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE name='text_vec'").fetchone()
    stored = get_setting(conn, "text_embed_model")
    if row and (f"float[{TEXT_DIM}]" not in row[0] or (stored and stored != settings.text_model)):
        conn.execute("DROP TABLE text_vec")
        n = conn.execute("UPDATE items SET status='enriched' WHERE status='indexed'").rowcount
        log.warning("text embedder changed to %s: %d items will be re-indexed", settings.text_model, n)
    conn.execute(vec_tables()[0])
    if stored != settings.text_model:
        conn.execute("INSERT INTO settings(key, value) VALUES ('text_embed_model', ?)"
                     " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (settings.text_model,))

vec_available = None


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_vec(conn):
    global vec_available
    if vec_available is False:
        return False
    try:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        vec_available = True
    except Exception as e:
        if vec_available is None:
            log.warning("sqlite-vec unavailable, vector search disabled: %s", e)
        vec_available = False
    return vec_available


def connect(path=None):
    conn = sqlite3.connect(str(path or settings.db_path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    _load_vec(conn)
    return conn


def init(conn):
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for i, sql in enumerate(MIGRATIONS[version:], start=version + 1):
        conn.executescript("BEGIN;" + sql + f";PRAGMA user_version={i};COMMIT;")
    if vec_available:
        _sync_text_model(conn)
        conn.execute(vec_tables()[1])
    if not conn.execute("SELECT 1 FROM categories LIMIT 1").fetchone() and version == 0:
        conn.executemany("INSERT INTO categories(name, pos) VALUES (?, ?)", [(c, i) for i, c in enumerate(DEFAULT_CATEGORIES)])
    conn.commit()


def open_db(path=None):
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    init(conn)
    return conn


def has_vec(conn):
    return bool(vec_available) and conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='text_vec'").fetchone() is not None


def get_categories(conn):
    return [r[0] for r in conn.execute("SELECT name FROM categories ORDER BY pos")]


def get_setting(conn, key):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def set_setting(conn, key, value):
    conn.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, value))
    conn.commit()


def update_item(conn, item_id, **fields):
    fields["updated_at"] = now()
    for k, v in list(fields.items()):
        if isinstance(v, (list, dict)):
            fields[k] = json.dumps(v, ensure_ascii=False)
    cols = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE items SET {cols} WHERE id=?", [*fields.values(), item_id])
    conn.commit()


def rss_mb():
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return None


CONTROL_KEYS = ("worker_paused", "fetch_paused", "batch_window")


def controls(conn):
    """User overrides from the desktop app/API; batch_window None means the .env default."""
    vals = dict(conn.execute(f"SELECT key, value FROM settings WHERE key IN ({','.join('?' * len(CONTROL_KEYS))})",
                             CONTROL_KEYS).fetchall())
    return {"worker_paused": vals.get("worker_paused") == "1", "fetch_paused": vals.get("fetch_paused") == "1",
            "batch_window": vals.get("batch_window") or None}


def set_worker(conn, stage, item_id=None, in_window=None, paused=None):
    row = conn.execute("SELECT stage, item_id FROM worker_status WHERE id=1").fetchone()
    since = now() if (row["stage"], row["item_id"]) != (stage, item_id) else None
    sets, args = ["stage=?", "item_id=?", "heartbeat=?", "rss_mb=?"], [stage, item_id, now(), rss_mb()]
    if since:
        sets.append("since=?"); args.append(since)
    if in_window is not None:
        sets.append("in_batch_window=?"); args.append(int(in_window))
    if paused is not None:
        sets.append("paused=?"); args.append(int(paused))
    conn.execute(f"UPDATE worker_status SET {', '.join(sets)} WHERE id=1", args)
    conn.commit()


def set_fetch_backoff(conn, until):
    conn.execute("UPDATE worker_status SET fetch_backoff_until=? WHERE id=1", (until,))
    conn.commit()


def set_llm_status(conn, healthy, checked_at, model, backend):
    conn.execute("UPDATE worker_status SET llm_remote_healthy=?, llm_remote_checked_at=?, llm_remote_model=?,"
                 " llm_backend=? WHERE id=1", (int(healthy), checked_at, model, backend))
    conn.commit()


def add_event(conn, stage, message, level="info", item=None, duration=None):
    item_id, shortcode = (item["id"], item["shortcode"]) if item is not None else (None, None)
    cur = conn.execute(
        "INSERT INTO events(at, level, stage, item_id, shortcode, message, duration_s) VALUES (?,?,?,?,?,?,?)",
        (now(), level, stage, item_id, shortcode, message[:500],
         round(duration, 2) if duration is not None else None))
    conn.execute("DELETE FROM events WHERE id <= ?", (cur.lastrowid - EVENTS_KEEP,))
    conn.commit()
    return cur.lastrowid


def event_dict(row):
    return {k: row[k] for k in ("id", "at", "level", "stage", "item_id", "shortcode", "message", "duration_s")}
