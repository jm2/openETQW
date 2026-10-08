# Render-world portals: plan

Status: stage 1 built and measured, not landed (see "Stage 1 result"); stage 3 done (2026-10-08). Stage 2 waits for an owner decision. Engine renderer only.

**Goal.** Restore idTech 4 area/portal visibility so the client stops submitting geometry, entities and lights that sit behind portals the view cannot see through. This hurts most where the frame rate ceiling is lowest: about 145–165 fps uncapped on Adreno X1-85, with the default cap now 240 (M5 step 18).

## 1. What the code does today

- **Loading.** `LoadProcBinary` (`src/renderer/RenderWorld_load.cpp`) reads the materials, the models, `nodes`, `interAreaPortals` and `megaTextureInfo` sections, and skips the rest by their byte length (`shadowModel`, `mapEnvBounds`, `atmosLightProjection`; ledger 013).
  - `ParseInterAreaPortals_Binary` keeps only the area count (`portalAreas.SetNum`) and discards every portal.
  - Each binary model already carries a **fixed-area list** (`SetFixedAreas`), the areas it touches.
- **The portal API** (`src/renderer/RenderWorld_portals.cpp`) is stubbed:
  - `NumPortals`, `NumPortalsInArea` and `FindPortal` return 0;
  - `SetPortalState` does nothing;
  - `GetPortal` returns an empty portal;
  - `AreasAreConnected` is true only for area 0 with itself.
- **The live front end is `R_BuildDrawView`** (`src/renderer/tr_light.cpp`), not idTech 4's `R_RenderView` / `FindViewLightsAndEntities`, which `tr_main.cpp` keeps under `#if`.
  - It walks **every** entity def and **every** light def.
  - `R_AddModelSurfaces` collects every static world model (all `_areaN` models and every LOD and reference model) and frustum-tests each surface in parallel (`AnalyzeAmbientSurfaceCandidates`).
  - There is no area test anywhere: a surface, entity or light is drawn when its bounds meet the view frustum.

## 2. Measured baseline (win-arm64)

Adreno X1-85, debugoptimized, main f3a468c, game 9d9c7d9. Listen host (`+spawnServer`), standing at the GDF spawn, `com_maxFPS 0`, `com_logFrameTiming 1` for 30 s, validation off, raster path.

| Map | fps | frame p50 | work p50 | fence wait | world draws / view | surfaces / view | interactions / view | shadow draws / view |
|---|---|---|---|---|---|---|---|---|
| valley | 144 | 6.96 ms | 5.38 ms | 3.20 ms | 862 | 381 | 338 | 68 |
| outskirts | 164 | 6.10 ms | 4.52 ms | 2.10 ms | 1,231 | 591 | 472 | 11 |

Per-view figures are the shutdown counters divided by the recorded world views (6,680 and 7,640).

About half of each frame is spent waiting on the frame fence, so the GPU is the limit on this host. Fewer draws, interactions and shadow volumes are what will move the number. The CPU front end is the second target.

How much portal culling saves is not yet known. Stage 1 measures it before anything else depends on it (§5).

## 3. The retail data

Each stock `.procb` has `interAreaPortals`:

| Map | areas | portals | `_areaN` models | other static models |
|---|---|---|---|---|
| valley | 54 | 60 | 54 | 754 |
| outskirts | 30 | 38 | | |
| canyon | 57 | 63 | | |
| refinery | 69 | 53 | | |
| ark | 79 | 107 | | |

The binary layout was decoded from valley (1.5 `pak005`). All values are little-endian; each `vec3` is written as a count (3) followed by three floats, as `ReadProcFloatArray` expects elsewhere.

```
int numAreas, int numPortals
per portal:
  int numPoints, int area0, int area1
  int flags                // bits of portalFlags_t (Material.h): 7 = VIS|OUTSIDE|BLOCKAMBIENT, 1 = VIS
  int unknownInt           // 3500 or 12000 on the BLOCKAMBIENT portals seen; 0 on VIS-only
  vec3 unknownVec          // (0.24 0.24 0.24), (0.1 0.1 0.08) on BLOCKAMBIENT; (1 1 1) on VIS-only
  vec3 points[numPoints]
```

The two unknown fields go with `PORTAL_BLOCKAMBIENT`, which the SDK describes as "defines ambient light sectors". They look like an ambient sector's distance and colour, but that's a guess. They're not needed for culling and will be kept as read, not interpreted.

## 4. Reference implementation

