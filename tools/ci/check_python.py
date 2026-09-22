#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Python hygiene gate for CI: compile every tracked *.py, then pyflakes.

1. Syntax: every *.py tracked by git is compiled in memory (no __pycache__ is
   written). Files listed in COMPILE_EXCLUDES are skipped with their reason;
   an exclude that no longer exists, or that now compiles, is reported so the
   list cannot go stale silently.
2. pyflakes over the tracked *.py under the given roots (default tests/ and
   tools/). Messages are split in two classes:
     blocking  - things that are bugs at runtime (undefined names, return
                 outside a function, broken %-format / str.format calls,
                 'is' against a literal, ...): fail the job;
     advisory  - hygiene (unused imports/variables, f-strings without
                 placeholders, redefinitions): GitHub warning annotations only.
   pyflakes must be importable (pip install pyflakes); pass
   --allow-missing-pyflakes to only warn when it is not.

Exit status: 0 clean (advisory findings allowed), 1 syntax error or blocking
pyflakes finding, 2 usage/environment error.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# path -> reason. Keep this short; every entry is technical debt.
COMPILE_EXCLUDES = {
    "src/sys/win32/sdk/prepare.py": "legacy Python 2 script from the original Win32 SDK tree (print statement)",
}

BLOCKING_MESSAGES = {
    "UndefinedName",
    "UndefinedExport",
    "UndefinedLocal",
    "DuplicateArgument",
    "ReturnOutsideFunction",
    "YieldOutsideFunction",
    "ContinueOutsideLoop",
    "BreakOutsideLoop",
    "DefaultExceptNotLast",
    "TwoStarredExpressions",
    "TooManyExpressionsInStarredAssignment",
    "IfTuple",
    "AssertTuple",
    "IsLiteral",
    "InvalidPrintSyntax",
    "RaiseNotImplemented",
    "ForwardAnnotationSyntaxError",
    "FutureFeatureNotDefined",
    "LateFutureImport",
    "ImportStarNotPermitted",
    "MultiValueRepeatedKeyVariable",
    "PercentFormatExpectedMapping",
    "PercentFormatExpectedSequence",
    "PercentFormatExtraNamedArguments",
    "PercentFormatInvalidFormat",
    "PercentFormatMissingArgument",
    "PercentFormatMixedPositionalAndNamed",
    "PercentFormatPositionalCountMismatch",
    "PercentFormatStarRequiresSequence",
    "PercentFormatUnsupportedFormatCharacter",
    "StringDotFormatExtraNamedArguments",
    "StringDotFormatExtraPositionalArguments",
    "StringDotFormatInvalidFormat",
    "StringDotFormatMissingArgument",
    "StringDotFormatMixingAutomatic",
}


def in_actions() -> bool:
    return os.environ.get("GITHUB_ACTIONS", "").lower() == "true"


def annotate(level: str, path: str, line: int, col: int, message: str) -> None:
    text = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    if in_actions():
        print(f"::{level} file={path},line={line},col={col}::{text}")
    else:
        print(f"{path}:{line}:{col}: {level}: {message}")


def tracked_python(root: Path) -> list[str]:
    try:
        output = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--", "*.py"],
            capture_output=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"check_python: cannot list tracked files: {exc}")
    return sorted(item.decode("utf-8") for item in output.split(b"\0") if item)


