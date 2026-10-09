#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Update tools/ci/companion.ref to a newer openETQW-game commit.

CI builds the engine against the exact openETQW-game commit recorded in
tools/ci/companion.ref (see tools/ci/fetch_companion.py). Run this after
pushing game-repo changes the engine depends on, then commit the new pin
together with the engine change that needs it.

The target commit is resolved, in order of preference:

  --sha SHA        use this exact 40-character commit (no lookup at all)
  --local PATH     resolve --ref inside an existing local clone, without any
                   network access (e.g. --local ../openETQW-game --ref origin/main;
                   run 'git -C ../openETQW-game fetch origin' first)
  (default)        'git ls-remote' against the companion repository for --ref
                   (default: main). This is a developer tool, so it uses your
                   normal git credentials (keychain, credential manager, ...).

Examples
  python3 tools/ci/bump_companion.py                      # pin origin main
  python3 tools/ci/bump_companion.py --ref my-branch      # pin a branch head
  python3 tools/ci/bump_companion.py --local ../openETQW-game --ref origin/main
  python3 tools/ci/bump_companion.py --check              # exit 1 if main moved

Exit codes: 0 pin updated / already current, 1 error or (--check) pin is
stale, 2 usage error.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPO = "jm2/openETQW-game"
DEFAULT_PIN_FILE = ROOT / "tools" / "ci" / "companion.ref"
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class BumpError(Exception):
    pass


def git(args: list[str], cwd: Optional[Path] = None, timeout: float = 120.0) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise BumpError("git executable not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise BumpError(f"git {' '.join(args[:2])} timed out") from exc


def read_pin(pin_file: Path) -> str:
    if not pin_file.is_file():
        return ""
    values = [
        line.strip().lower()
        for line in pin_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    return values[0] if len(values) == 1 and FULL_SHA_RE.match(values[0]) else ""


def resolve_remote(url: str, ref: str) -> str:
    if FULL_SHA_RE.match(ref.lower()):
        return ref.lower()
    candidates = [ref] if ref.startswith("refs/") else [f"refs/heads/{ref}", f"refs/tags/{ref}^{{}}", f"refs/tags/{ref}"]
    result = git(["ls-remote", url, *candidates])
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()[-1:] or ["unknown error"]
        raise BumpError(f"git ls-remote {url} failed: {detail[0]}")
    refs: dict[str, str] = {}
    for line in result.stdout.splitlines():
        sha, _, name = line.partition("\t")
        refs[name.strip()] = sha.strip().lower()
    for candidate in candidates:
        if candidate in refs:
            return refs[candidate]
    raise BumpError(f"{ref!r} was not found in {url}")


def resolve_local(clone: Path, ref: str) -> str:
    if not (clone / ".git").exists():
        raise BumpError(f"not a git clone: {clone}")
    result = git(["-C", str(clone), "rev-parse", "--verify", f"{ref}^{{commit}}"])
    if result.returncode != 0:
        raise BumpError(f"cannot resolve {ref!r} in {clone}: {result.stderr.strip()}")
    return result.stdout.strip().lower()


def describe(clone: Optional[Path], old: str, new: str) -> None:
    """Best-effort changelog between the old and new pin from a local clone."""
    if clone is None or not (clone / ".git").exists():
        return
    have_both = all(
        git(["-C", str(clone), "cat-file", "-e", f"{sha}^{{commit}}"]).returncode == 0 for sha in (old, new) if sha
    )
    if not have_both or not old:
        return
    log = git(["-C", str(clone), "log", "--oneline", "--no-decorate", f"{old}..{new}"])
    if log.returncode == 0 and log.stdout.strip():
        lines = log.stdout.strip().splitlines()
        print(f"  {len(lines)} new commit(s):")
        for line in lines[:40]:
            print(f"    {line}")
        if len(lines) > 40:
            print(f"    ... {len(lines) - 40} more")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", default=DEFAULT_REPO, help=f"companion OWNER/NAME (default {DEFAULT_REPO})")
    parser.add_argument("--url", default=None, help="full clone URL (default https://github.com/<repo>.git)")
    parser.add_argument("--ref", default=None, help="branch, tag or ref to pin (default: main, or origin/main with --local)")
    parser.add_argument("--sha", default=None, help="pin this exact 40-character commit SHA")
    parser.add_argument("--local", type=Path, default=None, help="resolve --ref in this local clone (no network)")
    parser.add_argument("--pin-file", type=Path, default=DEFAULT_PIN_FILE, help="pin file to update")
    parser.add_argument("--check", action="store_true", help="do not write; exit 1 when the pin differs from the target")
    parser.add_argument("--dry-run", action="store_true", help="print the change without writing it")
    args = parser.parse_args(argv)
    if args.sha and (args.local or args.ref):
        parser.error("--sha cannot be combined with --ref or --local")
    if args.sha and not FULL_SHA_RE.match(args.sha.lower()):
        parser.error("--sha must be a full 40-character commit SHA")
    if args.ref is None:
        args.ref = "origin/main" if args.local else "main"
    return args


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    url = args.url or f"https://github.com/{args.repo}.git"
    try:
        if args.sha:
            target, how = args.sha.lower(), "explicit --sha"
        elif args.local:
            target, how = resolve_local(args.local.resolve(), args.ref), f"{args.ref} in {args.local}"
        else:
            target, how = resolve_remote(url, args.ref), f"{args.ref} at {url}"
    except BumpError as exc:
        print(f"bump_companion: ERROR: {exc}", file=sys.stderr)
        return 1
    if not FULL_SHA_RE.match(target):
        print(f"bump_companion: ERROR: resolved value is not a commit SHA: {target!r}", file=sys.stderr)
        return 1

    current = read_pin(args.pin_file)
    try:
        shown_pin = args.pin_file.resolve().relative_to(ROOT)
    except ValueError:
        shown_pin = args.pin_file
    if current == target:
        print(f"bump_companion: {shown_pin} already pins {target} ({how})")
        return 0

    print(f"bump_companion: {shown_pin}: {current or '<none>'} -> {target} ({how})")
    default_clone = Path(os.environ.get("OPENETQW_GAME_REPO", "").strip() or ROOT.parent / "openETQW-game")
    describe(args.local.resolve() if args.local else default_clone, current, target)
    if args.check:
        print("bump_companion: pin is stale (--check)")
        return 1
    if args.dry_run:
        print("bump_companion: dry run, nothing written")
        return 0
    args.pin_file.parent.mkdir(parents=True, exist_ok=True)
    # LF on every host: write_text would write CRLF on Windows
    with args.pin_file.open("w", encoding="utf-8", newline="\n") as pin:
        pin.write(target + "\n")
    print(f"bump_companion: wrote {shown_pin}; commit it with the engine change that needs it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
