<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# GUI text

The GUI's text on every platform and both renderers is drawn from the game's own fonts (#181).

## The problem (2026-10-22)

`sdDeviceContext::DrawText` queues text in `sdGuiModel`. That model drew it only on Windows:
- Vulkan: `GetVulkanBitmapFont` built a GDI atlas of Arial Narrow. Everywhere else it was a
  stub that returned `NULL`, so every string was dropped. The main menu on macOS and Linux
  showed its panels and no words (the owner, on a Linux laptop).
- OpenGL: `wglUseFontBitmapsW` display lists, Windows only; elsewhere text was skipped.

Windows was wrong too: one system font for every GUI font, at the point size in virtual
pixels, so text was blurred on any screen larger than 640x480. `FindFont` recorded the name
and nothing used it.

`GetTextDimensions` guessed: half the point size per character, `scale` 1 and no per-character
advances. The game's `sdTextDimensionHelper` (`DeviceContextHelper.h`) reads those advances
for edit-box cursors, selection and `ShortenText`'s truncation; with none, every advance
was 0.

## What the data holds

- `localization/<language>/fonts/<name>.font`, in the language pak: a two-key declaration,
  for example `file "localization/fonts/dinpro-condmedium.otf"` and `faceIndex 0`. English
  ships `dinpro.font` and `digital.font`.
- The faces in `pak000.pk4` (`dinpro-condmedium.otf`, CFF outlines, and `lcdsh-regular.ttf`)
  and `pak006.pk4` (`arialuni.ttf`, which covers the characters the others lack).

## The design

One module, `src/renderer/GuiFont.{h,cpp}`, used by the device context and both GUI renderers.
- **Faces.** `FindFont( name )` keeps its handle numbering. The first use of a handle reads
  `localization/<sys_lang>/fonts/<name>.font`, then the English one, then loads the face
  file it names from the paks into memory for `stb_truetype` (v1.26, already in the tree as
  `src/imgui/imstb_truetype.h`, public domain; it reads CFF). A font that cannot be loaded
  uses `arialuni`, with one warning per name. A character missing from a face is taken from
  `arialuni`.
- **Units.** The GUI is 640x480 virtual units. `pointSize` is the em height in virtual
  units, as the Windows path used it. Glyphs are rasterised at the screen's size (em pixels
  = `pointSize` times screen height over 480), not at 640x480, and placed in virtual units
  with the horizontal and vertical scales separately (screen width over 640, height over
  480), so they keep their shape on any aspect ratio.
- **Layout**, one function for drawing and measuring: colour escapes (`^0` the draw colour,
  `^1`-`^D` the idWStr palette, `^^` nothing special; `DTF_NOCOLORS` strips them,
  `DTF_INCLUDECOLORESCAPES` draws them), new lines (ignored with `DTF_SINGLELINE`),
  `DTF_WORDWRAP` at the last space that fits (mid-word when one word is wider than the
  rectangle), `DTF_TRUNCATE` with `...`, kerning, horizontal and vertical alignment, and
  snapping to screen pixels unless `DTF_NO_SNAP_TO_PIXELS`.
- **Measuring.** `GetTextDimensions` returns the laid-out width and height in virtual units,
  `scale` as screen pixels per virtual unit (horizontal), each source character's advance in
  1/64 screen pixels (escapes 0) and the line breaks in the game's convention (the index of
  the first character of each later line, or of the `\n` that ends a line).
- **Atlases.** One per face and pixel size, 1024 wide, growing in height to 4096, filled on
  demand. Before a frame's GUI is drawn, every queued string is laid out, missing glyphs are
  rasterised and changed atlases are uploaded once, so no image changes while the frame
  records draws that use it.
- **Clipping.** A string queued with clipping on carries the clip rectangle it was queued
  under (unless `DTF_NOCLIPPING`); each glyph quad is cut to it with its texture
  coordinates.
- **Renderers.** Vulkan batches the glyph quads like other GUI triangles. OpenGL draws the
  same quads textured from the same atlas, on every platform; the GDI and `wgl` code goes.
- **Dedicated server.** Unchanged: it has no GUI and keeps its headless device context.

## Not in this step

- `DTF_DROPSHADOW`: the SDK marks it "TODO in the font rendering, handled by the GUIs".
- Right-to-left text and complex shaping: retail 1.5 has none.
- Whether retail 1.5's sizes and spacing match exactly: compared by screenshots against
  retail 1.5 on Windows, in the PR.

## Test

`tests/GuiTextLayoutTest.cpp` (unit suite): the layout on a stand-in face with fixed metrics.
It covers alignment, wrapping, truncation, escapes and the advances and line breaks that
`sdTextDimensionHelper` reads. The screenshot sets cover what is drawn.
