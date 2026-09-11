from __future__ import annotations

import logging
import sys

KEYWORD_DETECTED = 25
SPEAKER_CHANGED = 26
logging.addLevelName(KEYWORD_DETECTED, "KEYWORD_DETECTED")
logging.addLevelName(SPEAKER_CHANGED, "SPEAKER_CHANGED")


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # yt-dlp / huggingface pueden ser ruidosos; los dejamos en WARNING.
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
