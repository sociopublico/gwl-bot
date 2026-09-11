from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

PIPELINE_ROOT = Path(__file__).resolve().parent
SESSIONS_DIR = PIPELINE_ROOT / "sessions"

_ALIASES = {
    "80": "unga80",
    "81": "unga81",
    "unga80": "unga80",
    "unga81": "unga81",
    "ga80": "unga80",
    "ga81": "unga81",
}


@dataclass(frozen=True)
class SessionConfig:
    id: int
    year: int
    name: str
    ordinal: str
    gadebate_base: str
    slug_file: Path
    date_index: Path | None
    journal_dir: Path
    debate_dates: tuple[str, ...]
    sources: tuple[str, ...]
    min_pdf_chars: int
    min_audio_chars: int
    whisper_model: str
    whisper_device: str
    whisper_compute_type: str
    whisper_beam_size: int
    whisper_vad: bool
    user_agent: str
    root: Path
    config_path: Path

    def speaker_url(self, slug: str) -> str:
        return f"{self.gadebate_base.rstrip('/')}/en/{self.id}/{slug}"

    def archive_url(self) -> str:
        return f"{self.gadebate_base.rstrip('/')}/en/sessions-archive"


def resolve_session_path(session: str | Path) -> Path:
    raw = str(session).strip()
    path = Path(raw)
    if path.suffix == ".toml" and path.is_file():
        return path.resolve()
    key = _ALIASES.get(raw.lower(), raw.lower())
    candidate = SESSIONS_DIR / f"{key}.toml"
    if candidate.is_file():
        return candidate
    raise FileNotFoundError(
        f"No hay config para sesión {raw!r}. Probá --session 80, 81, o un .toml"
    )


def _load_whisper_env() -> None:
    from pipeline.env import load_dotenv

    load_dotenv()


def _env_or(data: dict, key: str, env_name: str, default: str) -> str:
    raw = os.environ.get(env_name) or data.get(key) or default
    return str(raw).strip() or default


def _env_bool(data: dict, key: str, env_name: str, default: bool) -> bool:
    raw = os.environ.get(env_name)
    if raw is None and key in data:
        value = data[key]
        if isinstance(value, bool):
            return value
        raw = str(value)
    if raw is None:
        return default
    lowered = str(raw).strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return default


def load_session(session: str | Path) -> SessionConfig:
    path = resolve_session_path(session)
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    root = PIPELINE_ROOT
    _load_whisper_env()

    def rel(name: str) -> Path:
        value = data[name]
        p = Path(value)
        return p if p.is_absolute() else (root / p)

    date_raw = data.get("date_index") or ""
    date_index = rel("date_index") if date_raw else None
    journal_raw = data.get("journal_dir") or f"data/unga{int(data['id'])}"
    journal_dir = Path(journal_raw)
    journal_dir = journal_dir if journal_dir.is_absolute() else (root / journal_dir)
    sources = tuple(data.get("sources") or ("pdf_en", "audio_en", "pdf_other", "video"))
    unknown = [s for s in sources if s not in {"pdf_en", "audio_en", "pdf_other", "video"}]
    if unknown:
        raise ValueError(f"sources desconocidos: {unknown}")
    return SessionConfig(
        id=int(data["id"]),
        year=int(data["year"]),
        name=str(data["name"]),
        ordinal=str(data.get("ordinal") or ""),
        gadebate_base=str(data["gadebate_base"]).rstrip("/"),
        slug_file=rel("slug_file"),
        date_index=date_index,
        journal_dir=journal_dir,
        debate_dates=tuple(data.get("debate_dates") or ()),
        sources=sources,
        min_pdf_chars=int(data.get("min_pdf_chars") or 200),
        min_audio_chars=int(data.get("min_audio_chars") or 200),
        whisper_model=_env_or(data, "whisper_model", "WHISPER_MODEL", "base"),
        whisper_device=_env_or(data, "whisper_device", "WHISPER_DEVICE", "cpu"),
        whisper_compute_type=_env_or(
            data, "whisper_compute_type", "WHISPER_COMPUTE_TYPE", "int8"
        ),
        whisper_beam_size=int(
            os.environ.get("WHISPER_BEAM_SIZE") or data.get("whisper_beam_size") or 1
        ),
        whisper_vad=_env_bool(data, "whisper_vad", "WHISPER_VAD", True),
        user_agent=str(data.get("user_agent") or "gwl-pipeline/0.1"),
        root=root,
        config_path=path,
    )


def load_slugs(config: SessionConfig) -> list[str]:
    if not config.slug_file.is_file():
        return []
    slugs: list[str] = []
    for line in config.slug_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        slugs.append(line)
    return slugs


def load_date_index(config: SessionConfig) -> dict[str, str]:
    if not config.date_index or not config.date_index.is_file():
        return {}
    import json

    raw = json.loads(config.date_index.read_text(encoding="utf-8"))
    return {str(k): str(v)[:10] for k, v in raw.items() if v}


def write_slugs(config: SessionConfig, slugs: list[str]) -> None:
    config.slug_file.parent.mkdir(parents=True, exist_ok=True)
    header = f"# Fichas /en/{config.id}/{{slug}} — {config.name}\n"
    config.slug_file.write_text(header + "\n".join(slugs) + "\n", encoding="utf-8")
