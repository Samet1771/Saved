import json
import logging
import random
import shutil
import signal
import time
from datetime import datetime, timedelta, timezone

from . import db
from .config import in_batch_window, settings
from .ml import llm
from .stages import enrich, extract, fetch, index
from .stages.enrich import Waiting
from .stages.fetch import RateLimited, Terminal, first_line

log = logging.getLogger("worker")

STAGES = [("fetch", "queued", fetch), ("extract", "fetched", extract), ("enrich", "extracted", enrich),
          ("index", "enriched", index)]
PENDING = tuple(s[1] for s in STAGES)
MAX_ATTEMPTS = 3
BACKOFF = [15 * 60, 30 * 60, 60 * 60, 2 * 60 * 60]
MAX_RATE_LIMITED = 3


def fmt_duration(s):
    if s < 90:
        return f"{s:.0f} s"
    if s < 5400:
        return f"{s / 60:.0f} min"
    return f"{s / 3600:.1f} h"


def done_message(name, row):
    media = json.loads(row["media"] or "[]")
    if name == "fetch":
        parts = [row["type"] or "item"]
        if len(media) > 1:
            parts.append(f"{len(media)} slides")
        dur = sum(m.get("duration") or 0 for m in media if m["kind"] == "video")
        if dur:
            parts.append(f"{dur:.1f} s video")
    elif name == "extract":
        audio = sum(m.get("duration") or 0 for m in media if m.get("audio"))
        parts = [f"{audio:.1f} s audio, lang {row['lang'] or '?'}" if audio else "no audio"]
        if row["ocr_text"]:
            parts.append(f"{len(row['ocr_text'])} chars on screen")
    elif name == "enrich":
        tags = json.loads(row["tags"] or "[]")
        parts = [f"{row['category'] or 'no category'} · {', '.join(tags[:4]) or 'no tags'}"]
    else:
        parts = [f"{len(json.loads(row['keyframes'] or '[]'))} frames"]
    return f"{name} done: {', '.join(parts)}"


def model_label(model):
    name = model.__name__.rsplit(".", 1)[-1] if hasattr(model, "__name__") else type(model).__name__
    name = getattr(model, "LABEL", name)
    if hasattr(model, "loaded_file") and model.loaded_file():
        note = ", text-only: mmproj missing" if getattr(model, "text_only", False) else ""
        return f"{name} ({model.loaded_file()}{note})"
    return name


