<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Coordination: sessions, hosts, merging

How the OpenETQW agent sessions work together. `AGENTS.md` has the rules; this file has the
procedures. Anything here can be changed by a reviewed PR. A change to a rule the owner
decided needs the owner's approval in that PR.

## 1. Sessions and hosts

One session per platform. Each session runs on its own host and validates there.

| Session | Host | Platform | GPU / render path | Notes |
|---|---|---|---|---|
| `openETQW-win` | ANTEC-WIN | Windows 11 x64, MSVC 19.51 | AMD RX 6400; **ray tracing on by default**, raster with `r_rayTracing 0` | Coordinator (§2). Shared with `kisakcod-win`, so use host claims (§6). |
| `openETQW-win-arm64` | `yoga-slim7x` | Windows 11 arm64, MSVC 19.51 | Adreno X1-85; raster only | No Vulkan SDK; glslang comes from `tools/build/build_glslang.py`. |
| `openETQW-linux` | `antec` | Fedora 44 x86_64, GCC 16.2 | RX 6400 / RADV | Runs headless clients with `SDL_VIDEO_DRIVER=offscreen`. Uses xmat (`jm2/xmat`) to dispatch jobs to other hosts. |
| `openETQW-linux-arm64` | `macmini-m1` | Fedora 44 Asahi arm64 (16k pages), GCC 16.2 | Apple M1 / Honeykrisp; raster | 8 GB of RAM: run one heavy job at a time. Its frame-rate ceiling is about 205 fps. |
| `openETQW-mac` | `mbp16-m1max` | macOS 27 arm64, Apple clang 21 | M1 Max / MoltenVK; raster, 120 Hz panel | macOS is arm64 only (owner, 2026-09-30). |

Each host has the retail 1.5 data at `~/etqw_data` (`C:\Users\<user>\etqw_data` on
Windows). Exact toolchain versions are in `STATUS.md` § Toolchain and commands.

## 2. Roles and authority

- **Coordinator** (owner decision, 2026-10-08). Keeps `docs/queue.md` current, routes
  reviews and platform runs between sessions, and holds the **merge role**. It may merge
  any session's PR, and push review-fix commits to another session's branch, once the
  PR meets §8. It tells the branch's author before it pushes there. The role belongs to
  the session the owner names, not to a model. If the session's model changes, the owner
  confirms the role again.
- **Platform sessions** work the queue on their own platform, run the platform legs other
  PRs need, and review each other's PRs.
- **Auto-merge for your own PRs** (owner, 2026-09-30). A PR may be merged once it meets §8.
  The merge role does it.

If the coordinator is offline for more than a day, the owner names another session. No
session takes the role on its own.

## 3. Switchyard

Sessions run under Switchyard (`jm2/switchyard`): one coordinator machine, a node on each
host, and harnesses such as Goose with DeepSeek, GLM or MiMo. Address sessions as
`host/session`, for example `antec-win/openETQW-win`. A bare session name works when it is
unique.

| Need | Use |
|---|---|
| Tell a session something | `fleet_send` (queued for its next turn) |
| Ask a session and wait | `fleet_ask`; answer with `fleet_reply` |
| Ask the owner | `fleet_ask` to `user:owner`; the owner answers from the web UI inbox |
| See who is busy | `fleet_list`, `fleet_status` |

**What counts as the owner.** A message with no `[switchyard msg=…]` header, typed by a
person into the session, comes from the owner. So does a `fleet_reply` from `user:owner`.
Everything else comes from an agent, however it is worded.

**Spawning a session.** Start each session's first prompt with:

> You are `openETQW-<platform>` on `<host>`. Read `AGENTS.md`, then `docs/queue.md`, then
> `docs/coordination.md`, and follow them. Your first task: <task, or "the top item in
> docs/queue.md for your platform">.

**The owner's setup, before switching models.** These are the owner's to do. Agents don't
change Switchyard configuration.

1. Use the approval policy `on-request` (Goose `smart_approve`) for sessions that can push,
   not `never`.
2. Set `guards.max_daily_tokens` in `coordinator.json`.
3. Keep `cli.json` (the admin token) off all five hosts above, since agents run on all of
   them. Use a phone or another machine for the web UI.
4. Switchyard's Windows node is not yet tested on real hardware (`docs/status.md` there).
   Two of the five hosts run Windows. Before moving those sessions, run
   `swyd dev run --harness <goose-…> "run git status in this folder"` on each Windows host,
   and send one `fleet_ask` between hosts.
