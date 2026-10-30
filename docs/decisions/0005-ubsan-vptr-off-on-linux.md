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
  `tools/ci/check_sanitizer_scope.py` (game, Static checks) fail if a build file, the
  meson options, a source file or (in the game) a workflow turns off another sanitizer
  check, makes one recoverable or excludes code from it; if the vptr exclusion stops
  being Linux-only; if the sanitizer legs stop building with `address,undefined`; if
  their `halt_on_error=1` goes; or (engine) if `tools/ci/ubsan.supp` gains an entry or
  the validator stops passing `--sanitize` through unchanged.

## Consequences

- The game repository's instrumented module build (`commit-validation.yml`, its
  sanitizer legs on macOS and Linux) has built without the vptr check since
  2026-09-22, for the same reason: a dynamically loaded module cannot link the
  engine-side typeinfo. That exclusion predates this decision; the game's guard allows
  it as it stands and nothing broader.
- The engine's macOS sanitizer workflow (`macos-sanitizer.yml`, run by hand) keeps the
  vptr check; no leg that runs on every pull request does.
- A bad downcast between engine and game classes is therefore not caught by UBSan in
  per-PR CI.