idTech 4's `RenderWorld_portals.cpp` does the job. The working copy to follow is openQ4's at `jm2/openQ4` b37b1456 (Doom 3 GPL notice; licence per the owner's 2026-10-07 decision: GPL-3.0-or-later). The pieces:

- **`FindViewAreas`** starts from `PointInArea( viewOrigin )`. `FloodViewThroughArea_r` walks the area's portals: it culls each portal winding against the current portal stack's planes, clips it to the screen rectangle (`ScreenRectFromWinding`) and recurses into the far area with the narrowed rectangle. A blocked portal (`PS_BLOCK_VIEW`) stops the walk.
- **Area references.** Entities and lights are linked into the areas their bounds touch, pushed down the BSP `nodes` tree (`PushVolumeIntoTree`). The front end then visits only the visible areas' references, each with the area's screen rectangle as its scissor.
- **Outside the map** (view origin in no area, `-1`), everything is drawn, as today.

## 5. Stages

### Stage 1: renderer-only area culling, no simulation change

1. **Parse** the portals (§3) into the render world: windings, planes, areas, flags. Keep the two unknown fields.
2. **Area membership:**
   - `_areaN` models → area N;
   - other static models → their fixed-area lists, which are already loaded;
   - entity defs and light defs → their areas, from their bounds through `nodes`, updated when they move (`UpdateEntityDef` / `UpdateLightDef`).
   - Presentation copies and interpolated entities (M5 steps 9–14: `sdPresentation` draws an entity at an interpolated pose between ticks) must use the area of the pose actually drawn, not the tick's: the area test runs on the bounds the front end draws, at draw time.
3. **View flood** in `R_BuildDrawView`: compute the visible areas and their screen rectangles from the view origin, through `PORTAL_VIS` portals, with the frustum narrowed per portal.
   - Skip static models and entity defs with no visible area.
   - Find lights through the flood, not through the view frustum: a light def is linked into every area its volume touches, so a light whose origin is behind a portal but whose volume reaches a visible area is still found and lights it. Skip a light only when none of its areas is visible. Its shadows can still come from casters in areas the view doesn't see (a light's caster set is its own volume, as in Doom 3).
   - Optionally scissor by area rectangle later; not in the first change.
   - Subviews (mirrors, remote cameras) flood from their own origin.
