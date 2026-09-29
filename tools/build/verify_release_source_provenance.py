#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Verify that a release build used the exact approved source commits.

Adapted from openQ4's tools/build/verify_release_source_provenance.py at
jm2/openQ4 b37b1456 (GPL-3.0; docs/provenance.md): the engine (openETQW) and
companion (openETQW-game) checkouts must be clean and at the expected commits,
and the stage manifest tools/build/stage_game.py wrote at configure time
(.tmp/gamelibs_stage/openetqw_gamelibs_stage_manifest.json) must record the
same clean commits and still match the staged files. The donor imports its
manifest check from stage_gamelibs.py; stage_game.py has none, so it is here.

The companion checkout defaults to $OPENETQW_GAME_REPO, else ../openETQW-game
beside the engine root (the same rule as meson.build and fetch_companion.py).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path


FULL_GIT_SHA_RE = re.compile(r"[0-9a-f]{40}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
ENGINE_LABEL = "openETQW"
GAME_LABEL = "openETQW-game"


def require_full_git_sha(value: str, label: str) -> str:
    value = value.strip().lower()
    if FULL_GIT_SHA_RE.fullmatch(value) is None:
        raise RuntimeError(f"{label} must be a full 40-character Git SHA: {value!r}")
    return value


def require_repository(path: Path, label: str) -> Path:
    if path.is_symlink():
        raise RuntimeError(f"{label} must not be a symlink: {path}")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError(f"{label} is unavailable: {path}") from exc
    if not resolved.is_dir():
        raise RuntimeError(f"{label} is not a directory: {resolved}")
    return resolved


def git_value(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(
            f"Git command failed for {repository}: git {' '.join(args)}"
            + (f": {detail}" if detail else "")
        )
    return completed.stdout.strip()


def verify_repository_commit(repository: Path, expected_commit: str, label: str) -> None:
    actual_commit = require_full_git_sha(
        git_value(repository, "rev-parse", "--verify", "HEAD^{commit}"),
        f"{label} checked-out commit",
    )
    if actual_commit != expected_commit:
        raise RuntimeError(
            f"{label} checked-out commit {actual_commit} does not match expected "
            f"release commit {expected_commit}"
        )

    if git_value(repository, "status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError(f"{label} checkout is dirty during release provenance validation")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def read_stage_manifest(path: Path) -> dict[str, object]:
    if path.is_symlink():
        raise RuntimeError(f"stage manifest must not be a symlink: {path}")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError(f"stage manifest is unavailable: {path}") from exc
    if not resolved.is_file():
        raise RuntimeError(f"stage manifest is not a regular file: {resolved}")
    try:
        manifest = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"stage manifest is unreadable: {resolved}") from exc
    if not isinstance(manifest, dict):
        raise RuntimeError("stage manifest root must be an object")
    if type(manifest.get("format")) is not int or manifest.get("format") != 1:
        raise RuntimeError("stage manifest has an unsupported format")
    return manifest


def validate_staged_files(manifest: dict[str, object], stage_root: Path) -> None:
    """Every staged file the manifest lists is still present with its hash."""
    stage_root = stage_root.resolve()
    files = manifest.get("files")
    file_count = manifest.get("fileCount")
    if not isinstance(files, list) or type(file_count) is not int or file_count != len(files):
        raise RuntimeError("stage manifest file count is inconsistent")

    for entry in files:
        rel = entry.get("path") if isinstance(entry, dict) else None
        expected_hash = entry.get("sha256") if isinstance(entry, dict) else None
        if not isinstance(rel, str) or not isinstance(expected_hash, str):
            raise RuntimeError("stage manifest contains a malformed file entry")
        if SHA256_RE.fullmatch(expected_hash) is None:
            raise RuntimeError(f"stage manifest contains a malformed sha256 for {rel}")

        rel_path = Path(rel)
        if not rel_path.parts or rel_path.is_absolute() or any(part in ("", ".", "..") for part in rel_path.parts):
            raise RuntimeError(f"stage manifest contains an unsafe path: {rel}")
        path = stage_root / rel_path
        if not path.resolve().is_relative_to(stage_root):
            raise RuntimeError(f"stage manifest path escapes the stage root: {rel}")
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"stage manifest references a missing file: {rel}")
        if file_sha256(path) != expected_hash:
            raise RuntimeError(f"stage manifest hash mismatch for {rel}")


def verify_manifest_commit(
    manifest: dict[str, object],
    *,
    commit_key: str,
    dirty_key: str,
    expected_commit: str,
    label: str,
) -> None:
    raw_commit = manifest.get(commit_key)
    if not isinstance(raw_commit, str):
        raise RuntimeError(f"stage manifest is missing {label} commit metadata")
    staged_commit = require_full_git_sha(raw_commit, f"staged {label} commit")
    if staged_commit != expected_commit:
        raise RuntimeError(
            f"staged {label} commit {staged_commit} does not match expected "
            f"release commit {expected_commit}"
        )
    if manifest.get(dirty_key) is not False:
        raise RuntimeError(f"stage manifest does not record a clean {label} checkout")


def verify_release_source_provenance(
    engine_root: Path,
    game_repo: Path,
    stage_manifest: Path,
    expected_engine_commit: str,
    expected_game_commit: str,
) -> None:
    expected_engine_commit = require_full_git_sha(expected_engine_commit, f"expected {ENGINE_LABEL} commit")
    expected_game_commit = require_full_git_sha(expected_game_commit, f"expected {GAME_LABEL} commit")
    engine_root = require_repository(engine_root, f"{ENGINE_LABEL} repository")
    game_repo = require_repository(game_repo, f"{GAME_LABEL} repository")

    verify_repository_commit(engine_root, expected_engine_commit, ENGINE_LABEL)
    verify_repository_commit(game_repo, expected_game_commit, GAME_LABEL)

    manifest = read_stage_manifest(stage_manifest)
    validate_staged_files(manifest, stage_manifest.parent)
    verify_manifest_commit(
        manifest,
        commit_key="projectGitCommit",
        dirty_key="projectGitDirty",
        expected_commit=expected_engine_commit,
        label=ENGINE_LABEL,
    )
    verify_manifest_commit(
        manifest,
        commit_key="gameLibsGitCommit",
        dirty_key="gameLibsGitDirty",
        expected_commit=expected_game_commit,
        label=GAME_LABEL,
    )


def default_game_repo(engine_root: Path) -> Path:
    raw = os.environ.get("OPENETQW_GAME_REPO", "").strip()
    return Path(raw).expanduser() if raw else engine_root.resolve().parent / GAME_LABEL


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--engine-root", required=True, type=Path, help=f"{ENGINE_LABEL} checkout")
    parser.add_argument(
        "--game-repo",
        type=Path,
        default=None,
        help=f"{GAME_LABEL} checkout (default: $OPENETQW_GAME_REPO, else <engine-root>/../{GAME_LABEL})",
    )
    parser.add_argument(
        "--stage-manifest",
        required=True,
        type=Path,
        help="<engine-root>/.tmp/gamelibs_stage/openetqw_gamelibs_stage_manifest.json of the release build",
    )
    parser.add_argument("--expected-engine-commit", required=True)
    parser.add_argument("--expected-game-commit", required=True)
    args = parser.parse_args(argv)
    if args.game_repo is None:
        args.game_repo = default_game_repo(args.engine_root)
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        verify_release_source_provenance(
            args.engine_root,
            args.game_repo,
            args.stage_manifest,
            args.expected_engine_commit,
            args.expected_game_commit,
        )
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(
        "release source provenance verified: "
        f"{ENGINE_LABEL}={args.expected_engine_commit.lower()} "
        f"{GAME_LABEL}={args.expected_game_commit.lower()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
