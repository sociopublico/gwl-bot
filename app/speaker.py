from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.config import Config
from app.logger import SPEAKER_CHANGED
from app.roster import (
    RosterEntry,
    country_from_title,
    country_keys,
    country_score,
    fold_name,
    has_country_words,
    load_roster_file,
    load_roster_json,
    match_roster,
    merge_rosters,
    parse_roster,
    roster_score,
)
from app.youtube import format_timecode

logger = logging.getLogger(__name__)

LlmCall = Callable[[str, str | None], dict[str, Any] | None]
NowFn = Callable[[], datetime]
StatusFn = Callable[[str, str], object]

_NY = ZoneInfo("America/New_York")
SPEAKER_MAX_AGE = timedelta(minutes=90)
_SPEAKER_STATE = "speaker.json"
ROSTER_CHECK_SECONDS = 60.0

_HONORIFIC = r"(?:his|her)\s+(?:royal\s+)?(?:excellency|majesty|highness)"

_CUE_RE = re.compile(
    r"(?:"
    r"i\s+(?:now\s+)?(?:give|call|yield)\s+(?:the\s+)?floor|"
    r"give\s+(?:the\s+)?floor\s+to|"
    r"the\s+assembly\s+will\s+(?:now\s+)?(?:hear|here)|"
    r"we\s+(?:shall|will)\s+now\s+hear|"
    r"the\s+distinguished\s+(?:representative|delegate|ambassador)|"
    r"invite\s+(?:him|her|them)\s+to\s+address|"
    r"(?:protocol\s+to\s+)?escort\s+(?:his|her)\s+excellency|"
    r"request\s+protocol\s+to\s+escort"
    r")",
    re.IGNORECASE,
)
# Menciones a otro dignatario dentro del discurso, no una intro del chair.
_NOT_INTRO_RE = re.compile(
    r"\b(?:"
    r"congratulat\w+|"
    r"on\s+(?:his|her|your)\s+election|"
    r"allow\s+me\s+to\s+(?:congratulate|thank|pay)|"
    r"i\s+(?:wish\s+to\s+)?thank\s+(?:his|her)\s+excellency|"
    r"former\s+presidents?\s+of\s+the\s+general\s+assembly"
    r")\b",
    re.IGNORECASE,
)

