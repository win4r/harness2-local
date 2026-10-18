# SPDX-License-Identifier: Apache-2.0
"""Human-feedback reporting distinguishes opt-in from selected steered tasks."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness2.cli import report
from harness2.core.judge import Scored, write


class FeedbackReportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def summary(self, task_id, modes):
        path = self.root / task_id / "summary.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"task_id": task_id, "selections": modes}), encoding="utf-8")

    def selection(self, fingerprint="note-sha", **kwargs):
        return dict(ok=True, chosen_label="agg1", feedback_fingerprint=fingerprint) | kwargs

    def test_counts_only_requested_successful_selections(self):
        self.summary("parallel", {"agg": self.selection(), "seq": self.selection("")})
        self.summary("sequential", {"seq": self.selection()})
        self.summary("failed", {"agg": self.selection(ok=False)})
        self.summary("no_pick", {"agg": self.selection(chosen_label="")})
        self.summary("empty", {"agg": self.selection("")})
        self.summary("unused", {"agg": self.selection(feedback_used=False)})
        self.assertEqual(report._feedback_tasks(self.root, ("agg",)), (False, 1))
        self.assertEqual(report._feedback_tasks(self.root, ("seq",)), (False, 1))
        self.assertEqual(report._feedback_tasks(self.root, ("agg", "seq")), (False, 2))

    def test_multiple_requested_modes_count_one_task_once(self):
        self.summary("both", {"agg": self.selection(), "seq": self.selection()})
        self.assertEqual(report._feedback_tasks(self.root, ("agg", "seq")), (False, 1))

    def test_empty_enabled_namespace_is_zero_from_either_identity_marker(self):
        self.summary("empty", {"agg": self.selection("")})
        for name in ("config.json", ".feedback.json"):
            with self.subTest(marker=name):
                path = self.root / name
                path.write_text(json.dumps({"feedback": True}), encoding="utf-8")
                self.assertEqual(report._feedback_tasks(self.root, ("agg",)), (True, 0))
                path.unlink()

    def cli_output(self):
        write(
            [
                Scored(
                    task_id="t",
                    mode="agg",
                    k=1,
                    label="agg1",
                    run_id="id",
                    chosen_is_base=False,
                    n_passed=1,
                    n_criteria=1,
                )
            ],
            self.root / "judged.json",
        )
        adapter = SimpleNamespace(
            spec=SimpleNamespace(name="jb"), substrate=SimpleNamespace(name="oc")
        )
        output = io.StringIO()
        with (
            patch.object(report.C, "make_adapter", return_value=adapter),
            patch.object(report.C, "runs_root_for", return_value=self.root.parent),
            patch.object(report.C, "summary_namespace", return_value=self.root.name),
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(report.main(["--bench", "jb", "--modes", "parallel"]), 0)
        return output.getvalue()

    def test_cli_does_not_invent_task_count_for_enabled_empty_notes(self):
        (self.root / "config.json").write_text('{"feedback": true}', encoding="utf-8")
        self.summary("t", {"agg": self.selection("")})
        output = self.cli_output()
        self.assertIn("human feedback namespace: enabled; 0 selected task(s)", output)
        self.assertNotIn("1 selected task(s)", output)
        self.assertNotIn("not directly comparable", output)

    def test_cli_warns_for_actual_feedback_but_excludes_unrequested_mode(self):
        self.summary("t", {"agg": self.selection(), "seq": self.selection()})
        self.summary("other", {"seq": self.selection()})
        output = self.cli_output()
        self.assertIn("1 selected task(s)", output)
        self.assertIn("not directly comparable", output)


if __name__ == "__main__":
    unittest.main()
