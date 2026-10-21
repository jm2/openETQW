<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Work queue

The shared to-do list for every session. Coordinator log: [#133](https://github.com/jm2/openETQW/issues/133); see its latest comment for who coordinates now (coordination §2). Take items from the top. The coordinator keeps it
in order, and every PR that changes an item's state updates its row. `owner` names the
session that holds the item; `—` means nobody does. Before taking an item, check its PR or
issue for anything newer than this file.

**Parked (2026-10-20, the owner's wrap-up).** Every openETQW session finished or pushed its
in-progress work and is parked: no PRs are open in either repository apart from this docs pass, and no host claims are held.
Each row below names the branch or comment where its work stopped and its exact next step. When
the owner resumes the sessions, start at §2's top row that has a next step.

Updated: 2026-10-20 evening by `openETQW-linux` (coordinator), after a quota-bounded sprint (§5). Earlier that day: merged since 2026-10-18 are #145, #146,
#149, #152, #153, #155–#158 and game #53–#56 (§5); filed #159 and #160. Before that,
2026-10-18, after a pass over every doc.

## 1. Open PRs, in merge order

| # | What | Owner | Head | Left to do |
|---|---|---|---|---|

## 2. Open issues, in priority order

| # | What | Owner | Next step |
|---|---|---|---|
| [#134](https://github.com/jm2/openETQW/issues/134) | Raster shadows: retail's `shadowVisDistMult` / `shadowProjectDist` limits are not applied, so far props' sun volumes fill the stencil (the jetpack seat on a network client: 84 draw pairs against 113 without entity casters). | `openETQW-win-arm64` (parked) | Read what retail's renderer does with them first; don't guess. No host has a retail 1.5 install to compare against. The owner allowed `openETQW-win-arm64` to download the retail 1.5 installer (`STATUS.md` § Owner decisions, 2026-10-20); nothing retail enters the repositories (rule 3). Not started because of the park. A graphics change: screenshot set. |
| [#122](https://github.com/jm2/openETQW/issues/122) | GDF rifle viewmodel: near-black on raster, tan with ray tracing. | — (parked) | Get a retail reference first: a retail 1.5 screenshot from the owner, or published retail footage. Until then, don't change either path. Escalate to the owner if no reference turns up. |
| [#135](https://github.com/jm2/openETQW/issues/135) | `mixed-caps`: B's 240 cap A1 'turn' sits at its threshold on `antec` (233–235.3 fps against 235.2; the test's own ceiling is about 282 with two clients) and fails on `main` too. | `openETQW-linux` | Not reproduced on 2026-10-20: 5 of 5 quiet runs on antec at main 24b2edf read B 240 'turn' 240.0 fps (ceiling 276–278), and every full suite since 2026-10-15 passed it. Closing on that evidence; reopen if it recurs. |
| [#141](https://github.com/jm2/openETQW/issues/141) | Mouse motion: SDL's sub-pixel `xrel`/`yrel` is truncated per event (`src/sys/sdl/sdl_events.cpp:238`), so slow motion is lost on high-DPI displays. From code, not reproduced. | `openETQW-mac` (parked) | Branch `fix/mouse-subpixel-deltas` at 42de177, no PR yet: the fix and its unit test pass, local checks are clean, and the full suite passed on macOS apart from a `seat-poses-listen` L3 flake (#162; 3/3 reruns pass). Next: the owner's Retina check, old build against new (commands on #141, issuecomment-6290237448), then the PR. Input path only. |
| [#147](https://github.com/jm2/openETQW/issues/147) | High-density displays: should `r_mode` / `r_custom*` mean pixels or points (SDL sizes on macOS are points)? | — | An owner question with three options; not urgent. |
| [#162](https://github.com/jm2/openETQW/issues/162) | `seat-poses-listen` L3 (held yaw): 2 of 16 cross-tick rates over 2.5x once on macOS (worst 3.53x); 3 of 3 reruns passed. | — | On the next failure, keep the log directory and look at the client's frame times at the stepping pairs. The bound stays. |
| [#159](https://github.com/jm2/openETQW/issues/159) | `all-stock-maps`: salvage's Strogg `aas_area_validity` fell to 53.8% once (antec, 2026-10-20); two reruns passed. | — | Cause found on `macmini-m1` (issuecomment-6305469953): the Strogg bot jumps from AAS area 3297 into an off-mesh pocket at (-768, 3215, 2991) and stays stuck there (`stuck_longest` 35.6 s; 20.9% with `--bot-seconds 120`). The check is right and the bound stays. Next: look at the spot in a client with `aas_showAreas 1` to tell a retail pocket from a port bug (AAS load or bot jump reachability). |
| [#160](https://github.com/jm2/openETQW/issues/160) | `seat-poses-listen`: H3 once saw no moving draws in the badger's driver seat (antec, 2026-10-20). | `openETQW-linux` | PR open (tests/seat-poses-h3-stall): a stalled titan phase is reported and driven once more, as #165 does for the jetpack. 5 of 5 quiet runs on antec didn't reproduce it (220–222 moving draws). |
| [#151](https://github.com/jm2/openETQW/issues/151) | `seat-poses` I1: the jetpack's flight can stall between close walls. Since #165 a stall is reported and flown once more, and a second stall fails. | — | The start sits between walls closer than one drive leg, so a retry can stall too: find a start with room, or a shorter leg (options on #151, issuecomment-6304223638). The 99% bound stays. |
| [#154](https://github.com/jm2/openETQW/issues/154) | `seat-poses`: no test holds a `freepivot` gun at its extent while the hull moves (late look, ledger 064). | `openETQW-win` (parked) | WIP on `tests/seat-poses-moving-edge` at de67612: L7's metric works (0.001° on main against 0.072° with the bug put back), but the peer's drive doesn't turn the hull moderately and repeatably. Next: a repeatable hull rotation (state on #154, issuecomment-6303561689). |
| [#148](https://github.com/jm2/openETQW/issues/148) | `ANTEC-WIN` drew at about 30 fps for some 15 minutes during a retail suite (2026-10-20 02:16–02:31Z); cause unknown. | — | If it recurs, capture what the issue lists. |

## 3. Milestone work, in order

Each item names its acceptance test. Details are in the linked plan.

1. **M5 step 14b, the hull lead** (`docs/hull-lead.md`). The code is in, off by default
   (ledger 067). A network client's and a listen host's `VEH_PRED` numbers per class are in
   §6 (owner: `openETQW-win`; merged as #157 with game #56). Left: the owner's review on a
   high-refresh display (owner-gated, §4).
2. **M5 step 14, what is left** (owner: `openETQW-win`): late look for `freepivot` views and the damped
   first-person `clampDamped*` view is merged (#152 with game #54). Left: the owner decides
   which seats join the default list (§4).
3. **M4, what is left** (`STATUS.md` § Milestones, M4). Done: classes, damage and kills,
   deployable delivery, both match endings, stopwatch and campaign transitions (meson
   `m4-*`, #104 and #107). Left: a match played out by bots (they play no objectives), and
   a started match on maps beyond the campaign's three. Acceptance: new meson tests pass on
   the retail 1.5 data on two platforms.
4. **Retail data discovery** (`STATUS.md` § Next steps, item 7): the Linux folder picker,
   and a person trying the macOS and Windows pickers (owner).
5. **Provenance labels** (`docs/provenance.md` §6): the minizip relabel and the other
   open label questions. Acceptance: `provenance_post_split.py` reports no gap, the game's
   `check_spdx.py` passes, and the §6 rows are resolved.
6. **`docs/platform-matrix.md`** (release gate, M7): what is verified per platform, with
   the evidence level, host and commit.
7. **Release gate 5, third-party binaries:** review `src/openal/lib/openal32.lib`, both
   copies of `eaxguid.lib` and zlib contrib's `.obj` files. Replace each, or record why it
   stays (`STATUS.md` § Release gate, item 5).
8. **The M1 and M2 gaps** (`STATUS.md` § Milestones):
   - M1: the `false` defaults of `build_engine` and `build_dedicated`;
   - M2: collision in the graphical client is never asserted;
   - M2: the headless render world is still stubbed.
     Its effects are recorded in ledger 013 and are not measured. Next: a test-only parity
     test that runs the same short scripted scenes on a listen server (real render world)
     and on a dedicated server (`idRenderWorldHeadless`) and compares the outcomes: a hit
     on a prone bot (render-model combat model), `RadiusPush` behind a wall, and a vehicle
     exit near the map edge. Its result is the evidence for the owner question in §4.

   Each gap gets a test or a recorded decision.
9. **M3, what is left** (`STATUS.md` § Milestones, M3):
    - ~~cross-OS pairs that include an arm64 host~~: done (#144). Linux arm64 (`macmini-m1`)
      passes X1–X4 both ways with Linux x64, Windows x64 and macOS arm64
      (issuecomment-6281426367, -6282183468, -6303053162, -6303056403);
    - weapon fire seen by the other player (a headless peer can't observe it; needs a
      Vulkan observer or a server-side check).

    Acceptance: `cross-os` X1–X4 each way per pair, recorded like the evidence rows of
    2026-10-16, with the server's `playerPos` polled every 0.25 s (coordination §5).

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
- **A Windows run on an NVIDIA or Intel GPU** (`STATUS.md` § Next steps, item 3). No such
  host is in the fleet; the owner would have to provide one.
- **Hosted CI:** comes back only with Actions minutes. Until then, local checks replace it.
- **#122's retail reference** if none can be found.
- **The headless render world (M2):** keep `idRenderWorldHeadless` as it is, or load the
  `.procb` areas and nodes on the dedicated server (`PointInArea`, `BoundsInAreas`;
  moderate), with render-model traces still out (they need render models; heavy).
  Recommended by `openETQW-linux-arm64` (2026-10-20): keep the stub for M2 as a recorded
  decision (ledger 013) until the §3 item 8 parity test measures the effects.
- **A person at the controls.** Smooth motion at high caps, and M3's "Vulkan client with a
  human at the controls", are judged by people on real displays (M5 D3), not by a test.

## 5. Recently done

2026-10-20, the evening sprint:
- #165 (#151, in part): a stalled jetpack flight in `seat-poses` I1 is reported and flown once more.
- #144's last pair, macOS arm64 ↔ Linux arm64: X1–X4 pass both ways at 318b638.
- #163 closed: no other `;`-chained checks.
- #164: what the sessions knew but the docs didn't.

2026-10-20:
- #149 (#128): off-view static casters cast stencil shadows. The review fix scales the caster box about the light, so the cull is conservative. Screenshot sets on all five platforms.
- #157 with game #56: the hull lead's `VEH_PRED` per class on a network client and a listen host (`lead-measure-join`, `-listen`).
- #158 (#136): `bot-gameplay`'s encounter is repeatable (inline bot AI, command-line join).
- #156: `screenshot_set.py`'s canyon anchor is outdoors, and the hangar is an extra view.
- #155 with game #55 (#126): samples in teleport transit leave the AAS tally.
- #153 (#150): the determinism replays turn tooltips off.
- #152 with game #54: late look for `freepivot` and the damped view.

#145 (2026-10-20; #127): `+set r_mode` was never ignored; the window cap now says when it shrinks a window, Linux and macOS cap at the real display, and the headless drivers don't cap. M5 step 3's seat part (2026-10-19, #146): `seat-poses-*` are the seat capture, and the new S5 checks the cockpit (`docs/m5-plan.md` step 3).

Closed on re-check (2026-10-18), with evidence:
- the mixed-caps producer gap: M5 step 8 removed it (`docs/m5-plan.md` step 8, ledger 059: A7/A7o pass, known gaps removed);
- the vehicle proxy gate: steps 13–14 replaced it for listed seats, so all that's left is the owner's default seat list (§4).

M3 cross-OS with the Mac, all four directions (2026-10-16, `STATUS.md` evidence), #140 (the other three), #139 (the first of them), #138 (the Mac Local Network notes), #132 (this runbook), #137 (STATUS current), #130 (dropped clients are told; listen-host `disconnect` ends its server), #129 (raster shadow volumes; #124 fixed), #131 (removed the Discord announcement), #125 (the screenshot tool and rule), #123 (the
raster sky), #118 (Stop disconnects the client), #117 (the vehicle-pop tolerance),
#112 (the Windows retail-data folder picker). Older work is in `STATUS.md` and the merged
PRs.
