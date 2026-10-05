<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# OpenETQW

OpenETQW is a native 64-bit replacement engine for *Enemy Territory: Quake Wars* (ET:QW), in the mold of openQ4. It runs the original game from retail game data that you supply. The goals are native clients and dedicated servers, LAN multiplayer between OpenETQW players, and high-refresh presentation.

This repository is the engine. The game module is in the companion repository `openETQW-game`.

OpenETQW is an unofficial project. It is not affiliated with or endorsed by id Software, Splash Damage, Activision or ZeniMax Media. Enemy Territory: Quake Wars, ET:QW, Quake and the company names above are trademarks or registered trademarks of their respective owners. OpenETQW does not use the game's logos or artwork.

**Both repositories are private.** They stay private until the release gate is cleared and the owner decides to publish (see [Licensing](#licensing)). Nothing here is a release.

## Two repositories

| Repository | Contents | License |
|---|---|---|
| `openETQW` (this one) | Engine, platform layer, renderer, networking, tools, tests, docs, `STATUS.md` | GPL-3.0-or-later (`LICENSE`) |
| `openETQW-game` | Game module source, the SDK idLib, SDK interface headers | ET:QW SDK EULA for SDK-derived files (shipped verbatim in its `legal/`); MIT for new files |

The layout follows openQ4 and openQ4-game. The engine and the game module are built together from the two repositories. Compatibility with the retail game module ABI is not a goal.

## Retail game data is required

OpenETQW needs the game data from a legitimately owned retail ET:QW install, patched to version 1.5. You must own the game. Both the base game's paks and the 1.5 patch's paks are required in `base/`:

| Set | Files | Checked at startup |
|---|---|---|
| Base game | `pak000.pk4`–`pak004.pk4` | yes, by checksum |
| Official 1.5 patch | `pak005.pk4`–`pak008.pk4` | yes, by checksum |
| Language (base game) | `zpak_<language>000.pk4` | no |
| Language (1.5 patch) | `zpak_<language>001.pk4`–`003.pk4` | no |

This split follows the file list in the official 1.5 patch README. Without a base dialogue pak (`zpak_<language>000.pk4`) the client warns, as openQ4's does. Unpatched data is not supported. Neither repository contains retail game data, executables or media, and none may be added. No retail executable is used at run time.

The engine refuses to start unless `base/` holds `pak000`–`pak008` with their retail checksums: openQ4's retail-install check (`fs_validateOfficialPaks`; the checksums are listed in [`docs/official-pk4-checksums.md`](docs/official-pk4-checksums.md)). It is one of the release-gate safeguards. Tests on synthetic data turn it off with `+set fs_validateOfficialPaks 0`.

## Network play: OpenETQW only

OpenETQW speaks its own network protocol, 20293.5 (major `0x4F45`, "OE", minor 5; see `src/framework/async/AsyncNetwork.h`). It cannot play with retail ET:QW 1.5 clients or servers. The server refuses a retail client at connect with "Server uses protocol 20293.5" (tested with a simulated retail peer; no real retail 1.5 client has been tried).

This follows openQ4: retail 1.5 network compatibility is not a goal. Retail 1.5 game data stays required and supported.

## Status

The engine builds and runs on macOS arm64 (Apple Clang), Linux x86_64 (GCC) and Windows x64 (MSVC). The dedicated server loads every stock map with its scripts and navigation, and bots move. The Vulkan client (through MoltenVK on macOS) renders maps, joins dedicated servers and hosts listen servers. Automated tests cover a dedicated server hosting multiple clients over loopback, reconnects, map restarts and a long lossy session. [`STATUS.md`](STATUS.md) has the evidence levels, the exact commands, the known failures and the open items.

## Build on macOS

You need Apple Clang from the Xcode command line tools, Python 3, and these Homebrew packages: `meson`, `ninja`, `pkgconf`, `sdl3`, `vulkan-headers`, `vulkan-loader`, `molten-vk`.

Clone both repositories side by side. The engine looks for the game repository at `../openETQW-game`, or at `$OPENETQW_GAME_REPO` when that is set.

```sh
# 1. Game module and SDK idLib (the engine links the idLib, so build this first)
cd openETQW-game
meson setup build
meson compile -C build

# 2. Engine client, dedicated server and tests
cd ../openETQW
meson setup build -Dbuild_engine=true -Dbuild_dedicated=true -Dbuild_tests=true
meson compile -C build

# 3. The engine loads the game module from the directory of its executable.
#    The engine build does not copy it here; a symlink works.
#    (On Windows the engine build copies game_x64.dll, see Build on Windows.)
ln -s "$PWD/../openETQW-game/build/game_arm64.dylib" build/game_arm64.dylib
```

This produces `build/openETQW-client_arm64` and `build/openETQW-ded_arm64`.

Configuring the engine copies headers and a few sources from the game repository into `.tmp/gamelibs_stage`. After changing idLib or interface headers in the game repository, run `meson setup --reconfigure build` in the engine, or it keeps using the old copies.

## Build on Linux

You need GCC, Python 3, Meson and Ninja, and these packages (Fedora names): `gcc-c++`, `meson`, `ninja-build`, `pkgconf`, `SDL3-devel`, `vulkan-loader-devel`, `vulkan-headers`, `zlib-ng-compat-devel`, `mesa-libGL-devel`, plus `glslang` (the client compiles its shaders with `glslangValidator`) and `vulkan-validation-layers` for the tests. Ubuntu 24.04 has no SDL3 package; `tools/ci/linux_deps.sh` installs the rest and builds SDL3 into a private prefix, as CI does.

The commands are the macOS ones, with `game_x64.so` in the symlink step.

## Build on Windows

You need Windows x64, Visual Studio 2026 (MSVC 19.51 or later, the "Desktop development with C++" workload), Python 3 with `meson` and `ninja`, the Vulkan SDK (the client compiles its shaders with its `glslangValidator`), and vcpkg (triplet `x64-windows`) with `sdl3[vulkan]`, `zlib`, `vulkan-headers`, `vulkan-loader` and `pkgconf`.

```bat
rem vcpkg's prefix; PKG_CONFIG lets Meson find SDL3 and zlib through pkg-config
set VCPKG=%USERPROFILE%\vcpkg\installed\x64-windows
set PKG_CONFIG=%VCPKG%\tools\pkgconf\pkgconf.exe
set PKG_CONFIG_PATH=%VCPKG%\lib\pkgconfig;%VCPKG%\share\pkgconfig

rem 1. Game module and SDK idLib (build this first)
cd openETQW-game
meson setup build --vsenv
meson compile -C build

rem 2. Engine client, dedicated server and tests
cd ..\openETQW
meson setup build --vsenv -Dbuild_engine=true -Dbuild_dedicated=true -Dbuild_tests=true -Dcmake_prefix_path=%VCPKG%
meson compile -C build
```

This produces `build\openETQW-client_x64.exe` and `build\openETQW-ded_x64.exe`. The engine build copies `game_x64.dll`, `SDL3.dll` and `z.dll` next to them, and copies the game module again whenever the game build replaces it. Start the client from the repository root: it finds its Vulkan shaders (`base\vkprogs`) through the current directory.

The engine links the idLib in the game repository's `build` directory, so for an optimized build configure both with the same `--buildtype` (for example `debugoptimized`). MSVC 19.51 miscompiles an inlined `strncpy` at `/O2`; the game repository works around it (`src/idlib/text/StrBuilder.h`). The dedicated server stops cleanly on Ctrl+C or Ctrl+Break, as on SIGTERM elsewhere.

## Install and packages

`meson install -C build` stages a portable engine tree in `.install/` (or `-Dinstall_root=<dir>`): the client, the dedicated server, the game module, `base/vkprogs` and `licenses/`. That tree runs from anywhere: the engine finds its shaders and its game module next to its executable, and keeps configs, logs and the shader cache in the per-user directory (`~/.local/share/openetqw`, `%LOCALAPPDATA%\openETQW`, `~/Library/Application Support/openETQW`). Point it at the retail data with `+set fs_basepath`, or put the tree in the ET:QW install directory.

`python3 tools/validation/openetqw_validate.py pr` builds both repositories, runs the unit suite, stages the tree and checks it, as CI does. [`docs/releasing.md`](docs/releasing.md) describes the CI workflows, the packages and how a release is made.

## Test

```sh
# Asset-free unit tests (no retail data needed)
meson test -C build --suite unit

# Full suite with retail data. It starts real servers and clients on fixed
# UDP ports, so run one test at a time and one suite per host.
ETQW_DATA_PATH=/path/to/etqw meson test -C build -j 1
# the same without the 10-minute soak (lossy-session)
ETQW_DATA_PATH=/path/to/etqw meson test -C build -j 1 --no-suite soak
```

`ETQW_DATA_PATH` is the retail 1.5 install root, whose `base/` holds `pak000.pk4`–`pak008.pk4` (base game and 1.5 patch). Without it, or with any of those paks missing, every retail-data test exits 77 and Meson reports it as skipped.

## Run

```sh
# Dedicated server on Valley; the console reads stdin
build/openETQW-ded_arm64 +set dedicated 1 +set fs_basepath "$ETQW_DATA_PATH" +set fs_game base \
  +set si_rules sdGameRulesObjective +set si_pure 0 +spawnServer valley

# Client in a local devmap session (cheats on; this does not start a network server)
build/openETQW-client_arm64 +set fs_basepath "$ETQW_DATA_PATH" +set fs_game base \
  +set si_rules sdGameRulesObjective +devmap valley
```

## Licensing

- New engine code is GPL-3.0-or-later. Code derived from the Doom 3 GPL release keeps id Software's notices and additional terms. Third-party libraries keep their own licenses.
- At HEAD this repository contains no SDK-licensed files (checked by the `zero-sdk` test). Its history does, through jmarshall's root commits. The engine build uses SDK-licensed code from the game repository: it links the game's SDK idLib (`libidLib.a`) and compiles headers and `DeclType.cpp` staged from it. These are tracked exceptions, following openQ4's precedent (`docs/provenance.md` §4), so engine packages must carry the SDK EULA as well as the GPL (`docs/decisions/0001-sdk-code-in-the-engine-build.md`).
- The SDK EULA allows modifications that operate only with ET:QW. Whether that covers a game module running on OpenETQW is untested; the project accepts that risk, as openQ4 does for Quake 4, with two safeguards, both in place: the engine refuses to run without the retail 1.5 data (openQ4's check), and `meson install` puts the licence texts in every package (the GPL, the SDK EULA and the third-party notices under `licenses/`; `docs/decisions/0002-sdk-eula-risk-and-retail-install-check.md`).
- `docs/provenance.md` records the origin and license of each imported file. It does not yet cover every file (release gate item 3).
- This repository is a hard fork of jmarshall's ET:QW source reconstruction (jmarshall/etqw), and its history starts from his commits. That code has no license file, and some of it describes itself as derived from the retail binaries. Whether it can be redistributed is unresolved; until it is (a licence from him or replacement code), release gate item 1 stays blocking.

The release gate has six items: a redistributable license for jmarshall's code (or its replacement); engine binaries that link no SDK code, apart from exceptions the owner approves; provenance that covers every file; LICENSE and README files that explain the split, the SDK license and the retail-data requirement; no retail content anywhere in either repository's history; and the SDK EULA with every package, an enforced retail-install requirement, and nothing sold. None of the items is cleared yet. For item 2, the SDK exceptions are approved and their condition, the SDK EULA in every engine package, is met: `meson install` carries it (checked by the `install-licenses` test). The SDK files in the engine's early history still need EULA §5 handling before publishing, so the item is not cleared yet. The READMEs of both repositories are the work toward the fourth item, pending the owner's review.

## Documents

- [`STATUS.md`](STATUS.md): current state of both repositories, with evidence.
- [`CONTRIBUTING.md`](CONTRIBUTING.md): merge gates and local checks.
- [`docs/compat-ledger.md`](docs/compat-ledger.md): deliberate deviations from retail 1.5.
- [`docs/provenance.md`](docs/provenance.md): file origins and licenses.
- [`docs/releasing.md`](docs/releasing.md): CI workflows, packages and releases.
