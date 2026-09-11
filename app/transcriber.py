from __future__ import annotations

import logging
import time

import numpy as np
from faster_whisper import WhisperModel

from app.config import Config
from app.transcript import TranscriptSegment

logger = logging.getLogger(__name__)


class Transcriber:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._model: WhisperModel | None = None

    def load(self) -> None:
        logger.info(
            "Loading Whisper model=%s device=%s compute_type=%s",
            self.config.whisper_model,
            self.config.whisper_device,
            self.config.whisper_compute_type,
        )
        self._model = WhisperModel(
            self.config.whisper_model,
            device=self.config.whisper_device,
            compute_type=self.config.whisper_compute_type,
            cpu_threads=self.config.cpu_threads,
        )
        logger.info("Whisper model ready")

    def transcribe(self, pcm: bytes) -> tuple[str, list[TranscriptSegment], float]:
        if self._model is None:
            raise RuntimeError("Whisper model no está cargado")

        if len(pcm) < self.config.sample_rate:
            logger.warning("Skipping invalid audio chunk (%s bytes)", len(pcm))
            return "", [], 0.0

        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        if not np.any(audio):
            logger.debug("Skipping silent chunk")
            return "", [], 0.0

        started = time.monotonic()
        language = self.config.language
        raw_segments, _info = self._model.transcribe(
            audio,
            language=language,
            beam_size=self.config.whisper_beam_size,
            vad_filter=self.config.whisper_vad,
            condition_on_previous_text=False,
            without_timestamps=False,
        )
        segments: list[TranscriptSegment] = []
        texts: list[str] = []
        for segment in raw_segments:
            piece = segment.text.strip()
            if not piece:
                continue
            segments.append(
                TranscriptSegment(
                    start=float(segment.start),
                    end=float(segment.end),
                    text=piece,
                )
            )
            texts.append(piece)
        elapsed = time.monotonic() - started
        return " ".join(texts).strip(), segments, elapsed
