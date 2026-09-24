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
   - `meson test -C build --suite unit` (asset-free unit tests, including the zero-SDK audit)
2. **Retail-data suite for runtime changes.** Anything touching the engine, the game or the
   network gets the full Meson suite with retail data:
   `ETQW_DATA_PATH=<directory containing base/pak000.pk4> meson test -C build -j 1`.
   Put the result in the PR with the host, OS, architecture and commit. The tests use fixed
   UDP ports, so run one suite at a time on a host.
3. **Independent review.** Someone other than the author (a person or a separate review
   session) reads the diff and the raw test output, not only the summary.
4. **CodeRabbit.** CodeRabbit reviews each PR. It currently allows about one review per
   hour: ask again with `@coderabbitai review` when the window opens, and say in the PR
   while it is out of quota. The ChatGPT Codex connector (`@codex review`) and Codacy, when
   they review, count the same way.
5. **Every finding is answered**, from the independent review and from the bots: fixed, or
   explained in the PR.
6. **Merge commit.** Branches are merged with a merge commit, which keeps the per-fix history.

When Actions minutes are available again, the `CI` workflow (`.github/workflows/ci.yml`)
must also be green. Its build legs skip while `openETQW-game` is private, and experimental
legs report without blocking.

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
  repositories; that is an open owner decision.

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
