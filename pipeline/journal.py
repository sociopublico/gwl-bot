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


def write_journal_slugs(
    config: SessionConfig,
    day: str,
    slugs: list[str],
    *,
    parts: list[str] | None = None,
    overwrite: bool = False,
) -> Path | None:
    """Persiste el orden del día. No pisa un journal existente salvo overwrite."""
    path = journal_path(config, day)
    if path.is_file() and not overwrite:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# Schedule gadebate /en {day}"]
    last_part = None
    for index, slug in enumerate(slugs):
        part = parts[index] if parts and index < len(parts) else None
        if part and part != last_part:
            lines.append(f"# {part}")
            last_part = part
        lines.append(slug)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
