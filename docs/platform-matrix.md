<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Platform matrix (M7 release gate)

What has been verified on each platform: the latest full retail-data suite on record for each,
with its commits and result. The per-run evidence register (every row with its raw log) is kept
by the project's sessions outside this repository; this file carries the summary.

Last updated: 2026-11-02.

## Evidence levels

- **runtime**: the thing ran on that host and its own assertions or checks were read. The
  `CONTRIBUTING.md` level is given in parentheses when it is narrower (`map-loaded`,
  `match-tested`).
- **outcome-only**: an outcome was observed but not asserted by a test (a manual session, a
  screenshot review, a one-off measurement).
- **static**: no game execution; source, build, scan or validation checks only.

A skip (exit 77) is not a pass. A run on a software rasterizer (llvmpipe, no `/dev/dri`) is
not GPU evidence.

## Platform coverage

| Platform | Latest full retail suite on record | Date | Commits | Result |
|---|---|---|---|---|
| Linux arm64 | 77 tests | 2026-10-26 | `527c1ef` / `7fbdf80` | 76 OK, 0 FAIL, 1 SKIP (`cross-os`) on the post-merge leg at `-O2`. The 2026-10-28 run at `7031119` (79 OK, 0 FAIL, 1 SKIP) was reported but has not been recorded from its raw log yet |
| Linux x86_64 | 112 tests | 2026-11-02 | `main` `2f8eb52` with #408, #410, #411 and #412 merged in / game #70 `5b59b20` | 110 OK, 0 FAIL, 2 SKIP (`cross-os`; `text-input-x11`, which needs Xvfb and xdotool on the host); clean |
| Windows 11 x64 | 80 tests | 2026-10-28 | `7031119` / `7fbdf80` | 78 OK, 0 FAIL, 2 SKIP (`cross-os`, `client-server-timeout`); clean |
| Windows 11 arm64 | 80 tests | 2026-10-28 | `7031119` / `7fbdf80` | 78 OK, 0 FAIL, 2 SKIP; clean, and the first suite on Vulkan (no OpenGL/D3D12 fallback) |
| macOS arm64 | 80 tests | 2026-10-28 | `7031119` / `7fbdf80` | 78 OK, 1 FAIL, 1 SKIP; the first GPU suite (MoltenVK). The failure, `mixed-caps` A10 (#335), is fixed by #336 at `ce94b9e`; not yet re-run as a full suite |

A result stands for the commits named in its row, not for `main` today. Since 2026-10-28,
#336 (the macOS `mixed-caps` failure), #340, #344, #345 and #346 have merged.
The other platforms' rows predate the LAN-session fixes of 2026-10-30 to 2026-11-02
(#363 to #412); the Linux x86_64 row covers them, apart from the last commit of #412, which
changes only its OpenGL vertex program and the minimap test's bound (both minimap tests
passed at that commit on the Linux x64, Windows x64 and Linux arm64 hosts).
