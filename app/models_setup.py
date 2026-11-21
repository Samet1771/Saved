"""Download the models the chosen compute mode needs, so the first items don't stall on downloads.

    python -m app.models_setup server|hybrid
"""
import sys
from pathlib import Path

from .config import settings

LLM_REPO = "unsloth/Qwen3.5-4B-GGUF"
LLM_FILES = {"Qwen3.5-4B-Q4_K_M.gguf": "llm_model_path", "mmproj-F16.gguf": "llm_mmproj_path"}


def step(name, fn):
    print(f"- {name}…", flush=True)
    fn()


def search_models():
    from huggingface_hub import snapshot_download
    for repo in (settings.text_model, settings.visual_model):
        step(repo, lambda r=repo: snapshot_download(r))


def server_models():
    from huggingface_hub import hf_hub_download

    from .ml import ocr, whisper
    step(f"Whisper ({settings.whisper_model})", whisper.load)
    step("OCR (RapidOCR)", ocr.load)
    for name, attr in LLM_FILES.items():
        dest = Path(getattr(settings, attr))
        if dest.is_file():
            continue

        def get(name=name, dest=dest):
            dest.parent.mkdir(parents=True, exist_ok=True)
            Path(hf_hub_download(LLM_REPO, name, local_dir=dest.parent)).rename(dest)
        step(f"{LLM_REPO}/{name}", get)


def main(argv=None):
    mode = (argv or sys.argv[1:] or [settings.compute_mode])[0]
    if mode not in ("server", "hybrid"):
        print("usage: python -m app.models_setup server|hybrid", file=sys.stderr)
        return 1
    print(f"Downloading models for {mode} mode into {settings.models_dir}")
    search_models()
    if mode == "server":
        server_models()
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
