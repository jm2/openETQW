#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stage openETQW-game headers and interfaces into a temporary local directory."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

MANIFEST_NAME = "openetqw_gamelibs_stage_manifest.json"

STAGED_HEADER_GLOBS = (
    "src/game/*.h",
    "src/game/botai/*.h",
    "src/game/decls/*.h",
    "src/idlib/*.h",
    "src/idlib/bv/*.h",
    "src/idlib/containers/*.h",
    "src/idlib/geometry/*.h",
    "src/idlib/hashing/*.h",
    "src/idlib/math/*.h",
    "src/idlib/text/*.h",
    "src/idlib/sys/*.h",
    "src/idlib/sys/threading/*.h",
    "src/idlib/threading/*.h",
    "src/framework/*.h",
    "src/framework/*.inc",
    "src/framework/async/*.h",
    "src/sdnet/*.h",
    "src/renderer/*.h",
    "src/sound/*.h",
    "src/bse/*.h",
    "src/cm/*.h",
    "src/common/*.h",
    "src/sys/*.h",
    "src/libs/AASLib/*.h",
    "src/libs/filelib/*.h",
    "src/libs/qglLib/*.h",
    "src/decllib/*.h",
)

# Test harness and engine reconstruction source files staged dynamically
STAGED_TEST_SOURCES = (
    "src/idlib/BitMsg.cpp",
    "src/decllib/DeclType.cpp",
)


def repo_git_value(root: Path, *args: str) -> str:
    if not (root / ".git").exists():
        return ""
    completed = subprocess.run(
        # no optional index lock: this runs at every (re)configure
        ["git", "--no-optional-locks", "-C", str(root), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def repo_git_dirty(root: Path) -> bool:
    return bool(repo_git_value(root, "status", "--porcelain"))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        if os.name == "nt":
            try:
                # Windows paths are case-insensitive
                Path(path.resolve().as_posix().lower()).relative_to(
                    Path(parent.resolve().as_posix().lower())
                )
                return True
            except ValueError:
                return False
        return False


def validate_stage_root(project_root: Path, game_root: Path, stage_root: Path) -> None:
    if stage_root.is_symlink():
        raise RuntimeError(f"Refusing to stage into symlink: {stage_root}")
    project_tmp = project_root / ".tmp"
    if not is_relative_to(stage_root, project_tmp) or stage_root == project_tmp:
        raise RuntimeError(f"Stage root must reside strictly under .tmp: {stage_root}")
    if is_relative_to(project_root, stage_root) or is_relative_to(game_root, stage_root):
        raise RuntimeError(f"Refusing to stage over source repositories: {stage_root}")


def make_writable(path: str | Path) -> None:
    """Ensure a file or directory path has write permissions."""
    try:
        p = Path(path)
        if p.is_dir():
            os.chmod(p, stat.S_IWRITE | stat.S_IWUSR | stat.S_IREAD | stat.S_IRUSR | stat.S_IEXEC | stat.S_IXUSR)
        else:
            os.chmod(p, stat.S_IWRITE | stat.S_IWUSR | stat.S_IREAD | stat.S_IRUSR)
    except OSError:
        pass


def safe_rmtree(target: Path, max_retries: int = 3, retry_delay: float = 0.05) -> None:
    """Recursively remove a directory tree, handling Windows read-only attributes and file locks."""
    if not target.exists() and not target.is_symlink():
        return

    if target.is_symlink() or target.is_file():
        make_writable(target)
        target.unlink(missing_ok=True)
        return

    def _rmtree_onerror(func, path, exc_info):
        parent = os.path.dirname(path)
        if parent:
            make_writable(parent)
        make_writable(path)
        try:
            func(path)
        except OSError:
            pass

    def _rmtree_onexc(func, path, exc):
        parent = os.path.dirname(path)
        if parent:
            make_writable(parent)
        make_writable(path)
        try:
            func(path)
        except OSError:
            pass

    has_onexc = hasattr(shutil, "rmtree") and "onexc" in inspect.signature(shutil.rmtree).parameters

    for attempt in range(max_retries):
        try:
            if has_onexc:
                shutil.rmtree(target, onexc=_rmtree_onexc)
            else:
                shutil.rmtree(target, onerror=_rmtree_onerror)
            if not target.exists():
                return
        except OSError:
            pass
        if attempt < max_retries - 1:
            time.sleep(retry_delay)

    if target.exists():
        raise RuntimeError(f"Failed to cleanly remove directory tree: {target}")


def stage_files(game_root: Path, stage_root: Path) -> list[Path]:
    staged_paths: list[Path] = []
    
    # Collect files matching globs
    source_candidates: set[Path] = set()
    for pattern in STAGED_HEADER_GLOBS:
        for p in game_root.glob(pattern):
            if p.is_file() and not p.is_symlink():
                source_candidates.add(p)

    for rel_str in STAGED_TEST_SOURCES:
        p = game_root / rel_str
        if p.is_file() and not p.is_symlink():
            source_candidates.add(p)

    for src_path in sorted(source_candidates):
        rel = src_path.relative_to(game_root)
        dest_path = stage_root / rel
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_path, dest_path)
        make_writable(dest_path)
        staged_paths.append(dest_path)

    return staged_paths


def write_and_verify_manifest(
    project_root: Path, game_root: Path, stage_root: Path, staged_files: list[Path]
) -> None:
    files_entry = []
    for p in sorted(staged_files):
        rel = p.relative_to(stage_root).as_posix()
        files_entry.append({
            "path": rel,
            "sha256": file_sha256(p),
        })

    manifest = {
        "format": 1,
        "projectRoot": project_root.as_posix(),
        "projectGitCommit": repo_git_value(project_root, "rev-parse", "--verify", "HEAD"),
        "projectGitDirty": repo_git_dirty(project_root),
        "gameLibsRoot": game_root.as_posix(),
        "gameLibsGitCommit": repo_git_value(game_root, "rev-parse", "--verify", "HEAD"),
        "gameLibsGitDirty": repo_git_dirty(game_root),
        "fileCount": len(files_entry),
        "files": files_entry,
    }

    manifest_file = stage_root / MANIFEST_NAME
    manifest_file.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # Immediate self-verification
    data = json.loads(manifest_file.read_text(encoding="utf-8"))
    for item in data["files"]:
        target = stage_root / item["path"]
        if not target.is_file():
            raise RuntimeError(f"Manifest verification failed, missing file: {target}")
        if file_sha256(target) != item["sha256"]:
            raise RuntimeError(f"Manifest verification checksum mismatch: {target}")


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("usage: stage_game.py <project-root> <game-root> <stage-root>", file=sys.stderr)
        return 2

    project_root = Path(argv[1]).resolve()
    game_root = Path(argv[2]).resolve()
    stage_root = Path(argv[3]).resolve()

    if not (game_root / "src" / "game").is_dir():
        print(f"error: openETQW-game src/game not found: {game_root}", file=sys.stderr)
        return 1

    try:
        validate_stage_root(project_root, game_root, stage_root)
        safe_rmtree(stage_root)
        stage_root.mkdir(parents=True, exist_ok=True)
        staged = stage_files(game_root, stage_root)
        write_and_verify_manifest(project_root, game_root, stage_root, staged)
    except Exception as exc:
        print(f"error during stage_game: {exc}", file=sys.stderr)
        return 1

    print(stage_root.as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
