#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check that every retail-data integration test skips (exit 77) without data.

Hosted CI runners never have retail ETQW data, so every integration test must
exit 77 (the Meson 'exitcode' protocol's SKIP) when ETQW_DATA_PATH is unset or
points at a directory that does not exist, quickly, without a Python
traceback and without starting the engine.

Discovery (union of both, printed with the reason):
  * tests registered in meson.build / tests/meson.build whose suite includes
    'integration' (or 'stock-maps' / 'client');
  * tests/test_*.py that consume retail data (reference ETQW_DATA_PATH,
    etqw_data, pak000, etqw_harness or resolve_data_path).

Isolation: each test runs from a scratch copy of tests/ (src/ and tools/ are
symlinked read-only for tests that inspect them) whose build/ directory holds
*stub* engine binaries. A stub records that it was started and exits 1, so a
test that launches the engine instead of skipping is caught, and no real
server is ever started (safe on developer machines with retail data). HOME is
redirected as well, so per-user defaults cannot find real data.

Per test and scenario ('unset', 'missing-dir') a test passes when it exits 77
within --timeout seconds, prints no traceback and never started a stub.

Known violators can be listed in a baseline file (default
tools/ci/integration_skip_baseline.txt): they are reported as warnings instead
of failing the run, so the check can gate new regressions while existing tests
are fixed. A baseline entry that now passes is reported as a notice (or an
error with --strict-baseline) so the baseline only ever shrinks. The baseline
is only for standalone scripts: a test registered with Meson in an
integration suite must always skip cleanly, and listing one in the baseline
is itself an error.

Hard-coded absolute paths (/Users/..., /home/..., C:\\...) in the tests are
reported as one warning per file (retail data must come from ETQW_DATA_PATH).

Exit status: 0 no unexpected violations, 1 violations, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASELINE = ROOT / "tools" / "ci" / "integration_skip_baseline.txt"
SKIP = 77
INTEGRATION_SUITES = {"integration", "stock-maps", "client"}
DATA_HINTS = re.compile(r"ETQW_DATA_PATH|etqw_data|pak000|etqw_harness|resolve_data_path")
HARDCODED_PATH = re.compile(r"""["'](/Users/[^"']+|/home/[^"']+|[A-Za-z]:\\\\[^"']+)["']""")
NOT_INTEGRATION = {
    # Static/contract checks that never touch retail data.
    "tests/test_zero_sdk.py",
    "tests/test_meson_sources_stress.py",
    "tests/test_win32_platform_stress.py",
    # the build helpers' option handling (meson suite 'unit'); ETQW_DATA_PATH is only an
    # environment value it hands the helpers, which it runs with no engine or data
    "tests/test_build_scripts.py",
    # run the dedicated server on synthetic data only
    "tests/test_retail_install_check.py",
    "tests/test_retired_cvars.py",
    "tests/test_frame_cap_cvars.py",
    "tests/test_default_binds.py",
    # harness unit tests: import etqw_harness, start no engine, read no retail data
    "tests/test_harness_failure_markers.py",
    "tests/test_harness_private_dirs.py",
}
STUB_ARCHES = ("arm64", "x64")
STUB_NAMES = ("openETQW-ded", "openETQW-client")
TRACEBACK = "Traceback (most recent call last)"


@dataclass
class Discovered:
    path: str
    reasons: list[str] = field(default_factory=list)

    @property
    def meson(self) -> bool:
        """Registered with Meson in an integration suite (never baselinable)."""
        return any(reason.startswith("meson ") for reason in self.reasons)


@dataclass
class Outcome:
    test: str
    scenario: str
    exit_code: Optional[int]
    elapsed: float
    timed_out: bool
    traceback: bool
    stub_launched: list[str]
    log: str

    @property
    def ok(self) -> bool:
        return self.exit_code == SKIP and not self.timed_out and not self.traceback and not self.stub_launched

    def reason(self, verbose: bool = False) -> str:
        parts = []
        if self.timed_out:
            parts.append("timed out (did not skip promptly)")
        elif self.exit_code != SKIP:
            parts.append(f"exit {self.exit_code} instead of 77")
        if self.traceback:
            parts.append("Python traceback")
        if self.stub_launched:
            launched = "launched the engine without retail data"
            if verbose:
                binary = Path(self.stub_launched[0].split(" ", 1)[0]).name
                launched += f" ({binary}, {len(self.stub_launched)}x)"
            parts.append(launched)
        return "; ".join(parts) or "ok"


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------

def meson_test_blocks(text: str) -> list[str]:
    """Return the argument text of every top-level-ish test(...) call."""
    blocks = []
    for match in re.finditer(r"(?m)^\s*test\(", text):
        depth, index = 0, match.end() - 1
        while index < len(text):
            char = text[index]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    blocks.append(text[match.end():index])
                    break
            elif char == "#":
                newline = text.find("\n", index)
                index = len(text) if newline < 0 else newline
                continue
            index += 1
    return blocks


def discover_meson(root: Path) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for meson_file in (root / "meson.build", root / "tests" / "meson.build"):
        if not meson_file.is_file():
            continue
        for block in meson_test_blocks(meson_file.read_text(encoding="utf-8", errors="replace")):
            script = re.search(r"'tests'\s*/\s*'([^']+\.py)'", block)
            if not script:
                continue
            suite_match = re.search(r"suite\s*:\s*(\[[^\]]*\]|'[^']*')", block)
            suites = set(re.findall(r"'([^']+)'", suite_match.group(1))) if suite_match else set()
            rel = f"tests/{script.group(1)}"
            if suites & INTEGRATION_SUITES:
                found.setdefault(rel, []).append(
                    f"meson {meson_file.relative_to(root).as_posix()} suite={','.join(sorted(suites))}"
                )
            else:
                found.setdefault(rel, [])  # registered, but not integration
    return found


def discover(root: Path) -> tuple[list[Discovered], dict[str, list[str]]]:
    meson = discover_meson(root)
    tests: dict[str, Discovered] = {}
    for rel, reasons in meson.items():
        if reasons:
            tests[rel] = Discovered(rel, list(reasons))
    for path in sorted((root / "tests").glob("test_*.py")):
        rel = path.relative_to(root).as_posix()
        if rel in NOT_INTEGRATION:
            continue
        hints = sorted(set(DATA_HINTS.findall(path.read_text(encoding="utf-8", errors="replace"))))
        if hints:
            tests.setdefault(rel, Discovered(rel)).reasons.append("uses " + ", ".join(hints))
    hardcoded: dict[str, list[str]] = {}
    for path in sorted((root / "tests").glob("*.py")):
        rel = path.relative_to(root).as_posix()
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if HARDCODED_PATH.search(line) and not line.lstrip().startswith("#"):
                hardcoded.setdefault(rel, []).append(f"{number}: {line.strip()[:160]}")
    return sorted(tests.values(), key=lambda item: item.path), hardcoded


# ---------------------------------------------------------------------------
# sandbox
# ---------------------------------------------------------------------------

STUB_SCRIPT = """#!/bin/sh
# Stub engine binary written by tools/ci/check_integration_skips.py.
printf '%s\\n' "$0 $*" >> "$(dirname "$0")/stub-invocations.log"
echo "OPENETQW CI STUB: not a real engine; the test should have skipped (exit 77) before launching it" >&2
exit 1
"""

STUB_SCRIPT_CMD = """@echo off
rem Stub engine binary written by tools/ci/check_integration_skips.py.
>>"%~dp0stub-invocations.log" echo %~nx0 %*
echo OPENETQW CI STUB: not a real engine; the test should have skipped (exit 77) before launching it 1>&2
exit /b 1
"""

STUB_SUFFIX = ".cmd" if os.name == "nt" else ""


def neutralise_hardcoded_paths(tests_dir: Path, absent: Path) -> int:
    """Point hard-coded absolute paths in the sandbox copies at a missing dir.

    On a developer machine such a path may hold real retail data, which would
    make the run unrepresentative of a hosted runner (where it never exists).
    Only the scratch copies are rewritten; the repository is never touched."""
    rewritten = 0

    def replace(match: "re.Match[str]") -> str:
        nonlocal rewritten
        rewritten += 1
        quote = match.group(0)[0]
        return f"{quote}{absent.as_posix()}{quote}"

    for path in tests_dir.glob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        updated = HARDCODED_PATH.sub(replace, text)
        if updated != text:
            path.write_text(updated, encoding="utf-8")
    return rewritten


def build_sandbox(root: Path, sandbox: Path, keep_hardcoded: bool) -> Path:
    shutil.copytree(
        root / "tests",
        sandbox / "tests",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        symlinks=False,
    )
    if not keep_hardcoded:
        count = neutralise_hardcoded_paths(sandbox / "tests", sandbox / "hardcoded-path-not-on-this-host")
        if count:
            print(f"check_integration_skips: sandbox copies: {count} hard-coded absolute path(s) "
                  "pointed at a missing directory (as on a hosted runner)")
    for name in ("src", "tools", "meson.build", "meson_options.txt"):
        source = root / name
        if source.exists():
            link = sandbox / name
            try:
                link.symlink_to(source, target_is_directory=source.is_dir())
            except OSError:
                if source.is_dir():
                    shutil.copytree(source, link)
                else:
                    shutil.copy2(source, link)
    build = sandbox / "build"
    build.mkdir()
    for stem in STUB_NAMES:
        for arch in STUB_ARCHES:
            stub = build / f"{stem}_{arch}{STUB_SUFFIX}"
            stub.write_text(STUB_SCRIPT_CMD if STUB_SUFFIX else STUB_SCRIPT, encoding="utf-8")
            stub.chmod(0o755)
    (sandbox / "home").mkdir()
    return build / "stub-invocations.log"


def run_one(sandbox: Path, test: str, scenario: str, timeout: float, log_dir: Path, stub_log: Path) -> Outcome:
    env = {key: value for key, value in os.environ.items() if not key.startswith(("MESON_", "ETQW_", "OPENETQW_"))}
    env["HOME"] = str(sandbox / "home")
    env["USERPROFILE"] = str(sandbox / "home")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    env["OPENETQW_DED_BINARY"] = str(sandbox / "build" / f"openETQW-ded_x64{STUB_SUFFIX}")
    env["OPENETQW_CLIENT_BINARY"] = str(sandbox / "build" / f"openETQW-client_x64{STUB_SUFFIX}")
    if scenario == "missing-dir":
        env["ETQW_DATA_PATH"] = str(sandbox / "no-retail-data-here")
    before = stub_log.read_text(encoding="utf-8").splitlines() if stub_log.exists() else []
    started = time.monotonic()
    proc = subprocess.Popen(
        [sys.executable, str(sandbox / test)],
        cwd=str(sandbox),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=True,
    )
    timed_out = False
    try:
        output, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                proc.kill()
        except OSError:
            proc.kill()
        output, _ = proc.communicate()
    elapsed = time.monotonic() - started
    after = stub_log.read_text(encoding="utf-8").splitlines() if stub_log.exists() else []
    launched = after[len(before):]
    log_path = log_dir / scenario / (Path(test).stem + ".log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(output or "", encoding="utf-8")
    return Outcome(
        test=test,
        scenario=scenario,
        exit_code=None if timed_out else proc.returncode,
        elapsed=round(elapsed, 2),
        timed_out=timed_out,
        traceback=TRACEBACK in (output or ""),
        stub_launched=launched,
        log=str(log_path),
    )


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------

def load_baseline(path: Optional[Path]) -> dict[str, str]:
    if path is None or not path.is_file():
        return {}
    entries: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        body, _, comment = line.partition("#")
        body = body.strip()
        if body:
            entries[body] = comment.strip()
    return entries


BASELINE_HEADER = """\
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Known integration tests that do NOT yet skip cleanly (exit 77, no traceback,
# no engine launch) when retail data is absent. tools/ci/check_integration_skips.py
# reports these as warnings instead of failing CI; every other integration test
# must skip cleanly. Fix the test, then delete its line (CI prints a notice when
# an entry has been fixed). Regenerate with:
#   python3 tools/ci/check_integration_skips.py --write-baseline
#
# Format: tests/<file>.py  # what was observed
"""


def write_baseline(path: Path, entries: dict[str, str]) -> None:
    lines = [BASELINE_HEADER]
    for test in sorted(entries):
        comment = entries[test].replace("\n", " ")
        lines.append(f"{test}  # {comment}" if comment else test)
    path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")


def gha(command: str, message: str, file: str = "", line: int = 0) -> None:
    escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    if os.environ.get("GITHUB_ACTIONS", "").lower() == "true":
        props = ""
        if file:
            props = f" file={file}" + (f",line={line}" if line else "")
        print(f"::{command}{props}::{escaped}", flush=True)
    else:
        where = (f"{file}:{line}: " if line else f"{file}: ") if file else ""
        print(f"{command.upper()}: {where}{message}", flush=True)


def write_summary(lines: list[str]) -> None:
    target = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n\n")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, default=ROOT, help="engine repository root")
    parser.add_argument("--timeout", type=float, default=20.0, help="seconds per test run (default 20)")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE, help="known-violations file")
    parser.add_argument("--no-baseline", action="store_true", help="ignore the baseline; every violation fails")
    parser.add_argument("--strict-baseline", action="store_true", help="fail when a baseline entry now passes")
    parser.add_argument("--only", action="append", default=[], help="run just this test (repeatable)")
    parser.add_argument("--list", action="store_true", help="print the discovered tests and exit")
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="add the tests that violate now to the baseline file (existing entries are kept) and exit 0",
    )
    parser.add_argument("--prune-fixed", action="store_true", help="with --write-baseline: drop entries that now pass")
    parser.add_argument("--output-dir", type=Path, default=ROOT / ".tmp" / "ci" / "integration-skips")
    parser.add_argument("--keep-sandbox", action="store_true", help="do not delete the scratch sandbox")
    parser.add_argument(
        "--keep-hardcoded-paths",
        action="store_true",
        help="run the sandbox copies with their hard-coded absolute paths intact (default: point them at a missing dir)",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    root = args.root.resolve()
    tests, hardcoded = discover(root)
    if args.only:
        wanted = {item if item.startswith("tests/") else f"tests/{item}" for item in args.only}
        tests = [test for test in tests if test.path in wanted]
    print(f"check_integration_skips: {len(tests)} integration test(s) under {root}")
    for test in tests:
        kind = "meson " if test.meson else "script"
        print(f"  [{kind}] {test.path:52s} {'; '.join(test.reasons)}")
    for rel, hits in hardcoded.items():
        first_line, _, first_text = hits[0].partition(": ")
        more = f" (+{len(hits) - 1} more line(s))" if len(hits) > 1 else ""
        gha(
            "warning",
            f"hard-coded absolute path; retail data must come from ETQW_DATA_PATH: {first_text}{more}",
            rel,
            int(first_line),
        )
    if args.list:
        return 0

    baseline = {} if args.no_baseline else load_baseline(args.baseline)
    output_dir = args.output_dir.resolve()
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)
    sandbox = Path(tempfile.mkdtemp(prefix="openetqw-skipcheck-", dir=os.environ.get("RUNNER_TEMP") or None))
    outcomes: list[Outcome] = []
    try:
        stub_log = build_sandbox(root, sandbox, args.keep_hardcoded_paths)
        for test in tests:
            for scenario in ("unset", "missing-dir"):
                outcome = run_one(sandbox, test.path, scenario, args.timeout, output_dir, stub_log)
                outcomes.append(outcome)
                state = "SKIP(77) ok" if outcome.ok else "VIOLATION: " + outcome.reason(verbose=True)
                print(f"  [{scenario:11s}] {test.path:52s} {outcome.elapsed:6.2f}s  {state}", flush=True)
    finally:
        if args.keep_sandbox:
            print(f"check_integration_skips: sandbox kept at {sandbox}")
        else:
            shutil.rmtree(sandbox, ignore_errors=True)

    by_test: dict[str, list[Outcome]] = {}
    for outcome in outcomes:
        by_test.setdefault(outcome.test, []).append(outcome)
    meson_tests = {test.path for test in tests if test.meson}
    unexpected, known, fixed = [], [], []
    for test, results in by_test.items():
        bad = [result for result in results if not result.ok]
        if bad:
            detail = "; ".join(f"{result.scenario}: {result.reason()}" for result in bad)
            baselinable = test in baseline and test not in meson_tests
            (known if baselinable else unexpected).append((test, detail))
        elif test in baseline:
            fixed.append(test)
    stale = [] if args.only else sorted(set(baseline) - set(by_test))
    baselined_meson = sorted(set(baseline) & meson_tests)

    for test, detail in known:
        gha("warning", f"known integration-skip violation (baseline): {detail}", test)
    for test, detail in unexpected:
        gha("error", f"integration test does not skip cleanly without retail data: {detail}", test)
    for test in fixed:
        gha(
            "error" if args.strict_baseline else "notice",
            "now skips cleanly without retail data; remove it from tools/ci/integration_skip_baseline.txt",
            test,
        )
    for test in stale:
        gha("notice", f"baseline entry {test} matches no discovered integration test; remove it")
    for test in baselined_meson:
        gha(
            "error",
            "is registered with Meson in an integration suite and must always skip cleanly; "
            "remove it from tools/ci/integration_skip_baseline.txt",
            test,
        )

    if args.write_baseline:
        entries = {} if args.no_baseline else dict(load_baseline(args.baseline))
        for test, detail in unexpected + known:
            if test in meson_tests:
                continue  # never baselinable; must be fixed instead
            entries[test] = detail
        for test in baselined_meson:
            entries.pop(test, None)
        if args.prune_fixed:
            for test in fixed:
                entries.pop(test, None)
        write_baseline(args.baseline, entries)
        print(f"check_integration_skips: wrote {len(entries)} entr{'y' if len(entries) == 1 else 'ies'} to {args.baseline}")
        return 0

    report = {
        "root": str(root),
        "timeoutSeconds": args.timeout,
        "tests": [{"path": test.path, "reasons": test.reasons} for test in tests],
        "outcomes": [outcome.__dict__ | {"ok": outcome.ok, "reason": outcome.reason(verbose=True)} for outcome in outcomes],
        "mesonRegistered": sorted(meson_tests),
        "unexpectedViolations": [test for test, _ in unexpected],
        "baselineViolations": [test for test, _ in known],
        "baselineFixed": fixed,
        "hardcodedPaths": hardcoded,
    }
    (output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    clean = {test for test, results in by_test.items() if all(result.ok for result in results)}
    meson_run = meson_tests & set(by_test)
    summary = [
        "### Integration tests skip without retail data (exit 77)",
        "",
        f"- Meson-registered integration tests: {len(clean & meson_run)}/{len(meson_run)} skip cleanly "
        "(required, no baseline)",
        f"- standalone retail-data scripts: {len(clean - meson_run)}/{len(set(by_test) - meson_run)} skip cleanly",
        f"- unexpected violations: {len(unexpected)}" + (" (" + ", ".join(t for t, _ in unexpected) + ")" if unexpected else ""),
        f"- baseline (known) violations: {len(known)}",
        f"- baseline entries now fixed: {len(fixed)}" + (" (" + ", ".join(fixed) + ")" if fixed else ""),
        f"- files with hard-coded absolute paths: {len(hardcoded)}",
    ]
    write_summary(summary)
    print("\n".join(summary[2:]))
    print(f"check_integration_skips: report {output_dir / 'report.json'}")
    failed = bool(unexpected) or bool(baselined_meson) or (args.strict_baseline and bool(fixed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
