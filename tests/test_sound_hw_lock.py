#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""sound-hw-lock (#374): the audio hardware is freed only under the sound system's lock.

Windows' sound thread holds soundSystem->GetLock() around every AsyncUpdate, which writes
to the audio hardware's stream and mix buffer. A ShutdownHW without that lock can free
them in the middle of that write. idAsyncNetwork::SpawnServer (net_serverDedicated 1)
called it without the lock.

Every soundSystem->ShutdownHW() call outside src/sound must either go through
Snd_ShutdownHardwareLocked (src/sound/SoundHardwareShutdown.h, unit-tested by
sound-hw-shutdown), or come right after an sdScopedLock on the sound system's lock.

Exit 0 pass, 1 fail.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CALL = re.compile(r"soundSystem\s*->\s*ShutdownHW\s*\(")
LOCK = re.compile(r"sdScopedLock\s*<\s*true\s*>\s*\w+\s*\(\s*soundSystem\s*->\s*GetLock\s*\(\s*\)\s*\)")


def main():
    failures = []
    calls = 0
    for path in sorted((ROOT / "src").rglob("*.cpp")):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("src/sound/"):
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for i, line in enumerate(lines):
            if not CALL.search(line) or line.lstrip().startswith("//"):
                continue
            calls += 1
            previous = next((ln for ln in reversed(lines[:i]) if ln.strip()), "")
            locked = bool(LOCK.search(previous))
            print(f"  {'ok  ' if locked else 'FAIL'} {rel}:{i + 1}: {line.strip()}"
                  f"{'' if locked else '  (no sdScopedLock on soundSystem->GetLock() just before it)'}")
            if not locked:
                failures.append(f"{rel}:{i + 1}")
    print(f"  {calls} direct soundSystem->ShutdownHW() call(s) outside src/sound")
    if failures:
        print(f"FAIL: {len(failures)} call(s) free the audio hardware without the sound lock: {', '.join(failures)}")
        return 1
    print("PASS: every direct ShutdownHW call outside src/sound holds the sound lock")
    return 0


if __name__ == "__main__":
    sys.exit(main())
