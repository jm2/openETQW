# Render-world portals: plan

Status: plan for review (2026-10-08). Nothing here is implemented yet. Engine renderer only.

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
3. **View flood** in `R_BuildDrawView`: compute the visible areas and their screen rectangles from the view origin, through `PORTAL_VIS` portals, with the frustum narrowed per portal.
   - Skip static models and entity defs with no visible area.
   - Skip light defs with no visible area. A light's interactions still reach surfaces outside the visible set only through shadows; Doom 3's light-scissor rules are followed.
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
- **Ledger:** a new row (072): the client culls by area like idTech 4, and door portals don't block the view yet.

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

That makes stage 2 a gameplay change for its own plan and ledger row (013 updated). It needs:
- the owner's go-ahead;
- the dedicated server loading the area and portal data;
- the network and bot tests (`bot-gameplay`, `all-stock-maps`, the determinism and network suites).

### Stage 3 (separate): light-interaction geometry

The STATUS item "Light-interaction geometry is rebuilt on the CPU every frame": `CreateLightInteractionGeometry` (`tr_light.cpp`) re-tests every triangle of every surface in each light's bounds, every frame. At canyon's Strogg spawn it was 88% of the main thread on the Windows debug build.

The fix is to cache the result per static surface/light pair until either changes. Stage 1's area links already narrow which pairs exist. It's its own change after stage 1, measured on canyon.

## 6. Risks and open points

- **Mostly outdoor maps.** ETQW's maps are large outdoors with few vis portals (30–107). The flood may often reach most areas from the open terrain, and the gain may be mainly indoors and at ground level behind buildings. Stage 1 measures before claiming anything.
- **MegaTexture terrain.** Which area or areas the terrain models are in is not yet checked. If the terrain spans every area, it is always drawn: correct, but no saving there.
- **The unknown portal fields** are only kept. The ambient-sector reading belongs to the BLOCKAMBIENT/ambient work, not to culling.
- **The atmosphere light projection** (`atmosLightProjection`, still skipped) is unrelated to culling. It stays as it is.
