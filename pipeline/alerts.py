from __future__ import annotations

import json
import tomllib
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.detector import detect_keywords
from app.notifier import mark_sent, select_events_for_alert
from pipeline.config import PIPELINE_ROOT, SessionConfig, load_date_index

DEFAULT_KEYWORDS_PATH = PIPELINE_ROOT / "data" / "alert-keywords.toml"


@dataclass(frozen=True)
class KeywordGroup:
    name: str
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class AlertSimConfig:
    groups: tuple[KeywordGroup, ...]
    cooldown_seconds: float = 120.0
    chunk_seconds: float = 20.0
    chunk_overlap_seconds: float = 4.0
    words_per_minute: float = 140.0
    speech_gap_seconds: float = 90.0

    @property
    def keywords(self) -> tuple[str, ...]:
        seen: list[str] = []
        folded: set[str] = set()
        for group in self.groups:
            for keyword in group.keywords:
                key = keyword.casefold()
                if key in folded:
                    continue
                folded.add(key)
                seen.append(keyword)
        return tuple(seen)

    def group_for(self, keyword: str) -> str | None:
        key = keyword.casefold()
        for group in self.groups:
            if any(item.casefold() == key for item in group.keywords):
                return group.name
        return None


@dataclass(frozen=True)
class SpeechRecord:
    path: Path
    session_id: int
    slug: str
    country: str
    speaker: str
    title: str
    date: str
    source: str
    language: str
    text: str


@dataclass(frozen=True)
class SimulatedEmail:
    day: str
    slug: str
    country: str
    speaker: str
    keywords: tuple[str, ...]
    t_seconds: float


@dataclass(frozen=True)
class DayStats:
    day: str
    speeches: int
    expected_speeches: int
    emails: int
    hits: int
    projected_emails: float | None
    sample_ok: bool


@dataclass
class AlertReport:
    session_id: int
    english_only: bool
    config: AlertSimConfig
    speeches: tuple[SpeechRecord, ...]
    skipped_non_english: int
    emails: tuple[SimulatedEmail, ...]
    days: tuple[DayStats, ...]
    hits_by_keyword: dict[str, int]
    emails_by_keyword: dict[str, int]
    emails_by_keyword_day: dict[str, dict[str, int]]
    emails_by_group: dict[str, int]
    hits: int
    emails_per_speech: float


def load_alert_config(path: Path | None = None) -> AlertSimConfig:
    target = path or DEFAULT_KEYWORDS_PATH
    with target.open("rb") as fh:
        data = tomllib.load(fh)
    groups: list[KeywordGroup] = []
    for raw in data.get("group") or []:
        name = str(raw.get("name") or "").strip()
        keywords = tuple(
            str(item).strip() for item in (raw.get("keywords") or []) if str(item).strip()
        )
        if not name or not keywords:
            raise ValueError(f"grupo inválido en {target}: hace falta name y keywords")
        groups.append(KeywordGroup(name=name, keywords=keywords))
    if not groups:
        raise ValueError(f"{target} no tiene [[group]] con keywords")
    overlap = float(data.get("chunk_overlap_seconds") or 4)
    chunk = float(data.get("chunk_seconds") or 20)
    if overlap < 0 or overlap >= chunk:
        raise ValueError("chunk_overlap_seconds debe ser >= 0 y menor que chunk_seconds")
    wpm = float(data.get("words_per_minute") or 140)
    if wpm <= 0:
        raise ValueError("words_per_minute debe ser > 0")
    return AlertSimConfig(
        groups=tuple(groups),
        cooldown_seconds=float(data.get("cooldown_seconds") or 120),
        chunk_seconds=chunk,
        chunk_overlap_seconds=overlap,
        words_per_minute=wpm,
        speech_gap_seconds=float(data.get("speech_gap_seconds") or 90),
    )


