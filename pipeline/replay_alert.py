"""Arma el mail de un discurso desde pdf_en o transcript_ai, sin el monitor en vivo.

No escribe logs/speeches.json ni logs/keywords.jsonl. No carga Whisper.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from app.detector import DetectionEvent, _keyword_pattern
from app.notifier import build_speech_email
from pipeline.cascade import SourceUnavailable
from pipeline.config import SessionConfig
from pipeline.gadebate import scrape_speaker
from pipeline.models import ExtractedSpeech, SpeakerPage
from pipeline.roster import load_roster, page_from_entry
from pipeline.run import _extract_source

REPLAY_SOURCES = frozenset({"pdf_en", "transcript_ai"})
WORDS_PER_MINUTE = 140.0

ExtractFn = Callable[[SessionConfig, SpeakerPage, str], tuple[ExtractedSpeech, str]]
ScrapeFn = Callable[[SessionConfig, str], SpeakerPage]
SendFn = Callable[[str, str, str], None]


def load_monitor_keywords() -> tuple[str, ...]:
    """Las mismas KEYWORDS del monitor. No exige STREAM_URL."""
    from app.config import _parse_keywords
    from pipeline.env import load_dotenv

    load_dotenv()
    return _parse_keywords(os.environ.get("KEYWORDS", ""))


def monitor_context_seconds() -> tuple[float, float]:
    from pipeline.env import load_dotenv

    load_dotenv()
    before = _env_float("ALERT_TEXT_BEFORE_SECONDS", 75.0)
    after = _env_float("ALERT_TEXT_AFTER_SECONDS", 30.0)
    return before, after


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _key(value: str) -> str:
    return " ".join(value.casefold().split())


def _matches(entry: dict, country: str) -> bool:
    query = _key(country)
    if not query:
        return False
    return query in {_key(str(entry.get("country") or "")), _key(str(entry.get("slug") or ""))}


def find_country(
    config: SessionConfig,
    country: str,
    day: str | None,
) -> list[tuple[str, dict]]:
    if day:
        payload = load_roster(config, day)
        entries = (payload or {}).get("speakers") or []
        return [(day, entry) for entry in entries if _matches(entry, country)]

    directory = config.root / "data" / "roster" / str(config.id)
    if not directory.is_dir():
        return []
    found: list[tuple[str, dict]] = []
    for path in sorted(directory.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        file_day = str(payload.get("day") or path.stem)
        for entry in payload.get("speakers") or []:
            if _matches(entry, country):
                found.append((file_day, entry))
    return found


def _words_for(seconds: float) -> int:
    return max(0, round(WORDS_PER_MINUTE * max(seconds, 0.0) / 60.0))


def passage_around(
    text: str,
    start: int,
    end: int,
    *,
    before_words: int,
    after_words: int,
) -> str:
    words = list(re.finditer(r"\S+", text))
    if not words:
        return ""
    hit = 0
    for index, word in enumerate(words):
        if word.start() <= start < word.end():
            hit = index
            break
        if word.start() > start:
            hit = index
            break
    end_index = hit
    for index, word in enumerate(words):
        if word.start() < end:
            end_index = index
    lo = max(0, hit - before_words)
    hi = min(len(words), end_index + 1 + after_words)
    return " ".join(word.group(0) for word in words[lo:hi])


def quote_events(
    text: str,
    keywords: tuple[str, ...],
    *,
    speaker: str,
    title: str,
    speech_start: datetime,
    before_seconds: float,
    after_seconds: float,
) -> list[DetectionEvent]:
    flat = " ".join(text.split())
    before_words = _words_for(before_seconds)
    after_words = _words_for(after_seconds)
    events: list[DetectionEvent] = []
    for keyword in keywords:
        for match in _keyword_pattern(keyword).finditer(flat):
            seconds = len(flat[: match.start()].split()) / WORDS_PER_MINUTE * 60.0
            passage = passage_around(
                flat,
                match.start(),
                match.end(),
                before_words=before_words,
                after_words=after_words,
            )
            events.append(
                DetectionEvent(
                    timestamp=speech_start + timedelta(seconds=seconds),
                    keyword=keyword,
                    transcript=flat,
                    context=passage,
                    mail_context=passage,
                    speaker=speaker,
                    speaker_title=title or None,
                    video_seconds=seconds,
                    timestamp_reliable=False,
                )
            )
    events.sort(key=lambda event: (event.video_seconds or 0.0, event.keyword.casefold()))
    return events


def _speech_start(day: str) -> datetime:
    try:
        stamp = datetime.strptime(day[:10], "%Y-%m-%d")
    except ValueError:
        stamp = datetime.now(timezone.utc).replace(tzinfo=None)
    return stamp.replace(tzinfo=timezone.utc)


def render_replay(
    events: list[DetectionEvent],
    *,
    name: str,
    country: str,
    title: str,
    source: str,
) -> tuple[str, str, str]:
    subject, body, html = build_speech_email(
        events,
        name=name,
        country=country,
        title=title,
    )
    line = f"Replay | source={source}"
    body = f"{line}\n{body}"
    html = f"<p><b>{line}</b></p>{html}"
    return subject, body, html


def _ensure_source(
    config: SessionConfig,
    page: SpeakerPage,
    source: str,
    scrape: ScrapeFn,
) -> SpeakerPage:
    if getattr(page, source, None) is not None:
        return page
    fresh = scrape(config, page.slug)
    if getattr(fresh, source, None) is None:
        detail = f": {fresh.error}" if fresh.error else ""
        raise SourceUnavailable(f"{source} no está en la ficha de {page.slug}{detail}")
    if not fresh.country:
        fresh.country = page.country
    if not fresh.name:
        fresh.name = page.name
    if not fresh.speech_date:
        fresh.speech_date = page.speech_date
    return fresh


def run_replay(
    config: SessionConfig,
    *,
    country: str,
    source: str,
    day: str | None,
    keywords: tuple[str, ...],
    send: bool,
    before_seconds: float = 75.0,
    after_seconds: float = 30.0,
    extract: ExtractFn = _extract_source,
    scrape: ScrapeFn = scrape_speaker,
    sender: SendFn | None = None,
) -> int:
    if source not in REPLAY_SOURCES:
        print(
            f"error: --source debe ser pdf_en o transcript_ai, no {source!r}",
            file=sys.stderr,
        )
        return 1

    matches = find_country(config, country, day)
    if not matches:
        where = f" en {day}" if day else ""
        print(f"error: no encuentro {country!r}{where}", file=sys.stderr)
        return 1
    days = sorted({item[0] for item in matches})
    if day is None and len(days) > 1:
        print(
            f"error: {country!r} está en más de un día: {', '.join(days)}. Pasá --day.",
            file=sys.stderr,
        )
        return 1
    if len(matches) > 1:
        listed = ", ".join(
            f"{item_day} {entry.get('slug')}" for item_day, entry in matches
        )
        print(f"error: hay más de una ficha para {country!r}: {listed}", file=sys.stderr)
        return 1

    match_day, entry = matches[0]
    page = page_from_entry(entry)
    if not page.speech_date:
        page.speech_date = match_day
    try:
        page = _ensure_source(config, page, source, scrape)
        speech, _via = extract(config, page, source)
    except SourceUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    events = quote_events(
        speech.text,
        keywords,
        speaker=speech.name or page.name,
        title=speech.rank or page.rank or page.speaker_title,
        speech_start=_speech_start(speech.speech_date or match_day),
        before_seconds=before_seconds,
        after_seconds=after_seconds,
    )
    if not events:
        print(f"No hay keywords en {page.slug} ({source}).", file=sys.stderr)
        return 0

    subject, body, html = render_replay(
        events,
        name=speech.name or page.name,
        country=speech.country or page.country,
        title=speech.rank or page.rank or page.speaker_title,
        source=source,
    )
    print(f"Subject: {subject}\n")
    print(body, end="")
    if not send:
        return 0
    deliver = sender or _smtp_send
    try:
        deliver(subject, body, html)
    except Exception as exc:
        print(f"error: no se pudo mandar el mail: {exc}", file=sys.stderr)
        return 1
    print("Mail enviado.", file=sys.stderr)
    return 0


def _smtp_send(subject: str, body: str, html: str) -> None:
    from app.config import Config
    from app.notifier import EmailNotifier, active_smtp_credentials

    monitor = Config.from_env()
    if not monitor.email_enabled:
        raise RuntimeError("SMTP no está configurado (SMTP_HOST, SMTP_FROM, ALERT_EMAIL_TO)")
    notifier = EmailNotifier(monitor)
    notifier._send(subject, body, active_smtp_credentials(monitor), html=html)
