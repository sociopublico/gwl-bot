from __future__ import annotations

import fcntl
import json
import logging
import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.detector import DetectionEvent
from app.keyword_journal import event_to_record
from app.speaker import Speaker

logger = logging.getLogger(__name__)

SPEECHES_FILE = "speeches.json"
_PAREN_RE = re.compile(r"\([^)]*\)")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def country_slug(country: str) -> str:
    """Libya → libya. Iran (Islamic Republic of) → iran."""
    text = _PAREN_RE.sub(" ", country.strip().lower())
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return _SLUG_RE.sub("-", text).strip("-")


def event_from_record(record: dict) -> DetectionEvent:
    raw = str(record.get("timestamp") or "")
    stamp = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    seconds = record.get("video_seconds")
    title = str(record.get("speaker_title") or "").strip()
    watch = str(record.get("watch_url") or "").strip()
    webtv = str(record.get("webtv_url") or "").strip()
    speaker = str(record.get("speaker") or "").strip() or "unknown"
    return DetectionEvent(
        timestamp=stamp,
        keyword=str(record.get("keyword") or ""),
        transcript=str(record.get("context") or ""),
        context=str(record.get("context") or ""),
        speaker=speaker,
        speaker_title=title or None,
        video_seconds=float(seconds) if seconds is not None else None,
        watch_url=watch or None,
        webtv_url=webtv or None,
        timestamp_reliable=bool(record.get("timestamp_reliable", True)),
    )


@dataclass
class StoredSpeech:
    name: str
    country: str | None
    title: str | None
    quotes: list[DetectionEvent] = field(default_factory=list)

    @property
    def slug(self) -> str:
        return country_slug(self.country or "")


def _find_name(speeches: list[StoredSpeech], name: str) -> StoredSpeech | None:
    folded = name.casefold()
    for speech in speeches:
        if speech.name.casefold() == folded:
            return speech
    return None


def _select(
    speeches: Sequence[StoredSpeech],
    *,
    name: str | None,
    country: str | None,
) -> list[StoredSpeech]:
    if name and name.strip():
        query = name.strip().casefold()
        exact = [speech for speech in speeches if speech.name.casefold() == query]
        if exact:
            return exact
        return [speech for speech in speeches if query in speech.name.casefold()]
    if country and country.strip():
        wanted = country_slug(country)
        if not wanted:
            return []
        return [speech for speech in speeches if speech.slug == wanted]
    return []


class SpeechStore:
    """Citas sin mandar. Con log_dir escribe logs/speeches.json; si no, queda en memoria."""

    def __init__(self, directory: str | Path | None) -> None:
        raw = str(directory or "").strip()
        self._path = Path(raw) / SPEECHES_FILE if raw else None
        self._speeches: list[StoredSpeech] = []

    @property
    def path(self) -> Path | None:
        return self._path

    def add(self, speaker: Speaker, events: Sequence[DetectionEvent]) -> None:
        if not events:
            return

        def mutate(speeches: list[StoredSpeech]) -> None:
            speech = _find_name(speeches, speaker.name)
            if speech is None:
                speech = StoredSpeech(
                    name=speaker.name,
                    country=speaker.country,
                    title=speaker.title,
                )
                speeches.append(speech)
            else:
                if speaker.country:
                    speech.country = speaker.country
                if speaker.title:
                    speech.title = speaker.title
            speech.quotes.extend(events)

        self._update(mutate)

    def update_meta(self, speaker: Speaker) -> None:
        def mutate(speeches: list[StoredSpeech]) -> None:
            speech = _find_name(speeches, speaker.name)
            if speech is None:
                return
            if speaker.country:
                speech.country = speaker.country
            if speaker.title:
                speech.title = speaker.title

        self._update(mutate)

    def list_speeches(self) -> list[StoredSpeech]:
        return [_copy(speech) for speech in self._read()]

    def take_named(self, name: str) -> StoredSpeech | None:
        taken: list[StoredSpeech | None] = [None]

        def mutate(speeches: list[StoredSpeech]) -> None:
            speech = _find_name(speeches, name)
            if speech is None or not speech.quotes:
                return
            speeches.remove(speech)
            taken[0] = speech

        self._update(mutate)
        return taken[0]

    def take_match(
        self,
        *,
        name: str | None,
        country: str | None,
    ) -> tuple[str, StoredSpeech | None]:
        """'ok' y el discurso, 'missing' o 'ambiguous'. Solo saca el archivo si hay uno solo."""
        found: list[StoredSpeech] = []

        def mutate(speeches: list[StoredSpeech]) -> None:
            matches = _select(speeches, name=name, country=country)
            found.extend(matches)
            if len(matches) == 1:
                speeches.remove(matches[0])

        self._update(mutate)
        if len(found) == 1:
            return "ok", found[0]
        if len(found) > 1:
            return "ambiguous", None
        return "missing", None

    def put_back(self, speech: StoredSpeech) -> None:
        def mutate(speeches: list[StoredSpeech]) -> None:
            current = _find_name(speeches, speech.name)
            if current is None:
                speeches.append(speech)
                return
            if speech.country and not current.country:
                current.country = speech.country
            if speech.title and not current.title:
                current.title = speech.title
            current.quotes = list(speech.quotes) + list(current.quotes)

        self._update(mutate)

    def _update(self, mutate: Callable[[list[StoredSpeech]], None]) -> None:
        if self._path is None:
            mutate(self._speeches)
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._path.with_suffix(".lock")
        with lock_path.open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            speeches = self._read_file()
            if speeches is None:
                return
            mutate(speeches)
            self._write_file(speeches)

    def _read(self) -> list[StoredSpeech]:
        if self._path is None:
            return self._speeches
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._path.with_suffix(".lock")
        with lock_path.open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            speeches = self._read_file()
        return speeches or []

    def _read_file(self) -> list[StoredSpeech] | None:
        assert self._path is not None
        if not self._path.is_file():
            return []
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.error("Speech store unreadable | path=%s | %s", self._path, exc)
            return None
        if not raw.strip():
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error("Speech store unreadable | path=%s | %s", self._path, exc)
            return None
        items = data.get("speeches") if isinstance(data, dict) else None
        if not isinstance(items, list):
            logger.error("Speech store unreadable | path=%s | speeches missing", self._path)
            return None
        speeches: list[StoredSpeech] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip() or "unknown"
            country = str(item.get("country") or "").strip() or None
            title = str(item.get("title") or "").strip() or None
            quotes_raw = item.get("quotes")
            quotes: list[DetectionEvent] = []
            if isinstance(quotes_raw, list):
                for record in quotes_raw:
                    if isinstance(record, dict) and record.get("keyword"):
                        quotes.append(event_from_record(record))
            speeches.append(StoredSpeech(name=name, country=country, title=title, quotes=quotes))
        return speeches

    def _write_file(self, speeches: list[StoredSpeech]) -> None:
        assert self._path is not None
        payload = {
            "speeches": [
                {
                    "name": speech.name,
                    "country": speech.country or "",
                    "title": speech.title or "",
                    "slug": speech.slug,
                    "quotes": [event_to_record(event) for event in speech.quotes],
                }
                for speech in speeches
                if speech.quotes
            ]
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self._path)


def _copy(speech: StoredSpeech) -> StoredSpeech:
    return StoredSpeech(
        name=speech.name,
        country=speech.country,
        title=speech.title,
        quotes=list(speech.quotes),
    )
