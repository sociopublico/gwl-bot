from __future__ import annotations

import re
import sys
from pathlib import Path

from pipeline.config import SessionConfig
from pipeline.store import list_speech_txts, parse_speech_txt

_ID_NUMBER = re.compile(r"(\d+)$")


def _order_key(date: str, speech_id: str, country: str) -> tuple[str, float, str]:
    match = _ID_NUMBER.search(speech_id or "")
    return (date or "9999", float(match.group(1)) if match else float("inf"), country)


def merge_speeches(
    config: SessionConfig,
    *,
    speech_date: str | None = None,
    dest: Path | None = None,
    english_only: bool = False,
) -> list[str]:
    entries: list[tuple[tuple[str, float, str], str]] = []
    for path in list_speech_txts(config, speech_date=speech_date, dest=dest):
        try:
            speech = parse_speech_txt(path)
            raw = path.read_text(encoding="utf-8")
        except (OSError, ValueError) as exc:
            print(f"SKIP {path} {exc}", file=sys.stderr)
            continue
        if english_only and speech.language.strip().lower() not in {"en", "english"}:
            continue
        key = _order_key(speech.speech_date, speech.id_speech, speech.country)
        entries.append((key, raw.strip()))
    entries.sort(key=lambda item: item[0])
    return [raw for _, raw in entries]


def default_merge_path(config: SessionConfig, *, dest: Path | None = None) -> Path:
    root = dest or (config.root / "out")
    return root / str(config.id) / "speeches.txt"


def render_merged(speeches: list[str]) -> str:
    return "\n\n".join(speeches) + "\n" if speeches else ""


def write_merged(speeches: list[str], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_merged(speeches), encoding="utf-8")
    return path
