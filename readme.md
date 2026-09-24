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

OpenETQW needs the data from a full retail ET:QW install patched to 1.5 (the `base/` paks). You must own the game. The branches of both repositories contain no retail game data, executables or media, and none may be added. One retail image was purged from both histories on 2026-09-24; GitHub still keeps it in pull-request refs until those are removed (see `STATUS.md`, release gate 5). No retail executable is used at run time.

The engine does not yet refuse to run without a full retail install. That check is one of the release-gate items.

## Network play: OpenETQW only, for now

OpenETQW speaks its own network protocol, 20293.5 (major `0x4F45`, "OE", minor 5; see `src/framework/async/AsyncNetwork.h`). It cannot play with retail ET:QW 1.5 clients or servers. The server refuses a retail client at connect with "Server uses protocol 20293.5" (tested with a simulated retail peer; no real retail 1.5 client has been tried).

This follows openQ4. Retail 1.5 network compatibility is deferred, not ruled out: the owner will decide later whether to restore it. Retail 1.5 game data stays required and supported.

## Status

As of 2026-09-24, every engine build and every runtime result comes from one host: macOS arm64 (Apple Silicon, Apple Clang 21, debug builds). The game module alone also compiled in hosted CI on Linux, Windows and macOS x64 before the Actions minutes ran out. On that host the dedicated server loads every stock map with its scripts and navigation, and bots move. The Vulkan client (through MoltenVK) has rendered five maps, checked by draw counters rather than images. A dedicated server has hosted two headless clients over loopback. The engine has not yet been configured or compiled on Linux or Windows, and the graphical client has never connected to a server. [`STATUS.md`](STATUS.md) has the evidence levels, the exact commands and the open items.

## Build on macOS

The development host uses Apple Clang from the Xcode command line tools, Python 3, and these Homebrew packages: `meson`, `ninja`, `pkgconf`, `sdl3`, `vulkan-headers`, `vulkan-loader`, `molten-vk`.

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
#    The engine build does not copy it; the development host uses a symlink.
ln -s "$PWD/../openETQW-game/build/game_arm64.dylib" build/game_arm64.dylib
```

This produces `build/openETQW-client_arm64` and `build/openETQW-ded_arm64`.

Configuring the engine copies headers and a few sources from the game repository into `.tmp/gamelibs_stage`. After changing idLib or interface headers in the game repository, run `meson setup --reconfigure build` in the engine, or it keeps using the old copies.

## Test

```sh
# Asset-free unit tests (no retail data needed)
meson test -C build --suite unit

# Full suite with retail data. It starts real servers and clients on fixed
# UDP ports, so run one test at a time and one suite per host.
ETQW_DATA_PATH=/path/to/etqw meson test -C build -j 1
```

`ETQW_DATA_PATH` is the directory that contains `base/pak000.pk4`. Without it, every retail-data test exits 77 and Meson reports it as skipped.

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
- At HEAD this repository contains no SDK-licensed files (checked by the `zero-sdk` test). Its history does, through jmarshall's root commits; whether that must change is an open owner question. The engine build still uses SDK-licensed code from the game repository: it links the game's SDK idLib (`libidLib.a`) and compiles headers and `DeclType.cpp` staged from it. So engine binaries are not yet free of SDK code. These are tracked exceptions.
- `docs/provenance.md` records the origin and license of each imported file. The 2026-09-24 audit found gaps in it: files added after the split are not in it yet, the SDK code the engine build uses was under-registered, and some files were labeled wrongly (corrected on 2026-09-24).
- This repository is a hard fork of jmarshall's ET:QW source reconstruction (jmarshall/etqw), and its history starts from his commits. That code has no license file, and some of it describes itself as derived from the retail binaries. Whether it can be redistributed is an open owner question.

The release gate has six items: a redistributable license for jmarshall's code (or its replacement); engine binaries that link no SDK code, apart from exceptions the owner approves; provenance that covers every file; LICENSE and README files that explain the split, the SDK license and the retail-data requirement; no retail content anywhere in either repository's history; and the SDK EULA with every package, an enforced retail-install requirement, and nothing sold. None of them is cleared yet. The READMEs of both repositories are the work toward the fourth item, pending the owner's review.

## Documents

- [`STATUS.md`](STATUS.md): current state of both repositories, with evidence.
- [`CONTRIBUTING.md`](CONTRIBUTING.md): merge gates and local checks.
- [`docs/compat-ledger.md`](docs/compat-ledger.md): deliberate deviations from retail 1.5.
- [`docs/provenance.md`](docs/provenance.md): file origins and licenses.