def check_syntax(root: Path, files: list[str]) -> tuple[int, int]:
    errors = compiled = 0
    for rel in files:
        path = root / rel
        if not path.is_file():
            continue  # deleted in the working tree
        source = path.read_bytes()
        reason = COMPILE_EXCLUDES.get(rel)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                compile(source, rel, "exec", dont_inherit=True)
                ok, detail = True, ""
            except (SyntaxError, ValueError) as exc:
                ok = False
                detail = f"{exc.__class__.__name__}: {getattr(exc, 'msg', exc)}"
                line = getattr(exc, "lineno", 1) or 1
                col = getattr(exc, "offset", 1) or 1
        if reason:
            if ok:
                annotate("notice", rel, 1, 1, "now compiles; remove it from COMPILE_EXCLUDES in tools/ci/check_python.py")
            else:
                print(f"check_python: skip {rel} ({reason})")
            continue
        compiled += 1
        if not ok:
            errors += 1
            annotate("error", rel, line, col, detail)
    for rel in COMPILE_EXCLUDES:
        if rel not in files:
            annotate("notice", rel, 1, 1, "listed in COMPILE_EXCLUDES but not tracked any more; remove the entry")
    return compiled, errors


def run_pyflakes(root: Path, files: list[str], allow_missing: bool) -> tuple[int, int, int]:
    try:
        from pyflakes import api as flakes_api
        from pyflakes import reporter as flakes_reporter
    except ImportError:
        message = "pyflakes is not installed (python -m pip install pyflakes)"
        if allow_missing:
            print(f"check_python: WARNING: {message}; skipping lint")
            return 0, 0, 0
        raise SystemExit(f"check_python: {message}")

    class Collector(flakes_reporter.Reporter):
        def __init__(self) -> None:
            super().__init__(sys.stdout, sys.stderr)
            self.messages: list = []
            self.syntax: list[tuple[str, int, int, str]] = []

        def flake(self, message) -> None:  # noqa: D401 - pyflakes API
            self.messages.append(message)

        def syntaxError(self, filename, msg, lineno, offset, text) -> None:
            self.syntax.append((filename, lineno or 1, offset or 1, msg))

        def unexpectedError(self, filename, msg) -> None:
            self.syntax.append((filename, 1, 1, msg))

    collector = Collector()
    for rel in files:
        path = root / rel
        if rel in COMPILE_EXCLUDES or not path.is_file():
            continue
        flakes_api.check(path.read_text(encoding="utf-8", errors="replace"), rel, collector)

    blocking = advisory = 0
    for filename, line, col, msg in collector.syntax:
        blocking += 1
        annotate("error", filename, line, col, f"pyflakes: {msg}")
    for message in sorted(collector.messages, key=lambda m: (m.filename, m.lineno, m.col)):
        kind = message.__class__.__name__
        text = f"pyflakes {kind}: {message.message % message.message_args}"
        if kind in BLOCKING_MESSAGES:
            blocking += 1
            annotate("error", message.filename, message.lineno, message.col + 1, text)
        else:
            advisory += 1
            annotate("warning", message.filename, message.lineno, message.col + 1, text)
    return len(files), blocking, advisory


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root")
    parser.add_argument(
        "--lint-roots",
        nargs="*",
        default=["tests", "tools"],
        help="directories whose tracked *.py are linted with pyflakes (default: tests tools)",
    )
    parser.add_argument("--allow-missing-pyflakes", action="store_true")
    parser.add_argument("--strict", action="store_true", help="treat advisory pyflakes findings as blocking")
    args = parser.parse_args(argv)
    root = args.root.resolve()

    files = tracked_python(root)
    compiled, syntax_errors = check_syntax(root, files)
    print(f"check_python: compiled {compiled} tracked file(s), {syntax_errors} syntax error(s)")

    prefixes = tuple(prefix.rstrip("/") + "/" for prefix in args.lint_roots)
    lint_files = [rel for rel in files if rel.startswith(prefixes)]
    linted, blocking, advisory = run_pyflakes(root, lint_files, args.allow_missing_pyflakes)
    print(f"check_python: pyflakes on {linted} file(s): {blocking} blocking, {advisory} advisory finding(s)")

    summary = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(
                "### Python checks\n"
                f"- compiled {compiled} tracked file(s): {syntax_errors} syntax error(s)\n"
                f"- pyflakes ({', '.join(args.lint_roots)}): {blocking} blocking, {advisory} advisory\n\n"
            )
    failed = syntax_errors or blocking or (args.strict and advisory)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
