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
| `openETQW-mac` | `mbp16-m1max` | macOS 27 arm64, Apple clang 21 | M1 Max / MoltenVK; raster, 120 Hz panel | macOS is arm64 only (owner, 2026-09-30). Shared with `kisakcod-mac`; there is no `~/.host-coordination` here, so the sessions agree by message (§6). Also an xmat build host for `antec`. Older text calls it `mbp-m1max`. |

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

**`antec` specifics** (`openETQW-linux`):
- Scratch goes under `/var/tmp`, never `/tmp`. `/tmp` is a RAM tmpfs with a per-user
  quota, and it fails with `Disk quota exceeded` mid-build or mid-push. Set
  `TMPDIR=/var/tmp` for test runs.
- Fetch and push over HTTPS, as on Windows: tool shells have no SSH agent, so `origin`
  fails with `Permission denied (publickey)`.
  `git -c credential.helper='!gh auth git-credential' fetch https://github.com/jm2/openETQW.git +refs/heads/main:refs/remotes/origin/main`
- To test a PR without touching `~/openETQW`, use worktrees. Run from `~/openETQW`, in one shell
  (`W` is reused; `pr/N` comes from
  `git -c credential.helper='!gh auth git-credential' fetch https://github.com/jm2/openETQW.git pull/<N>/head:refs/remotes/pr/<N>`):
  ```sh
  W=/var/tmp/oe-<name>; mkdir -p $W
  git worktree add --detach $W/engine <engine sha or pr/N>
  git -C ~/openETQW-game worktree add --detach $W/game "$(git show <engine sha or pr/N>:tools/ci/companion.ref)"
  ln -sfn $W/game $W/openETQW-game     # the engine's meson finds ../openETQW-game
  (cd $W/game && meson setup build -Duse_pch=false && ninja -C build)
  cd $W/engine && meson setup build -Duse_pch=false -Dplatform_backend=sdl3 \
    -Dbuild_engine=true -Dbuild_dedicated=true -Dbuild_games=true -Dbuild_tests=true && ninja -C build
  ln -sfn ../../openETQW-game/build/game_x64.so build/game_x64.so
  ```
  Without the four `-Dbuild_*` options the engine configures, but no binaries are built.
  Remove the worktrees afterwards: `git worktree remove --force …`, then `git worktree prune`.
- Full retail suite in that tree (66 tests, about 65 minutes on 2026-10-09):
  `SDL_VIDEO_DRIVER=offscreen ETQW_DATA_PATH=$HOME/etqw_data TMPDIR=/var/tmp meson test -C build -j 1 --no-rebuild --logbase <name>`.
  The full log is `build/meson-logs/<name>.txt`.
- One stock map: `python3 tests/test_all_stock_maps.py --ded-binary build/openETQW-ded_x64 --jobs 1 --maps slipgate --log-dir <dir>`.
- `tests/test_valley_objective_adversarial.py` is not a meson test. Run it as a script.
- Never `pkill -f <pattern>` when the pattern can match your own shell's command line,
  because it kills the agent's shell. Find the PID with `ps -eo pid,args | grep <name> | grep -v grep`,
  then `kill <pid>`.
- `cross-os` skips (exit 77) on antec. A full run is "N OK, 0 fail, 1 skip".
- `mixed-caps` and `high-refresh-*` A1 measure frame throughput and read low under host
  load. Before blaming a change, read the `ceiling N` value in the A1 lines: 324–329 fps
  on a quiet antec (2026-10-09 morning), about 281 when it failed that afternoon. Rerun the
  test on `main` on the same host. If `main` fails too, it is the host.

**xmat** (`jm2/xmat`; binary `~/xmat/xmat` on antec). It builds one commit on several
hosts over ssh, with antec as the controller.
- `openETQW-linux` owns the fleet file (`~/.config/xmat/fleet.toml`) and the target setup
  for every project. Other projects add only their own `.xmat.toml`.
- `~/xmat/xmat doctor` checks the targets; `xmat run [<commit>]`, `xmat log <target>` and
  `xmat get` are the rest.
- openETQW has no `.xmat.toml` (2026-10-15), so platform runs are done by each host's own
  session, not by xmat.
