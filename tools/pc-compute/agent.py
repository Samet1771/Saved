"""PC GPU compute agent for Saved's hybrid mode: claims extract/enrich/index work over HTTP and computes it on this GPU."""
import argparse
import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
KEY_FILE = REPO / "tools" / "pc-llm" / "key.txt"
CONFIG_FILE = HERE / "agent.local.json"
STAGES = ("extract", "enrich", "index")
HEARTBEAT_S = 20
HEALTH_S = 10
RETRY_S = 600
MAX_TRANSIENT = 3
MAX_KEYFRAME_BYTES = 2 * 1024 * 1024 - 64 * 1024

secrets = set()
log_file = None
stop = threading.Event()
print_lock = threading.Lock()
timers = threading.local()


def mask(text):
    text = str(text)
    for s in secrets:
        text = text.replace(s, "***")
    return text


def log(msg):
    line = f"{datetime.now():%H:%M:%S} {mask(msg)}"
    with print_lock:
        print(line, flush=True)
        if log_file:
            log_file.write(line + "\n")
            log_file.flush()


def load_config():
    cfg = {}
    if CONFIG_FILE.is_file():
        cfg = json.loads(CONFIG_FILE.read_text("utf-8-sig"))

    def pick(env, key, default=None):
        v = os.environ.get(env)
        return v if v else cfg.get(key, default)

    return {"url": str(pick("SAVED_URL", "url", "")).rstrip("/"), "token": str(pick("SAVED_TOKEN", "token", "")),
            "cache": pick("COMPUTE_CACHE", "cache_dir", str(HERE / "cache")),
            "llm_port": int(pick("LLM_PORT", "llm_port", 8099)),
            "enrich_threads": int(pick("COMPUTE_ENRICH_THREADS", "enrich_threads", 2)),
            "cache_max_gb": float(pick("COMPUTE_CACHE_MAX_GB", "cache_max_gb", 10)),
            "backlog": int(pick("COMPUTE_BACKLOG", "backlog", 10)),
            "threads": int(pick("THREADS", "threads", 4)),
            "name": pick("COMPUTE_AGENT_NAME", "name", os.environ.get("COMPUTERNAME", "pc"))}


def setup_environment(cfg):
    if not cfg["url"] or not cfg["token"]:
        sys.exit("SAVED_URL and SAVED_TOKEN are required (environment or tools/pc-compute/agent.local.json)")
    if not KEY_FILE.is_file():
        sys.exit(f"{KEY_FILE} is missing; run tools/pc-llm/start-llm.ps1 once to create it")
    key = KEY_FILE.read_text().strip()
    secrets.update({cfg["token"], key})
    global log_file
    cache = Path(cfg["cache"]).resolve()
    (cache / "data").mkdir(parents=True, exist_ok=True)
    (cache / "logs").mkdir(exist_ok=True)
    path = cache / "logs" / "agent.log"
    if path.is_file() and path.stat().st_size > 5 * 1024 * 1024:
        path.replace(path.with_suffix(".log.1"))
    log_file = open(path, "a", encoding="utf-8")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    os.environ.update({"DEVICE": "cuda", "DATA_DIR": str(cache / "data"), "MODELS_DIR": str(cache / "models"),
                       "LLM_REMOTE_URL": f"http://127.0.0.1:{cfg['llm_port']}", "LLM_REMOTE_KEY": key,
                       "SEARCH_MODELS": "1", "THREADS": str(cfg["threads"])})
    if "HF_HOME" not in os.environ:
        os.environ["HF_HOME"] = str(cache / "hf")
        default_token = Path.home() / ".cache" / "huggingface" / "token"
        if default_token.is_file() and "HF_TOKEN_PATH" not in os.environ:
            os.environ["HF_TOKEN_PATH"] = str(default_token)
    paths = []
    for base in sys.path:
        for sub in ("nvidia/cublas/bin", "nvidia/cudnn/bin", "nvidia/cuda_runtime/bin", "torch/lib"):
            p = Path(base) / sub
            if p.is_dir():
                paths.append(str(p))
    for p in paths:
        os.add_dll_directory(p)
    os.environ["PATH"] = os.pathsep.join(paths + [os.environ.get("PATH", "")])
    sys.path.insert(0, str(REPO))
    return cache