_HONORIFIC_RE = re.compile(r"^(?:mr|mrs|ms|miss|dr|sir|madam|sheikh)\.?\s+", re.IGNORECASE)
_HONORIFIC_AT_RE = re.compile(rf"{_HONORIFIC}\s*[,:]?\s*", re.IGNORECASE)
# El ASR pierde el "Her": "We are Excellency Maria Malma Stena-Gad, Minister ...".
_BARE_HONORIFIC_AT_RE = re.compile(
    r"(?<!your\s)\b(?:excellency|majesty|highness)\b\s*[,:]?\s*",
    re.IGNORECASE,
)
# El chair cierra el discurso: agradece al orador o levanta la sesión.
_ASSEMBLY_THANKS_RE = re.compile(
    r"\bon\s+behalf\s+of\s+the\s+(?:general\s+)?assembly,?\s+i\s+(?:wish\s+to\s+)?thank\b",
    re.IGNORECASE,
)
_CHAIR_THANKS_RE = re.compile(
    r"\bi\s+(?:wish\s+to\s+)?thank\s+the\s+(.{3,160}?)(?=[.;!?]|\bi\s+now\b|$)",
    re.IGNORECASE,
)
_THANK_YOU_RE = re.compile(r"\bthank\s+you\b", re.IGNORECASE)
_MEETING_END_RE = re.compile(
    r"\b(?:"
    r"(?:the|this)\s+meeting\s+(?:is|stands)\s+(?:now\s+)?(?:adjourned|suspended|closed)|"
    r"(?:heard\s+)?the\s+last\s+speaker\s+in\s+the\s+general\s+debate\s+for\s+this\s+meeting"
    r")\b",
    re.IGNORECASE,
)
_REPLY_RE = re.compile(
    r"\bi\s+(?:now\s+)?(?:call\s+(?:up)?on|give\s+(?:the\s+)?floor\s+to)\s+the\s+"
    r"(?:distinguished\s+)?(?:representative|delegate|delegation)\s+of\s+(?:the\s+)?"
    r"(.+?)(?=[.,;:!?]|\s+(?:to|who|in|for|on|and)\b|$)",
    re.IGNORECASE,
)
_THANK_JUNK = {
    "foreign",
    "affairs",
    "minister",
    "prime",
    "president",
    "deputy",
    "council",
    "government",
    "defence",
    "defense",
    "national",
    "security",
    "cooperation",
    "international",
    "relations",
    "development",
    "integration",
    "integrations",
    "regional",
    "public",
    "general",
    "assembly",
}
# Tras un cierre, la intro puede llegar sin "give the floor" (cortada entre chunks).
_AFTER_END_SECONDS = 150.0
# "I thank the President of Kenya" dentro del discurso no cierra a nadie recién empezado.
_MIN_SPEECH_SECONDS = 120.0
_INVITE_CUT_RE = re.compile(
    r"\s+and\s+(?:i\s+)?(?:invite|ask|call)\b|\s+and\s+invite\b",
    re.IGNORECASE,
)
_TITLE_WORD_RE = re.compile(
    r"^(?:president|prime|minister|king|queen|secretary|emir|amir|sultan|"
    r"chancellor|head|vice|deputy|foreign|premier|taoiseach|chairman|chair|"
    r"grand|duke|pope|pontiff|general)$",
    re.IGNORECASE,
)
_ROLE_OF_RE = re.compile(
    r"\b(?:the\s+)?"
    r"(?:president|prime\s+minister|king|queen|emir|chancellor|"
    r"(?:distinguished\s+)?(?:representative|delegate|ambassador)|"
    r"head\s+of\s+(?:state|government))"
    r"\s+of\s+(?:the\s+)?(.+?)"
    r"(?=\s+and\s+(?:i\s+)?(?:invite|ask|call)\b|\s+and\s+invite\b|[.,;:]|$)",
    re.IGNORECASE,
)
# País en la oración que sigue a una intro, cuando el nombre salió mal.
_FOLLOWUP_COUNTRY_RE = re.compile(
    r"\b(?:on behalf of|republic of|people of)\s+(?:the\s+)?(.+?)(?=[,.;:]|$)",
    re.IGNORECASE,
)
_TITLE_JUNK = {"by", "the", "a", "an"}
# El nombre puede no llegar al umbral si el país de la oración siguiente es único.
_COUNTRY_CONFIRM_SCORE = 0.4

_STOPWORDS = {
    "the",
    "of",
    "and",
    "a",
    "an",
    "to",
    "for",
    "his",
    "her",
    "excellency",
    "majesty",
    "highness",
    "royal",
    "mr",
    "mrs",
    "ms",
    "miss",
    "dr",
    "sir",
    "madam",
    "president",
    "prime",
    "minister",
    "representative",
    "delegate",
    "ambassador",
    "king",
    "queen",
    "secretary",
    "general",
    "head",
    "state",
    "government",
    "floor",
    "assembly",
    "address",
    "distinguished",
    "republic",
    "federative",
    "united",
    "kingdom",
    "da",
    "de",
    "dos",
    "del",
    "van",
    "von",
}

_NAME_JUNK = {
    "invite",
    "address",
    "assembly",
    "floor",
    "photo",
    "library",
    "attention",
    "manner",
    "decided",
    "finally",
    "draw",
    "nations",
    "united",
    "hear",
    "proceed",
    "agrees",
}

_LLM_SYSTEM = (
    "You extract who is being introduced to speak at a UN-style assembly. "
    "Return JSON only with keys: is_introduction (boolean), name (string or null), "
    "title (string or null), country (string or null). "
    "If the chair is speaking without introducing the next speaker, "
    "is_introduction must be false."
)


@dataclass(frozen=True)
class Speaker:
    name: str = "unknown"
    title: str | None = None
    country: str | None = None
    confidence: str = "unknown"
    source: str = "regex"

    @property
    def display_title(self) -> str | None:
        parts = [part for part in (self.title, self.country) if part]
        return ", ".join(parts) if parts else None


