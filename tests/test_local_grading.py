# SPDX-License-Identifier: Apache-2.0
"""Local refinements to reviewed upstream PRs #12 and #45, without model calls."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

from harness2.cli import judge as judge_cli
from harness2.core import judge, schedule
from harness2.demo import DemoAdapter, DemoProposer


def scored(**kwargs):
    fields = dict(
        task_id="t",
        mode="agg",
        k=1,
        label="agg1",
        run_id="fixed-id",
        chosen_is_base=False,
        n_passed=1,
        n_criteria=1,
        selected_at="2026-10-18T00:00:00+00:00",
    )
    fields.update(kwargs)
    return judge.Scored(**fields)


class ScoreValidityTests(unittest.TestCase):
    def test_finite_integer_and_fractional_weighted_scores_remain_valid(self):
        for numerator, denominator in ((0, 2), (2, 2), (0.25, 0.5), (1.5, 2), (0, 0.5)):
            with self.subTest(numerator=numerator, denominator=denominator):
                row = scored(n_passed=numerator, n_criteria=denominator)
                self.assertTrue(row.graded)
                self.assertEqual(row.frac, numerator / denominator)

    def test_malformed_counts_are_ungraded_in_cached_and_new_scores(self):
        invalid = [
            (True, 2),
            (1, True),
            (False, 2),
            (1, False),
            (float("nan"), 2),
            (1, float("nan")),
            (float("inf"), 2),
            (1, float("inf")),
            (float("-inf"), 2),
            (1, float("-inf")),
            ("1", 2),
            (1, "2"),
            ([], 2),
            (1, {}),
            (-1, 2),
            (3, 2),
            (None, 2),
            (1, None),
            (0, 0),
            (0, -1),
        ]
        for numerator, denominator in invalid:
            with self.subTest(numerator=numerator, denominator=denominator):
                cached = scored(n_passed=numerator, n_criteria=denominator)
                self.assertFalse(cached.graded)
                self.assertIsNone(cached.frac)
                adapter = Mock()
                adapter.score.return_value = dict(
                    n_passed=numerator, n_criteria=denominator, all_pass=True
                )
                fresh = judge.judge_one(adapter, "t", "agg", 1, "agg1", "id", Path("."))
                self.assertFalse(fresh.graded)
                self.assertFalse(fresh.all_pass)
                self.assertIn("invalid score counts", fresh.error)


class SelectionCacheTests(unittest.TestCase):
    def test_legacy_or_malformed_stamp_never_preserves_an_old_grade(self):
        for stamp in ("", None, "   ", 1, False):
            with self.subTest(stamp=stamp):
                old = scored(selected_at=stamp)
                failed = replace(old, n_passed=None, n_criteria=None, error="judge unavailable")
                self.assertIs(judge_cli._merge([old], [failed])[0], failed)

    def test_missing_legacy_stamp_survives_load_but_not_failed_rejudge(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "judged.json"
            row = scored().__dict__.copy()
            row.pop("selected_at")
            path.write_text(json.dumps([row]), encoding="utf-8")
            old = judge_cli._load_rows(path)[0]
            self.assertEqual(old.selected_at, "")
            failed = replace(old, n_passed=None, n_criteria=None, error="judge unavailable")
            self.assertFalse(judge_cli._merge([old], [failed])[0].graded)

    def test_matching_nonempty_stamp_retains_grade_only_for_same_successful_selection(self):
        old = scored()
        failed = replace(old, n_passed=None, n_criteria=None, error="judge unavailable")
        self.assertIs(judge_cli._merge([old], [failed])[0], old)
        changes = [
            dict(run_id="new-id"),
            dict(selected_at="2026-10-18T00:00:01+00:00"),
            dict(error="selection did not complete successfully"),
        ]
        for change in changes:
            new = replace(failed, **change)
            self.assertIs(judge_cli._merge([old], [new])[0], new)

    def test_failed_rejudge_keeps_grade_only_for_same_selection(self):
        # Adapted from upstream PR #45: real demo execution, serialization,
        # resume, rerun, scoring and cache merge; only the external judge is stubbed.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adapter = DemoAdapter(root)
            proposer = DemoProposer(adapter.spec.channels["proposer"])

            def run_stream():
                return schedule.run_domain(
                    "accounting",
                    adapter.tasks(),
                    1,
                    adapter=adapter,
                    prop=proposer,
                    runs_root=adapter.spec.runs_root,
                    library_root=root / "library/1",
                )

            ns_dir = adapter.spec.runs_root / "demo-oc-agg1"

            def selections():
                return {s["task_id"]: s for s in judge_cli.collect_selections(ns_dir, ("agg",))}

            run_stream()
            first = selections()
            self.assertTrue(all(sel["selected_at"] for sel in first.values()))
            adapter.score = Mock(return_value={"n_passed": 1, "n_criteria": 1})
            graded = judge.judge_selections(adapter, list(first.values()))
            adapter.score = Mock(side_effect=RuntimeError("judge endpoint unavailable"))
            failed = judge.judge_selections(adapter, list(first.values()))
            self.assertTrue(all(r.graded for r in judge_cli._merge(graded, failed)))

            (Path(first["ledger-1"]["chosen_run_dir"]) / "answer.txt").unlink()
            run_stream()
            again = selections()
            self.assertEqual(again["ledger-1"]["chosen_run_id"], first["ledger-1"]["chosen_run_id"])
            self.assertNotEqual(again["ledger-1"]["selected_at"], first["ledger-1"]["selected_at"])
            self.assertEqual(again["ledger-2"]["selected_at"], first["ledger-2"]["selected_at"])
            failed = judge.judge_selections(adapter, list(again.values()))
            merged = {r.task_id: r.graded for r in judge_cli._merge(graded, failed)}
            self.assertEqual(merged, {"ledger-1": False, "ledger-2": True})


if __name__ == "__main__":
    unittest.main()
