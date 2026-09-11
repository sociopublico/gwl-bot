from __future__ import annotations

import json
import re
import shutil
import subprocess
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path

from pipeline.config import PIPELINE_ROOT
from pipeline.countries import _ALIAS_PAIRS, slugify, weak_slug
from pipeline.http import fetch

PROTOCOL_URL = (
    "https://www.un.org/dgacm/sites/www.un.org.dgacm/files/"
    "Documents_Protocol/hspmfmlist_0.pdf"
)
PROTOCOL_DIR = PIPELINE_ROOT / "data" / "protocol"
PROTOCOL_JSON = PROTOCOL_DIR / "speakers.json"

_ZW = dict.fromkeys(map(ord, "\u2060\u200b\ufeff"), None)
_HONOR = re.compile(
    r"^(His|Her|Son|Sa|Su)\s+(Excellency|Excellence|Majesty|Majestad|Highness|Holiness|Eminence)\s+",
    re.I,
)
_TITLE_PREFIX = re.compile(
    r"^(Excelentísim[oa]s?\s+(?:Señora?s?|Señores)|Monsieur|Madame|Monseigneur|"
    r"Mr\.|Ms\.|Mrs\.|Sir|Dame|Don)\s+",
    re.I,
)
_HONORS_TAIL = re.compile(
    r",?\s+(GCMG|KGN|KStJ|AC|MP|GCB|GCL|GNZM|QSO|ON|PC|JP|KC|SC|FB|"
    r"C\.G\.H\.|E\.G\.H\.).*$"
)
_HONORIFIC_ONLY = re.compile(
    r"^(His|Her|Son|Sa|Su)\s+(Excellency|Excellence|Majesty|Highness|Holiness|Eminence)$",
    re.I,
)
_PERSON_START = re.compile(
    r"(?:"
    r"His\s+Excellency|Her\s+Excellency|His\s+Majesty|Her\s+Majesty|"
    r"His\s+Highness|Her\s+Highness|His\s+Holiness|His\s+Eminence|"
    r"Son\s+Excellence|Sa\s+Majesté|Su\s+Majestad|"
    r"Excelentísima\s+Señora|Excelentísimo\s+Señor|Excelentísimos\s+Señores|"
    r"No\s+Prime\s+Minister|Same\s+as\s+Head\s+of\s+State|Same\s+as\s+Prime\s+Minister|"
    r"Same\s+as\s+PM\b|\bnone\b|"
    r"Madame|Monsieur|Monseigneur|"
    r"\bKing\s+(?=[A-ZÀ-Ÿ])|\bQueen\s+(?=[A-ZÀ-Ÿ])|\bPope\s+(?=[A-ZÀ-Ÿ])|"
    r"\bCardinal\s+(?=[A-ZÀ-Ÿ])|\bArchbishop\s+(?=[A-ZÀ-Ÿ])|"
    r"Mr\.|Ms\.|Mrs\.|Dame\s+(?=[A-ZÀ-Ÿ])|Sir\s+(?=[A-ZÀ-Ÿ])"
    r")",
    re.I,
)
_NEXT_PERSON = re.compile(
    r"\s+(?="
    r"(?:His|Her)\s+(?:Excellency|Majesty|Highness|Holiness|Eminence)\b"
    r"|Son\s+Excellence\b|Sa\s+Majesté\b|Su\s+Majestad\b"
    r"|Excelentísim"
    r")"
)
_SAME_HS = re.compile(r"same as head", re.I)
_SAME_HG = re.compile(r"same as prime", re.I)
_NO_PM = re.compile(r"no prime minister", re.I)
_HEADER = re.compile(r"HEAD OF STATE")
_LEVELS = ("HS", "HG", "CD")


@dataclass(frozen=True)
class ProtocolPerson:
    level: str
    name: str
    gender: str
    pronouns: str
    honorific: str = ""


@dataclass
class ProtocolCountry:
    country: str
    people: list[ProtocolPerson]

    def by_level(self, level: str) -> ProtocolPerson | None:
        matches = [person for person in self.people if person.level == level]
        if not matches:
            return None
        return matches[-1]


def _clean(text: str) -> str:
    text = (text or "").translate(_ZW)
    return re.sub(r"[ \t]+", " ", text).strip(" ,")


def _gender_from_blob(blob: str) -> tuple[str, str]:
    low = f" {blob.lower()} "
    if re.search(
        r" her excellency | her majesty | her highness | excelentísima |"
        r" madame | ms\.| mrs\.| dame ",
        low,
    ):
        return "female", "she/her"
    if re.search(
        r" his excellency | his majesty | his highness | his holiness |"
        r" his eminence | su majestad | excelentísimo | monsieur | mr\.| sir | king |"
        r" pope | cardinal |\bdon ",
        low,
    ):
        return "male", "he/him"
    if "son excellence" in low and " madame " in low:
        return "female", "she/her"
    if "son excellence" in low:
        return "male", "he/him"
    return "", ""