class Conflict(Exception):
    pass


class Transient(Exception):
    pass


class ApiError(Exception):
    pass


class Server:
    def __init__(self, url, token, agent):
        self.url, self.token, self.agent = url, token, agent
        self.local = threading.local()

    def session(self):
        import requests
        if not hasattr(self.local, "s"):
            self.local.s = requests.Session()
            self.local.s.headers["Authorization"] = f"Bearer {self.token}"
        return self.local.s

    def request(self, method, path, retry_s=RETRY_S, timeout=60, **kw):
        import requests
        deadline = time.monotonic() + retry_s
        delay = 2
        while True:
            try:
                r = self.session().request(method, self.url + path, timeout=timeout, **kw)
            except requests.RequestException as e:
                err = Transient(f"server unreachable: {type(e).__name__}")
            else:
                if r.status_code == 409:
                    raise Conflict(r.text[:200])
                if r.status_code >= 500:
                    err = Transient(f"HTTP {r.status_code}")
                elif r.status_code >= 400:
                    raise ApiError(f"HTTP {r.status_code} on {path}: {r.text[:200]}")
                else:
                    return r
            if time.monotonic() >= deadline or stop.is_set():
                raise err
            stop.wait(delay)
            delay = min(delay * 2, 30)

    def claim(self, stage, limit, info=None):
        return self.request("POST", "/api/work/claim", retry_s=0, timeout=30,
                            json={"agent": self.agent, "stage": stage, "limit": limit, "info": info}).json()

    def heartbeat(self, info):
        return self.request("POST", "/api/work/heartbeat", retry_s=0, timeout=15,
                            json={"agent": self.agent, "info": info}).json()

    def release(self, ids):
        if ids:
            self.request("POST", "/api/work/release", retry_s=0, timeout=15, json={"ids": list(ids)})

    def post(self, item_id, stage, **kw):
        return self.request("POST", f"/api/work/{item_id}/{stage}", **kw).json()

    def download(self, shortcode, name, dst):
        r = self.request("GET", f"/media/{quote(shortcode)}/{quote(name)}", retry_s=120, timeout=120, stream=True)
        part = dst.with_name(dst.name + ".part")
        with open(part, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
        part.replace(dst)


class Job:
    def __init__(self, item, d, categories=None):
        self.item, self.id, self.sc, self.dir, self.categories = item, item["id"], item["shortcode"], d, categories


def dir_bytes(d):
    return sum(f.stat().st_size for f in d.rglob("*") if f.is_file())


def gpu_info():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.splitlines()[0]
        name, used, total, util = [x.strip() for x in out.split(",")]
        return {"gpu": name, "vram_used_mb": int(used), "vram_total_mb": int(total), "gpu_util": int(util)}
    except Exception:
        return {}


def timed(mod, name, key):
    fn = getattr(mod, name)

    def wrapper(*a, **k):
        t = time.perf_counter()
        try:
            return fn(*a, **k)
        finally:
            d = getattr(timers, "t", None)
            if d is not None:
                d[key] = d.get(key, 0) + time.perf_counter() - t
                d[key + "_n"] = d.get(key + "_n", 0) + 1
    setattr(mod, name, wrapper)


def shrink(path):
    if path.stat().st_size <= MAX_KEYFRAME_BYTES:
        return
    from PIL import Image
    with Image.open(path) as im:
        im = im.convert("RGB")
        quality = 85
        while True:
            im.save(path, "JPEG", quality=quality)
            if path.stat().st_size <= MAX_KEYFRAME_BYTES or quality <= 40:
                return
            quality -= 15


def short_error(e):
    tb = traceback.extract_tb(e.__traceback__)[-3:]
    frames = " <- ".join(f"{Path(f.filename).name}:{f.lineno} {f.name}" for f in reversed(tb))
    return mask(f"{type(e).__name__}: {e} [{frames}]")[:500]


class Agent:
    def __init__(self, cfg, cache, args):
        self.cfg, self.args = cfg, args
        self.items_dir = cache / "items"
        self.items_dir.mkdir(parents=True, exist_ok=True)
        self.server = Server(cfg["url"], cfg["token"], cfg["name"])
        self.workers = {"extract": 1, "enrich": cfg["enrich_threads"], "index": 1}
        self.targets = {"extract": 2, "enrich": cfg["enrich_threads"] + 1, "index": 3}
        self.queues = {s: queue.Queue() for s in STAGES}
        self.wake = {s: threading.Event() for s in STAGES}
        self.lock = threading.Lock()
        self.held = {s: 0 for s in STAGES}
        self.leased = {}
        self.transient = {}
        self.stats = {s: {"done": 0, "failed": 0, "total_s": 0.0} for s in STAGES}
        self.paused = False
        self.llm_ok = False
        self.state = "starting"
        self.last_work = time.monotonic()
        self.peak_vram = 0

    def info(self):
        with self.lock:
            stats = {s: dict(v) for s, v in self.stats.items()}
            held = dict(self.held)
        g = gpu_info()
        self.peak_vram = max(self.peak_vram, g.get("vram_used_mb", 0))
        return {**g, "state": self.state, "queues": held, "done": {s: v["done"] for s, v in stats.items()},
                "failed": {s: v["failed"] for s, v in stats.items() if v["failed"]},
                "avg_s": {s: round(v["total_s"] / v["done"], 1) for s, v in stats.items() if v["done"]},
                "llm_ok": self.llm_ok, "enrich_slots": self.workers["enrich"]}

    def heartbeat_loop(self):
        while not stop.is_set():
            try:
                self.paused = bool(self.server.heartbeat(self.info()).get("paused"))
            except Exception as e:
                log(f"heartbeat failed: {e}")
            stop.wait(5 if self.paused else HEARTBEAT_S)

    def health_loop(self):
        from app.ml import llm
        while not stop.is_set():
            ok = llm.remote_healthy(force=True)
            if ok != self.llm_ok:
                log("llama-server is up" if ok else "llama-server is unreachable, enrich claims paused")
                self.llm_ok = ok
                self.wake["enrich"].set()
            stop.wait(HEALTH_S)

    def cache_state(self):
        dirs = [d for d in self.items_dir.iterdir() if d.is_dir()]
        return len(dirs), sum(dir_bytes(d) for d in dirs)

    def clean_stale(self):
        for d in self.items_dir.iterdir():
            if d.is_dir() and time.time() - d.stat().st_mtime > 24 * 3600:
                shutil.rmtree(d, ignore_errors=True)

    def can_claim(self, stage):
        if stage == "enrich" and not self.llm_ok:
            return False
        if stage == "extract":
            n, size = self.cache_state()
            return n < self.cfg["backlog"] and size < self.cfg["cache_max_gb"] * 1e9
        return True

    def claimer(self, stage):
        delay = 3
        while not stop.is_set():
            with self.lock:
                room = self.targets[stage] - self.held[stage]
            wait = None
            if self.paused or room <= 0 or not self.can_claim(stage):
                wait = 2
            else:
                try:
                    res = self.server.claim(stage, min(room, 4), self.info() if stage == "extract" else None)
                except Conflict as e:
                    log(f"server refused claims: {mask(e)}")
                    wait = 30
                except (Transient, ApiError) as e:
                    log(f"claim {stage} failed: {e}")
                    wait = 15
                else:
                    if res.get("paused"):
                        self.paused = True
                    fresh = []
                    with self.lock:
                        for it in res.get("items", []):
                            if it["id"] in self.leased:
                                continue
                            self.leased[it["id"]] = stage
                            self.held[stage] += 1
                            fresh.append(Job(it, self.items_dir / it["shortcode"], res.get("categories")))
                    for job in fresh:
                        self.queues[stage].put(job)
                    if fresh:
                        delay = 3
                        self.last_work = time.monotonic()
                    else:
                        wait, delay = delay, min(delay * 2, 15)
            if wait:
                self.wake[stage].wait(wait)
                self.wake[stage].clear()

    def finish(self, stage, job, keep_lease=False):
        with self.lock:
            if not keep_lease:
                self.leased.pop(job.id, None)
            self.held[stage] -= 1
        self.last_work = time.monotonic()
        self.wake[stage].set()

    def ensure(self, job, names):
        job.dir.mkdir(parents=True, exist_ok=True)
        n = 0
        for name in names:
            name = Path(name).name
            if not (job.dir / name).is_file():
                self.server.download(job.sc, name, job.dir / name)
                n += 1
        return n

    def do_extract(self, job):
        from app.stages import extract
        item = job.item
        self.ensure(job, [m["file"] for m in item["media"]])
        timers.t = {}
        t = time.perf_counter()
        fields = extract.compute(item, job.dir)
        dur = time.perf_counter() - t
        t = timers.t
        files = []
        for name in fields["keyframes"]:
            shrink(job.dir / name)
            files.append(("files", (name, (job.dir / name).read_bytes(), "image/jpeg")))
        self.server.post(job.id, "extract", data={"fields": json.dumps(fields), "duration_s": f"{dur:.2f}"}, files=files)
        return dur, (f"whisper {t.get('whisper', 0):.1f} s | ocr {t.get('ocr', 0):.1f} s ({t.get('ocr_n', 0)} frames)"
                     f" | {len(fields['ocr_lines'])} lines, {len(fields['transcript'])} segments, {len(files)} keyframes")

    def do_enrich(self, job):
        from app.ml import llm
        from app.stages import enrich
        item = job.item
        self.ensure(job, [m["file"] for m in item["media"]])
        t = time.perf_counter()
        result = enrich.compute(item, job.categories or [], job.dir, backend="remote")
        dur = time.perf_counter() - t
        self.server.post(job.id, "enrich", json={"result": result, "model": llm.enriched_by("remote"), "duration_s": round(dur, 2)})
        return dur, f"category {result.get('category')}, {len(result.get('tags') or [])} tags, {len(result.get('timeline') or [])} events"

    def do_index(self, job):
        from app.stages import index
        item = job.item
        names = list(item.get("keyframes") or []) + [m["file"] for m in item["media"] if m["kind"] == "image"]
        self.ensure(job, names)
        t = time.perf_counter()
        text_vec, visual = index.compute(item, job.dir)
        dur = time.perf_counter() - t
        self.server.post(job.id, "index", json={"text_vec": text_vec, "visual": visual, "duration_s": round(dur, 2)})
        shutil.rmtree(job.dir, ignore_errors=True)
        return dur, f"text {'yes' if text_vec else 'no'}, {len(visual)} image vectors"

    def is_transient(self, e):
        import requests
        from app.ml import llm
        if isinstance(e, (Transient, llm.RemoteError, requests.RequestException, urllib.error.URLError,
                          ConnectionError, TimeoutError)):
            return True
        return "out of memory" in str(e).lower()

    def handle(self, stage, job):
        fn = {"extract": self.do_extract, "enrich": self.do_enrich, "index": self.do_index}[stage]
        t0 = time.perf_counter()
        keep_lease = False
        try:
            dur, detail = fn(job)
            with self.lock:
                s = self.stats[stage]
                s["done"] += 1
                s["total_s"] += dur
                self.transient.pop(job.id, None)
            log(f"{stage:<7} {job.sc} {dur:5.1f} s  {detail}")
            if stage != "index":
                self.wake[{"extract": "enrich", "enrich": "index"}[stage]].set()
        except Conflict:
            log(f"{stage:<7} {job.sc} changed on the server, dropped")
            shutil.rmtree(job.dir, ignore_errors=True)
        except Exception as e:
            dur = time.perf_counter() - t0
            if stop.is_set():
                log(f"{stage:<7} {job.sc} interrupted by shutdown")
                keep_lease = True
                return
            if self.is_transient(e):
                n = self.transient[job.id] = self.transient.get(job.id, 0) + 1
                if n < MAX_TRANSIENT:
                    log(f"{stage:<7} {job.sc} transient error ({short_error(e)}), released for retry {n}/{MAX_TRANSIENT}")
                    if stage == "enrich":
                        from app.ml import llm
                        llm.mark_remote_down()
                        self.llm_ok = False
                    try:
                        self.server.release([job.id])
                    except Exception:
                        pass
                    stop.wait(15)
                    return
            msg = short_error(e)
            log(f"{stage:<7} {job.sc} FAILED {msg}")
            with self.lock:
                self.stats[stage]["failed"] += 1
            try:
                self.server.post(job.id, "fail", json={"stage": stage, "error": msg, "duration_s": round(dur, 2)})
            except Conflict:
                pass
            except Exception as e2:
                log(f"could not report failure of {job.sc}: {e2}")
            shutil.rmtree(job.dir, ignore_errors=True)
        finally:
            self.finish(stage, job, keep_lease)

    def worker(self, stage):
        q = self.queues[stage]
        while not stop.is_set():
            try:
                job = q.get(timeout=0.5)
            except queue.Empty:
                continue
            if stop.is_set():
                q.put(job)
                return
            self.handle(stage, job)

    def preload(self):
        from app.ml import ocr, text_embed, visual_embed, whisper
        for name, mod in (("whisper", whisper), ("ocr", ocr), ("text embedding", text_embed), ("image embedding", visual_embed)):
            t = time.perf_counter()
            mod.load()
            log(f"loaded {name} in {time.perf_counter() - t:.1f} s")
        timed(whisper, "transcribe", "whisper")
        timed(ocr, "ocr", "ocr")

    def shutdown(self):
        log("stopping, giving leases back")
        deadline = time.monotonic() + self.args.grace
        for t in self.threads:
            t.join(max(0, deadline - time.monotonic()))
        with self.lock:
            ids = list(self.leased)
        try:
            self.server.release(ids)
            if ids:
                log(f"released {len(ids)} items")
        except Exception as e:
            log(f"release failed (leases expire on their own): {e}")

    def run(self):
        self.threads = []

        def spawn(fn, *a):
            t = threading.Thread(target=fn, args=a, daemon=True)
            t.start()
            return t

        spawn(self.heartbeat_loop)
        self.state = "loading models"
        self.preload()
        self.clean_stale()
        spawn(self.health_loop)
        self.state = "running"
        log(f"running against {self.cfg['url']} as {self.cfg['name']}: 1 extract, {self.workers['enrich']} enrich, 1 index workers")
        for s in STAGES:
            self.threads += [spawn(self.worker, s) for _ in range(self.workers[s])]
        for s in STAGES:
            spawn(self.claimer, s)
        while not stop.wait(1):
            if self.args.idle_exit and time.monotonic() - self.last_work > self.args.idle_exit:
                with self.lock:
                    idle = not self.leased
                if idle:
                    log(f"idle for {self.args.idle_exit:.0f} s, exiting")
                    stop.set()
        self.state = "stopping"
        self.shutdown()
        log(f"peak VRAM seen {self.peak_vram} MB; " + ", ".join(
            f"{s} {v['done']} done" + (f" {v['total_s'] / v['done']:.1f} s avg" if v["done"] else "") for s, v in self.stats.items()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--idle-exit", type=float, default=0, help="exit after this many seconds with no work (0 = never)")
    ap.add_argument("--grace", type=float, default=60, help="seconds to let in-flight items finish on shutdown")
    args = ap.parse_args()
    cfg = load_config()
    cache = setup_environment(cfg)
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), lambda *_: stop.set())
    Agent(cfg, cache, args).run()
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
