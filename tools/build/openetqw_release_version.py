#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compute the next openETQW manual release version.

Adapted from openQ4's tools/build/openq4_release_version.py at jm2/openQ4
b37b1456 (GPL-3.0; docs/provenance.md): the same bump modes, tag rules and
key=value output, with the path table mapped to the openETQW tree. openQ4
zero-pads the patch field to three digits (0.1.010); openETQW does not
(v0.1.0, v0.1.1, ...).

The output is written for $GITHUB_OUTPUT:
    python tools/build/openetqw_release_version.py --bump-mode auto >> "$GITHUB_OUTPUT"
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


PRODUCT_NAME = "openETQW"
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
RELEASE_TAG_RE = re.compile(r"^v(\d+\.\d+\.\d+)$")
BUMP_MODE_ALIASES = {
    "auto": "auto",
    "major": "major",
    "major (x..)": "major",
    "minor": "minor",
    "minor (.x.)": "minor",
    "patch": "patch",
    "patch (..x)": "patch",
    "serial": "patch",
    "serial (..x)": "patch",
}


@dataclass(frozen=True)
class ReleaseVersion:
    major: int
    minor: int
    patch: int
    text: str

    @property
    def key(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)


def parse_bump_mode(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in BUMP_MODE_ALIASES:
        raise argparse.ArgumentTypeError(
            "bump mode must be one of: auto, major (x..), minor (.x.), patch (..x)"
        )
    return BUMP_MODE_ALIASES[normalized]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=f"Compute the next manual {PRODUCT_NAME} release version."
    )
    parser.add_argument(
        "--source-root",
        default=".",
        help=f"{PRODUCT_NAME} repository root (default: current directory).",
    )
    parser.add_argument(
        "--current-version",
        default="",
        help="Current configured release version. Defaults to parsing meson.build.",
    )
    parser.add_argument(
        "--version-override",
        default="",
        help="Optional explicit release version that bypasses automatic bump selection.",
    )
    parser.add_argument(
        "--bump-mode",
        type=parse_bump_mode,
        default="auto",
        help="Release bump mode: auto, major (x..), minor (.x.), or patch (..x).",
    )
    return parser.parse_args(argv[1:])


def run_git(source_root: Path, *git_args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(source_root), *git_args],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    return completed.stdout.strip()


def read_project_version(source_root: Path, explicit_current_version: str) -> str:
    if explicit_current_version.strip():
        return explicit_current_version.strip()

    meson_build = source_root / "meson.build"
    text = meson_build.read_text(encoding="utf-8")
    match = re.search(r"version:\s*'([^']+)'", text)
    if match is None:
        raise SystemExit(f"failed to parse project version from {meson_build}")
    return match.group(1).strip()


def parse_version(version_text: str) -> ReleaseVersion:
    normalized = version_text.strip()
    match = VERSION_RE.fullmatch(normalized)
    if match is None:
        raise SystemExit(
            "release version must be a numeric major.minor.patch string "
            f"(for example 0.1.0), got: {version_text!r}"
        )
    major, minor, patch = (int(group) for group in match.groups())
    return ReleaseVersion(major=major, minor=minor, patch=patch, text=normalized)


def list_release_tags(source_root: Path) -> list[tuple[str, ReleaseVersion]]:
    output = run_git(source_root, "tag", "--list", "v*")
    tags: list[tuple[str, ReleaseVersion]] = []
    for raw_tag in output.splitlines():
        tag = raw_tag.strip()
        match = RELEASE_TAG_RE.fullmatch(tag)
        if match is None:
            continue
        tags.append((tag, parse_version(match.group(1))))
    return tags


def find_latest_release_tag(source_root: Path) -> tuple[str, ReleaseVersion] | None:
    release_tags = list_release_tags(source_root)
    if not release_tags:
        return None
    return max(release_tags, key=lambda item: item[1].key)


def count_commits(source_root: Path, range_spec: str) -> int:
    output = run_git(source_root, "rev-list", "--count", range_spec)
    try:
        return int(output) if output else 0
    except ValueError:
        return 0


def collect_changed_files(source_root: Path, range_spec: str) -> list[str]:
    output = run_git(source_root, "diff", "--name-only", range_spec)
    return [line.strip() for line in output.splitlines() if line.strip()]


