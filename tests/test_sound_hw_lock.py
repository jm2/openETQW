#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""sound-hw-lock (#374): the audio hardware is freed only under the sound system's lock.

Windows' sound thread holds soundSystem->GetLock() around every AsyncUpdate, which writes
to the audio hardware's stream and mix buffer. A ShutdownHW or Shutdown without that lock
can free them in the middle of that write. idAsyncNetwork::SpawnServer
(net_serverDedicated 1) called ShutdownHW without the lock.

Outside src/sound, every call that frees the hardware (soundSystem or soundSystemLocal,
-> or ., ShutdownHW or Shutdown) in a .cpp or .h must either go through
Snd_ShutdownHardwareLocked (src/sound/SoundHardwareShutdown.h, unit-tested by
sound-hw-shutdown), or come after an sdScopedLock on soundSystem->GetLock() in the same
brace scope. InitHW is not checked: it runs during start-up, before the sound thread
does any work (that waits for common->IsInitialized()).

Exit 0 pass, 1 fail.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CALL = re.compile(r"\b(?:soundSystem|soundSystemLocal)\s*(?:->|\.)\s*(?:ShutdownHW|Shutdown)\s*\(")
LOCK = re.compile(r"sdScopedLock\s*<\s*true\s*>\s*\w+\s*\(\s*soundSystem\s*->\s*GetLock\s*\(\s*\)\s*\)")
COMMENTS = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)


def strip_comments(text):
    # keep the line count, so line numbers still match the file
    return COMMENTS.sub(lambda m: "\n" * m.group(0).count("\n"), text)


def locked_in_scope(lines, i):
    """Whether an sdScopedLock on the sound lock is in force at line i: before it, in the same
    brace scope or an enclosing one, and not in a scope that has already closed."""
    depth = 0
    for line in reversed(lines[:i]):
        for ch in reversed(line):
            if ch == "}":
                depth += 1
            elif ch == "{":
                depth -= 1
        if depth < 0:
            # left the call's own scope for an enclosing one: a lock there still covers it
            depth = 0
        if depth == 0 and LOCK.search(line):
            return True
        if re.match(r"^\S.*\)\s*(const\s*)?\{?\s*$", line) and not line.startswith(("if", "for", "while", "switch")):
            # a function's signature at column 0: the function starts here
            return False
    return False


def main():
    failures = []
    calls = 0
    for path in sorted(p for ext in ("*.cpp", "*.h") for p in (ROOT / "src").rglob(ext)):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("src/sound/"):
            continue
        lines = strip_comments(path.read_text(encoding="utf-8", errors="replace")).splitlines()
        for i, line in enumerate(lines):
            if not CALL.search(line):
                continue
            calls += 1
            locked = locked_in_scope(lines, i)
            print(f"  {'ok  ' if locked else 'FAIL'} {rel}:{i + 1}: {line.strip()}"
                  f"{'' if locked else '  (no sdScopedLock on soundSystem->GetLock() in scope)'}")
            if not locked:
                failures.append(f"{rel}:{i + 1}")
    print(f"  {calls} direct call(s) outside src/sound that free the audio hardware")
    if failures:
        print(f"FAIL: {len(failures)} call(s) free the audio hardware without the sound lock: {', '.join(failures)}")
        return 1
    print("PASS: every call outside src/sound that frees the audio hardware holds the sound lock")
    return 0


if __name__ == "__main__":
    sys.exit(main())