def with_excluded_keywords(config: AlertSimConfig, excluded: tuple[str, ...]) -> AlertSimConfig:
    drop = {item.casefold() for item in excluded if item.strip()}
    if not drop:
        return config
    groups = []
    for group in config.groups:
        kept = tuple(k for k in group.keywords if k.casefold() not in drop)
        if kept:
            groups.append(KeywordGroup(name=group.name, keywords=kept))
    if not groups:
        raise ValueError("después de --exclude no queda ninguna keyword")
    return AlertSimConfig(
        groups=tuple(groups),
        cooldown_seconds=config.cooldown_seconds,
        chunk_seconds=config.chunk_seconds,
        chunk_overlap_seconds=config.chunk_overlap_seconds,
        words_per_minute=config.words_per_minute,
        speech_gap_seconds=config.speech_gap_seconds,
    )


def parse_speech_txt(path: Path) -> SpeechRecord:
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---"):
        raise ValueError(f"{path} no tiene frontmatter ---")
    rest = raw[3:]
    end = rest.find("\n---")
    if end < 0:
        raise ValueError(f"{path} frontmatter sin cierre")
    header: dict[str, str] = {}
    for line in rest[:end].splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        header[key.strip()] = value.strip()
    body = rest[end + 4 :].strip()
    date = header.get("date") or path.parent.name
    return SpeechRecord(
        path=path,
        session_id=int(header.get("session") or 0),
        slug=header.get("slug") or path.stem,
        country=header.get("country") or "",
        speaker=header.get("speaker") or "",
        title=header.get("title") or "",
        date=date[:10],
        source=header.get("source") or "",
        language=(header.get("language") or "und").lower(),
        text=body,
    )


def load_speeches(
    root: Path,
    session_id: int,
    *,
    english_only: bool = True,
) -> tuple[tuple[SpeechRecord, ...], int]:
    session_dir = root / str(session_id)
    if not session_dir.is_dir():
        return (), 0
    speeches: list[SpeechRecord] = []
    skipped = 0
    for path in sorted(session_dir.glob("*/*.txt")):
        record = parse_speech_txt(path)
        if english_only and record.language != "en":
            skipped += 1
            continue
        speeches.append(record)
    speeches.sort(key=lambda item: (item.date, item.slug))
    return tuple(speeches), skipped


def _chunk_sizes(config: AlertSimConfig) -> tuple[int, int, float]:
    words_per_chunk = max(1, round(config.words_per_minute * config.chunk_seconds / 60.0))
    overlap_words = min(
        words_per_chunk - 1,
        max(0, round(config.words_per_minute * config.chunk_overlap_seconds / 60.0)),
    )
    hop = max(1, words_per_chunk - overlap_words)
    hop_seconds = hop / config.words_per_minute * 60.0
    return words_per_chunk, hop, hop_seconds


