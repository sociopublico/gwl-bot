from __future__ import annotations

import sys
from pathlib import Path

from pipeline.cascade import SourceUnavailable

_MODEL = None
_MODEL_KEY: tuple[str, str, str] | None = None


def _load_model(model_name: str, device: str, compute_type: str):
    global _MODEL, _MODEL_KEY
    key = (model_name, device, compute_type)
    if _MODEL is not None and _MODEL_KEY == key:
        return _MODEL
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise SourceUnavailable(
            "audio_en: falta faster-whisper. "
            "Instalalo con: pipeline/.venv/bin/pip install -r pipeline/requirements.txt"
        ) from exc
    print(
        f"whisper cargando modelo={model_name} device={device} compute={compute_type}",
        file=sys.stderr,
        flush=True,
    )
    _MODEL = WhisperModel(model_name, device=device, compute_type=compute_type)
    _MODEL_KEY = key
    return _MODEL


def _join_segments(segments) -> str:
    paragraphs: list[str] = []
    buf: list[str] = []
    prev_end = 0.0
    last_mark = 0.0
    for segment in segments:
        piece = (segment.text or "").strip()
        if not piece:
            continue
        gap = float(segment.start) - prev_end
        if buf and gap > 1.2:
            paragraphs.append(" ".join(buf))
            buf = []
        buf.append(piece)
        prev_end = float(segment.end)
        if prev_end - last_mark >= 60:
            print(f"  whisper {int(prev_end)}s", file=sys.stderr, flush=True)
            last_mark = prev_end
    if buf:
        paragraphs.append(" ".join(buf))
    return "\n\n".join(paragraphs).strip()


def transcribe_audio_file(
    path: Path,
    *,
    language: str | None = "en",
    model_name: str = "base",
    device: str = "cpu",
    compute_type: str = "int8",
    beam_size: int = 1,
    vad: bool = True,
) -> str:
    if not path.is_file() or path.stat().st_size == 0:
        raise SourceUnavailable(f"audio_en: archivo vacío ({path.name})")
    model = _load_model(model_name, device, compute_type)
    print(
        f"whisper transcribiendo {path.name} ({path.stat().st_size // 1024} KB)",
        file=sys.stderr,
        flush=True,
    )
    raw_segments, _info = model.transcribe(
        str(path),
        language=language,
        beam_size=beam_size,
        vad_filter=vad,
        condition_on_previous_text=False,
        without_timestamps=False,
    )
    from pipeline.extract_pdf import strip_assembly_protocol

    text = strip_assembly_protocol(_join_segments(raw_segments))
    if not text:
        raise SourceUnavailable(f"audio_en: Whisper no devolvió texto ({path.name})")
    return text
