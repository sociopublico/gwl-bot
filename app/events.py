from __future__ import annotations

import logging
from collections.abc import Sequence

from app.detector import DetectionEvent
from app.logger import KEYWORD_DETECTED
from app.notifier import Notifier

logger = logging.getLogger(__name__)


def emit_detection(event: DetectionEvent) -> None:
    parts = [event.keyword]
    if event.speaker and event.speaker != "unknown":
        parts.append(event.speaker)
    if event.video_seconds is not None:
        parts.append(f"t={int(round(event.video_seconds))}s")
    parts.append(event.context)
    if event.watch_url:
        parts.append(event.watch_url)
    logger.log(KEYWORD_DETECTED, "%s", " | ".join(parts))
    logger.debug(
        "Detection detail | ts=%s | keyword=%s | speaker=%s | transcript=%s",
        event.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        event.keyword,
        event.speaker,
        event.transcript,
    )


def emit_detections(events: Sequence[DetectionEvent], notifier: Notifier) -> None:
    """Loguea cada detección y manda como máximo un email por chunk."""
    for event in events:
        emit_detection(event)
    if events:
        notifier.notify(events)
