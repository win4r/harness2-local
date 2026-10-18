# SPDX-License-Identifier: Apache-2.0
"""Integrated rerun failures must not reuse a selection, score, or self lesson."""

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2.cli.judge import _merge, collect_selections
from harness2.core import judge, prompts, schedule, selection, tree
from harness2.core.library import Library
from harness2.demo import DemoAdapter, DemoProposer


class RerunIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.adapter = DemoAdapter(self.root)
        self.prop = DemoProposer(self.adapter.spec.channels["proposer"])
        self.notes = self.root / "notes"
        self.notes.mkdir()
        self.note = self.notes / "ledger-1.md"
        self.note.write_text("Initial review.")
        self.ns = self.adapter.spec.runs_root / "demo-oc-agg1"

    def stream(self, modes=("agg", "seq")):
        return schedule.run_domain(
            "accounting",
            ["ledger-1"],
            1,
            adapter=self.adapter,
            prop=self.prop,
            runs_root=self.adapter.spec.runs_root,
            library_root=self.root / "lib",
            modes=modes,
            feedback_dir=self.notes,
        )

    def test_failed_reflection_cannot_attach_prior_grade_to_replaced_output(self):
        self.assertEqual(self.stream()["failed"], 0)
        old = {row["mode"]: row for row in collect_selections(self.ns, ("agg", "seq"))}
        old_grades = [
            judge.Scored(
                task_id="ledger-1",
                mode=mode,
                k=1,
                label=sel["chosen_label"],
                run_id=sel["chosen_run_id"],
                chosen_is_base=False,
                selected_at=sel["selected_at"],
                n_passed=1,
                n_criteria=1,
            )
            for mode, sel in old.items()
        ]
        self.note.write_text("Revised review requires rerun.")
        execute = self.adapter.execute
        observed_invalidations = []
        latest_final = []

        def replace_output(*args, **kwargs):
            current = {r["mode"]: r for r in collect_selections(self.ns, ("agg", "seq"))}
            observed_invalidations.append(not current["agg"]["ok"])
            self.assertEqual(current["seq"]["selected_at"], old["seq"]["selected_at"])
            run_dir = execute(*args, **kwargs)
            if args[2].endswith("/agg1"):
                (run_dir / "answer.txt").write_text("Total: 0.00\n")
                latest_final.append(run_dir)
            return run_dir

        with (
            patch.object(self.adapter, "execute", side_effect=replace_output),
            patch.object(
                selection, "reflect_adoption", side_effect=RuntimeError("reflection context failed")
            ),
        ):
            rerun = self.stream(("agg",))
        self.assertEqual(rerun["tasks"][0]["status"], "partial")
        self.assertTrue(observed_invalidations and all(observed_invalidations))
        self.assertEqual((latest_final[-1] / "answer.txt").read_text(), "Total: 0.00\n")
        current = {row["mode"]: row for row in collect_selections(self.ns, ("agg", "seq"))}
        self.assertFalse(current["agg"]["ok"])
        self.assertNotEqual(current["agg"]["selected_at"], old["agg"]["selected_at"])
        self.assertEqual(current["seq"], old["seq"])
        with patch.object(
            self.adapter, "score", create=True, side_effect=RuntimeError("judge unavailable")
        ):
            failed_grades = judge.judge_selections(self.adapter, list(current.values()))
        merged = {row.mode: row for row in _merge(old_grades, failed_grades)}
        self.assertFalse(merged["agg"].graded)
        self.assertTrue(merged["agg"].error.startswith("selection"))
        self.assertTrue(merged["seq"].graded)

    def test_base_execution_failure_also_retires_requested_selection(self):
        self.stream()
        old = {row["mode"]: row for row in collect_selections(self.ns, ("agg", "seq"))}
        self.note.write_text("Revised review.")
        with patch.object(self.adapter, "execute", side_effect=RuntimeError("base failed")):
            rerun = self.stream(("agg",))
        self.assertEqual(rerun["tasks"][0]["status"], "base-failed")
        current = {row["mode"]: row for row in collect_selections(self.ns, ("agg", "seq"))}
        self.assertFalse(current["agg"]["ok"])
        self.assertNotEqual(current["agg"]["selected_at"], old["agg"]["selected_at"])
        self.assertEqual(current["seq"], old["seq"])

    def test_direct_library_resume_excludes_self_and_preserves_other_entries(self):
        library = Library(
            self.root / "direct-library", "accounting", self.adapter.substrate.surface()
        )
        library.add(
            "prior-task",
            tree.read_tree(library.surface, self.adapter.base_harness()),
            experience={
                "cue": "Prior source",
                "lesson": "Prior lesson",
                "evidence": "Prior contrast",
            },
        )
        other = library.entry_dir("prior-task")

        def content_hashes():
            return {
                str(p.relative_to(other)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in other.rglob("*")
                if p.is_file()
            }

        before_other = content_hashes()
        seen_self = []
        call = self.prop.call

        def inspect_context(mission, context_dir, label, **kwargs):
            if not label.startswith(("select.", "experience.")):
                seen_self.append((Path(context_dir) / "library/harnesses/ledger-1").exists())
                self.assertTrue((Path(context_dir) / "library/harnesses/prior-task").is_dir())
            return call(mission, context_dir, label, **kwargs)

        def run():
            return schedule.run_task(
                "ledger-1",
                1,
                adapter=self.adapter,
                prop=self.prop,
                runs_root=self.adapter.spec.runs_root,
                library=library,
                modes=("agg",),
                feedback_dir=self.notes,
            )

        with patch.object(self.prop, "call", side_effect=inspect_context):
            self.assertEqual(run().status, "ok")
            counts = (self.adapter.execution_count, len(self.prop._calls))
            for _ in range(2):
                self.assertEqual(run().status, "skipped")
                self.assertEqual(counts, (self.adapter.execution_count, len(self.prop._calls)))
                self.assertEqual(library.entries(), ["ledger-1", "prior-task"])
                self.assertEqual(content_hashes(), before_other)
            self.note.write_text("Changed review requires a new generation.")
            self.assertEqual(run().status, "ok")
        self.assertTrue(seen_self)
        self.assertFalse(any(seen_self))
        self.assertEqual(content_hashes(), before_other)
        self.assertEqual(library.entries(), ["ledger-1", "prior-task"])

    def test_authoring_prompt_change_fails_closed_before_any_new_execution(self):
        self.assertEqual(self.stream(("agg",))["failed"], 0)
        count = (self.adapter.execution_count, len(self.prop._calls))
        old = collect_selections(self.ns, ("agg",))[0]
        with patch.object(prompts, "GENERALIZE", prompts.GENERALIZE + "\nNew editing requirement."):
            result = self.stream(("agg",))
            self.assertEqual(result["tasks"][0]["status"], "exception")
            self.assertIn("Authoring prompts changed", result["tasks"][0]["error"])
            self.assertIn("fresh tag/run root", result["tasks"][0]["error"])
            self.assertEqual(count, (self.adapter.execution_count, len(self.prop._calls)))
            fresh = schedule.run_task(
                "ledger-1",
                1,
                adapter=self.adapter,
                prop=self.prop,
                runs_root=self.adapter.spec.runs_root,
                modes=("agg",),
                tag="new-method",
            )
            self.assertEqual(fresh.status, "ok")
        self.assertEqual(collect_selections(self.ns, ("agg",))[0], old)

    def test_changed_unrequested_sequential_template_does_not_block_parallel_resume(self):
        def run(modes):
            return schedule.run_domain(
                "accounting",
                ["ledger-1"],
                2,
                adapter=self.adapter,
                prop=self.prop,
                runs_root=self.adapter.spec.runs_root,
                library_root=self.root / "k2-lib",
                modes=modes,
            )

        self.assertEqual(run(("agg", "seq"))["failed"], 0)
        count = (self.adapter.execution_count, len(self.prop._calls))
        compose = prompts.compose_round
        with patch.object(
            prompts,
            "compose_round",
            side_effect=lambda *a, **kw: compose(*a, **kw) + "\nNew sequential method.",
        ):
            self.assertEqual(run(("agg",))["tasks"][0]["status"], "skipped")
            self.assertEqual(count, (self.adapter.execution_count, len(self.prop._calls)))
            self.assertEqual(run(("seq",))["tasks"][0]["status"], "exception")
            self.assertEqual(count, (self.adapter.execution_count, len(self.prop._calls)))

    def test_rejected_source_change_preserves_direct_library_bytes(self):
        library = Library(
            self.root / "direct-library", "accounting", self.adapter.substrate.surface()
        )

        def run():
            return schedule.run_task(
                "ledger-1",
                1,
                adapter=self.adapter,
                prop=self.prop,
                runs_root=self.adapter.spec.runs_root,
                modes=("agg",),
                library=library,
            )

        self.assertEqual(run().status, "ok")
        before = {
            str(p.relative_to(library.dir)): p.read_bytes()
            for p in library.dir.rglob("*")
            if p.is_file()
        }
        count = (self.adapter.execution_count, len(self.prop._calls))
        (self.adapter.task_files("ledger-1") / "ledger.csv").write_text("amount\n999\n")
        with self.assertRaisesRegex(ValueError, "fresh tag/run root"):
            run()
        after = {
            str(p.relative_to(library.dir)): p.read_bytes()
            for p in library.dir.rglob("*")
            if p.is_file()
        }
        self.assertEqual(after, before)
        self.assertEqual(count, (self.adapter.execution_count, len(self.prop._calls)))
