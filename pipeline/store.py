from __future__ import annotations

import json
from pathlib import Path

from pipeline.config import SessionConfig, load_date_index
from pipeline.metadata import normalize_speech_id, transformation_for
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


def find_speech_in_dir(directory: Path, slug: str) -> Path | None:
    if not directory.is_dir():
        return None
    direct = directory / f"{slug}.txt"
    if direct.is_file():
        return direct
    for path in sorted(directory.glob("*.txt")):
        if path.stem == slug:
            return path
        try:
            if parse_speech_txt(path).slug == slug:
                return path
        except (OSError, ValueError):
            continue
    return None


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
        found = find_speech_in_dir(session_dir / day, slug)
        if found:
            return found
    matches: list[Path] = []
    for path in sorted(session_dir.glob("*/*.txt")):
        if path.stem == slug:
            matches.append(path)
            continue
        try:
            if parse_speech_txt(path).slug == slug:
                matches.append(path)
        except (OSError, ValueError):
            continue
    return matches[0] if matches else None


def existing_speech_id_labels(
    config: SessionConfig,
    *,
    dest: Path | None = None,
) -> list[str]:
    labels: list[str] = []
    for path in list_speech_txts(config, dest=dest):
        label = normalize_speech_id(path.stem)
        if not label:
            try:
                label = normalize_speech_id(parse_speech_txt(path).id_speech)
            except (OSError, ValueError):
                label = ""
        if label:
            labels.append(label)
    return labels


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
    ]
    speech_id = normalize_speech_id(speech.id_speech)
    if speech_id:
        header.append(f"id_speech: {speech_id}")
    header.extend(
        [
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
    )
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
    speech_id = normalize_speech_id(meta.get("id_speech", "")) or normalize_speech_id(
        path.stem
    )
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
        id_speech=speech_id,
    )


def write_speech(speech: ExtractedSpeech, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    existing = find_speech_in_dir(directory, speech.slug)
    if existing and not normalize_speech_id(speech.id_speech):
        try:
            speech.id_speech = parse_speech_txt(existing).id_speech
        except (OSError, ValueError):
            speech.id_speech = normalize_speech_id(existing.stem)
    speech.id_speech = normalize_speech_id(speech.id_speech)
    name = f"{speech.id_speech}.txt" if speech.id_speech else f"{speech.slug}.txt"
    path = directory / name
    path.write_text(speech_to_txt(speech), encoding="utf-8")
    if existing and existing.resolve() != path.resolve() and existing.is_file():
        existing.unlink()
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
