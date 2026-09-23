from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace

from app.detector import DetectionEvent
from app.notifier import Notifier
from app.transcript import TranscriptSegment

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Line:
    start: float
    end: float
    text: str


@dataclass
class _Pending:
    events: list[DetectionEvent]
    hit: float
    ready_at: float


def passage_between(lines: Sequence[_Line], start: float, end: float) -> str:
    """Une segmentos que caen en [start, end] y descarta los que ya quedaron cubiertos.

    El overlap de chunks retranscribe el mismo tramo. Si un segmento termina
    dentro de lo ya incluido, se salta. Si cruza el borde, se queda: pierde
    unas palabras repetidas y no el discurso nuevo.
    """
    chosen = [line for line in lines if line.start < end and line.end > start and line.text]
    chosen.sort(key=lambda line: (line.start, line.end))
    kept: list[_Line] = []
    covered_until = start
    for line in chosen:
        if kept and line.end <= covered_until + 0.3:
            continue
        kept.append(line)
        covered_until = max(covered_until, line.end)
    return " ".join(line.text for line in kept)


class AlertContext:
    """Demora el mail hasta tener texto de después de la keyword."""

    def __init__(
        self,
        notifier: Notifier,
        *,
        before_seconds: float,
        after_seconds: float,
    ) -> None:
        self._notifier = notifier
        self.before_seconds = max(before_seconds, 0.0)
        self.after_seconds = max(after_seconds, 0.0)
        self._lines: list[_Line] = []
        self._pending: list[_Pending] = []
        self._covered_until = 0.0
        self._fallback_hit = 0.0

    @property
    def emails_sent(self) -> int:
        return self._notifier.emails_sent

    def add_window(
        self,
        window_start: float,
        window_end: float,
        text: str,
        segments: Sequence[TranscriptSegment] | None = None,
    ) -> None:
        self._fallback_hit = window_start
        self._covered_until = max(self._covered_until, window_end)
        if segments:
            for segment in segments:
                piece = " ".join(segment.text.split())
                if not piece:
                    continue
                start = window_start + segment.start
                end = window_start + max(segment.end, segment.start)
                self._lines.append(_Line(start, end, piece))
        else:
            piece = " ".join(text.split())
            if piece:
                self._lines.append(_Line(window_start, window_end, piece))
        self._trim()

    def notify(self, events: Sequence[DetectionEvent]) -> None:
        if not events:
            return
        if self.before_seconds <= 0 and self.after_seconds <= 0:
            self._notifier.notify(events)
            return

        pending_keys = {
            event.keyword.casefold() for pending in self._pending for event in pending.events
        }
        fresh = [event for event in events if event.keyword.casefold() not in pending_keys]
        if not fresh:
            logger.info(
                "EMAIL_WAITING | keywords=%s",
                ", ".join(dict.fromkeys(event.keyword for event in events)),
            )
            return

        hit = _hit_seconds(fresh, self._fallback_hit)
        pending = _Pending(events=list(fresh), hit=hit, ready_at=hit + self.after_seconds)
        self._pending.append(pending)
        if self._covered_until + 1e-6 < pending.ready_at:
            logger.info(
                "EMAIL_WAITING_CONTEXT | keywords=%s | hit=%.1fs | need_until=%.1fs | have=%.1fs",
                ", ".join(dict.fromkeys(event.keyword for event in fresh)),
                hit,
                pending.ready_at,
                self._covered_until,
            )
        self.flush_ready()

    def notify_status(self, subject: str, body: str) -> bool:
        return self._notifier.notify_status(subject, body)

    def flush_ready(self) -> None:
        due: list[_Pending] = []
        waiting: list[_Pending] = []
        for pending in self._pending:
            if self._covered_until + 1e-6 >= pending.ready_at:
                due.append(pending)
            else:
                waiting.append(pending)
        self._pending = waiting
        for pending in due:
            self._deliver(pending)

    def flush(self) -> None:
        if not self._pending:
            return
        logger.info(
            "EMAIL_CONTEXT_PARTIAL | pending=%s | covered=%.1fs",
            len(self._pending),
            self._covered_until,
        )
        pending = self._pending
        self._pending = []
        for item in pending:
            self._deliver(item)

    def reset(self) -> None:
        self._lines.clear()
        self._pending.clear()
        self._covered_until = 0.0
        self._fallback_hit = 0.0

    def _deliver(self, pending: _Pending) -> None:
        start = pending.hit - self.before_seconds
        end = pending.hit + self.after_seconds
        passage = passage_between(self._lines, start, end)
        events: Sequence[DetectionEvent]
        if passage:
            events = [replace(event, mail_context=passage) for event in pending.events]
        else:
            events = pending.events
        self._notifier.notify(events)
        self._trim()

    def _trim(self) -> None:
        if self._pending:
            keep_from = min(pending.hit for pending in self._pending) - self.before_seconds
        else:
            keep_from = self._covered_until - self.before_seconds
        self._lines = [line for line in self._lines if line.end >= keep_from - 1.0]


def _hit_seconds(events: Sequence[DetectionEvent], fallback: float) -> float:
    hits = [event.video_seconds for event in events if event.video_seconds is not None]
    if not hits:
        return fallback
    return min(hits)