- Setting up a new target needs the owner's approval in that host's own session.
- Message `openETQW-win` before any xmat run on ANTEC-WIN beyond a quick build.
- `~/xmat/.git/info/exclude` must list `xmat`; otherwise the binary reports `+dirty`
  and `doctor` shows a version mismatch.

**`macmini-m1` specifics** (builddir `build` in both repos):

```sh
cd ~/openETQW-game && git checkout <companion.ref> && meson setup --reconfigure build && ninja -C build
cd ~/openETQW && ln -sfn ~/openETQW-game/build/game_arm64.so build/game_arm64.so   # once per builddir
ninja -C build
SDL_VIDEO_DRIVER=offscreen ETQW_DATA_PATH=~/etqw_data meson test -C build -j 1                # retail suite, ~60-75 min
SDL_VIDEO_DRIVER=offscreen ETQW_DATA_PATH=~/etqw_data meson test -C build <name>              # one test
```

- Run `meson setup --reconfigure build` in the game repo after every pull or checkout. Its
  `meson.build` globs sources at configure time, so a new `.cpp` is missed and the link
  fails with undefined references.
- `meson test` with both `--suite X` and a test name prints nothing. Run them separately.
- Stop a client with `pkill -x openETQW-client`. `pkill -f` also matches the harness's
  own Python process.
- The host auto-updates (dnf, kernel and SDL3). When results shift on plain `main`, check
  `rpm -qa --last | head -30` and A/B on `main` before blaming a branch.
- Push over HTTPS with `gh` credentials, as on Windows; SSH to GitHub fails here.
- Screenshot sets work offscreen under Honeykrisp
  (`SDL_VIDEO_DRIVER=offscreen python3 tools/validation/screenshot_set.py capture ...`).
  Upload the sheets as JPEG with `gh release upload screenshots-prNNN <files>`, or use
  `gh release create screenshots-prNNN --prerelease ...` if no platform has made the
  release yet. `gh` can't attach images to a comment.

**After merging `main` into a branch** (the manifest conflicts often): take `main`'s
`docs/provenance-manifest.json`, commit the merge, then regenerate on the committed merge
with the game at the pin, and commit that:
`python3 tools/build/provenance_post_split.py --game-repo <game checkout at companion.ref> --update`.
Running it before the merge commit exists is what left an `addedIn` null (2026-10-08).

