from __future__ import annotations

import logging
import os
import select
import subprocess
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import IO
from urllib.parse import urlparse

from app.config import Config
from app.youtube import video_id_from_url

logger = logging.getLogger(__name__)

YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"}


class StreamError(Exception):
    """Error recuperable del stream (desconexión, ffmpeg, yt-dlp, timeout)."""


class ShutdownRequested(Exception):
    """El proceso recibió SIGINT/SIGTERM."""


class EndOfStream(Exception):
    """El archivo/VOD terminó de forma limpia."""


@dataclass
class StreamInfo:
    stream_url: str
    headers: dict[str, str]
    is_live: bool
    video_id: str | None
    origin_seconds: float
    origin_reliable: bool
    title: str


def is_youtube_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in YOUTUBE_HOSTS or host.endswith(".youtube.com")


def _live_origin_seconds(info: dict, now: float) -> tuple[float, bool]:
    start = info.get("release_timestamp") or info.get("timestamp")
    try:
        start_ts = float(start) if start is not None else None
    except (TypeError, ValueError):
        start_ts = None
    if start_ts and start_ts > 0:
        return max(0.0, now - start_ts), True
    logger.warning(
        "Live start time unknown; t= links are relative to when we connected"
    )
    return 0.0, False


def resolve_stream(config: Config, now: float | None = None) -> StreamInfo:
    """Resuelve la URL directa y el ancla de tiempo del video."""
    if not is_youtube_url(config.stream_url):
        logger.info("Using STREAM_URL directly with FFmpeg (not YouTube)")
        return StreamInfo(
            stream_url=config.stream_url,
            headers={},
            is_live=False,
            video_id=None,
            origin_seconds=config.stream_start_seconds,
            origin_reliable=True,
            title="direct",
        )

    opts: dict = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "noprogress": True,
        "retries": 3,
        "extractor_retries": 3,
        "no_warnings": False,
        "live_from_start": False,
    }
    if config.cookies_file:
        opts["cookiefile"] = config.cookies_file

    logger.info("Resolving YouTube stream with yt-dlp")
    try:
        import yt_dlp

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(config.stream_url, download=False)
    except ImportError as exc:
        raise StreamError("yt-dlp is not installed in the container") from exc
    except yt_dlp.utils.DownloadError as exc:
        raise StreamError(f"yt-dlp failed: {exc}") from exc
    except Exception as exc:
        raise StreamError(f"yt-dlp unexpected error: {exc}") from exc

    if not info:
        raise StreamError("yt-dlp did not return stream info")

    stream_url = info.get("url")
    if not stream_url:
        raise StreamError("yt-dlp did not return a playable URL")

    headers = dict(info.get("http_headers") or {})
    is_live = bool(info.get("is_live") or info.get("live_status") == "is_live")
    title = info.get("title") or "unknown"
    video_id = info.get("id") or video_id_from_url(config.stream_url)
    clock_now = time.time() if now is None else now

    if is_live:
        origin_seconds, origin_reliable = _live_origin_seconds(info, clock_now)
    else:
        origin_seconds = config.stream_start_seconds
        origin_reliable = True

    logger.info("Resolved stream | live=%s | title=%s | video_id=%s", is_live, title, video_id)
    if not is_live:
        if config.stream_start_seconds:
            logger.info(
                "VOD: seeking to %.1fs (not a live stream)",
                config.stream_start_seconds,
            )
        else:
            logger.warning(
                "URL does not look like an active live stream; audio will be read from the start"
            )
    return StreamInfo(
        stream_url=stream_url,
        headers=headers,
        is_live=is_live,
        video_id=str(video_id) if video_id else None,
        origin_seconds=origin_seconds,
        origin_reliable=origin_reliable,
        title=title,
    )


def _ffmpeg_command(
    stream_url: str,
    headers: dict[str, str],
    start_seconds: float = 0.0,
) -> list[str]:
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
    ]
    if stream_url.startswith(("http://", "https://")):
        cmd.extend(
            [
                "-reconnect",
                "1",
                "-reconnect_streamed",
                "1",
                "-reconnect_on_network_error",
                "1",
                "-rw_timeout",
                "30000000",
            ]
        )
    if start_seconds > 0:
        cmd.extend(["-ss", f"{start_seconds:.3f}"])
    if headers:
        header_blob = "".join(f"{key}: {value}\r\n" for key, value in headers.items())
        cmd.extend(["-headers", header_blob])
    cmd.extend(
        [
            "-i",
            stream_url,
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "s16le",
            "pipe:1",
        ]
    )
    return cmd


