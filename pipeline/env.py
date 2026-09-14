from __future__ import annotations

import os
from pathlib import Path

from pipeline.config import PIPELINE_ROOT

# El .env del repo pisa estas claves: si no, un export viejo de bashrc
# (otro JSON de service account) hace que publish --sheet no vea las credenciales.
_OVERRIDE_FROM_DOTENV = {
    "GOOGLE_SHEETS_SPREADSHEET_ID",
    "GOOGLE_SHEETS_METADATA_TAB",
    "GOOGLE_SHEETS_ANALYSIS_TAB",
    "GOOGLE_SHEETS_COUNTRY_TAB",
    "GOOGLE_SHEETS_COUNTRY_CSV",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GITHUB_REPO",
    "GITHUB_BRANCH",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "GITHUB_TRANSCRIPT_STYLE",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL",
    "ANALYZE_ALLOW_STUB",
}


def _credential_candidates(raw: str) -> list[Path]:
    paths: list[Path] = []
    root = PIPELINE_ROOT.parent
    if raw:
        incoming = Path(raw).expanduser()
        paths.append(incoming if incoming.is_absolute() else (root / incoming))
        # .env del host: /root/traefik/gwl-bot/foo.json → en Docker es /app/foo.json
        paths.append(root / incoming.name)
    paths.extend(
        [
            Path("/secrets/google-sa.json"),
            root / "secrets" / "google-sa.json",
        ]
    )
    paths.extend(sorted(root.glob("gwl-bot-*.json")))
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        resolved = path.expanduser()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(resolved)
    return unique


def resolve_google_credentials() -> None:
    """En Docker el .env suele traer un path del laptop: no pisar el mount /secrets."""
    raw = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    for path in _credential_candidates(raw):
        if path.is_file():
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(path)
            return


def load_dotenv(path: Path | None = None) -> None:
    """Carga claves de .env. Las de Sheets pisan el entorno; el resto no."""
    env_path = path or (PIPELINE_ROOT.parent / ".env")
    if env_path.is_file():
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
                incoming = Path(parsed).expanduser()
                if not incoming.is_absolute():
                    incoming = PIPELINE_ROOT.parent / incoming
                current = os.environ.get(key, "").strip()
                if not incoming.is_file() and current:
                    # Path del host en .env no existe acá: dejar el de Compose (/secrets/...).
                    continue
            os.environ[key] = parsed
    resolve_google_credentials()

