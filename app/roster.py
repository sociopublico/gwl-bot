from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

logger = logging.getLogger(__name__)

_PARTICLES = {
    "da",
    "de",
    "do",
    "dos",
    "das",
    "del",
    "della",
    "van",
    "von",
    "bin",
    "al",
    "el",
    "di",
    "du",
    "la",
    "dar",
}

# Title/protocol words that Whisper leaves in the name/title blob.
_SKIP = _PARTICLES | {
    "president",
    "prime",
    "minister",
    "king",
    "queen",
    "secretary",
    "general",
    "republic",
    "state",
    "government",
    "head",
    "vice",
    "deputy",
    "foreign",
    "excellency",
    "majesty",
    "highness",
    "royal",
    "his",
    "her",
    "mr",
    "mrs",
    "ms",
    "dr",
    "sir",
    "madam",
    "federative",
    "united",
    "kingdom",
    "assembly",
    "address",
    "distinguished",
}

_COUNTRY_SKIP = {
    "the",
    "of",
    "and",
    "republic",
    "federative",
    "kingdom",
    "islamic",
    "democratic",
    "people",
    "peoples",
    "state",
    "union",
    "plurinational",
    "bolivarian",
    "federal",
    "independent",
    "great",
    "northern",
}

# ASR / short names → forma canónica plegada.
_COUNTRY_ALIASES = {
    "brasil": "brazil",
    "brazel": "brazil",
    "usa": "united states of america",
    "us": "united states of america",
    "united states": "united states of america",
    "uk": "united kingdom",
    "britain": "united kingdom",
    "great britain": "united kingdom",
    "england": "united kingdom",
    "turkiye": "turkiye",
    "turkey": "turkiye",
    "naoero": "nauru",
    "south korea": "republic of korea",
    "north korea": "democratic peoples republic of korea",
    "dprk": "democratic peoples republic of korea",
    "ivory coast": "cote d ivoire",
    "uae": "united arab emirates",
    "russia": "russian federation",
    "iran": "iran",
    "syria": "syrian arab republic",
    "venezuela": "venezuela",
    "bolivia": "bolivia",
    "tanzania": "united republic of tanzania",
    "moldova": "republic of moldova",
    "laos": "lao peoples democratic republic",
    "vietnam": "viet nam",
    "viet nam": "viet nam",
    "czechia": "czechia",
    "czech republic": "czechia",
    "netherlands": "netherlands",
    "holland": "netherlands",
    "palestine": "state of palestine",
    "vatican": "holy see",
    "eswatini": "eswatini",
    "swaziland": "eswatini",
    "myanmar": "myanmar",
    "burma": "myanmar",
    "east timor": "timor leste",
    "cabo verde": "cabo verde",
    "cape verde": "cabo verde",
    "drc": "democratic republic of the congo",
    "congo kinshasa": "democratic republic of the congo",
    "gambia": "gambia",
    "micronesia": "micronesia",
}

_OF_COUNTRY_RE = re.compile(r"\bof\s+(?:the\s+)?(.+)$", re.I)


@dataclass(frozen=True)
class RosterEntry:
    name: str
    country: str = ""
    title: str = ""


def fold_name(text: str) -> str:
    stripped = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in stripped if not unicodedata.combining(ch))
    stripped = stripped.replace("-", " ").replace("'", " ")
    stripped = re.sub(r"[^A-Za-z0-9\s]", " ", stripped)
    return " ".join(stripped.casefold().split())


def roster_tokens(name: str) -> list[str]:
    return [token for token in fold_name(name).split() if token not in _SKIP and len(token) > 1]


def country_from_title(title: str) -> str:
    match = _OF_COUNTRY_RE.search(title.strip().strip(" .,;:"))
    return match.group(1).strip(" .,;:") if match else ""


