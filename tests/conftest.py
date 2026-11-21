import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, db

FIXTURES = Path(__file__).parent / "fixtures"
TOKEN = "test-token"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("API_TOKEN", TOKEN)
    monkeypatch.setenv("SEARCH_MODELS", "0")
    monkeypatch.setenv("WEB_DIR", str(tmp_path / "web"))
    monkeypatch.setenv("BATCH_WINDOW", "always")
    monkeypatch.setenv("FETCH_DELAY_MIN", "20")
    monkeypatch.setenv("FETCH_DELAY_MAX", "40")
    monkeypatch.setenv("MIN_FREE_GB", "0")
    s = config.reload()
    yield s
    config.reload()


@pytest.fixture
def conn(env):
    c = db.open_db()
    yield c
    c.close()


@pytest.fixture
def client(env):
    from app.api import app
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {TOKEN}"
        yield c


def add_item(conn, shortcode, **fields):
    base = {"url": f"https://www.instagram.com/reel/{shortcode}/", "type": "reel", "status": "indexed",
            "source": "export", "saved_at": "2025-01-01T00:00:00Z", "created_at": db.now(), "updated_at": db.now()}
    base.update(fields)
    for k, v in list(base.items()):
        if isinstance(v, (list, dict)):
            base[k] = json.dumps(v)
    cols = ["shortcode", *base]
    cur = conn.execute(f"INSERT INTO items({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [shortcode, *base.values()])
    conn.commit()
    return cur.lastrowid
