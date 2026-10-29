<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# OpenETQW

OpenETQW is a native 64-bit engine for *Enemy Territory: Quake Wars* (ET:QW), in the spirit
of openQ4. It runs the original game from your own retail copy, with native clients and
dedicated servers on macOS, Linux and Windows, LAN play between OpenETQW players, and
smooth high-refresh rendering.

This repository is the engine. The game module lives in the companion repository,
[`openETQW-game`](https://github.com/jm2/openETQW-game).

OpenETQW is an unofficial fan project. It is not affiliated with or endorsed by id Software,
Splash Damage, Activision or ZeniMax Media. Enemy Territory: Quake Wars, ET:QW, Quake and the
company names above are trademarks or registered trademarks of their respective owners.
OpenETQW does not use the game's logos or artwork.

There is no release yet: to play, build it from source (below).

## What you need

**The game.** OpenETQW needs the data from a retail copy of ET:QW that you own, patched to
version 1.5. Your install's `base/` folder must hold:

| Set | Files | Checked at startup |
|---|---|---|
| Base game | `pak000.pk4`–`pak004.pk4` | yes, by checksum |
| Official 1.5 patch | `pak005.pk4`–`pak008.pk4` | yes, by checksum |
| Language (base game) | `zpak_<language>000.pk4` | no (the client warns if it's missing) |
| Language (1.5 patch) | `zpak_<language>001.pk4`–`003.pk4` | no |

The engine won't start on unpatched or incomplete data. The expected checksums are in
[`docs/official-pk4-checksums.md`](docs/official-pk4-checksums.md). Neither repository
contains any game data, executables or media, and the engine never uses the retail
executable.

**A supported platform.** macOS on Apple silicon, Linux x64 or arm64, or Windows x64 or
arm64.

## Multiplayer

OpenETQW players can play each other over a LAN, across all supported platforms. OpenETQW
uses its own network protocol, so it can't join retail ET:QW servers, and retail clients
can't join OpenETQW servers: a retail client is turned away at connect with "Server uses
protocol 20293.5".

## Where things stand

The engine builds and runs on all five platforms. The dedicated server loads every stock
map with its scripts and bot navigation, and bots play. The Vulkan client renders the maps,
joins dedicated servers and hosts listen servers. Automated tests cover servers with
several clients, reconnects and map changes, dropped and kicked players, a long session over
a lossy connection, and LAN games between macOS, Linux and Windows in every direction.

The client can draw well above the game's 30 Hz simulation rate (capped at 240 fps by
default), on foot and in the vehicle seats where that's turned on.

[`STATUS.md`](STATUS.md) has the details and the known problems, and
[`docs/platform-matrix.md`](docs/platform-matrix.md) shows the latest full test run on each
platform.

## Quick build

The helper scripts in `scripts/` take a fresh checkout to a running game with one command.
[`docs/build-scripts.md`](docs/build-scripts.md) has the details, and the sections further
down show the same steps by hand.

### 1. Get the code

```sh
git clone https://github.com/jm2/openETQW.git
git clone https://github.com/jm2/openETQW-game.git   # optional: the helper clones it if missing
```

The helper looks for the game repository next to the engine (`../openETQW-game`), or
wherever `OPENETQW_GAME_REPO` points. If it isn't there, the helper clones it at the commit
the engine expects (`tools/ci/companion.ref`).

### 2. Install the basics

| Platform | Install first | The helper then |
|---|---|---|
| macOS | Xcode command line tools (`xcode-select --install`), [Homebrew](https://brew.sh) | installs the Homebrew packages it needs |
| Linux | Python 3, git | prints the `dnf`, `apt-get` or `pacman` command for anything missing; `--install-deps` runs it with `sudo` |
| Windows | Visual Studio 2026 with "Desktop development with C++" (plus the ARM64 build tools on arm64), Python 3 with `python -m pip install --user meson ninja`, [vcpkg](https://github.com/microsoft/vcpkg), the [Vulkan SDK](https://vulkan.lunarg.com) | checks them; `-InstallDeps` installs the libraries through vcpkg |

Ubuntu 24.04 and Debian 12 have no SDL3 package; `tools/ci/linux_deps.sh` builds it for you.

### 3. Build

From the engine repository:

```sh
./scripts/build-macos.sh          # macOS
./scripts/build-linux.sh          # Linux
```

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-windows.ps1    # Windows
```

This builds the game module, the client, the dedicated server and the unit tests, and tells
you where the binaries are: `build/openETQW-client_<arch>` and `build/openETQW-ded_<arch>`
(`.exe` on Windows). Run it again after changes and it rebuilds only what changed.

### 4. Play

Point the engine at your game install with `ETQW_DATA_PATH` (the folder whose `base/` holds
`pak000.pk4`), or leave it unset and the engine looks for an install itself.

```sh
export ETQW_DATA_PATH=~/etqw                     # optional
./scripts/build-macos.sh --run                   # build, then open the main menu
./scripts/build-macos.sh --run --map valley      # build, then play Valley locally
./scripts/build-linux.sh --server --map valley   # build, then host a dedicated server
```

```powershell
$env:ETQW_DATA_PATH = "C:\Games\ETQW"            # optional
.\scripts\build-windows.ps1 -Run
.\scripts\build-windows.ps1 -Server -Map valley
```

**A LAN game:** one machine hosts with `--server --map valley`. Everyone else starts the
client with `--run`, opens the console and types `connect <host IP>`. Type `quit` in the
server's terminal to stop it. The host's firewall has to let the server's UDP traffic in,
and macOS asks for Local Network access the first time.

### Options

| macOS / Linux | Windows | What it does |
|---|---|---|
| (none) | (none) | Check dependencies, build the game and the engine |
| `--run` | `-Run` | Then start the client (main menu, or a local game with `--map`) |
| `--server` | `-Server` | Then start a dedicated server on `--map` (default `valley`) |
| `--map NAME` | `-Map NAME` | The map for `--run` or `--server` |
| `--test` | `-Test` | Then run the unit tests (no game data needed) |
| `--debug`, `--release` | `-BuildType debug`, `-BuildType release` | Build type (default `debugoptimized`) |
| `--clean` | `-Clean` | Rebuild everything from scratch |
| `--build-dir DIR` | `-BuildDir DIR` | Engine build directory (default `build`) |
| `--sync-game` | `-SyncGame` | Move the game repository to the commit the engine expects (refuses if it has uncommitted changes) |
| `--install-deps` (Linux) | `-InstallDeps` | Install missing packages instead of printing the command |
| `--no-deps` | `-NoDeps` | Skip the dependency check |
| — | `-VcpkgRoot DIR` | Where vcpkg is (default `$env:VCPKG_ROOT`, else `%USERPROFILE%\vcpkg`) |
| `--validate` | `-Validate` | Run the pre-merge check (`openetqw_validate.py pr`) instead of a build |
| `--dry-run` | `-DryRun` | Print the commands without running them |
| `--help` | `-Help` | List the options |

`--run`, `--server` and `--test` can't be combined.

### Renderers

**Vulkan is the default and the one to use** (MoltenVK on macOS). **OpenGL is a fallback**
(`+set r_renderAPI opengl`) for systems where Vulkan doesn't work. Some of the original
effects, such as smoke and the vision enhancer, were written for NVIDIA's Cg, which has no
runtime on 64-bit systems, so OpenGL leaves them out. The sky, atmosphere, grass and
post-processing have been ported. Expect Vulkan to look better and to get fixes first.

### After pulling new changes

Run the helper again. If the game repository needs to move to a newer commit, the helper
warns you; add `--sync-game` to do it.

### If something goes wrong

- **A missing dependency:** the helper names it and the command to install it.
- **A build that behaves oddly after big changes:** `--clean` (Windows `-Clean`).
- **"not the engine's pinned commit":** add `--sync-game`.
- **Windows prints `'vswhere.exe' is not recognized` during setup:** harmless.
- **The engine can't find the game data:** set `ETQW_DATA_PATH`; the startup log lists where
  it looked.

## Building by hand

### macOS

You need the Xcode command line tools, Python 3 and these Homebrew packages: `meson`,
`ninja`, `pkgconf`, `sdl3`, `vulkan-headers`, `vulkan-loader`, `molten-vk`. Clone both
repositories side by side (or set `OPENETQW_GAME_REPO`).

```sh
# 1. The game module and its idLib (the engine links the idLib, so this comes first)
cd openETQW-game
meson setup build
meson compile -C build

# 2. The engine: client, dedicated server and tests
cd ../openETQW
meson setup build -Dbuild_engine=true -Dbuild_dedicated=true -Dbuild_tests=true
meson compile -C build

# 3. The engine loads the game module from its own directory; a symlink works
ln -s "$PWD/../openETQW-game/build/game_arm64.dylib" build/game_arm64.dylib
```

After changing headers in the game repository, run `meson setup --reconfigure build` in the
engine so it picks them up.

### Linux

You need GCC, Python 3, Meson, Ninja and (Fedora names) `gcc-c++`, `pkgconf`, `SDL3-devel`,
`vulkan-loader-devel`, `vulkan-headers`, `zlib-ng-compat-devel`, `mesa-libGL-devel` and
`glslang`, plus `vulkan-validation-layers` for the tests. The commands are the macOS ones,
with `game_x64.so` (or `game_arm64.so`) in the last step.

### Windows

You need Visual Studio 2026 with "Desktop development with C++", Python 3 with `meson` and
`ninja`, the Vulkan SDK, and vcpkg with `sdl3[vulkan]`, `zlib`, `vulkan-headers`,
`vulkan-loader` and `pkgconf` (triplet `x64-windows`, or `arm64-windows` on arm64).

```powershell
$vcpkg = "$env:USERPROFILE\vcpkg\installed\x64-windows"
$env:PKG_CONFIG = "$vcpkg\tools\pkgconf\pkgconf.exe"
$env:PKG_CONFIG_PATH = "$vcpkg\lib\pkgconfig;$vcpkg\share\pkgconfig"

# 1. The game module and its idLib
Set-Location openETQW-game
meson setup build --vsenv
meson compile -C build

# 2. The engine
Set-Location ..\openETQW
meson setup build --vsenv -Dbuild_engine=true -Dbuild_dedicated=true -Dbuild_tests=true "-Dcmake_prefix_path=$vcpkg"
meson compile -C build
```

The engine build copies the game module and the DLLs it needs next to the executables.
Configure both repositories with the same `--buildtype`, and start the client from the
repository root.

## Install

`meson install -C build` puts a portable copy of the engine in `.install/` (or
`-Dinstall_root=<dir>`): the client, the dedicated server, the game module, the shaders and
the licence texts. It runs from anywhere and keeps its settings, logs and shader cache in
your user folder (`~/.local/share/openetqw`, `%LOCALAPPDATA%\openETQW` or
`~/Library/Application Support/openETQW`).

If you don't tell it where the game is, the engine looks in this order:
1. `OPENETQW_BASEPATH`;
2. the path in `basepath.txt` in your user folder;
3. its own folder;
4. the usual install locations and the system's installer records;
5. your Steam libraries.

## Tests

```sh
# Unit tests (no game data needed)
meson test -C build --suite unit

# The full suite, with your game data. It starts real servers and clients on fixed
# UDP ports, so run one test at a time and one suite per machine.
ETQW_DATA_PATH=/path/to/etqw meson test -C build -j 1
# the same, skipping the 10-minute lossy-connection session
ETQW_DATA_PATH=/path/to/etqw meson test -C build -j 1 --no-suite soak
```

Without game data, the tests that need it report as skipped.

## Running it directly

```sh
# A dedicated server on Valley
build/openETQW-ded_arm64 +set dedicated 1 +set fs_basepath "$ETQW_DATA_PATH" +set fs_game base \
  +set si_rules sdGameRulesObjective +set si_pure 0 +spawnServer valley

# The client in a local game (cheats on, no network server)
build/openETQW-client_arm64 +set fs_basepath "$ETQW_DATA_PATH" +set fs_game base \
  +set si_rules sdGameRulesObjective +devmap valley
```

## Licensing

OpenETQW is built from several sources, each under its own terms:

- **The engine code in this repository** is under the GNU General Public License, version
  3 or later ([`LICENSE`](LICENSE)). Code that comes from id Software's Doom 3 GPL release
  keeps id's notices and additional terms.
- **Third-party libraries** keep their own licences; their notices are in their folders and
  in `licenses/` of every install.
- **The game module** (in `openETQW-game`) is id Software's ET:QW 1.5 SDK code, under the
  *Enemy Territory: Quake Wars SDK Limited Use License Agreement* (the SDK EULA,
  [`EULA_SDK_ETQW.txt`](EULA_SDK_ETQW.txt)), plus new files under the MIT licence. The SDK
  EULA allows free, non-commercial distribution only and requires the full retail game. The
  engine also links the game module's idLib, so every OpenETQW package carries the SDK EULA
  as well as the GPL.
- **Early history.** OpenETQW began as a fork of jmarshall's ET:QW source reconstruction,
  and this repository's history starts with his commits. The first three
  (`b8f61ab`, `d131a72`, `e1407bc`) include the SDK code, which is covered by the SDK EULA
  above, not by the GPL; it was moved to the game repository in `9043b91`.

[`docs/provenance.md`](docs/provenance.md) records where each file came from.

## Thanks

To id Software and Splash Damage for ET:QW and its SDK, to jmarshall for the reconstruction
this project grew from, and to openQ4, whose approach this project follows.

## Documents

- [`STATUS.md`](STATUS.md): what works today, with evidence.
- [`docs/platform-matrix.md`](docs/platform-matrix.md): the latest full test run on each platform.
- [`CONTRIBUTING.md`](CONTRIBUTING.md): how changes get in, and the checks to run first.
- [`AGENTS.md`](AGENTS.md): rules for coding agents.
- [`docs/compat-ledger.md`](docs/compat-ledger.md): where OpenETQW deliberately differs from retail 1.5.
- [`docs/provenance.md`](docs/provenance.md): file origins and licences.
- [`docs/releasing.md`](docs/releasing.md): CI, packages and releases.
- [`docs/m5-plan.md`](docs/m5-plan.md), [`docs/frame-path.md`](docs/frame-path.md),
  [`docs/vehicle-responsiveness.md`](docs/vehicle-responsiveness.md),
  [`docs/hull-lead.md`](docs/hull-lead.md): the high-refresh work.
