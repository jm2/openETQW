<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Work queue

The shared to-do list for every session. Coordinator log: [#133](https://github.com/jm2/openETQW/issues/133); see its latest comment for who coordinates now (coordination §2). Take items from the top. The coordinator keeps it
in order, and every PR that changes an item's state updates its row. `owner` names the
session that holds the item; `—` means nobody does. Before taking an item, check its PR or
issue for anything newer than this file.

**Working (2026-10-23).** The sessions are fixing the open issues. Owner, 2026-10-23: "continue the fixes, but I need kisakcod to make some serious progress so limit the multi-hour review runs until the very tail end when all the identified fixes for issues are in and it's absolutely necessary". The coordinator's plan for following it, which changes no merge gate: fix PRs get targeted tests and an independent review and wait, open. The kisakcod sessions have priority on shared hosts. At the end, one integration head (main plus every fix PR) gets the retail suite and the screenshot sets, which the gates still require, and the PRs merge in order on that evidence. Docs-only PRs merge as usual.

Updated: 2026-10-23 by `openETQW-linux` (coordinator): the combined run merged #202, #204, #206, #207, #210, #213 and #215 (§5); §1 is empty. Earlier: #180 (merged 2026-10-22), #198, #199 and #200 are in §5; M4's last item is done.
Updated: 2026-10-22 by `openETQW-linux` (coordinator): the game #51 row with the Linux arm64
evidence and the open owner ask, and the review state of §3 items 3, 8 and 9 and of #162.
Updated 2026-10-21 after the overnight work (§5); earlier that day, merged since 2026-10-18
were #145, #146, #149, #152, #153, #155–#158 and game #53–#56 (§5), and #159 and #160 were
filed. Before that, 2026-10-18, after a pass over every doc.

## 1. Open PRs, in merge order

| # | What | Owner | Head | Left to do |
|---|---|---|---|---|
| PR `fix/postprocess-glare-233` | OpenGL draws the GUI post-process (`guis/postprocess`: glare, saturation, contrast, tint), as retail: ARB ports, the GUI path's destination buffers, render targets found by name (part of #233; the Vulkan half is openETQW-win's) | openETQW-linux-arm64 | 46e56d3+ | Independent review; screenshot sets on other OpenGL platforms; the combined run |

## 2. Open issues, in priority order

