<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Build helpers

One command per platform that takes a fresh checkout to a running client or server:

| Platform | Command |
|---|---|
| macOS arm64 | `./scripts/build-macos.sh` |
| Linux x64 and arm64 | `./scripts/build-linux.sh` |
| Windows x64 and arm64 | `powershell -ExecutionPolicy Bypass -File scripts\build-windows.ps1` |

They follow the owner's tributary and balun helpers (`scripts/build-*.sh`, `build-windows.ps1`):
no options does a full build, and a few flags launch, test or validate. They call meson and
the existing tools. Their logic lives here and in the scripts, not in a new build system.
macOS and Linux share `scripts/lib/build-common.sh`.

## Why (2026-10-22)

The build was a hand-run recipe from `readme.md`:
1. build the game repository;
2. build the engine with the right `-D` options;
3. symlink the game module next to the binaries;
4. rerun `meson setup --reconfigure` when game headers change, or the engine keeps
   stale staged copies (`.tmp/gamelibs_stage`).

Windows adds Visual Studio's environment, vcpkg's variables and `cmake_prefix_path`. The
helpers do all of that, so that a person can build for a LAN playtest without the
project's notes. `openetqw_validate.py` is the merge gate's check, not a user build, and
stays as it is (`--validate` calls it).

## What a run does

With no options:
1. **Dependencies.** Checks the tools and libraries.
   - macOS: the Xcode command line tools, Python 3, Homebrew, and the formulae `meson ninja
     pkgconf sdl3 vulkan-headers vulkan-loader molten-vk glslang`. Missing formulae are
     installed with `brew install`, as tributary does.
   - Linux: `c++`, `meson`, `ninja`, `pkg-config`, `glslangValidator`, and the pkg-config
     modules `sdl3`, `vulkan`, `zlib` and `gl` plus the Vulkan headers. It prints the
     `dnf`, `apt-get` or `pacman` command for what is missing; `--install-deps` runs it with
     `sudo`. On apt it points at `tools/ci/linux_deps.sh` for SDL3, which Ubuntu 24.04 and
     Debian 12 do not package.
   - Windows: Visual Studio with the C++ tools for the host's architecture (vswhere),
     Python with meson and ninja, vcpkg (`-VcpkgRoot`, `$env:VCPKG_ROOT` or
     `%USERPROFILE%\vcpkg`) with `sdl3[vulkan] zlib vulkan-headers vulkan-loader pkgconf`
     for `x64-windows` or `arm64-windows`, and glslangValidator (a warning only).
     `-InstallDeps` runs `vcpkg install`, which needs no administrator rights.
   - `--no-deps` skips the check, for hosts set up by hand.
2. **The game repository.** `$OPENETQW_GAME_REPO`, else `../openETQW-game`, the
   engine's own rule (`meson.build`). If it is missing, the helper clones
   `jm2/openETQW-game` there and checks out the engine's pin (`tools/ci/companion.ref`).
   If it is at another commit, it warns and builds anyway; `--sync-game` checks out the
   pin, and refuses if the checkout has uncommitted changes. It never moves a checkout
   without being asked.
3. **The game build** in the game repository's `build/`, where the engine looks for idLib
   and the module.
4. **The engine build** with `-Dbuild_engine=true -Dbuild_dedicated=true -Dbuild_tests=true`
   (Windows also passes `--vsenv` and `-Dcmake_prefix_path`). The engine's `build/` by
   default, `--build-dir` otherwise.
   - Both builds use one build type, since the engine links the game's idLib:
     `debugoptimized` by default, `--debug` or `--release` (Windows: `-BuildType`).
   - An existing build directory is always set up with `--reconfigure`, so game headers
     are staged again; `--clean` wipes both build directories first.
5. **The game module.** On macOS and Linux it is symlinked next to the binaries; on
   Windows the engine build copies it, with `SDL3.dll` and `z.dll`.
6. It prints where the client, the dedicated server and the module are.

Then at most one of:
- `--test`: `meson test --suite unit`, the asset-free suite.
- `--run`: the client, from the engine root (Windows finds its shaders through the
  current directory). Without `--map` it opens the main menu; with it, a `+devmap`
  session.
- `--server`: the dedicated server, `+spawnServer` on `--map` (default `valley`), with
  objective rules and `si_pure 0`, as the readme's command.

Both pass `+set fs_basepath $ETQW_DATA_PATH` when that is set, and otherwise let the
engine find the retail data itself (readme, Install and packages).

`--validate` runs `tools/validation/openetqw_validate.py pr` instead of a build.
`--dry-run` prints every command without running it. Conflicting options are usage errors
(exit 2): `--test` with `--run` or `--server`, `--map` without either, `--validate` with
build options, `--debug` with `--release`, and unknown options.

## Test

meson `build-scripts` (unit suite, `tests/test_build_scripts.py`) runs the host's helper
with `--dry-run --no-deps` against a stand-in game repository. It checks the usage errors,
the step order (game, then engine), one build type for both, reconfigure against
`--clean`, the engine's options, the module link, the launch lines and a missing
`OPENETQW_GAME_REPO`. On macOS and Linux it also runs the Windows helper when `pwsh` is
installed. It needs no network, no compiler and no retail data.

## Not in this step

- **`--package`**: a release package (`.app`/`.dmg`, tar.xz and AppImage, zip and
  installer) through `tools/build/package_release.py`. That needs `build_glslang.py`'s
  output and the bundled licences, so it is a step of its own.
- **A Linux arm64 or Windows arm64 specific path**: none is needed; the helpers take the
  architecture from the host.

## Evidence

| Host | Helper | What ran | Result |
|---|---|---|---|
| `mbp16-m1max` (macOS 27.0.1 arm64, Apple Clang, Homebrew) | `build-macos.sh` | a fresh game and engine build with `--test`; a second run (reconfigure) with `--server --map valley`, retail 1.5 data | built; unit suite 14/14; valley loaded on the dedicated server, `quit` shut it down |
| `mbp16-m1max` | `build-windows.ps1` (pwsh 7, dry run) | `build-scripts` | pass |
| Linux x64, Linux arm64 | `build-linux.sh` | — | to be run |
| Windows x64, Windows arm64 | `build-windows.ps1` | — | to be run |
