# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""A retried proposal starts from its previous attempt, whatever rejected that attempt."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2.core import recursion, tree
from harness2.core.proposer import Proposer
from harness2.demo import DemoAdapter


class StageRetryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.adapter = DemoAdapter(self.root)

    def run_stage(self, defect: str, extra: str):
        """Through the real Proposer: attempt 1 fixes the script and adds `defect`, the retry
        only drops `defect` from whatever it finds in `context/harness/`."""
        seen, missions = [], []

        def session(cwd, mission, system, wall):
            harness = Path(cwd) / "context/harness"
            script = harness / "scripts/summarize.py"
            seen.append(script.read_text())
            missions.append(mission)
            if "PREVIOUS ATTEMPT WAS REJECTED" not in mission:
                (harness / defect).write_text(extra)
                script.write_text(script.read_text().replace(" if amount > 0", ""))
            elif (harness / defect).is_file():
                (harness / defect).unlink()
            return "<notes>Keep signed amounts.</notes>", 0.0, ""

        work = self.root / defect.replace("/", "_")
        prop = Proposer(self.adapter.spec.channels["proposer"], audit_dir=work / "audit")
        with patch.object(prop, "_opencode", side_effect=session):
            stage = recursion._run_stage(
                label="inv1",
                dim="INV",
                job="Repair the script",
                adapter=self.adapter,
                prop=prop,
                ctx_dir=work / "context",
                task_id="ledger-1",
                rollouts=[],
                fallback=tree.read_tree(
                    self.adapter.substrate.surface(), self.adapter.base_harness()
                ),
                harness_dir=self.adapter.base_harness(),
                library_dir=None,
            )
        return stage, seen, missions

    def test_retry_keeps_the_rejected_attempts_edits(self):
        for defect, extra in (
            ("scripts/check.py", "def broken(:\n"),
            ("scripts/summarize.py.bak", "# an editor's backup, off the editable surface\n"),
        ):
            with self.subTest(defect=defect):
                stage, seen, missions = self.run_stage(defect, extra)
                self.assertIn(defect, missions[1])
                self.assertNotIn(" if amount > 0", seen[1])
                self.assertEqual(
                    (stage.fell_back, stage.kept_base, stage.attempts), (False, False, 2)
                )
                self.assertNotIn(" if amount > 0", stage.tree["scripts/summarize.py"])
                self.assertNotIn(defect, stage.tree)

    def test_retry_restores_reserved_helpers_and_retains_valid_script_edits(self):
        surface = self.adapter.substrate.surface()
        original = tree.read_tree(surface, self.adapter.base_harness())
        for alteration in ("add", "modify", "delete"):
            for off_surface in (False, True):
                with self.subTest(alteration=alteration, off_surface=off_surface):
                    fallback = dict(original)
                    if alteration != "add":
                        fallback["scripts/read_doc.py"] = "helper_version = 'original'\n"
                    tree.write_tree(surface, fallback, self.adapter.base_harness())
                    work = self.root / f"reserved-{alteration}-{off_surface}"
                    seen = []

                    def session(cwd, mission, system, wall):
                        harness = cwd / "context/harness"
                        script = harness / "scripts/summarize.py"
                        helper = harness / "scripts/read_doc.py"
                        seen.append(tree.read_tree(surface, harness))
                        if len(seen) == 1:
                            script.write_text(script.read_text().replace(" if amount > 0", ""))
                            if alteration == "delete":
                                helper.unlink()
                            else:
                                helper.write_text("helper_version = 'changed'\n")
                            if off_surface:
                                (harness / "scripts/summarize.py.bak").write_text("backup\n")
                        else:
                            self.assertIn("PREVIOUS ATTEMPT WAS REJECTED", mission)
                            self.assertNotIn(" if amount > 0", script.read_text())
                            self.assertEqual(
                                helper.read_text() if helper.exists() else None,
                                fallback.get("scripts/read_doc.py"),
                            )
                            self.assertFalse((harness / "scripts/summarize.py.bak").exists())
                        return "<notes>Keep signed amounts.</notes>", 0.0, ""

                    proposer = Proposer(
                        self.adapter.spec.channels["proposer"], audit_dir=work / "audit"
                    )
                    with patch.object(proposer, "_opencode", side_effect=session):
                        stage = recursion._run_stage(
                            label="inv1",
                            dim="INV",
                            job="Repair the script",
                            adapter=self.adapter,
                            prop=proposer,
                            ctx_dir=work / "context",
                            task_id="ledger-1",
                            rollouts=[],
                            fallback=fallback,
                            harness_dir=self.adapter.base_harness(),
                            library_dir=None,
                        )
                    self.assertEqual((stage.fell_back, stage.attempts), (False, 2))
                    self.assertNotIn(" if amount > 0", stage.tree["scripts/summarize.py"])
                    self.assertEqual(
                        stage.tree.get("scripts/read_doc.py"), fallback.get("scripts/read_doc.py")
                    )

    def test_repeated_reserved_mutation_falls_back_without_applying_other_edits(self):
        surface = self.adapter.substrate.surface()
        fallback = tree.read_tree(surface, self.adapter.base_harness())
        work = self.root / "reserved-repeated"

        def session(cwd, mission, system, wall):
            harness = cwd / "context/harness"
            script = harness / "scripts/summarize.py"
            script.write_text(script.read_text().replace(" if amount > 0", ""))
            (harness / "scripts/read_doc.py").write_text("helper_version = 'changed'\n")
            return "Edited", 0.0, ""

        proposer = Proposer(self.adapter.spec.channels["proposer"], audit_dir=work / "audit")
        with patch.object(proposer, "_opencode", side_effect=session):
            stage = recursion._run_stage(
                label="inv1",
                dim="INV",
                job="Repair the script",
                adapter=self.adapter,
                prop=proposer,
                ctx_dir=work / "context",
                task_id="ledger-1",
                rollouts=[],
                fallback=fallback,
                harness_dir=self.adapter.base_harness(),
                library_dir=None,
            )
        self.assertEqual((stage.fell_back, stage.attempts), (True, 2))
        self.assertIn("reserved runner script", stage.error)
        self.assertEqual(stage.tree, fallback)


if __name__ == "__main__":
    unittest.main()
