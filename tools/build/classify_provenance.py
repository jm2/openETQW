#!/usr/bin/env python3
"""Classify 100% of imported ETQW files into the five canonical provenance tiers."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import zipfile
from pathlib import Path

RE_PATTERNS = [
    re.compile(r"hex-rays", re.IGNORECASE),
    re.compile(r"quakewars-hexrays", re.IGNORECASE),
    re.compile(r"pdb\b", re.IGNORECASE),
    re.compile(r"disassembl", re.IGNORECASE),
    re.compile(r"retail executable", re.IGNORECASE),
]

DOOM3_GPL_PATTERN = re.compile(
    r"GNU General Public License.*version 3|subject to certain additional terms",
    re.IGNORECASE | re.DOTALL,
)

THIRD_PARTY_DIRS = (
    "src/curl",
    "src/imgui",
    "src/recast",
    "src/openal",
    "src/libs/zlib",
    "src/libs/ogg",
    "src/libs/libvorbis",
    "src/libs/speex",
    "src/sys/sdl2",
)


def compute_hashes(path: Path) -> tuple[str, str, int]:
    raw_data = path.read_bytes()
    raw_sha256 = hashlib.sha256(raw_data).hexdigest()
    try:
        text = raw_data.decode("utf-8")
        canonical_data = text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    except UnicodeDecodeError:
        canonical_data = raw_data
    canonical_sha256 = hashlib.sha256(canonical_data).hexdigest()
    return raw_sha256, canonical_sha256, len(raw_data)


def load_sdk_comparison_manifest(bundle_path: Path) -> dict[str, dict]:
    sdk_files = {}
    with zipfile.ZipFile(bundle_path) as z:
        with z.open("comparison_files.csv") as f:
            reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8"))
            for row in reader:
                recon_path = row["reconstruction_path"]
                if recon_path:
                    sdk_files[recon_path] = row
    return sdk_files


def classify_file(rel_path: str, file_path: Path, sdk_files: dict[str, dict]) -> tuple[str, str, str]:
    src_rel = rel_path[4:] if rel_path.startswith("src/") else rel_path

    # Tier 1: SDK match
    if src_rel in sdk_files or rel_path in sdk_files:
        return "SDK", "LicenseRef-ETQW-SDK", "openETQW-game"

    # Tier 3: Third Party
    for tp in THIRD_PARTY_DIRS:
        if rel_path.startswith(tp):
            return "ThirdParty", "Upstream", "openETQW"

    # Inspect file header/content
    header = ""
    try:
        with file_path.open("r", encoding="utf-8", errors="ignore") as f:
            header = f.read(8192)
    except Exception:
        pass

    # Tier 4: Reconstruction Retail RE
    if any(rx.search(header) for rx in RE_PATTERNS):
        return "Reconstruction_retail_RE", "GPL-3.0-or-later", "openETQW"

    # Tier 2: Doom 3 GPL
    if DOOM3_GPL_PATTERN.search(header):
        return "Doom3_GPL", "GPL-3.0-or-later", "openETQW"

    # Tier 5: Reconstruction Original
    # Exception: compiledscript belongs to game repo
    target_repo = "openETQW-game" if rel_path.startswith("src/compiledscript") else "openETQW"
    license_type = "MIT" if rel_path.startswith("src/compiledscript") else "GPL-3.0-or-later"
    return "Reconstruction_original", license_type, target_repo


def main() -> int:
    parser = argparse.ArgumentParser(description="Classify ETQW codebase provenance")
    parser.add_argument("source_root", type=Path, help="Path to ~/etqw or source root")
    parser.add_argument("bundle_path", type=Path, help="Path to etqw_sdk_comparison_bundle.zip")
    parser.add_argument("--output-json", type=Path, default=Path("docs/provenance-manifest.json"))
    args = parser.parse_args()

    sdk_files = load_sdk_comparison_manifest(args.bundle_path)
    records = []
    counts = {"SDK": 0, "Doom3_GPL": 0, "ThirdParty": 0, "Reconstruction_retail_RE": 0, "Reconstruction_original": 0}

    for dirpath, dirnames, filenames in os.walk(args.source_root):
        if ".git" in dirnames:
            dirnames.remove(".git")
        for filename in filenames:
            file_path = Path(dirpath) / filename
            rel_path = file_path.relative_to(args.source_root).as_posix()
            tier, license_id, target_repo = classify_file(rel_path, file_path, sdk_files)
            raw_hash, canon_hash, byte_len = compute_hashes(file_path)

            counts[tier] += 1
            records.append({
                "path": rel_path,
                "tier": tier,
                "license": license_id,
                "targetRepo": target_repo,
                "rawSha256": raw_hash,
                "canonicalSha256": canon_hash,
                "bytes": byte_len,
            })

    output_data = {
        "schemaVersion": 1,
        "totalFiles": len(records),
        "counts": counts,
        "files": sorted(records, key=lambda r: r["path"]),
    }

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(output_data, indent=2) + "\n", encoding="utf-8")
    print(f"Classified {len(records)} files: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