def collect_diff_totals(source_root: Path, range_spec: str) -> tuple[int, int]:
    output = run_git(source_root, "diff", "--numstat", range_spec)
    additions = 0
    deletions = 0
    for raw_line in output.splitlines():
        parts = raw_line.strip().split("\t", 2)
        if len(parts) < 2:
            continue
        added_raw, deleted_raw = parts[0], parts[1]
        if added_raw.isdigit():
            additions += int(added_raw)
        if deleted_raw.isdigit():
            deletions += int(deleted_raw)
    return additions, deletions


# (prefix or exact path, category, weight, major subsystem). First match wins;
# a prefix ends in '/'. Documentation (docs/ and any *.md) is weight 0 and is
# checked before this table.
CATEGORY_RULES: tuple[tuple[str, str, int, bool], ...] = (
    ("src/framework/", "engine", 4, True),
    ("src/renderer/", "renderer", 4, True),
    ("src/bse/", "bse", 4, True),
    ("src/sys/", "platform", 3, True),
    ("src/sound/", "sound", 3, True),
    ("src/cm/", "collision", 3, True),
    ("src/curl/", "dependencies", 2, False),
    ("src/imgui/", "dependencies", 2, False),
    ("src/libs/", "dependencies", 2, False),
    ("src/openal/", "dependencies", 2, False),
    ("src/", "engine", 3, False),
    ("base/vkprogs/", "shaders", 3, False),
    ("base/glprogs/", "shaders", 3, False),
    ("base/", "content", 1, False),
    ("tests/", "tests", 1, False),
    ("tools/", "build", 2, False),
    (".github/", "workflow", 2, False),
    ("meson.build", "build", 2, False),
    ("meson_options.txt", "build", 2, False),
)

DOC_PREFIXES = ("docs/",)
DOC_SUFFIXES = (".md",)


def classify_path(path: str) -> tuple[str, int, bool]:
    if path.startswith(DOC_PREFIXES) or path.lower().endswith(DOC_SUFFIXES):
        return "docs", 0, False

    for prefix, category, weight, major_subsystem in CATEGORY_RULES:
        if prefix.endswith("/"):
            if path.startswith(prefix):
                return category, weight, major_subsystem
        elif path == prefix:
            return category, weight, major_subsystem

    return "misc", 1, False


def analyze_change_scale(
    changed_files: list[str],
    *,
    commit_count: int,
    additions: int,
    deletions: int,
) -> tuple[str, str, int]:
    if not changed_files:
        return "patch", "No changed files were detected since the previous release tag.", 0

    unique_categories: dict[str, int] = {}
    major_subsystems: set[str] = set()
    docs_only = True
    for path in changed_files:
        category, weight, is_major = classify_path(path)
        unique_categories[category] = max(unique_categories.get(category, 0), weight)
        if weight > 0:
            docs_only = False
        if is_major:
            major_subsystems.add(category)

    if docs_only:
        return "patch", "Only documentation changed since the previous release.", 0

    score = sum(unique_categories.values())
    file_count = len(changed_files)
    churn = additions + deletions

    if file_count >= 10:
        score += 1
    if file_count >= 25:
        score += 2
    if file_count >= 60:
        score += 2

    if churn >= 400:
        score += 1
    if churn >= 1500:
        score += 2
    if churn >= 5000:
        score += 3

    if commit_count >= 10:
        score += 1
    if commit_count >= 25:
        score += 2

    if len(major_subsystems) >= 3:
        score += 2

    category_summary = ", ".join(sorted(category for category in unique_categories if category != "docs"))
    reason = (
        f"Changed {file_count} files across {category_summary or 'misc'} with "
        f"{commit_count} commits and {churn} lines of diff."
    )

    if score >= 12 or (len(major_subsystems) >= 3 and churn >= 1200):
        return "minor", reason, score
    return "patch", reason, score


def emit_metadata(metadata: dict[str, str | int]) -> None:
    for key, value in metadata.items():
        print(f"{key}={value}")


