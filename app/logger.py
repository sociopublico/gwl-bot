from __future__ import annotations

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

KEYWORD_DETECTED = 25
SPEAKER_CHANGED = 26
logging.addLevelName(KEYWORD_DETECTED, "KEYWORD_DETECTED")
logging.addLevelName(SPEAKER_CHANGED, "SPEAKER_CHANGED")

_HIGHLIGHT_LEVELS = frozenset({KEYWORD_DETECTED, SPEAKER_CHANGED, logging.WARNING, logging.ERROR})
_HIGHLIGHT_MARKERS = (
    "Email alert sent",
    "Email alert failed",
    "EMAIL_SKIPPED_COOLDOWN",
    "SESSION_",
    "Shutting down",
    "Stream connected",
    "Stream ended",
    "Email alerts enabled",
    "Email alerts disabled",
)


class FlushingStreamHandler(logging.StreamHandler):
    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self.flush()


class FlushingTimedRotatingFileHandler(TimedRotatingFileHandler):
    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self.flush()


class HighlightsFilter(logging.Filter):
    """Deja pasar speakers, keywords, email, errores y marcas de sesión."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno in _HIGHLIGHT_LEVELS or record.levelno >= logging.WARNING:
            return True
        try:
            message = record.getMessage()
        except Exception:
            return False
        return any(marker in message for marker in _HIGHLIGHT_MARKERS)


def _formatter() -> logging.Formatter:
    return logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def setup_logging(level: str = "INFO", log_dir: str | Path | None = None) -> Path | None:
    """Consola + full.log + highlights.log. Flush en cada línea. Retorna el dir usado."""
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level.upper())

    formatter = _formatter()

    console = FlushingStreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    resolved: Path | None = None
    if log_dir:
        resolved = Path(log_dir)
        resolved.mkdir(parents=True, exist_ok=True)

        full = FlushingTimedRotatingFileHandler(
            resolved / "full.log",
            when="midnight",
            interval=1,
            backupCount=14,
            encoding="utf-8",
        )
        full.setFormatter(formatter)
        root.addHandler(full)

        highlights = FlushingTimedRotatingFileHandler(
            resolved / "highlights.log",
            when="midnight",
            interval=1,
            backupCount=14,
            encoding="utf-8",
        )
        highlights.setFormatter(formatter)
        highlights.addFilter(HighlightsFilter())
        root.addHandler(highlights)

    # yt-dlp / huggingface pueden ser ruidosos; los dejamos en WARNING.
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    return resolved
