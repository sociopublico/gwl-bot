"""Corrige el orador de las keywords ya registradas (lo que ve el dashboard).

El dashboard (`pipeline alerts-site`) junta logs/keywords.jsonl, logs/highlights.log*
y los snapshots pipeline/data/alerts/<sesión>/*.json, y deduplica por orador: hay que
corregir los tres igual o el hit aparece dos veces. Cada --fix es una ventana UTC
[desde, hasta) y el orador correcto. Por defecto solo muestra qué cambiaría.

  docker compose exec monitor python -m app.keyword_fix \\
      --fix "2026-09-25T13:10:55Z..2026-09-25T13:28:37Z=Taneti Maamau|Kiribati"
  (lo mismo + --apply para escribir)
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.keyword_journal import _LINE_RE, _T_RE


@dataclass(frozen=True)
class Fix:
    start: datetime
    end: datetime
    name: str
    title: str = ""

    def covers(self, stamp: datetime) -> bool:
        return self.start <= stamp < self.end


@dataclass
class Change:
    path: Path
    stamp: datetime
    keyword: str
    old: str
    new: str


def _parse_utc(text: str) -> datetime:
    raw = text.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    stamp = datetime.fromisoformat(raw.replace(" ", "T"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def parse_fix(spec: str) -> Fix:
    window, sep, who = spec.partition("=")
    start, dots, end = window.partition("..")
    if not sep or not dots or not who.strip():
        raise ValueError(f'--fix inválido (esperado "DESDE..HASTA=Nombre|Título"): {spec}')
    name, _, title = who.partition("|")
    fix = Fix(_parse_utc(start), _parse_utc(end), name.strip(), title.strip())
    if fix.end <= fix.start:
        raise ValueError(f"--fix con HASTA <= DESDE: {spec}")
    return fix


def _fix_for(fixes: Sequence[Fix], stamp: datetime) -> Fix | None:
    for fix in fixes:
        if fix.covers(stamp):
            return fix
    return None


def _record_stamp(record: dict) -> datetime | None:
    try:
        return _parse_utc(str(record.get("timestamp") or ""))
    except ValueError:
        return None


def _fix_record(record: dict, fixes: Sequence[Fix], path: Path, changes: list[Change]) -> bool:
    stamp = _record_stamp(record)
    fix = _fix_for(fixes, stamp) if stamp else None
    if fix is None or stamp is None:
        return False
    old = str(record.get("speaker") or "")
    old_title = str(record.get("speaker_title") or "")
    if old == fix.name and (not fix.title or old_title == fix.title):
        return False
    record["speaker"] = fix.name
    if fix.title:
        record["speaker_title"] = fix.title
    changes.append(Change(path, stamp, str(record.get("keyword") or ""), old, fix.name))
    return True


def fix_jsonl(text: str, fixes: Sequence[Fix], path: Path, changes: list[Change]) -> str:
    out: list[str] = []
    for raw in text.splitlines(keepends=True):
        line = raw.strip()
        try:
            record = json.loads(line) if line else None
        except json.JSONDecodeError:
            record = None
        if isinstance(record, dict) and _fix_record(record, fixes, path, changes):
            ending = "\n" if raw.endswith("\n") else ""
            out.append(json.dumps(record, ensure_ascii=False) + ending)
        else:
            out.append(raw)
    return "".join(out)


def fix_highlights(text: str, fixes: Sequence[Fix], path: Path, changes: list[Change]) -> str:
    out: list[str] = []
    for raw in text.splitlines(keepends=True):
        match = _LINE_RE.match(raw.strip())
        if not match:
            out.append(raw)
            continue
        stamp = datetime.strptime(match.group("ts"), "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
        fix = _fix_for(fixes, stamp)
        parts = match.group("body").split(" | ")
        if fix is None or len(parts) < 2:
            out.append(raw)
            continue
        # keyword | [orador] | [t=Ns] | contexto (como lo escribe app.events)
        has_speaker = len(parts) >= 3 and not _T_RE.match(parts[1])
        old = parts[1] if has_speaker else ""
        if old == fix.name:
            out.append(raw)
            continue
        if has_speaker:
            parts[1] = fix.name
        else:
            parts.insert(1, fix.name)
        ending = "\n" if raw.endswith("\n") else ""
        out.append(f"{match.group('ts')} | KEYWORD_DETECTED | {' | '.join(parts)}{ending}")
        changes.append(Change(path, stamp, parts[0], old, fix.name))
    return "".join(out)


def fix_snapshot(text: str, fixes: Sequence[Fix], path: Path, changes: list[Change]) -> str:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text
    hits = payload.get("hits") if isinstance(payload, dict) else payload
    if not isinstance(hits, list):
        return text
    touched = False
    for record in hits:
        if isinstance(record, dict) and _fix_record(record, fixes, path, changes):
            touched = True
    if not touched:
        return text
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _rewrite_in_place(path: Path, before: str, after: str) -> None:
    # En el lugar (no os.replace): el logger del monitor tiene highlights.log abierto.
    with path.open("r+", encoding="utf-8") as fh:
        if fh.read() != before:
            raise RuntimeError(f"{path} cambió mientras se corregía; volvé a correr")
        fh.seek(0)
        fh.write(after)
        fh.truncate()


def target_files(log_dir: Path, snapshot_dir: Path | None) -> list[tuple[Path, object]]:
    files: list[tuple[Path, object]] = []
    jsonl = log_dir / "keywords.jsonl"
    if jsonl.is_file():
        files.append((jsonl, fix_jsonl))
    for path in sorted(log_dir.glob("highlights.log*")):
        if path.is_file():
            files.append((path, fix_highlights))
    if snapshot_dir is not None and snapshot_dir.is_dir():
        for path in sorted(snapshot_dir.glob("*.json")):
            files.append((path, fix_snapshot))
    return files


def run(
    fixes: Sequence[Fix],
    log_dir: Path,
    snapshot_dir: Path | None,
    *,
    apply: bool,
) -> list[Change]:
    changes: list[Change] = []
    for path, fixer in target_files(log_dir, snapshot_dir):
        before = path.read_text(encoding="utf-8", errors="replace")
        after = fixer(before, fixes, path, changes)  # type: ignore[operator]
        if apply and after != before:
            _rewrite_in_place(path, before, after)
    return changes


def format_changes(changes: Sequence[Change], fixes: Sequence[Fix]) -> str:
    lines: list[str] = []
    for fix in fixes:
        mine = [c for c in changes if fix.covers(c.stamp)]
        head = f"{fix.start:%Y-%m-%d %H:%M:%S}..{fix.end:%H:%M:%S}Z → {fix.name}"
        if fix.title:
            head += f" | {fix.title}"
        lines.append(f"{head}: {len(mine)} cambio(s)")
        for change in mine:
            old = change.old or "(sin orador)"
            lines.append(
                f"  {change.stamp:%H:%M:%S} {change.keyword:<16} {old} → {change.new}"
                f"  [{change.path.name}]"
            )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--fix",
        action="append",
        required=True,
        help='"DESDE..HASTA=Nombre|Título" en UTC, ej. 2026-09-25T13:10:55Z..2026-09-25T13:28:37Z',
    )
    parser.add_argument("--log-dir", default="logs")
    parser.add_argument(
        "--snapshots",
        default=None,
        help="carpeta de snapshots del dashboard (ej. pipeline/data/alerts/81)",
    )
    parser.add_argument("--apply", action="store_true", help="escribir (si no, solo muestra)")
    args = parser.parse_args(argv)

    try:
        fixes = [parse_fix(spec) for spec in args.fix]
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    snapshot_dir = Path(args.snapshots) if args.snapshots else None
    try:
        changes = run(fixes, Path(args.log_dir), snapshot_dir, apply=args.apply)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(format_changes(changes, fixes))
    print("Escrito." if args.apply else "Dry-run: agregá --apply para escribir.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
