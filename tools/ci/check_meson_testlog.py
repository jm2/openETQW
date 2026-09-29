#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Assert the per-test outcome of a `meson test` run from its JSON log.

`meson test` exits 0 when every selected test passed OR was skipped, so on its
own it cannot prove that the retail-data integration suite skipped (exit 77)
on a hosted runner instead of quietly running against something it found.
This reads the machine-readable log meson writes next to the text log
(<builddir>/meson-logs/<logbase>.json, one JSON object per line) and requires
every result to be one of --expect.

Examples (as used by tools/validation/openetqw_validate.py):

  meson test -C build --suite integration --logbase testlog-integration ...
  check_meson_testlog.py build/meson-logs/testlog-integration.json --expect SKIP

Exit status: 0 every result expected (and at least --min-count tests),
1 an unexpected result or too few tests, 2 usage error or unreadable log.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

RESULTS = ("OK", "SKIP", "FAIL", "EXPECTEDFAIL", "UNEXPECTEDPASS", "TIMEOUT", "INTERRUPT", "ERROR")


def gha(command: str, message: str) -> None:
    escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    if os.environ.get("GITHUB_ACTIONS", "").lower() == "true":
        print(f"::{command}::{escaped}", flush=True)
    else:
        print(f"{command.upper()}: {message}", flush=True)


def load(path: Path) -> list[dict]:
    entries = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{number}: not JSON: {exc}") from exc
        if "name" not in entry or "result" not in entry:
            raise ValueError(f"{path}:{number}: missing 'name' or 'result'")
        entries.append(entry)
    return entries


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("testlog", type=Path, help="meson-logs/<logbase>.json")
    parser.add_argument(
        "--expect",
        action="append",
        choices=RESULTS,
        required=True,
        help="an acceptable result (repeatable), e.g. --expect SKIP",
    )
    parser.add_argument("--min-count", type=int, default=1, help="fail when fewer tests ran (default 1)")
    parser.add_argument("--title", default="", help="heading for the job summary")
    args = parser.parse_args(argv)

    try:
        entries = load(args.testlog)
    except (OSError, ValueError) as exc:
        gha("error", f"check_meson_testlog: cannot read {args.testlog}: {exc}")
        return 2

    expected = set(args.expect)
    unexpected = [entry for entry in entries if entry["result"] not in expected]
    title = args.title or f"meson test results ({args.testlog.name})"
    print(f"check_meson_testlog: {len(entries)} test(s) in {args.testlog}; expecting {'/'.join(sorted(expected))}")
    rows = [f"### {title}", "", "| test | result | exit | seconds |", "| --- | --- | --- | --- |"]
    for entry in entries:
        mark = "ok " if entry["result"] in expected else "BAD"
        code = entry.get("returncode")
        seconds = float(entry.get("duration") or 0.0)
        print(f"  {mark} {entry['result']:14s} exit={code!s:5s} {seconds:7.2f}s  {entry['name']}")
        rows.append(f"| {entry['name']} | {entry['result']} | {code} | {seconds:.2f} |")

    failed = False
    for entry in unexpected:
        failed = True
        gha(
            "error",
            f"{entry['name']}: {entry['result']} (exit {entry.get('returncode')}), expected "
            f"{'/'.join(sorted(expected))}",
        )
    if len(entries) < args.min_count:
        failed = True
        gha("error", f"check_meson_testlog: only {len(entries)} test(s) in {args.testlog}, expected >= {args.min_count}")

    summary = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("\n".join(rows) + "\n\n")
    print(f"check_meson_testlog: {'FAIL' if failed else 'OK'} ({len(unexpected)} unexpected)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
