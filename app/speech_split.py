"""Separa un discurso guardado en logs/speeches.json en varios oradores.

Sirve cuando el tracker no detectó un cambio y las citas quedaron en `unknown`
o en el orador anterior. Cada cita y cada línea va al último corte cuyo inicio
(segundos de video) sea <= su tiempo. Por defecto solo muestra el reparto.

  docker compose exec monitor python -m app.speech_split --from unknown --list-runs
  docker compose exec monitor python -m app.speech_split --from unknown --run last --list-intros
  docker compose exec monitor python -m app.speech_split --from unknown --run last \\
      --since 2026-09-25T12:52:00Z \\
      --cut "1106.59=Taneti Maamau|Kiribati" --cut "2185.62=Hussain Mohamed Latheef|Maldives"
  (lo mismo + --apply para escribir)
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from app.detector import DetectionEvent
from app.speaker import has_introduction_cue
from app.speech_store import SpeechStore, StoredSpeech, _find_name

# Un salto hacia atrás mayor a esto es un reinicio del monitor (el origen vuelve a 0).
RUN_RESET_SECONDS = 300.0
_THANK_RE = re.compile(r"\bwish\s+(?:to\s+)?(?:the\s+)?thank\b", re.IGNORECASE)

Line = tuple[float, float, str]


@dataclass(frozen=True)
class Cut:
    start: float
    name: str
    country: str | None = None
    title: str | None = None


@dataclass
class Share:
    cut: Cut
    lines: list[Line] = field(default_factory=list)
    quotes: list[DetectionEvent] = field(default_factory=list)


@dataclass
class SplitPlan:
    source: str
    shares: list[Share]
    kept_lines: int
    kept_quotes: list[DetectionEvent]
    skipped_quotes: list[DetectionEvent]
    run_range: tuple[float, float] | None
    moved_line_indexes: set[int] = field(default_factory=set)


def parse_cut(raw: str) -> Cut:
    """`1106.59=Taneti Maamau|Kiribati|President` (país y cargo opcionales)."""
    if "=" not in raw:
        raise ValueError(f"corte inválido (falta '='): {raw!r}")
    seconds_raw, rest = raw.split("=", 1)
    try:
        start = float(seconds_raw.strip())
    except ValueError as exc:
        raise ValueError(f"corte inválido (segundos): {raw!r}") from exc
    parts = [part.strip() for part in rest.split("|")]
    name = parts[0] if parts else ""
    if not name:
        raise ValueError(f"corte inválido (sin nombre): {raw!r}")
    country = parts[1] if len(parts) > 1 and parts[1] else None
    title = parts[2] if len(parts) > 2 and parts[2] else None
    return Cut(start=start, name=name, country=country, title=title)


def line_runs(lines: Sequence[Line]) -> list[tuple[int, int]]:
    """Tramos [inicio, fin) de líneas con la misma base de tiempo."""
    if not lines:
        return []
    runs: list[tuple[int, int]] = []
    begin = 0
    for index in range(1, len(lines)):
        if lines[index][0] < lines[index - 1][0] - RUN_RESET_SECONDS:
            runs.append((begin, index))
            begin = index
    runs.append((begin, len(lines)))
    return runs


def _cut_for(seconds: float | None, cuts: Sequence[Cut]) -> Cut | None:
    if seconds is None:
        return None
    chosen: Cut | None = None
    for cut in cuts:
        if cut.start <= seconds:
            chosen = cut
    return chosen


def run_bounds(lines: Sequence[Line], run: int | None) -> tuple[int, int]:
    """None = todas las líneas; si no, el tramo `run` (acepta negativos: -1 = último)."""
    if run is None or not lines:
        return 0, len(lines)
    runs = line_runs(lines)
    try:
        return runs[run]
    except IndexError as exc:
        raise ValueError(f"no existe el tramo {run}; hay {len(runs)}") from exc


def plan_split(
    speech: StoredSpeech,
    cuts: Sequence[Cut],
    *,
    run: int | None = None,
    since: datetime | None = None,
) -> SplitPlan:
    ordered = sorted(cuts, key=lambda cut: cut.start)
    source_key = speech.name.casefold()
    shares: dict[str, Share] = {}
    for cut in ordered:
        if cut.name.casefold() != source_key:
            shares.setdefault(cut.name.casefold(), Share(cut=cut))

    first, end = run_bounds(speech.lines, run)
    run_range = (speech.lines[first][0], speech.lines[end - 1][1]) if end > first else None

    kept_lines = first + (len(speech.lines) - end)
    moved: set[int] = set()
    for index in range(first, end):
        line = speech.lines[index]
        cut = _cut_for(line[0], ordered)
        if cut is None or cut.name.casefold() == source_key:
            kept_lines += 1
            continue
        shares[cut.name.casefold()].lines.append(line)
        moved.add(index)

    kept_quotes: list[DetectionEvent] = []
    skipped: list[DetectionEvent] = []
    for event in speech.quotes:
        if since is not None and event.timestamp < since:
            skipped.append(event)
            continue
        cut = _cut_for(event.video_seconds, ordered)
        if cut is None or cut.name.casefold() == source_key:
            kept_quotes.append(event)
            continue
        shares[cut.name.casefold()].quotes.append(event)

    return SplitPlan(
        source=speech.name,
        shares=list(shares.values()),
        kept_lines=kept_lines,
        kept_quotes=kept_quotes,
        skipped_quotes=skipped,
        run_range=run_range,
        moved_line_indexes=moved,
    )


def _retag(event: DetectionEvent, cut: Cut) -> DetectionEvent:
    parts = [part for part in (cut.title, cut.country) if part]
    return replace(event, speaker=cut.name, speaker_title=", ".join(parts) or None)


def apply_split(
    store: SpeechStore,
    source: str,
    cuts: Sequence[Cut],
    *,
    run: int | None = None,
    since: datetime | None = None,
) -> SplitPlan | None:
    """Mueve citas y líneas bajo el lock del archivo. None si no existe el origen."""
    result: list[SplitPlan | None] = [None]

    def mutate(speeches: list[StoredSpeech]) -> None:
        speech = _find_name(speeches, source)
        if speech is None:
            return
        plan = plan_split(speech, cuts, run=run, since=since)
        result[0] = plan
        moved_quotes = {id(event) for share in plan.shares for event in share.quotes}
        speech.lines = [
            line
            for index, line in enumerate(speech.lines)
            if index not in plan.moved_line_indexes
        ]
        speech.quotes = [event for event in speech.quotes if id(event) not in moved_quotes]
        for share in plan.shares:
            if not share.lines and not share.quotes:
                continue
            target = _find_name(speeches, share.cut.name)
            if target is None:
                target = StoredSpeech(
                    name=share.cut.name,
                    country=share.cut.country,
                    title=share.cut.title,
                )
                speeches.append(target)
            else:
                target.country = share.cut.country or target.country
                target.title = share.cut.title or target.title
            target.lines.extend(share.lines)
            target.quotes.extend(_retag(event, share.cut) for event in share.quotes)

    store._update(mutate)
    return result[0]


def _clip(text: str, limit: int = 150) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[: limit - 3] + "..."


def list_intros(speech: StoredSpeech, *, run: int | None) -> list[Line]:
    first, end = run_bounds(speech.lines, run)
    return [
        line
        for line in speech.lines[first:end]
        if has_introduction_cue(line[2]) or _THANK_RE.search(line[2])
    ]


def _quote_line(event: DetectionEvent) -> str:
    seconds = f"{event.video_seconds:.2f}" if event.video_seconds is not None else "-"
    stamp = event.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"    {stamp}  t={seconds:>9}  {event.keyword:<16} {_clip(event.context, 90)}"


def format_plan(plan: SplitPlan, existing: Sequence[StoredSpeech]) -> str:
    out: list[str] = [f"Origen: {plan.source}"]
    if plan.run_range is not None:
        out.append(f"Tramo de líneas: {plan.run_range[0]:.2f} a {plan.run_range[1]:.2f}")
    for share in plan.shares:
        cut = share.cut
        where = ", ".join(part for part in (cut.country, cut.title) if part) or "-"
        span = ""
        if share.lines:
            span = f" ({share.lines[0][0]:.2f} a {share.lines[-1][1]:.2f})"
        out.append(
            f"-> {cut.name} | {where} | desde {cut.start:.2f} | "
            f"{len(share.lines)} líneas{span} | {len(share.quotes)} citas"
        )
        current = _find_name(list(existing), cut.name)
        if current is not None:
            out.append(
                f"    ya existe con {len(current.quotes)} citas y {len(current.lines)} líneas; se suman"
            )
        out.extend(_quote_line(event) for event in share.quotes)
    out.append(
        f"Queda en {plan.source}: {plan.kept_lines} líneas, {len(plan.kept_quotes)} citas"
    )
    out.extend(_quote_line(event) for event in plan.kept_quotes)
    if plan.skipped_quotes:
        out.append(f"Citas anteriores a --since (no se tocan): {len(plan.skipped_quotes)}")
    return "\n".join(out)


def _parse_since(raw: str) -> datetime | None:
    if not raw:
        return None
    stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def _parse_run(raw: str) -> int | None:
    value = raw.strip().casefold()
    if value == "all":
        return None
    if value == "last":
        return -1
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"--run debe ser all, last o un número: {raw!r}") from exc


def format_runs(speech: StoredSpeech) -> str:
    out: list[str] = []
    for index, (first, end) in enumerate(line_runs(speech.lines)):
        lines = speech.lines[first:end]
        out.append(
            f"tramo {index}: {len(lines)} líneas | {lines[0][0]:.2f} a {lines[-1][1]:.2f} | "
            f"{_clip(lines[0][2], 60)}"
        )
    return "\n".join(out) or "sin líneas"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Separar un discurso guardado por orador")
    parser.add_argument("--from", dest="source", required=True, help="nombre del discurso origen")
    parser.add_argument(
        "--cut",
        action="append",
        default=[],
        help="SEG=Nombre|País|Cargo; repetible. Usar el nombre de origen para dejar un tramo ahí",
    )
    parser.add_argument(
        "--run",
        default="all",
        help="all, last (último tramo tras un reinicio) o el número de --list-runs (-2 = anteúltimo)",
    )
    parser.add_argument("--since", default="", help="solo citas desde este instante UTC (ISO)")
    parser.add_argument("--list-runs", action="store_true", help="mostrar los tramos de líneas")
    parser.add_argument("--list-intros", action="store_true", help="mostrar intros y agradecimientos")
    parser.add_argument("--apply", action="store_true", help="escribir los cambios")
    parser.add_argument("--log-dir", default="", help="default: LOG_DIR del monitor")
    args = parser.parse_args(argv)

    log_dir = args.log_dir
    if not log_dir:
        from app.config import Config

        log_dir = Config.from_env().log_dir
    store = SpeechStore(log_dir)
    speeches = store.list_speeches()
    speech = _find_name(speeches, args.source)
    if speech is None:
        print(f"No hay discurso {args.source!r} en {store.path}.", file=sys.stderr)
        for item in speeches:
            print(f"  {item.name} | {len(item.quotes)} citas | {len(item.lines)} líneas", file=sys.stderr)
        return 1
    try:
        run = _parse_run(args.run)
        run_bounds(speech.lines, run)
        cuts = [parse_cut(raw) for raw in args.cut]
        since = _parse_since(args.since)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.list_runs:
        print(format_runs(speech))
    if args.list_intros:
        for start, _end, text in list_intros(speech, run=run):
            print(f"{start:10.2f}  {_clip(text)}")
    if not cuts:
        if args.list_runs or args.list_intros:
            return 0
        print("Indicá al menos un --cut (o --list-runs / --list-intros).", file=sys.stderr)
        return 2

    plan = plan_split(speech, cuts, run=run, since=since)
    print(format_plan(plan, speeches))
    if not args.apply:
        print("\nDry-run: no se cambió nada. Repetí el comando con --apply para escribir.")
        return 0
    applied = apply_split(store, args.source, cuts, run=run, since=since)
    if applied is None:
        print("El discurso de origen desapareció mientras tanto.", file=sys.stderr)
        return 1
    print("\nAplicado. Mandá cada mail con: python -m app.speech_mail --name \"...\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
