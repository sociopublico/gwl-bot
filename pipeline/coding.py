"""Paso 5: codebook UNGA → Indicators.csv + Emerging_Priorities.csv.

Lee los .txt del día, llama a Claude en tandas de 4–6 y escribe los dos CSV
junto a metadata.csv. El methodology prompt default es claude-prompt.md.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from pipeline.claude import ClaudeError, anthropic_settings, call_claude
from pipeline.config import PIPELINE_ROOT, SessionConfig
from pipeline.metadata import normalize_speech_id, parse_speech_id
from pipeline.models import ExtractedSpeech
from pipeline.store import list_speech_txts, out_dir, parse_speech_txt

DEFAULT_PROMPT_PATH = PIPELINE_ROOT.parent / "claude-prompt.md"
CHUNK_SIZE = 3
CODING_MAX_TOKENS = 32768
CODING_TIMEOUT = 300.0
CODING_SOURCE = "claude"

INDICATORS = (
    "gender_equality_position",
    "women_leadership_position",
    "women_multilateral_leadership_position",
    "sg_selection_position",
    "sg_selection_position_gender",
    "un_reform_position",
    "current_multilateralism_position",
    "future_multilateralism_position",
)

CLUSTERS = {
    "gender_equality_position": "Gender and women's leadership",
    "women_leadership_position": "Gender and women's leadership",
    "women_multilateral_leadership_position": "Gender and women's leadership",
    "sg_selection_position": "SG election",
    "sg_selection_position_gender": "SG election",
    "un_reform_position": "UN reform",
    "current_multilateralism_position": "Multilateralism",
    "future_multilateralism_position": "Multilateralism",
}

STANDARD_OPTIONS = {
    0: "No Mention",
    1: "Positive / Supportive",
    2: "Mixed / Ambiguous",
    3: "Negative / Pushback",
}

FUTURE_OPTIONS = {
    0: "No Mention",
    5: "Preservation / Strengthening",
    2: "Mixed / Ambiguous",
    6: "Transformation",
}

EMERGING_TOPICS = (
    "Climate change",
    "AI / digital transformation",
    "Peace and security",
    "Development / SDGs",
    "Inequality",
    "Financing / debt",
    "Human rights",
    "Migration",
    "Global health",
    "Food security",
    "Technology / digital divide",
    "Other",
)

NESTED_CHECKS = (
    ("sg_selection_position_gender", "sg_selection_position"),
    ("sg_selection_position_gender", "gender_equality_position"),
    ("women_multilateral_leadership_position", "women_leadership_position"),
)

INDICATOR_COLUMNS = [
    "id_speech",
    "extract_id",
    "cluster",
    "indicator_name",
    "code",
    "option",
    "textual_extract",
    "coder_notes",
    "coding_source",
    "model",
    "date_coded",
]

EMERGING_COLUMNS = [
    "id_speech",
    "id_extract",
    "emerging_topic",
    "textual_extract",
]

API_INSTRUCTIONS = """You are coding UNGA speeches against the methodology in the system prompt.

Respond with a single JSON object (no markdown) of the form:
{"indicators": [...], "emerging_priorities": [...]}

indicators: exactly 8 objects per speech, one per indicator_name in this order:
gender_equality_position, women_leadership_position, women_multilateral_leadership_position,
sg_selection_position, sg_selection_position_gender, un_reform_position,
current_multilateralism_position, future_multilateralism_position.

Each indicator object:
{"id_speech": "M_N", "indicator_name": "...", "code": <int>, "textual_extract": "...", "coder_notes": ""}

Use the codebook codes. For future_multilateralism_position the codes are 0, 5, 2, or 6.
textual_extract: exhaustive unabridged quotes, numbered (1) (2) (3), each a contiguous verbatim span for that indicator. Do not bridge non-adjacent sentences with "...". Omit paraphrases and passages about another indicator. If code is 0, omit textual_extract or send "". Do not write "No mention of …".
coder_notes: only for borderline/inferential codes or source-file data issues; else "".

