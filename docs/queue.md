<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Work queue

The shared to-do list for every session. Coordinator log: [#133](https://github.com/jm2/openETQW/issues/133); see its latest comment for who coordinates now (coordination §2). Take items from the top. The coordinator keeps it
in order, and every PR that changes an item's state updates its row. `owner` names the
session that holds the item; `—` means nobody does. Before taking an item, check its PR or
issue for anything newer than this file.

Updated: 2026-10-19 by `openETQW-linux` (coordinator): M5 step 3 done (#146); owners after the Claude Code sessions resumed (#133). Before that, 2026-10-18, after a pass over every doc. Since
2026-10-15: the old PRs are all merged, and cross-OS pairs among Linux, Windows and macOS
passed both ways (#139, #140). This pass rewrote §3's M5 rows to match what has landed and
added the M3 row. It also closed two re-check items with evidence (§5).

## 1. Open PRs, in merge order

| # | What | Owner | Head | Left to do |
|---|---|---|---|---|
| — | None open. | | | |

## 2. Open issues, in priority order

| # | What | Owner | Next step |
|---|---|---|---|
| [#128](https://github.com/jm2/openETQW/issues/128) | Raster stencil shadows: casters outside the view cast no shadow. | `openETQW-win-arm64` | #149 is open: the retail suite and every platform's screenshot set are next. #129 is merged. Raster path, so test on win-arm64, Linux arm64 or macOS. A graphics change, so it needs the screenshot set. |
| [#134](https://github.com/jm2/openETQW/issues/134) | Raster shadows: retail's `shadowVisDistMult` / `shadowProjectDist` limits are not applied, so far props' sun volumes fill the stencil (the jetpack seat on a network client: 84 draw pairs against 113 without entity casters). | — | Read what retail's renderer does with them first; don't guess. A graphics change: screenshot set. |
| [#122](https://github.com/jm2/openETQW/issues/122) | GDF rifle viewmodel: near-black on raster, tan with ray tracing. | — (parked) | Get a retail reference first: a retail 1.5 screenshot from the owner, or published retail footage. Until then, don't change either path. Escalate to the owner if no reference turns up. |
| [#127](https://github.com/jm2/openETQW/issues/127) | `+set r_mode N` on the command line is ignored at startup. | — | Any platform. Small. Fixing it lets `screenshot_set.py` capture above 640x480. |
| [#126](https://github.com/jm2/openETQW/issues/126) | `all-stock-maps`: slipgate's `aas_area_validity` flakes on teleporter transit samples. | `openETQW-linux` | Test-only fix: exclude samples taken in transit, and explain why in the PR. Don't loosen the bound (`AGENTS.md` rule 4). |
| [#135](https://github.com/jm2/openETQW/issues/135) | `mixed-caps`: B's 240 cap A1 'turn' sits at its threshold on `antec` (233–235.3 fps against 235.2; the test's own ceiling is about 282 with two clients) and fails on `main` too. | — | Find what costs B frames while turning with a second client, or ask the owner about B's high cap on antec. Not a host-load problem (the 2026-10-09 'ceiling drop' note compared different tests). |
| [#136](https://github.com/jm2/openETQW/issues/136) | `bot-gameplay`: the two bots sometimes never engage in the window. | `openETQW-linux` | Make the encounter deterministic; don't loosen the check. |
| [#141](https://github.com/jm2/openETQW/issues/141) | Mouse motion: SDL's sub-pixel `xrel`/`yrel` is truncated per event (`src/sys/sdl/sdl_events.cpp:238`), so slow motion is lost on high-DPI displays. From code, not reproduced. | — | Port openQ4's remainder-keeping `SDL3_ConsumeMouseDelta`, with a test that feeds fractional deltas. Check it on a Retina Mac. Input path only. |
| [#147](https://github.com/jm2/openETQW/issues/147) | High-density displays: should `r_mode` / `r_custom*` mean pixels or points (SDL sizes on macOS are points)? | — | An owner question with three options; not urgent. |
| [#148](https://github.com/jm2/openETQW/issues/148) | `ANTEC-WIN` drew at about 30 fps for some 15 minutes during a retail suite (2026-10-20 02:16–02:31Z); cause unknown. | — | If it recurs, capture what the issue lists. |
| — | `screenshot_set.py`: the canyon anchor is indoors. | — | Add an outdoor canyon anchor; take eye positions from `getviewpos`. |

## 3. Milestone work, in order

Each item names its acceptance test. Details are in the linked plan.

1. **M5 step 14b, the hull lead** (`docs/hull-lead.md`). The code is in, off by default
   (ledger 067). Left: a network client's `VEH_PRED` numbers per class, then the owner's
   review on a high-refresh display (owner-gated, §4).
2. **M5 step 14, what is left** (owner: `openETQW-win`, branches `feat/late-look-freepivot-damped`):
   late mouse look for `freepivot` views (the badger, desecrator, hog and platypus gunners
   and the titan passenger) and for the presented seat with a hull-relative `clampDamped*`
   clamp: the husky driver (ledger 064). From valley's `listSeatViews`, the desecrator
   driver's standard view is rate-limited, not damped, so it has late look since
   2026-10-08's rate tier; the titan driver's damped view is third person. The `pivot` and
   `freepivot` views themselves have been presentable since 2026-10-07 (joint blending,
   ledger 070), and `pivot` has late look since 2026-10-08. Acceptance: `seat-poses`
   late-look phases for those seats. Then the owner decides which seats join the default
   list (§4).
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

   Each gap gets a test or a recorded decision.
9. **M3, what is left** (`STATUS.md` § Milestones, M3):
    - cross-OS pairs that include an arm64 host. `openETQW-linux-arm64` offered
      `macmini-m1`; ask `kisakcod` first, since it's the 8 GB host;
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
- **A person at the controls.** Smooth motion at high caps, and M3's "Vulkan client with a
  human at the controls", are judged by people on real displays (M5 D3), not by a test.

## 5. Recently done

M5 step 3's seat part (2026-10-19): `seat-poses-*` are the seat capture, and the new S5 checks the cockpit (`docs/m5-plan.md` step 3).

Closed on re-check (2026-10-18), with evidence:
- the mixed-caps producer gap: M5 step 8 removed it (`docs/m5-plan.md` step 8, ledger 059: A7/A7o pass, known gaps removed);
- the vehicle proxy gate: steps 13–14 replaced it for listed seats, so all that's left is the owner's default seat list (§4).

M3 cross-OS with the Mac, all four directions (2026-10-16, `STATUS.md` evidence), #140 (the other three), #139 (the first of them), #138 (the Mac Local Network notes), #132 (this runbook), #137 (STATUS current), #130 (dropped clients are told; listen-host `disconnect` ends its server), #129 (raster shadow volumes; #124 fixed), #131 (removed the Discord announcement), #125 (the screenshot tool and rule), #123 (the
raster sky), #118 (Stop disconnects the client), #117 (the vehicle-pop tolerance),
#112 (the Windows retail-data folder picker). Older work is in `STATUS.md` and the merged
PRs.
