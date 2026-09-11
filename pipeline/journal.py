from __future__ import annotations

from pathlib import Path

from pipeline.config import SessionConfig


def journal_path(config: SessionConfig, day: str) -> Path:
    return config.journal_dir / f"{day}.txt"


def load_journal_slugs(config: SessionConfig, day: str) -> list[str] | None:
    """Orden UN Journal del día. None si no hay archivo (o está vacío)."""
    path = journal_path(config, day)
    if not path.is_file():
        return None
    slugs: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        slugs.append(line.split()[0])
    return slugs or None
