from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Sequence
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


def fold_name(text: str) -> str:
    stripped = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in stripped if not unicodedata.combining(ch))
    stripped = stripped.replace("-", " ").replace("'", " ")
    stripped = re.sub(r"[^A-Za-z0-9\s]", " ", stripped)
    return " ".join(stripped.casefold().split())


def roster_tokens(name: str) -> list[str]:
    return [token for token in fold_name(name).split() if token not in _SKIP and len(token) > 1]


def parse_roster(raw: str) -> tuple[str, ...]:
    names: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"[\n;|]", raw):
        name = " ".join(part.split()).strip()
        if not name or name.startswith("#"):
            continue
        key = fold_name(name)
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    return tuple(names)


def load_roster_file(path: str) -> tuple[str, ...]:
    file_path = Path(path)
    if not file_path.is_file():
        logger.warning("Speaker roster file not found: %s", path)
        return ()
    return parse_roster(file_path.read_text(encoding="utf-8"))


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


def _unique_surname_hit(extracted: str, roster: Sequence[str]) -> str | None:
    extracted_tokens = roster_tokens(extracted)
    if not extracted_tokens:
        return None
    hits: list[str] = []
    for canonical in roster:
        can_tokens = roster_tokens(canonical)
        if not can_tokens:
            continue
        surname = can_tokens[-1]
        if len(surname) < 5:
            continue
        if any(
            len(token) >= 5 and _ratio(token, surname) >= 0.88 for token in extracted_tokens
        ):
            hits.append(canonical)
    if len(hits) == 1:
        return hits[0]
    return None


def match_roster(
    extracted: str,
    roster: Sequence[str],
    threshold: float,
) -> tuple[str, float] | None:
    if not extracted or not roster:
        return None
    ranked: list[tuple[float, str]] = []
    for canonical in roster:
        ranked.append((roster_score(extracted, canonical), canonical))
    ranked.sort(key=lambda item: item[0], reverse=True)
    best_score, best_name = ranked[0]
    ambiguous = False
    if len(ranked) > 1:
        second = ranked[1][0]
        if second >= threshold and best_score - second < 0.08:
            ambiguous = True
            logger.info(
                "Speaker roster ambiguous | asr=%s | %s=%.2f | %s=%.2f",
                extracted,
                best_name,
                best_score,
                ranked[1][1],
                second,
            )
    if best_score >= threshold and not ambiguous:
        return best_name, best_score
    unique = _unique_surname_hit(extracted, roster)
    if unique is not None:
        score = roster_score(extracted, unique)
        return unique, score
    return None
