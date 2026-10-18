# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Opt-in, frozen human notes for selection and reflection only.

Files are UTF-8 ``<task-slug>.md`` in a flat directory. Freeze the entire directory
before any execution: subsequent edits cannot change a running sweep's evidence.
This is human-steered use, not an answer-free benchmark condition.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from . import context
from .library import slug

MAX_FILE_BYTES = 64 * 1024
MAX_TOTAL_BYTES = 1024 * 1024


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""


def path_for(feedback_dir: Path | str, task_id: str) -> Path:
    name = f"{slug(task_id)}.md"
    if not task_id or "\\" in name or "\x00" in name or Path(name).name != name:
        raise ValueError(f"unsafe feedback task id: {task_id!r}")
    return Path(feedback_dir) / name


def validate_text(text: str, *, name: str = "feedback") -> str:
    if not isinstance(text, str):
        raise TypeError(f"{name} must be text")
    if len(text.encode("utf-8")) > MAX_FILE_BYTES:
        raise ValueError(f"{name} exceeds {MAX_FILE_BYTES} bytes")
    for marker in context.GRADER_VERDICT_MARKERS:
        if marker in text:
            raise ValueError(f"{name} contains grader-output marker {marker!r}")
    return text.strip()


@dataclass(frozen=True)
class Snapshot:
    enabled: bool
    directory: str = ""
    entries: tuple[tuple[str, str], ...] = ()
    unmatched: tuple[str, ...] = ()
    digest: str = ""

    def text(self, task_id: str) -> str:
        for task, body in self.entries:
            if task == task_id:
                return body
        if self.enabled:
            raise ValueError(f"task {task_id!r} was not included in the feedback snapshot")
        return ""


def freeze(feedback_dir: Path | str | None, task_ids: Iterable[str]) -> Snapshot:
    """Validate all files, including unmatched files, and retain immutable text.

    Reject symlinks, nested directories, invalid UTF-8, oversized notes, grader
    verdicts and task-to-filename collisions (also on case-insensitive volumes).
    An absent matching file means no note; unmatched files are reported separately.
    """
    tasks = tuple(dict.fromkeys(str(t) for t in task_ids))
    if feedback_dir is None:
        return Snapshot(False)
    root = Path(feedback_dir)
    if root.is_symlink():
        raise ValueError(f"feedback directory must not be a symlink: {root}")
    if not root.is_dir():
        raise FileNotFoundError(f"feedback directory {root} does not exist")
    expected: dict[str, str] = {}
    folded: dict[str, str] = {}
    for task in tasks:
        name = path_for(root, task).name
        key = unicodedata.normalize("NFC", name).casefold()
        if key in folded and folded[key] != task:
            raise ValueError(f"feedback filename collision: {folded[key]!r} and {task!r}")
        folded[key] = task
        expected[name] = task
    texts = {task: "" for task in tasks}
    unmatched = []
    total = 0
    h = hashlib.sha256()
    for path in sorted(root.iterdir()):
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"feedback must contain regular files only: {path}")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError(f"feedback must be a regular file: {path}")
            raw = stream.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise ValueError(f"{path} exceeds {MAX_FILE_BYTES} bytes")
        total += len(raw)
        if total > MAX_TOTAL_BYTES:
            raise ValueError(f"feedback directory exceeds {MAX_TOTAL_BYTES} total bytes")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"{path} is not UTF-8 text") from exc
        text = validate_text(text, name=str(path))
        h.update(json.dumps([path.name, hashlib.sha256(raw).hexdigest()]).encode("utf-8"))
        h.update(b"\0")
        if path.name in expected:
            texts[expected[path.name]] = text
        else:
            unmatched.append(path.name)
    return Snapshot(
        True, str(root.resolve()), tuple(texts.items()), tuple(unmatched), h.hexdigest()
    )


def check_dir(feedback_dir: Path | str, task_ids: Iterable[str]) -> list[str]:
    return list(freeze(feedback_dir, task_ids).unmatched)


def read(feedback_dir: Path | str | None, task_id: str) -> str:
    """One-shot convenience; drivers use a single snapshot for the complete stream."""
    return freeze(feedback_dir, [task_id]).text(task_id)


def claim_namespace(namespace_dir: Path, snapshot: Snapshot) -> None:
    """Sticky opt-in identity, enforced equally by CLI, demo, and direct core API."""
    import fcntl

    namespace_dir.mkdir(parents=True, exist_ok=True)
    path = namespace_dir / ".feedback.json"
    with (namespace_dir / ".feedback.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            if old.get("feedback") and not snapshot.enabled:
                raise ValueError(
                    "This namespace was steered by human feedback. Supply feedback again "
                    "or use a fresh tag/run root for answer-free work."
                )
            if snapshot.enabled:
                tmp = path.with_suffix(".tmp")
                tmp.write_text(
                    json.dumps(
                        {
                            "feedback": True,
                            "snapshot_sha256": snapshot.digest,
                            "directory": snapshot.directory,
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                os.replace(tmp, path)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