| # | What | Owner | Next step |
|---|---|---|---|
| [#262](https://github.com/jm2/openETQW/issues/262) | Vulkan material descriptor cache never frees orphaned sets (guiDescriptorPool exhaustion after ~300 map loads) | openETQW-win-arm64 | PR `fix/vk-material-descriptor-leak-262`: sets freed with their images, unused sets evicted when full, one-time warning. |

## 3. Milestone work, in order

Each item names its acceptance test. Details are in the linked plan.

1. **M5 step 14b, the hull lead** (`docs/hull-lead.md`). The code is in, off by default
   (ledger 067). A network client's and a listen host's `VEH_PRED` numbers per class are in
   §6 (owner: `openETQW-win`; merged as #157 with game #56). Left: the owner's review on a
   high-refresh display (owner-gated, §4).
2. **M5 step 14, what is left** (owner: `openETQW-win`): late look for `freepivot` views and the damped
   first-person `clampDamped*` view is merged (#152 with game #54). Left: the owner decides
   which seats join the default list (§4).
3. **M4, what is left**: done (#199, §5). Every M4 item listed in `STATUS.md` § Milestones now has a meson test.
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
     and the parity test that measures them is merged: [#178](https://github.com/jm2/openETQW/pull/178)
     with game [#60](https://github.com/jm2/openETQW-game/pull/60) (2026-10-22; meson `m2-render-world-parity`,
     OK in 22 s in a 69/70 retail suite on antec's RX 6400). Earlier evidence, at `146e44e`: `m2-render-world-parity` 1/1 OK 26.10 s on antec
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

- **Before the repositories go public: less detail, no personal data.** Owner, 2026-10-23: "sooner or later this level of detail and any PII (including hostnames, etc.) should stop being published and then eventually scrubbed from the repo: it doesn't make sense to include all this if and when the repo goes public." Until then, new docs, PRs and comments keep host names, local paths and per-session narration to what the evidence rules need, and never include IPs, user names or home paths. Changing the evidence rules (AGENTS.md rule 6, `CONTRIBUTING.md` § Records, which name the host) and the scrub itself (a history rewrite or fresh repositories) are the owner's decisions.
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

2026-10-22/23:
- #273 Vulkan: no interaction specular in GUI render worlds, as OpenGL since #251 (ledger 079)
- #272 Vulkan draws guis/postprocess's glow natively, with retail's scene alpha (fixes #233 with #263)
- #271 Vulkan shadow stencil counts around 128 and lights where 128 >= stencil, as OpenGL (fixes #267)
- #269 Stuff grass fades with distance on both APIs: _diffusionMask is a 16x16 ordered dither; Vulkan fades per instance (fixes #256; colour gap in #270)
- #263 OpenGL draws retail's guis/postprocess glow (ARB ports of downsample, glare, blur, glow), the scene lift #233 measured
- #266 Vulkan: image descriptors use the evicting allocator; currentRender material sets purged on swapchain recreate (#264 review)
- #264 Vulkan: material descriptor sets freed with purged images and evicted when the pool is full (fixes #262)
- #261 Vulkan: front-end descriptor sets in their own pool, hashed, freed with purged images, evicted when full (fixes #258)
- #260 OpenGL forceClear follows glConfig.samples; clearer multisample fallback warning (#255 review)
- #259 OpenGL: retry without multisampling when the requested r_multiSamples fails (fixes #255 on SDL3)
- #257 Vulkan: sunlit opaque surfaces take retail's amblit ambient (minSpecAmbientColor) via ambSurf; others the cube plus its specular term (fixes #122's ambient gap)
- #254 OpenGL: load qglSampleCoverageARB; the SDL3 window requests r_multiSamples (fixes #253)
- #249 Raster Vulkan: retail's ambient cube (multiplicative, before the flat ambient), self-illumination, interaction specular; generated cubes no longer black (part of #122)
- #252 OpenGL: load qglMultiTexCoord4fARB, so instanced trees and shrubs draw (#246 OpenGL half)
- #251 No interaction specular in GUI render worlds (limbo model), matched to measured retail; ledger row 079 (owner accepted 2026-10-25; fixes #212 with #250)
- #250 Light colours clamped to [0,1] before r_lightScale (GL, Vulkan, RT), as retail (limbo and canyon bunker light measured; part of #212)
- #248 Vulkan draws instanced models (valley's trees, shrubs) per instance; front-end scissor from instanced bounds; foliage vertex colour (part of #246)
- #247 MegaTexture: bit-identical IDCT over nonzero coefficients; the finest level completes about 2x sooner after a teleport (part of #233)
- #245 seat-poses-listen: one re-run of a host-contended phase, then exit 1 'host contended' (§9 2026-10-24; closes #162, refs #196)
- #242 Vulkan terrain: retail's notlit_lit under the atmosphere sun, with minSpecShadowColor in shadow (part of #233)
- #243 Owner decisions of 2026-10-24 in coordination §9 (a contended re-run fails on a second failure; burn-down; #141 undeferred) and ledger row 078. Closed with evidence: #186 (macOS globe renders), #148 (no repro).
- #244 Mouse: carry SDL's sub-pixel motion between events (fixes #141)
- #241 Vulkan megatexture detail texture, with the CPU mipmap-state levels (part of #233)
- #236 (openETQW-win-arm64): Vulkan applies retail's linear atmosphere fog to world surfaces, raster (fog-once stencil) and RT (part of #233)
- #238 (openETQW-win): Vulkan front-end pipelines gated on the stage cull, MoltenVK swizzle fallback, comment and dead-code cleanup (#229 and #234 follow-ups)
- #240 (openETQW-linux): the default image is black outside developer mode, as Doom 3; missing terrain spec masks no longer light map edges (fixes #239)
- #237 (openETQW-linux): Vulkan scales the terrain's light by the bakedamb stage's parameters.w, as retail; valley ground loses its doubled sun (part of #233)
- #235 (openETQW-win): the ambient cube is no longer pre-scaled by brightness, which every consumer applies; the menu globe lands on retail's brightness (fixes #232)
- #234 (openETQW-win): Vulkan ports retail's sfx/atmos for the menu globe's atmosphere, with the stage's back-face cull (#186 lead 2)
- #231 (openETQW-win): the ambient cube bakes the directional ambient lights on black, not on ambientColor; the menu globe gets retail's terminator and dark limb (#186 lead 1)
- #229 (openETQW-win): Vulkan reads retail's 3Dc normal maps with X and Y the right way round (BC5 view swizzle, decode fallback, heat haze and water)
- #230 (openETQW-linux): the ambient specular cube is baked on black, not minSpecAmbientColor, ending OpenGL's silvery overbright on lit models (#212)
- #227 (openETQW-win-arm64): the client re-challenges on Bad challenge, takes challenge replies only from the server's address and port, and still times out; new test client-bad-challenge (may fix #216)
- #226 (openETQW-win): OpenGL material stages also leave the environment cube, gradient map and dither mask to the renderer, re-set per surface (follow-up to #224, #186)
- #228 (openETQW-win): OpenGL copies red into alpha for uncompressed TD_BUMP images and _flat gets alpha 128, as retail's programs read X from alpha (OpenGL menu globe, #186)
- #225 (openETQW-win-arm64): the client takes a challengeResponse only while challenging; new test client-stray-challenge (side finding of #216)
- #224 (openETQW-win): OpenGL material stages no longer overwrite the entity's ambient, sun and specular cubes with _normalCubeMap (#186 OpenGL globe, part)
- #223 (openETQW-linux): OpenGL loads ARB ports from base/glprogs for retail's Cg sky, atmosphere and grass programs; fixes #219
- #222 (openETQW-linux): OpenGL unbinds stale element and array buffers before client-pointer draws; fixes #218 and drops #221's glthread stopgap
- #221 (openETQW-linux): Linux OpenGL turns off Mesa glthread (stopgap for #218's crash and GPU reset)
- #220 (openETQW-mac): OpenGL decodes 3Dc (ATI1/ATI2) normal maps when the driver has no LATC; fixes macOS's part of #212
- #215 (part of #186): Vulkan ports retail's sfx/feAmbDecal and sfx/feAmbDecal_cloud, so the menu globe is lit by its ambient cube map, with night lights only on the dark side. Still brighter than retail, no visible terminator, atmosphere rim unchanged (#186 open). Combined run 2026-10-23 (as #202).
- #213 (#159): all-stock-maps leaves area-0 samples inside a measured gap in retail's own AAS (salvage) out of aas_area_validity and reports them (owner: match retail). Combined run 2026-10-23 (as #202).
- #210 (#208, #211): surfaces of equal sort draw in submission order (a stable merge sort, view entities first as the SDK does), so the menu's star field no longer covers the nebula; the game's sky-layer sortOffset is honoured. Combined run 2026-10-23 (as #202); smoke and vehicle glass were not in the sets.
- #207 (#203): the OpenGL GUI path resyncs the GL state cache each frame after direct state calls. Combined run 2026-10-23 (as #202).
- #206 (#187): Vulkan's stand-in sky gradient runs only for render worlds loaded from a map, so the limbo player model no longer sits in a black box (ledger 076). Combined run 2026-10-23 (as #202).
- #204 (#185): OpenGL skips any stage whose render program is defaulted (retail's Cg programs, which have no runtime on x64 or arm64), in the world and the GUI, instead of drawing it solid orange; OpenGL is a documented degraded fallback (ledger 077). Combined run 2026-10-23 (as #202).
- #202 (#193): the OpenGL GUI state cache follows direct state changes, so the menu's and limbo's buttons draw (Mods, Replay, Credits; team buttons). Combined run 2026-10-23 (integration e9df517): antec suite 71/2 flakes (#162, #216; both OK alone)/1 skip; screenshot sets from five platforms, no regressions.
- The owner's answers of 2026-10-23 (`STATUS.md` § Owner decisions): #209 merged (ledger row 076 kept); #192 and game #51 closed; #147 decided, so sizes stay points; #185's OpenGL is a documented degraded fallback (`readme.md` § Renderers, ledger row 077); #159 fixed by #213; #197 is waiting on a check that the in-game Name field works.
- #205 (#188): `openetqw_validate.py` links the installed game module beside the build's binaries (copies it on Windows), so `meson test` on a validate tree loads the PR's module rather than one in `<data>/base/`. The by-hand link step in coordination §5 is gone.
- #180: build helpers, one command per platform (`scripts/build-*`, `docs/build-scripts.md`; the owner's request), merged 2026-10-22.
- #200 (#189): the Vulkan GUI honours retail's `trivialMakeAlpha*` programs, which use brightness as alpha and draw white. The compass ring's black background no longer covers the minimap, and the menu and HUD sheets and the health cross draw translucent, as GL (which runs the real program) does. Found by skipping GUI stages one material at a time. Screenshot sets on `screenshots-pr200` from all five platforms (Windows x64 with RT on and off). Antec suite: 70 OK / 1 FAIL / 1 SKIP. The failure was `all-stock-maps` outskirts GDF `aas_area_validity` at 85.4% (#159's class); it was OK when rerun alone.
- #199 (§3 item 3, M4): `m4-bot-match` (two live bots play a time-limited match and complete no objective; movement is measured within one life) and `m4-offcampaign-match` (an objective match on quarry and island runs to its time limit, and the map's defenders win). They pass on macOS arm64 at 6d43788 and 5e12932, and on antec Linux x64 at 5e12932 (34.8 s and 69.4 s). The antec full suite at c19f7ff gave 73 OK / 0 FAIL / 1 SKIP (cross-os).
- #198 (part of #193): a single vertically centred line of GUI text centres its ink (capitals to descenders) as retail 1.5 does, and the bottom of a text rectangle gives way by the font's descent, so the main menu banner keeps its descenders. Screenshot sets on `screenshots-pr198` (Linux x64, Windows x64, Windows arm64; macOS banner check). Antec retail suite at the merge head: 71 OK / 0 FAIL / 1 SKIP (cross-os). #193 stays open for the blank OpenGL buttons.
- #195 (part of #189): masked GUI draws are cut to where their mask coordinates are 0..1, so the command map's territory outlines no longer cover the 3D view under Vulkan. Screenshot sets on `screenshots-pr195` from all five platforms. #189's black minimap was fixed by #200.
- #194 (#190): a Bink movie named by a binary sound decl is found, so the first-start intro ends (here, at once, with no movie installed) and the main menu draws on a fresh profile. The menu it uncovered shows #193 (clipped descenders, blank OpenGL buttons) and #186's dark OpenGL globe.
- #191 (#184): Vulkan submits the GUI at the end of the frame, so the HUD and menus queued after the last scene draw. Screenshot sets: Linux x64, Windows x64 (RT on and off), Windows arm64 and macOS on `screenshots-pr191`. Found once the HUD drew: #189 (minimap image, unmasked command-map lines), #190 (black fresh-profile main menu) and #192 (limbo name clipped on Windows arm64).
- #182 (#181): GUI text drawn from the game's fonts on every platform and both renderers (`docs/gui-text.md`). Screenshot sets for all five platforms are on `screenshots-pr182`; follow-ups #184-#188.
- #177 with game #59 (game #51): `sdUserInterfaceLocal::Load`'s catch no longer touches `this` after a failed load; the GUI tests cover it. Merge evidence: antec retail suite 70 OK / 0 FAIL / 1 SKIP (cross-os) at the merge head, which settles the macmini cap-240 question.
- #178 with game #60: the M2 render-world parity test (`m2-render-world-parity`).

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
