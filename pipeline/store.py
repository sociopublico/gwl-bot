from __future__ import annotations

import json
from pathlib import Path

from pipeline.config import SessionConfig, load_date_index
from pipeline.metadata import transformation_for
from pipeline.models import ExtractedSpeech


def out_dir(config: SessionConfig, speech_date: str, *, dest: Path | None = None) -> Path:
    root = dest or (config.root / "out")
    day = speech_date or "unknown-date"
    path = root / str(config.id) / day
    path.mkdir(parents=True, exist_ok=True)
    return path


def is_english_transcript(path: Path) -> bool:
    try:
        lang = (parse_speech_txt(path).language or "").strip().lower()
    except (OSError, ValueError):
        return False
    return lang in {"en", "english"}


def find_existing_speech(
    config: SessionConfig,
    slug: str,
    *,
    dest: Path | None = None,
) -> Path | None:
    root = dest or (config.root / "out")
    session_dir = root / str(config.id)
    if not session_dir.is_dir():
        return None
    day = load_date_index(config).get(slug)
    if day:
        path = session_dir / day / f"{slug}.txt"
        if path.is_file():
            return path
    matches = sorted(session_dir.glob(f"*/{slug}.txt"))
    return matches[0] if matches else None


def list_speech_txts(
    config: SessionConfig,
    *,
    speech_date: str | None = None,
    dest: Path | None = None,
) -> list[Path]:
    root = dest or (config.root / "out")
    session_dir = root / str(config.id)
    if not session_dir.is_dir():
        return []
    if speech_date:
        folder = session_dir / speech_date
        if not folder.is_dir():
            return []
        return sorted(p for p in folder.glob("*.txt"))
    return sorted(session_dir.glob("*/*.txt"))


def speech_relpath(path: Path, *, repo_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo_root.resolve()))
    except ValueError:
        return str(path)


def speech_to_txt(speech: ExtractedSpeech) -> str:
    header = [
        "---",
        f"session: {speech.session_id}",
        f"slug: {speech.slug}",
        f"country: {speech.country}",
        f"speaker: {speech.name}",
        f"title: {speech.rank}",
        f"speaker_title: {speech.speaker_title}",
        f"date: {speech.speech_date}",
        f"source: {speech.source}",
        f"source_url: {speech.source_url}",
        f"language: {speech.language}",
        f"original_language: {speech.original_language}",
        f"transformation: {speech.transformation or transformation_for(speech.source)}",
        "---",
        "",
        speech.text.strip(),
        "",
    ]
    return "\n".join(header)


def parse_speech_txt(path: Path) -> ExtractedSpeech:
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---"):
        raise ValueError(f"{path} no tiene encabezado YAML")
    parts = raw.split("---", 2)
    if len(parts) < 3:
        raise ValueError(f"{path} YAML incompleto")
    meta: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    source = meta.get("source", "")
    return ExtractedSpeech(
        session_id=int(meta.get("session") or 0),
        slug=meta.get("slug") or path.stem,
        country=meta.get("country", ""),
        name=meta.get("speaker", ""),
        rank=meta.get("title", ""),
        speech_date=meta.get("date", ""),
        source=source,
        source_url=meta.get("source_url", ""),
        language=meta.get("language", ""),
        text=parts[2].strip(),
        speaker_title=meta.get("speaker_title", ""),
        original_language=meta.get("original_language", ""),
        transformation=meta.get("transformation") or transformation_for(source),
    )


def write_speech(speech: ExtractedSpeech, directory: Path) -> Path:
    path = directory / f"{speech.slug}.txt"
    path.write_text(speech_to_txt(speech), encoding="utf-8")
    return path


def append_manifest(directory: Path, speech: ExtractedSpeech, txt_path: Path) -> None:
    record = {
        "session": speech.session_id,
        "slug": speech.slug,
        "country": speech.country,
        "speaker": speech.name,
        "title": speech.rank,
        "speaker_title": speech.speaker_title,
        "date": speech.speech_date,
        "source": speech.source,
        "source_url": speech.source_url,
        "language": speech.language,
        "original_language": speech.original_language,
        "transformation": speech.transformation,
        "chars": len(speech.text),
        "txt": str(txt_path),
        "skipped": speech.skipped,
    }
    with (directory / "manifest.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
