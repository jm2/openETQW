#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fail if tracked text names a private host, user, address or home path.

The repositories are to go public, so nothing committed may say which personal
machine ran what (owner, 2026-10-29). Name a host by its platform instead
("the Linux x64 host"). This check looks for:

- LAN IPv4 addresses (192.168/16, 172.16/12, 10/8 except 10.0.x.y, which is how
  Windows SDK versions read);
- home paths with a real user name (/home/<name>/, /Users/<name>/,
  C:\\Users\\<name>), placeholders such as <user> or $USER excepted;
- e-mail addresses other than noreply ones and example.com;
- .lan host names;
- the private names in tools/ci/pii_terms.sha256. That file holds only SHA-256
  digests of the lower-cased names (host names, user names), so the list itself
  names nobody. A word matches when its digest, or the digest of one of its
  hyphen-joined parts, is in the list.

Modes:
  (default)    every tracked text file, except vendored trees and the files in
               tools/ci/pii_baseline.txt, which still carry old detail and are
               cleaned in a later pass;
  --diff BASE  only the lines added since BASE (git diff BASE...HEAD), in every
               file including the baseline ones, plus the messages of the commits
               in BASE..HEAD. This is the check that stops new detail.
  --add-term   print the digest line for a name, to append to pii_terms.sha256.

Exit codes: 0 pass, 1 findings.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TERMS_FILE = REPO_ROOT / "tools/ci/pii_terms.sha256"
BASELINE_FILE = REPO_ROOT / "tools/ci/pii_baseline.txt"

# Vendored third-party trees and generated data: their authors' addresses are theirs.
EXCLUDE_PREFIXES = (
    "src/curl/", "src/sys/sdl2/", "src/imgui/", "src/openal/", "src/zlib/", "src/libs/",
    "src/sound/OggVorbis/", "src/sound/ogg/", "src/sound/speex/", "src/recast/",
    "src/framework/minizip/", "docs/provenance-manifest.json", "docs/provenance/",
)
BINARY_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".bmp", ".tga", ".ico", ".dds", ".lib", ".a", ".so", ".dll",
    ".exe", ".zip", ".gz", ".spv", ".pk4", ".wav", ".ogg", ".ttf", ".cur",
)

PRIVATE_IP_RE = re.compile(
    r"(?<![\d.])(?:192\.168|172\.(?:1[6-9]|2\d|3[01])|10\.(?:[1-9]\d?|1\d\d|2[0-4]\d|25[0-5]))"
    r"\.\d{1,3}\.\d{1,3}(?![\d.]*\d)"
)
PLACEHOLDER_USERS = {
    "user", "username", "you", "me", "runner", "runneradmin", "public", "shared", "default",
    "name", "builder", "ubuntu", "admin", "dev", "example",
}
HOME_RE = re.compile(r"(?:/home/|/Users/|[A-Za-z]:\\{1,2}Users\\{1,2})([A-Za-z0-9._-]+)")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
LAN_RE = re.compile(r"\b[A-Za-z0-9-]+\.lan\b")
WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*")


def digest(term: str) -> str:
    return hashlib.sha256(term.strip().lower().encode()).hexdigest()


def load_terms() -> set[str]:
    terms = set()
    for line in TERMS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            terms.add(line)
    return terms


def load_baseline() -> set[str]:
    if not BASELINE_FILE.exists():
        return set()
    return {
        line.strip() for line in BASELINE_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }


# Authors' notices in imported code (id Software, zlib, minizip) are public record.
NOTICE_EMAIL_DOMAINS = (
    "idsoftware.com", "winimage.com", "gzip.org", "alumni.caltech.edu", "compuserve.com",
)


def email_allowed(address: str) -> bool:
    address = address.lower()
    local, _, domain = address.partition("@")
    return ("noreply" in address or domain in ("example.com", "example.org")
            or domain.endswith((".example", ".invalid")) or domain in NOTICE_EMAIL_DOMAINS
            or local in ("git", "user"))


