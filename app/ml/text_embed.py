import gc
import time

from ..config import settings

PROMPTS = {"google/embeddinggemma-2": ("task: search result | query: ", "title: none | text: ")}

_model = None
loads, load_s = 0, None


def loaded():
    return _model is not None


def load(half=False):
    global _model, loads, load_s
    if _model is None:
        t = time.perf_counter()
        import torch
        from sentence_transformers import SentenceTransformer
        torch.set_num_threads(settings.threads)
        _model = SentenceTransformer(settings.text_model, device="cpu")
        _model.max_seq_length = 2048
        if half:
            _model = _model.to(torch.bfloat16)
        loads, load_s = loads + 1, time.perf_counter() - t
    return _model


def embed(texts, half=False, query=False):
    prefix = PROMPTS.get(settings.text_model, ("", ""))[0 if query else 1]
    return load(half).encode([prefix + t for t in texts], normalize_embeddings=True, batch_size=4).tolist()


def unload():
    global _model
    _model = None
    gc.collect()
