"""Paso 4: Claude API → filas en la pestaña Analysis.

El prompt vive en pipeline/data/analyze-prompt.md (lo pegan cuando esté).
Las columnas se leen del encabezado de la pestaña; si la hoja está vacía se
usan ANALYSIS_COLUMNS (identidad + summary/notes de placeholder).

Optimización de costo:
- Prefijo estable (prompt + columnas) en `system` con prompt caching explícito.
- Discurso + identidad en `user` (no se cachea).
- Skip de slug|date|id_speech ya presentes en Sheets antes de llamar a la API.
- TTL configurable (default 1h) y reporte de usage/costo estimado.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pipeline.config import PIPELINE_ROOT, SessionConfig
from pipeline.env import load_dotenv
from pipeline.models import ExtractedSpeech
from pipeline.sheets import (
    ANALYSIS_COLUMNS,
    ANALYSIS_IDENTITY,
    analysis_identity_key,
    analysis_key_aliases,
    append_analysis_rows,
    can_write_sheets,
    existing_analysis_keys,
    sheets_settings,
    sheets_unavailable_reason,
)
from pipeline.store import list_speech_txts, parse_speech_txt
from pipeline.progress import write_analysis_snapshot

DEFAULT_PROMPT_PATH = PIPELINE_ROOT / "data" / "analyze-prompt.md"
DEFAULT_MODEL = "claude-haiku-4-5"
DEFAULT_CACHE_TTL = "1h"
STUB_MARKERS = ("TODO: pegar el prompt", "PEGAR_PROMPT")
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.I)

SYSTEM_ROLE = (
    "Sos un analista de discursos de la Asamblea General. "
    "Respondé solo un objeto JSON válido, sin texto alrededor."
)

# USD por millón de tokens (Claude Haiku 4.5; otros modelos usan estas tasas
# solo como aproximación en el resumen).
_RATE_INPUT = 1.0
_RATE_OUTPUT = 5.0
_RATE_CACHE_READ = 0.10
_RATE_CACHE_WRITE_5M = 1.25
_RATE_CACHE_WRITE_1H = 2.0


class AnalyzeError(RuntimeError):
    pass


@dataclass(frozen=True)
class ClaudeCallResult:
    data: dict
    usage: dict[str, Any] = field(default_factory=dict)


@dataclass
class UsageTotals:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_5m: int = 0
    cache_creation_1h: int = 0
    calls: int = 0
    cache_hits: int = 0
    cache_writes: int = 0

    def add(self, usage: dict[str, Any]) -> None:
        self.calls += 1
        self.input_tokens += int(usage.get("input_tokens") or 0)
        self.output_tokens += int(usage.get("output_tokens") or 0)
        created = int(usage.get("cache_creation_input_tokens") or 0)
        read = int(usage.get("cache_read_input_tokens") or 0)
        self.cache_creation_input_tokens += created
        self.cache_read_input_tokens += read
        if read > 0:
            self.cache_hits += 1
        if created > 0:
            self.cache_writes += 1
        creation = usage.get("cache_creation")
        if isinstance(creation, dict):
            self.cache_creation_5m += int(creation.get("ephemeral_5m_input_tokens") or 0)
            self.cache_creation_1h += int(creation.get("ephemeral_1h_input_tokens") or 0)
        elif created > 0:
            # Respuestas viejas sin desglose: asumir el TTL pedido no está
            # disponible acá; contamos como 5m (más barato) solo si no hay 1h.
            self.cache_creation_5m += created

    def estimated_usd(self) -> float:
        write_5m = self.cache_creation_5m
        write_1h = self.cache_creation_1h
        # Si el desglose no suma el total de creation, el resto se cobra como 5m.
        accounted = write_5m + write_1h
        if self.cache_creation_input_tokens > accounted:
            write_5m += self.cache_creation_input_tokens - accounted
        return (
            self.input_tokens * _RATE_INPUT
            + self.output_tokens * _RATE_OUTPUT
            + self.cache_read_input_tokens * _RATE_CACHE_READ
            + write_5m * _RATE_CACHE_WRITE_5M
            + write_1h * _RATE_CACHE_WRITE_1H
        ) / 1_000_000

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "cache_hits": self.cache_hits,
            "cache_writes": self.cache_writes,
            "estimated_usd": round(self.estimated_usd(), 6),
        }


def prompt_is_stub(text: str) -> bool:
    lower = text.lower()
    return any(marker.lower() in lower for marker in STUB_MARKERS)


def load_prompt(path: Path | None = None) -> str:
    prompt_path = path or DEFAULT_PROMPT_PATH
    if not prompt_path.is_file():
        raise AnalyzeError(f"no encuentro el prompt en {prompt_path}")
    text = prompt_path.read_text(encoding="utf-8").strip()
    if not text:
        raise AnalyzeError(f"prompt vacío: {prompt_path}")
    return text


def parse_json_object(raw: str) -> dict:
    text = (raw or "").strip()
    if not text:
        raise AnalyzeError("Claude devolvió texto vacío")
    fenced = _JSON_FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise AnalyzeError(f"no hay JSON en la respuesta: {text[:200]!r}")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise AnalyzeError(f"JSON inválido: {exc}") from exc
    if not isinstance(data, dict):
        raise AnalyzeError("Claude no devolvió un objeto JSON")
    return data


def _ficha_url(speech: ExtractedSpeech) -> str:
    if speech.session_id and speech.slug:
        return f"https://gadebate.un.org/en/{speech.session_id}/{speech.slug}"
    return ""


def identity_fields(speech: ExtractedSpeech) -> dict[str, str]:
    return {
        "slug": speech.slug,
        "date_time": speech.speech_date,
        "id_speech": speech.id_speech or "",
        "country": speech.country,
        "speaker_name": speech.name,
        "ficha_url": _ficha_url(speech),
    }


def analysis_key(speech: ExtractedSpeech) -> str:
    return analysis_identity_key(
        slug=speech.slug,
        date_time=speech.speech_date,
        id_speech=speech.id_speech,
    )


def _speech_aliases(speech: ExtractedSpeech) -> set[str]:
    return analysis_key_aliases(
        slug=speech.slug,
        date_time=speech.speech_date,
        id_speech=speech.id_speech,
        ficha_url=_ficha_url(speech),
    )


def estimate_tokens(text: str) -> int:
    """Estimación burda (~4 chars/token). Solo para warnings, no billing."""
    return max(0, (len(text) + 3) // 4)


def cache_min_tokens(model: str) -> int:
    """Mínimo de tokens del prefijo cacheable según docs de Anthropic."""
    m = (model or "").lower()
    if "haiku-4-5" in m or "haiku-4.5" in m:
        return 4096
    if "haiku" in m:
        return 2048
    if "opus-4-6" in m or "opus-4.6" in m or "opus-4-5" in m or "opus-4.5" in m:
        return 4096
    if "opus-4-7" in m or "opus-4.7" in m:
        return 2048
    # Sonnet / Opus recientes / default
    return 1024


def build_system_prompt(prompt: str, *, columns: list[str]) -> str:
    """Prefijo estable: role + prompt de coding + esquema de columnas."""
    return (
        f"{SYSTEM_ROLE}\n\n"
        f"{prompt.strip()}\n\n"
        "Respondé únicamente un objeto JSON (sin markdown) con exactamente "
        f"estas claves: {json.dumps(columns, ensure_ascii=False)}.\n"
        "Los campos de identidad ya vienen en el mensaje del usuario; "
        "copialos tal cual en el JSON de salida."
    )


def build_user_message(speech: ExtractedSpeech) -> str:
    """Mensaje por discurso (variable: identidad + texto)."""
    identity = identity_fields(speech)
    return (
        "Identidad (copiá estos campos tal cual en el JSON):\n"
        f"{json.dumps(identity, ensure_ascii=False)}\n\n"
        f"DISCURSO ({speech.slug} {speech.speech_date}):\n"
        f"{speech.text.strip()}\n"
    )


def _anthropic_settings() -> tuple[str, str]:
    load_dotenv()
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    model = os.environ.get("ANTHROPIC_MODEL", "").strip() or DEFAULT_MODEL
    if not api_key:
        raise AnalyzeError("falta ANTHROPIC_API_KEY en .env")
    return api_key, model


def cache_control_from_env() -> dict[str, str] | None:
    """ANTHROPIC_CACHE_TTL: 1h (default) | 5m | off."""
    load_dotenv()
    raw = (
        os.environ.get("ANTHROPIC_CACHE_TTL", DEFAULT_CACHE_TTL) or DEFAULT_CACHE_TTL
    ).strip().lower()
    if raw in {"off", "0", "false", "no", "none", "disabled"}:
        return None
    if raw in {"5m", "5min", "5", "300", "ephemeral"}:
        return {"type": "ephemeral"}
    if raw in {"1h", "1hr", "60m", "3600"}:
        return {"type": "ephemeral", "ttl": "1h"}
    print(
        f"warn: ANTHROPIC_CACHE_TTL={raw!r} inválido; uso {DEFAULT_CACHE_TTL}",
        file=sys.stderr,
    )
    return {"type": "ephemeral", "ttl": "1h"}


def allow_stub() -> bool:
    load_dotenv()
    return os.environ.get("ANALYZE_ALLOW_STUB", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _post_messages(payload: dict, api_key: str, timeout: float = 120) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        ANTHROPIC_URL,
        data=body,
        method="POST",
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise AnalyzeError(f"Anthropic HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise AnalyzeError(f"Anthropic red: {exc}") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise AnalyzeError("Anthropic no devolvió JSON") from exc
    if not isinstance(data, dict):
        raise AnalyzeError("Anthropic: cuerpo inesperado")
    return data


def _format_usage_line(usage: dict[str, Any]) -> str:
    inp = int(usage.get("input_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)
    created = int(usage.get("cache_creation_input_tokens") or 0)
    read = int(usage.get("cache_read_input_tokens") or 0)
    return f"in={inp} out={out} cache_write={created} cache_read={read}"


def call_claude(
    user_message: str,
    *,
    api_key: str,
    model: str,
    system_prompt: str | None = None,
    cache_control: dict[str, str] | None = None,
) -> ClaudeCallResult:
    """Llama a Messages API. Prefijo cacheable va en system_prompt."""
    if system_prompt is None:
        system_prompt = SYSTEM_ROLE

    if cache_control:
        system: Any = [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": cache_control,
            }
        ]
    else:
        system = system_prompt

    payload = {
        "model": model,
        "max_tokens": 2048,
        "system": system,
        "messages": [{"role": "user", "content": user_message}],
    }
    data = _post_messages(payload, api_key)
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return ClaudeCallResult(
        data=parse_json_object(_content_text(data)),
        usage=dict(usage),
    )


def _content_text(data: dict) -> str:
    chunks = data.get("content") or []
    texts: list[str] = []
    for chunk in chunks:
        if isinstance(chunk, dict) and chunk.get("type") == "text":
            texts.append(str(chunk.get("text") or ""))
    return "\n".join(texts)


def merge_analysis_row(
    speech: ExtractedSpeech,
    model_row: dict,
    *,
    columns: list[str],
) -> dict[str, str]:
    identity = identity_fields(speech)
    row: dict[str, str] = {}
    for col in columns:
        if col in ANALYSIS_IDENTITY:
            row[col] = identity.get(col, "")
            continue
        value = model_row.get(col, "")
        if value is None:
            row[col] = ""
        elif isinstance(value, (dict, list)):
            row[col] = json.dumps(value, ensure_ascii=False)
        else:
            row[col] = str(value).strip()
    return row


def _speeches_for_day(
    config: SessionConfig,
    *,
    day: str,
    slug: str | None,
    dest: Path | None,
) -> list[ExtractedSpeech]:
    paths = list_speech_txts(config, speech_date=day, dest=dest)
    speeches: list[ExtractedSpeech] = []
    for path in paths:
        if path.name == "manifest.json":
            continue
        try:
            speech = parse_speech_txt(path)
        except ValueError as exc:
            print(f"SKIP {path.name}: {exc}", file=sys.stderr)
            continue
        if slug and speech.slug != slug:
            continue
        speeches.append(speech)
    return speeches


def _warn_cache_threshold(system_prompt: str, model: str, cache_control: dict | None) -> None:
    if not cache_control:
        print("cache=off (ANTHROPIC_CACHE_TTL=off)", file=sys.stderr)
        return
    est = estimate_tokens(system_prompt)
    minimum = cache_min_tokens(model)
    ttl = cache_control.get("ttl", "5m")
    if est < minimum:
        print(
            f"warn: system≈{est} tokens (<{minimum} mín. para cache en {model}); "
            f"Anthropic puede ignorar cache_control hasta que el prompt crezca. "
            f"ttl={ttl}",
            file=sys.stderr,
        )
    else:
        print(
            f"cache=on ttl={ttl} system≈{est} tokens (mín={minimum})",
            file=sys.stderr,
        )


def analyze_day(
    config: SessionConfig,
    *,
    day: str,
    slug: str | None = None,
    dest: Path | None = None,
    prompt_path: Path | None = None,
    dry_run: bool = False,
) -> int:
    try:
        prompt = load_prompt(prompt_path)
    except AnalyzeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    stub = prompt_is_stub(prompt)
    if stub and not dry_run and not allow_stub():
        print(
            "error: pipeline/data/analyze-prompt.md sigue siendo un stub. "
            "Pegá el prompt de coding (y columnas de ejemplo en la pestaña "
            "Analysis) antes de correr analyze. "
            "Para una prueba: ANALYZE_ALLOW_STUB=1 o --dry-run.",
            file=sys.stderr,
        )
        return 1

    speeches = _speeches_for_day(config, day=day, slug=slug, dest=dest)
    if not speeches:
        print(
            f"error: no hay .txt en pipeline/out/{config.id}/{day}",
            file=sys.stderr,
        )
        return 1

    settings = sheets_settings()
    columns = list(ANALYSIS_COLUMNS)
    existing_records: list[dict] = []
    if not dry_run:
        if not can_write_sheets(settings):
            print(f"error: {sheets_unavailable_reason(settings)}", file=sys.stderr)
            return 1
        from pipeline.sheets import read_analysis_records

        columns, existing_records = read_analysis_records(settings)
        if not columns:
            columns = list(ANALYSIS_COLUMNS)

    already = existing_analysis_keys(existing_records)
    pending: list[ExtractedSpeech] = []
    for speech in speeches:
        if _speech_aliases(speech) & already:
            continue
        if not (speech.id_speech or "").strip():
            print(
                f"warn: {analysis_key(speech)} sin id_speech; "
                "la clave puede colisionar si hay varios sin slug",
                file=sys.stderr,
            )
        pending.append(speech)
    skipped_existing = len(speeches) - len(pending)

    print(
        f"analyze session={config.id} day={day} speeches={len(speeches)} "
        f"pending={len(pending)} already={skipped_existing} "
        f"columns={len(columns)} stub={int(stub)}",
        file=sys.stderr,
    )
    if dry_run:
        system_prompt = build_system_prompt(prompt, columns=columns)
        cache_control = cache_control_from_env()
        _warn_cache_threshold(system_prompt, DEFAULT_MODEL, cache_control)
        for speech in pending:
            print(f"DRY {analysis_key(speech)} chars={len(speech.text)}", file=sys.stderr)
        for speech in speeches:
            if _speech_aliases(speech) & already:
                print(f"DRY skip existing {analysis_key(speech)}", file=sys.stderr)
        print(
            json.dumps(
                {
                    "day": day,
                    "speeches": len(speeches),
                    "pending": len(pending),
                    "already": skipped_existing,
                    "columns": columns,
                    "stub": stub,
                    "cache_ttl": None
                    if not cache_control
                    else cache_control.get("ttl", "5m"),
                    "system_tokens_est": estimate_tokens(system_prompt),
                }
            ),
            file=sys.stderr,
        )
        return 0

    if not pending:
        print(
            json.dumps(
                {
                    "analyzed": 0,
                    "written": 0,
                    "skipped": skipped_existing,
                    "errors": 0,
                    "note": "nada pendiente (ya en Sheets)",
                }
            ),
            file=sys.stderr,
        )
        return 0

    try:
        api_key, model = _anthropic_settings()
    except AnalyzeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    system_prompt = build_system_prompt(prompt, columns=columns)
    cache_control = cache_control_from_env()
    _warn_cache_threshold(system_prompt, model, cache_control)

    rows: list[dict[str, str]] = []
    errors = 0
    totals = UsageTotals()
    for speech in pending:
        user_message = build_user_message(speech)
        try:
            result = call_claude(
                user_message,
                api_key=api_key,
                model=model,
                system_prompt=system_prompt,
                cache_control=cache_control,
            )
            row = merge_analysis_row(speech, result.data, columns=columns)
            rows.append(row)
            totals.add(result.usage)
            write_analysis_snapshot(
                config,
                day=day,
                slug=speech.slug,
                row=row,
                model=model,
            )
            print(
                f"OK {analysis_key(speech)} model={model} {_format_usage_line(result.usage)}",
                file=sys.stderr,
            )
        except AnalyzeError as exc:
            errors += 1
            print(f"FAIL {analysis_key(speech)} {exc}", file=sys.stderr)

    written = append_analysis_rows(
        rows,
        headers=columns,
        settings=settings,
        existing=existing_records,
    )
    print(
        json.dumps(
            {
                "analyzed": len(rows),
                "written": len(written),
                "skipped": skipped_existing + (len(rows) - len(written)),
                "errors": errors,
                "model": model,
                "usage": totals.as_dict(),
            }
        ),
        file=sys.stderr,
    )
    return 0 if errors == 0 else 2
