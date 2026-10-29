# SPDX-License-Identifier: GPL-3.0-or-later
"""Temporary directories without tempfile.mkdtemp's Windows DACL.

Since Python 3.13 (CVE-2024-4030), os.mkdir(path, 0o700) on Windows, and so
tempfile.mkdtemp and tempfile.TemporaryDirectory, gives the new directory an
owner-only DACL. A process on a restricted token (the Codex Windows sandbox,
openai/codex#19791) cannot write through it: the engine could not write its
generated SPIR-V (vkprogs/cache/...) under a test's fs_savepath, so Vulkan init
failed and the client fell back to OpenGL, and the tool tests failed at setup.

make_private_dir uses the default mode on Windows, so the directory inherits the
temp root's ACL like any other, and mkdtemp's 0o700 on POSIX, so Linux and macOS
behave as before. On Windows the default parent is the per-user temp root
(%TEMP%, under the user's profile, which other standard users can't read), so the
inherited ACL is no wider than the user's own; a caller that passes a shared `dir`
gets that directory's ACL. The contents are test configs, logs and shader caches,
not secrets. This module has no side effects on import (etqw_harness sets
XDG_DATA_HOME and LOCALAPPDATA), so tests that don't use the harness import it
directly; etqw_harness re-exports both names.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from typing import Optional

PRIVATE_DIR_MODE = 0o777 if os.name == "nt" else 0o700


def make_private_dir(prefix: str = "etqw-", dir: Optional[os.PathLike | str] = None) -> Path:
    """A new, empty, uniquely named directory under `dir` (default: the temp root),
    like tempfile.mkdtemp but without the owner-only DACL it applies on Windows (see
    PRIVATE_DIR_MODE). os.mkdir never reuses an existing path,
    so the name is as race-free as mkdtemp's. The caller removes it."""
    parent = Path(dir) if dir is not None else Path(tempfile.gettempdir())
    names = tempfile._get_candidate_names()  # mkdtemp's own name source (private CPython API)
    for _ in range(tempfile.TMP_MAX):
        path = parent / f"{prefix}{next(names)}"
        try:
            os.mkdir(path, PRIVATE_DIR_MODE)  # like mkdtemp: a missing parent raises
        except FileExistsError:
            continue
        return Path(os.path.abspath(path))
    raise FileExistsError(f"no unused directory name with prefix {prefix!r} under {parent}")


def _make_writable_and_retry(func, path, _exc) -> None:
    # TemporaryDirectory does the same: read-only entries (git objects, a test's
    # chmod) block removal on Windows until their write bit is back
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
        func(path)
    except OSError:
        pass


def remove_private_dir(path: os.PathLike | str) -> None:
    """Remove a directory from make_private_dir, read-only entries included."""
    if not os.path.lexists(path):
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_make_writable_and_retry)
    else:
        shutil.rmtree(path, onerror=_make_writable_and_retry)


class PrivateTempDir:
    """tempfile.TemporaryDirectory's shape (a context manager yielding the path as a
    str, removed on exit) over make_private_dir."""

    def __init__(self, prefix: str = "etqw-", dir: Optional[os.PathLike | str] = None):
        self.name = str(make_private_dir(prefix, dir))

    def __enter__(self) -> str:
        return self.name

    def __exit__(self, *exc) -> None:
        self.cleanup()

    def cleanup(self) -> None:
        remove_private_dir(self.name)
