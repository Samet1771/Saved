import os
from pathlib import Path


def _env(name, default):
    v = os.environ.get(name)
    return default if v is None or v == "" else v


class Settings:
    def __init__(self):
        self.data_dir = Path(_env("DATA_DIR", "/data"))
        self.media_dir = Path(_env("MEDIA_DIR", str(self.data_dir / "media")))
        self.models_dir = Path(_env("MODELS_DIR", str(self.data_dir / "models")))
        self.db_path = Path(_env("DB_PATH", str(self.data_dir / "vault.db")))
        self.cookies_path = self.data_dir / "secrets" / "burner_cookies.txt"
        self.web_dir = Path(_env("WEB_DIR", str(Path(__file__).resolve().parent.parent / "web")))
        self.api_token = _env("API_TOKEN", "")
        self.auth_disabled = _env("AUTH_DISABLED", "0") == "1"
        self.compute_mode = _env("COMPUTE_MODE", "server").lower()
        if self.compute_mode not in ("server", "hybrid"):
            raise ValueError("COMPUTE_MODE must be server or hybrid")
        self.device = _env("DEVICE", "cpu").lower()
        self.work_lease_s = float(_env("WORK_LEASE_S", 900))
        self.agent_timeout_s = float(_env("AGENT_TIMEOUT_S", 90))
        self.fetch_delay_min = float(_env("FETCH_DELAY_MIN", 20))
        self.fetch_delay_max = float(_env("FETCH_DELAY_MAX", 40))
        self.fetch_slots = max(1, int(_env("FETCH_SLOTS", 1)))
        self.min_free_gb = float(_env("MIN_FREE_GB", 5))
        self.batch_window = _env("BATCH_WINDOW", "01:00-07:00")
        self.tz = _env("TZ", "Europe/Istanbul")
        self.threads = int(_env("THREADS", 4))
        self.whisper_model = _env("WHISPER_MODEL", "deepdml/faster-whisper-large-v3-turbo-ct2")
        self.keyframes = int(_env("KEYFRAMES", 8))
        self.vlm_fps = float(_env("VLM_FPS", 2))
        self.vlm_frame_width = int(_env("VLM_FRAME_WIDTH", 336))
        self.vlm_max_frames = int(_env("VLM_MAX_FRAMES", 120))
        self.llm_model_path = _env("LLM_MODEL_PATH", str(self.models_dir / "Qwen3.5-4B-Q4_K_M.gguf"))
        self.llm_ctx = int(_env("LLM_CTX", 4096))
        self.llm_mmproj_path = _env("LLM_MMPROJ_PATH", str(self.models_dir / "qwen35-mmproj-F16.gguf"))
        self.llm_remote_url = _env("LLM_REMOTE_URL", "").rstrip("/")
        self.llm_remote_key = _env("LLM_REMOTE_KEY", "")
        self.llm_local_fallback = _env("LLM_LOCAL_FALLBACK", "0") not in ("0", "false", "no")
        self.text_model = _env("TEXT_EMBED_MODEL", "google/embeddinggemma-2")
        self.text_dim = int(_env("TEXT_EMBED_DIM", 0))
        self.visual_model = _env("VISUAL_EMBED_MODEL", "google/siglip2-base-patch16-224")
        self.search_models = _env("SEARCH_MODELS", "1") not in ("0", "false", "no")
        self.search_idle_unload = float(_env("SEARCH_IDLE_UNLOAD", 900))
        self.idle_sleep = float(_env("WORKER_IDLE_SLEEP", 10))
        self.cgroup_dir = _env("CGROUP_DIR", "")
        self.system_mem_bytes = int(_env("SYSTEM_MEM_BYTES", 0))
        self.cors_origins = [o.strip() for o in _env(
            "CORS_ORIGINS", "http://tauri.localhost,https://tauri.localhost,tauri://localhost").split(",") if o.strip()]


settings = Settings()


def valid_window(window):
    window = (window or "").strip().lower()
    if window in ("always", "never", "off", ""):
        return True
    from datetime import time
    try:
        start, end = (time.fromisoformat(p.strip()) for p in window.split("-"))
    except ValueError:
        return False
    return True


def in_batch_window(at=None, window=None):
    window = (window or settings.batch_window).strip().lower()
    if window == "always":
        return True
    if window in ("never", "off", ""):
        return False
    from datetime import datetime, time
    from zoneinfo import ZoneInfo
    start, end = (time.fromisoformat(p.strip()) for p in window.split("-"))
    t = (at or datetime.now(ZoneInfo(settings.tz))).time()
    return start <= t < end if start <= end else (t >= start or t < end)


def reload():
    global settings
    settings.__init__()
    return settings
