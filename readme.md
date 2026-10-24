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

The engine builds and runs on five platforms: macOS arm64 (Apple Clang), Linux x86_64 and arm64 (GCC), and Windows x64 and arm64 (MSVC). On each, the dedicated server loads every stock map with its scripts and navigation, and bots move. The Vulkan client (through MoltenVK on macOS) renders maps, joins dedicated servers and hosts listen servers. Automated tests cover:
- a dedicated server hosting several clients over loopback;
- reconnects and map changes;
- dropped and kicked clients;
- a long lossy session;
- LAN play between Linux x86_64, Windows x64 and macOS arm64, every pair in both directions.

High-refresh play, drawing above the 30 Hz game tick (default cap 240), works on foot and in the vehicle seats that are turned on. Which seats are on by default is still to be decided. [`STATUS.md`](STATUS.md) has the evidence levels, the exact commands, the known failures and the open items.

## Quick build

The build helpers in `scripts/` take a fresh checkout to a running game with one command
([`docs/build-scripts.md`](docs/build-scripts.md) has the details). The sections after this
one are the same steps by hand.

### 1. Get the two repositories side by side

```sh
git clone https://github.com/jm2/openETQW.git
git clone https://github.com/jm2/openETQW-game.git   # optional: the helper clones it if missing
```

The helper looks for the game repository at `../openETQW-game`, or wherever
`OPENETQW_GAME_REPO` points. If it is missing, the helper clones it there at the commit the
engine is pinned to (`tools/ci/companion.ref`).

### 2. Install what the helper can't

