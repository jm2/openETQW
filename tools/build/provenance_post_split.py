#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Keep docs/provenance-manifest.json covering the files added after the split.

The manifest's original 5,274 entries classify the files imported at the split
(classify_provenance.py; their hashes are the imported contents).  Files added
to either repository afterwards get an entry here, with "addedAfterSplit": true,
the commit that added the path ("addedIn"), the SPDX tag the file carries
("spdx", null when it has none), and, for a file taken from another project, its
donor.  Their hashes describe the engine files at the commit that last changed
the manifest and the game files at the commit in "postSplitAsOf"; rerun with
--update to refresh them.

  provenance_post_split.py --game-repo ../openETQW-game           # report gaps, exit 1 if any
  provenance_post_split.py --game-repo ../openETQW-game --update  # add/refresh the entries

A file without an SPDX tag is classified by RULES below; a path that neither a
tag nor a rule covers is an error, so a new untagged file needs a decision.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "docs" / "provenance-manifest.json"
SPLIT_COMMITS = {"openETQW": "9043b91", "openETQW-game": "9600d7a"}
SPDX = re.compile(rb"SPDX-License-Identifier:\s*([A-Za-z0-9.+\- ()]+?)\s*(?:\*/|-->|$)", re.M)

OPENQ4 = {"repository": "jm2/openQ4", "commit": "b37b1456f3e400fd4b523090402a6dd1fae2af1b"}
# path -> (tier, license, donor relation); docs/provenance.md, "openQ4-derived engine files"
DONORS = {
    "src/sys/URLPolicy.h": ("Reconstruction_original", "identical"),
    "src/sys/osx/macosx_common.h": ("Reconstruction_original", "identical"),
    "src/sys/osx/macosx_sys.h": ("Reconstruction_original", "identical"),
    "src/sys/osx/macosx_local.h": ("Reconstruction_original", "adapted"),
    "src/sys/osx/macosx_misc.mm": ("Doom3_GPL", "adapted"),
    "src/sys/osx/macosx_compat.mm": ("Doom3_GPL", "adapted"),
}
OPENQ4_TOOLING = {
    # ports of an openQ4 file, and files written after one's design (docs/provenance.md)
    "tools/build/openetqw_version.py": "tools/build/openq4_version.py",
    "tools/build/openetqw_release_version.py": "tools/build/openq4_release_version.py",
    "tools/build/generate_release_changelog.py": "tools/build/generate_release_changelog.py",
    "tools/build/verify_release_asset_set.py": "tools/build/verify_release_asset_set.py",
    "tools/build/verify_release_source_provenance.py": "tools/build/verify_release_source_provenance.py",
    "tools/build/build_windows_installer.py": "tools/build/build_windows_installer.py",
    "tools/build/openETQWInstaller.iss.in": "tools/build/openQ4Installer.iss.in",
    "tools/build/package_linux_appimage.py": "tools/build/package_linux_appimage.py",
    ".github/workflows/manual-release.yml": ".github/workflows/manual-release.yml",
    # engine code adapted from one function of a donor file
    "src/sys/sdl/MouseDelta.h": "src/sys/sdl3/sdl3_backend.cpp",
}

# Untagged files: (repository, path prefix or exact path) -> (tier, license).
# Documentation, records and repository metadata take the repository's licence;
# licence and EULA texts are the texts they reproduce.
RULES = [
    ("openETQW-game", "EULA_SDK_ETQW.txt", ("SDK", "LicenseRef-ETQW-SDK")),
    ("openETQW-game", "legal/", ("SDK", "LicenseRef-ETQW-SDK")),
    ("openETQW-game", "", ("Reconstruction_original", "MIT")),
    ("openETQW", "LICENSES/third-party/minizip.txt", ("ThirdParty", "Zlib")),
    # decision 0004: the retail ET:QW icon and launch splash, and files made from them
    ("openETQW", "assets/icons/", ("Retail_asset", "LicenseRef-ETQW-Retail")),
    ("openETQW", "assets/splash/", ("Retail_asset", "LicenseRef-ETQW-Retail")),
    ("openETQW", "", ("Reconstruction_original", "GPL-3.0-or-later")),
]
TIER_BY_LICENSE = {"LicenseRef-ETQW-SDK": "SDK"}


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args]).decode("utf-8", "replace")