def _parse_person(blob: str, level: str) -> ProtocolPerson | None:
    blob = _clean(blob)
    if not blob:
        return None
    low = blob.lower()
    if _NO_PM.search(low) or low in {"none", "null", "...", "…"}:
        return None
    if "pending upcoming" in low:
        return None
    if _SAME_HS.search(low):
        return ProtocolPerson(level=level, name="__same_as_hs__", gender="", pronouns="")
    if _SAME_HG.search(low) or re.search(r"same as pm\b", low):
        return ProtocolPerson(level=level, name="__same_as_hg__", gender="", pronouns="")
    gender, pronouns = _gender_from_blob(blob)
    honorific = ""
    hm = re.match(
        r"^((?:His|Her|Son|Sa|Su)\s+\S+|Excelentísim[oa]s?\s+Señora?s?)",
        blob,
        re.I,
    )
    if hm:
        honorific = hm.group(1).strip()
    name = _HONOR.sub("", blob)
    name = _TITLE_PREFIX.sub("", name)
    name = _HONORS_TAIL.sub("", name)
    name = re.sub(r"\s+", " ", name).strip(" ,")
    if len(name) < 3 or _HONORIFIC_ONLY.match(name):
        return None
    if name.lower() in {"excellency", "excellence", "majesty"}:
        return None
    return ProtocolPerson(
        level=level,
        name=name,
        gender=gender,
        pronouns=pronouns,
        honorific=honorific,
    )


def _split_people(blob: str) -> list[str]:
    blob = _clean(blob)
    if not blob:
        return []
    parts = [part.strip(" ,") for part in _NEXT_PERSON.split(blob) if part.strip(" ,")]
    return parts or [blob]


_COUNTRY_TAIL = re.compile(
    r"^(AND|THE|OF|REPUBLIC|STATES|FEDERATED|BOLIVARIAN|PLURINATIONAL|ISLANDS?|KINGDOM)$",
    re.I,
)


def _country_key(text: str) -> str:
    return re.sub(r"[()]", " ", _clean(text)).strip()


def _is_country_name(text: str) -> bool:
    raw = _country_key(text)
    if not raw or re.search(r"[\d,.]", raw):
        return False
    if re.search(r"UNITED NATIONS|PUBLIC LIST|PROTOCOL|HEADS OF", raw):
        return False
    letters = [c for c in raw if c.isalpha()]
    if len(letters) < 4:
        return False
    return all(c.isupper() for c in letters)


def _is_country_tail(text: str) -> bool:
    raw = _country_key(text)
    return bool(raw) and bool(_COUNTRY_TAIL.fullmatch(raw))


def _line_pieces(line: str) -> list[tuple[int, str]]:
    pieces: list[tuple[int, str]] = []
    for match in re.finditer(r"\S+(?:[ \t]\S+)*", line):
        x, tok = match.start(), match.group()
        starts = [m.start() for m in _PERSON_START.finditer(tok)]
        if starts:
            prefix = tok[: starts[0]].strip()
            if prefix:
                pieces.append((x, prefix))
            for i, start in enumerate(starts):
                end = starts[i + 1] if i + 1 < len(starts) else len(tok)
                blob = tok[start:end].strip()
                if blob:
                    pieces.append((x + start, blob))
        elif tok:
            pieces.append((x, tok))
    return pieces


def _header_anchors(line: str) -> tuple[int, int, int]:
    hs = line.find("HEAD OF STATE")
    hg = line.find("HEAD OF GOVERNMENT")
    mfa = line.find("MINISTER")
    if hs < 0 or hg < 0 or mfa < 0:
        return (32, 64, 96)
    return (hs, hg, mfa)


def _starts(line: str) -> list[int]:
    return [m.start() for m in _PERSON_START.finditer(line)]


def _nearest(x: int, anchors: tuple[int, int, int]) -> int:
    return min(range(3), key=lambda i: abs(x - anchors[i]))


def _refine_anchors(line: str, anchors: tuple[int, int, int]) -> tuple[int, int, int]:
    starts = _starts(line)
    if len(starts) < 2:
        return anchors
    refined = list(anchors)
    for x in starts:
        refined[_nearest(x, anchors)] = x
    return (refined[0], refined[1], refined[2])