def _parse_entry(raw: str) -> RosterEntry | None:
    line = " ".join(raw.split()).strip()
    if not line or line.startswith("#"):
        return None
    parts = [part.strip() for part in line.split("|")]
    name = parts[0] if parts else ""
    if not name:
        return None
    country = parts[1] if len(parts) > 1 else ""
    title = parts[2] if len(parts) > 2 else ""
    return RosterEntry(name=name, country=country, title=title)


def parse_roster(raw: str) -> tuple[RosterEntry, ...]:
    entries: list[RosterEntry] = []
    seen: set[str] = set()
    for part in re.split(r"[\n;]", raw):
        entry = _parse_entry(part)
        if entry is None:
            continue
        key = fold_name(entry.name)
        if key in seen:
            continue
        seen.add(key)
        entries.append(entry)
    return tuple(entries)


def load_roster_file(path: str) -> tuple[RosterEntry, ...]:
    file_path = Path(path)
    if not file_path.is_file():
        logger.warning("Speaker roster file not found: %s", path)
        return ()
    return parse_roster(file_path.read_text(encoding="utf-8"))


def _as_entry(item: RosterEntry | str) -> RosterEntry:
    if isinstance(item, RosterEntry):
        return item
    parsed = _parse_entry(item)
    return parsed if parsed is not None else RosterEntry(name=item)