**Test ports.** Before adding a networked test, check its server port and every client
port (`port + 1`, …) against every literal port in `tests/meson.build` and each
`default=` in `tests/*.py`. Two tests sharing a port collide once meson runs them in
parallel (m4-stopwatch was moved to 29985 for that, #107).

**macOS (`mbp16-m1max`).** Build the game first, then the engine.
`build` is the working build directory here (`validate pr` uses its own):

```sh
ninja -C ~/openETQW-game/build && ninja -C ~/openETQW/build
ETQW_DATA_PATH=~/etqw_data meson test -C ~/openETQW/build --no-rebuild -j 1 <names>
```

- Both game client windows open on one display. Don't move them during a timing test.
- The engine loads the game module from its own directory first:
  `build/game_arm64.dylib` is a symlink to `~/openETQW-game/build/game_arm64.dylib`. To
  test another game build, re-point the symlink for the run and restore it. A module in
  the data's `base/` is ignored while the symlink exists.
- The engine stages the game's headers only at configure time. After editing game idlib or
  interface headers, run `meson setup --reconfigure build` in the engine, or it builds
  against stale headers.
- When Homebrew upgrades `vulkan-loader`, the old Cellar path stays in Meson's cache and the
  link fails (`libvulkan.dylib … missing`). Fix it with `meson setup --reconfigure --clearcache build`.
- The client binary is `build/openETQW-client_arm64`, not an `.app`. Pass it to
  `screenshot_set.py --client`. Shaders compile at runtime from the tree's `base/vkprogs`,
  which is gitignored, so stage shader edits with `git add -u` (or `-f`).
- **Packaging:** see `docs/releasing.md`. The `.app` takes SDL3, the Vulkan loader, MoltenVK and the game
  module from `Contents/Frameworks`, and MoltenVK through `Contents/Resources/vulkan/icd.d`.
- **Local Network privacy:** macOS refuses UDP to LAN hosts other than the gateway
  (`EHOSTUNREACH`) for a terminal app without the Local Network permission. That blocks
  the M3 `cross-os` legs from this Mac (queue §4, owner-gated). Loopback tests are unaffected.
- **Before heavy or timing jobs,** message `kisakcod-mac` (same Mac) to hold its builds,
  and tell it when you're done. It asks before its own heavy builds.
- **xmat:** this Mac serves `antec`'s controller through `~/.ssh/xmat-forced.sh`, which
  allows only `xmat host`, `host-file` and `host-info`. Any change under `~/.ssh` is the
  owner's to make. Never widen those keys unless the owner asks directly.

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

**`antec` has no `~/.host-coordination/` (2026-10-15).** Other projects share it:
`kisakcod-linux`, and Q3A, which builds under `/var/tmp/q3-*`. Coordinate by message:
- Before a timing run, ask `kisakcod-linux` to hold heavy builds and GPU runs, with an
  end time. It answers "go" or "busy until X", holds when asked, and says when it is done.
- When it asks for antec, answer "go", or "busy until X" with your suite's progress.
- Check `uptime` and `ps -eo pcpu,comm --sort=-pcpu | head` before blaming a timing failure.
- Messages to non-openETQW sessions are for load and heads-ups only (§9, 2026-10-03).

`macmini-m1` has no `~/.host-coordination` folder (as of 2026-10-15). It is shared with
`kisakcod-linux-arm64`, a different project, and the two coordinate by message instead.
Before a heavy or timing job, send it a heads-up with the expected length and wait for
its ack. Send "done" when the job is finished. It does the same in return.

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
| 2026-09-24 | Owner, in the `openETQW-mac` session: "We're out of minutes for private repos for now: do you best with independent local reviews until that changes. Do not make the repos public under any circumstances until you hear otherwise from me." Later: "Stay private, gates by convention." Never push the pre-purge backup bundles in `~/openETQW/.agents/backups-2026-09-24-prepurge/`; they hold the purged retail bitmap. |
| 2026-09-25 | Retail-data testing is manual and local. Automated CI uses no retail data (C12). |
| 2026-09-30 | Your own PRs merge once checks pass and the review is resolved. Don't wait for CodeRabbit's quota; use a session review. |
| 2026-09-30 | Shared hosts use host claims (§6). |
| 2026-09-30 | "Go with your recommendations; see how openQ4 does it when in doubt, with escalation to me if I've differed on it before." |
| 2026-10-03 | Sessions talk within their own project: "ensure openetqw is talking to openetqw sessions and likewise for kisakcod". Notices about a shared host across projects are fine. Given to `openETQW-linux-arm64`. |
| 2026-10-03 | "limited to single-thread/one agent per session/machine": no subagents or multi-agent workflows; one session per host does its own planning, work and reviews. Given to `openETQW-linux`. |
| 2026-10-03 | "ensure your messages go to the other openetqw sessions only", with the exception "you can message more widely, but only for machine loading/heads up purposes so we don't overload macmini-m1 especially since it only has 8gb ram". Given to `openETQW-linux`. |
| 2026-10-06 | Idle hosts test open work: "If the machine is idle and there are open changes or pending work, you're authorized to proactively test/fix in the idle gaps." This replaced "run the full suite only when requested". Merging still needs the merge role. Given to `openETQW-linux-arm64`. |
| 2026-10-08 | `openETQW-win` is the coordinator, with the merge role and the right to push review fixes to other sessions' branches (§2). |
| 2026-10-09 | Graphics changes need the screenshot set from every platform, including terrain views. The sessions review the sheets; the owner sees only ambiguous cases. The sheets go to one prerelease per PR, `screenshots-prNNN`. |
| 2026-10-09 | Owner, in the `openETQW-mac` session: "I don't need to look at every single screenshot unless there ambiguity or an escalation. Please self-review screenshots for correctness and be the primary gate unless further review required." |
| 2026-10-09 | A parked vehicle's client-prediction pop matches retail; `headless-client-vehicle` tolerates it (#117). |
| 2026-10-09 | The Discord release announcement is removed and is not to come back. |