def _split_line(line: str, anchors: tuple[int, int, int]) -> tuple[str, list[str]]:
    cols = ["", "", ""]
    country = ""
    for x, tok in _line_pieces(line):
        if _is_country_name(tok) or _is_country_tail(tok):
            country = f"{country} {tok}".strip()
            continue
        j = _nearest(x, anchors)
        cols[j] = f"{cols[j]} {tok}".strip()
    return country, cols


def _people_from_cols(cols: list[str]) -> list[ProtocolPerson]:
    grouped: dict[str, list[ProtocolPerson]] = {level: [] for level in _LEVELS}
    sentinels: dict[str, str] = {}
    for level, blob in zip(_LEVELS, cols):
        for piece in _split_people(blob):
            person = _parse_person(piece, level)
            if not person:
                continue
            if person.name in {"__same_as_hs__", "__same_as_hg__"}:
                sentinels[level] = person.name
                continue
            grouped[level].append(person)

    people: list[ProtocolPerson] = []
    people.extend(grouped["HS"])
    if sentinels.get("HG") == "__same_as_hs__" and grouped["HS"]:
        hs = grouped["HS"][0]
        people.append(
            ProtocolPerson(
                level="HG",
                name=hs.name,
                gender=hs.gender,
                pronouns=hs.pronouns,
                honorific=hs.honorific,
            )
        )
    else:
        people.extend(grouped["HG"])
    if sentinels.get("CD") == "__same_as_hg__":
        hg = next((p for p in people if p.level == "HG"), None)
        if hg:
            people.append(
                ProtocolPerson(
                    level="CD",
                    name=hg.name,
                    gender=hg.gender,
                    pronouns=hg.pronouns,
                    honorific=hg.honorific,
                )
            )
    elif sentinels.get("CD") == "__same_as_hs__" and grouped["HS"]:
        hs = grouped["HS"][0]
        people.append(
            ProtocolPerson(
                level="CD",
                name=hs.name,
                gender=hs.gender,
                pronouns=hs.pronouns,
                honorific=hs.honorific,
            )
        )
    else:
        people.extend(grouped["CD"])
    return people


def parse_protocol_layout(text: str) -> list[ProtocolCountry]:
    lines = [ln.replace("\x0c", "").translate(_ZW) for ln in text.splitlines()]
    blocks: list[tuple[tuple[int, int, int], list[str]]] = []
    current: list[str] = []
    anchors = (32, 64, 96)
    seen_header = False
    for line in lines:
        if _HEADER.search(line) and "COUNTRY" in line:
            if current:
                blocks.append((anchors, current))
            current = []
            anchors = _header_anchors(line)
            seen_header = True
            continue
        if not seen_header:
            continue
        if re.search(r"date of appointment", line, re.I):
            continue
        if line.strip():
            current.append(line)
    if current:
        blocks.append((anchors, current))

    countries: list[ProtocolCountry] = []
    for header_anchors, body in blocks:
        name_lines: list[str] = []
        title_lines: list[str] = []
        in_title = False
        for line in body:
            if re.search(r"full title", line, re.I):
                in_title = True
                title_lines.append(line)
                continue
            if in_title:
                title_lines.append(line)
            else:
                name_lines.append(line)
        if not name_lines:
            continue

        anchors = header_anchors
        for line in name_lines:
            if len(_starts(line)) >= 2:
                anchors = _refine_anchors(line, anchors)
                break

        country_parts: list[str] = []
        cols = ["", "", ""]
        for line in name_lines:
            bit, pieces = _split_line(line, anchors)
            if bit:
                country_parts.append(bit)
            for i, piece in enumerate(pieces):
                if piece:
                    cols[i] = f"{cols[i]} {piece}".strip()
        for line in title_lines:
            for marker in re.finditer(
                r"Same as Head of State|Same as Prime Minister|Same as PM|No Prime Minister",
                line,
                re.I,
            ):
                j = _nearest(marker.start(), anchors)
                cols[j] = f"{cols[j]} {marker.group()}".strip()

        country = _clean(" ".join(country_parts))
        country = re.sub(r"\s+", " ", country)
        if not _is_country_name(country):
            continue
        people = _people_from_cols(cols)
        if people:
            countries.append(ProtocolCountry(country=country, people=people))
    return countries


def layout_from_pdf(pdf: Path) -> str:
    binary = shutil.which("pdftotext")
    if not binary:
        raise RuntimeError(
            "Hace falta pdftotext (poppler-utils) para parsear el PDF de protocolo"
        )
    proc = subprocess.run(
        [binary, "-layout", str(pdf), "-"],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"pdftotext falló: {err or proc.returncode}")
    return proc.stdout


