from __future__ import annotations

import os
from pathlib import Path

from pipeline.config import PIPELINE_ROOT


def load_dotenv(path: Path | None = None) -> None:
    """Carga claves de .env que todavía no están en el entorno."""
    env_path = path or (PIPELINE_ROOT.parent / ".env")
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")