def hashes(data: bytes) -> tuple[str, str]:
    raw = hashlib.sha256(data).hexdigest()
    try:
        text = data.decode("utf-8")
        canonical = text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    except UnicodeDecodeError:
        canonical = data
    return raw, hashlib.sha256(canonical).hexdigest()


def classify(repo_name: str, rel: str, tag: str | None) -> tuple[str, str]:
    if rel in DONORS and repo_name == "openETQW":
        return DONORS[rel][0], tag or ""
    if tag:
        return TIER_BY_LICENSE.get(tag, "Reconstruction_original"), tag
    for name, prefix, result in RULES:
        if name == repo_name and (rel == prefix or (prefix.endswith("/") and rel.startswith(prefix)) or prefix == ""):
            return result
    raise SystemExit(f"error: no SPDX tag and no rule for {repo_name}:{rel}")


def entry_for(repo: Path, repo_name: str, rel: str) -> dict:
    data = (repo / rel).read_bytes()
    match = SPDX.search(data[:4096])
    tag = match.group(1).decode().strip() if match else None
    tier, license_id = classify(repo_name, rel, tag)
    if not license_id:
        raise SystemExit(f"error: {rel} has a donor but no SPDX tag")
    added = git(repo, "log", "--diff-filter=A", "--format=%H", "--", rel).split()
    raw, canonical = hashes(data)
    size: int | None = len(data)
    if repo_name == "openETQW" and repo / rel == MANIFEST:
        raw = canonical = size = None  # the manifest cannot hold its own hash
    entry = {
        "path": rel, "tier": tier, "license": license_id, "targetRepo": repo_name,
        "rawSha256": raw, "canonicalSha256": canonical, "bytes": size,
        "addedAfterSplit": True, "addedIn": added[-1] if added else None, "spdx": tag,
    }
    if repo_name == "openETQW" and rel in DONORS:
        entry["donor"] = dict(OPENQ4, path=rel, relation=DONORS[rel][1])
    elif repo_name == "openETQW" and rel in OPENQ4_TOOLING:
        entry["donor"] = dict(OPENQ4, path=OPENQ4_TOOLING[rel], relation="adapted")
    return entry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--game-repo", type=Path, required=True)
    parser.add_argument("--update", action="store_true", help="add missing entries and refresh post-split ones")
    args = parser.parse_args()
    repos = {"openETQW": ROOT, "openETQW-game": args.game_repo.resolve()}

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    files = manifest["files"]
    imported = [f for f in files if not f.get("addedAfterSplit")]
    imported_keys = {(f["targetRepo"], f["path"]) for f in imported}

    gaps, post = [], []
    for name, repo in repos.items():
        tracked = [p for p in git(repo, "ls-files", "-z").split("\0") if p]
        missing = [p for p in tracked if (name, p) not in imported_keys]
        known = {f["path"] for f in files if f.get("addedAfterSplit") and f["targetRepo"] == name}
        gaps += [(name, p) for p in missing if p not in known]
        if args.update:
            post += [entry_for(repo, name, p) for p in sorted(missing)]
        stale = sorted(known - set(tracked))
        for p in stale:
            print(f"stale post-split entry (no longer tracked): {name}:{p}")

    if not args.update:
        for name, p in gaps:
            print(f"not in the manifest: {name}:{p}")
        print(f"{len(gaps)} tracked file(s) missing from {MANIFEST.relative_to(ROOT)}")
        return 1 if gaps else 0

    manifest = {
        "schemaVersion": manifest["schemaVersion"],
        "totalFiles": len(imported) + len(post),
        "counts": {tier: count for tier, count in manifest["counts"].items()}
        if manifest["counts"] == dict(collections.Counter(f["tier"] for f in imported))
        else dict(collections.Counter(f["tier"] for f in imported)),
        "postSplitCounts": dict(collections.Counter(f["tier"] for f in post)),
        # the engine's entries describe the commit that last changed this file
        "postSplitAsOf": {"openETQW": "the commit that last changed this manifest",
                          "openETQW-game": git(repos["openETQW-game"], "rev-parse", "HEAD").strip()},
        "files": imported + post,
    }
    with MANIFEST.open("w", encoding="utf-8", newline="\n") as out:
        out.write(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    by_repo = collections.Counter(f["targetRepo"] for f in post)
    print(f"post-split entries: {dict(by_repo)}; tiers {manifest['postSplitCounts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
