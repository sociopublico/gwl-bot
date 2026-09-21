from __future__ import annotations

import logging
import time
from pathlib import Path

from app.config import Config
from app.notifier import Notifier

logger = logging.getLogger(__name__)

HEARTBEAT_NAME = ".heartbeat"


def heartbeat_path(log_dir: str | Path) -> Path:
    return Path(log_dir) / HEARTBEAT_NAME


def write_heartbeat(log_dir: str | Path) -> Path:
    path = heartbeat_path(log_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{time.time():.3f}\n", encoding="utf-8")
    return path


def heartbeat_is_fresh(
    log_dir: str | Path,
    watchdog_seconds: float,
    *,
    now: float | None = None,
    multiplier: float = 2.0,
) -> bool:
    """True si el watchdog está apagado o el pulse es más reciente que multiplier * timeout."""
    if watchdog_seconds <= 0:
        return True
    path = heartbeat_path(log_dir)
    if not path.is_file():
        return False
    stamp = now if now is not None else time.time()
    age = stamp - path.stat().st_mtime
    return age < watchdog_seconds * multiplier


def check_stale(
    config: Config,
    notifier: Notifier,
    last_progress: float,
    *,
    now: float | None = None,
    uptime: float = 0.0,
    chunks: int = 0,
) -> bool:
    """Log + mail si no hubo chunk transcrito en WATCHDOG_SECONDS. True si disparó."""
    if config.watchdog_seconds <= 0:
        return False
    stamp = time.monotonic() if now is None else now
    age = stamp - last_progress
    if age < config.watchdog_seconds:
        return False

    logger.error(
        "MONITOR_STALE | no transcript for %.0fs | watchdog=%.0fs | "
        "uptime=%.0fs | chunks=%s | stream=%s",
        age,
        config.watchdog_seconds,
        uptime,
        chunks,
        config.stream_url,
    )
    subject = "MONITOR_STALE | stream monitor not transcribing"
    body = "\n".join(
        [
            "The live monitor is running but has not transcribed audio recently.",
            f"Silence: {age:.0f}s (limit {config.watchdog_seconds:.0f}s)",
            f"Uptime: {uptime:.0f}s",
            f"Chunks since start: {chunks}",
            f"Stream: {config.stream_url}",
            "",
            "The process was not killed. Check docker compose ps (unhealthy) "
            "and logs/highlights.log. The live gap is not recovered; "
            "use the UNGA pipeline for the published speech.",
        ]
    )
    notifier.notify_status(subject, body)
    return True
