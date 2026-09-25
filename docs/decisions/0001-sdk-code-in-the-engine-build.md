# 0001: SDK code in the engine build

- **Date:** 2026-09-25
- **Status:** decided by the owner ("Also openq4 precedent", answer A6)
- **Scope:** CHARTER §11 tracked exceptions E1–E4 (`docs/provenance.md` §4); release gate item 2

## Context

The engine is GPL-3.0-or-later, and its repository holds no SDK files (the `zero-sdk` test). Its binaries are still built with SDK code from `openETQW-game`:

- **E1** the SDK idLib, `libidLib.a` (72 objects), linked into both engine binaries and the unit tests;
- **E2** the SDK `src/decllib/DeclType.cpp`, staged and compiled into both binaries;
- **E3** the SDK `src/idlib/BitMsg.cpp`, compiled into three unit tests;
- **E4** 189 staged SDK headers and `framework/BuildDefines.inc`, which keep the engine's and the game module's memory layouts in agreement.

CHARTER §11 requires each exception to be resolved: by a GPL replacement (a Doom 3 or dhewm3 idLib extended to the ETQW API, about 80k lines plus about 19k lines of headers, binary-compatible with the SDK-built game), by keeping interface headers only, by new GPL code, or by the owner's approval.

openQ4 reached the same place without saying so. Its engine repository compiles its own idLib, 121 of whose 146 files are near-identical to the Quake 4 SDK copy (50 carry Raven markers) under a GPL label, and its engine compiles 94 SDK game headers.

## Decision

E1–E4 are approved as tracked exceptions. No code is replaced; `DeclType.cpp` is not rewritten.

Unlike openQ4, OpenETQW states the exceptions openly and keeps each file's own licence text. Nothing is relabelled GPL (CHARTER §11).

## Consequences

- Every engine package must carry the ET:QW SDK EULA as well as the GPL (EULA §5: the agreement accompanies each copy). The engine's `meson install` does this on the branch `feat/retail-install-check` (commit 0b3386c; checked by meson `install-licenses`).
- Release gate item 2 is cleared for the engine binaries once that lands. The SDK files in the engine's early history (jmarshall's root commits, left in place by the owner) still need EULA §5 handling before publishing (`docs/provenance.md` §6).
- `docs/provenance.md` §4 remains the registry. Any new SDK code in the engine build needs a new registry entry and the owner's approval.
