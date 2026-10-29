<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Contributing

Agents: read `AGENTS.md` first. Work items are GitHub issues.

`main` changes only through pull requests, merged by the maintainer once the gates below
are met.

## Hosted CI is not running
Since 2026-09-24 the GitHub Actions minutes for these private repositories are used up.
Workflows do not start, so a missing check proves nothing either way. Until minutes are
available again, the local checks below replace CI. Do not make a repository public to
get CI back: both stay private until the maintainer decides to publish.

## Merge gates
1. **Local checks pass.** Run them from the repository root and put the result in the PR:
   - `actionlint` (workflow lint; CI used 1.7.12)
   - `python3 tools/ci/check_python.py` (syntax and pyflakes over `tests/` and `tools/`; needs `pyflakes==4.0.0`, the version CI used)
   - `python3 tools/ci/check_integration_skips.py` (every retail-data test must exit 77 without data; it starts stub binaries, never a real server)
   - `python3 tools/ci/check_include_case.py` (every `#include` matches the file's letter case; Linux is case-sensitive, macOS is not)
   - `python3 tools/ci/check_no_pii.py --diff origin/main` (nothing the branch adds, commit messages included, names a private host, user, LAN address, e-mail or home path; § What never goes in)
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
   Put the result in the PR with the platform (OS, architecture, GPU class), commit and data set. The tests use
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
   **Graphics changes** (anything that changes what is drawn: `src/renderer`, the shaders under
   `base/vkprogs` and the GL programs, materials, the viewmodel or sky code in openETQW-game)
   must carry the standard screenshot set from **every platform**: Windows x64 with ray tracing
   on and off, Windows arm64, Linux and macOS (owner decision, 2026-10-09). On each platform, build
   the engine and the game at `main` and capture, then build both at the PR and capture with
   main's positions, then compare (keep the output outside the checkout):
   `python3 tools/validation/screenshot_set.py capture --out ../shots/main --label <main commit>`
   `python3 tools/validation/screenshot_set.py capture --out ../shots/pr --label <commit> --anchors ../shots/main/screenshot_set.json`
   `python3 tools/validation/screenshot_set.py compare ../shots/main ../shots/pr --out ../shots/compare`
   Add `--rt 0` to both captures on a GPU without ray tracing. The client runs from the
   build's own tree, so it uses that build's shaders, with a private save path, so no saved
   setting differs between the captures. The sheets show retail game content, so they never
   go on the public repositories (§ What never goes in). The project's sessions upload each
   platform's contact sheets (`sheet_rt*.png` and `compare_sheet.png`, JPEG is fine) to a
   prerelease `screenshots-prNNN` on the private `jm2/openETQW-ops`
   (`gh release create -R jm2/openETQW-ops screenshots-prNNN --prerelease --title "Screenshots for #NNN" --notes "..."`,
   then `gh release upload -R jm2/openETQW-ops screenshots-prNNN <files>`; one release per
   PR), and post `compare.md` on the PR. Other contributors keep their sheets and describe
   what they saw. The author and a reviewing session look at every sheet before the merge and are the
   gate: the numbers and the heuristic warnings (a near-black viewmodel, a flat sky) only
   point at things, and their look decides. The owner is asked only when something is
   ambiguous, for example when it is unclear what retail looks like (owner, 2026-10-09). The
   tests check numbers only, and a dark viewmodel and a sky drawn as a box went unnoticed
   until someone looked at the screen.
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

## What never goes in
- Retail game data, retail executables, PDBs, raw disassembly or decompiler output,
  packet captures, and screenshots or captures that show retail game content. This holds for both repositories and their whole history.
- SDK-licensed files. They belong in `openETQW-game`.
- Private detail about the people and machines behind the work (owner, 2026-10-29; the
  repositories are to go public). Name a machine by its platform ("the Linux x64 host",
  "the Windows arm64 host"), never by host name. No LAN addresses or service ports of
  personal machines, user names, home paths (write `$HOME/etqw_data`, `C:\Users\<name>`),
  personal e-mail addresses, or which session runs on which machine. This covers files,
  commit messages, PR and issue text, comments and release notes. Logs stay on the host
  that made them; quote the lines that matter. `tools/ci/check_no_pii.py` enforces the
  file and commit part: its tree scan in the unit suite, its `--diff` mode for what a
  branch adds. A new private name goes into `tools/ci/pii_terms.sha256` as a digest
  (`--add-term NAME`), never as text. The files in `tools/ci/pii_baseline.txt` predate the
  rule and are being cleaned; add nothing to them that the rule forbids.
- New engine code carries `SPDX-License-Identifier: GPL-3.0-or-later`. Code adapted from
  Doom 3, dhewm3, RBDOOM-3-BFG or openQ4 keeps its original notices, gets an
  `SPDX-License-Identifier` line, and its donor commit is recorded in `docs/provenance.md`.

## Where a defect goes
Every defect or bug found is filed as a GitHub issue in the repository that owns the
code, after searching the open issues for a duplicate. One issue per defect, with the
symptom, the exact steps to reproduce it and the environment (platform, OS, commit, data set),
the mechanism with `file:line`, the scope (what the fix may touch and what it must not),
and a done-test a reviewer can run.
Put the measurements in the issue itself.

## Records
Record deliberate deviations from retail 1.5 in `docs/compat-ledger.md` and file origins
in `docs/provenance.md`. Keep `STATUS.md` honest about what was verified: each claim
carries its evidence level (`inspected` < `configured` < `compiled` < `unit-tested` <
`launched` < `map-loaded` < `match-tested`), platform (OS, architecture, GPU class) and commit.