emerging_priorities: zero or more objects per speech. emerging_topic MUST be one of:
Climate change, AI / digital transformation, Peace and security, Development / SDGs, Inequality, Financing / debt, Human rights, Migration, Global health, Food security, Technology / digital divide, Other.
Each: {"id_speech": "M_N", "emerging_topic": "...", "textual_extract": "<one short line>"}

Process every speech below with a full close read (not keyword scanning).

Hard rules (also in the system methodology):
- sg_selection_position: ONLY the appointment, the process, a candidacy, a nomination, an explicit next or new Secretary-General, or the fact that no woman has held the office. Congratulating the PGA (e.g. Annalena Baerbock) or any other UN election is code 0. Praise or "building on the achievements" of the sitting Secretary-General is code 0; do not quote it.
- sg_selection_position_gender: code 1 for a woman SG read in context: "a woman", "she", a named woman candidate; women's representation followed by "the next Secretary-General" or "the highest level"; or lamenting that no woman has ever led the UN as SG (then sg_selection_position cannot be 0). A PGA mention in the same passage does not cancel the SG sentence. "Her or him" or gender balance as one criterion, with no push toward a woman, is code 2. Code 1 here means gender_equality_position cannot be 0; code 2 does not.
- If women_multilateral_leadership_position is non-zero, women_leadership_position cannot be 0; if that passage is the only evidence, use the same code.
- Praise of only the speaker's own wife or first lady, with no claim about women in general, is code 2 on women_leadership_position and women_multilateral_leadership_position.
- gender_equality_position: code 1 for gender imbalance, gender inclusion as a rights agenda, WPS, a woman SG, stated opportunities or benefits for women and girls, or women and girls targeted by hate, discrimination, or misogyny the speaker opposes. Code 0 when women and children are listed only as victims of war, drugs, famine, or environmental harm. Opposition to transgender people or gender identity is code 3, never 1; together with praise of only the speaker's own wife or first lady, code 2. The code must match coder_notes.
- current_multilateralism_position: if the past was better / progress is under threat, the present has lost credibility, or the future will "restore" the UN, code 3 (Negative), not Mixed. A leftover "the UN remains a pillar" does not turn that into Mixed.
- un_reform_position: a call to renew, change, transform, or reorient the UN or this Organization is code 1 even with no named mechanism. Crisis + the UN must change/transform/reform, UN80, or SC without veto is also code 1, not 3. Critique of inaction on a crisis, or reform only of the financial architecture, IMF, or World Bank, is code 0, not Mixed.
- If un_reform_position is 1, future_multilateralism_position MUST be 6 (Transformation), not 5 (Preservation), including when the speaker supports UN80.
"""

_TOPIC_SLUG = re.compile(r"[^a-z0-9]+")


class CodingError(RuntimeError):
    pass


def flatten_newlines(value: str) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    return text.replace("\n", " | ")


def option_for(indicator: str, code: int) -> str:
    mapping = (
        FUTURE_OPTIONS
        if indicator == "future_multilateralism_position"
        else STANDARD_OPTIONS
    )
    return mapping.get(code, "")


def extract_id_for(speech_id: str, indicator: str) -> str:
    return f"{speech_id}_{indicator}"


def topic_slug(topic: str) -> str:
    slug = _TOPIC_SLUG.sub("_", topic.strip().lower()).strip("_")
    return slug or "other"


def emerging_extract_id(speech_id: str, topic: str) -> str:
    return f"{speech_id}_{topic_slug(topic)}"


def canonical_topic(raw: str) -> str | None:
    key = (raw or "").strip().lower()
    if not key:
        return None
    for topic in EMERGING_TOPICS:
        if topic.lower() == key:
            return topic
    return None


def speech_id_of(speech: ExtractedSpeech, path: Path | None = None) -> str:
    label = normalize_speech_id(speech.id_speech)
    if label:
        return label
    if path is not None:
        return normalize_speech_id(path.stem)
    return ""


def chunk_speeches(
    items: list[tuple[ExtractedSpeech, Path, str]],
    *,
    size: int = CHUNK_SIZE,
) -> list[list[tuple[ExtractedSpeech, Path, str]]]:
    if size < 1:
        size = CHUNK_SIZE
    return [items[i : i + size] for i in range(0, len(items), size)]


def load_methodology(path: Path | None = None) -> str:
    prompt_path = path or DEFAULT_PROMPT_PATH
    if not prompt_path.is_file():
        raise CodingError(f"no encuentro el prompt en {prompt_path}")
    text = prompt_path.read_text(encoding="utf-8").strip()
    if not text:
        raise CodingError(f"prompt vacío: {prompt_path}")
    return text


def indicators_csv_path(directory: Path) -> Path:
    return directory / "Indicators.csv"


def emerging_csv_path(directory: Path) -> Path:
    return directory / "Emerging_Priorities.csv"


def coding_status_path(
    config: SessionConfig,
    day: str,
    *,
    dest: Path | None = None,
) -> Path:
    if dest is not None:
        return dest / "_coding_status" / str(config.id) / f"{day}.json"
    return config.root / "data" / "coding" / str(config.id) / f"{day}.json"


def write_coding_status(
    config: SessionConfig,
    day: str,
    *,
    dest: Path | None = None,
    model: str = "",
) -> Path:
    """Snapshot versionable de qué id_speech ya están coded (para GitHub Pages)."""
    directory = out_dir(config, day, dest=dest)
    ind_path = indicators_csv_path(directory)
    em_path = emerging_csv_path(directory)
    by_speech: dict[str, dict[str, int | str]] = {}
    if ind_path.is_file():
        with ind_path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                sid = normalize_speech_id(str(row.get("id_speech") or ""))
                if not sid:
                    continue
                entry = by_speech.setdefault(
                    sid, {"indicators": 0, "emerging": 0, "model": "", "date_coded": ""}
                )
                entry["indicators"] = int(entry["indicators"]) + 1
                if not entry["model"]:
                    entry["model"] = str(row.get("model") or model or "")
                if not entry["date_coded"]:
                    entry["date_coded"] = str(row.get("date_coded") or "")
    if em_path.is_file():
        with em_path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                sid = normalize_speech_id(str(row.get("id_speech") or ""))
                if not sid:
                    continue
                entry = by_speech.setdefault(
                    sid, {"indicators": 0, "emerging": 0, "model": "", "date_coded": ""}
                )
                entry["emerging"] = int(entry["emerging"]) + 1
    path = coding_status_path(config, day, dest=dest)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "session": config.id,
        "day": day,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model": model,
        "speeches": by_speech,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"coding status → {path} ({len(by_speech)} speeches)", file=sys.stderr)
    return path


def load_coding_status(
    config: SessionConfig,
    day: str,
    *,
    dest: Path | None = None,
) -> dict | None:
    path = coding_status_path(config, day, dest=dest)
    if not path.is_file() and dest is not None:
        # Fallback al snapshot versionado del repo
        path = coding_status_path(config, day, dest=None)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def existing_speech_ids(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        ids: set[str] = set()
        for row in reader:
            label = normalize_speech_id(str(row.get("id_speech") or ""))
            if label:
                ids.add(label)
        return ids


def append_csv(path: Path, columns: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.is_file()
    if not rows and not new_file:
        return
    with path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=columns,
            extrasaction="ignore",
            lineterminator="\n",
        )
        if new_file:
            writer.writeheader()
        for row in rows:
            writer.writerow({col: flatten_newlines(row.get(col, "")) for col in columns})


def drop_speech_ids(path: Path, columns: list[str], ids: set[str]) -> None:
    if not path.is_file() or not ids:
        return
    with path.open(encoding="utf-8", newline="") as fh:
        kept = [
            row
            for row in csv.DictReader(fh)
            if normalize_speech_id(str(row.get("id_speech") or "")) not in ids
        ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=columns,
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in kept:
            writer.writerow({col: row.get(col, "") for col in columns})


def _parse_code(raw: object) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def consistency_warnings(indicator_rows: list[dict[str, str]]) -> list[str]:
    by_speech: dict[str, dict[str, int]] = {}
    for row in indicator_rows:
        speech_id = row.get("id_speech") or ""
        name = row.get("indicator_name") or ""
        code = _parse_code(row.get("code"))
        if not speech_id or not name or code is None:
            continue
        by_speech.setdefault(speech_id, {})[name] = code
    warnings: list[str] = []
    for speech_id, codes in by_speech.items():
        for child, parent in NESTED_CHECKS:
            if codes.get(child, 0) != 0 and codes.get(parent, 0) == 0:
                warnings.append(
                    f"{speech_id}: {child}={codes.get(child)} pero {parent}=0"
                )
        if codes.get("un_reform_position") == 1 and codes.get(
            "future_multilateralism_position"
        ) not in (None, 6):
            warnings.append(
                f"{speech_id}: un_reform_position=1 pero "
                f"future_multilateralism_position="
                f"{codes.get('future_multilateralism_position')} (debería ser 6)"
            )
    return warnings


def align_reform_to_transformation(indicator_rows: list[dict[str, str]]) -> None:
    """Si UN reform es Positive, future multilateralism pasa a Transformation (6)."""
    by_speech: dict[str, list[dict[str, str]]] = {}
    for row in indicator_rows:
        by_speech.setdefault(row.get("id_speech") or "", []).append(row)
    for speech_id, rows in by_speech.items():
        codes = {row.get("indicator_name"): _parse_code(row.get("code")) for row in rows}
        if codes.get("un_reform_position") != 1:
            continue
        if codes.get("future_multilateralism_position") == 6:
            continue
        for row in rows:
            if row.get("indicator_name") != "future_multilateralism_position":
                continue
            row["code"] = "6"
            row["option"] = option_for("future_multilateralism_position", 6)
            note = row.get("coder_notes") or ""
            aligned = "aligned: un_reform Positive → future Transformation"
            row["coder_notes"] = f"{note} | {aligned}".strip(" |") if note else aligned
            print(f"ALIGN {speech_id}: future_multilateralism_position → 6", file=sys.stderr)


def build_user_message(items: list[tuple[ExtractedSpeech, Path, str]]) -> str:
    parts = [API_INSTRUCTIONS.strip(), ""]
    for speech, path, speech_id in items:
        parts.append(f"===== SPEECH id_speech={speech_id} filename={path.name} =====")
        parts.append(speech.text.strip())
        parts.append("")
    return "\n".join(parts).strip() + "\n"


def _indicator_row(
    *,
    speech_id: str,
    indicator: str,
    code: int,
    textual_extract: str,
    coder_notes: str,
    model: str,
    date_coded: str,
) -> dict[str, str]:
    return {
        "id_speech": speech_id,
        "extract_id": extract_id_for(speech_id, indicator),
        "cluster": CLUSTERS.get(indicator, ""),
        "indicator_name": indicator,
        "code": str(code),
        "option": option_for(indicator, code),
        "textual_extract": textual_extract,
        "coder_notes": coder_notes,
        "coding_source": CODING_SOURCE,
        "model": model,
        "date_coded": date_coded,
    }


def _indicators_by_speech(
    payload: dict,
    allowed: set[str],
) -> dict[str, dict[str, dict]]:
    by_speech: dict[str, dict[str, dict]] = {speech_id: {} for speech_id in allowed}
    raw_indicators = payload.get("indicators") or []
    if not isinstance(raw_indicators, list):
        raise CodingError("indicators no es una lista")
    for raw in raw_indicators:
        if not isinstance(raw, dict):
            continue
        speech_id = normalize_speech_id(str(raw.get("id_speech") or ""))
        name = str(raw.get("indicator_name") or "").strip()
        if speech_id not in allowed:
            continue
        if name not in CLUSTERS:
            continue
        by_speech[speech_id][name] = raw
    return by_speech


def incomplete_speeches(
    payload: dict,
    items: list[tuple[ExtractedSpeech, Path, str]],
) -> dict[str, list[str]]:
    """id_speech → indicadores ausentes o con code inválido. Vacío = todos completos."""
    allowed = {speech_id for _, _, speech_id in items}
    by_speech = _indicators_by_speech(payload, allowed)
    missing: dict[str, list[str]] = {}
    for _, _, speech_id in items:
        found = by_speech.get(speech_id) or {}
        problems: list[str] = []
        for name in INDICATORS:
            raw = found.get(name)
            if not raw:
                problems.append(name)
                continue
            code = _parse_code(raw.get("code"))
            if code is None or option_for(name, code) == "":
                problems.append(name)
        if problems:
            missing[speech_id] = problems
    return missing


def rows_from_payload(
    payload: dict,
    items: list[tuple[ExtractedSpeech, Path, str]],
    *,
    model: str,
    date_coded: str,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    allowed = {speech_id for _, _, speech_id in items}
    by_speech = _indicators_by_speech(payload, allowed)
    skip_ids = set(incomplete_speeches(payload, items))

    indicator_rows: list[dict[str, str]] = []
    for _, _, speech_id in items:
        if speech_id in skip_ids:
            continue
        found = by_speech.get(speech_id) or {}
        for name in INDICATORS:
            raw = found.get(name) or {}
            code = _parse_code(raw.get("code"))
            notes = str(raw.get("coder_notes") or "").strip()
            extract = str(raw.get("textual_extract") or "").strip()
            if code is None:
                code = 0
            if code == 0:
                extract = ""
            indicator_rows.append(
                _indicator_row(
                    speech_id=speech_id,
                    indicator=name,
                    code=code,
                    textual_extract=extract,
                    coder_notes=notes,
                    model=model,
                    date_coded=date_coded,
                )
            )

    emerging_rows: list[dict[str, str]] = []
    raw_emerging = payload.get("emerging_priorities") or []
    if not isinstance(raw_emerging, list):
        raise CodingError("emerging_priorities no es una lista")
    seen: set[tuple[str, str]] = set()
    for raw in raw_emerging:
        if not isinstance(raw, dict):
            continue
        speech_id = normalize_speech_id(str(raw.get("id_speech") or ""))
        if speech_id not in allowed or speech_id in skip_ids:
            continue
        topic = canonical_topic(str(raw.get("emerging_topic") or ""))
        if topic is None:
            original = str(raw.get("emerging_topic") or "").strip()
            print(
                f"SKIP emerging_topic {speech_id!r} {original!r}: no está en la lista cerrada",
                file=sys.stderr,
            )
            continue
        key = (speech_id, topic)
        if key in seen:
            continue
        seen.add(key)
        emerging_rows.append(
            {
                "id_speech": speech_id,
                "id_extract": emerging_extract_id(speech_id, topic),
                "emerging_topic": topic,
                "textual_extract": str(raw.get("textual_extract") or "").strip(),
            }
        )
    align_reform_to_transformation(indicator_rows)
    return indicator_rows, emerging_rows


def _speeches_for_day(
    config: SessionConfig,
    *,
    day: str,
    slug: str | None,
    dest: Path | None,
) -> list[tuple[ExtractedSpeech, Path, str]]:
    paths = list_speech_txts(config, speech_date=day, dest=dest)
    items: list[tuple[ExtractedSpeech, Path, str]] = []
    for path in paths:
        try:
            speech = parse_speech_txt(path)
        except ValueError as exc:
            print(f"SKIP {path.name}: {exc}", file=sys.stderr)
            continue
        if slug and speech.slug != slug:
            continue
        speech_id = speech_id_of(speech, path)
        if not speech_id:
            print(
                f"SKIP {path.name}: sin id_speech (corre publish antes para nombrar M_N.txt)",
                file=sys.stderr,
            )
            continue
        items.append((speech, path, speech_id))
    items.sort(key=lambda item: (parse_speech_id(item[2]) or 10**9, item[0].slug))
    return items


def code_day(
    config: SessionConfig,
    *,
    day: str,
    slug: str | None = None,
    dest: Path | None = None,
    prompt_path: Path | None = None,
    dry_run: bool = False,
    force: bool = False,
    chunk_size: int = CHUNK_SIZE,
) -> int:
    try:
        methodology = load_methodology(prompt_path)
    except CodingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    items = _speeches_for_day(config, day=day, slug=slug, dest=dest)
    if not items:
        print(
            f"error: no hay .txt con id_speech en pipeline/out/{config.id}/{day}",
            file=sys.stderr,
        )
        return 1

    directory = out_dir(config, day, dest=dest)
    already = existing_speech_ids(indicators_csv_path(directory)) if not force else set()
    pending = [item for item in items if item[2] not in already]
    skipped = len(items) - len(pending)
    chunks = chunk_speeches(pending, size=chunk_size)

    print(
        f"coding session={config.id} day={day} speeches={len(items)} "
        f"pending={len(pending)} skipped={skipped} chunks={len(chunks)}",
        file=sys.stderr,
    )
    if dry_run:
        for index, chunk in enumerate(chunks, start=1):
            ids = ",".join(item[2] for item in chunk)
            chars = sum(len(item[0].text) for item in chunk)
            print(
                f"DRY chunk {index}/{len(chunks)} speeches={ids} chars={chars}",
                file=sys.stderr,
            )
        print(
            json.dumps(
                {
                    "day": day,
                    "speeches": len(items),
                    "pending": len(pending),
                    "skipped": skipped,
                    "chunks": len(chunks),
                }
            ),
            file=sys.stderr,
        )
        return 0

    if not pending:
        print("nada para codear (ids ya están en Indicators.csv; usá --force)", file=sys.stderr)
        write_coding_status(config, day, dest=dest)
        return 0

    try:
        api_key, model = anthropic_settings()
    except ClaudeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if force:
        pending_ids = {item[2] for item in pending}
        drop_speech_ids(indicators_csv_path(directory), INDICATOR_COLUMNS, pending_ids)
        drop_speech_ids(emerging_csv_path(directory), EMERGING_COLUMNS, pending_ids)

    date_coded = datetime.now(timezone.utc).date().isoformat()
    errors = 0
    coded = 0
    indicator_total = 0
    emerging_total = 0
    for index, chunk in enumerate(chunks, start=1):
        ids = ",".join(item[2] for item in chunk)
        message = build_user_message(chunk)
        try:
            payload = call_claude(
                message,
                api_key=api_key,
                model=model,
                system=methodology,
                max_tokens=CODING_MAX_TOKENS,
                timeout=CODING_TIMEOUT,
                cache_system=True,
                disable_thinking=True,
            )
            incomplete = incomplete_speeches(payload, chunk)
            complete = [item for item in chunk if item[2] not in incomplete]
            for speech_id, names in incomplete.items():
                errors += 1
                print(
                    f"FAIL speech={speech_id} missing={','.join(names)}",
                    file=sys.stderr,
                )
            if not complete:
                continue
            indicator_rows, emerging_rows = rows_from_payload(
                payload,
                complete,
                model=model,
                date_coded=date_coded,
            )
        except (ClaudeError, CodingError, json.JSONDecodeError) as exc:
            errors += 1
            print(f"FAIL chunk {index}/{len(chunks)} speeches={ids} {exc}", file=sys.stderr)
            continue
        for warning in consistency_warnings(indicator_rows):
            print(f"WARN {warning}", file=sys.stderr)
        append_csv(indicators_csv_path(directory), INDICATOR_COLUMNS, indicator_rows)
        append_csv(emerging_csv_path(directory), EMERGING_COLUMNS, emerging_rows)
        coded += len(complete)
        indicator_total += len(indicator_rows)
        emerging_total += len(emerging_rows)
        print(
            f"OK chunk {index}/{len(chunks)} speeches="
            f"{','.join(item[2] for item in complete)} "
            f"indicators={len(indicator_rows)} emerging={len(emerging_rows)} model={model}",
            file=sys.stderr,
        )

    print(
        json.dumps(
            {
                "coded": coded,
                "skipped": skipped,
                "errors": errors,
                "indicators": indicator_total,
                "emerging": emerging_total,
                "model": model,
            }
        ),
        file=sys.stderr,
    )
    write_coding_status(config, day, dest=dest, model=model)
    return 0 if errors == 0 else 2
