# 0004: The retail ET:QW icon and launch splash

- **Date:** 2026-10-29
- **Status:** decided by the owner
- **Scope:** hard rule 3 (`AGENTS.md`); `docs/provenance.md` §7; packaging

## Context

Hard rule 3 keeps retail content out of both repositories. Two retail images are
useful to a port: the application icon and the launch splash. openQ4 ships the retail
Quake 4 splash (`assets/splash/quake4_rt_bitmap_4001.bmp`, the retail executable's
bitmap resource 4001) in its repository, its Windows executable and every package.

A history scan on 2026-10-29 found the retail ET:QW splash, `etqw_splash.bmp`, in
jmarshall's initial commit `6de0697`. No branch reaches that commit; GitHub's
`refs/pull/1/head` does in both repositories, so the file has been public since the
repositories were. The Windows bootstrap names it `IDB_ETQW_SPLASH` and shows nothing
in its place.

## Decision

The owner, 2026-10-29: "keep the splash" and "use the original icon if we have it for
now".

- `assets/splash/etqw_splash.bmp` is the splash from `6de0697`, unchanged (736×269,
  24-bit). It has not been compared byte for byte with the resource in a retail Windows
  `etqw.exe`; no retail Windows executable was at hand.
- `assets/icons/etqw_icon.png` is the icon id's Linux 1.5 client installs as
  `etqw_icon.png` (256×256 RGBA, SHA-256
  `ee009d912966584867144253b815eef564085ce74b85e8c51c400aeb43579927`).
  `assets/icons/etqw.ico` is made from it; the macOS `.icns` is made from it too.
- The PR refs that reach `6de0697` stay as they are.
- These files are the only exception to rule 3. Any other retail image, or a new one,
  needs its own decision.

## Consequences

- The provenance manifest classes everything under `assets/icons/` and
  `assets/splash/` as `Retail_asset`, `LicenseRef-ETQW-Retail`
  (`tools/build/provenance_post_split.py`). They are id Software's, not GPL.
- The icon replaces the placeholder `tools/build/linux/openetqw.svg` in the packages,
  and the splash is shown at start-up as the retail game and openQ4 show theirs.
- "For now": a project icon of its own can replace the retail one later.
