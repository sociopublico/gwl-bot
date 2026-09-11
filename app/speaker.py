from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.config import Config
from app.logger import SPEAKER_CHANGED
from app.roster import load_roster_file, match_roster, parse_roster
from app.youtube import format_timecode

logger = logging.getLogger(__name__)

LlmCall = Callable[[str, str | None], dict[str, Any] | None]

_CUE_RE = re.compile(
    r"(?:"
    r"his\s+excellency|her\s+excellency|"
    r"i\s+(?:now\s+)?(?:give|call|yield)\s+(?:the\s+)?floor|"
    r"give\s+(?:the\s+)?floor\s+to|"
    r"the\s+assembly\s+will\s+(?:now\s+)?(?:hear|here)|"
    r"we\s+(?:shall|will)\s+now\s+hear|"
    r"the\s+distinguished\s+(?:representative|delegate|ambassador)"
    r")",
    re.IGNORECASE,
)

_HONORIFIC_RE = re.compile(r"^(?:mr|mrs|ms|miss|dr|sir|madam|sheikh)\.?\s+", re.IGNORECASE)
_EXCELLENCY_AT_RE = re.compile(r"(?:his|her)\s+excellency\s*[,:]?\s*", re.IGNORECASE)
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


def _split_by_title_words(rest: str) -> tuple[str, str | None]:
    words = rest.split()
    name_words: list[str] = []
    title_words: list[str] = []
    in_title = False
    for word in words:
        token = _word_token(word)
        if not in_title and token and _TITLE_WORD_RE.match(token):
            in_title = True
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
            if not in_title and _starts_with_title(part):
                in_title = True
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
    best: Speaker | None = None
    best_at = -1
    for match in _EXCELLENCY_AT_RE.finditer(text):
        name, title = _split_name_and_title(text[match.end() :])
        if not _looks_like_person_name(name):
            continue
        tokens = _name_tokens(name)
        confidence = "strong" if len(tokens) >= 2 else "weak"
        speaker = Speaker(name=name, title=title, confidence=confidence, source="regex")
        if match.start() >= best_at:
            best = speaker
            best_at = match.start()
    return best


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


class SpeakerTracker:
    def __init__(self, config: Config, llm_call: LlmCall | None = None) -> None:
        self.config = config
        self.aliases = parse_aliases(config.speaker_aliases)
        self.roster = parse_roster(config.speaker_roster)
        if config.speaker_roster_file:
            self.roster = self.roster + load_roster_file(config.speaker_roster_file)
        self._llm_call = llm_call
        self._current = Speaker()
        self._pending: Speaker | None = None
        self._previous_text = ""
        if self.roster:
            logger.info("Speaker roster loaded | n=%s", len(self.roster))

    @property
    def current(self) -> Speaker:
        return self._current

    def reset(self) -> None:
        self._current = Speaker()
        self._pending = None
        self._previous_text = ""

    def observe(self, text: str, video_seconds: float | None = None) -> Speaker:
        if self._pending is not None:
            self._current = self._pending
            self._pending = None

        extracted = self._extract(text)
        if extracted is not None:
            name = apply_alias(extracted.name, self.aliases)
            matched = match_roster(name, self.roster, self.config.speaker_roster_threshold)
            if matched is None and extracted.title:
                matched = match_roster(
                    f"{name} {extracted.title}",
                    self.roster,
                    self.config.speaker_roster_threshold,
                )
            if matched is not None:
                canonical, score = matched
                if canonical.casefold() != name.casefold():
                    logger.info(
                        "Speaker roster match | asr=%s | canonical=%s | score=%.2f",
                        name,
                        canonical,
                        score,
                    )
                name = canonical
            extracted = Speaker(
                name=name,
                title=extracted.title,
                country=extracted.country,
                confidence=extracted.confidence,
                source=extracted.source,
            )
            if extracted.name.casefold() != self._current.name.casefold():
                self._pending = extracted
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

        self._previous_text = text
        return self._current

    def _extract(self, text: str) -> Speaker | None:
        compact = " ".join(text.split())
        if not compact:
            return None
        window = compact
        if self._previous_text:
            window = f"{self._previous_text} {compact}"
        cue = has_introduction_cue(compact) or has_introduction_cue(window)
        regex_speaker = extract_introduction_regex(window)
        if regex_speaker is not None and regex_speaker.confidence == "strong":
            return regex_speaker
        if cue and self._llm_enabled():
            llm_speaker = self._try_llm(compact)
            if llm_speaker is not None:
                return llm_speaker
        if regex_speaker is not None:
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
