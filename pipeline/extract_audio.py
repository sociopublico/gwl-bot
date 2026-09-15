from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from pipeline.cascade import SourceUnavailable

_MODEL = None
_MODEL_KEY: tuple[str, str, str] | None = None
_WORKER_ENV = "_GWL_WHISPER_WORKER"


def _cpu_threads() -> int:
    raw = os.environ.get("OMP_NUM_THREADS") or os.environ.get("CPU_THREADS") or "1"
    try:
        n = int(str(raw).strip() or "1")
    except ValueError:
        n = 1
    return max(1, n)


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
    threads = _cpu_threads()
    print(
        f"whisper cargando modelo={model_name} device={device} "
        f"compute={compute_type} threads={threads} pid={os.getpid()}",
        file=sys.stderr,
        flush=True,
    )
    _MODEL = WhisperModel(
        model_name,
        device=device,
        compute_type=compute_type,
        cpu_threads=threads,
        num_workers=1,
    )
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
        if prev_end - last_mark >= 15:
            print(f"  whisper {int(prev_end)}s", file=sys.stderr, flush=True)
            last_mark = prev_end
    if buf:
        paragraphs.append(" ".join(buf))
    return "\n\n".join(paragraphs).strip()


def _transcribe_inner(
    path: Path,
    *,
    language: str | None,
    model_name: str,
    device: str,
    compute_type: str,
    beam_size: int,
    vad: bool,
) -> str:
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


def _transcribe_via_worker(
    path: Path,
    *,
    language: str | None,
    model_name: str,
    device: str,
    compute_type: str,
    beam_size: int,
    vad: bool,
) -> str:
    """Hijo aparte: si el OOM killer manda SIGKILL (137), el extract del día sigue."""
    with tempfile.NamedTemporaryFile(
        suffix=".txt", prefix="whisper-", delete=False
    ) as handle:
        out_path = Path(handle.name)
    env = os.environ.copy()
    env[_WORKER_ENV] = "1"
    cmd = [
        sys.executable,
        "-m",
        "pipeline.extract_audio",
        "--in",
        str(path),
        "--out",
        str(out_path),
        "--language",
        language or "",
        "--model",
        model_name,
        "--device",
        device,
        "--compute-type",
        compute_type,
        "--beam-size",
        str(beam_size),
        "--vad",
        "1" if vad else "0",
    ]
    try:
        proc = subprocess.run(cmd, env=env, check=False)
    except OSError as exc:
        out_path.unlink(missing_ok=True)
        raise SourceUnavailable(f"whisper worker no arrancó: {exc}") from exc
    if proc.returncode == 137:
        out_path.unlink(missing_ok=True)
        raise SourceUnavailable(
            f"whisper SIGKILL (137) en {path.name}: probable OOM. "
            "Bajá PIPELINE_WHISPER_MODEL=tiny o dale más RAM al VPS"
        )
    if proc.returncode != 0:
        out_path.unlink(missing_ok=True)
        raise SourceUnavailable(
            f"whisper worker exit {proc.returncode} ({path.name})"
        )
    try:
        text = out_path.read_text(encoding="utf-8").strip()
    finally:
        out_path.unlink(missing_ok=True)
    if not text:
        raise SourceUnavailable(f"audio_en: Whisper no devolvió texto ({path.name})")
    return text


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
    kwargs = dict(
        language=language,
        model_name=model_name,
        device=device,
        compute_type=compute_type,
        beam_size=beam_size,
        vad=vad,
    )
    if os.environ.get(_WORKER_ENV) == "1":
        return _transcribe_inner(path, **kwargs)
    return _transcribe_via_worker(path, **kwargs)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pipeline.extract_audio")
    parser.add_argument("--in", dest="src", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--language", default="en")
    parser.add_argument("--model", default="base")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--beam-size", type=int, default=1)
    parser.add_argument("--vad", default="1")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    os.environ[_WORKER_ENV] = "1"
    try:
        text = _transcribe_inner(
            Path(args.src),
            language=args.language or None,
            model_name=args.model,
            device=args.device,
            compute_type=args.compute_type,
            beam_size=args.beam_size,
            vad=args.vad.strip().lower() not in {"0", "false", "no", "off"},
        )
    except SourceUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    Path(args.out).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
