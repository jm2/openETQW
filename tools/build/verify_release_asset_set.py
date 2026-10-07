#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Verify and record the exact file set approved for GitHub release upload.

Adapted from openQ4's tools/build/verify_release_asset_set.py at jm2/openQ4
b37b1456 (GPL-3.0; docs/provenance.md): the same exact-whitelist rule, archive
integrity checks and post-upload size/SHA-256 check, for openetqw-* assets.
Changes from the donor: every archive member path is checked (no absolute
paths, no '..', no links that leave the archive, no device files), every asset
must be non-empty, and the manifest is JSON with each asset's name, size and
SHA-256 (the donor writes a plain list of names).

Before upload:
    verify_release_asset_set.py --artifact-dir DIR --manifest OUT.json --expected NAME ...
After upload (GitHub REST "list release assets" JSON):
    verify_release_asset_set.py --artifact-dir DIR --published-assets ASSETS.json --expected NAME ...
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import lzma
import os
import posixpath
import re
import stat
import sys
import tarfile
from pathlib import Path
from zipfile import BadZipFile, ZipFile


MAX_RELEASE_ASSETS = 64
MAX_ARCHIVE_EXPANDED_BYTES = 16 * 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 100000
MANIFEST_FORMAT = 1
SAFE_ASSET_NAME_RE = re.compile(r"openetqw-[A-Za-z0-9][A-Za-z0-9._+-]{0,248}")
WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def _lstat(path: Path, label: str) -> os.stat_result:
    try:
        return path.lstat()
    except OSError as exc:
        raise RuntimeError(f"{label} is unavailable: {path}") from exc


def require_directory(path: Path, label: str) -> Path:
    path_stat = _lstat(path, label)
    if stat.S_ISLNK(path_stat.st_mode):
        raise RuntimeError(f"{label} must not be a symlink: {path}")
    if not stat.S_ISDIR(path_stat.st_mode):
        raise RuntimeError(f"{label} must be a directory: {path}")
    return path


def require_regular_file(path: Path, label: str) -> os.stat_result:
    path_stat = _lstat(path, label)
    if stat.S_ISLNK(path_stat.st_mode):
        raise RuntimeError(f"{label} must not be a symlink: {path}")
    if not stat.S_ISREG(path_stat.st_mode):
        raise RuntimeError(f"{label} must be a regular file: {path}")
    return path_stat


def validate_asset_name(value: str) -> str:
    if (
        not value
        or SAFE_ASSET_NAME_RE.fullmatch(value) is None
        or ".." in value
        or "/" in value
        or "\\" in value
    ):
        raise RuntimeError(f"unsafe expected release artifact name: {value!r}")
    return value


def validate_expected_names(expected_names: list[str]) -> tuple[str, ...]:
    if not expected_names:
        raise RuntimeError("release artifact whitelist must not be empty")
    if len(expected_names) > MAX_RELEASE_ASSETS:
        raise RuntimeError(f"release artifact whitelist exceeds {MAX_RELEASE_ASSETS} entries")

    validated = tuple(validate_asset_name(value) for value in expected_names)
    duplicates = sorted({value for value in validated if validated.count(value) > 1})
    if duplicates:
        raise RuntimeError("release artifact whitelist contains duplicate names: " + ", ".join(duplicates))
    return validated


def check_member_path(archive_name: str, member_name: str) -> str:
    """Reject member paths that would land outside the extraction directory."""
    normalized = member_name.replace("\\", "/")
    parts = normalized.split("/")
    if (
        not normalized
        or "\x00" in normalized
        or normalized.startswith("/")
        or WINDOWS_DRIVE_RE.match(normalized)
        or ".." in parts
    ):
        raise RuntimeError(f"release archive has an unsafe member path: {archive_name}: {member_name!r}")
    return normalized


def check_tar_member(archive_name: str, member: tarfile.TarInfo) -> None:
    name = check_member_path(archive_name, member.name)
    if member.isfile() or member.isdir():
        return
    if member.issym():
        # A relative link must resolve inside the archive root.
        target = member.linkname.replace("\\", "/")
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
        if (
            not target
            or target.startswith("/")
            or WINDOWS_DRIVE_RE.match(target)
            or resolved == ".."
            or resolved.startswith("../")
        ):
            raise RuntimeError(
                f"release archive has a symlink leaving the archive: {archive_name}: "
                f"{member.name!r} -> {member.linkname!r}"
            )
        return
    if member.islnk():
        check_member_path(archive_name, member.linkname)
        return
    raise RuntimeError(f"release archive has a special-file member: {archive_name}: {member.name!r}")