def release_metadata(
    *,
    current_version: ReleaseVersion,
    latest_release: tuple[str, ReleaseVersion] | None,
    version: str,
    release_tag: str,
    release_scale: str,
    release_reason: str,
    bump_mode: str,
    analysis_score: int = 0,
    commit_count: int = 0,
    changed_files: int = 0,
    added_lines: int = 0,
    deleted_lines: int = 0,
) -> dict[str, str | int]:
    return {
        "current_version": current_version.text,
        "latest_release_tag": latest_release[0] if latest_release else "",
        "latest_release_version": latest_release[1].text if latest_release else "",
        "version": version,
        "version_tag": version,
        "release_tag": release_tag,
        "release_name": f"{PRODUCT_NAME} {version}",
        "release_scale": release_scale,
        "release_reason": release_reason,
        "bump_mode": bump_mode,
        "analysis_score": analysis_score,
        "commit_count": commit_count,
        "changed_files": changed_files,
        "added_lines": added_lines,
        "deleted_lines": deleted_lines,
    }


def validate_version_override(
    override_version: ReleaseVersion,
    current_version: ReleaseVersion,
    latest_release: tuple[str, ReleaseVersion] | None,
) -> None:
    if override_version.key < current_version.key:
        raise SystemExit(
            "version override must not be lower than the configured project version "
            f"{current_version.text}, got: {override_version.text}"
        )

    if latest_release is None:
        return

    latest_tag, latest_version = latest_release
    if override_version.key < latest_version.key:
        raise SystemExit(
            "version override must not be lower than the latest published release "
            f"{latest_tag} ({latest_version.text}), got: {override_version.text}"
        )


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    source_root = Path(args.source_root).resolve()

    current_version = parse_version(read_project_version(source_root, args.current_version))
    latest_release = find_latest_release_tag(source_root)

    version_override = args.version_override.strip()
    if version_override:
        override_version = parse_version(version_override)
        validate_version_override(override_version, current_version, latest_release)
        emit_metadata(release_metadata(
            current_version=current_version,
            latest_release=latest_release,
            version=override_version.text,
            release_tag=f"v{override_version.text}",
            release_scale="override",
            release_reason="Version override was supplied manually.",
            bump_mode=args.bump_mode,
        ))
        return 0

    if latest_release is None:
        emit_metadata(release_metadata(
            current_version=current_version,
            latest_release=None,
            version=current_version.text,
            release_tag=f"v{current_version.text}",
            release_scale="floor",
            release_reason=(
                f"No published v* release tag was found, so the configured version "
                f"{current_version.text} becomes the first manual release."
            ),
            bump_mode=args.bump_mode,
        ))
        return 0

    latest_tag, latest_version = latest_release
    if current_version.key > latest_version.key:
        emit_metadata(release_metadata(
            current_version=current_version,
            latest_release=latest_release,
            version=current_version.text,
            release_tag=f"v{current_version.text}",
            release_scale="floor",
            release_reason=(
                f"The configured repo version {current_version.text} is ahead of "
                f"the latest published release {latest_tag}, so the repo version is used."
            ),
            bump_mode=args.bump_mode,
        ))
        return 0

    range_spec = f"{latest_tag}..HEAD"
    commit_count = count_commits(source_root, range_spec)
    changed_files = collect_changed_files(source_root, range_spec)
    additions, deletions = collect_diff_totals(source_root, range_spec)

    if args.bump_mode == "auto" and commit_count == 0:
        emit_metadata(release_metadata(
            current_version=current_version,
            latest_release=latest_release,
            version=latest_version.text,
            release_tag=latest_tag,
            release_scale="current",
            release_reason=(
                f"No commits were found since {latest_tag}, so the existing release version is reused."
            ),
            bump_mode=args.bump_mode,
        ))
        return 0

    if args.bump_mode in ("major", "minor", "patch"):
        release_scale = args.bump_mode
        release_reason = f"{args.bump_mode.capitalize()} bump was selected manually."
        analysis_score = 0
    else:
        release_scale, release_reason, analysis_score = analyze_change_scale(
            changed_files,
            commit_count=commit_count,
            additions=additions,
            deletions=deletions,
        )

    base = latest_version
    if release_scale == "major":
        next_version = f"{base.major + 1}.0.0"
    elif release_scale == "minor":
        next_version = f"{base.major}.{base.minor + 1}.0"
    else:
        next_version = f"{base.major}.{base.minor}.{base.patch + 1}"

    emit_metadata(release_metadata(
        current_version=current_version,
        latest_release=latest_release,
        version=next_version,
        release_tag=f"v{next_version}",
        release_scale=release_scale,
        release_reason=release_reason,
        bump_mode=args.bump_mode,
        analysis_score=analysis_score,
        commit_count=commit_count,
        changed_files=len(changed_files),
        added_lines=additions,
        deleted_lines=deletions,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
