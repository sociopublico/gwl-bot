from __future__ import annotations

import os
from pathlib import Path

from pipeline.config import PIPELINE_ROOT

# El .env del repo pisa estas claves: si no, un export viejo de bashrc
# (otro JSON de service account) hace que publish --sheet no vea las credenciales.
_OVERRIDE_FROM_DOTENV = {
    "GOOGLE_SHEETS_SPREADSHEET_ID",
    "GOOGLE_SHEETS_METADATA_TAB",
    "GOOGLE_SHEETS_COUNTRY_TAB",
    "GOOGLE_SHEETS_COUNTRY_CSV",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GITHUB_REPO",
    "GITHUB_BRANCH",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "GITHUB_TRANSCRIPT_STYLE",
}


def load_dotenv(path: Path | None = None) -> None:
    """Carga claves de .env. Las de Sheets pisan el entorno; el resto no."""
    env_path = path or (PIPELINE_ROOT.parent / ".env")
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        parsed = value.strip().strip('"').strip("'")
        if key not in _OVERRIDE_FROM_DOTENV and key in os.environ:
            continue
        if key == "GOOGLE_APPLICATION_CREDENTIALS":
            incoming = Path(parsed)
            current = os.environ.get(key, "")
            if not incoming.is_file() and current and Path(current).is_file():
                continue
        os.environ[key] = parsed
