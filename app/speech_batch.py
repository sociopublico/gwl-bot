from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Protocol

from app.detector import DetectionEvent
from app.speaker import Speaker
from app.speech_store import SpeechStore

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

    def __init__(self, notifier: SpeechSender, log_dir: str = "") -> None:
        self._notifier = notifier
        self._store = SpeechStore(log_dir)
        self._speaker = Speaker()
        self._open = False

    @property
    def emails_sent(self) -> int:
        return self._notifier.emails_sent

    @property
    def store(self) -> SpeechStore:
        return self._store

    def add_window(self, *_args: object, **_kwargs: object) -> None:
        return None

    def flush_ready(self) -> None:
        return None

    def note_speaker(self, speaker: Speaker) -> None:
        """Si cambió la persona, manda el mail del discurso que termina."""
        if not self._open:
            self._speaker = speaker
            self._open = True
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
            self._store.update_meta(self._speaker)
            return
        self._send_open()
        self._speaker = speaker
        self._open = True

    def notify(self, events: Sequence[DetectionEvent]) -> None:
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
            speech.quotes,
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
