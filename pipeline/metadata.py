from __future__ import annotations

import re
from dataclasses import dataclass

from pipeline.countries import (
    INSTITUTIONAL_LEVELS,
    NO_ISO_SLUGS,
    CountryIndex,
    CountryMatch,
)
from pipeline.models import ExtractedSpeech
from pipeline.protocol import ProtocolIndex, load_protocol_index

METADATA_COLUMNS = [
    "id_speech",
    "num_of_appearance",
    "date_time",
    "iso_country",
    "country",
    "region",
    "subregion",
    "security_council_member",
    "g20_member",
    "speaker_name",
    "speaker_level",
    "speaker_pronouns",
    "speaker_gender",
    "ficha_url",
    "statement_web_url",
    "transcript_url",
    "source",
    "original language",
    "transformation",
]

LANGUAGE_NAMES = {
    "en": "english",
    "fr": "french",
    "es": "spanish",
    "pt": "portuguese",
    "ar": "arabic",
    "zh": "chinese",
    "ru": "russian",
    "de": "german",
    "it": "italian",
    "nl": "dutch",
    "tr": "turkish",
    "ko": "korean",
    "ja": "japanese",
    "pl": "polish",
    "uk": "ukrainian",
    "el": "greek",
    "he": "hebrew",
    "fa": "persian",
    "hi": "hindi",
    "bn": "bengali",
    "ur": "urdu",
    "id": "indonesian",
    "ms": "malay",
    "th": "thai",
    "vi": "vietnamese",
    "sw": "swahili",
    "am": "amharic",
    "so": "somali",
    "sq": "albanian",
    "mk": "macedonian",
    "sr": "serbian",
    "hr": "croatian",
    "bs": "bosnian",
    "sl": "slovenian",
    "sk": "slovak",
    "cs": "czech",
    "hu": "hungarian",
    "ro": "romanian",
    "bg": "bulgarian",
    "fi": "finnish",
    "sv": "swedish",
    "da": "danish",
    "no": "norwegian",
    "nb": "norwegian",
    "is": "icelandic",
    "lt": "lithuanian",
    "lv": "latvian",
    "et": "estonian",
    "ga": "irish",
    "mt": "maltese",
    "cy": "welsh",
    "ka": "georgian",
    "hy": "armenian",
    "az": "azerbaijani",
    "kk": "kazakh",
    "uz": "uzbek",
    "ky": "kyrgyz",
    "tg": "tajik",
    "mn": "mongolian",
    "ne": "nepali",
    "si": "sinhala",
    "my": "burmese",
    "km": "khmer",
    "lo": "lao",
    "ps": "pashto",
    "ku": "kurdish",
    "tl": "tagalog",
    "fil": "filipino",
}

_SG = re.compile(r"\bsecretary[\s-]+general\b", re.I)
_PGA = re.compile(r"\bpresident of the general assembly\b", re.I)
_HS = re.compile(
    r"\b(president|king|queen|amir|emir|pope|emperor|sultan|grand duke|"
    r"head of state|governor-?general)\b",
    re.I,
)
_HG = re.compile(r"\b(prime minister|chancellor|head of government)\b", re.I)
_CD = re.compile(
    r"\b(minister\s+(for|of)\s+foreign|secretary of state|foreign minister)\b",
    re.I,
)
_ID_SPEECH = re.compile(r"^M_(\d+)$", re.I)


def language_label(code: str) -> str:
    raw = (code or "").strip()
    if not raw or raw.lower() in {"und", "unknown"}:
        return ""
    lowered = raw.lower()
    if lowered in LANGUAGE_NAMES:
        return LANGUAGE_NAMES[lowered]
    if lowered in LANGUAGE_NAMES.values():
        return lowered
    return lowered


def transformation_for(source: str, *, translated: bool = False) -> str:
    if translated or source == "pdf_other":
        return "translate"
    if source in {"audio_en", "video"}:
        return "whisper"
    return "none"


def infer_speaker_level(rank: str, slug: str = "") -> str:
    if slug in INSTITUTIONAL_LEVELS:
        return INSTITUTIONAL_LEVELS[slug]
    text = rank or ""
    if _PGA.search(text):
        return "PGA"
    if _SG.search(text):
        return "SG"
    if _HS.search(text):
        return "HS"
    if _HG.search(text):
        return "HG"
    if _CD.search(text):
        return "CD"
    return ""


def infer_pronouns_gender(title: str) -> tuple[str, str]:
    text = f" {title or ''} ".lower()
    if re.search(r"\bher excellency\b|\bher majesty\b|\bh\.e\. she\b", text):
        return "she/her", "female"
    if re.search(r"\bhis excellency\b|\bhis majesty\b|\bh\.e\. he\b", text):
        return "he/him", "male"
    stripped = (title or "").strip().lower()
    if stripped.startswith("her "):
        return "she/her", "female"
    if stripped.startswith("his "):
        return "he/him", "male"
    return "", ""


_PROTOCOL_INDEX: ProtocolIndex | None = None


