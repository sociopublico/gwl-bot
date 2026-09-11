from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None:
        return ""
    return value.strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name, str(default))
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} debe ser un entero, recibí {raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    raw = _env(name, str(default))
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} debe ser un número, recibí {raw!r}") from exc


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name, "true" if default else "false").lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} debe ser true/false, recibí {raw!r}")


def _parse_keywords(raw: str) -> tuple[str, ...]:
    keywords = tuple(part.strip() for part in raw.split(",") if part.strip())
    if not keywords:
        raise ValueError("KEYWORDS no puede estar vacío")
    return keywords


def _parse_emails(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in raw.split(",") if part.strip())


@dataclass(frozen=True)
class Config:
    stream_url: str
    keywords: tuple[str, ...]
    whisper_model: str
    whisper_device: str
    whisper_compute_type: str
    whisper_beam_size: int
    whisper_vad: bool
    language: str | None
    chunk_seconds: float
    chunk_overlap_seconds: float
    context_words: int
    reconnect_delay: float
    heartbeat_seconds: float
    cpu_threads: int
    log_level: str
    cookies_file: str | None
    audio_read_timeout: float
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_from: str
    alert_email_to: tuple[str, ...]
    smtp_starttls: bool
    smtp_ssl: bool
    alert_cooldown_seconds: float
    smtp_timeout: float
    sample_rate: int = 16000
    stream_start_seconds: float = 0.0
    exit_on_eof: bool = True
    speaker_tracking: bool = True
    speaker_aliases: str = ""
    speaker_roster: str = ""
    speaker_roster_file: str | None = None
    speaker_roster_threshold: float = 0.62
    speaker_llm_api_key: str = ""
    speaker_llm_base_url: str = "https://api.openai.com/v1"
    speaker_llm_model: str = "gpt-4o-mini"
    speaker_llm_timeout: float = 8.0

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_host and self.smtp_from and self.alert_email_to)

    @property
    def chunk_bytes(self) -> int:
        # PCM s16le mono: 2 bytes por sample.
        return int(self.chunk_seconds * self.sample_rate) * 2

    @property
    def overlap_bytes(self) -> int:
        return int(self.chunk_overlap_seconds * self.sample_rate) * 2

    @property
    def hop_bytes(self) -> int:
        return self.chunk_bytes - self.overlap_bytes

    @property
    def read_timeout(self) -> float:
        # El hop tarda ~CHUNK_SECONDS - overlap; damos margen por buffering HLS.
        return max(self.chunk_seconds - self.chunk_overlap_seconds, 1.0) + self.audio_read_timeout

    @classmethod
    def from_env(cls) -> Config:
        stream_url = _env("STREAM_URL")
        if not stream_url:
            raise ValueError("STREAM_URL es obligatorio")

        language_raw = _env("LANGUAGE", "en")
        cookies = _env("COOKIES_FILE") or None

        chunk_seconds = _env_float("CHUNK_SECONDS", 20)
        if chunk_seconds <= 0:
            raise ValueError("CHUNK_SECONDS debe ser mayor a 0")

        chunk_overlap_seconds = _env_float("CHUNK_OVERLAP_SECONDS", 4)
        if chunk_overlap_seconds < 0:
            raise ValueError("CHUNK_OVERLAP_SECONDS no puede ser negativo")
        if chunk_overlap_seconds >= chunk_seconds:
            raise ValueError("CHUNK_OVERLAP_SECONDS debe ser menor que CHUNK_SECONDS")

        context_words = _env_int("CONTEXT_WORDS", 20)
        if context_words < 0:
            raise ValueError("CONTEXT_WORDS no puede ser negativo")

        stream_start_seconds = _env_float("STREAM_START_SECONDS", 0)
        if stream_start_seconds < 0:
            raise ValueError("STREAM_START_SECONDS no puede ser negativo")

        speaker_roster_threshold = _env_float("SPEAKER_ROSTER_THRESHOLD", 0.62)
        if speaker_roster_threshold <= 0 or speaker_roster_threshold > 1:
            raise ValueError("SPEAKER_ROSTER_THRESHOLD debe estar entre 0 y 1")

        speaker_llm_timeout = _env_float("SPEAKER_LLM_TIMEOUT", 8)
        if speaker_llm_timeout < 1:
            raise ValueError("SPEAKER_LLM_TIMEOUT debe ser al menos 1 segundo")

        reconnect_delay = _env_float("RECONNECT_DELAY", 10)
        if reconnect_delay < 1:
            raise ValueError("RECONNECT_DELAY debe ser al menos 1 segundo")

        heartbeat_seconds = _env_float("HEARTBEAT_SECONDS", 60)
        if heartbeat_seconds < 1:
            raise ValueError("HEARTBEAT_SECONDS debe ser al menos 1 segundo")

        cpu_threads = _env_int("CPU_THREADS", 4)
        if cpu_threads < 1:
            raise ValueError("CPU_THREADS debe ser al menos 1")

        smtp_port = _env_int("SMTP_PORT", 587)
        if smtp_port < 1:
            raise ValueError("SMTP_PORT debe ser al menos 1")

        cooldown = _env_float("ALERT_COOLDOWN_SECONDS", 120)
        if cooldown < 0:
            raise ValueError("ALERT_COOLDOWN_SECONDS no puede ser negativo")

        smtp_timeout = _env_float("SMTP_TIMEOUT", 15)
        if smtp_timeout < 1:
            raise ValueError("SMTP_TIMEOUT debe ser al menos 1 segundo")

        smtp_ssl_raw = _env("SMTP_SSL")
        smtp_ssl = _env_bool("SMTP_SSL", smtp_port == 465) if smtp_ssl_raw else smtp_port == 465

        return cls(
            stream_url=stream_url,
            keywords=_parse_keywords(_env("KEYWORDS", "women,gender,refugees")),
            whisper_model=_env("WHISPER_MODEL", "base") or "base",
            whisper_device=_env("WHISPER_DEVICE", "cpu") or "cpu",
            whisper_compute_type=_env("WHISPER_COMPUTE_TYPE", "int8") or "int8",
            whisper_beam_size=_env_int("WHISPER_BEAM_SIZE", 1),
            whisper_vad=_env_bool("WHISPER_VAD", True),
            language=language_raw or None,
            chunk_seconds=chunk_seconds,
            chunk_overlap_seconds=chunk_overlap_seconds,
            context_words=context_words,
            reconnect_delay=reconnect_delay,
            heartbeat_seconds=heartbeat_seconds,
            cpu_threads=cpu_threads,
            log_level=_env("LOG_LEVEL", "INFO") or "INFO",
            cookies_file=cookies,
            audio_read_timeout=_env_float("AUDIO_READ_TIMEOUT", 30),
            smtp_host=_env("SMTP_HOST"),
            smtp_port=smtp_port,
            smtp_user=_env("SMTP_USER"),
            smtp_password=os.getenv("SMTP_PASSWORD", "") or "",
            smtp_from=_env("SMTP_FROM"),
            alert_email_to=_parse_emails(_env("ALERT_EMAIL_TO")),
            smtp_starttls=_env_bool("SMTP_STARTTLS", True),
            smtp_ssl=smtp_ssl,
            alert_cooldown_seconds=cooldown,
            smtp_timeout=smtp_timeout,
            stream_start_seconds=stream_start_seconds,
            exit_on_eof=_env_bool("EXIT_ON_EOF", True),
            speaker_tracking=_env_bool("SPEAKER_TRACKING", True),
            speaker_aliases=_env("SPEAKER_ALIASES"),
            speaker_roster=_env("SPEAKER_ROSTER"),
            speaker_roster_file=_env("SPEAKER_ROSTER_FILE") or None,
            speaker_roster_threshold=speaker_roster_threshold,
            speaker_llm_api_key=os.getenv("SPEAKER_LLM_API_KEY", "") or "",
            speaker_llm_base_url=_env("SPEAKER_LLM_BASE_URL", "https://api.openai.com/v1")
            or "https://api.openai.com/v1",
            speaker_llm_model=_env("SPEAKER_LLM_MODEL", "gpt-4o-mini") or "gpt-4o-mini",
            speaker_llm_timeout=speaker_llm_timeout,
        )
