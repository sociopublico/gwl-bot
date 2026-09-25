from __future__ import annotations

import json
import sys
from pathlib import Path

from pipeline.config import SessionConfig
from pipeline.store import list_speech_txts, parse_speech_txt


def merge_speeches(
    config: SessionConfig,
    *,
    speech_date: str | None = None,
    dest: Path | None = None,
    english_only: bool = False,
) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for path in list_speech_txts(config, speech_date=speech_date, dest=dest):
        try:
            speech = parse_speech_txt(path)
        except (OSError, ValueError) as exc:
            print(f"SKIP {path} {exc}", file=sys.stderr)
            continue
        if english_only and speech.language.strip().lower() not in {"en", "english"}:
            continue
        records.append(
            {
                "name": speech.name,
                "country": speech.country,
                "date": speech.speech_date,
                "speech": speech.text,
            }
        )
    records.sort(key=lambda r: (r["date"] or "9999", r["country"]))
    return records


def default_merge_path(config: SessionConfig, *, dest: Path | None = None) -> Path:
    root = dest or (config.root / "out")
    return root / str(config.id) / "speeches.json"


def write_merged(records: list[dict[str, str]], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