def protocol_index() -> ProtocolIndex:
    global _PROTOCOL_INDEX
    if _PROTOCOL_INDEX is None:
        _PROTOCOL_INDEX = load_protocol_index()
    return _PROTOCOL_INDEX


def clear_protocol_index() -> None:
    global _PROTOCOL_INDEX
    _PROTOCOL_INDEX = None


def apply_protocol(
    speech: ExtractedSpeech,
    *,
    level: str,
    pronouns: str,
    gender: str,
) -> tuple[str, str, str]:
    if speech.slug in NO_ISO_SLUGS:
        return level, pronouns, gender
    person = protocol_index().match_speaker(
        slug=speech.slug,
        country=speech.country,
        name=speech.name,
        rank=speech.rank,
    )
    if not person:
        return level, pronouns, gender
    return (
        person.level or level,
        person.pronouns or pronouns,
        person.gender or gender,
    )


def format_date_time(iso_date: str) -> str:
    parts = (iso_date or "").split("-")
    if len(parts) == 3 and all(parts):
        year, month, day = parts
        return f"{day}/{month}/{year}"
    return iso_date or ""


def speaker_name_cell(speech: ExtractedSpeech) -> str:
    lines = [part for part in (speech.speaker_title, speech.name, speech.rank) if part]
    return "\n".join(lines) if lines else speech.name


def next_speech_id(existing: list[str]) -> int:
    maximum = 0
    for raw in existing:
        match = _ID_SPEECH.match(str(raw).strip())
        if match:
            maximum = max(maximum, int(match.group(1)))
    return maximum + 1


def parse_speech_id(raw: str) -> int | None:
    match = _ID_SPEECH.match(str(raw).strip())
    return int(match.group(1)) if match else None


def format_speech_id(n: int) -> str:
    return f"M_{n}"


def normalize_speech_id(raw: str) -> str:
    num = parse_speech_id(raw)
    return format_speech_id(num) if num else ""


@dataclass
class MetadataRow:
    id_speech: str = ""
    num_of_appearance: str = ""
    date_time: str = ""
    iso_country: str = ""
    country: str = ""
    region: str = ""
    subregion: str = ""
    security_council_member: str = ""
    g20_member: str = ""
    speaker_name: str = ""
    speaker_level: str = ""
    speaker_pronouns: str = ""
    speaker_gender: str = ""
    ficha_url: str = ""
    statement_web_url: str = ""
    transcript_url: str = ""
    source: str = ""
    original_language: str = ""
    transformation: str = ""
    slug: str = ""
    speech_date: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "id_speech": self.id_speech,
            "num_of_appearance": self.num_of_appearance,
            "date_time": self.date_time,
            "iso_country": self.iso_country,
            "country": self.country,
            "region": self.region,
            "subregion": self.subregion,
            "security_council_member": self.security_council_member,
            "g20_member": self.g20_member,
            "speaker_name": self.speaker_name,
            "speaker_level": self.speaker_level,
            "speaker_pronouns": self.speaker_pronouns,
            "speaker_gender": self.speaker_gender,
            "ficha_url": self.ficha_url,
            "statement_web_url": self.statement_web_url,
            "transcript_url": self.transcript_url,
            "source": self.source,
            "original language": self.original_language,
            "transformation": self.transformation,
        }


def row_values(row: MetadataRow, headers: list[str] | None = None) -> list[str]:
    data = row.as_dict()
    cols = headers or METADATA_COLUMNS
    return [data.get(col, "") for col in cols]


def build_metadata_row(
    speech: ExtractedSpeech,
    *,
    countries: CountryIndex,
    appearance: int,
    transcript_url: str = "",
    ficha_url: str = "",
    match: CountryMatch | None = None,
) -> tuple[MetadataRow, str]:
    found = match or countries.lookup(speech.slug, speech.country)
    pronouns, gender = infer_pronouns_gender(speech.speaker_title)
    level = infer_speaker_level(speech.rank, speech.slug)
    level, pronouns, gender = apply_protocol(
        speech, level=level, pronouns=pronouns, gender=gender
    )
    country_name = found.row.country if found.row else speech.country
    row = MetadataRow(
        num_of_appearance=str(appearance),
        date_time=format_date_time(speech.speech_date),
        iso_country=found.row.iso_country if found.row else "",
        country=country_name,
        region=found.row.region if found.row else "",
        subregion=found.row.subregion if found.row else "",
        security_council_member=(
            found.row.security_council_member if found.row else ""
        ),
        g20_member=found.row.g20_member if found.row else "",
        speaker_name=speaker_name_cell(speech),
        speaker_level=level,
        speaker_pronouns=pronouns,
        speaker_gender=gender,
        ficha_url=ficha_url,
        statement_web_url=speech.source_url,
        transcript_url=transcript_url,
        source=speech.source,
        original_language=language_label(speech.original_language or speech.language),
        transformation=speech.transformation or transformation_for(speech.source),
        slug=speech.slug,
        speech_date=speech.speech_date,
    )
    return row, found.warning


def duplicate_key(ficha_url: str, slug: str, date_time: str) -> str:
    if ficha_url:
        return ficha_url.strip()
    return f"{slug}|{date_time}"