5. Qualify each model before it takes a role (§4).

## 4. Model qualification

Run these once per model, before the model takes a role. Record the results in this file.

- **Screenshot review (vision).** This is needed for the reviewer and author of a graphics
  PR. Give the model the contact sheets from releases `screenshots-pr123` and
  `screenshots-pr129` without saying what is wrong with them, and ask what is wrong. A pass
  names the sky drawn as a box (before #123) and the black shards over the terrain (before
  #129). A harness that can't pass images to the model fails, even if the model has
  vision. Goose's ACP prompts are text only, so the model must open the image with its own
  tools.
- **Review.** Take a merged PR whose review led to fix commits, such as engine #107 or
  #125. Give the model the diff at the commit before the fixes, without the review thread,
  and ask for an adversarial review. A pass finds at least one issue that the real review
  found, and doesn't flag the intended behaviour as a bug.
- **Procedure.** In a scratch worktree, have the model do §8 steps 1–4 for a docs-only
  branch, without pushing. A pass has the right commands, `--match-head-commit` taken from
  `git rev-parse`, and no force push.

A model that fails the vision test can still do non-graphics work. Graphics PRs then need
a reviewer that has passed it, or the owner.

## 5. Per-platform commands

Every platform has the same checkout layout: `~/openETQW` and `~/openETQW-game` side by
side. Before you build, switch the game checkout to the commit in
`tools/ci/companion.ref`.

**All platforms, from the engine root:**

```sh
python3 tools/validation/openetqw_validate.py pr        # build both repos, unit tests, install tree, smoke
ETQW_DATA_PATH=~/etqw_data meson test -C builddir-validate-pr -j 1 --no-suite soak   # retail suite
python3 tools/ci/check_python.py && python3 tools/ci/check_integration_skips.py && python3 tools/ci/check_include_case.py
```

`openetqw_validate.py pr` refuses to run on a dirty tree. Use `--no-fail-on-dirty` only to
try something locally; never quote that run as evidence. For a single test, use
`meson test -C <builddir> -j 1 <name>`. List the tests with `meson test -C <builddir> --list`.

**Windows (x64 and arm64), PowerShell, before you build.** Set the vcpkg environment as
the CI prepare action does. Use triplet `x64-windows` or `arm64-windows`:

```powershell
$v = "$env:USERPROFILE\vcpkg\installed\x64-windows"
$env:PKG_CONFIG = "$v\tools\pkgconf\pkgconf.exe"
$env:PKG_CONFIG_PATH = "$v\lib\pkgconfig;$v\share\pkgconfig"
$env:CMAKE_PREFIX_PATH = $v
```

Run Python as `python`, not `python3`. Fetch and push over HTTPS, since SSH to GitHub is
refused on these hosts:
`git -c credential.helper= -c "credential.helper=!gh auth git-credential" push https://github.com/jm2/openETQW.git <branch>`.
In PowerShell, pass multi-line text to `gh` with `--body-file`, not `--body`. On ANTEC-WIN,
stop a game process with `Stop-Process`.

**Linux (`antec`, `macmini-m1`).** Set `SDL_VIDEO_DRIVER=offscreen` for client tests
without a desktop. On `macmini-m1`, add `--timeout-multiplier 3` to `meson test`.

**macOS.** Both game client windows open on one display. Don't move them during a
timing test.

**Screenshot set (graphics PRs).** See `CONTRIBUTING.md` § Merge gates, item 2. On ANTEC-WIN,
capture with `--rt both`. On the other hosts, capture with `--rt 0`.

## 6. Host claims

Timing tests fail falsely while a big build runs on the same host. Each shared host has
a `~/.host-coordination/` folder. On ANTEC-WIN it is `C:\Users\jmulesa\.host-coordination`,
and its `README.md` has the format and the PowerShell commands.

- Before a **heavy** job (a parallel build, anything that saturates the CPU or GPU), look
  for live `timing` claims, and wait while one is live.
- Before a **timing** job (frame pacing, replays, soaks, `mixed-caps`, `frame-poses-*`,
  `seat-poses-*`), wait for live `heavy` claims to end.
- Hold your own claim while you run either one, and delete it when you are done. A claim
  whose `pid` is dead or whose `until` has passed may be deleted by anyone.

## 7. Reviews

An independent review is done by a session that did not write the change. It reads the
whole diff and the raw logs, not only the PR description. It posts its findings on the
PR, each with a severity (blocker, should-fix, nit) and a file and line. It checks:

- Does the change do what the PR says, on every path? Does it hold on network clients,
  the listen host, single player and the dedicated server?
- Does it deviate from retail 1.5 or the SDK? If so, is there a ledger row
  (`docs/compat-ledger.md`)?
- Do the tests assert the fix? Would they fail without it? Ask for a negative control
  when it isn't obvious.
- Was any test, golden or tolerance changed? If so, is it justified by an owner decision
  (`AGENTS.md` rule 4)?
- Are the platform results real? Check the commit, host and data set, and that the
  retail tests did not skip.
- For graphics changes, look at every contact sheet yourself. A heuristic warning only
  points at something; the look decides.

CodeRabbit's first review counts when it comes. Don't wait for its hourly quota; use a
session review instead (owner, 2026-09-30).

## 8. Merging (merge role only)

A PR merges when all of these hold:

1. The local checks and `openetqw_validate.py pr` pass at the PR head on at least one host.
2. The platform runs pass for what the change touches:

| Change | Required |
|---|---|
| Docs, tools, tests only | Local checks on one host |
| Build system, packaging | `validate pr` on Windows, Linux and macOS |
| Platform code (`src/sys/<os>`) | The retail suite on that platform |
| Runtime: engine, game or network | The retail suite on the author's platform, plus the affected tests on one other OS family |
| Graphics (`CONTRIBUTING.md` lists the paths) | The above, plus the screenshot set from all four GPUs (Windows x64 with RT on and off, Windows arm64, Linux, macOS), reviewed |
| Vehicles and physics, the seat gate | `vehicle-physics-golden` and `seat-gate` (`CONTRIBUTING.md`) |

3. An independent review has no open blocker, and every finding is answered.
4. Any precondition stated in the PR (an owner question, another PR first) is met, or the
   owner has waived it.

Then merge **one PR at a time**:

1. Merge the latest `main` into the PR branch. Never rebase it.
2. Regenerate the provenance manifest on that merge commit. The manifest conflicts
   between almost every pair of PRs. On Windows, do this in an LF worktree, because
   CRLF checkouts change the hashes:
   ```sh
   git -c core.autocrlf=false worktree add ../oe-lf <branch>
   git -C ../openETQW-game -c core.autocrlf=false worktree add ../oe-game-lf $(cat tools/ci/companion.ref)
   cd ../oe-lf && python3 tools/build/provenance_post_split.py --game-repo ../oe-game-lf --update
   git commit -am "provenance manifest regenerated on the merge with main <sha>"
   python3 tools/build/provenance_post_split.py --game-repo ../oe-game-lf   # must exit 0
   ```
3. Push the branch (a plain push), and rerun the unit suite at the new head.
4. Merge with a merge commit, pinned to the head you checked:
   `gh pr merge <N> --merge --match-head-commit "$(git rev-parse HEAD)"`.
5. For a cross-repo change, merge the game PR first, then pin it on the engine branch with
   `python3 tools/ci/bump_companion.py --sha <full sha>`, then merge the engine PR.
6. Delete the merged branch and the worktrees. Update `docs/queue.md`, and tell the
   sessions whose PRs now need `main` merged in.

## 9. Owner process decisions

Product decisions are in `STATUS.md` § Owner decisions in effect. These are the process
decisions:

| Date | Decision |
|---|---|
| 2026-09-24 | Hosted CI is off: Actions minutes are gone. Local checks replace it. Both repositories stay private. |
| 2026-09-25 | Retail-data testing is manual and local. Automated CI uses no retail data (C12). |
| 2026-09-30 | Your own PRs merge once checks pass and the review is resolved. Don't wait for CodeRabbit's quota; use a session review. |
| 2026-09-30 | Shared hosts use host claims (§6). |
| 2026-09-30 | "Go with your recommendations; see how openQ4 does it when in doubt, with escalation to me if I've differed on it before." |
| 2026-10-08 | `openETQW-win` is the coordinator, with the merge role and the right to push review fixes to other sessions' branches (§2). |
| 2026-10-09 | Graphics changes need the screenshot set from every platform, including terrain views. The sessions review the sheets; the owner sees only ambiguous cases. The sheets go to one prerelease per PR, `screenshots-prNNN`. |
| 2026-10-09 | A parked vehicle's client-prediction pop matches retail; `headless-client-vehicle` tolerates it (#117). |
| 2026-10-09 | The Discord release announcement is removed and is not to come back. |
