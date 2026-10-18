# SPDX-License-Identifier: Apache-2.0
"""Regression checks for requested-mode, evidence-prefix, and input cache identity."""

import json
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from harness2.core import prompts, schedule, selection
from harness2.core.proposer import ProposerError
from harness2.demo import DemoAdapter, DemoProposer


class LocalResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.adapter = DemoAdapter(self.root)
        self.prop = DemoProposer(self.adapter.spec.channels["proposer"])

    def run_task(self, *, tag="", k=1):
        return schedule.run_task(
            "ledger-1",
            k,
            adapter=self.adapter,
            prop=self.prop,
            runs_root=self.adapter.spec.runs_root,
            modes=("agg",),
            tag=tag,
        )

    def stream(self, *, k=2, modes=("agg",), tasks=None):
        return schedule.run_domain(
            "accounting",
            tasks or self.adapter.tasks(),
            k,
            adapter=self.adapter,
            prop=self.prop,
            runs_root=self.adapter.spec.runs_root,
            library_root=self.root / f"library/{k}",
            modes=modes,
        )

    def test_requested_valid_mode_skips_unrequested_failure(self):
        class BadSequential(DemoProposer):
            def call(self, mission, context_dir, label, **kwargs):
                if label == "select.seq2":
                    raise ProposerError("temporary selector failure", attempts=1)
                return super().call(mission, context_dir, label, **kwargs)

        self.prop = BadSequential(self.prop.spec)
        self.assertEqual(
            self.stream(modes=("agg", "seq"), tasks=["ledger-1"])["tasks"][0]["status"], "partial"
        )
        counts = (self.adapter.execution_count, len(self.prop._calls))
        for _ in range(2):
            self.assertEqual(self.stream(tasks=["ledger-1"])["tasks"][0]["status"], "skipped")
            self.assertEqual(counts, (self.adapter.execution_count, len(self.prop._calls)))
        self.assertEqual(
            self.stream(modes=("seq",), tasks=["ledger-1"])["tasks"][0]["status"], "partial"
        )
        self.assertGreater(self.adapter.execution_count, counts[0])

    def test_recovered_earlier_task_invalidates_later_empty_library(self):
        class Recovering(DemoProposer):
            recovering = False

            def call(self, mission, context_dir, label, **kwargs):
                if (
                    not self.recovering
                    and label.startswith("select.")
                    and (Path(context_dir) / "source/ledger.csv")
                    .read_text()
                    .startswith("amount\n100.25")
                ):
                    raise ProposerError("temporary selector failure", attempts=1)
                return super().call(mission, context_dir, label, **kwargs)

        self.prop = Recovering(self.prop.spec)
        self.assertEqual([r["status"] for r in self.stream()["tasks"]], ["partial", "ok"])
        later = self.adapter.spec.runs_root / "demo-oc-agg2/ledger-2/context/inv1/library"
        self.assertFalse(later.exists())
        self.prop.recovering = True
        self.assertEqual([r["status"] for r in self.stream()["tasks"]], ["ok", "ok"])
        self.assertTrue((later / "harnesses/ledger-1/EXPERIENCE.md").is_file())
        self.assertEqual([r["status"] for r in self.stream()["tasks"]], ["skipped", "skipped"])

    def test_actual_selector_reflector_and_candidate_formats_invalidate_resume(self):
        self.assertEqual(self.stream()["failed"], 0)
        self.assertEqual([r["status"] for r in self.stream()["tasks"]], ["skipped", "skipped"])
        with patch.object(prompts, "ANSWER_FREE_SELECT", prompts.ANSWER_FREE_SELECT + "\nChanged."):
            self.assertEqual([r["status"] for r in self.stream()["tasks"]], ["ok", "ok"])
            self.assertEqual([r["status"] for r in self.stream()["tasks"]], ["skipped", "skipped"])
        self.assertEqual([r["status"] for r in self.stream()["tasks"]], ["ok", "ok"])
        experience = prompts.experience
        with patch.object(
            prompts, "experience", side_effect=lambda *a, **kw: experience(*a, **kw) + "\nChanged."
        ):
            self.assertEqual([r["status"] for r in self.stream()["tasks"]], ["ok", "ok"])
        self.assertEqual([r["status"] for r in self.stream()["tasks"]], ["ok", "ok"])
        header = selection._candidate_header
        with patch.object(selection, "_candidate_header", lambda *a: header(*a) + "Changed."):
            self.assertEqual([r["status"] for r in self.stream()["tasks"]], ["ok", "ok"])

    def test_k1_ignores_unused_selector_prompt_but_checks_reflection(self):
        self.assertEqual(self.stream(k=1)["failed"], 0)
        with patch.object(prompts, "ANSWER_FREE_SELECT", prompts.ANSWER_FREE_SELECT + "\nChanged."):
            self.assertEqual(
                [r["status"] for r in self.stream(k=1)["tasks"]], ["skipped", "skipped"]
            )
        experience = prompts.experience
        with patch.object(
            prompts, "experience", side_effect=lambda *a, **kw: experience(*a, **kw) + "\nChanged."
        ):
            self.assertEqual([r["status"] for r in self.stream(k=1)["tasks"]], ["ok", "ok"])

    def test_source_change_fails_before_reusing_old_execution_and_fresh_tag_works(self):
        first = self.run_task()
        chosen = Path(first.selections["agg"]["chosen_run_dir"])
        self.assertEqual(self.adapter.deliverable_text(chosen).strip(), "Total: 85.15")
        before = (self.adapter.execution_count, len(self.prop._calls))
        (self.adapter.task_files("ledger-1") / "ledger.csv").write_text("amount\n999\n")
        with self.assertRaisesRegex(ValueError, "fresh tag/run root"):
            self.run_task()
        self.assertEqual(before, (self.adapter.execution_count, len(self.prop._calls)))
        self.assertEqual(self.adapter.deliverable_text(chosen).strip(), "Total: 85.15")
        fresh = self.run_task(tag="updated-input")
        self.assertEqual(fresh.status, "ok")
        self.assertEqual(
            self.adapter.deliverable_text(Path(fresh.selections["agg"]["chosen_run_dir"])).strip(),
            "Total: 999.00",
        )

    def test_prompt_and_base_changes_require_new_execution_identity(self):
        self.run_task()
        before = self.adapter.execution_count
        with patch.object(self.adapter, "task_prompt", return_value="Changed task instructions."):
            with self.assertRaisesRegex(ValueError, "fresh tag/run root"):
                self.run_task()
        base = self.adapter.base_harness() / "systemprompt.md"
        base.write_text(base.read_text() + "\nChanged instructions.\n")
        with self.assertRaisesRegex(ValueError, "fresh tag/run root"):
            self.run_task()
        self.assertEqual(self.adapter.execution_count, before)

    def test_resume_survives_scratch_removal_and_preserves_selected_generation(self):
        self.stream()
        summary = self.adapter.spec.runs_root / "demo-oc-agg2/ledger-1/summary.json"
        before = json.loads(summary.read_text())["selections"]["agg"]["selected_at"]
        self.assertTrue(before)
        for path in self.adapter.spec.runs_root.glob("*/*/selection"):
            shutil.rmtree(path)
        self.assertEqual([r["status"] for r in self.stream()["tasks"]], ["skipped", "skipped"])
        self.assertEqual(
            before, json.loads(summary.read_text())["selections"]["agg"]["selected_at"]
        )
        doc = json.loads(summary.read_text())
        del doc["selections"]["agg"]["prompts_digest"]
        summary.write_text(json.dumps(doc))
        self.assertEqual([r["status"] for r in self.stream()["tasks"]], ["ok", "skipped"])
        self.assertNotEqual(
            before, json.loads(summary.read_text())["selections"]["agg"]["selected_at"]
        )

    def test_task_only_to_library_stream_adds_missing_reflection(self):
        self.assertEqual(self.run_task(k=2).status, "ok")
        self.assertEqual(self.stream(tasks=["ledger-1"])["tasks"][0]["status"], "ok")
        summary = json.loads(
            (self.adapter.spec.runs_root / "demo-oc-agg2/ledger-1/summary.json").read_text()
        )
        self.assertTrue(summary["selections"]["agg"]["experience_prompted"])

    def test_legacy_identity_does_not_silently_trust_stale_adapter_cache(self):
        self.run_task()
        (self.adapter.spec.runs_root / "demo-oc-agg1/ledger-1/.task-inputs.json").unlink()
        with self.assertRaisesRegex(ValueError, "Legacy task cache"):
            self.run_task()

    def test_changed_effective_proposer_requires_fresh_namespace(self):
        self.run_task()
        count = self.adapter.execution_count
        self.prop = DemoProposer(replace(self.prop.spec, model="local/changed"))
        with self.assertRaisesRegex(ValueError, "fresh tag/run root"):
            self.run_task()
        self.assertEqual(self.adapter.execution_count, count)