def parse_aliases(raw: str) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for part in raw.split(","):
        piece = part.strip()
        if not piece or ":" not in piece:
            continue
        key, value = piece.split(":", 1)
        key = key.strip().casefold()
        value = value.strip()
        if key and value:
            aliases[key] = value
    return aliases


def apply_alias(name: str, aliases: dict[str, str]) -> str:
    folded = name.casefold()
    if folded in aliases:
        return aliases[folded]
    for key, value in aliases.items():
        if key and key in folded:
            return value
    return name


def has_introduction_cue(text: str) -> bool:
    return bool(_CUE_RE.search(text))


def _name_tokens(name: str) -> list[str]:
    tokens = re.findall(r"[A-Za-zÀ-ÿ']+", name)
    return [token for token in tokens if token.casefold() not in _STOPWORDS and len(token) > 1]


def _looks_like_person_name(name: str) -> bool:
    tokens = _name_tokens(name)
    if not tokens:
        return False
    if any(token.casefold() in _NAME_JUNK for token in tokens):
        return False
    return True


def _word_token(word: str) -> str:
    return re.sub(r"[^A-Za-zÀ-ÿ']", "", word)


def _starts_with_title(part: str) -> bool:
    first = part.split()[0] if part.split() else ""
    token = _word_token(first)
    return bool(token and _TITLE_WORD_RE.match(token))


def _title_word_index(part: str) -> int | None:
    for index, word in enumerate(part.split()):
        token = _word_token(word)
        if token and _TITLE_WORD_RE.match(token):
            return index
    return None


def _split_by_title_words(rest: str) -> tuple[str, str | None]:
    words = rest.split()
    name_words: list[str] = []
    title_words: list[str] = []
    in_title = False
    for word in words:
        token = _word_token(word)
        if not in_title and token and _TITLE_WORD_RE.match(token):
            in_title = True
            if name_words and _word_token(name_words[-1]).casefold() in _TITLE_JUNK:
                name_words.pop()
        if in_title:
            title_words.append(word)
        else:
            name_words.append(word)
        if not in_title and len(name_words) >= 8:
            break
    name = " ".join(name_words).strip(" ,")
    title = " ".join(title_words).strip(" ,") or None
    return name, title


def _split_name_and_title(rest: str) -> tuple[str, str | None]:
    rest = _HONORIFIC_RE.sub("", rest.strip(), count=1)
    rest = _INVITE_CUT_RE.split(rest, maxsplit=1)[0]
    rest = rest.split(".")[0]
    rest = " ".join(rest.split()).strip(" ,;:")
    if not rest:
        return "", None

    # Whisper often inserts commas between name fragments
    # ("red-chap, tie-jip, Erdogan, president of..."). Don't treat the first
    # comma as name/title; stop at the first segment that starts a title.
    parts = [part.strip(" ,") for part in rest.split(",") if part.strip(" ,")]
    if len(parts) >= 2:
        name_parts: list[str] = []
        title_parts: list[str] = []
        in_title = False
        for part in parts:
            title_at = None if in_title else _title_word_index(part)
            if title_at is not None:
                in_title = True
                words = part.split()
                prefix = [
                    word
                    for word in words[:title_at]
                    if _word_token(word).casefold() not in _TITLE_JUNK
                ]
                if prefix:
                    name_parts.append(" ".join(prefix))
                title_parts.append(" ".join(words[title_at:]))
                continue
            if in_title:
                title_parts.append(part)
            else:
                name_parts.append(part)
        if name_parts:
            name = " ".join(name_parts).strip(" ,")
            title = ", ".join(title_parts).strip(" ,") or None
            return name, title

    return _split_by_title_words(rest)


def extract_introduction_regex(text: str) -> Speaker | None:
    best = _extract_honorific(text, _HONORIFIC_AT_RE)
    if best is None:
        best = _extract_honorific(text, _BARE_HONORIFIC_AT_RE)
    if best is not None:
        return best
    return extract_role_country(text)