def _drain_stderr(stream: IO[bytes]) -> None:
    for raw in iter(stream.readline, b""):
        line = raw.decode("utf-8", errors="replace").strip()
        if line:
            logger.debug("ffmpeg: %s", line)


def apply_overlap(previous_tail: bytes, fresh: bytes, overlap_bytes: int) -> tuple[bytes, bytes]:
    """Arma la ventana a transcribir y el tail que se reusa en el chunk siguiente."""
    window = previous_tail + fresh
    if overlap_bytes <= 0:
        return window, b""
    if len(window) <= overlap_bytes:
        return window, window
    return window, window[-overlap_bytes:]


class AudioStream:
    def __init__(self, config: Config, stop_event: threading.Event) -> None:
        self.config = config
        self.stop_event = stop_event
        self._proc: subprocess.Popen[bytes] | None = None
        self._stderr_thread: threading.Thread | None = None
        self.video_id: str | None = None
        self.is_live = False
        self.origin_seconds = 0.0
        self.origin_reliable = True
        self.title = ""

    def __enter__(self) -> AudioStream:
        info = resolve_stream(self.config)
        self.video_id = info.video_id
        self.is_live = info.is_live
        self.origin_seconds = info.origin_seconds
        self.origin_reliable = info.origin_reliable
        self.title = info.title
        start_seconds = 0.0 if info.is_live else self.config.stream_start_seconds
        cmd = _ffmpeg_command(info.stream_url, info.headers, start_seconds=start_seconds)
        logger.info(
            "Starting FFmpeg audio capture | origin=%.1fs reliable=%s",
            self.origin_seconds,
            self.origin_reliable,
        )
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            raise StreamError("ffmpeg is not installed in the container") from exc

        if self._proc.stderr is not None:
            self._stderr_thread = threading.Thread(
                target=_drain_stderr,
                args=(self._proc.stderr,),
                name="ffmpeg-stderr",
                daemon=True,
            )
            self._stderr_thread.start()

        if self._proc.stdout is None:
            raise StreamError("ffmpeg stdout is not available")
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def close(self) -> None:
        proc = self._proc
        if proc is None:
            return
        self._proc = None
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        logger.info("FFmpeg stopped")

    def chunks(self) -> Iterator[tuple[bytes, bytes]]:
        if self._proc is None or self._proc.stdout is None:
            raise StreamError("audio stream is not open")

        overlap_bytes = self.config.overlap_bytes
        hop_bytes = self.config.hop_bytes
        timeout = self.config.read_timeout
        logger.info(
            "Reading audio in %.1fs chunks with %.1fs overlap (%s bytes hop)",
            self.config.chunk_seconds,
            self.config.chunk_overlap_seconds,
            hop_bytes,
        )

        tail = b""
        first = True
        while not self.stop_event.is_set():
            to_read = self.config.chunk_bytes if first else hop_bytes
            first = False
            fresh = self._read_exact(to_read, timeout)
            window, tail = apply_overlap(tail, fresh, overlap_bytes)
            yield window, fresh
        raise ShutdownRequested()

    def _read_exact(self, size: int, timeout: float) -> bytes:
        assert self._proc is not None and self._proc.stdout is not None
        buf = bytearray()
        deadline = time_deadline(timeout)

        while len(buf) < size:
            if self.stop_event.is_set():
                raise ShutdownRequested()

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise StreamError(
                    f"timed out after {timeout:.0f}s waiting for {size - len(buf)} audio bytes"
                )

            if self._proc.poll() is not None:
                if len(buf) >= self.config.sample_rate:
                    logger.info(
                        "FFmpeg ended; flushing partial chunk (%s bytes)",
                        len(buf),
                    )
                    return bytes(buf)
                code = self._proc.returncode
                if code in (0, None):
                    raise EndOfStream()
                raise StreamError(f"ffmpeg exited with code {code}")

            ready, _, _ = select.select([self._proc.stdout], [], [], min(1.0, remaining))
            if not ready:
                continue

            piece = os.read(self._proc.stdout.fileno(), size - len(buf))
            if not piece:
                if len(buf) >= self.config.sample_rate:
                    logger.info(
                        "FFmpeg stdout closed; flushing partial chunk (%s bytes)",
                        len(buf),
                    )
                    return bytes(buf)
                code = self._proc.poll()
                if code in (0, None):
                    raise EndOfStream()
                raise StreamError("ffmpeg stdout closed")
            buf.extend(piece)

        return bytes(buf)


def time_deadline(timeout: float) -> float:
    return time.monotonic() + timeout
