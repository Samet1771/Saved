import gc
import time

from ..config import settings

_model = _proc = None
loads, load_s = 0, None


def loaded():
    return _model is not None


def load(half=False):
    global _model, _proc, loads, load_s
    if _model is None:
        t = time.perf_counter()
        import torch
        from transformers import AutoModel, AutoProcessor
        torch.set_num_threads(settings.threads)
        _model = AutoModel.from_pretrained(settings.visual_model, dtype=torch.bfloat16 if half or settings.device == "cuda" else torch.float32)
        _model = _model.to(settings.device).eval()
        _proc = AutoProcessor.from_pretrained(settings.visual_model)
        loads, load_s = loads + 1, time.perf_counter() - t
    return _model, _proc


def _norm(t):
    if not hasattr(t, "norm"):
        t = t.pooler_output
    t = t.float()
    return (t / t.norm(dim=-1, keepdim=True)).tolist()


def embed_images(paths):
    import torch
    from PIL import Image
    model, proc = load()
    out = []
    for i in range(0, len(paths), 8):
        images = [Image.open(p).convert("RGB") for p in paths[i:i + 8]]
        with torch.no_grad():
            out += _norm(model.get_image_features(**proc(images=images, return_tensors="pt").to(model.device, model.dtype)))
    return out


def embed_text(text, half=False):
    import torch
    model, proc = load(half)
    inputs = proc(text=[text.lower()], padding="max_length", max_length=64, truncation=True, return_tensors="pt")
    with torch.no_grad():
        return _norm(model.get_text_features(**inputs.to(model.device)))[0]


def unload():
    global _model, _proc
    _model = _proc = None
    gc.collect()
