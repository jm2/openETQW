<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Comparing against retail 1.5

How the sessions measure openETQW against retail ET:QW 1.5. Retail images, captures and
logs stay on the host that made them (AGENTS rule 3): publish numbers, not pictures.

Written 2026-10-25 from the 2026-10-23 to 10-25 rendering work (#212, #233, #246, #256,
#270 and their PRs). Everything here was used in practice on the Linux x64 host.

## 1. Running retail

- **The scripted route:** `tools/validation/retail_capture.py` does all of §1 and §2 in one
  command and writes the mean of several grabs per view, plus `capture.json` with the §4
  box means (the part that can be published):

  ```
  python3 tools/validation/retail_capture.py --retail-dir <extracted retail tree> \
      --out <dir outside both repositories> --map valley \
      [--anchors <our screenshot_set.json>] [--views <views.json>] [--set "g_skipPostProcess 1"]
  ```

  Without `--views` it takes `screenshot_set.py`'s own views around the map's anchor, so
  the two sides line up. It applies the §3 resets unless `--keep-profile-spec` is given,
  refuses an `--out` inside either repository, and stops the compositor and retail when it
  ends. It needs `mutter`, the 32-bit retail runtime libraries and libX11/libXtst; it uses
  the GPU, so check host claims first. The extracted tree is the two retail packages
  unpacked into one scratch directory (`etqw.x86` beside `base/pak000.pk4`…`pak008.pk4`).
  Its unit test (`retail-capture-tool`) covers only the offline parts; a change to the
  driving code needs a real capture, looked at, before it merges.
- **Linux x64 is the reference host.** The retail Linux client (`etqw.x86`, 32-bit) runs from
  an extracted retail tree. Start it from its own directory with `LD_LIBRARY_PATH=.` and a
  private profile: `+set fs_savepath <dir> +set fs_userpath <dir> +set r_fullscreen 0
  +set r_mode 3 +set s_driver null +exec <cfg>`. `r_mode 3` is 640×480, the size every
  measurement box below assumes.
- **Run it under a private headless compositor**, never on the owner's desktop:
  `mutter --headless --wayland --wayland-display=<name> --virtual-monitor 1024x768`.
  Retail is an X client, so it runs on that compositor's Xwayland. Find the X display in
  the compositor's log ("Using public X11 display :N"). Mutter starts Xwayland only when
  the first X client connects, so there is no `-auth` argument to read beforehand: try
  each `/run/user/<uid>/.mutter-Xwaylandauth.*` made since the compositor started, newest
  first, until the display opens. The newest one overall can belong to someone else's
  compositor.
- **Stop what you started, by PID.** Never `pkill -f` or `pgrep -f` a pattern that also
  appears in your own command line: it matches your own shell and kills it.
- **Never use `SDL_VIDEO_DRIVER=offscreen` with OpenGL** on the Linux x64 host (it reset the
  amdgpu GPU). Run our OpenGL client under the private compositor too, with
  `WAYLAND_DISPLAY=<name> SDL_VIDEO_DRIVER=wayland`.

## 2. Driving retail

- **Input:** synthetic X input (XTest) reaches retail's window, but not our SDL3 window.
  - **Console commands:** XTest can't type an underscore, so put commands in a `.cfg` in the
    profile's `base/` and run them with grave, `exec x.cfg`, Return, grave.
  - **Checking the console:** before typing, look at a grab of the window. When the console
    is open, its top 200 rows are dark and a cyan line sits near row 235.
- **Reading a cvar:** `condump <file>` after typing the cvar's name writes `"name" is:"v"
  default:"d"`, plus the help text, to `base/<file>`.
- **Traps the script handles:**
  - a fresh profile starts with the console **open**, so toggle it by its observed state,
    never blindly;
  - keys reach retail through its top-level (the compositor's frame window), while
    `XGetImage` must read the inner 640×480 window: the frame adds a title bar, and the
    compositor's screen-sized InputOnly window answers `XGetImage` with BadMatch;
  - `devmap <map>` under retail's default campaign rules fails with "User Map Start Denied
    'Unknown Campaign'"; set `si_rules sdGameRulesObjective` first;
  - the "Create User" login dialog doesn't block console commands or `devmap`;
  - Xlib's default error handler exits the process, so install one, or an X error leaves
    retail and the compositor running.
- **Spawning:** after a map loads, limbo is open. `0` picks GDF and Return deploys, with the
  console closed. `l` toggles limbo in game.
- **Moving the camera:** `setviewpos x y z yaw pitch 0`. Retail ignores pitch while you
  fall, so turn on `noclip` for air views. `noclip` is a toggle: send it once.
- **Restarts:** after `vid_restart`, wait about 25 s, then close the console, which reopens.
- **Window grabs** use plain X calls: the largest mapped top-level window, read back with
  `XGetImage`.

## 3. Retail settings that look like differences but aren't

A profile retail creates itself is auto-detected at a low machine spec on the reference
GPU, and its saved `etqwconfig.cfg` then changes what retail draws. Check and reset these
before any comparison:

| cvar | low-spec profile | set to |
|---|---|---|
| `com_machineSpec` | 0 | 3 |
| `r_shaderQuality` | 2 | 0 (also changes which MegaTexture levels blend) |
| `r_detailTexture` | 0 | 1 |
| `r_skipStuff` | 1 (no grass or rubbish at all) | 0 |
| `r_visDistMult` | 0.8 (default 1) | note it when comparing draw distances |
| `r_renderProgramLodDistance` | -1 (default 200) | note it |

Also:
- **`r_skipSpecular`** takes effect only after a `vid_restart`; neither a runtime set nor
  `reloadDecls` changes the image.
- **Post-processing:** retail has no `r_skipPostProcess`. The full-screen post-process
  (glow, glare, saturation, contrast, tint) is turned off with `g_skipPostProcess 1`. It
  lifts the whole scene (valley's sky about 1.21×, its ground about +5.5 at
  `r_lightScale 0`), so compare with it on in both, or off in both.
- **Lighting:** `r_lightScale` (default 2) and `r_megaDrawMethod` (default 3) apply at
  runtime.

## 4. Measuring

- **Boxes:** compare mean RGB over fixed boxes at 640×480 with the same `setviewpos` on both
  sides:
  - near ground (20,300)–(300,470);
  - mid (20,200)–(300,300);
  - sky (20,20)–(200,120);
  - the viewmodel rifle (400,330)–(500,400).
- **Frames:** average 3 to 6 grabs about 0.7 s apart. The limbo soldier and the viewmodel idle
  by about ±2, which is more than many effects being measured.
- **Settle time:** about 3 s after a cvar change, 8–15 s after a `setviewpos`. The finest
  MegaTexture level can take several seconds to finish streaming after a teleport
  (`megaTextureInfo` shows each level's state).
- **Isolating one term,** with everything else held equal:
  - `r_lightScale 0`: MegaTexture `notlit_lit` becomes 0.65 × the terrain colour;
  - `r_skipInteractions 1`: ambient only;
  - `r_skipSpecular 1`, after a `vid_restart` in retail;
  - `r_shadows 0`;
  - `g_skipPostProcess 1`;
  - `r_skipStuff 1`, then diff against stuff on to mask the grass pixels.
- **Fitting:** a block-mean fit (for example 16 px blocks) of retail against ours tells a
  scale from an offset from a curve, but the views must line up. A misaligned view only
  adds noise.

## 5. Probing our renderer

- **Our side of a comparison:** `tools/validation/screenshot_set.py capture … --set
  r_mode 3 --anchors <set.json>` reuses another capture's view positions.
  - For one-off views, drive the client the way `screenshot_set.py` does: send console
    commands, then `set r_vkScreenshot <path>`, which captures OpenGL too. Close limbo with
    `closeLimbo` before `setviewpos`.
- **OpenGL:** to read one term of a retail program, edit its ARB port in `base/glprogs/`
  for one run. For example, append `MUL result.color.rgb, ambient, 0.25;` to output the
  ambient term scaled into range. The ports are found through the file system search
  path (`fs_cdpath` defaults to the folder that holds `base/vkprogs`), so a run from the
  checkout picks the edit up without a rebuild. Restore the file afterwards.
- **Vulkan:** gate a temporary push-constant override or print on an environment variable,
  for example forcing `pc.color` to 1 and the vertex colour off to see a raw texture. Print
  once per material or per draw, with a counter cap.
- **Retail's render programs:** for reference, read them from the paks:
  - `renderprogs/*/*.inc` are plain text;
  - `generated/declb/renderprogs/*.rprogb` are binary decls, but `strings -n 2` shows the
    program text.

  Extract into a scratch directory and delete it afterwards. Never commit retail content.

## 6. Methods that worked, from the sessions

- **Running our client:** start it from the checkout root or the install folder. Drive it
  over stdin: start it with stdin as a pipe and write console lines (`closeLimbo`, `noclip`,
  `setviewpos …`, cvars, then `set r_vkScreenshot "<absolute path>.tga"`). Wait about 70 s
  after `+devmap valley` before the first command, and about 4 s after each screenshot.
- **A/B toggles in one client:** send `set …` over the console, then capture, which saves a
  client start per variant. Exceptions:
  - cvars that act through change callbacks (`r_skipSpecular`, `r_skipBump`) do nothing on
    the command line;
  - a fresh client per variant is the equivalent of retail's `vid_restart`.
- **Compare ratios, not absolute colours:** compare the frame with a feature against the
  same frame with it skipped (for example `r_skipStuff 1`). Take the pixels that differ by
  more than a threshold, and report the feature's mean against the ground under it. Check
  that the ground matches retail's before trusting the ratio.
- **Black blobs across resolutions:** box-downscale to retail's 640-wide frame, then count
  near-black pixels (luma < 40) in the same fractional box.
- **An affine fit** of ours to retail over every box and variant points to a whole-frame
  pass when it fits well and a pure scale fits worse. Retail ≈ 0.824 × ours + 15.5 (rms 2)
  is how the missing post-process was found (#233).
- **Separate causes with cvar sweeps before writing code.** For example, `r_stuffFadeStart`
  0 and `r_stuffFadeEnd` 2600, against 5000 and 6000, isolated the grass fade's noise mask
  (#256).
- **Scoring a lighting change against OpenGL:** take the pixels where main and the PR
  differ (summed RGB > 18), and compare the mean |main − OpenGL| with |PR − OpenGL|. Always
  look at the difference image: the animated HUD hint, the flag and the weapon sway make
  large spurious differences, especially in the sky views.
- **Stress:** cycling `devmap` over six maps 18 times (about 30 minutes in one client)
  reveals descriptor and image leaks (#261, #264).
- **Retail's textures:** `generated/dds/…` holds retail's precompressed textures, under
  hashed names. Pillow opens the top mip of DXT1, DXT3 and DXT5; decode lower mips by hand
  (a short BC3 decoder is enough).
- **Retail's render programs:** read `renderprogs/*.inc` in place with Python's `zipfile`.
  For binary decls, run `strings -n 3` on the newest pak's copy (pak008 before pak007,
  and so on).
  - `renderbindings.rprogb` shows the bindings, for example `noiseMap` = `_diffusionMask`.
  - A render program's `$if` on a cvar that doesn't exist reads as 0.
- **Confirm the switch took effect** before reading a result. Several wrong conclusions
  came from tests whose cvar never applied, and others from a run that was really OpenGL
  (no glslang on PATH) or a pass that failed to compile.