4. **Doors.** `SetPortalState` stays a no-op in this stage, so every portal is open to the renderer. That's never wrong, just less culling.
5. **Controls and evidence:**
   - `r_usePortals` (default 1; 0 restores today's frustum-only path for A/B);
   - `r_showPortals` (portal outlines);
   - per-view counters (visible areas; static models, entities and lights culled by area) in the Vulkan shutdown metrics.
6. **The game-facing API stays as it is** (§6), so the game, the PVS, bots and the network see no change.

Gate for stage 1:
- **No visible geometry may be lost.** Screenshots at fixed spots on the four campaign-client maps and valley must match `r_usePortals 0` (a pixel diff within the frame-to-frame noise). Also checked: walking through each map's portals, under and over terrain, inside buildings and from aircraft altitude.
- **The tests pass:** campaign-client-maps with validation, vulkan-shadows, client-lifecycle and inprocess-map-change, plus the full suite (the determinism and seat tests prove nothing else moved).
- **The measurement:** draws, interactions, shadow draws and uncapped fps before and after at the §2 spots, plus one indoor spot (ark, refinery). If the outdoor maps gain little because most areas are always visible, the report says so.
- **Ledger:** a new row (072): the client culls by area like idTech 4, and **door portals stay open in stage 1** (`SetPortalState` is still ignored, so a closed door never hides what is behind it; retail's hides it).
- **Measured per map**, not only valley: valley's open terrain may show little, so each campaign map gets its own before/after line.

### Stage 1 result (2026-10-08): built, measured, not landed

Stage 1 was implemented on `feat/render-portal-culling` (9426634):
- the `interAreaPortals` parsing;
- the view flood with frustum narrowing;
- area culling of static models, entities and lights, behind `r_usePortals`;
- counters.

It is kept on that branch for stage 2's parsing, and **not merged**: on these maps frustum culling already removes what the portals would.

Measured on win-arm64 (Adreno X1-85, debugoptimized, main f3a468c / game 9d9c7d9): each map's GDF spawn, uncapped, 20 one-second `FRAME_TIMING` windows, `r_usePortals 1` against `0`.

| Map | fps on / off | Change | Areas visible per view | Static models culled per view | Draws per view on / off |
|---|---|---|---|---|---|
| valley | 155.8 / 143.9 | +8% | 19 of 54 | 35 | 861 / 862 |
| outskirts | 164.1 / 164 (baseline) | 0% | 27 of 30 | 3 | 1,214 / 1,231 |
| ark | 331.6 / 350.2 | −5% | 10 of 79 | 69 | 313 / 313 |
| refinery | 326.4 / 339.6 | −4% | 1 of 69 | 68 | 356 / 356 |
| canyon | 337.1 / 344.6 | −2% | 1 of 57 | 56 | 261 / 260 |

The outskirts "off" run in this series measured 33 fps, an outlier from something else on the host; the §2 baseline (164 fps) stands for it.

- **The draw count barely moves.** The spawns are outdoors: the view's area is a large open one, and the areas the flood rules out are indoors and already outside the view frustum. On ark, refinery and canyon the frames are identical with and without culling (mean pixel difference 0.03–0.10).
- **Where it's faster** (valley), the saving is CPU: fewer surfaces analysed. Where it's slower, the flood and the per-entity area lookup cost more than they save at 330+ fps.
- **Shadow casters.** The shadow pass takes its casters from the surfaces chosen for drawing (`R_AddLightSurfaces` walks `sortedDrawSurfaces`). An area-culled static model therefore stops casting shadows into visible areas: on valley, the shadow geometry of buildings and fences behind portals disappears from the ground in view (pixel difference 1.0, mostly shadow edges).
  - A correct version must keep area-culled shadow casters for the light pass, which gives back most of valley's 8%.
  - Doom 3 avoids this because its interactions are per light and area, not per view.
- **Found: the game's `BoundsInAreas` always returns no area.** `idRenderWorldLocal::BoundsInAreas`, which the game's PVS calls, starts its walk at node 0, which is also the "solid" child marker, and stops at once. The branch gives the renderer its own `BackendBoundsInAreas` and leaves the game-facing query alone. STATUS lists it under Known broken. Stage 2 depends on fixing it.

### Stage 2: game-visible portals (needs an owner decision)

Thirteen game files use the portal API through the render world:
- `gamesys/Pvs.cpp` and `Game_local.cpp`: the PVS, which today starts with every area visible;
- `Mover.cpp`: doors call `SetPortalState`;
- `botai/aas/AAS_routing.cpp`: `AreasAreConnected`;
- `Atmosphere.cpp`, `effects/RainEffect.cpp`, `SnowEffect.cpp`, `PrecipitationSystem.h`: `GetAreaPortalFlags`, `PORTAL_OUTSIDE`;
- `structures/DeployZone.cpp`, `Waypoints/LocationMarker.cpp`, `Light.cpp`, `vehicles/TransportExtras.cpp`, `Misc.cpp`, `Entity.cpp`.

Filling in the API changes, on the client and the listen host:
- the PVS, so which entities a listen host snapshots for its clients;
- bot routing;
- doors;
- precipitation and atmosphere lookups;
- deploy zones.

The headless dedicated server (`idRenderWorldHeadless`) has no `.procb` today, so it would keep one area and disagree with listen hosts unless it loads the same data.

The PVS also feeds what the server puts in each client's snapshot and the antilag (AoR) checks, so stage 2 is a **network** question too, not only a client one.

That makes stage 2 a gameplay and network change for its own plan and ledger row (013 updated). It needs:
- the owner's go-ahead;
- the dedicated server loading the area and portal data;
- the network and bot tests (`bot-gameplay`, `all-stock-maps`, the determinism and network suites).

### Stage 3: light-interaction geometry (done 2026-10-08)

The STATUS item "Light-interaction geometry is rebuilt on the CPU every frame": `CreateLightInteractionGeometry` (`tr_light.cpp`) re-tests every triangle of every surface in each light's bounds, every frame. At canyon's Strogg spawn it was 88% of the main thread on the Windows debug build.

Two changes:
- **The per-frame index list grows 64K entries at a time** (`frontEndInteractionIndexes`; it grew 16 at a time, so appending a large surface's subset copied the list over and over). This is most of the gain.
- **A subset cache for static geometry** (`r_interactionCache`, default 1). The triangles of one surface that touch one light's frustum are kept across frames and reused while four things are unchanged: the light's six planes, the surface's model matrix, and its vertex and index arrays. Entries unused for 120 views (light passes, so subviews count) are dropped, and everything is dropped when the render world is cleared or loads a map, or when models are reloaded.
  - Only the proc's `_area` world models and entities drawing a file-loaded model as it is are cached, and never deformed or streamed geometry. Models built in memory (BSE effects, snapshots, instantiated dynamic models) report `DM_STATIC` too but are rebuilt every frame, often at the same addresses, so they are excluded. The key includes the entity, so props sharing one model keep separate entries.
  - With the cache on and off, the frames are identical (mean pixel difference 0.06).

Measured on win-arm64 (Adreno X1-85, debugoptimized, game 728dd1c), uncapped, 20 one-second `FRAME_TIMING` windows. Canyon at a fixed viewpoint (`setviewpos -1271.86 4348.12 453 -135`, the Strogg spawn):

| Build | fps | Work p50 |
|---|---|---|
| main 290fca8 | 95.0 / 96.1 (two runs) | 8.8 ms |
| growth step only (`r_interactionCache 0`) | 266.9 | 2.14 ms |
| growth step and cache (after #108's review: file-loaded and `_area` models only) | 279.4 | 2.00 ms |

valley at the GDF spawn: 143.7 → 156.3 fps. The cache had no hits there: its lit surfaces are dynamic or entirely inside their light.

Images against main differ only within main's own run-to-run noise at this spot: main against main has a mean difference of 2.20, the branch against main 1.34–2.64, the branch against itself 0.08. At 95 fps the animation phases land differently from run to run.

## 6. Risks and open points

- **Mostly outdoor maps.** ETQW's maps are large outdoors with few vis portals (30–107). The flood may often reach most areas from the open terrain, and the gain may be mainly indoors and at ground level behind buildings. Stage 1 measures before claiming anything.
- **MegaTexture terrain.** Which area or areas the terrain models are in is not yet checked. If the terrain spans every area, it is always drawn: correct, but no saving there.
- **The unknown portal fields** are only kept. The ambient-sector reading belongs to the BLOCKAMBIENT/ambient work, not to culling.
- **The atmosphere light projection** (`atmosLightProjection`, still skipped) is unrelated to culling. It stays as it is.
