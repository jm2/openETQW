<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Work queue

The shared to-do list for every session. Coordinator log: [#133](https://github.com/jm2/openETQW/issues/133); see its latest comment for who coordinates now (coordination §2). Take items from the top. The coordinator keeps it
in order, and every PR that changes an item's state updates its row. `owner` names the
session that holds the item; `—` means nobody does. Before taking an item, check its PR or
issue for anything newer than this file.

Updated: 2026-10-15 by `openETQW-win`. Work has been paused since 2026-10-09 for the weekly
quota; nothing has merged since #131.

## 1. Open PRs, in merge order

| # | What | Owner | Head | Left to do |
|---|---|---|---|---|
| [#129](https://github.com/jm2/openETQW/pull/129) | Raster stencil shadows build real volumes; fixes #124's black shards. Ledger 074. | `openETQW-win-arm64` | 75b4455 | (1) Answer CodeRabbit's finding at `src/renderer/tr_light.cpp:2122` (cached vertex welds may go stale on a cache hit). (2) Merge `main` and regenerate the manifest (coordination §8). (3) Rerun the full retail suite at the new head on win-arm64. (4) All five screenshot sets at the new head, compared against `main` and uploaded to `screenshots-pr129` (coordination §8): 75b4455 changed which casters get volumes, so the sets taken at 73fca6b (win-arm64, macOS APPROVE, Linux arm64) are stale. (5) An independent review that looks at every sheet. **Open question:** on a network client the jetpack seat draws 85 pairs against `main`'s 108; find the cause or show it is unrelated. What is known (win-arm64): it is the GPU (`fence_ms` about 18 of 23 ms per frame at 73fca6b); 75b4455's skipping of `noShadow`, weapon- and model-depth-hacked casters fixed `seat-poses-join` I1c (18 to 28 pairs) but not the whole gap; on foot at the same spot the PR is faster than `main` (189 vs 143 fps), and `seat-poses-devmap`/`-listen` pass, so look at what a remote client draws in the jetpack seat. A devmap repro with `enterVehicle 0 vehicle_icarus` drew no frame timing; use the `seat-poses-join` harness with `com_logFrameTiming 1` added to the client's cvars, and the `Vulkan shadow volumes:` shutdown line. `m4-campaign` failed once at 73fca6b with the headless client exiting 0xC0000005 and passed on rerun; it is not this PR, since the dedicated binary does not compile `tr_light.cpp`. The valley tank's shadow question (issuecomment-6085445717) needs someone who knows retail's valley; if nobody does, ask the owner. |
| [#130](https://github.com/jm2/openETQW/pull/130) | The server tells a dropped client, and `disconnect` ends a listen host's server (#119, #120). Ledger 075. | `openETQW-linux` | 514bcac | After #129 merges: (1) merge `main` and regenerate the manifest; (2) run `client-server-kick`, `client-server-timeout`, `listen-host-disconnect`, `client-error-drop`, `client-error-load-drop`, `remote-client-map-change`, `reconnect-restart`, `client-network-join` and `client-network-listen` on Windows x64; (3) answer CodeRabbit's finding at `src/framework/async/AsyncServer.cpp:1113`; (4) get an independent review. macOS and Linux arm64 each passed 9/9 at 514bcac. Linux x64 (antec), full suite at 568b101 (the same code; only the ledger number and manifest changed since): 64 OK, 1 fail (`mixed-caps` A1, environmental: `main` 7782751 failed it the same way on the same host), 1 skip. The merge with #129 will conflict in `docs/compat-ledger.md`, where both insert a row after 073: keep 074 (#129), then 075 (#130). The worktree is `/var/tmp/openetqw-net/engine` on antec, branch `net/client-drop-stop`. |

## 2. Open issues, in priority order

| # | What | Owner | Next step |
|---|---|---|---|
| [#128](https://github.com/jm2/openETQW/issues/128) | Raster stencil shadows: casters outside the view cast no shadow. | — | After #129. Raster path, so test on win-arm64, Linux arm64 or macOS. A graphics change, so it needs the screenshot set. |
| [#122](https://github.com/jm2/openETQW/issues/122) | GDF rifle viewmodel: near-black on raster, tan with ray tracing. | `openETQW-win` | Get a retail reference first: a retail 1.5 screenshot from the owner, or published retail footage. Until then, don't change either path. Escalate to the owner if no reference turns up. |
| [#127](https://github.com/jm2/openETQW/issues/127) | `+set r_mode N` on the command line is ignored at startup. | — | Any platform. Small. Fixing it lets `screenshot_set.py` capture above 640x480. |
| [#126](https://github.com/jm2/openETQW/issues/126) | `all-stock-maps`: slipgate's `aas_area_validity` flakes on teleporter transit samples. | — | Test-only fix: exclude samples taken in transit, and explain why in the PR. Don't loosen the bound (`AGENTS.md` rule 4). |
| — | `antec`: `mixed-caps` misses A1 at times. | `openETQW-linux` | Likely load on the host, such as the owner's GNOME indexer. Run it on a quiet host before treating it as a bug. Seen on 2026-10-09: B's 240 cap read 233–234 fps in "turn" (need ≥ 235.2), with a `ceiling` of about 281, against 324–329 that morning; `main` 7782751 failed it identically. kisakcod-linux's runs changed no GPU setting. The GNOME session was already up during the morning's good runs, so the indexer (`localsearch-3`) is the remaining suspect; it is the owner's to pause. |
| — | `screenshot_set.py`: the canyon anchor is indoors. | — | Add an outdoor canyon anchor; take eye positions from `getviewpos`. |

## 3. Milestone work, in order

Each item names its acceptance test. Details are in the linked plan.

1. **M5 step 14b, the hull lead** (`docs/hull-lead.md`). The code is in, off by default
   (ledger 067). Left: a network client's `VEH_PRED` numbers per class, then the owner's
   review on a high-refresh display (owner-gated, §4).
2. **M5 step 3, the last part:** frame-pose capture in vehicle seats (D2). The devmap
   variant is done. First check whether `seat-poses-join`/`-listen` (step 14) already
   cover it. If they do, mark step 3 done in `docs/m5-plan.md` and cite their checks. If
   they don't, `frame-poses-*` gain seat checks, with a negative control.
3. **M5 step 14, the remaining seats:** `pivot` and `freepivot` views wait for joint
   blending and stage 5 (`docs/vehicle-responsiveness.md`). Acceptance: `seat-poses`
   covers them, and the owner decides which seats join the default list.
4. **M4 gameplay tests** (`STATUS.md` § Milestones, M4): classes, damage and kills,
   deployable delivery, a defender or time-limit win, stopwatch and campaign transitions,
   and a started match on maps other than Valley. Acceptance: new meson tests pass on the
   retail 1.5 data on two platforms (any two; STATUS's older "macOS arm64" is just where the work started).
5. **Retail data discovery** (`STATUS.md` § Next steps, item 7): the Linux folder picker,
   and a person trying the macOS and Windows pickers (owner).
6. **Provenance labels** (`docs/provenance.md` §6): the minizip relabel and the other
   open label questions. Acceptance: `provenance_post_split.py` reports no gap, the game's
   `check_spdx.py` passes, and the §6 rows are resolved.
7. **`docs/platform-matrix.md`** (release gate, M7): what is verified per platform, with
   the evidence level, host and commit.
8. **Release gate 5, third-party binaries:** review `src/openal/lib/openal32.lib`, both
   copies of `eaxguid.lib` and zlib contrib's `.obj` files. Replace each, or record why it
   stays (`STATUS.md` § Release gate, item 5).
9. **The M1 and M2 gaps** (`STATUS.md` § Milestones):
   - M1: the `false` defaults of `build_engine` and `build_dedicated`;
   - M2: collision in the graphical client is never asserted;
   - M2: the headless render world is still stubbed.

   Each gap gets a test or a recorded decision.
10. **Leftovers to re-check against current `main`:** STATUS lists these as open, but
    later steps may have closed them. Close each with evidence, or give it its own row:
    - the mixed-caps producer gap (A7/A7o, before step 8);
    - the vehicle proxy gate (`docs/frame-path.md` §9).

**Before you add a row:** compare `STATUS.md` § Milestones and § Release gate with this
list. Anything open there and missing here belongs in this section or in §4.

## 4. Owner-gated

No session can finish these alone. Prepare the evidence, then ask the owner
(`fleet_ask` to the owner's address, coordination §3).

- **Release gate 1, the jmarshall licence.** Blocking for any public release. The owner
  deferred it on 2026-09-25. Nothing to do until the owner reopens it.
- **Publishing.** Both repositories stay private until the owner decides otherwise; the
  owner expects to recreate them first.
- **The hull lead:** the p95 bound and each class's L, and which driver and pilot seats
  join the default list. Needs a review on a high-refresh display.
- **Render portals, stage 2:** the network decision (`docs/render-portals.md`).
- **Mac M3 cross-OS runs:** the macOS Local Network permission and the firewall rules.
  The owner sets these: grant Local Network to the terminal app (System Settings → Privacy &
  Security → Local Network), then restart its tmux server. For a Mac-hosted server, also
  `sudo /usr/libexec/ApplicationFirewall/socketfilterfw --add ~/openETQW/build/openETQW-ded_arm64`
  and `sudo … --unblockapp` on the same path. Then `OPENETQW_REMOTE_SERVER=host:port meson test -C build cross-os`
  both ways against `antec` and ANTEC-WIN.
- **A Windows run on an NVIDIA or Intel GPU** (`STATUS.md` § Next steps, item 3). No such
  host is in the fleet; the owner would have to provide one.
- **Hosted CI:** comes back only with Actions minutes. Until then, local checks replace it.
- **#122's retail reference** if none can be found.

## 5. Recently done

#131 (removed the Discord announcement), #125 (the screenshot tool and rule), #123 (the
raster sky), #118 (Stop disconnects the client), #117 (the vehicle-pop tolerance),
#112 (the Windows retail-data folder picker). Older work is in `STATUS.md` and the merged
PRs.