def verify_archive_integrity(path: Path) -> None:
    """Read the entire payload, including compression trailers, before upload."""
    try:
        if path.name.endswith(".zip"):
            with ZipFile(path) as archive:
                members = archive.infolist()
                if len(members) > MAX_ARCHIVE_MEMBERS or sum(
                    member.file_size for member in members
                ) > MAX_ARCHIVE_EXPANDED_BYTES:
                    raise RuntimeError(f"release archive exceeds validation limits: {path.name}")
                for member in members:
                    check_member_path(path.name, member.filename)
                bad_member = archive.testzip()
                if bad_member is not None:
                    raise RuntimeError(f"release archive has a corrupt member: {path.name}: {bad_member}")
        elif path.name.endswith((".tar.gz", ".tar.xz")):
            opener = gzip.open if path.name.endswith(".tar.gz") else lzma.open
            # Tar readers can stop at the end-of-archive blocks without checking
            # the gzip/xz footer. Explicitly drain the compressed stream first.
            expanded = 0
            with opener(path, "rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    expanded += len(chunk)
                    if expanded > MAX_ARCHIVE_EXPANDED_BYTES:
                        raise RuntimeError(f"release archive exceeds validation limits: {path.name}")
            with tarfile.open(path, "r|*") as archive:
                for index, member in enumerate(archive):
                    if index >= MAX_ARCHIVE_MEMBERS:
                        raise RuntimeError(f"release archive has too many members: {path.name}")
                    check_tar_member(path.name, member)
                    if member.isfile():
                        with archive.extractfile(member) as stream:
                            while stream.read(1024 * 1024):
                                pass
    except (OSError, EOFError, tarfile.TarError, BadZipFile, lzma.LZMAError) as exc:
        raise RuntimeError(f"release archive is truncated or corrupt: {path.name}: {exc}") from exc


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def verify_published_assets(artifact_dir: Path, expected_names: tuple[str, ...], published: list[dict]) -> None:
    """Ensure GitHub stored the exact bytes that passed local validation."""
    if (
        not isinstance(published, list)
        or not all(isinstance(item, dict) for item in published)
        or len(published) != len(expected_names)
        or {item.get("name") for item in published} != set(expected_names)
    ):
        raise RuntimeError("published release assets do not match the approved whitelist")
    by_name = {item["name"]: item for item in published}
    for name in expected_names:
        path = artifact_dir / name
        size = require_regular_file(path, "approved release artifact").st_size
        asset = by_name[name]
        if asset.get("size") != size or asset.get("digest") != f"sha256:{file_sha256(path)}":
            raise RuntimeError(f"published release asset size/SHA-256 mismatch: {name}")


def verify_asset_set(artifact_dir: Path, expected_names: list[str]) -> tuple[str, ...]:
    expected = validate_expected_names(expected_names)
    expected_set = set(expected)
    require_directory(artifact_dir, "release artifact directory")

    try:
        entries = list(os.scandir(artifact_dir))
    except OSError as exc:
        raise RuntimeError(f"release artifact directory could not be enumerated: {artifact_dir}") from exc

    actual_names: set[str] = set()
    invalid_entries: list[str] = []
    for entry in entries:
        try:
            is_regular = entry.is_file(follow_symlinks=False)
            is_symlink = entry.is_symlink()
        except OSError as exc:
            raise RuntimeError(f"release artifact changed during validation: {entry.name}") from exc
        if is_symlink or not is_regular:
            invalid_entries.append(entry.name)
            continue
        actual_names.add(entry.name)

    missing = sorted(expected_set - actual_names)
    unexpected = sorted(actual_names - expected_set)
    if missing or unexpected or invalid_entries:
        details: list[str] = []
        if missing:
            details.append("missing: " + ", ".join(repr(value) for value in missing))
        if unexpected:
            details.append("unexpected: " + ", ".join(repr(value) for value in unexpected))
        if invalid_entries:
            details.append("non-regular: " + ", ".join(repr(value) for value in sorted(invalid_entries)))
        raise RuntimeError(
            "release artifact set does not match the exact approved whitelist (" + "; ".join(details) + ")"
        )

    for name in expected:
        if require_regular_file(artifact_dir / name, f"approved release artifact {name}").st_size == 0:
            raise RuntimeError(f"approved release artifact is empty: {name}")
        verify_archive_integrity(artifact_dir / name)
    return expected


def write_asset_manifest(output: Path, artifact_dir: Path, expected_names: tuple[str, ...]) -> None:
    require_directory(output.parent, "release asset manifest directory")
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"release asset manifest output must not already exist: {output}")
    assets = []
    for name in expected_names:
        path = artifact_dir / name
        size = require_regular_file(path, f"approved release artifact {name}").st_size
        assets.append({"name": name, "size": size, "sha256": file_sha256(path)})
    contents = json.dumps({"format": MANIFEST_FORMAT, "assets": assets}, indent=2) + "\n"
    try:
        with output.open("x", encoding="utf-8", newline="\n") as file_handle:
            file_handle.write(contents)
    except OSError as exc:
        raise RuntimeError(f"release asset manifest could not be written: {output}") from exc


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--artifact-dir", required=True, type=Path)
    output = parser.add_mutually_exclusive_group(required=True)
    output.add_argument("--manifest", type=Path, help="JSON manifest (name, size, sha256) to create")
    output.add_argument("--published-assets", type=Path, help="GitHub REST assets JSON to verify after upload")
    parser.add_argument("--expected", required=True, action="append", help="approved asset name (repeat)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        expected = verify_asset_set(args.artifact_dir, args.expected)
        if args.published_assets:
            try:
                published = json.loads(args.published_assets.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"published assets JSON is unreadable: {args.published_assets}") from exc
            verify_published_assets(args.artifact_dir, expected, published)
        else:
            write_asset_manifest(args.manifest, args.artifact_dir, expected)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"release artifact whitelist verified: {len(expected)} files; "
        f"{'published size/SHA-256 verified' if args.published_assets else f'manifest={args.manifest}'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
