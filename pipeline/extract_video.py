from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from pipeline.cascade import SourceUnavailable

DEFAULT_KALTURA_PARTNER = "2503451"


def kaltura_play_url(entry_id: str, partner_id: str | None = None) -> str:
    partner = (
        (partner_id or "").strip()
        or os.environ.get("KALTURA_PARTNER_ID", "").strip()
        or DEFAULT_KALTURA_PARTNER
    )
    return (
        f"https://cdnapisec.kaltura.com/p/{partner}/sp/{partner}00/"
        f"playManifest/entryId/{entry_id}/format/url/protocol/https"
    )


def _ffmpeg_to_wav(source: str, dest: Path, *, user_agent: str, timeout: float) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SourceUnavailable("video: no está ffmpeg en PATH")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp.wav")
    cmd = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-user_agent",
        user_agent,
        "-i",
        source,
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        str(tmp),
    ]
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        tmp.unlink(missing_ok=True)
        raise SourceUnavailable(f"video: ffmpeg timeout ({int(timeout)}s)") from exc
    if proc.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        hint = err[-1] if err else f"exit {proc.returncode}"
        raise SourceUnavailable(f"video: ffmpeg no pudo extraer audio ({hint})")
    tmp.replace(dest)


def transcribe_kaltura(
    entry_id: str,
    dest_wav: Path,
    *,
    partner_id: str | None = None,
    user_agent: str,
    language: str | None = "en",
    model_name: str = "base",
    device: str = "cpu",
    compute_type: str = "int8",
    beam_size: int = 1,
    vad: bool = True,
    timeout: float = 900.0,
) -> str:
    from pipeline.extract_audio import transcribe_audio_file

    url = kaltura_play_url(entry_id, partner_id)
    if not dest_wav.is_file() or dest_wav.stat().st_size == 0:
        _ffmpeg_to_wav(url, dest_wav, user_agent=user_agent, timeout=timeout)
    return transcribe_audio_file(
        dest_wav,
        language=language,
        model_name=model_name,
        device=device,
        compute_type=compute_type,
        beam_size=beam_size,
        vad=vad,
    )
