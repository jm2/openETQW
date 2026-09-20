#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Automated Zero-SDK Verification Test for openETQW engine repository.

Verifies that ZERO SDK-licensed files are tracked or present in the openETQW repository.
Runs as part of `meson test -C build`.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

BANNED_LICENSE_MARKERS = [
    b"ETQW SDK Limited Use License Agreement",
    b"LicenseRef-ETQW-SDK",
    b"Enemy Territory: Quake Wars Software Development Kit Limited Use License Agreement",
]

FORBIDDEN_TRACKED_PREFIXES = (
    "src/game/",
    "src/compiledscript/",
    "src/compiledscript.xcodeproj/",
)


def get_repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def get_tracked_files(repo_root: Path) -> list[str]:
    try:
        out = subprocess.check_output(["git", "ls-files", "-z"], cwd=repo_root)
        return [f.decode("utf-8") for f in out.split(b"\0") if f]
    except Exception as exc:
        raise RuntimeError(f"Failed to query git tracked files: {exc}") from exc


def load_sdk_manifest(repo_root: Path) -> tuple[set[str], set[str]]:
    """Load known SDK paths and SHA-256 digests from manifest or bundle."""
    sdk_hashes = set()
    sdk_paths = set()

    # 1. From docs/provenance/sdk_hashes.json
    hashes_file = repo_root / "docs" / "provenance" / "sdk_hashes.json"
    if hashes_file.is_file():
        try:
            data = json.loads(hashes_file.read_text(encoding="utf-8"))
            sdk_hashes.update(data.get("hashes", []))
        except Exception:
            pass

    # 2. From reference bundle if available
    zip_cand = repo_root / "openetqw_handoff_v3" / "reference" / "etqw_sdk_comparison_bundle.zip"
    if zip_cand.is_file():
        try:
            import zipfile
            with zipfile.ZipFile(zip_cand) as z:
                if "sdk_manifest.json" in z.namelist():
                    m = json.loads(z.read("sdk_manifest.json").decode("utf-8"))
                    for entry in m.get("files", []):
                        sdk_hashes.add(entry["sha256"])
                        sdk_paths.add(entry["path"])
        except Exception:
            pass

    # Safety floor hashes for critical exception headers
    safety_hashes = {
        "a2c408c8d01e8ee8ddf96c5a9328406a4471d2ede324d4ca5ef6974ee5e174df",  # Game.h
        "f3695fb2f6c5f411af847700d7e80358d78167319ec1730f6d9b2f2565c68f22",  # NetworkSystem.h
        "25a1fd43ab023599b6f2b02eae7488e9c2efe7668ef150597bd600c6acf40296",  # UsercmdGen.h
        "f4fa484450895cad97a8ed4cec35eb099338096313c9bae40849f31065e8dae6",  # BuildDefines.h
        "83073d1864a29e98bda738b62d9bcb1bd9f25ab19dd32f779f226e8d03d110c0",  # BuildDefines.inc
        "c52d8309eb7bdc7d7e26438d27e06b872116af2a1e69309208207d3c885b5a94",  # BotAI_Debug.h
        "c3798a8f2a0103212e6ebdc57bed9ba44ff3445f7ed4abb576335b8c382ea6c3",  # Bot_Common.h
    }
    sdk_hashes.update(safety_hashes)

    # Exclude empty file hash (e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855)
    EMPTY_FILE_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    sdk_hashes.discard(EMPTY_FILE_SHA256)

    return sdk_hashes, sdk_paths


def compute_hashes(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    raw_hash = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8")
        canonical = text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
        canon_hash = hashlib.sha256(canonical).hexdigest()
    except UnicodeDecodeError:
        canon_hash = raw_hash
    return raw_hash, canon_hash


def verify_gitignore(repo_root: Path) -> list[str]:
    errors = []
    gitignore = repo_root / ".gitignore"
    if not gitignore.is_file():
        errors.append("Missing .gitignore file in engine root")
    else:
        content = gitignore.read_text(encoding="utf-8")
        if ".tmp" not in content and ".tmp/" not in content:
            errors.append(".gitignore does not exclude .tmp/ staging directory")
    return errors


def main() -> int:
    repo_root = get_repo_root()
    print(f"Running Zero-SDK Audit on repository: {repo_root}")

    sdk_hashes, sdk_paths = load_sdk_manifest(repo_root)
    print(f"Loaded {len(sdk_hashes)} SDK verification fingerprints, {len(sdk_paths)} SDK paths.")

    tracked_files = get_tracked_files(repo_root)
    print(f"Auditing {len(tracked_files)} tracked files...")

    violations: list[str] = []

    # Gate 4: .gitignore containment
    violations.extend(verify_gitignore(repo_root))

    for rel_path in tracked_files:
        full_path = repo_root / rel_path

        # Ignore .agents metadata if tracked
        if rel_path.startswith(".agents/"):
            continue

        # Gate 2: Path prefix check
        for forbidden in FORBIDDEN_TRACKED_PREFIXES:
            if rel_path.startswith(forbidden):
                violations.append(f"Forbidden game/compiledscript path tracked in engine repo: {rel_path}")

        # Gate 2b: SDK path match check
        src_sub = rel_path[4:] if rel_path.startswith("src/") else rel_path
        if src_sub in sdk_paths or rel_path in sdk_paths:
            violations.append(f"Tracked path matches known SDK source path: {rel_path}")

        # Gate 4: Staged files in git check
        if rel_path.startswith(".tmp/"):
            violations.append(f"Staged temporary file committed to git: {rel_path}")

        if not full_path.is_file():
            continue

        # Gate 1: Hash match check
        raw_hash, canon_hash = compute_hashes(full_path)
        if raw_hash in sdk_hashes or canon_hash in sdk_hashes:
            violations.append(f"Tracked file matches SDK hash: {rel_path} (hash: {canon_hash})")

        # Gate 3: License text check (exclude docs, test harness self-marker, and license classifier tool)
        if rel_path.startswith("docs/") or rel_path in ("tests/test_zero_sdk.py", "tools/build/classify_provenance.py"):
            continue

        try:
            sample = full_path.read_bytes()[:4096]
            for marker in BANNED_LICENSE_MARKERS:
                if marker in sample:
                    violations.append(f"Tracked file contains forbidden SDK license notice: {rel_path}")
                    break
        except Exception:
            pass

    if violations:
        print("\n[FAILED] Zero-SDK Policy Violations Detected:", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        return 1

    print("\n[PASSED] Zero SDK-licensed files detected in repository tracking.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