| Platform | Install first | The helper then |
|---|---|---|
| macOS arm64 | Xcode command line tools (`xcode-select --install`), [Homebrew](https://brew.sh) | installs the missing Homebrew packages itself |
| Linux x64 / arm64 | Python 3, git | prints the `dnf`, `apt-get` or `pacman` command for anything missing; `--install-deps` runs it with `sudo` |
| Windows x64 / arm64 | Visual Studio 2026 with "Desktop development with C++" (plus the ARM64 build tools on arm64), Python 3 with `python -m pip install --user meson ninja` (the helper finds the user Scripts directory), [vcpkg](https://github.com/microsoft/vcpkg), the [Vulkan SDK](https://vulkan.lunarg.com) | checks them; `-InstallDeps` runs `vcpkg install` for the libraries |

On Ubuntu 24.04 and Debian 12 there is no SDL3 package; `tools/ci/linux_deps.sh` builds it
into a private prefix.

### 3. Build

From the engine repository:

```sh
./scripts/build-macos.sh          # macOS
./scripts/build-linux.sh          # Linux
```

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-windows.ps1    # Windows
```

This checks the dependencies, builds the game module, then the client, the dedicated
server and the unit tests (`debugoptimized`), and puts the game module next to them. It
ends by printing where the binaries are: `build/openETQW-client_<arch>` and
`build/openETQW-ded_<arch>` (`.exe` on Windows). Running it again rebuilds only what
changed. The Windows helper also runs in PowerShell's ConstrainedLanguage mode
(AppLocker, WDAC).

### 4. Play

The game needs the retail 1.5 data (see [Retail game data is
required](#retail-game-data-is-required)). Set `ETQW_DATA_PATH` to the install root whose
`base/` holds `pak000.pk4`, or leave it unset and the engine looks for an install itself.

```sh
export ETQW_DATA_PATH=~/etqw                     # optional
./scripts/build-macos.sh --run                   # build, then open the main menu
./scripts/build-macos.sh --run --map valley      # build, then a local devmap session
./scripts/build-linux.sh --server --map valley   # build, then host a dedicated server
```

```powershell
$env:ETQW_DATA_PATH = "C:\Games\ETQW"            # optional
.\scripts\build-windows.ps1 -Run
.\scripts\build-windows.ps1 -Server -Map valley
```

**A LAN game:** one machine hosts with `--server --map valley`.
Players on any platform start the client with `--run`, open the console and type
`connect <host IP>`. The dedicated server's console reads commands from its terminal;
`quit` stops it. The host's firewall must let the server's UDP traffic in, and macOS asks
for Local Network access the first time a client or server uses the LAN.

### Options

| macOS / Linux | Windows | What it does |
|---|---|---|
| (none) | (none) | Check dependencies, build the game and the engine |
| `--run` | `-Run` | Then start the client (main menu, or a devmap session with `--map`) |
| `--server` | `-Server` | Then start a dedicated server on `--map` (default `valley`) |
| `--map NAME` | `-Map NAME` | The map for `--run` or `--server` |
| `--test` | `-Test` | Then run the asset-free unit tests |
| `--debug`, `--release` | `-BuildType debug`, `-BuildType release` | Build type for both repositories (default `debugoptimized`) |
| `--clean` | `-Clean` | Wipe both build directories first |
| `--build-dir DIR` | `-BuildDir DIR` | Engine build directory (default `build`) |
| `--sync-game` | `-SyncGame` | Move the game repository to the engine's pinned commit first (refuses if it has uncommitted changes) |
| `--install-deps` (Linux) | `-InstallDeps` | Install missing packages instead of printing the command |
| `--no-deps` | `-NoDeps` | Skip the dependency check |
| — | `-VcpkgRoot DIR` | The vcpkg tree (default `$env:VCPKG_ROOT`, else `%USERPROFILE%\vcpkg`) |
| `--validate` | `-Validate` | Run the merge gate's local check (`openetqw_validate.py pr`) instead of a build |
| `--dry-run` | `-DryRun` | Print the commands without running them |
| `--help` | `-Help` | List the options |

`--run`, `--server` and `--test` can't be combined.

### Renderers

**Vulkan is the main renderer and the default** (`r_renderAPI vulkan`; MoltenVK over Metal
on macOS). Use it unless it fails on your system. **OpenGL is a degraded fallback**
(`+set r_renderAPI opengl`): retail 1.5 wrote some of its effects (glow, smoke and others)
as Cg programs. Cg has no runtime on x64 or arm64, so OpenGL leaves those effects out,
except the sky, the atmosphere fades and the grass, which have our own ARB ports in
`base/glprogs` (`docs/compat-ledger.md` row 077). For now, expect Vulkan to look better than
OpenGL and to get fixes first.

### After pulling new changes

Run the helper again; it reconfigures the build, so changed game headers are picked up. If
the engine's pinned game commit moved, add `--sync-game` (or update the game repository
yourself). The helper warns when the game repository is not at the pinned commit.

### If something goes wrong

- **A missing dependency:** the helper names it and the command to install it.
- **A build that behaves strangely after big changes:** `--clean` (Windows `-Clean`)
  rebuilds both repositories from scratch.
- **"not the engine's pinned commit":** `--sync-game`, or check out
  `tools/ci/companion.ref`'s commit in the game repository.
- **Windows prints `'vswhere.exe' is not recognized` during setup:** harmless; it comes
  from Visual Studio's environment script that meson runs.
- **The engine can't find the retail data:** set `ETQW_DATA_PATH`; the startup log lists
  where it looked.

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

The commands are the macOS ones, with `game_x64.so` in the symlink step (`game_arm64.so` on arm64).

## Build on Windows

You need Windows x64 (or arm64, see below), Visual Studio 2026 (MSVC 19.51 or later, the "Desktop development with C++" workload), Python 3 with `meson` and `ninja`, the Vulkan SDK (the client compiles its shaders with its `glslangValidator`), and vcpkg (triplet `x64-windows`) with `sdl3[vulkan]`, `zlib`, `vulkan-headers`, `vulkan-loader` and `pkgconf`.

```powershell
# vcpkg's prefix; PKG_CONFIG lets Meson find SDL3 and zlib through pkg-config
$vcpkg = "$env:USERPROFILE\vcpkg\installed\x64-windows"
$env:PKG_CONFIG = "$vcpkg\tools\pkgconf\pkgconf.exe"
$env:PKG_CONFIG_PATH = "$vcpkg\lib\pkgconfig;$vcpkg\share\pkgconfig"

# 1. Game module and SDK idLib (build this first)
Set-Location openETQW-game
meson setup build --vsenv
meson compile -C build

# 2. Engine client, dedicated server and tests
Set-Location ..\openETQW
meson setup build --vsenv -Dbuild_engine=true -Dbuild_dedicated=true -Dbuild_tests=true "-Dcmake_prefix_path=$vcpkg"
meson compile -C build
```

This produces `build\openETQW-client_x64.exe` and `build\openETQW-ded_x64.exe`. Windows arm64 builds the same way, with the vcpkg triplet `arm64-windows` and the ARM64 toolset (`docs/coordination.md` §5 has the exact commands), and produces `_arm64` binaries. The engine build copies `game_x64.dll` (`game_arm64.dll` on arm64), `SDL3.dll` and `z.dll` next to them, and copies the game module again whenever the game build replaces it. Start the client from the repository root: it finds its Vulkan shaders (`base\vkprogs`) through the current directory.

The engine links the idLib in the game repository's `build` directory, so for an optimized build configure both with the same `--buildtype` (for example `debugoptimized`). MSVC 19.51 miscompiles an inlined `strncpy` at `/O2`; the game repository works around it (`src/idlib/text/StrBuilder.h`). The dedicated server stops cleanly on Ctrl+C or Ctrl+Break, as on SIGTERM elsewhere.

## Install and packages

`meson install -C build` stages a portable engine tree in `.install/` (or `-Dinstall_root=<dir>`): the client, the dedicated server, the game module, `base/vkprogs` and `licenses/`. That tree runs from anywhere: the engine finds its shaders and its game module next to its executable, and keeps configs, logs and the shader cache in the per-user directory (`~/.local/share/openetqw`, `$env:LOCALAPPDATA\openETQW`, `~/Library/Application Support/openETQW`). Without `+set fs_basepath`, the engine looks for the retail data itself. It takes the first install root whose `base/` holds `pak000.pk4`, in this order:
1. `OPENETQW_BASEPATH`, one or more install roots;
2. the path on the first line of `basepath.txt` in that per-user directory;
3. the engine's own directory;
4. the platform's installer records and usual install folders;
5. the game's Steam installs, read from each Steam library's app manifests.

If it finds none, the log lists where it looked and the startup error says how to point the engine at an install (ledger 068).

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

On Windows, in PowerShell:

```powershell
$env:ETQW_DATA_PATH = "C:\path\to\ETQW"
meson test -C build -j 1
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

On Windows, in PowerShell from the repository root:

```powershell
.\build\openETQW-client_x64.exe +set fs_basepath "$env:ETQW_DATA_PATH" +set fs_game base `
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

- [`AGENTS.md`](AGENTS.md): rules and the work loop for coding agents; start here.
- [`docs/queue.md`](docs/queue.md): the work queue, in order.
- [`docs/coordination.md`](docs/coordination.md): sessions, hosts, per-platform commands, reviews and merging.
- [`STATUS.md`](STATUS.md): current state of both repositories, with evidence.
- [`CONTRIBUTING.md`](CONTRIBUTING.md): merge gates and local checks.
- [`docs/compat-ledger.md`](docs/compat-ledger.md): deliberate deviations from retail 1.5.
- [`docs/provenance.md`](docs/provenance.md): file origins and licenses.
- [`docs/releasing.md`](docs/releasing.md): CI workflows, packages and releases.
- [`docs/m5-plan.md`](docs/m5-plan.md), [`docs/frame-path.md`](docs/frame-path.md), [`docs/vehicle-responsiveness.md`](docs/vehicle-responsiveness.md), [`docs/hull-lead.md`](docs/hull-lead.md): the high-refresh work.