def download_protocol_pdf(dest: Path, *, user_agent: str) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    _, _, body = fetch(PROTOCOL_URL, user_agent=user_agent, timeout=60)
    dest.write_bytes(body)
    return dest


def _name_tokens(name: str) -> set[str]:
    folded = unicodedata.normalize("NFKD", name or "")
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch)).lower()
    parts = re.findall(r"[a-z0-9]+", folded)
    stop = {"mr", "ms", "mrs", "sir", "dr", "the", "of", "van", "der", "de", "da", "do", "dame"}
    return {p for p in parts if p not in stop and len(p) > 1}


def _score_names(left: str, right: str) -> float:
    a, b = _name_tokens(left), _name_tokens(right)
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / min(len(a), len(b))


_RANK_LEVEL = (
    (re.compile(r"\b(governor-?general|governor general)\b", re.I), "HS"),
    (re.compile(r"\b(president|king|queen|amir|emir|pope|emperor|sultan)\b", re.I), "HS"),
    (re.compile(r"\b(prime minister|chancellor|head of government)\b", re.I), "HG"),
    (
        re.compile(
            r"\b(minister\s+(for|of)\s+foreign|secretary of state|foreign minister|"
            r"cabinet secretary for foreign)\b",
            re.I,
        ),
        "CD",
    ),
)


def rank_to_level(rank: str) -> str:
    for pattern, level in _RANK_LEVEL:
        if pattern.search(rank or ""):
            return level
    return ""


class ProtocolIndex:
    def __init__(self, countries: list[ProtocolCountry]) -> None:
        self.countries = countries
        self._by_key: dict[str, ProtocolCountry] = {}
        for item in countries:
            for key in (slugify(item.country), weak_slug(item.country)):
                if key:
                    self._by_key.setdefault(key, item)
            collapsed = slugify(item.country).replace("-d-", "-d")
            if collapsed:
                self._by_key.setdefault(collapsed, item)
        for left, right in _ALIAS_PAIRS:
            if left in self._by_key:
                self._by_key.setdefault(right, self._by_key[left])
            if right in self._by_key:
                self._by_key.setdefault(left, self._by_key[right])

    def lookup_country(self, slug: str, country_name: str = "") -> ProtocolCountry | None:
        for key in (
            slug,
            slugify(slug),
            weak_slug(slug),
            slugify(country_name),
            weak_slug(country_name),
            slugify(slug).replace("-d-", "-d"),
            slugify(country_name).replace("-d-", "-d"),
        ):
            if key and key in self._by_key:
                return self._by_key[key]
        return None

    def match_speaker(
        self,
        *,
        slug: str,
        country: str,
        name: str,
        rank: str,
    ) -> ProtocolPerson | None:
        item = self.lookup_country(slug, country)
        if not item:
            return None
        best: ProtocolPerson | None = None
        best_score = 0.0
        for person in item.people:
            score = _score_names(name, person.name)
            if score > best_score:
                best, best_score = person, score
        if best and best_score >= 0.5:
            return best
        level = rank_to_level(rank)
        if level:
            return item.by_level(level)
        return None


def index_from_records(records: list[dict]) -> ProtocolIndex:
    countries: list[ProtocolCountry] = []
    for raw in records:
        people = [
            ProtocolPerson(
                level=str(p.get("level") or ""),
                name=str(p.get("name") or ""),
                gender=str(p.get("gender") or ""),
                pronouns=str(p.get("pronouns") or ""),
                honorific=str(p.get("honorific") or ""),
            )
            for p in (raw.get("people") or [])
        ]
        countries.append(ProtocolCountry(country=str(raw.get("country") or ""), people=people))
    return ProtocolIndex(countries)


def load_protocol_index(path: Path | None = None) -> ProtocolIndex:
    json_path = path or PROTOCOL_JSON
    if not json_path.is_file():
        return ProtocolIndex([])
    records = json.loads(json_path.read_text(encoding="utf-8"))
    return index_from_records(records)


def write_protocol_index(countries: list[ProtocolCountry], path: Path | None = None) -> Path:
    json_path = path or PROTOCOL_JSON
    json_path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "country": item.country,
            "people": [asdict(person) for person in item.people],
        }
        for item in countries
    ]
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return json_path


def refresh_protocol(*, user_agent: str, dest_pdf: Path | None = None) -> tuple[Path, int]:
    pdf = dest_pdf or (PROTOCOL_DIR / "hspmfmlist.pdf")
    download_protocol_pdf(pdf, user_agent=user_agent)
    countries = parse_protocol_layout(layout_from_pdf(pdf))
    path = write_protocol_index(countries)
    return path, len(countries)
