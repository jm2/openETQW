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

Updated: 2026-10-22 by `openETQW-linux` (coordinator): the review state of §3 items 3, 8
and 9 and of #162. Updated 2026-10-21 after the overnight work (§5); earlier that day, merged
since 2026-10-18 were #145, #146, #149, #152, #153, #155–#158 and game #53–#56 (§5), and
#159 and #160 were filed. Before that, 2026-10-18, after a pass over every doc.

## 1. Open PRs, in merge order

| # | What | Owner | Head | Left to do |
|---|---|---|---|---|
| [#180](https://github.com/jm2/openETQW/pull/180) | Build helpers, one command per platform (`scripts/build-*`, `docs/build-scripts.md`; the owner's request, 2026-10-22). | `openETQW-mac` | `build/helper-scripts` | Full runs of `build-linux.sh` on Linux x64 and arm64 and of `build-windows.ps1` on Windows x64 and arm64; an independent review. |

## 2. Open issues, in priority order

| # | What | Owner | Next step |
|---|---|---|---|
| [#122](https://github.com/jm2/openETQW/issues/122) | GDF rifle viewmodel: near-black on raster, tan with ray tracing. | — (parked) | Get a retail reference first: a retail 1.5 screenshot from the owner, or published retail footage. Until then, don't change either path. Escalate to the owner if no reference turns up. |
| [#141](https://github.com/jm2/openETQW/issues/141) | Mouse motion: SDL's sub-pixel `xrel`/`yrel` is truncated per event (`src/sys/sdl/sdl_events.cpp:238`), so slow motion is lost on high-DPI displays. From code, not reproduced. | `openETQW-mac` (parked) | Branch `fix/mouse-subpixel-deltas` at 42de177, no PR yet: the fix and its unit test pass, local checks are clean, and the full suite passed on macOS apart from a `seat-poses-listen` L3 failure (#162, since reproduced 6 of 10 on mbp16: listen-host CPU spikes). Next: the owner's Retina check, old build against new (commands on #141, issuecomment-6290237448), then the PR. Input path only. |
| [#147](https://github.com/jm2/openETQW/issues/147) | High-density displays: should `r_mode` / `r_custom*` mean pixels or points (SDL sizes on macOS are points)? | — | An owner question with three options; not urgent. |
| [#162](https://github.com/jm2/openETQW/issues/162) | `seat-poses-listen` L3 (held yaw): cross-tick rates over 2.5x on macOS. | `openETQW-mac` | Reproduced 6 of 10 on mbp16 in the cap-120 driver phase; the cause is CPU work spikes on the listen host, not GPU waits (issuecomment-6306063943). Branch `fix/seat-poses-listen-l3` at `1ff001f` (base 2b4a158; patch `208e03b6…cdf`, 10,644 B): one retry after a draw gap of at least 2x the median; the 2.5x L3 bound is unchanged and a second stall still fails. Code-reviewed at Linux arm64 and macOS with no blocker and one nit: 2.0x sits just under a two-vblank gap at the saved 8.511 ms median (1.9x would cover it), while the saved 37.447 ms gap is caught. The retry-pass path is statically reviewed, not run. Next: the listen run needs a display-capable host (mbp16 has no display, so zero listen runs there) or the owner's Retina check. Never apply the superseded a7aa73a/fefa519f. |
| [#159](https://github.com/jm2/openETQW/issues/159) | `all-stock-maps`: salvage's Strogg `aas_area_validity` dips when a bot lands in a gap in retail's own AAS (no area or reachability in a 64 u strip between areas 3297 and 2747). | — (owner-gated, §4) | The cause is retail data, not a port bug (issuecomment-6306739573). Whether to exclude recorded retail AAS gaps from the check is the owner's call (§4). |
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
   the retail 1.5 data on two platforms. The two tests are written on branch head `8d59718`
   (patch `m4item3-engine.patch`, 24,047 B, sha256 `7fc78335…1e1d`); independent
   review found one should-fix, both new tests missing from `docs/provenance-manifest.json`,
   fixed at that head, and nothing else. **Neither test has been run on any platform yet.**
   Next: one run on macOS arm64, then one on a second platform.
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
   - ~~M1: the `false` defaults of `build_engine` and `build_dedicated`~~: both default to `true` (#173);
   - M2: collision in the graphical client is never asserted;
   - M2: the headless render world is still stubbed. Its effects are recorded in ledger 013,
     and the parity test that measures them is PR [#178](https://github.com/jm2/openETQW/pull/178)
     with game [#60](https://github.com/jm2/openETQW-game/pull/60) (engine `146e44e`, game
     pin `8a13028`). Evidence at that head: `m2-render-world-parity` 1/1 OK 26.10 s on antec
     x86_64 (llvmpipe/offscreen, not GPU raster), both processes exit 0, `openetqw_validate.py pr`
     PASSED, provenance 0 missing. Reviewed at
     Windows with a full assertion replay against the raw log and at Linux arm64 (no blocker;
     its one should-fix, the manifest entry, is fixed in `04b2610`).
     **It does not close the client-collision gap.** Both render worlds' `ModelTrace`/`Trace`/
     `FastWorldTrace` are stubs that answer false (`src/renderer/RenderWorld.cpp:787-799`,
     `src/sys/stub/headless_stubs.cpp:120-133`), so the prone-bot and `RadiusPush` scenes are
     stub parity by construction and only the vehicle-exit `PointInArea` scene can differ today.
     Render-model collision needs collisionModelManager/client-prediction coverage, not
     render-world traces. Its result is the evidence for the owner question in §4.

   Each gap gets a test or a recorded decision.
9. **M3, what is left** (`STATUS.md` § Milestones, M3):
    - ~~cross-OS pairs that include an arm64 host~~: done (#144). Linux arm64 (`macmini-m1`)
      passes X1–X4 both ways with Linux x64, Windows x64 and macOS arm64
      (issuecomment-6281426367, -6282183468, -6303053162, -6303056403);
    - weapon fire seen by the other player (a headless peer can't observe it; needs a
      Vulkan observer or a server-side check). The test is written on branch head `be49e04`
      (after `8e06d7c`). Reviewed from the text diffs `e2070be2` (test) and `534c375c`
      (engine), not from the `be49e04` object: no blocker, no should-fix, one nit
      (`set g_debugWeaponState -1` is unconfirmed). macOS arm64 ran the earlier revision
      `ed6735b7` twice at engine `1ff001f` + game `bb8e0c5` and it passed, but that revision
      measured client 0 by position (the observer, not the shooter) with 2D drift only, so it
      is not evidence for the revised test. Next: publish `be49e04` and run the revised test
      once on each platform.

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
- **#159, retail AAS gaps:** salvage's AAS has a 64 u strip with no area that bots can land
  in, so `all-stock-maps` sometimes fails. Exclude a recorded list of retail AAS gaps from
  `aas_area_validity`, or keep it as a known flake (#159).
- **The headless render world (M2):** keep `idRenderWorldHeadless` as it is, or load the
  `.procb` areas and nodes on the dedicated server (`PointInArea`, `BoundsInAreas`;
  moderate), with render-model traces still out (they need render models; heavy).
  Recommended by `openETQW-linux-arm64` (2026-10-20): keep the stub for M2 as a recorded
  decision (ledger 013) until the §3 item 8 parity test measures the effects.
  Measured 2026-10-21 by `openETQW-linux (Switchworks)` on antec (Linux x86_64, engine
  `fix/m2-render-world-parity` on a38e311, game bb8e0c5 plus the test-only `m2Parity`
  command, retail 1.5, `valley`). The listen host is a Vulkan client on SDL's offscreen
  driver with llvmpipe (software Vulkan, Mesa 26.2.4), so it exercises the client's real
  `idRenderWorldLocal` but no GPU raster path. Raw logs
  `/var/tmp/oe-swk-linux/handoff/m2-parity-afab6d7`, reproducible with meson
  `m2-render-world-parity`:
  - `probe`: dedicated `numAreas=1`, `PointInArea` 0 inside and 0 outside; listen
    `numAreas=54`, 0 inside and -1 outside;
  - `prone`: both `modelHit=0`, `modelFraction=1.0`; both `clipHit=1`,
    `clipFraction=0.8708`. The combat model has real bounds on both (about
    96x54x86 dedicated, 94x49x86 listen; the pose varies a little);
  - `RadiusPush` behind a wall: identical on both (`wallFound=1`,
    `wallDistance=281.78`, `listed=8`, `pushed=1`, `speed=68.5902`,
    `velocity=(59.3801 34.2830 1.8150)`), because `FastWorldTrace` answers false on
    both;
  - vehicle exit outside the map: dedicated accepts (`exitAccepted=1`,
    `stillInVehicle=0`, `vehicleArea=0`); listen rejects (`exitAccepted=0`,
    `stillInVehicle=1`, `vehicleArea=-1`). This is ledger 013's effect, now measured.
- **A person at the controls.** Smooth motion at high caps, and M3's "Vulkan client with a
  human at the controls", are judged by people on real displays (M5 D3), not by a test.

## 5. Recently done

2026-10-20/21, overnight:
- #169 (#134): retail's per-entity shadow limits.
- #174 (#170): a light's `shadowSpec` and `dontCastFromAtmosLight`.
- #171 with game #57 (#154): L7, a gun at its limit while the hull rocks.
- #175 with game #58 (#151): the jetpack flies from open ground.
- #172 (#160): a stalled H3 phase is driven once more.
- #173: `build_engine` and `build_dedicated` default to true, closing the M1 gap.
- #168: ledger 013's vehicle-exit effect, and the M2 render-world question.
- #135 closed, not reproduced.

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
