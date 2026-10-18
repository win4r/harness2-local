# SPDX-License-Identifier: Apache-2.0
"""Runtime containment regressions adapted from upstream PR #32."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2.core import recursion, tree
from harness2.core.proposer import Proposer
from harness2.demo import DemoAdapter


class RuntimeContainmentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.adapter = DemoAdapter(self.root)
        self.proposer = Proposer(self.adapter.spec.channels["proposer"])

    def test_directory_symlink_does_not_admit_external_files_or_redirect_writes(self):
        surface = self.adapter.substrate.surface()
        base = tree.read_tree(surface, self.adapter.base_harness())
        outside = self.root / "external_scripts"
        outside.mkdir()
        sentinel = outside / "summarize.py"
        sentinel.write_text("external = True\n")
        staged = self.root / "linked_harness"
        staged.mkdir()
        (staged / "systemprompt.md").write_text("Keep this harness inside its directory.\n")
        (staged / "scripts").symlink_to(outside, target_is_directory=True)
        rejected = []
        loaded = tree.read_tree(surface, staged, rejected=rejected)
        self.assertEqual(rejected, ["scripts"])
        self.assertNotIn("scripts/summarize.py", loaded)
        tree.write_tree(surface, base, staged)
        self.assertFalse((staged / "scripts").is_symlink())
        self.assertEqual(
            (staged / "scripts/summarize.py").read_text(), base["scripts/summarize.py"]
        )
        self.assertEqual(sentinel.read_text(), "external = True\n")

    def test_harvest_rejects_parent_symlinks_and_outside_subpaths(self):
        outside = self.root / "outside"
        (outside / "harness").mkdir(parents=True)
        (outside / "harness/secret.py").write_text("secret = True\n")
        session = self.root / "session"
        session.mkdir()
        (session / "context").symlink_to(outside, target_is_directory=True)
        proposer = Proposer(self.proposer.spec, audit_dir=self.root / "audit")
        for subpath in ("context/harness", "../outside/harness", str(outside / "harness"), "."):
            with self.subTest(subpath=subpath):
                self.assertIsNone(proposer._harvest(session, subpath, "inv1"))

    def test_symlink_and_surface_containment(self):
        from harness2.core import runmark

        surface = self.adapter.substrate.surface()
        base = tree.read_tree(surface, self.adapter.base_harness())
        with self.assertRaises(ValueError):
            tree.normalize_rel("scripts/bad\x00.py")
        self.assertFalse(tree.is_allowed(surface, "scripts/bad\x00.py"))
        self.assertFalse(tree.is_allowed(surface, "..\\outside.py"))
        with self.assertRaises(ValueError):
            tree.write_tree(surface, {**base, "off_surface.txt": "bad"}, self.root / "bad_dest")

        outside = self.root / "outside.py"
        outside.write_text("secret = 1\n")
        staged = tree.write_tree(surface, base, self.root / "staged")
        orig_prompt = (staged / "systemprompt.md").read_text()
        with self.assertRaises(ValueError):
            tree.write_tree(surface, {"systemprompt.md": "mutated\n", "../evil.md": "x"}, staged)
        self.assertEqual((staged / "systemprompt.md").read_text(), orig_prompt)
        (staged / "scripts").mkdir(exist_ok=True)
        (staged / "scripts/escape.py").symlink_to(outside)
        (staged / "scripts/dangling.py").symlink_to(self.root / "missing.py")
        rejected = []
        loaded = tree.read_tree(surface, staged, rejected=rejected)
        self.assertEqual(sorted(rejected), ["scripts/dangling.py", "scripts/escape.py"])
        self.assertNotIn("scripts/escape.py", loaded)

        (staged / "guardrails.md").unlink()
        (staged / "guardrails.md").symlink_to(outside)
        tree.write_tree(surface, base, staged)
        self.assertFalse((staged / "guardrails.md").is_symlink())
        self.assertEqual(outside.read_text(), "secret = 1\n")

        sym_dir, empty_dir = self.root / "sym_harness", self.root / "empty_dir"
        sym_dir.mkdir()
        empty_dir.mkdir()
        (sym_dir / "systemprompt.md").symlink_to(outside)
        self.assertFalse(tree.is_tree(surface, sym_dir))
        self.assertEqual(runmark.fingerprint(sym_dir), runmark.fingerprint(empty_dir))

        sym_root = self.root / "sym_root_link"
        sym_root.symlink_to(staged)
        self.assertEqual(tree.read_tree(surface, sym_root), {})
        self.assertFalse(tree.is_tree(surface, sym_root))
        self.assertEqual(runmark.fingerprint(sym_root), "absent")
        with self.assertRaises(ValueError):
            tree.write_tree(surface, base, sym_root)

        prop_inst = Proposer(self.proposer.spec, audit_dir=self.root / "audit")
        (staged / "scripts").mkdir(exist_ok=True)
        (staged / "scripts/escape.py").symlink_to(outside)
        harvested = prop_inst._harvest(self.root, "staged", "h1")
        self.assertTrue((harvested / "scripts/escape.py").is_symlink())
        self.assertIsNone(prop_inst._harvest(self.root, "sym_root_link", "h2"))

    def test_reserved_scripts_immutability(self):
        from dataclasses import replace

        surface = self.adapter.substrate.surface()
        base = tree.read_tree(surface, self.adapter.base_harness())
        with self.assertRaises(ValueError):
            tree.validate(surface, {**base, "scripts/read_doc.py": "x = 1\n"})
        with self.assertRaises(ValueError):
            tree.validate(surface, {**base, "scripts/read_doc.py": "x = 1\n"}, base=base)

        base_with_reserved = {**base, "scripts/read_doc.py": "orig = 1\n"}
        tree.validate(surface, dict(base_with_reserved), base=base_with_reserved)
        with self.assertRaises(ValueError):
            tree.validate(
                surface, {**base, "scripts/read_doc.py": "changed = 2\n"}, base=base_with_reserved
            )
        with self.assertRaises(ValueError):
            tree.validate(surface, dict(base), base=base_with_reserved)

        with patch.object(
            self.adapter,
            "spec",
            replace(self.adapter.spec, reserved_scripts=frozenset({"summarize.py"})),
        ):
            _, r_err = recursion._accept_tree(
                surface, self.adapter, {**base, "scripts/summarize.py": "x = 1\n"}, base, "inv1"
            )
            self.assertIn("reserved runner script", r_err)
