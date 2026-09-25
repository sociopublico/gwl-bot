from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html import unescape

from pipeline.config import SessionConfig
from pipeline.countries import (
    LISTING_SLUG_ALIASES,
    NO_ISO_SLUGS,
    slugify,
    weak_slug,
)
from pipeline.http import HttpError, fetch
from pipeline.models import FileRef, SpeakerPage

_SESSION_TAIL = re.compile(
    r"\s*[—–-]\s*\d+(?:st|nd|rd|th)\s+session.*$",
    re.I,
)


def strip_tags(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", unescape(raw))
    return re.sub(r"\s+", " ", text).strip()


def _field(html: str, name: str) -> str:
    match = re.search(
        rf'field--name-{re.escape(name)}[\s\S]{{0,800}}?<div class="field__item"[^>]*>([\s\S]*?)</div>',
        html,
    )
    return strip_tags(match.group(1)) if match else ""


def _abs(config: SessionConfig, href: str) -> str:
    href = href.strip()
    if href.startswith("http"):
        return href
    return config.gadebate_base + (href if href.startswith("/") else "/" + href)


def _pdfs_from_html(config: SessionConfig, html: str) -> list[FileRef]:
    match = re.search(
        r"Full statement([\s\S]*?)(?:<h3>Audio</h3>|<h3>Photo</h3>|Right of reply)",
        html,
        re.I,
    )
    chunk = match.group(1) if match else ""
    pdfs: list[FileRef] = []
    for lab, href in re.findall(
        r'custom-statement-file-label">([\s\S]*?)</div>[\s\S]*?href="([^"]+\.pdf)"',
        chunk,
        re.I,
    ):
        url = _abs(config, href)
        pdfs.append(FileRef(label=strip_tags(lab), url=url, filename=url.rsplit("/", 1)[-1]))
    if not pdfs:
        for href in re.findall(r'href="([^"]+\.pdf)"', chunk, re.I):
            url = _abs(config, href)
            pdfs.append(FileRef(label="", url=url, filename=url.rsplit("/", 1)[-1]))
    return pdfs


def _audios_from_html(html: str) -> list[FileRef]:
    seen: dict[str, FileRef] = {}
    for href, lang, label in re.findall(
        r'<option value="\s*(https://[^"]+\.mp3)"[^>]*data-language-code="([^"]*)"[^>]*>\s*([^<]+)</option>',
        html,
        re.I,
    ):
        url = href.strip()
        seen[url] = FileRef(
            label=strip_tags(label).lower(),
            url=url,
            filename=url.rsplit("/", 1)[-1],
            lang=lang.lower(),
        )
    return list(seen.values())


def _name_from_html(html: str) -> str:
    match = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    if match:
        raw = match.group(1).replace("&amp;", "&")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {}
        desc = str(data.get("description") or "")
        name = _SESSION_TAIL.sub("", desc).strip()
        name = re.sub(r"^(His|Her)\s+Excellency\s+", "", name, flags=re.I).strip()
        if name:
            return name
    alt = re.search(r'alt="Portrait of (?:His|Her) Excellency ([^"(]+)', html)
    return alt.group(1).strip() if alt else ""


def _pick_pdfs(pdfs: list[FileRef], orig_hint: str) -> tuple[FileRef | None, FileRef | None]:
    en = next(
        (
            p
            for p in pdfs
            if "english" in p.label.lower() or p.filename.lower().endswith("_en.pdf")
        ),
        None,
    )
    hint = orig_hint.lower().strip()
    orig = None
    if hint:
        orig = next(
            (p for p in pdfs if p.filename.lower() == hint or hint in p.filename.lower()),
            None,
        )
    if orig is None:
        orig = next((p for p in pdfs if "as delivered" in p.label.lower()), None)
    if orig is None:
        orig = next(
            (
                p
                for p in pdfs
                if p is not en and not p.filename.lower().endswith("_en.pdf")
            ),
            None,
        )
    if orig is en:
        orig = None
    return en, orig


def _pick_audios(audios: list[FileRef]) -> tuple[FileRef | None, FileRef | None]:
    en = next(
        (
            a
            for a in audios
            if a.lang == "en" or a.filename.upper().endswith("_EN.mp3")
        ),
        None,
    )
    floor = next(
        (
            a
            for a in audios
            if a.filename.upper().endswith("_FL.mp3") or a.label == "original language"
        ),
        None,
    )
    return en, floor


_TRANSCRIPT_PREPARE = re.compile(
    r'(?:href|data-prepare-url)="([^"]+/transcript/([^/]+)/prepare-download)"',
    re.I,
)


def _transcript_from_html(
    config: SessionConfig, html: str, slug: str
) -> FileRef | None:
    english: FileRef | None = None
    other: FileRef | None = None
    for href, lang in _TRANSCRIPT_PREPARE.findall(html):
        code = (lang or "").strip().lower() or "en"
        url = _abs(config, href)
        ref = FileRef(
            label="transcript ai",
            url=url,
            filename=f"{config.id}-{slug}-{code}-transcript.txt",
            lang=code,
        )
        if code == "en":
            english = ref
        elif other is None:
            other = ref
    return english or other


def parse_speaker_page(config: SessionConfig, url: str, html: str) -> SpeakerPage:
    slug = url.rstrip("/").split("/")[-1]
    title = ""
    t = re.search(r"<title>([^<]+)</title>", html, re.I)
    if t:
        title = strip_tags(t.group(1)).split("|")[0].strip()
    country = _field(html, "field-speaker-list") or title
    pdfs = _pdfs_from_html(config, html)
    audios = _audios_from_html(html)
    pdf_en, pdf_other = _pick_pdfs(pdfs, _field(html, "field-language-neutral-statement"))
    audio_en, audio_floor = _pick_audios(audios)
    transcript_ai = _transcript_from_html(config, html, slug)
    datetime_attr = ""
    d = re.search(r'datetime="([^"]+)"', html)
    if d:
        datetime_attr = d.group(1)
    kaltura = None
    km = re.search(r'data-entryid="([^"]+)"', html)
    if km:
        kaltura = km.group(1)
    partner = None
    pm = re.search(r'data-partnerid="([^"]+)"', html, re.I)
    if pm:
        partner = pm.group(1)
    else:
        pm = re.search(r"kaltura\.com/p/(\d+)", html, re.I)
        if pm:
            partner = pm.group(1)
    return SpeakerPage(
        slug=slug,
        url=url,
        country=country,
        name=_name_from_html(html),
        rank=_field(html, "field-speaker-function-2") or _field(html, "field-speaker-name"),
        speaker_title=_field(html, "field-speaker-title"),
        speech_date=datetime_attr[:10] if datetime_attr else "",
        pdfs=pdfs,
        audios=audios,
        pdf_en=pdf_en,
        pdf_other=pdf_other,
        audio_en=audio_en,
        audio_floor=audio_floor,
        transcript_ai=transcript_ai,
        video_entry_id=kaltura,
        video_partner_id=partner,
    )


def scrape_speaker(config: SessionConfig, slug: str) -> SpeakerPage:
    url = config.speaker_url(slug)
    try:
        status, _, body = fetch(url, user_agent=config.user_agent)
        html = body.decode("utf-8", "replace")
        page = parse_speaker_page(config, url, html)
        page.http_status = status
        if _ficha_sin_contenido(page):
            title = ""
            t = re.search(r"<title>([^<]+)</title>", html, re.I)
            if t:
                title = strip_tags(t.group(1))[:80]
            page.error = (
                f"ficha vacía HTTP {status} {len(body)} bytes "
                f"title={title!r} body={html[:200]!r}"
            )
        return page
    except HttpError as exc:
        return SpeakerPage(
            slug=slug,
            url=url,
            country="",
            name="",
            rank="",
            speaker_title="",
            speech_date="",
            error=str(exc),
            http_status=exc.status,
        )


def _ficha_sin_contenido(page: SpeakerPage) -> bool:
    return not (
        page.pdfs
        or page.audios
        or page.video_entry_id
        or page.speech_date
        or page.pdf_en
        or page.pdf_other
        or page.audio_en
        or page.transcript_ai
    )


def slugs_from_archive_html(config: SessionConfig, html: str) -> list[str]:
    prefix = f"/en/{config.id}/"
    found = []
    seen: set[str] = set()
    for href in re.findall(r'href="([^"]+)"', html):
        if prefix not in href:
            continue
        slug = href.split(prefix, 1)[-1].strip("/").split("?")[0]
        if not slug or slug in seen or "/" in slug:
            continue
        seen.add(slug)
        found.append(slug)
    return found


def refresh_slugs_from_archive(config: SessionConfig) -> list[str]:
    _, _, body = fetch(config.archive_url(), user_agent=config.user_agent)
    html = body.decode("utf-8", "replace")
    slugs = slugs_from_archive_html(config, html)
    if not slugs:
        raise RuntimeError(
            f"El archive no devolvió fichas /en/{config.id}/ "
            f"(el sitio a veces está en mantenimiento). "
            f"Usá el slug_file de {config.name}."
        )
    return slugs


@dataclass(frozen=True)
class ListingSpeaker:
    part: str
    title: str
    name: str
    slug: str


_HOMEPAGE_TITLE_RE = re.compile(
    r'views-field-field-speaker-title">([\s\S]*?)</span>[\s\S]{0,1500}?speaker-info">([\s\S]*?)</span>',
    re.I,
)
_HOMEPAGE_DATE_RE = re.compile(r'"settingsDate"\s*:\s*"(\d{4}-\d{2}-\d{2})"')
_SPEAKER_HREF_RE = re.compile(r"/en/\d+/([a-z0-9-]+)", re.I)


def _view_chunk(html: str, display_id: str) -> str:
    marker = f"view-display-id-{display_id}"
    start = html.find(marker)
    if start < 0:
        return ""
    rest = html[start:]
    nxt = rest.find("view-display-id-block_", 16)
    return rest if nxt < 0 else rest[:nxt]


def _clean_listing_title(raw: str) -> str:
    return re.sub(r"^\d+\.\s*", "", strip_tags(raw)).strip()


def _name_from_listing_info(raw: str) -> str:
    before = unescape(raw).split("<br", 1)[0]
    name = strip_tags(before)
    name = re.sub(r"^(His|Her)\s+Excellency\s+", "", name, flags=re.I)
    name = re.sub(
        r"^(His|Her)\s+(Royal\s+Highness|Highness|Majesty)\s+",
        "",
        name,
        flags=re.I,
    )
    return name.strip()


def build_slug_catalog(slugs: list[str]) -> dict[str, str]:
    catalog: dict[str, str] = {}
    for slug in slugs:
        slug = (slug or "").strip()
        if not slug:
            continue
        for key in (slug, slugify(slug), weak_slug(slug)):
            if key:
                catalog.setdefault(key, slug)
    # _ALIAS_PAIRS es solo para ISO (nauru↔naoero). No reescribir el slug de la
    # URL: gadebate 81 usa /naoero; 80 usaba /nauru.
    for alias, slug in LISTING_SLUG_ALIASES.items():
        catalog.setdefault(alias, slug)
    return catalog


def slug_catalog_for(config: SessionConfig) -> dict[str, str]:
    from pipeline.config import load_slugs

    slugs = list(NO_ISO_SLUGS)
    slugs.extend(load_slugs(config))
    extra = config.root / "data" / "unga80-slugs.txt"
    if extra.is_file() and extra.resolve() != config.slug_file.resolve():
        for line in extra.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            slugs.append(line)
    return build_slug_catalog(slugs)


def slug_from_speaker_title(title: str, catalog: dict[str, str] | None = None) -> str:
    cleaned = _clean_listing_title(title)
    if not cleaned:
        return ""
    keys = [slugify(cleaned), weak_slug(cleaned)]
    if catalog:
        for key in keys:
            if key and key in catalog:
                return catalog[key]
    return next((key for key in keys if key), "")


def listings_from_homepage_html(
    html: str,
    catalog: dict[str, str] | None = None,
) -> tuple[str, list[ListingSpeaker]]:
    listed_day = ""
    match = _HOMEPAGE_DATE_RE.search(html)
    if match:
        listed_day = match.group(1)
    speakers: list[ListingSpeaker] = []
    seen: set[str] = set()
    for display, part in (
        ("block_morning_session", "morning"),
        ("block_afternoon_session", "afternoon"),
    ):
        chunk = _view_chunk(html, display)
        if not chunk:
            continue
        for raw_title, raw_info in _HOMEPAGE_TITLE_RE.findall(chunk):
            title = _clean_listing_title(raw_title)
            href = _SPEAKER_HREF_RE.search(raw_title)
            slug = href.group(1) if href else slug_from_speaker_title(title, catalog)
            if not slug or slug in seen:
                continue
            seen.add(slug)
            speakers.append(
                ListingSpeaker(
                    part=part,
                    title=title,
                    name=_name_from_listing_info(raw_info),
                    slug=slug,
                )
            )
    return listed_day, speakers


def fetch_homepage_listings(
    config: SessionConfig,
    day: str | None = None,
) -> list[ListingSpeaker]:
    _, _, body = fetch(config.homepage_url(), user_agent=config.user_agent)
    html = body.decode("utf-8", "replace")
    listed_day, speakers = listings_from_homepage_html(html, slug_catalog_for(config))
    if day and listed_day and listed_day != day:
        raise RuntimeError(
            f"gadebate /en muestra el Daily schedule de {listed_day}, no {day}"
        )
    if not speakers:
        raise RuntimeError(
            "gadebate /en no listó Morning/Afternoon Session "
            f"(día {listed_day or day or '?'})"
        )
    return speakers
