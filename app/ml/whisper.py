import gc
import time

from ..config import settings

_model = None
loads, load_s = 0, None


def loaded():
    return _model is not None


def load():
    global _model, loads, load_s
    if _model is None:
        t = time.perf_counter()
        from faster_whisper import WhisperModel
        _model = WhisperModel(settings.whisper_model, device="cpu", compute_type="int8",
                              cpu_threads=settings.threads, download_root=str(settings.models_dir / "whisper"))
        loads, load_s = loads + 1, time.perf_counter() - t
    return _model


def transcribe(path):
    segments, info = load().transcribe(str(path), vad_filter=True, beam_size=5)
    out = [{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()} for s in segments]
    return out, info.language


def unload():
    global _model
    _model = None
    gc.collect()
