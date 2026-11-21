import os

from ..config import settings

os.environ.setdefault("HF_HOME", str(settings.models_dir))
