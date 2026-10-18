# SPDX-License-Identifier: Apache-2.0
"""Human notes stay opt-in, frozen, and outside harness authoring sessions."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2.core import feedback, prompts, schedule, selection
from harness2.demo import DemoAdapter, DemoProposer


class RecordingProposer(DemoProposer):
    def __init__(self, spec):
        super().__init__(spec)
        self.records = []

    def call(self, mission, context_dir, label, **kwargs):
        root = Path(context_dir)
        note = root / "feedback.md"
        self.records.append(
            {
                "label": label,
                "mission": mission,
                "note": note.read_text() if note.exists() else "",
                "source": (root / "source/ledger.csv").read_text(),
            }
        )
        return super().call(mission, context_dir, label, **kwargs)


class LocalFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.adapter = DemoAdapter(self.root)
        self.prop = RecordingProposer(self.adapter.spec.channels["proposer"])
        self.notes = self.root / "feedback"
        self.notes.mkdir()

    def run_domain(self, *, k=1, snapshot=None):
        return schedule.run_domain(
            "accounting",
            self.adapter.tasks(),
            k,
            adapter=self.adapter,
            prop=self.prop,
            runs_root=self.adapter.spec.runs_root,
            library_root=self.root / "library",
            modes=("agg",),
            feedback_dir=self.notes if snapshot is None else None,
            feedback_snapshot=snapshot,
        )

    def test_notes_reach_only_selector_and_reflection_and_hash_actual_missions(self):
        note = "Include the negative credit before totaling."
        (self.notes / "ledger-1.md").write_text(note)
        self.assertEqual(self.run_domain(k=2)["failed"], 0)
        for record in self.prop.records:
            reviewed = record["source"].startswith("amount\n100.25") and record["label"].startswith(
                ("select.", "experience.")
            )
            self.assertEqual(record["note"], note + "\n" if reviewed else "")
            self.assertEqual("context/feedback.md" in record["mission"], reviewed)
        summary = self.adapter.spec.runs_root / "demo-oc-agg2/ledger-1/summary.json"
        sel = json.loads(summary.read_text())["selections"]["agg"]
        self.assertEqual(sel["feedback_fingerprint"], feedback.fingerprint(note))
        self.assertTrue(sel["feedback_used"])
        for prefix, key in [
            ("select.", "selector_prompt_sha256"),
            ("experience.", "experience_prompt_sha256"),
        ]:
            record = next(
                r
                for r in self.prop.records
                if r["source"].startswith("amount\n100.25") and r["label"].startswith(prefix)
            )
            self.assertEqual(sel[key], hashlib.sha256(record["mission"].encode()).hexdigest())
        counts = (self.adapter.execution_count, len(self.prop.records))
        self.assertEqual(
            [r["status"] for r in self.run_domain(k=2)["tasks"]], ["skipped", "skipped"]
        )
        self.assertEqual(counts, (self.adapter.execution_count, len(self.prop.records)))

    def test_snapshot_cannot_change_mid_stream(self):
        (self.notes / "ledger-1.md").write_text("First note.")
        later = self.notes / "ledger-2.md"
        later.write_text("Frozen second note.")
        execute = self.adapter.execute

        def mutate_after_preflight(*args, **kwargs):
            later.write_text("Changed after preflight.")
            return execute(*args, **kwargs)

        with patch.object(self.adapter, "execute", side_effect=mutate_after_preflight):
            self.assertEqual(self.run_domain()["failed"], 0)
        later_records = [
            r
            for r in self.prop.records
            if r["source"].startswith("amount\n35.50") and r["label"].startswith("experience.")
        ]
        self.assertEqual(len(later_records), 1)
        self.assertEqual(later_records[0]["note"], "Frozen second note.\n")

    def test_k1_has_no_added_selector_and_sticky_identity_reaches_direct_api(self):
        (self.notes / "ledger-1.md").write_text("Include credits.")
        self.assertEqual(self.run_domain()["failed"], 0)
        self.assertFalse(any(r["label"].startswith("select.") for r in self.prop.records))
        self.assertEqual(len(self.prop.records), 8)
        before = self.adapter.execution_count
        with self.assertRaisesRegex(ValueError, "steered by human feedback"):
            schedule.run_task(
                "ledger-1",
                1,
                adapter=self.adapter,
                prop=self.prop,
                runs_root=self.adapter.spec.runs_root,
                modes=("agg",),
            )
        self.assertEqual(before, self.adapter.execution_count)
        marker = json.loads(
            (self.adapter.spec.runs_root / "demo-oc-agg1/.feedback.json").read_text()
        )
        self.assertTrue(marker["feedback"])

    def test_empty_snapshot_does_not_change_prompts(self):
        empty = feedback.freeze(self.notes, self.adapter.tasks())
        self.assertEqual(self.run_domain(k=2, snapshot=empty)["failed"], 0)
        self.assertFalse(
            any(r["note"] or "context/feedback.md" in r["mission"] for r in self.prop.records)
        )
        summary = json.loads(
            (self.adapter.spec.runs_root / "demo-oc-agg2/ledger-1/summary.json").read_text()
        )
        self.assertFalse(summary["selections"]["agg"]["feedback_used"])
        self.assertFalse(summary["selections"]["agg"]["feedback_fingerprint"])

    def test_all_feedback_files_preflight_before_first_execution(self):
        bad = self.notes / "unmatched.md"
        cases = [b"\xff", b"x" * (feedback.MAX_FILE_BYTES + 1), b'{"pass_rate": 1}']
        for raw in cases:
            with self.subTest(raw=raw[:20]):
                bad.write_bytes(raw)
                with self.assertRaises(ValueError):
                    self.run_domain()
                self.assertEqual(self.adapter.execution_count, 0)
        bad.unlink()
        target = self.root / "outside.md"
        target.write_text("Outside")
        bad.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "regular files"):
            self.run_domain()
        self.assertEqual(self.adapter.execution_count, 0)
        bad.unlink()
        (self.notes / "nested").mkdir()
        with self.assertRaisesRegex(ValueError, "regular files"):
            self.run_domain()

    def test_root_symlinks_collisions_and_total_limit(self):
        link = self.root / "feedback-link"
        link.symlink_to(self.notes, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            feedback.freeze(link, ["task"])
        for tasks in [("team/task", "team__task"), ("Task", "task"), ("é", "e\u0301")]:
            with self.subTest(tasks=tasks), self.assertRaisesRegex(ValueError, "collision"):
                feedback.freeze(self.notes, tasks)
        (self.notes / "task.md").write_text("12345")
        (self.notes / "unmatched.md").write_text("67890")
        with (
            patch.object(feedback, "MAX_TOTAL_BYTES", 9),
            self.assertRaisesRegex(ValueError, "total bytes"),
        ):
            feedback.freeze(self.notes, ["task"])

    def test_case_match_is_exact_and_unmatched_notes_are_reported(self):
        (self.notes / "Ledger-1.md").write_text("Wrong case")
        frozen = feedback.freeze(self.notes, ["ledger-1"])
        self.assertEqual(frozen.text("ledger-1"), "")
        self.assertEqual(frozen.unmatched, ("Ledger-1.md",))
        with self.assertRaisesRegex(ValueError, "not included"):
            frozen.text("unselected-task")

    def test_feedback_prompts_invalidate_actual_prompt_cache(self):
        (self.notes / "ledger-1.md").write_text("Include credits.")
        self.assertEqual(self.run_domain(k=2)["failed"], 0)
        with patch.object(
            prompts,
            "HUMAN_FEEDBACK_SELECT",
            prompts.HUMAN_FEEDBACK_SELECT + "\nChanged selection instruction.",
        ):
            result = self.run_domain(k=2)
        self.assertEqual([r["status"] for r in result["tasks"]], ["ok", "skipped"])

    def test_direct_selection_rejects_grader_verdict_before_model_call(self):
        with self.assertRaisesRegex(ValueError, "grader-output"):
            selection.run_selection(
                "ledger-1",
                2,
                "agg",
                {},
                adapter=self.adapter,
                prop=self.prop,
                work_dir=self.root / "work",
                feedback='"pass_rate": 1',
            )
        self.assertEqual(self.prop.records, [])

    def test_candidate_id_guidance_does_not_assume_rerun_identity(self):
        self.assertIn("only the current displayed pool", prompts.HUMAN_FEEDBACK_SELECT)
        self.assertNotIn("stable across reruns", prompts.HUMAN_FEEDBACK_SELECT)
        original = selection.assign_ids(["agg1", "agg2"], task_id="ledger-1", k=2, mode="agg")
        reduced = selection.assign_ids(["agg2"], task_id="ledger-1", k=2, mode="agg")
        self.assertNotEqual(original, reduced)
