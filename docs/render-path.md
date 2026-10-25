<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Render path: how a world view is drawn, and the traps found so far

The order of the passes in each backend, and the non-obvious rules behind them, as learnt
while matching retail 1.5 (#122, #212, #233, #246, #256, #265, #267, #270 and their PRs).
Read it before changing lighting, sorting, stencil use or the scene's alpha. Retail
measurement methods are in `docs/retail-comparison.md`.

Written 2026-10-25 against engine `main` after #278. Function names are given rather than
line numbers; check the code before relying on a detail.

## 1. OpenGL (retail's own programs)

OpenGL runs retail's ARB render programs. It also runs our ARB ports of retail's Cg
programs (`base/glprogs/`, loaded by `SD_LoadCgReplacement` in
`DeclRenderProgram_opengl.cpp`): the sky, the atmosphere fades, the grass and the
post-process (#219, #223, #263). Other Cg programs are skipped (ledger 077).

- **Finding the ports:** they're found through the file system search path. `fs_cdpath`
  defaults to `Sys_DefaultCDPath()`: the app bundle's resources on macOS, else the folder
  that holds `base/vkprogs`, else the working directory.
  - A client started from the build folder finds no ports, and the log says "Reference
    shader of type 1 is defaulted in '<prog>'". Start it from the checkout root or the
    install folder; `screenshot_set.py` does.
- **With `r_megaDrawMethod != 0`** (retail's default is 3), every opaque surface the
  atmosphere sun lights goes to the light's `mtInteractions`, and its bit is set in
  `space->ambSurf` (`tr_light.cpp`).
  - `R_FillDepthAmbient` (`draw_new.cpp`) then skips those surfaces.
  - `R_DrawMTInteractions` draws them with the sun interaction's amblit program:
    ambient = diffuse × vertex colour × `$ambientAvgColor` (the atmosphere's
    `minSpecAmbientColor`). That uses no ambient cube, no `$ambientScale` and no specular
    cube.
- **Shadows:** the stencil is cleared to 128 and the shadow volumes count around it.
  - Lit means `128 >= stencil` (GL_GEQUAL), so a negative count (a front face without
    its cap under z-fail) still counts as lit.
  - After the volumes, a full-screen `shadow/multiplitive` quad multiplies every pixel
    with a positive count (GL_LESS 128) by `minSpecShadowColor` (0.75 on valley).
  - So for the sun: shadowed = (ambient + sun) × m, lit = ambient + sun.
- **Light colour registers are clamped to [0, 1] before `r_lightScale`** (#250).
- **A view of a world with no map** (a GUI's: the limbo and menu models,
  `idRenderWorldLocal::HasMap()`) binds a black specular map in the interaction pass
  (#251, ledger 079).
- **Extension entry points:** every `qgl*` extension pointer must be loaded with
  `LOAD_QGL` in `R_CheckPortableExtensions` (`RenderSystem_init.cpp`). An unloaded pointer
  fails silently, because the callers check it for NULL.
  - Missing `qglMultiTexCoord4fARB` (#252): every instanced model collapsed to a point.
  - Missing `qglSampleCoverageARB` (#253): the coverage fade never ran.
  - Audit by comparing the declarations in `qgllib.cpp` with the `LOAD_QGL` list.
- **The window:** every client creates its window through the SDL3 path
  (`src/sys/sdl/sdl_glimp.cpp`), Windows included. Multisample attributes are set there;
  the log prints "GL multisample: N samples" (#254). An unsupported count retries
  without multisampling (#259).
  - Apple's OpenGL never refuses a sample count; it quietly clamps it (3, 32, 64 → 4).
  - Apple's OpenGL 2.1 has no LATC, so retail's ATI1/ATI2 normal maps are decoded on the
    CPU (#220).
- **Screenshots:** `r_vkScreenshot` also captures OpenGL, and forces alpha to 255
  (`WriteOpenGLScreenshot`). Measuring the framebuffer's alpha needs a local patch.

### Porting a retail Cg program to ARB (`base/glprogs/`)

- **Texture coordinates:** the ARB path's preamble (`decompressVertex` in
  `DeclRenderProgram_opengl.cpp`) already unpacks `$texCoordAttrib` for `r_32ByteVtx`. Drop
  the Cg's own `$if r_32ByteVtx tex *= 1/4096`, or the coordinates collapse and the frame
  comes out one flat colour.
- **Rectangle textures:** `$currentRenderTexelSize.xy` is the viewport in pixels (the Cg's
  `screenSize`), and RECT textures sample in pixels.
- **ARB vertex programs** can't read `result.*`; keep values in a TEMP.
- **The GUI path:** the OpenGL GUI path (`GuiModel.cpp`, `SubmitFrame`) is separate and
  immediate-mode. A stage feature handled in `draw_new.cpp` (`destinationBuffer`,
  `_currentRender`) has to be added there separately (#263).
- **Render-target lookup:** `ImageFromFile` (`Image_init.cpp`) now finds render targets by
  name. It used to make a second, defaulted `_currentRender`; the symptom was a stage image
  pointer different from `globalImages->currentRenderImage`.
- **Reading retail's decl text** with temporary prints:
  - render programs: in `sdDeclRenderProgram::Parse`. The first parse is the real text;
    a failed parse reparses the default under the same name, so number the dumps.
  - compiled ARB text: in `sdRenderShaderARB::Upload`.
  - materials: in `idMaterial::Parse`. `.mtrb` bodies are compressed and can't be read
    offline.
  - GUI decls: through `idLexer::LoadMemoryBinary` in `sdDeclGUI::Parse` (game repo).

  All of it is retail-derived, so keep it in scratch.
- **Finding which draw wrote a pixel:** read the pixel back before and after each draw in
  `RB_DrawElementsWithCounters`, and log the blend state with `glGet*`, armed by a temporary
  cvar for one frame. `tr_render.cpp` doesn't include `tr_local.h`, and `tr_backend.cpp`
  isn't linked into the client, so use `glGet*` for state.

## 2. Vulkan raster, in order (`sdVulkanBackend::DrawView`)

1. **Sky gradient,** only for a world with a map (#187).
2. **Base pass** over the view's surfaces in sort order.
   - **Instancing:** an instanced entity (`sdInstStatic`: trees, shrubs, map objects) is
     expanded into one copy per instance by `sdVulkanInstanceSurfaces::ForEach` (#248),
     culled by the instance's bounds and by `R_DistanceVisibility`.
   - **Grass ordering:** stuff grass sorts at `SS_DECAL` but carries its own light. It is
     moved after the other opaque and decal surfaces (#275).
3. **`RunRasterLighting`,** started by the first surface sorted after `SS_DECAL`, or by the
   first stuff grass. Everything drawn before it gets the lighting; nothing after it does.
   1. **Scene-albedo snapshot,** for MegaTexture terrain.
   2. **Ambient:** `ClearViewStencil()` (0), then the ambient-cube pass
      (`ambient_cube.frag`) over the lit opaque surfaces up to `SS_DECAL`, skipping
      translucent ones and MegaTexture.
      - It multiplies the destination (2·src·dst; the shader writes ambient/2, so up to 2
        fits the UNORM target).
      - It marks the stencil "equal 0, then increment", so each pixel is scaled once.
      - Surfaces with an `ambSurf` bit take the flat `minSpecAmbientColor` instead of the
        cube, as OpenGL's amblit does (#257). Other surfaces get the cube plus its
        specular term (2 × specular map × the specular cube along the reflection).
      - A second, additive draw puts back self-illumination (#249).
   3. **Flat ambient:** a full-screen multiply by `r_rayTracingAmbient` (0.34) over the
      pixels the cube pass didn't mark (the stencil-zero-only pipeline). It also covers
      MegaTexture terrain.
   4. **Per light:** clear the light's scissor in the stencil to 128, draw the shadow
      volumes (z-fail, INCR/DECR_AND_WRAP), then the interactions.
      - Shadowed interactions test GREATER_OR_EQUAL against 128, as OpenGL does (#271).
      - Terrain lit by the sun uses retail's `notlit_lit` and its shadow split
        (`minSpecShadowColor`) (#242).
      - Sunlit models are scaled by m where the count is above zero (LESS against 128)
        (#268).
   5. **Fog:** clear the stencil to 0 again, then fog each lit, non-translucent surface
      once (stencil) (#236). Translucent surfaces, grass among them, aren't fogged here.
4. **The rest of the base pass:** surfaces sorted after `SS_DECAL`, translucent ones,
   then the viewmodel. Each draws with its own depth range.
5. **The GUI and post-process.** `sdGuiModel::SubmitFrameVulkan` hands the post-process
   to `sdVulkanBackend::DrawPostProcessGlow` (#272).

### More Vulkan details

- **Layouts:** `guiDescriptorSetLayout` has 5 bindings (`VULKAN_MATERIAL_TEXTURES`: diffuse,
  bump, specular, self-illumination, ambient cube). The front-end layout has 6, adding the
  specular cube, with the same push-constant range. The ambient-cube pipelines use
  `frontEndPipelineLayout`, so bind the joint set (set 1) with that same layout.
- **Ambient-cube push constants** (40 floats):
  - 16–18: the view origin in model space;
  - 32–35: brightness, `$ambientScale.x`, mode (0 scale, 1 add specular and
    self-illumination, 2 shadow scale), the self-illumination flag;
  - 36–39: the flat ambient colour and its use flag.
- **Cube or 2D:** generated ambient and specular cubes keep `cubeFiles` = CF_2D. Check a
  slot's type on the uploaded resource (`resource->cubeMap`), not on `cubeFiles`.
  Checking `cubeFiles` swapped every generated cube for black until #249.
- **The specular cube** (`SpecularCubeMapImage`) is our reconstruction: power-16 lights
  plus a power-32 sun on black, with no mips.
- **No facing cull:** interaction geometry (`CreateLightInteractionGeometry`) is culled to
  the light's frustum only, never by facing. `interaction.frag` discards N·L ≤ 0, but
  OpenGL's amblit program doesn't, so a pass that must match OpenGL's coverage must not add
  a facing test (#268).
- **`$diffuseColor`:** the light interaction doesn't apply the stage's `$diffuseColor`
  (OpenGL multiplies it into the light colour). No retail material sets it, so this only
  matters for mods.
- **Recreating the swapchain:** toggling `r_swapInterval` recreates it in `BeginFrame`
  without a resize. That's the cheapest way to exercise swapchain-recreation paths.
- **A probe for OpenGL's amblit surfaces:** `r_skipInteractions 1`, `r_ambientScale 0` and a
  white ambient cube don't change OpenGL's lighting of sun-lit models, because those take the
  amblit path. `r_shaderSkipSpecCubeMaps` is read only by `basic_ambient_fragment.inc` and
  `basic_fragment.inc`, so at `r_lightScale 0` it isolates the ambient specular-cube term.

### Stencil users and their baselines

| user | clear | test |
|---|---|---|
| ambient-cube marks and the flat ambient | 0 (`ClearViewStencil`) | cube: equal 0, then increment; flat: equal 0 |
| a light's shadows | 128 over the light scissor | lit: 128 >= stencil; shadowed scale: 128 < stencil |
| fog once | 0 | equal 0, then increment |

A new stencil user must pick its baseline and say where it clears.

### Push constants that several passes share

- **The interaction** (`interaction.vert`/`.frag`):
  - `pc.color.rgb` is the view origin for specular, or the scale in the notlit shadow
    split; `pc.color.a` is the specular factor (0 for foliage and GUI worlds), or −1 for
    MegaTexture terrain (scene albedo);
  - `parameters.x` turns the vertex colour on;
  - `parameters.z` is the alpha reference, or the ambient already applied for notlit;
  - `lightColorDirectional.w` = 1 for the sun, + 2 for a two-sided material, + 4 for
    notlit terrain.
- **The world base pass** (`ambient.vert`/`.frag`): out = texture × `pc.color` ×
  mix(1, vertex colour, `parameters.x`).
  - `parameters` = {vertex colour, texcoord scale, alpha reference, alpha-test flag: 1
    for an alpha test, 2 for the grass dither}.
  - `VulkanStageUsesVertexColor` decides `parameters.x`. It's on for `SVC_MODULATE`
    stages and for `interaction/foliage*` and `stuff/grass*`, since retail's programs
    always read `$colorAttrib`.

### The scene's alpha is the glow's weight

- **The glow:** retail's `postprocess/glow` composites clean × glare.x + blur × glare.y.
  The blur source (`downsample_glare`) is 0.3 × the scene's alpha on valley, so the
  scene's alpha decides how much each pixel glows.
- **Vulkan mirrors retail OpenGL's alpha** (#272):
  - the clear is 1;
  - an opaque lit surface (MegaTexture `notlit_lit`, the opaque base pass) writes 0;
  - the skybox writes about 0.35;
  - alpha-tested surfaces keep 1;
  - glBlendFunc factors apply to alpha too;
  - fog blends alpha as SRC_ALPHA / 1 − SRC_ALPHA.
- **The machinery:** specialization constant 0, `OPAQUE_BASE`, in
  `CreateWorldPipelineVariant`, used by the world fragment shaders. `r_vkGlowDebug 1`
  draws the scene's alpha instead of the glow.
- **Valley's post-process parameters:** glare 1.12, 1.02, 1, 0.7; glareBases 1, 1, 0, 0;
  saturation, contrast and tint all 1. With glare.z = 1, the Cg's smoothstep(1, 1, lum) is a
  step at 1. `guis/postprocess` draws `postprocess/glow` while
  `globals.gameHud.showPostProcess` is 0; Strogg players get glowStrogg; heatsight is
  separate.
  - The cost on Apple M1 OpenGL is +0.15 to +0.18 ms a frame.
  - `g_skipPostProcess 1` skips it (`sdPostProcess::DrawPost`, game `HudModule.cpp`).
- **Residuals:** fogged far cliffs carry about 0.3 against OpenGL's about 0; glowStrogg
  and heatsight aren't ported.

### Descriptor caches

- **Front-end sets:** front-end (ambient-program) descriptor sets have their own pool
  (4,096 sets, also capped in software, because Adreno ignores `maxSets`). They're
  hashed by owner (#261).
- **Material sets:** material sets share `guiDescriptorPool` (#264).
- **Freeing:** both are freed when a purged image's frame retires, and evicted when unused
  for more than `NUM_VULKAN_FRAMES` frames and the pool is full. Image descriptors
  allocate through the same evicting path. The sets that name `_currentRender` are
  purged when the swapchain is recreated (#266).

## 3. Ray tracing

`RunRasterLighting` does nothing with ray tracing on; `lighting.comp` lights the frame
instead.

- **Instances:** instanced surfaces reach ray tracing once per surface, through
  `rayTracingSource`. `AppendRayTracingInstances` expands every instance unculled (#248).
- **Light colour:** the colour clamp of #250 also applies here.

## 4. MegaTexture

- **Levels:** level 0 is the finest (256 tiles a side).
  - A level is drawn only when it's complete. After a teleport, the finest level takes
    seconds to stream and decode, so wait about 15 s before measuring.
  - `megaTextureInfo` prints each level's state. Its `valid` flag can stay 1 across a
    teleport, so time against "256 loaded and decoded, 0 queued, 0 to upload".
  - Use a different teleport target each time, since a repeat hits a warm tile cache.
- **The decoder** (`megatexturecodec.cpp`) is bit-identical to the original reference
  loop. The IDCT sums only the nonzero coefficients, from a product table, in the original
  order (#247).
- **Codec quality:** valley's tiles are quality 75/75/40, and its LUM level 78/78/27. Ours
  truncates `std × scale / 100` where libjpeg rounds (+50): exact for luma DC at quality 75,
  but chroma DC 8 against 9 and about 2% on AC. That's a possible small follow-up; it isn't
  what made #233's terrain dark.
- **Mipmaps on Vulkan:** levels above 0 of images with a mipmap state (for example the
  detail texture's fade to grey) are uploaded from the CPU mips (`GenerateImageEx`, #241),
  not blitted on the GPU.

## 5. Stuff: grass and rubbish (`Model_Stuff.cpp`)

- **Snapshots:** the stuff snapshot is rebuilt when the view moves `r_stuffUpdateDistance`
  (100) or turns, or when a fade cvar changes. To force a rebuild after a `setviewpos`,
  move away and back.
- **Grass fade:** retail fades grass per vertex between `r_stuffFadeStart` and
  `r_stuffFadeEnd`, and discards against `_diffusionMask`: a 16×16 screen-space threshold,
  sampled at the fragment position / 16.
  - `_diffusionMask` was a white placeholder until #269, which made it an ordered dither.
    Retail's coverage and instance-dither programs use it too.
  - Vulkan writes each grass instance's fade into the vertex alpha. The world shader
    discards against the same dither when the alpha-test flag is 2.
- **Grass colour:** diffuse × the instance tint (from the `.clustb`) × (ambient cube ×
  brightness + the clamped sun term). On Vulkan it must draw after the lighting block
  (#275).

## 6. Instancing and imposters (#246, deferred)

- **What exists:** `sdInstStatic` (game `Misc.cpp`) fills per-instance origin, axis,
  `maxVisDist`, `minVisDist` and `fadeOrigin` for every `misc_instance_spawner`.
  - Valley has 72 spawners, 30 of them with an `imposter_instance`; their `maxVisDist`
    values run 1,536–6,144.
- **Not drawn yet:**
  - The renderer never reads `renderEntity.imposter`; `declimposter.cpp` only parses it.
  - Retail's `imposters/lit` and `lit_foliage` programs are Cg, and `lit_foliage` fails
    to parse.
- **The distance cull:**
  - Vulkan raster culls instances by distance;
  - OpenGL's instance loop (`tr_render.cpp`) and ray tracing don't;
  - the unmerged branch `fix/instance-distance-cull-246` adds it for both.
- **Retail:** with `r_useMaxVisDist 0`, retail stops drawing distant trees, so it draws
  imposters past the cut.

## 7. Debugging traps

- **Placeholders:** images and bindings from the initial commit can be placeholders
  (`_diffusionMask` was white). Check a generated image's contents before trusting
  the program that samples it.
- **Cvars that need a callback:** `r_skipSpecular` and `r_skipBump` act only through
  change callbacks (`draw_arb2.cpp`), so set them at runtime, not on the command line.
  The same goes for `r_skipInteractions` and `r_skipAmbient` when bisecting.
- **Unknown cvars in programs:** a render program's `$if` reads a cvar by name, and a cvar
  that doesn't exist reads as 0 (`RenderProgramParser.cpp`). That's why
  `r_dxnNormalMaps` and `r_usePerFragmentSpecular` are always 0.
- **Confirm the knob took effect,** from a log line or a visible change, before reading a
  result. Several wrong conclusions came from tests whose switch never applied.
