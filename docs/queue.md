<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Work queue

The shared to-do list for every session. Coordinator log: [#133](https://github.com/jm2/openETQW/issues/133); see its latest comment for who coordinates now (coordination §2). Take items from the top. The coordinator keeps it
in order, and every PR that changes an item's state updates its row. `owner` names the
session that holds the item; `—` means nobody does. Before taking an item, check its PR or
issue for anything newer than this file.

Updated: 2026-10-16 by `openETQW-linux` (coordinator). #130 and #129 merged on 2026-10-16;
the PRs opened before 2026-10-15 are all merged; #132 merged the same day.

## 1. Open PRs, in merge order

| # | What | Owner | Head | Left to do |
|---|---|---|---|---|
| — | None open. | | | |

## 2. Open issues, in priority order

| # | What | Owner | Next step |
|---|---|---|---|
| [#128](https://github.com/jm2/openETQW/issues/128) | Raster stencil shadows: casters outside the view cast no shadow. | — | #129 is merged. Raster path, so test on win-arm64, Linux arm64 or macOS. A graphics change, so it needs the screenshot set. |
| [#134](https://github.com/jm2/openETQW/issues/134) | Raster shadows: retail's `shadowVisDistMult` / `shadowProjectDist` limits are not applied, so far props' sun volumes fill the stencil (the jetpack seat on a network client: 84 draw pairs against 113 without entity casters). | — | Read what retail's renderer does with them first; don't guess. A graphics change: screenshot set. |
| [#122](https://github.com/jm2/openETQW/issues/122) | GDF rifle viewmodel: near-black on raster, tan with ray tracing. | `openETQW-win` | Get a retail reference first: a retail 1.5 screenshot from the owner, or published retail footage. Until then, don't change either path. Escalate to the owner if no reference turns up. |
| [#127](https://github.com/jm2/openETQW/issues/127) | `+set r_mode N` on the command line is ignored at startup. | — | Any platform. Small. Fixing it lets `screenshot_set.py` capture above 640x480. |
| [#126](https://github.com/jm2/openETQW/issues/126) | `all-stock-maps`: slipgate's `aas_area_validity` flakes on teleporter transit samples. | — | Test-only fix: exclude samples taken in transit, and explain why in the PR. Don't loosen the bound (`AGENTS.md` rule 4). |
| [#135](https://github.com/jm2/openETQW/issues/135) | `mixed-caps`: B's 240 cap A1 'turn' sits at its threshold on `antec` (233–235.3 fps against 235.2; the test's own ceiling is about 282 with two clients) and fails on `main` too. | — | Find what costs B frames while turning with a second client, or ask the owner about B's high cap on antec. Not a host-load problem (the 2026-10-09 'ceiling drop' note compared different tests). |
| [#136](https://github.com/jm2/openETQW/issues/136) | `bot-gameplay`: the two bots sometimes never engage in the window. | — | Make the encounter deterministic; don't loosen the check. |
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
4. **M4, what is left** (`STATUS.md` § Milestones, M4). Done: classes, damage and kills,
   deployable delivery, both match endings, stopwatch and campaign transitions (meson
   `m4-*`, #104 and #107). Left: a match played out by bots (they play no objectives), and
   a started match on maps beyond the campaign's three. Acceptance: new meson tests pass on
   the retail 1.5 data on two platforms.
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
  The session's tmux server runs under launchd, so macOS never prompts and no app appears in
  the Local Network list (checked 2026-10-16). The owner, at the Mac: open a fresh Terminal.app
  window outside tmux, send one LAN packet from it so macOS prompts
  (`python3 -c "import socket;socket.socket(2,2).sendto(b'x',('192.168.1.194',27733))"`;
  192.168.1.194 is `antec`), allow it, and run the test from that same window. Check with a
  non-Apple binary like that one: `nc`, `ping` and `ssh` are exempt and succeed even while blocked. For a Mac-hosted server, also
  `sudo /usr/libexec/ApplicationFirewall/socketfilterfw --add ~/openETQW/build/openETQW-ded_arm64`
  and `sudo … --unblockapp` on the same path. Then `OPENETQW_REMOTE_SERVER=host:port meson test -C build cross-os`
  both ways against `antec` and ANTEC-WIN.
- **A Windows run on an NVIDIA or Intel GPU** (`STATUS.md` § Next steps, item 3). No such
  host is in the fleet; the owner would have to provide one.
- **Hosted CI:** comes back only with Actions minutes. Until then, local checks replace it.
- **#122's retail reference** if none can be found.

## 5. Recently done

#132 (this runbook), #137 (STATUS current), #130 (dropped clients are told; listen-host `disconnect` ends its server), #129 (raster shadow volumes; #124 fixed), #131 (removed the Discord announcement), #125 (the screenshot tool and rule), #123 (the
raster sky), #118 (Stop disconnects the client), #117 (the vehicle-pop tolerance),
#112 (the Windows retail-data folder picker). Older work is in `STATUS.md` and the merged
PRs.
