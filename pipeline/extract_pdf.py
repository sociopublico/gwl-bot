from __future__ import annotations

import re
from io import BytesIO

_PAGE_CHROME = re.compile(
    r"""
    ^(?:
        page\s+\d+(?:\s+of\s+\d+)?(?:\s*\|\s*.*)?
        | \d+\s*\|\s*p\s*a\s*g\s*e
        | \d+\s*/\s*\d+
        | \d{1,3}
    )$
    """,
    re.I | re.X,
)
_LETTERHEAD = re.compile(
    r"""
    ^(?:
        please\ check\ against\ delivery
        | \(please\ check\ against\ delivery\)
        | check\ against\ delivery
        | seul\ le\ prononc[eé]\ fait\ foi
        | t[eé]l[eé]charger\ le\ \.pdf
        | statement\ by\b
        | national\ statement\ by\b
        | address\ (?:by|to)\b
        | remarks\ delivered\b
        | the\ secretary-general$
        | permanent\ mission\b
        | fuente:.*
        | source:.*
        | publicado\ em\b
        | atualizado\ em\b
        | https?://
        | www\.
        | (?:tel|e-?mail|fax)\s*:
        | new\ york,?\s+(?:ny\b|\d)
        | \d{3,}\s+\w+\s+(?:avenue|street|blvd)
        | --+
    )
    """,
    re.I | re.X,
)
_SPACED_LETTERHEAD = re.compile(r"^(?:[A-Z]\s+){4,}[A-Z].*$")
_CID_NOISE = re.compile(r"/\d+")
_GREETING = re.compile(
    r"""
    ^(?:
        bismillah
        | assalam
        | wassalam
        | shalom
        | salve,?$
        | om\ (?:swasti|shanti)
        | salam\ kebajikan
        | rahayu
        | in\ the\ name\ of\ (?:god|allah)
        | au\ nom\ d
    )
    """,
    re.I | re.X,
)
_SPEECH_START = re.compile(
    r"""
    ^(?:
        -?\s*madam(?:e)?(?:\s+la)?(?:\s+pr[eé]sident[ae]?|\s+first)?
        | -?\s*mr\.?\s+(?:president|secretary)
        | -?\s*ms\.?\s+president
        | -?\s*the\s+president\s+of\s+the
        | -?\s*president\s+of\s+the\s+\d+
        | -?\s*secretary[- ]general\b
        | -?\s*distinguished\s+delegat
        | -?\s*ladies\s+and\s+gentlemen
        | -?\s*your\s+excellenc
        | -?\s*excellencies\b
        | -?\s*heads?\s+of\s+state
        | let\s+me\s+(?:begin|start)
        | it\s+is\s+(?:indeed\s+)?(?:a\s+|my\s+)?(?:great\s+)?(?:honou?r|pleasure)
        | i\s+(?:stand|congratulate|have\s+the|wish\s+to|am\s+(?:honou?red|pleased|happy)|want\s+to)
        | eighty\s+years
        | \d+\.\s+\S
        | estimad[ao]s?
        | se[nñ]hor[ae]s?\s
        | mesdames?\b
        | messieurs\b
        | monsieur\s+le
        | je\s+(?:suis|voudrais|tiens)
        | son\s+ochenta
        | -?\s*(?:his|her)\s+excellency\s+m[rs]
    )
    """,
    re.I | re.X,
)
_FINISHED = re.compile(r"""[.!?…؟۔]["'”’»)]*$""")
_NEW_BLOCK = re.compile(r"^(?:\d+\.\s|-+\s)")