def _ratio(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


def roster_score(extracted: str, canonical: str) -> float:
    extracted_tokens = roster_tokens(extracted)
    canonical_tokens = roster_tokens(canonical)
    if not extracted_tokens or not canonical_tokens:
        return 0.0

    def best(token: str, pool: Sequence[str]) -> float:
        return max(_ratio(token, other) for other in pool)

    cover_extracted = sum(best(token, canonical_tokens) for token in extracted_tokens) / len(
        extracted_tokens
    )
    cover_canonical = sum(best(token, extracted_tokens) for token in canonical_tokens) / len(
        canonical_tokens
    )
    full = _ratio("".join(extracted_tokens), "".join(canonical_tokens))
    surname = _ratio(extracted_tokens[-1], canonical_tokens[-1])
    return 0.35 * cover_extracted + 0.35 * cover_canonical + 0.15 * full + 0.15 * surname


def _unique_surname_hit(extracted: str, roster: Sequence[RosterEntry]) -> RosterEntry | None:
    extracted_tokens = roster_tokens(extracted)
    if not extracted_tokens:
        return None
    hits: list[RosterEntry] = []
    for entry in roster:
        can_tokens = roster_tokens(entry.name)
        if not can_tokens:
            continue
        surname = can_tokens[-1]
        if len(surname) < 5:
            continue
        if any(
            len(token) >= 5 and _ratio(token, surname) >= 0.88 for token in extracted_tokens
        ):
            hits.append(entry)
    if len(hits) == 1:
        return hits[0]
    return None


def _alias_country(folded: str) -> str:
    return _COUNTRY_ALIASES.get(folded, folded)


def country_keys(text: str) -> set[str]:
    folded = fold_name(text)
    if not folded:
        return set()
    aliased = _alias_country(folded)
    tokens = [token for token in aliased.split() if token not in _COUNTRY_SKIP and len(token) > 1]
    keys = {folded, aliased}
    if tokens:
        keys.add(" ".join(tokens))
        keys.add(_alias_country(" ".join(tokens)))
    for token in tokens:
        keys.add(token)
        keys.add(_alias_country(token))
    return {key for key in keys if key}


def country_score(extracted: str, canonical: str) -> float:
    left = country_keys(extracted)
    right = country_keys(canonical)
    if not left or not right:
        return 0.0
    overlap = {
        key
        for key in left & right
        if len(key) >= 4 and key not in _COUNTRY_SKIP
    }
    if overlap:
        return 1.0 if any(len(key) >= 5 for key in overlap) else 0.92
    best = 0.0
    for left_key in left:
        for right_key in right:
            best = max(best, _ratio(left_key, right_key))
    return best


def _resolved_country(country: str, title: str) -> str:
    return country.strip() or country_from_title(title)


def _role_key(text: str) -> str:
    folded = fold_name(text)
    if not folded:
        return ""
    if "prime minister" in folded or "premier" in folded or "head of government" in folded:
        return "hg"
    if "vice president" in folded:
        return "vp"
    if "minister" in folded or "secretary of state" in folded:
        return "m"
    if any(
        word in folded
        for word in (
            "president",
            "king",
            "queen",
            "emir",
            "sultan",
            "head of state",
            "grand duke",
            "pope",
            "pontiff",
        )
    ):
        return "hs"
    return ""


def _country_hits(
    roster: Sequence[RosterEntry],
    country: str,
    *,
    threshold: float,
) -> list[tuple[float, RosterEntry]]:
    hits: list[tuple[float, RosterEntry]] = []
    for entry in roster:
        if not entry.country:
            continue
        score = country_score(country, entry.country)
        if score >= threshold:
            hits.append((score, entry))
    hits.sort(key=lambda item: item[0], reverse=True)
    return hits


def _filter_role(
    hits: Sequence[tuple[float, RosterEntry]],
    title: str,
) -> list[tuple[float, RosterEntry]]:
    role = _role_key(title)
    if not role or len(hits) <= 1:
        return list(hits)
    matched = [
        item
        for item in hits
        if _role_key(f"{item[1].title} {item[1].country}") == role
        or _role_key(item[1].title) == role
    ]
    return matched or list(hits)


def match_roster(
    extracted: str,
    roster: Sequence[RosterEntry | str],
    threshold: float,
    *,
    title: str = "",
    country: str = "",
) -> tuple[RosterEntry, float] | None:
    entries = tuple(_as_entry(item) for item in roster)
    if not entries:
        return None

    name = extracted.strip()
    if name.casefold() in {"", "unknown"}:
        name = ""

    name_hit: tuple[RosterEntry, float] | None = None
    if name:
        ranked: list[tuple[float, RosterEntry]] = []
        for entry in entries:
            ranked.append((roster_score(name, entry.name), entry))
        ranked.sort(key=lambda item: item[0], reverse=True)
        best_score, best_entry = ranked[0]
        ambiguous = False
        if len(ranked) > 1:
            second = ranked[1][0]
            if second >= threshold and best_score - second < 0.08:
                ambiguous = True
                logger.info(
                    "Speaker roster ambiguous | asr=%s | %s=%.2f | %s=%.2f",
                    name,
                    best_entry.name,
                    best_score,
                    ranked[1][1].name,
                    second,
                )
        if best_score >= threshold and not ambiguous:
            name_hit = (best_entry, best_score)
        else:
            unique = _unique_surname_hit(name, entries)
            if unique is not None:
                name_hit = (unique, roster_score(name, unique.name))

    resolved = _resolved_country(country, title)
    country_hit: tuple[RosterEntry, float] | None = None
    if resolved:
        hits = _filter_role(_country_hits(entries, resolved, threshold=0.86), title)
        if len(hits) == 1:
            country_hit = (hits[0][1], hits[0][0])
        elif len(hits) > 1:
            logger.info(
                "Speaker roster country ambiguous | country=%s | n=%s",
                resolved,
                len(hits),
            )

    if name_hit and country_hit:
        if fold_name(name_hit[0].name) == fold_name(country_hit[0].name):
            return name_hit
        if name_hit[1] >= 0.78:
            return name_hit
        logger.info(
            "Speaker roster country overrides name | asr=%s | name=%s | country=%s",
            name or title,
            name_hit[0].name,
            country_hit[0].name,
        )
        return country_hit
    if name_hit:
        return name_hit
    if country_hit:
        logger.info(
            "Speaker roster country match | asr=%s | canonical=%s | country=%s | score=%.2f",
            name or title or resolved,
            country_hit[0].name,
            country_hit[0].country,
            country_hit[1],
        )
        return country_hit
    return None
