#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fail if the engine's install set lacks a licence text that must go with it.

Every package carries the licence texts (owner decision, 2026-09-25): the GPL
(LICENSE), the ET:QW SDK EULA (EULA section 5: the agreement must accompany
each copy, and the engine links SDK code; docs/provenance.md section 4), the
notices of the third-party code compiled into the engine, and the game
repository's LICENSE (its licence split and the MIT licence of its own code),
since the game module is installed with the engine. This reads Meson's
install plan (<builddir>/meson-info/intro-installed.json, what `meson
introspect --installed` prints) and checks that, whenever an engine executable
is installed, each of those texts is installed too.

Usage: check_install_licenses.py <builddir>
Exit codes: 0 pass, 1 a licence text is missing, 2 usage / environment error.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REQUIRED = [
    "licenses/openETQW/LICENSE",
    "licenses/openETQW/third-party/imgui.txt",
    "licenses/openETQW/third-party/recastnavigation.txt",
    "licenses/openETQW/third-party/minizip.txt",
    "licenses/ETQW-SDK/EULA_SDK_ETQW.txt",
    "licenses/ETQW-SDK/EULA SDK ETQW.rtf",
    "licenses/openETQW-game/LICENSE",
]
EXECUTABLE_PREFIX = "openETQW-"


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__.strip().splitlines()[-2], file=sys.stderr)
        return 2
    plan = Path(argv[0]) / "meson-info" / "intro-installed.json"
    if not plan.is_file():
        print(f"check_install_licenses: {plan} not found (not a configured Meson build directory?)", file=sys.stderr)
        return 2
    installed = [Path(dest).as_posix() for dest in json.loads(plan.read_text(encoding="utf-8")).values()]
    executables = [d for d in installed if Path(d).name.startswith(EXECUTABLE_PREFIX)]
    if not executables:
        print("check_install_licenses: no engine executable is installed by this build; nothing to check")
        return 0
    missing = [r for r in REQUIRED if not any(d.endswith("/" + r) for d in installed)]
    for r in missing:
        print(f"missing from the install set: {r}")
    print(f"check_install_licenses: {len(executables)} executable(s), {len(REQUIRED) - len(missing)}/{len(REQUIRED)} licence texts installed")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
