from __future__ import annotations

import logging
import time
from collections.abc import Sequence

from app.detector import DetectionEvent
from app.logger import KEYWORD_DETECTED
from app.notifier import Notifier

logger = logging.getLogger(__name__)


class DetectionDeduper:
    """Evita repetir el mismo KEYWORD_DETECTED por overlap de chunks."""

    def __init__(self, ttl_seconds: float = 30.0) -> None:
        self._ttl = max(ttl_seconds, 1.0)
        self._seen: dict[tuple[str, str], float] = {}

    def filter(
        self,
        events: Sequence[DetectionEvent],
        *,
        now: float | None = None,
    ) -> list[DetectionEvent]:
        stamp = time.monotonic() if now is None else now
        self._prune(stamp)
        kept: list[DetectionEvent] = []
        for event in events:
            key = (event.keyword.casefold(), event.context)
            previous = self._seen.get(key)
            if previous is not None and stamp - previous < self._ttl:
                continue
            self._seen[key] = stamp
            kept.append(event)
        return kept

    def _prune(self, now: float) -> None:
        cutoff = now - self._ttl
        stale = [key for key, seen_at in self._seen.items() if seen_at < cutoff]
        for key in stale:
            del self._seen[key]


_default_deduper = DetectionDeduper()


def emit_detection(event: DetectionEvent) -> None:
    parts = [event.keyword]
    if event.speaker and event.speaker != "unknown":
        parts.append(event.speaker)
    if event.video_seconds is not None:
        tag = f"t={int(round(event.video_seconds))}s"
        if not event.timestamp_reliable:
            tag += "?"
        parts.append(tag)
    parts.append(event.context)
    logger.log(KEYWORD_DETECTED, "%s", " | ".join(parts))
    logger.debug(
        "Detection detail | ts=%s | keyword=%s | speaker=%s | transcript=%s",
        event.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        event.keyword,
        event.speaker,
        event.transcript,
    )


def emit_detections(
    events: Sequence[DetectionEvent],
    notifier: Notifier,
    *,
    deduper: DetectionDeduper | None = None,
) -> list[DetectionEvent]:
    """Loguea cada detección (con dedup) y manda como máximo un email por chunk."""
    active = deduper if deduper is not None else _default_deduper
    unique = active.filter(events)
    for event in unique:
        emit_detection(event)
    if unique:
        notifier.notify(unique)
    return unique
