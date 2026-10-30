#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Keep the sanitizer exclusions as narrow as decision 0005 allows.

The owner allowed exactly one exclusion (docs/decisions/0005, 2026-10-29): UBSan's
vptr check, off on Linux, in meson.build. This fails when

* a meson file, the meson options or an engine source (vendored libraries aside)
  turns off another sanitizer check, makes one recoverable or excludes code from it,
  or turns vptr off anywhere else or not only on Linux;
* the CI sanitizer job stops building with address,undefined, or its ASan/UBSan
  failures stop being fatal (halt_on_error=1);
* the validator stops passing --sanitize through to b_sanitize unchanged;
* tools/ci/ubsan.supp gains an entry (a suppression needs its own owner decision,
  AGENTS rule 4).

After openQ4's tools/tests/linux_sanitizer_ci.py, which also pins the workflow lines.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_IF = "if host_system == 'linux' and cpp.has_argument('-fno-sanitize=vptr')"
ALLOWED_ARG = "add_project_arguments('-fno-sanitize=vptr', language: 'cpp')"
# what turns a check off, makes a failure non-fatal or excludes code from a check
WEAKENING = re.compile(r"fno-sanitize|sanitize-recover|sanitize-blacklist|sanitize-ignorelist"
                       r"|no_sanitize|disable_sanitizer|sanitize=[^,'\"\s]*vptr")
VENDORED = ("src/sys/sdl2/", "src/libs/")

failures: list[str] = []


def tracked(*patterns: str) -> list[str]:
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "--", *patterns],
                         capture_output=True, text=True, check=False).stdout
    return [p for p in out.splitlines() if p and not p.startswith(VENDORED)]


def text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8", errors="replace")


def code_lines(rel: str):
    for n, line in enumerate(text(rel).splitlines(), 1):
        if not line.lstrip().startswith("#"):
            yield n, line


# 1. exclusions in build files and engine sources
build_files = tracked("*meson.build", "meson_options.txt", "meson.options")
sources = tracked("src/*.c", "src/*.cpp", "src/*.h", "src/*.mm", "tests/*.c", "tests/*.cpp", "tests/*.h")
allowed_seen = False
for rel in build_files + sources:
    lines = text(rel).splitlines()
    for n, line in code_lines(rel):
        if not WEAKENING.search(line):
            continue
        stripped = line.strip()
        if rel == "meson.build" and stripped == ALLOWED_IF:
            allowed_seen = True
            continue
        if rel == "meson.build" and stripped == ALLOWED_ARG and n >= 2 and lines[n - 2].strip() == ALLOWED_IF:
            continue
        failures.append(f"{rel}:{n}: sanitizer exclusion outside the one decision 0005 allows: {stripped}")
if not allowed_seen:
    failures.append("meson.build: the Linux-only vptr exclusion of decision 0005 is missing or reworded; "
                    "change this test only with the decision")


# 2. lines that must stay as they are
def require(rel: str, needle: str, why: str) -> None:
    if needle not in text(rel):
        failures.append(f"{rel}: lost {needle!r} ({why})")


WORKFLOW = ".github/workflows/commit-validation.yml"
require(WORKFLOW, "openetqw_validate.py pr --build-dir build --sanitize address,undefined",
        "the sanitizer job builds with ASan and UBSan")
require(WORKFLOW, "ASAN_OPTIONS: halt_on_error=1:", "ASan reports fail the job")
require(WORKFLOW, "UBSAN_OPTIONS: halt_on_error=1:", "UBSan reports fail the job")
require("tools/validation/openetqw_validate.py", 'f"-Db_sanitize={args.sanitize}"',
        "--sanitize reaches b_sanitize unchanged")
for n, line in code_lines("tools/ci/ubsan.supp"):
    if line.strip():
        failures.append(f"tools/ci/ubsan.supp:{n}: a UBSan suppression needs its own owner decision: {line.strip()}")

if failures:
    print("\n".join(failures))
    print("sanitizer-scope: FAILED")
    sys.exit(1)
print("sanitizer-scope: only the Linux UBSan vptr exclusion (decision 0005)")