def extract_pdf_text(blob: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError(
            "Falta pypdf. Instalalo con: pip install -r pipeline/requirements.txt"
        ) from exc
    reader = PdfReader(BytesIO(blob))
    parts: list[str] = []
    for page in reader.pages:
        piece = page.extract_text() or ""
        if piece.strip():
            parts.append(piece)
    # Una sola secuencia de líneas: el salto de página no es un párrafo.
    text = "\n".join(parts)
    text = "\n".join(line.rstrip() for line in text.splitlines())
    return clean_pdf_text(text)


# PGA en el podio (audio EN): "The Assembly will hear an address by His/Her Excellency…"
_ASSEMBLY_INTRO = re.compile(
    r"""
    ^\s*
    (?:the\s+)?assembly\s+will\s+(?:now\s+)?hear\s+
    (?:an\s+|and\s+)?(?:address|statement)\s+by\b
    [\s\S]{10,500}?
    i\s+request\s+(?:the\s+)?protocol\s+to\s+
    (?:escort|his\s+court|her\s+court)
    [\s\S]{0,220}?
    (?:address\s+the\s+assembly|to\s+the\s+rostrum)
    \.?\s*
    """,
    re.I | re.X,
)
_ASSEMBLY_ESCORT = re.compile(
    r"""
    ^\s*i\s+request\s+(?:the\s+)?protocol\s+to\s+
    (?:escort|his\s+court|her\s+court)
    [\s\S]{10,220}?
    (?:address\s+the\s+assembly|to\s+the\s+rostrum)
    \.?\s*
    """,
    re.I | re.X,
)
_ASSEMBLY_OUTRO = re.compile(
    r"""
    (?:(?<=[.!?])\s+|(?<=\n)|^)
    on\s+behalf\s+of\s+(?:the\s+)?(?:general\s+)?assembly,?\s+
    i\s+wish\s+to\s+thank\b
    [\s\S]*$
    """,
    re.I | re.X,
)


def strip_assembly_protocol(text: str) -> str:
    """Saca la presentación y el agradecimiento del PGA que Whisper pega al discurso."""
    cleaned = (text or "").strip()
    if not cleaned:
        return cleaned
    cleaned = _ASSEMBLY_INTRO.sub("", cleaned, count=1).strip()
    cleaned = _ASSEMBLY_ESCORT.sub("", cleaned, count=1).strip()
    cleaned = _ASSEMBLY_OUTRO.sub("", cleaned).strip()
    return cleaned


def clean_pdf_text(text: str) -> str:
    lines = [_normalize_line(line) for line in text.splitlines()]
    kept: list[str] = []
    for line in lines:
        if _is_drop_line(line):
            continue
        kept.append(line)
    body = _drop_intro(kept)
    paragraphs = _reflow(body)
    return strip_assembly_protocol("\n\n".join(paragraphs).strip())


def _normalize_line(line: str) -> str:
    line = line.replace("\xa0", " ").replace("\u2009", " ").replace("\u202f", " ")
    line = line.replace("\u00ad", "")  # soft hyphen
    return re.sub(r"[ \t]+", " ", line).strip()


def _is_cid_noise(line: str) -> bool:
    if line.count("/") >= 8 and len(_CID_NOISE.findall(line)) >= 6:
        return True
    slashes = line.count("/")
    return slashes >= 4 and re.fullmatch(r"[\d/i\s]+", line) is not None


def _mostly_caps(line: str) -> bool:
    letters = [c for c in line if c.isalpha()]
    if len(letters) < 12:
        return False
    return sum(c.isupper() for c in letters) / len(letters) > 0.85


def _is_drop_line(line: str) -> bool:
    if not line:
        return False
    if _is_cid_noise(line):
        return True
    if _PAGE_CHROME.match(line):
        return True
    if _SPACED_LETTERHEAD.match(line):
        return True
    if _LETTERHEAD.match(line):
        return True
    if _GREETING.match(line):
        return True
    if _mostly_caps(line) and len(line) < 120:
        return True
    return False


def _drop_intro(lines: list[str]) -> list[str]:
    start = None
    for idx, line in enumerate(lines):
        if not line:
            continue
        if _SPEECH_START.match(line):
            start = idx
            break
    if start is None:
        return lines
    return lines[start:]


def _should_join(prev: str, curr: str) -> bool:
    if not prev or not curr:
        return False
    if _NEW_BLOCK.match(curr):
        return False
    first = curr.lstrip()[:1]
    if not first:
        return False
    if first.islower() or first in ",;:)]—–-":
        return True
    if _FINISHED.search(prev):
        return False
    # "Excellencies," / "Ladies and Gentlemen," seguido de oración nueva
    if prev.endswith((",", ":")) and first.isupper():
        return False
    return True


def _join_fragment(prev: str, curr: str) -> str:
    if prev.endswith("-") and not prev.endswith("--"):
        return prev[:-1] + curr.lstrip()
    return prev + " " + curr


def _reflow(lines: list[str]) -> list[str]:
    paragraphs: list[str] = []
    buf = ""
    for line in lines:
        if not line:
            continue
        if buf and _should_join(buf, line):
            buf = _join_fragment(buf, line)
            continue
        if buf:
            paragraphs.append(_finish_para(buf))
        buf = line
    if buf:
        paragraphs.append(_finish_para(buf))
    return [p for p in paragraphs if p]


def _finish_para(text: str) -> str:
    text = re.sub(r" +", " ", text).strip()
    text = re.sub(r" +([,.;:!?])", r"\1", text)
    return text
