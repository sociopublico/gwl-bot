from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pipeline.cascade import SourceUnavailable
from pipeline.extract_audio import transcribe_audio_file


class WhisperWorkerTest(unittest.TestCase):
    def test_sigkill_137_becomes_source_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "80_AO_EN.mp3"
            audio.write_bytes(b"fake-mp3")
            with patch(
                "pipeline.extract_audio.subprocess.run",
                return_value=SimpleNamespace(returncode=137),
            ):
                with self.assertRaises(SourceUnavailable) as ctx:
                    transcribe_audio_file(audio, model_name="tiny")
        self.assertIn("137", str(ctx.exception))
        self.assertIn("OOM", str(ctx.exception))

    def test_worker_nonzero_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "clip.mp3"
            audio.write_bytes(b"x")
            with patch(
                "pipeline.extract_audio.subprocess.run",
                return_value=SimpleNamespace(returncode=2),
            ):
                with self.assertRaises(SourceUnavailable) as ctx:
                    transcribe_audio_file(audio)
        self.assertIn("exit 2", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