def _extract_honorific(text: str, pattern: re.Pattern[str]) -> Speaker | None:
    best: Speaker | None = None
    best_at = -1
    for match in pattern.finditer(text):
        name, title = _split_name_and_title(text[match.end() :])
        if not _looks_like_person_name(name):
            continue
        tokens = _name_tokens(name)
        confidence = "strong" if len(tokens) >= 2 else "weak"
        country = country_from_title(title or "") or None
        if not country:
            role = _ROLE_OF_RE.search(text[match.end() :])
            if role:
                country = " ".join(role.group(1).split()).strip(" ,;:") or None
                if not title:
                    title = " ".join(role.group(0).split()).strip(" ,;:") or None
        speaker = Speaker(
            name=name,
            title=title,
            country=country,
            confidence=confidence,
            source="regex",
        )
        if match.start() >= best_at:
            best = speaker
            best_at = match.start()
    return best


def extract_role_country(text: str) -> Speaker | None:
    compact = _INVITE_CUT_RE.split(text, maxsplit=1)[0]
    match = _ROLE_OF_RE.search(compact)
    if not match:
        return None
    country = " ".join(match.group(1).split()).strip(" ,;:")
    if len(country) < 3:
        return None
    title = " ".join(match.group(0).split()).strip(" ,;:")
    return Speaker(
        name="",
        title=title,
        country=country,
        confidence="weak",
        source="regex",
    )


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        found = re.search(r"\{.*\}", text, re.DOTALL)
        if not found:
            raise
        data = json.loads(found.group(0))
    if not isinstance(data, dict):
        raise ValueError("LLM response is not a JSON object")
    return data


def default_llm_extract(config: Config, text: str, previous: str | None) -> dict[str, Any] | None:
    url = config.speaker_llm_base_url.rstrip("/") + "/chat/completions"
    user = text.strip()
    if previous:
        user = f"Previous chunk:\n{previous.strip()}\n\nCurrent chunk:\n{user}"
    payload = {
        "model": config.speaker_llm_model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": _LLM_SYSTEM},
            {"role": "user", "content": user},
        ],
    }
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {config.speaker_llm_api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=config.speaker_llm_timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"speaker LLM request failed: {exc}") from exc

    parsed = json.loads(raw)
    content = parsed["choices"][0]["message"]["content"]
    return _parse_json_object(content)


def speaker_from_llm(data: dict[str, Any]) -> Speaker | None:
    if not data.get("is_introduction"):
        return None
    name = str(data.get("name") or "").strip()
    if not name:
        return None
    title = str(data.get("title") or "").strip() or None
    country = str(data.get("country") or "").strip() or None
    return Speaker(name=name, title=title, country=country, confidence="weak", source="llm")


def _named(speaker: Speaker) -> bool:
    return bool(speaker.name) and speaker.name.casefold() != "unknown"


def _iso(stamp: datetime | None) -> str:
    if stamp is None:
        return ""
    return stamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _speaker_record(speaker: Speaker, started_at: datetime) -> dict[str, str]:
    return {
        "name": speaker.name,
        "title": speaker.title or "",
        "country": speaker.country or "",
        "confidence": speaker.confidence,
        "source": speaker.source,
        "started_at": _iso(started_at),
    }