def scan_line(line: str, terms: set[str]) -> list[str]:
    found = []
    for match in PRIVATE_IP_RE.finditer(line):
        found.append(f"LAN address {match.group(0)}")
    for match in HOME_RE.finditer(line):
        name = match.group(1)
        if (name.lower() not in PLACEHOLDER_USERS and name.strip(".")
                and not name.startswith(("$", "%", "<", "{"))):
            found.append("home path with a user name")
    for match in EMAIL_RE.finditer(line):
        if not email_allowed(match.group(0)):
            found.append("e-mail address")
    if LAN_RE.search(line):
        found.append(".lan host name")
    if terms:
        for match in WORD_RE.finditer(line):
            word = match.group(0)
            parts = {word, *word.split("-")}
            # also every run of adjacent parts, so "a-b" in "a-b-c" matches
            pieces = word.split("-")
            for start in range(len(pieces)):
                for end in range(start + 2, len(pieces) + 1):
                    parts.add("-".join(pieces[start:end]))
            if any(digest(part) in terms for part in parts if part):
                found.append("private name (pii_terms.sha256)")
                break
    return found


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    ).stdout


def scannable(path: str) -> bool:
    # this file's self test holds sample findings
    return (not path.startswith(EXCLUDE_PREFIXES) and not path.lower().endswith(BINARY_SUFFIXES)
            and path != "tools/ci/check_no_pii.py")


def scan_tree(terms: set[str]) -> list[str]:
    baseline = load_baseline()
    problems = []
    for path in git("ls-files", "-z").split("\0"):
        if not path or not scannable(path) or path in baseline:
            continue
        try:
            text = (REPO_ROOT / path).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for kind in scan_line(line, terms):
                problems.append(f"{path}:{number}: {kind}")
    return problems


def scan_diff(base: str, terms: set[str]) -> list[str]:
    problems = []
    path = None
    number = 0
    for line in git("diff", "--unified=0", "--no-color", f"{base}...HEAD").splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@"):
            number = int(re.search(r"\+(\d+)", line).group(1)) - 1
        elif line.startswith("+") and path is not None:
            number += 1
            if scannable(path):
                for kind in scan_line(line[1:], terms):
                    problems.append(f"{path}:{number}: {kind}")
    # --first-parent: the branch's own commits, not main's commits a merge brought in
    for commit in git("rev-list", "--first-parent", f"{base}..HEAD").split():
        for line in git("log", "-1", "--format=%B", commit).splitlines():
            for kind in scan_line(line, terms):
                problems.append(f"commit {commit[:10]} message: {kind}")
    return problems


def self_test(terms_digest: str) -> None:
    terms = {terms_digest}
    assert scan_line("server at 192.168.1.20:27733", set())
    assert scan_line("ping 10.12.0.4", set())
    assert not scan_line("Windows SDK 10.0.22621.0", set())
    assert not scan_line("version 1.5.12345.1", set())
    assert scan_line(r"C:\Users\somebody\etqw", set())
    assert not scan_line(r"C:\Users\<name>\etqw and /home/$USER/etqw_data", set())
    assert scan_line("/home/somebody/etqw_data", set())
    assert scan_line("mail somebody@gmail.com", set())
    assert not scan_line("Co-Authored-By: Claude <noreply@anthropic.com>", set())
    assert scan_line("ssh box.lan", set())
    assert scan_line("ran on samplehost-win", terms)
    assert scan_line("ran on SampleHost", terms)
    assert not scan_line("ran on the Linux x64 host", terms)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--diff", metavar="BASE", help="scan only what BASE...HEAD adds")
    parser.add_argument("--add-term", metavar="NAME", help="print the digest line for NAME")
    args = parser.parse_args()
    if args.add_term:
        print(digest(args.add_term))
        return 0
    self_test(digest("samplehost"))
    terms = load_terms()
    problems = scan_diff(args.diff, terms) if args.diff else scan_tree(terms)
    for problem in problems:
        print(problem)
    if problems:
        print(f"FAIL: {len(problems)} line(s) name a private host, user, address or home path. "
              "Name hosts by platform (\"the Linux x64 host\"); see CONTRIBUTING.md § What never goes in.")
        return 1
    print("PASS: no private hosts, users, addresses or home paths"
          + (f" added since {args.diff}" if args.diff else " outside the baseline files"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
