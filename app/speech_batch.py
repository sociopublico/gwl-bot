from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Protocol

from app.detector import DetectionEvent
from app.speaker import Speaker
from app.speech_store import SpeechStore, quotes_with_context
from app.transcript import TranscriptSegment

logger = logging.getLogger(__name__)


class SpeechSender(Protocol):
    @property
    def emails_sent(self) -> int: ...

    def send_speech(
        self,
        events: Sequence[DetectionEvent],
        *,
        name: str,
        country: str | None,
        title: str | None,
    ) -> bool: ...

    def notify_status(self, subject: str, body: str) -> bool: ...


class SpeakerBatch:
    """Junta las detecciones de un orador y manda un mail cuando cambia la persona."""

    def __init__(
        self,
        notifier: SpeechSender,
        log_dir: str = "",
        *,
        before_seconds: float = 75.0,
        after_seconds: float = 30.0,
    ) -> None:
        self._notifier = notifier
        self._store = SpeechStore(log_dir)
        self._speaker = Speaker()
        self._open = False
        self._before_seconds = max(before_seconds, 0.0)
        self._after_seconds = max(after_seconds, 0.0)
        self._pending_lines: list[tuple[float, float, str]] = []

    @property
    def emails_sent(self) -> int:
        return self._notifier.emails_sent

    @property
    def store(self) -> SpeechStore:
        return self._store

    def add_window(
        self,
        window_start: float,
        window_end: float,
        text: str,
        segments: Sequence[TranscriptSegment] | None = None,
    ) -> None:
        self._pending_lines.extend(
            _window_lines(window_start, window_end, text, segments)
        )

    def flush_ready(self) -> None:
        return None

    def note_speaker(self, speaker: Speaker) -> None:
        """Si cambió la persona, manda el mail del discurso que termina."""
        if not self._open:
            self._speaker = speaker
            self._open = True
            self._commit_lines()
            self._store.update_meta(speaker)
            return
        if speaker.name.casefold() == self._speaker.name.casefold():
            self._speaker = Speaker(
                name=self._speaker.name,
                title=speaker.title or self._speaker.title,
                country=speaker.country or self._speaker.country,
                confidence=speaker.confidence,
                source=speaker.source,
            )
            self._commit_lines()
            self._store.update_meta(self._speaker)
            return
        carried = list(self._pending_lines)
        self._commit_lines()
        self._send_open()
        self._speaker = speaker
        self._open = True
        self._pending_lines = carried

    def notify(self, events: Sequence[DetectionEvent]) -> None:
        self._commit_lines()
        if events:
            self._store.add(self._speaker, events)

    def notify_status(self, subject: str, body: str) -> bool:
        return self._notifier.notify_status(subject, body)

    def flush(self) -> None:
        """Cortar el proceso no manda el mail: las citas quedan en el archivo."""
        pending = [speech for speech in self._store.list_speeches() if speech.quotes]
        if not pending:
            return
        summary = " | ".join(f"{speech.name}={len(speech.quotes)}" for speech in pending)
        logger.info("SPEECH_MAIL_KEPT | %s", summary)

    def reset(self) -> None:
        """Un corte de stream no cierra el discurso: las citas siguen en el archivo."""
        return None

    def _send_open(self) -> None:
        speech = self._store.take_named(self._speaker.name)
        if speech is None or not speech.quotes:
            return
        logger.info(
            "SPEECH_MAIL | speaker=%s | country=%s | quotes=%s",
            speech.name,
            speech.country or "",
            len(speech.quotes),
        )
        sent = self._notifier.send_speech(
            quotes_with_context(
                speech,
                before_seconds=self._before_seconds,
                after_seconds=self._after_seconds,
            ),
            name=speech.name,
            country=speech.country,
            title=speech.title,
        )
        if not sent:
            self._store.put_back(speech)
            logger.error(
                "SPEECH_MAIL_KEPT | speaker=%s | quotes=%s | send failed",
                speech.name,
                len(speech.quotes),
            )

    def _commit_lines(self) -> None:
        if not self._open or not self._pending_lines:
            return
        self._store.add_lines(self._speaker, self._pending_lines)
        self._pending_lines = []


def _window_lines(
    window_start: float,
    window_end: float,
    text: str,
    segments: Sequence[TranscriptSegment] | None,
) -> list[tuple[float, float, str]]:
    lines: list[tuple[float, float, str]] = []
    if segments:
        for segment in segments:
            piece = " ".join(segment.text.split())
            if not piece:
                continue
            start = window_start + segment.start
            end = window_start + max(segment.end, segment.start)
            lines.append((start, end, piece))
        return lines
    piece = " ".join(text.split())
    if piece:
        lines.append((window_start, window_end, piece))
    return lines
