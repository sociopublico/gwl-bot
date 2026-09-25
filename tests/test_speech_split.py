from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from app.detector import DetectionEvent
from app.speaker import Speaker
from app.speech_split import (
    Cut,
    apply_split,
    line_runs,
    main,
    parse_cut,
    plan_split,
)
from app.speech_store import SpeechStore

DAY = datetime(2026, 9, 25, 13, 0, tzinfo=timezone.utc)


def _event(keyword: str, video_seconds: float, *, minutes: int = 0) -> DetectionEvent:
    return DetectionEvent(
        timestamp=DAY + timedelta(minutes=minutes),
        keyword=keyword,
        transcript="speech",
        context=f'"...{keyword}..."',
        video_seconds=video_seconds,
    )


def _seed(tmp: str) -> SpeechStore:
    store = SpeechStore(tmp)
    unknown = Speaker()
    # Tramo viejo (otro día) y tramo nuevo tras un reinicio (origen vuelve a ~0).
    store.add_lines(unknown, [(5000.0, 5010.0, "old day"), (5010.0, 5020.0, "old day 2")])
    store.add_lines(
        unknown,
        [
            (40.0, 50.0, "filler video"),
            (1106.59, 1110.0, "The Assembly will hear an address by his excellency"),
            (1654.0, 1660.0, "multilateralism faces growing challenges"),
            (2185.62, 2190.0, "The Assembly will hear an address by Hussain Muhammad Latif"),
            (2940.0, 2945.0, "The UN-80 initiative"),
            (4596.5, 4600.0, "The Assembly will hear an address by Nawaf Salam"),
        ],
    )
    store.add(unknown, [_event("women", 5005.0, minutes=-1440)])
    store.add(unknown, [_event("multilateralism", 1654.56, minutes=20)])
    store.add(unknown, [_event("un-80", 2940.0, minutes=41)])
    return store


CUTS = [
    Cut(1106.59, "Taneti Maamau", "Kiribati"),
    Cut(2185.62, "Hussain Mohamed Latheef", "Maldives"),
    Cut(4596.5, "unknown"),
]


class ParseTest(unittest.TestCase):
    def test_parse_cut(self) -> None:
        cut = parse_cut("1106.59=Taneti Maamau|Kiribati|President")
        self.assertEqual(cut, Cut(1106.59, "Taneti Maamau", "Kiribati", "President"))
        self.assertEqual(parse_cut("0=António Costa").country, None)
        with self.assertRaises(ValueError):
            parse_cut("Taneti Maamau")
        with self.assertRaises(ValueError):
            parse_cut("abc=Taneti")

    def test_line_runs_split_on_restart(self) -> None:
        lines = [(5000.0, 5010.0, "a"), (5010.0, 5020.0, "b"), (40.0, 50.0, "c"), (60.0, 70.0, "d")]
        self.assertEqual(line_runs(lines), [(0, 2), (2, 4)])
        self.assertEqual(line_runs([]), [])


class SplitTest(unittest.TestCase):
    def test_plan_uses_last_run_and_since(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _seed(tmp)
            speech = store.list_speeches()[0]
            plan = plan_split(speech, CUTS, run=-1, since=DAY)
        by_name = {share.cut.name: share for share in plan.shares}
        self.assertEqual(set(by_name), {"Taneti Maamau", "Hussain Mohamed Latheef"})
        self.assertEqual([e.keyword for e in by_name["Taneti Maamau"].quotes], ["multilateralism"])
        self.assertEqual(len(by_name["Taneti Maamau"].lines), 2)
        self.assertEqual([e.keyword for e in by_name["Hussain Mohamed Latheef"].quotes], ["un-80"])
        # 2 del tramo viejo + filler + Nawaf (corte al propio origen).
        self.assertEqual(plan.kept_lines, 4)
        self.assertEqual(len(plan.skipped_quotes), 1)

    def test_apply_moves_and_retags_quotes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _seed(tmp)
            apply_split(store, "unknown", CUTS, run=-1, since=DAY)
            speeches = {speech.name: speech for speech in store.list_speeches()}
        self.assertEqual(len(speeches["unknown"].quotes), 1)
        self.assertEqual(len(speeches["unknown"].lines), 4)
        taneti = speeches["Taneti Maamau"]
        self.assertEqual(taneti.country, "Kiribati")
        self.assertEqual(taneti.quotes[0].speaker, "Taneti Maamau")
        self.assertEqual(taneti.quotes[0].speaker_title, "Kiribati")
        self.assertEqual(speeches["Hussain Mohamed Latheef"].quotes[0].keyword, "un-80")

    def test_apply_merges_into_existing_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _seed(tmp)
            store.add(Speaker(name="Taneti Maamau", country="Kiribati"), [_event("gender", 1700.0)])
            apply_split(store, "unknown", CUTS, run=-1, since=DAY)
            taneti = {s.name: s for s in store.list_speeches()}["Taneti Maamau"]
        self.assertEqual(sorted(e.keyword for e in taneti.quotes), ["gender", "multilateralism"])

    def test_cli_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _seed(tmp)
            before = store.path.read_text(encoding="utf-8")
            code = main(
                [
                    "--log-dir",
                    tmp,
                    "--from",
                    "unknown",
                    "--run",
                    "last",
                    "--cut",
                    "1106.59=Taneti Maamau|Kiribati",
                ]
            )
            self.assertEqual(code, 0)
            self.assertEqual(store.path.read_text(encoding="utf-8"), before)
            code = main(
                [
                    "--log-dir",
                    tmp,
                    "--from",
                    "unknown",
                    "--run",
                    "last",
                    "--since",
                    "2026-09-25T13:00:00Z",
                    "--cut",
                    "1106.59=Taneti Maamau|Kiribati",
                    "--apply",
                ]
            )
            self.assertEqual(code, 0)
            names = [speech.name for speech in store.list_speeches()]
        self.assertIn("Taneti Maamau", names)

    def test_cli_rejects_bad_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _seed(tmp)
            self.assertEqual(main(["--log-dir", tmp, "--from", "unknown", "--run", "7", "--list-runs"]), 2)
            self.assertEqual(main(["--log-dir", tmp, "--from", "nadie", "--list-runs"]), 1)


if __name__ == "__main__":
    unittest.main()