def _parse_started(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def _restore_reason(started_at: datetime, now: datetime) -> str | None:
    if started_at.astimezone(_NY).date() != now.astimezone(_NY).date():
        return "previous day"
    if now - started_at > SPEAKER_MAX_AGE:
        return "older than 90m"
    return None


def _fresh_speaker(raw: object, now: datetime) -> tuple[Speaker, datetime] | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    if not name or name.casefold() == "unknown":
        return None
    started_at = _parse_started(raw.get("started_at"))
    if started_at is None:
        logger.info("SPEAKER_RESTORE_SKIPPED | %s | reason=missing timestamp", name)
        return None
    reason = _restore_reason(started_at, now)
    if reason is not None:
        logger.info(
            "SPEAKER_RESTORE_SKIPPED | %s | started=%s | reason=%s",
            name,
            _iso(started_at),
            reason,
        )
        return None
    title = str(raw.get("title") or "").strip() or None
    country = str(raw.get("country") or "").strip() or None
    return (
        Speaker(
            name=name,
            title=title,
            country=country,
            confidence=str(raw.get("confidence") or "unknown"),
            source=str(raw.get("source") or "restored"),
        ),
        started_at,
    )


def _mtime(path: Path | None) -> float | None:
    if path is None:
        return None
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _unverified_ok(raw: Speaker | None, name: str, cue_now: bool) -> bool:
    """Intro clara del chair (nombre completo + país) aunque no esté en la agenda."""
    if raw is None or not cue_now or not name:
        return False
    if raw.source != "regex" or raw.confidence != "strong":
        return False
    return bool(raw.country) and has_country_words(raw.country or "")


def _country_mentions(text: str, roster: Sequence[RosterEntry]) -> list[RosterEntry]:
    """Países de la agenda que aparecen en el texto, por frase o por palabra."""
    folded = fold_name(text)
    padded = f" {folded} "
    phrases = [match.group(1) for match in _FOLLOWUP_COUNTRY_RE.finditer(text)]
    phrases.extend(match.group(1) for match in _ROLE_OF_RE.finditer(text))
    hits: list[RosterEntry] = []
    seen: set[str] = set()
    for entry in roster:
        if not entry.country or entry.name in seen:
            continue
        keys = [key for key in country_keys(entry.country) if len(key) >= 5]
        phrase_hit = any(country_score(phrase, entry.country) >= 0.86 for phrase in phrases)
        word_hit = any(f" {key} " in padded for key in keys)
        if phrase_hit or word_hit:
            hits.append(entry)
            seen.add(entry.name)
    return hits


def confirm_roster_by_country(
    text: str,
    waiting: Speaker,
    roster: Sequence[RosterEntry],
) -> Speaker | None:
    """Si la oración nombra un solo país y el nombre se parece, usa esa ficha."""
    if not roster or not waiting.name or waiting.name.casefold() == "unknown":
        return None
    mentions = _country_mentions(text, roster)
    if len(mentions) != 1:
        return None
    entry = mentions[0]
    if roster_score(waiting.name, entry.name) < _COUNTRY_CONFIRM_SCORE:
        return None
    return Speaker(
        name=entry.name,
        title=waiting.title or entry.title or None,
        country=entry.country or waiting.country,
        confidence=waiting.confidence,
        source="roster-country",
    )


def speech_end_cue(
    text: str,
    current: Speaker,
    roster: Sequence[RosterEntry] = (),
    previous: str = "",
) -> str | None:
    """Motivo si el chair cierra el discurso de `current` o la sesión."""
    if _MEETING_END_RE.search(text):
        return "meeting end"
    if not _named(current):
        return None
    if _ASSEMBLY_THANKS_RE.search(text):
        return "chair thanks"
    # El país del tracker viene del ASR ("Foreign Affairs of Guatemala"): manda el de la agenda.
    countries = [
        entry.country
        for entry in roster
        if entry.country and entry.name.casefold() == current.name.casefold()
    ]
    if not countries and current.country:
        countries = [current.country]
    targets = {
        key
        for country in countries
        for key in country_keys(country)
        if len(key) >= 4 and not set(key.split()) <= _THANK_JUNK and has_country_words(key)
    }
    surname = _name_tokens(current.name)[-1:] if current.name else []
    targets.update(fold_name(token) for token in surname if len(token) >= 4)
    cue = has_introduction_cue(text)
    for match in _CHAIR_THANKS_RE.finditer(text):
        thanked = f" {fold_name(match.group(1))} "
        if "general assembly" in thanked:
            continue
        if not any(f" {target} " in thanked for target in targets if target):
            continue
        # El orador cierra con "Thank you" y recién ahí agradece el chair; así
        # "I thank the President of Guatemala" dentro del discurso no corta.
        before = f"{previous[-200:]} {text[: match.start()]}"
        if cue or _THANK_YOU_RE.search(before):
            return "chair thanks"
    return None


def reply_speaker(text: str) -> Speaker | None:
    """'I call on the representative of India' (derecho a réplica)."""
    match = _REPLY_RE.search(text)
    if not match:
        return None
    country = " ".join(match.group(1).split()).strip(" ,;:")
    if not has_country_words(country):
        return None
    return Speaker(
        name=f"{country} (right of reply)",
        title="Right of reply",
        country=country,
        confidence="strong",
        source="reply",
    )


class SpeakerTracker:
    def __init__(
        self,
        config: Config,
        llm_call: LlmCall | None = None,
        now: NowFn | None = None,
        *,
        on_roster_stale: StatusFn | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self.config = config
        self.aliases = parse_aliases(config.speaker_aliases)
        self._inline_roster = parse_roster(config.speaker_roster)
        self.roster: tuple[RosterEntry, ...] = self._inline_roster
        self._llm_call = llm_call
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic or time.monotonic
        self._on_roster_stale = on_roster_stale
        self._roster_stamp: tuple[str, float | None, float | None] | None = None
        self._roster_checked_at: float | None = None
        self._stale_warned_day: str | None = None
        self._current = Speaker()
        self._current_started_at: datetime | None = None
        self._pending: Speaker | None = None
        self._pending_started_at: datetime | None = None
        self._awaiting: Speaker | None = None
        self._previous_text = ""
        self._ended_at: datetime | None = None
        self.refresh_roster(force=True)
        self._load()

    @property
    def current(self) -> Speaker:
        return self._current

    def refresh_roster(self, *, force: bool = False) -> bool:
        """Relee el roster si cambió el día (NY) o el mtime de los archivos."""
        checked = self._monotonic()
        if (
            not force
            and self._roster_checked_at is not None
            and checked - self._roster_checked_at < ROSTER_CHECK_SECONDS
        ):
            return False
        self._roster_checked_at = checked
        day = self._now().astimezone(_NY).date().isoformat()
        day_path = self._day_roster_path(day)
        file_raw = (self.config.speaker_roster_file or "").strip()
        file_path = Path(file_raw) if file_raw else None
        stamp = (day, _mtime(day_path), _mtime(file_path))
        if not force and stamp == self._roster_stamp:
            return False
        self._roster_stamp = stamp

        day_entries = load_roster_json(day_path) if day_path is not None else ()
        # El JSON del día manda; speakers.txt puede ser de otro día.
        if day_entries:
            file_entries: tuple[RosterEntry, ...] = ()
            source = str(day_path)
        else:
            file_entries = load_roster_file(file_raw) if file_raw else ()
            source = file_raw or "inline"
        self.roster = merge_rosters(self._inline_roster, day_entries, file_entries)
        if self.roster:
            logger.info(
                "Speaker roster loaded | n=%s | day=%s | source=%s",
                len(self.roster),
                day,
                source,
            )
        if day_path is not None and not day_entries:
            self._warn_roster_stale(day, day_path)
        return True

    def _day_roster_path(self, day: str) -> Path | None:
        raw = (self.config.speaker_roster_dir or "").strip()
        if not raw:
            return None
        return Path(raw) / f"{day}.json"

    def _warn_roster_stale(self, day: str, path: Path) -> None:
        if self._stale_warned_day == day:
            return
        self._stale_warned_day = day
        fallback = self.config.speaker_roster_file or "ninguno"
        logger.warning(
            "ROSTER_STALE | day=%s | missing=%s | fallback=%s | n=%s",
            day,
            path,
            fallback,
            len(self.roster),
        )
        if self._on_roster_stale is None:
            return
        subject = f"ROSTER_STALE | falta el roster de oradores del {day}"
        body = (
            f"El monitor no encontró {path}.\n"
            f"Está usando {fallback} ({len(self.roster)} oradores), que puede ser de otro día.\n"
            "Los oradores que no estén ahí van a quedar sin verificar o como unknown.\n\n"
            f"Corré `python -m pipeline roster --session 81 --day {day}`, subí el JSON "
            "y hacé git pull en el servidor. El monitor lo toma solo en ~1 minuto."
        )
        try:
            self._on_roster_stale(subject, body)
        except Exception as exc:
            logger.warning("Roster stale mail failed: %s", exc)

    def reset(self) -> None:
        self._current = Speaker()
        self._current_started_at = None
        self._pending = None
        self._pending_started_at = None
        self._awaiting = None
        self._previous_text = ""
        self._ended_at = None
        self._persist()

    def _end_reason(self, compact: str) -> str | None:
        reason = speech_end_cue(compact, self._current, self.roster, self._previous_text)
        if reason != "chair thanks" or self._current_started_at is None:
            return reason
        elapsed = (self._now() - self._current_started_at).total_seconds()
        return reason if elapsed >= _MIN_SPEECH_SECONDS else None

    def _recently_ended(self) -> bool:
        if self._ended_at is None:
            return False
        return (self._now() - self._ended_at).total_seconds() <= _AFTER_END_SECONDS

    def observe(self, text: str, video_seconds: float | None = None) -> Speaker:
        self.refresh_roster()
        changed = False
        if self._pending is not None:
            self._current = self._pending
            self._current_started_at = self._pending_started_at or self._now()
            self._pending = None
            self._pending_started_at = None
            changed = True

        attributed = self._current
        prior = self._awaiting
        compact = " ".join(text.split())
        cue_now = has_introduction_cue(compact)
        # Este chunk todavía es del orador saliente; desde el próximo, unknown
        # (así sale su mail y lo que siga no se le pega).
        end_reason = self._end_reason(compact)
        if end_reason is not None and _named(self._current):
            logger.info(
                "SPEECH_END | %s | reason=%s",
                self._current.name,
                end_reason,
            )
            self._pending = Speaker()
            self._pending_started_at = self._now()
            self._ended_at = self._now()
            self._awaiting = None
        elif end_reason == "meeting end":
            self._ended_at = self._now()
        # La intro falló en el chunk anterior. Si esta oración nombra un solo
        # país de la agenda y el nombre se parece, ese es el orador.
        if prior is not None and not cue_now:
            self._awaiting = None
            confirmed = confirm_roster_by_country(compact, prior, self.roster)
            if confirmed is not None:
                logger.info(
                    "Speaker confirmed by country | asr=%s | canonical=%s | country=%s",
                    prior.name,
                    confirmed.name,
                    confirmed.country or "",
                )
                self._current = confirmed
                self._current_started_at = self._now()
                changed = True
                attributed = confirmed
                self._previous_text = text
                if changed:
                    self._persist()
                return attributed

        extracted = self._extract(text)
        raw = extracted
        if extracted is not None:
            name = apply_alias(extracted.name, self.aliases) if extracted.name else ""
            matched = match_roster(
                name,
                self.roster,
                self.config.speaker_roster_threshold,
                title=extracted.title or "",
                country=extracted.country or "",
            )
            if matched is not None:
                entry, score = matched
                if entry.name.casefold() != name.casefold():
                    logger.info(
                        "Speaker roster match | asr=%s | canonical=%s | score=%.2f",
                        name or extracted.title or extracted.country,
                        entry.name,
                        score,
                    )
                name = entry.name
                extracted = Speaker(
                    name=name,
                    title=extracted.title or entry.title or None,
                    country=extracted.country or entry.country or None,
                    confidence=extracted.confidence,
                    source=extracted.source,
                )
                self._awaiting = None
            elif name and not self.roster:
                extracted = Speaker(
                    name=name,
                    title=extracted.title,
                    country=extracted.country,
                    confidence=extracted.confidence,
                    source=extracted.source,
                )
            elif _unverified_ok(raw, name, cue_now):
                # Sin esto el orador anterior se queda con las citas del nuevo.
                logger.info(
                    "SPEAKER_UNVERIFIED | asr=%s | title=%s | country=%s | not in agenda",
                    name,
                    extracted.title or "",
                    extracted.country or "",
                )
                extracted = Speaker(
                    name=name,
                    title=extracted.title,
                    country=extracted.country,
                    confidence=extracted.confidence,
                    source="asr-unverified",
                )
                self._awaiting = None
            else:
                if self.roster and (name or extracted.title or extracted.country):
                    logger.info(
                        "Speaker ignored (not in agenda) | asr=%s | title=%s | country=%s",
                        name or "",
                        extracted.title or "",
                        extracted.country or "",
                    )
                if (
                    cue_now
                    and self.roster
                    and raw is not None
                    and raw.name
                    and raw.name.casefold() != "unknown"
                ):
                    self._awaiting = raw
                extracted = None
        if extracted is None:
            extracted = self._reply(compact)
        if extracted is not None:
            if extracted.name.casefold() != self._current.name.casefold():
                self._pending = extracted
                self._pending_started_at = self._now()
                changed = True
                stamp = format_timecode(video_seconds or 0.0)
                title = extracted.display_title or ""
                logger.log(
                    SPEAKER_CHANGED,
                    "%s | %s | %s | source=%s",
                    stamp,
                    extracted.name,
                    title,
                    extracted.source,
                )
                # Chunk de intro del chair: no atribuir keywords al orador saliente.
                attributed = Speaker()

        self._previous_text = text
        if changed:
            self._persist()
        return attributed

    def _reply(self, compact: str) -> Speaker | None:
        reply = reply_speaker(compact)
        if reply is None:
            return None
        matched = match_roster(
            "",
            self.roster,
            self.config.speaker_roster_threshold,
            country=reply.country or "",
        )
        if matched is not None:
            entry = matched[0]
            return Speaker(
                name=entry.name,
                title=entry.title or None,
                country=entry.country or reply.country,
                confidence="strong",
                source="roster-country",
            )
        return reply

    def _state_path(self) -> Path | None:
        raw = (self.config.log_dir or "").strip()
        if not raw:
            return None
        return Path(raw) / _SPEAKER_STATE

    def _load(self) -> None:
        path = self._state_path()
        if path is None or not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Speaker state unreadable | path=%s | %s", path, exc)
            return
        if not isinstance(data, dict):
            return
        now = self._now()
        current = _fresh_speaker(data.get("current"), now)
        pending = _fresh_speaker(data.get("pending"), now)
        if current is None and pending is None:
            self._persist()
            return
        if current is not None:
            self._current, self._current_started_at = current
            logger.info(
                "SPEAKER_RESTORED | %s | started=%s",
                self._current.name,
                _iso(self._current_started_at),
            )
        if pending is not None:
            self._pending, self._pending_started_at = pending
            logger.info(
                "SPEAKER_RESTORED | pending %s | started=%s",
                self._pending.name,
                _iso(self._pending_started_at),
            )

    def _persist(self) -> None:
        path = self._state_path()
        if path is None:
            return
        payload: dict[str, dict[str, str]] = {}
        if _named(self._current) and self._current_started_at is not None:
            payload["current"] = _speaker_record(self._current, self._current_started_at)
        if (
            self._pending is not None
            and _named(self._pending)
            and self._pending_started_at is not None
        ):
            payload["pending"] = _speaker_record(self._pending, self._pending_started_at)
        try:
            if not payload:
                path.unlink(missing_ok=True)
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(payload), encoding="utf-8")
            temporary.replace(path)
        except OSError as exc:
            logger.warning("Speaker state write failed | path=%s | %s", path, exc)

    def _extract(self, text: str) -> Speaker | None:
        compact = " ".join(text.split())
        if not compact:
            return None
        window = compact
        if self._previous_text:
            window = f"{self._previous_text} {compact}"
        cue_now = has_introduction_cue(compact)
        cue_window = has_introduction_cue(window)
        if _NOT_INTRO_RE.search(compact) and not cue_now:
            return None
        if not cue_now and not cue_window:
            if not self._recently_ended():
                return None
            regex_speaker = extract_introduction_regex(window)
            if regex_speaker is not None and regex_speaker.confidence == "strong":
                return regex_speaker
            return None
        source = compact if cue_now else window
        regex_speaker = extract_introduction_regex(source)
        cue = cue_now or cue_window
        if regex_speaker is not None and regex_speaker.confidence == "strong":
            return regex_speaker
        if cue and self._llm_enabled():
            llm_speaker = self._try_llm(compact)
            if llm_speaker is not None:
                return llm_speaker
        if regex_speaker is not None:
            if regex_speaker.name or cue:
                return regex_speaker
        return None

    def _llm_enabled(self) -> bool:
        return self._llm_call is not None or bool(self.config.speaker_llm_api_key)

    def _try_llm(self, text: str) -> Speaker | None:
        fn = self._llm_call
        if fn is None:
            fn = lambda current, previous: default_llm_extract(self.config, current, previous)
        try:
            data = fn(text, self._previous_text or None)
        except Exception as exc:
            logger.warning("Speaker LLM failed: %s", exc)
            return None
        if not data:
            return None
        try:
            return speaker_from_llm(data)
        except Exception as exc:
            logger.warning("Speaker LLM response invalid: %s", exc)
            return None