class Worker:
    def __init__(self, conn, stages=None, clock=time.monotonic, sleep=time.sleep):
        self.conn = conn
        self.stages = stages or STAGES
        self.clock, self.sleep = clock, sleep
        self.stop = False
        self.next_fetch = 0.0
        self.paused = False
        self.backoff_level = 0
        self.backoff_until = None
        self.cookies_mtime = None
        self.remote_up = None
        self._restore_backoff()

    def _row(self, item_id):
        return self.conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()

    def event(self, stage, message, level="info", item=None, duration=None):
        log.log({"info": logging.INFO, "warn": logging.WARNING}.get(level, logging.ERROR), "%s: %s%s", stage,
                f"{item['shortcode']} " if item is not None else "", message)
        db.add_event(self.conn, stage, message, level, item, duration)

    def _restore_backoff(self):
        until = self.conn.execute("SELECT fetch_backoff_until FROM worker_status WHERE id=1").fetchone()[0]
        if until:
            left = (datetime.fromisoformat(until.replace("Z", "+00:00")) - datetime.now(timezone.utc)).total_seconds()
            if left > 0:
                self.backoff_until, self.backoff_level = self.clock() + left, 1
            else:
                db.set_fetch_backoff(self.conn, None)

    def _model_loads(self, module):
        return {id(m): getattr(m, "loads", 0) for m in getattr(module, "MODELS", [])}

    def _report_loads(self, name, module, before):
        for m in getattr(module, "MODELS", []):
            if getattr(m, "loads", 0) != before.get(id(m), 0) and m.load_s is not None:
                self.event(name, f"loaded {model_label(m)} in {m.load_s:.1f} s", duration=m.load_s)

    def run_item(self, name, module, item_id):
        item = self._row(item_id)
        attempts = item["attempts"] + 1
        if attempts > MAX_ATTEMPTS:
            msg = f"worker stopped during {name} {MAX_ATTEMPTS} times (crash or out of memory?)"
            db.update_item(self.conn, item_id, status="failed", failed_stage=name, attempts=0, error=msg)
            self.event(name, f"failed: {msg}", "error", item)
            return False
        db.update_item(self.conn, item_id, attempts=attempts)
        db.set_worker(self.conn, name, item_id)
        loads = self._model_loads(module)
        t0 = time.perf_counter()
        ok = False
        try:
            module.process(self.conn, self._row(item_id))
            db.update_item(self.conn, item_id, attempts=0)
            self.event(name, done_message(name, self._row(item_id)), item=item, duration=time.perf_counter() - t0)
            ok = True
        except Waiting:
            db.update_item(self.conn, item_id, attempts=0)
        except RateLimited as e:
            self.rate_limited(name, item, e, time.perf_counter() - t0)
        except Terminal as e:
            db.update_item(self.conn, item_id, status=e.status, error=str(e)[:1000], failed_stage=name, attempts=0)
            self.event(name, f"{e.status}: {first_line(e)[:200]}", "warn", item, time.perf_counter() - t0)
        except Exception as e:
            log.exception("%s failed for item %s", name, item_id)
            db.update_item(self.conn, item_id, status="failed", error=f"{type(e).__name__}: {e}"[:1000],
                           failed_stage=name, attempts=0)
            self.event(name, f"failed: {type(e).__name__}: {first_line(e)[:200]}", "error", item,
                       time.perf_counter() - t0)
        finally:
            self._report_loads(name, module, loads)
            db.set_worker(self.conn, "idle")
            self.check_remote()
        return ok

    def rate_limited(self, name, item, err, duration):
        hits = item["rate_limited"] + 1
        if item["source"] == "export" and hits < MAX_RATE_LIMITED:
            db.update_item(self.conn, item["id"], status="queued", error=str(err)[:1000], attempts=0, rate_limited=hits)
            self.event(name, f"rate-limited, back in the queue: {str(err)[:200]}", "warn", item, duration)
        else:
            msg = str(err) if item["source"] == "ingest" else f"rate-limited {hits} times in a row: {err}"
            db.update_item(self.conn, item["id"], status="failed", error=msg[:1000], failed_stage=name, attempts=0,
                           rate_limited=0)
            self.event(name, f"failed: {msg[:200]}", "error", item, duration)
        if self.backoff_until is None or item["source"] == "export":
            self.start_backoff()

    def start_backoff(self):
        delay = BACKOFF[min(self.backoff_level, len(BACKOFF) - 1)]
        self.backoff_level += 1
        self.backoff_until = self.clock() + delay
        until = (datetime.now(timezone.utc) + timedelta(seconds=delay)).strftime("%Y-%m-%dT%H:%M:%SZ")
        db.set_fetch_backoff(self.conn, until)
        self.event("fetch", f"backoff: pausing fetch for {fmt_duration(delay)}", "warn", duration=delay)

    def backoff_active(self):
        if self.backoff_until is None:
            return False
        if self.clock() < self.backoff_until:
            return True
        self.backoff_until = None
        db.set_fetch_backoff(self.conn, None)
        self.event("fetch", "backoff over, fetching again")
        return False

    def fetch_succeeded(self):
        if self.backoff_level or self.backoff_until is not None:
            self.backoff_level, self.backoff_until = 0, None
            db.set_fetch_backoff(self.conn, None)
            self.event("fetch", "fetch works again, backoff reset")

    def set_paused(self, paused):
        if paused != self.paused:
            if paused:
                try:
                    free = f" ({shutil.disk_usage(settings.media_dir).free / 1024 ** 3:.1f} GB free)"
                except OSError:
                    free = ""
                self.event("fetch", f"disk low{free}: fetching paused", "warn")
            else:
                self.event("fetch", "disk ok: fetching resumed")
        self.paused = paused

    def check_cookies(self):
        try:
            mtime = settings.cookies_path.stat().st_mtime
        except OSError:
            mtime = None
        if mtime is not None and mtime != self.cookies_mtime:
            n = self.conn.execute("UPDATE items SET status='queued', error=NULL, failed_stage=NULL, attempts=0,"
                                  " updated_at=? WHERE status='needs_login'", (db.now(),)).rowcount
            self.conn.commit()
            if n:
                self.event("fetch", f"burner cookies found: re-queued {n} item{'s' * (n != 1)} that needed login")
        self.cookies_mtime = mtime

    def unload(self, *modules):
        for m in modules:
            name = next((n for n, _, mod in self.stages if mod is m), "worker")
            for model in getattr(m, "MODELS", []):
                was = getattr(model, "loaded", lambda: False)()
                label = model_label(model)
                model.unload()
                if was:
                    self.event(name, f"unloaded {label}")

    def check_remote(self):
        healthy = llm.remote_healthy()
        st = llm.remote_state()
        if st["url"] and healthy != self.remote_up:
            if healthy:
                self.event("enrich", f"remote GPU up: {st['url']} serving {st['model'] or 'an unknown model'}")
            else:
                self.event("enrich", f"remote GPU unreachable: {st['url']}; items wait for it before enrich", "warn")
        self.remote_up = healthy
        backend = "local" if llm.loaded() else "remote" if healthy else None
        db.set_llm_status(self.conn, healthy, st["checked_at"], st["model"], backend)

    def ingest_waiting(self):
        excluded = ("queued",) if self.paused else ()
        if not llm.can_enrich():
            excluded += ("extracted",)
        statuses = [s for s in PENDING if s not in excluded]
        return self.conn.execute(
            f"SELECT id FROM items WHERE source='ingest' AND status IN ({','.join('?' * len(statuses))})"
            " ORDER BY id LIMIT 1", statuses).fetchone()

    def process_ingest(self):
        if not self.ingest_waiting():
            return False
        for name, from_status, module in self.stages:
            if self.stop:
                break
            if name == "fetch" and self.paused:
                continue
            ids = [r[0] for r in self.conn.execute(
                "SELECT id FROM items WHERE source='ingest' AND status=? ORDER BY id", (from_status,))]
            for item_id in ids:
                if self.stop:
                    break
                if self.run_item(name, module, item_id) and name == "fetch":
                    self.fetch_succeeded()
            if ids:
                self.unload(module)
        return True

    def enrich_remote(self):
        name, _, module = next(s for s in self.stages if s[0] == "enrich")
        tried, done = set(), 0
        backlog = self.conn.execute("SELECT COUNT(*) FROM items WHERE status='extracted' AND source='export'"
                                    ).fetchone()[0]
        t0 = time.perf_counter()
        while not self.stop and not self.ingest_waiting() and llm.remote_healthy():
            row = next((r for r in self.conn.execute("SELECT id FROM items WHERE status='extracted'"
                                                     " AND source='export' ORDER BY id") if r[0] not in tried), None)
            if not row:
                break
            if not tried and backlog > 1:
                self.event(name, f"batch {name}: {backlog} items on the remote GPU")
            tried.add(row[0])
            self.maybe_fetch()
            if self.run_item(name, module, row[0]):
                done += 1
        if backlog > 1 and tried:
            self.event(name, f"batch {name} {'finished' if done == len(tried) else 'paused'}: {done} done",
                       duration=time.perf_counter() - t0)
        return bool(tried)

    def maybe_fetch(self):
        if self.clock() < self.next_fetch or self.backoff_active():
            return False
        self.set_paused(not fetch.disk_free_ok())
        db.set_worker(self.conn, "idle", paused=self.paused)
        if self.paused:
            return False
        row = self.conn.execute("SELECT id FROM items WHERE status='queued' AND source='export'"
                                " ORDER BY rate_limited, id LIMIT 1").fetchone()
        if not row:
            return False
        name, _, module = self.stages[0]
        if self.run_item(name, module, row["id"]):
            self.fetch_succeeded()
        self.next_fetch = self.clock() + random.uniform(settings.fetch_delay_min, settings.fetch_delay_max)
        return True

    def run_batches(self):
        worked = False
        for name, from_status, module in self.stages[1:]:
            if name == "enrich":
                continue
            ids = [r[0] for r in self.conn.execute("SELECT id FROM items WHERE status=? ORDER BY id", (from_status,))]
            if not ids:
                continue
            worked = True
            report = len(ids) > 1
            if report:
                self.event(name, f"batch {name}: {len(ids)} items")
            t0, done, complete = time.perf_counter(), 0, False
            try:
                for item_id in ids:
                    if self.stop or self.ingest_waiting() or not in_batch_window():
                        return True
                    self.maybe_fetch()
                    if self._row(item_id)["status"] == from_status:
                        self.run_item(name, module, item_id)
                        done += 1
                    if name == "extract":
                        self.enrich_remote()
                complete = True
            finally:
                if report:
                    self.event(name, f"batch {name} {'finished' if complete else 'paused'}: {done} done",
                               duration=time.perf_counter() - t0)
                self.unload(module)
        return worked

    def tick(self):
        self.set_paused(not fetch.disk_free_ok())
        self.check_cookies()
        window = in_batch_window()
        db.set_worker(self.conn, "idle", in_window=window, paused=self.paused)
        self.check_remote()
        if self.process_ingest():
            return True
        if self.enrich_remote():
            return True
        if window and self.run_batches():
            return True
        return self.maybe_fetch()

    def run(self):
        db.set_worker(self.conn, "idle")
        while not self.stop:
            try:
                worked = self.tick()
            except Exception:
                log.exception("worker tick failed")
                worked = False
            if not worked:
                end = self.clock() + settings.idle_sleep
                while not self.stop and self.clock() < end:
                    self.sleep(0.5)
        db.set_worker(self.conn, "idle")
        log.info("worker stopped")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    worker = Worker(db.open_db())

    def stop(signum, _frame):
        log.info("signal %s: finishing current item, then exiting", signum)
        worker.stop = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    worker.run()


if __name__ == "__main__":
    main()
