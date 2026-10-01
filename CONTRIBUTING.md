<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Contributing

`main` changes only through pull requests. Branch protection is not available on
these private repositories, so the merge gates below are a convention, not
enforced by GitHub. Keep to them anyway.

## Hosted CI is not running
Since 2026-09-24 the GitHub Actions minutes for these private repositories are used up.
Workflows do not start, so a missing check proves nothing either way. Until minutes are
available again, the local checks below replace CI. Do not make a repository public to
get CI back: both stay private until the owner decides to publish.

## Merge gates
1. **Local checks pass.** Run them from the repository root and put the result in the PR:
   - `actionlint` (workflow lint; CI used 1.7.12)
   - `python3 tools/ci/check_python.py` (syntax and pyflakes over `tests/` and `tools/`; needs `pyflakes==4.0.0`, the version CI used)
   - `python3 tools/ci/check_integration_skips.py` (every retail-data test must exit 77 without data; it starts stub binaries, never a real server)
   - `python3 tools/ci/check_include_case.py` (every `#include` matches the file's letter case; Linux is case-sensitive, macOS is not)
   - `meson test -C build --suite unit` (asset-free unit tests, including the zero-SDK audit, the licence texts in the
     install set and the retail-install check on synthetic paks)
2. **Retail-data suite for runtime changes.** Anything touching the engine, the game or the
   network gets the full Meson suite with retail data:
   `ETQW_DATA_PATH="$HOME/etqw_data" meson test -C build -j 1` (add `--no-suite soak` to skip the 10-minute `lossy-session`)
   (replace `$HOME/etqw_data` with the root of a full retail ET:QW install patched to 1.5:
   `base/` must hold `pak000`–`pak008.pk4`; the language paks, such as `zpak_english000`–`003`,
   are optional (they carry the dialogue); the unpatched DVD set is not supported, and the engine
   refuses to start on it). A new test that runs the engine on synthetic data must pass
   `+set fs_validateOfficialPaks 0`, as `tools/ci/dedicated_server_smoke.py` does. If it loads
   the game module, it also needs the stub decl from `etqw_harness.write_synthetic_stub_decls`:
   without it `game->Init()` fails, and a failed `game->Init()` stops the engine. Check that
   the retail-data tests ran rather than skipped: a run that skips them does not meet this gate.
   Put the result in the PR with the host, OS, architecture, commit and data set. The tests use
   fixed UDP ports, so run one suite at a time on a host.
   **Vehicle or physics changes** (anything under `src/game/vehicles` or `src/game/physics` in
   openETQW-game) must also pass `vehicle-physics-golden` (M5 step 5a; it is part of the full
   suite): the vehicle replay's `VEH_TRACE` must match `tests/golden/vehicle-physics/<toolchain>.txt`
   bit-exactly, or, on a toolchain with no golden, the reference golden at fixed precision (the
   test says which). A golden changes only on purpose, with the reason in its header:
   `python3 tests/test_vehicle_physics_golden.py --regenerate --reason "..."`, and the PR says why.
   **Seat gate changes** (openETQW-game `src/game/vehicles/SeatGate.*`, `idPlayer::IsSeatPresentationAllowed`,
   or the seat latch) must also pass `seat-gate` (M5 step 13; part of the full suite): an excluded seat
   never unlocks, keys match, and the gate changes no `SIM_HASH`, `VEH_TRACE` or `SIM_VIEW` value.
3. **Independent review.** Someone other than the author (a person or a separate review
   session) reads the diff and the raw test output, not only the summary.
4. **CodeRabbit.** CodeRabbit reviews each PR. It currently allows about one review per
   hour: ask again with `@coderabbitai review` when the window opens, and say in the PR
   while it is out of quota. The ChatGPT Codex connector (`@codex review`) and Codacy, when
   they review, count the same way.
5. **Every finding is answered**, from the independent review and from the bots: fixed, or
   explained in the PR.
6. **Merge commit.** Branches are merged with a merge commit, which keeps the per-fix history.

When Actions minutes are available again, **Commit Validation**
(`.github/workflows/commit-validation.yml`) and **Linux ARM64 Cross Build** must also be
green on the PR. Their build jobs skip while `openETQW-game` is private, and experimental
legs report without blocking. Every build job runs `tools/validation/openetqw_validate.py`
(`validate_pr.sh` / `validate_pr.ps1`), so the same check runs locally:
`python3 tools/validation/openetqw_validate.py pr` builds the game and the engine, runs the
unit suite and the integration skip check, stages the install tree with `meson install`,
checks the staged payload and runs the dedicated-server smoke on it.

## Cross-repo changes
The engine builds against the game at the commit pinned in `tools/ci/companion.ref`.
1. Merge the game PR first.
2. On the engine branch, pin the merged game commit with
   `python3 tools/ci/bump_companion.py --sha <full 40-character sha>`.
3. Merge the engine PR.

## History rewrite (2026-09-24)
Both repositories' histories were rewritten on 2026-09-24 to remove a retail file
(`src/sys/win32/rc/res/etqw_splash.bmp`, ET:QW cover art) from every commit. `main` and
`wip/stock-maps-m2-checkpoint` were force-pushed. Every commit ID changed: engine `main`
is now `84a224b` (was `2104090`), game `main` is `24f58bf` (was `285eaa1`).
- Re-clone any clone made before the rewrite, or hard-reset each branch to its new remote
  branch. Carry unpushed work over by cherry-picking it onto the new commits.
- Never push a branch or tag that still contains pre-rewrite commits: that would bring
  the file back.
- Engine commit IDs quoted in older docs are pre-rewrite IDs. The historical
  `companion.ref` values were remapped and still resolve; the game repository's historical
  `engine.ref` pins name engine commits that no longer exist.
- GitHub still keeps `refs/pull/1/head` in each repository, pointing at the old history
  (engine `53d0172`, game `9b3a02b`). Removing it needs GitHub Support or new
  repositories. The owner deferred it on 2026-09-25 and expects to recreate the repositories;
  when that happens, check that no ref reaches the purged bitmap (release gate 5 stays open
  until then).

## What never goes in
- Retail game data, retail executables, PDBs, raw disassembly or decompiler output, and
  packet captures. This holds for both repositories and their whole history.
- SDK-licensed files. They belong in `openETQW-game`.
- New engine code carries `SPDX-License-Identifier: GPL-3.0-or-later`. Code adapted from
  Doom 3, dhewm3, RBDOOM-3-BFG or openQ4 keeps its original notices, gets an
  `SPDX-License-Identifier` line, and its donor commit is recorded in `docs/provenance.md`.

## Records
Record deliberate deviations from retail 1.5 in `docs/compat-ledger.md` and file origins
in `docs/provenance.md`. Keep `STATUS.md` honest about what was verified: each claim
carries its evidence level (`inspected` < `configured` < `compiled` < `unit-tested` <
`launched` < `map-loaded` < `match-tested`), host, OS, architecture and commit.
