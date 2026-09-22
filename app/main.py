from __future__ import annotations

import logging
import signal
import sys
import threading
import time
from pathlib import Path

from app.audio import AudioStream, EndOfStream, ShutdownRequested, StreamError
from app.clock import StreamClock
from app.config import Config
from app.detector import detect_keywords
from app.events import DetectionDeduper, emit_detections
from app.keyword_journal import KeywordJournal
from app.logger import setup_logging
from app.notifier import build_notifier
from app.speaker import Speaker, SpeakerTracker
from app.transcriber import Transcriber
from app.watchdog import check_stale, write_heartbeat

logger = logging.getLogger(__name__)


def _install_signal_handlers(stop_event: threading.Event) -> None:
    def _handle(signum, _frame) -> None:
        name = signal.Signals(signum).name
        logger.info("Received %s, shutting down", name)
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)


def _clip(text: str, limit: int = 120) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def run(config: Config) -> None:
    stop_event = threading.Event()
    _install_signal_handlers(stop_event)

    logger.info("SESSION_START | stream monitor | log_dir=%s", config.log_dir)
    logger.info("Starting stream monitor")
    logger.info("Stream: %s", config.stream_url)
    logger.info(
        "Start offset=%.1fs | exit_on_eof=%s | speaker_tracking=%s",
        config.stream_start_seconds,
        config.exit_on_eof,
        config.speaker_tracking,
    )
    logger.info("Keywords: %s", ", ".join(config.keywords))
    logger.info(
        "Chunk=%.1fs | overlap=%.1fs | model=%s | language=%s | vad=%s",
        config.chunk_seconds,
        config.chunk_overlap_seconds,
        config.whisper_model,
        config.language or "auto",
        config.whisper_vad,
    )

    transcriber = Transcriber(config)
    transcriber.load()
    notifier = build_notifier(config)
    tracker = SpeakerTracker(config) if config.speaker_tracking else None
    deduper = DetectionDeduper(ttl_seconds=max(config.chunk_overlap_seconds * 2, 8.0))
    journal = (
        KeywordJournal(Path(config.log_dir) / "keywords.jsonl")
        if config.log_dir
        else None
    )

    started = time.monotonic()
    last_heartbeat = started
    last_progress = started
    write_heartbeat(config.log_dir)
    chunks = 0
    detections = 0

    def _maybe_stale() -> None:
        check_stale(
            config,
            notifier,
            last_progress,
            now=time.monotonic(),
            uptime=time.monotonic() - started,
            chunks=chunks,
        )

    while not stop_event.is_set():
        try:
            with AudioStream(config, stop_event) as stream:
                logger.info("Stream connected")
                if tracker is not None and not stream.is_live:
                    tracker.reset()
                clock = StreamClock(stream.origin_seconds, config.sample_rate)
                for window, fresh in stream.chunks():
                    clock.add_fresh(len(fresh))
                    text, segments, inference_s = transcriber.transcribe(window)
                    last_progress = time.monotonic()
                    write_heartbeat(config.log_dir)
                    chunks += 1
                    audio_s = len(window) / (config.sample_rate * 2)
                    logger.info(
                        "Transcribed chunk | audio=%.1fs | inference=%.1fs | text=%s",
                        audio_s,
                        inference_s,
                        _clip(text) if text else "(empty)",
                    )
                    window_start = clock.window_start(len(window))
                    speaker = (
                        tracker.observe(text, video_seconds=window_start)
                        if tracker is not None
                        else Speaker()
                    )
                    events = detect_keywords(
                        text,
                        config.keywords,
                        config.context_words,
                        segments=segments,
                        window_start=window_start,
                        video_id=stream.video_id,
                        webtv_asset_url=config.webtv_url or None,
                        speaker=speaker.name,
                        speaker_title=speaker.display_title,
                        timestamp_reliable=stream.origin_reliable,
                    )
                    unique = emit_detections(
                        events, notifier, deduper=deduper, journal=journal
                    )
                    detections += len(unique)

                    now = time.monotonic()
                    if now - last_heartbeat >= config.heartbeat_seconds:
                        logger.info(
                            "Still listening | uptime=%.0fs | chunks=%s | detections=%s | "
                            "emails_sent=%s | speaker=%s",
                            now - started,
                            chunks,
                            detections,
                            notifier.emails_sent,
                            speaker.name,
                        )
                        last_heartbeat = now
        except ShutdownRequested:
            break
        except EndOfStream:
            logger.info("Stream ended")
            if config.exit_on_eof:
                break
        except StreamError as exc:
            logger.error("Stream error: %s", exc)
        except Exception as exc:
            logger.exception("Unexpected error: %s", exc)

        if stop_event.is_set():
            break

        _maybe_stale()
        logger.warning("Reconnecting in %.0fs", config.reconnect_delay)
        if stop_event.wait(config.reconnect_delay):
            break
        _maybe_stale()

    logger.info(
        "Shutting down | uptime=%.0fs | chunks=%s | detections=%s | emails_sent=%s",
        time.monotonic() - started,
        chunks,
        detections,
        notifier.emails_sent,
    )
    logger.info("SESSION_END | stream monitor")


def main() -> None:
    try:
        config = Config.from_env()
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)

    setup_logging(config.log_level, config.log_dir)
    run(config)