def iter_chunks(text: str, words_per_chunk: int, hop: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    chunks: list[str] = []
    index = 0
    while index < len(words):
        chunks.append(" ".join(words[index : index + words_per_chunk]))
        if index + words_per_chunk >= len(words):
            break
        index += hop
    return chunks


def simulate_alerts(
    speeches: tuple[SpeechRecord, ...],
    config: AlertSimConfig,
    *,
    expected_by_day: dict[str, int] | None = None,
    session_id: int = 80,
    english_only: bool = True,
    skipped_non_english: int = 0,
) -> AlertReport:
    words_per_chunk, hop, hop_seconds = _chunk_sizes(config)
    keywords = config.keywords
    emails: list[SimulatedEmail] = []
    hits_by_keyword: Counter[str] = Counter()
    emails_by_keyword: Counter[str] = Counter()
    emails_by_keyword_day: dict[str, Counter[str]] = defaultdict(Counter)
    emails_by_group: Counter[str] = Counter()
    hits = 0
    emails_by_day: Counter[str] = Counter()
    hits_by_day: Counter[str] = Counter()
    speeches_by_day: Counter[str] = Counter()

    by_day: dict[str, list[SpeechRecord]] = defaultdict(list)
    for speech in speeches:
        by_day[speech.date].append(speech)
        speeches_by_day[speech.date] += 1

    stamp = datetime(2025, 1, 1, tzinfo=timezone.utc)
    for day in sorted(by_day):
        last_sent: dict[str, float] = {}
        now = 0.0
        for speech in by_day[day]:
            for chunk in iter_chunks(speech.text, words_per_chunk, hop):
                events = detect_keywords(
                    chunk,
                    keywords,
                    context_words=8,
                    timestamp=stamp,
                    speaker=speech.speaker,
                )
                if events:
                    hits += len(events)
                    hits_by_day[day] += len(events)
                    for event in events:
                        hits_by_keyword[event.keyword] += 1
                    selected = select_events_for_alert(
                        events,
                        last_sent,
                        config.cooldown_seconds,
                        now,
                    )
                    if selected:
                        chosen = tuple(dict.fromkeys(event.keyword for event in selected))
                        emails.append(
                            SimulatedEmail(
                                day=day,
                                slug=speech.slug,
                                country=speech.country,
                                speaker=speech.speaker,
                                keywords=chosen,
                                t_seconds=now,
                            )
                        )
                        emails_by_day[day] += 1
                        seen_groups: set[str] = set()
                        for keyword in chosen:
                            emails_by_keyword[keyword] += 1
                            emails_by_keyword_day[keyword][day] += 1
                            group = config.group_for(keyword)
                            if group and group not in seen_groups:
                                emails_by_group[group] += 1
                                seen_groups.add(group)
                        mark_sent(last_sent, selected, now)
                now += hop_seconds
            now += config.speech_gap_seconds

    expected = expected_by_day or {}
    rate = (len(emails) / len(speeches)) if speeches else 0.0
    days: list[DayStats] = []
    for day in sorted(set(speeches_by_day) | set(expected)):
        analyzed = speeches_by_day[day]
        roster = expected.get(day, 0)
        observed = emails_by_day[day]
        coverage = (analyzed / roster) if roster else 0.0
        sample_ok = analyzed >= 8 or coverage >= 0.4
        projected = None
        if roster and rate:
            if sample_ok and analyzed:
                projected = observed * (roster / analyzed)
            else:
                projected = rate * roster
        elif analyzed and roster:
            projected = observed * (roster / analyzed)
        days.append(
            DayStats(
                day=day,
                speeches=analyzed,
                expected_speeches=roster,
                emails=observed,
                hits=hits_by_day[day],
                projected_emails=projected,
                sample_ok=sample_ok,
            )
        )

    return AlertReport(
        session_id=session_id,
        english_only=english_only,
        config=config,
        speeches=speeches,
        skipped_non_english=skipped_non_english,
        emails=tuple(emails),
        days=tuple(days),
        hits_by_keyword=dict(hits_by_keyword),
        emails_by_keyword=dict(emails_by_keyword),
        emails_by_keyword_day={
            keyword: dict(days) for keyword, days in emails_by_keyword_day.items()
        },
        emails_by_group=dict(emails_by_group),
        hits=hits,
        emails_per_speech=rate,
    )


def expected_speeches_by_day(config: SessionConfig) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for day in load_date_index(config).values():
        counts[day] += 1
    return dict(counts)


def _fmt_num(value: float | int | None) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return str(int(round(value)))
    return str(value)


def _median(values: list[float]) -> float:
    xs = sorted(values)
    n = len(xs)
    mid = n // 2
    if n % 2:
        return float(xs[mid])
    return (xs[mid - 1] + xs[mid]) / 2.0


def _day_label(day: str) -> str:
    if len(day) >= 10:
        return f"{day[8:10]}/{day[5:7]}"
    return day


def keyword_day_matrix(
    report: AlertReport,
) -> tuple[list[str], list[tuple[str, list[int], int]]]:
    days = [day.day for day in report.days]
    rows: list[tuple[str, list[int], int]] = []
    for keyword in report.config.keywords:
        per_day = report.emails_by_keyword_day.get(keyword, {})
        counts = [int(per_day.get(day, 0)) for day in days]
        rows.append((keyword, counts, sum(counts)))
    return days, rows


def format_keyword_day_table(report: AlertReport) -> str:
    days, rows = keyword_day_matrix(report)
    labels = [_day_label(day) for day in days]
    keyword_width = max(len("keyword"), *(len(name) for name, _, _ in rows), 8)
    col = 7
    header = f"  {'keyword':<{keyword_width}}" + "".join(f"{label:>{col}}" for label in labels)
    header += f"{'total':>{col}}"
    lines = [
        "Mails por keyword y día (después del cooldown; una celda = mails que mencionan esa keyword)",
        header,
    ]
    for name, counts, total in rows:
        line = f"  {name:<{keyword_width}}" + "".join(f"{n:>{col}}" for n in counts)
        line += f"{total:>{col}}"
        lines.append(line)
    unique = [day.emails for day in report.days]
    footer = f"  {'mails únicos':<{keyword_width}}" + "".join(f"{n:>{col}}" for n in unique)
    footer += f"{sum(unique):>{col}}"
    lines.append(footer)
    lines.append(
        "  La fila 'mails únicos' cuenta 1 mail aunque traiga varias keywords. "
        "Por eso no es la suma de las filas."
    )
    return "\n".join(lines)


def format_report(report: AlertReport, *, without_reform: AlertReport | None = None) -> str:
    lines: list[str] = []
    analyzed = len(report.speeches)
    expected_total = sum(day.expected_speeches for day in report.days)
    lang = "solo inglés (el vivo se transcribe en EN)" if report.english_only else "todos los idiomas"
    lines.append(f"Simulación de alertas UNGA {report.session_id}")
    lines.append(
        f"Corpus: {analyzed} discursos analizados"
        + (f" / {expected_total} fichas del roster" if expected_total else "")
        + f". Idioma: {lang}."
    )
    if report.skipped_non_english:
        lines.append(
            f"Omitidos por no estar en inglés: {report.skipped_non_english} "
            "(el bot en vivo usa interpretación EN; esos no contarían igual)."
        )
    lines.append("")
    lines.append("Cómo se cuenta un mail")
    lines.append(
        f"  • 1 mail por ventana de ~{report.config.chunk_seconds:.0f} s "
        "si entra una keyword que no está en cooldown"
    )
    lines.append(
        f"  • la misma keyword no vuelve a mandar mail hasta "
        f"{report.config.cooldown_seconds:.0f} s"
    )
    lines.append("  • varias keywords en la misma ventana = 1 solo mail")
    lines.append(
        f"  • el texto se parte a {report.config.words_per_minute:.0f} palabras/minuto "
        "(aproximación; no hay timestamps reales)"
    )
    lines.append("")
    lines.append("Mails por día")
    lines.append(
        f"  {'día':<12} {'discursos':>12} {'mails':>8} {'proyección*':>12} {'hits':>8}"
    )
    observed_days = [day for day in report.days if day.speeches]
    for day in report.days:
        roster = (
            f"{day.speeches}/{day.expected_speeches}"
            if day.expected_speeches
            else str(day.speeches)
        )
        note = "" if day.sample_ok or not day.expected_speeches else " (muestra chica)"
        lines.append(
            f"  {day.day:<12} {roster:>12} {day.emails:>8} "
            f"{_fmt_num(day.projected_emails):>12} {day.hits:>8}{note}"
        )
    lines.append(
        "  * muestra suficiente ese día: escala los mails observados; "
        "si no, tasa global × oradores del roster."
    )

    if report.speeches:
        projected = [
            day.projected_emails
            for day in report.days
            if day.projected_emails is not None
        ]
        lines.append("")
        lines.append("Resumen para el equipo")
        lines.append(
            f"  Tasa: {report.emails_per_speech:.1f} mails por discurso "
            f"({len(report.emails)} mails / {len(report.speeches)} discursos)"
        )
        if projected:
            lines.append(
                f"  Proyección por día de debate: {_fmt_num(min(projected))}–"
                f"{_fmt_num(max(projected))} mails "
                f"(mediana {_fmt_num(_median(projected))})"
            )
        sampled = [float(day.emails) for day in observed_days if day.sample_ok]
        if sampled:
            lines.append(
                f"  Días con muestra ok: {_fmt_num(min(sampled))}–"
                f"{_fmt_num(max(sampled))} mails observados"
            )
        lines.append(f"  Hits crudos (sin cooldown): {report.hits}")

    lines.append("")
    lines.append(format_keyword_day_table(report))

    if without_reform is not None:
        lines.append("")
        lines.append('Si sacan la keyword suelta "reform" (dejan "un reform")')
        lines.append(
            f"  tasa {without_reform.emails_per_speech:.1f} mails/discurso "
            f"(antes {report.emails_per_speech:.1f})"
        )
        for day, other in zip(report.days, without_reform.days, strict=False):
            left = _fmt_num(day.projected_emails)
            right = _fmt_num(other.projected_emails)
            if left == "—" and right == "—":
                continue
            lines.append(f"  {day.day}: {left} → {right} mails/día")

    lines.append("")
    lines.append("Por grupo (mails que incluyen al menos una keyword del grupo)")
    for group in report.config.groups:
        lines.append(f"  {group.name:<20} {report.emails_by_group.get(group.name, 0):>6}")

    lines.append("")
    lines.append("Por keyword (hits crudos / mails que la mencionan)")
    for keyword in report.config.keywords:
        hits = report.hits_by_keyword.get(keyword, 0)
        mails = report.emails_by_keyword.get(keyword, 0)
        lines.append(f"  {keyword:<22} hits={hits:<5} mails={mails}")
    return "\n".join(lines) + "\n"


def report_to_json(report: AlertReport) -> dict:
    return {
        "session_id": report.session_id,
        "english_only": report.english_only,
        "speeches": len(report.speeches),
        "skipped_non_english": report.skipped_non_english,
        "hits": report.hits,
        "emails": len(report.emails),
        "emails_per_speech": report.emails_per_speech,
        "days": [
            {
                "day": day.day,
                "speeches": day.speeches,
                "expected_speeches": day.expected_speeches,
                "emails": day.emails,
                "hits": day.hits,
                "projected_emails": day.projected_emails,
                "sample_ok": day.sample_ok,
            }
            for day in report.days
        ],
        "hits_by_keyword": report.hits_by_keyword,
        "emails_by_keyword": report.emails_by_keyword,
        "emails_by_keyword_day": report.emails_by_keyword_day,
        "emails_by_group": report.emails_by_group,
        "sample_emails": [
            {
                "day": item.day,
                "slug": item.slug,
                "country": item.country,
                "speaker": item.speaker,
                "keywords": list(item.keywords),
            }
            for item in report.emails[:50]
        ],
    }


def run_alert_simulation(
    session: SessionConfig,
    *,
    keywords_path: Path | None = None,
    dest: Path | None = None,
    english_only: bool = True,
    exclude: tuple[str, ...] = (),
) -> AlertReport:
    sim = load_alert_config(keywords_path)
    if exclude:
        sim = with_excluded_keywords(sim, exclude)
    root = dest or (session.root / "out")
    speeches, skipped = load_speeches(root, session.id, english_only=english_only)
    return simulate_alerts(
        speeches,
        sim,
        expected_by_day=expected_speeches_by_day(session),
        session_id=session.id,
        english_only=english_only,
        skipped_non_english=skipped,
    )


def print_alert_report(
    session: SessionConfig,
    *,
    keywords_path: Path | None = None,
    dest: Path | None = None,
    english_only: bool = True,
    as_json: bool = False,
) -> AlertReport:
    report = run_alert_simulation(
        session,
        keywords_path=keywords_path,
        dest=dest,
        english_only=english_only,
    )
    without_reform = None
    if any(k.casefold() == "reform" for k in report.config.keywords):
        without_reform = run_alert_simulation(
            session,
            keywords_path=keywords_path,
            dest=dest,
            english_only=english_only,
            exclude=("reform",),
        )
    if as_json:
        payload = report_to_json(report)
        if without_reform is not None:
            payload["without_standalone_reform"] = report_to_json(without_reform)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(format_report(report, without_reform=without_reform), end="")
    return report
