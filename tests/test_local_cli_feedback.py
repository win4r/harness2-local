# SPDX-License-Identifier: Apache-2.0
"""Verify CLI preflight boundaries and immutable notes without a model service."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from harness2.cli import demo, run_sweep
from harness2.demo import DemoAdapter


class LocalFeedbackCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.adapter = DemoAdapter(self.root / "demo")
        self.notes = self.root / "notes"
        self.notes.mkdir()
        self.argv = ["--bench", "jb", "--k", "1", "--feedback-dir", str(self.notes)]

    def test_invalid_unmatched_note_fails_before_preflight_or_proposer(self):
        (self.notes / "unmatched.md").write_bytes(b"\xff")
        with (
            patch.object(run_sweep.C, "make_adapter", return_value=self.adapter),
            patch("harness2.preflight.require_ready") as ready,
            patch.object(run_sweep, "Proposer") as proposer,
            contextlib.redirect_stderr(io.StringIO()) as error,
        ):
            self.assertEqual(run_sweep.main(self.argv), 1)
        self.assertIn("UTF-8", error.getvalue())
        ready.assert_not_called()
        proposer.assert_not_called()

    def test_preflight_file_mutation_does_not_change_running_snapshot(self):
        note = self.notes / "ledger-1.md"
        note.write_text("Initial reviewed instructions")
        captured = []

        def run(*args, **kwargs):
            captured.append(kwargs["feedback_snapshot"].text("ledger-1"))
            return {"domains": [{"domain": "accounting", "done": 2, "failed": 0}]}

        with (
            patch.object(run_sweep.C, "make_adapter", return_value=self.adapter),
            patch(
                "harness2.preflight.require_ready",
                side_effect=lambda *a, **k: note.write_text("Changed later"),
            ),
            patch.object(run_sweep, "Proposer", return_value=Mock()),
            patch.object(run_sweep.schedule, "run_sweep", side_effect=run),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(run_sweep.main(self.argv), 0)
        self.assertEqual(captured, ["Initial reviewed instructions"])

    def test_cli_identity_can_enable_feedback_but_cannot_silently_remove_it(self):
        args = run_sweep._build_parser().parse_args(["--bench", "jb", "--k", "1"])
        namespace = self.root / "identity"
        run_sweep._check_identity(self.adapter, args, namespace)
        args.feedback_dir = self.notes
        run_sweep._check_identity(self.adapter, args, namespace)
        self.assertTrue(json.loads((namespace / "config.json").read_text())["feedback"])
        args.feedback_dir = None
        with self.assertRaisesRegex(ValueError, "human feedback"):
            run_sweep._check_identity(self.adapter, args, namespace)

    def test_demo_reports_invalid_feedback_as_clean_error(self):
        (self.notes / "ledger-1.md").write_bytes(b"\xff")
        with contextlib.redirect_stderr(io.StringIO()) as error:
            code = demo.main(
                ["--output", str(self.root / "other-demo"), "--feedback-dir", str(self.notes)]
            )
        self.assertEqual(code, 1)
        self.assertIn("UTF-8", error.getvalue())
        self.assertNotIn("Traceback", error.getvalue())
