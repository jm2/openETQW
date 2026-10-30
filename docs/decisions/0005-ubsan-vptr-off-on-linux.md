# 0005: UBSan's vptr check is off on Linux

- **Date:** 2026-10-29
- **Status:** decided by the owner
- **Scope:** hard rule 4 (`AGENTS.md`): the Linux x64 Clang ASan+UBSan CI leg and every
  Linux sanitizer build of both repositories

## Context

Hosted CI ran again on 2026-10-29, after the repositories went public. The Linux
ASan+UBSan leg (`openetqw_validate.py pr --sanitize address,undefined`) had never linked:
UBSan's vptr check (`-fsanitize=vptr`, part of `undefined` with Clang) needs the typeinfo
of every polymorphic class it checks, and the engine, the game module and the SDK idLib
define some classes' key functions on one side only. The dedicated server lacks the
typeinfo of `idImageManager` and `sdRenderShader` (client-only sources), and the unit
tests that link the game's `libidLib.a` lack `idFile`'s and `idFile_Memory`'s (engine
sources). Stubbing each class was possible for the first case but not a fix for the
general one: the game module is loaded at run time and keeps its own typeinfo.

openQ4 met the same problem and turned off the vptr check only, on Linux, for the
engine, the game and its BSE (`meson.build:1187-1193`), with a test that keeps the
exclusion that narrow (`tools/tests/linux_sanitizer_ci.py`).

## Decision

The owner, 2026-10-29, choosing between A (follow openQ4) and B (typeinfo stubs class
by class): "4. A".

- On Linux, both repositories compile C++ with `-fno-sanitize=vptr`. It changes nothing
  unless UBSan is on.
- AddressSanitizer and every other UBSan check stay on, and failures stay fatal
  (`halt_on_error=1`).
- `tests/test_sanitizer_scope.py` (engine, meson `sanitizer-scope`) and
  `tools/ci/check_sanitizer_scope.py` (game, Static checks) fail if any other sanitizer
  check is turned off, made recoverable or excluded, or if the vptr exclusion stops
  being Linux-only.

## Consequences

- The macOS sanitizer legs keep the vptr check.
- A bad downcast between engine and game classes is not caught by UBSan on Linux; the
  macOS legs still check it.
