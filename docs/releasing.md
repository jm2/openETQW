<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# CI, packages and releases

openETQW's CI and release pipeline follows openQ4's (jm2/openQ4 b37b1456), with
one deliberate difference: **one macOS build**. openQ4 ships an OpenGL and a Metal
macOS build; openETQW's renderer defaults to Vulkan on every platform (MoltenVK over
Metal on macOS) and falls back to OpenGL at run time, so there is one macOS app.

Hosted GitHub Actions minutes for the private repositories are used up (since
2026-09-24), and the companion repository is private, so none of the workflows below
has built the engine on a hosted runner yet. Each one is checked with `actionlint`,
and every step that can run on a developer machine is a script under `tools/` that
runs the same way locally.

## Validation profiles

`tools/validation/openetqw_validate.py` is the one build-and-check entry point for
CI, the release and developers (`validate_push.sh`/`.ps1`, `validate_pr.sh`/`.ps1`):

```sh
python3 tools/validation/openetqw_validate.py pr     # what a pull request runs
python3 tools/validation/openetqw_validate.py push   # what main runs
python3 tools/validation/openetqw_validate.py push --dry-run   # print the commands only
```

In order: the game build (`../openETQW-game/build`), the engine build (client,
dedicated server, tests), the unit suite, the integration suite (every test must
skip without retail data), `meson install` into `<build-dir>/install`, the
staged-payload checks, and the assetless dedicated-server smoke on the staged
binary. `pr` also fails when the run leaves the source tree dirty.

The staged-payload checks: the client, the dedicated server and exactly one game
module for the architecture; `base/vkprogs` equal to the tracked files (no shader
cache); the six licence texts every package carries; no symlinks or names that
differ only in case; on Linux the ELF machine, full RELRO, `BIND_NOW`, a
non-executable stack and a `$ORIGIN` RUNPATH on the engine; on macOS the Mach-O
architecture and the 11.0 minimum OS of every binary; on Windows `SDL3.dll` and
`z.dll` next to the engine.

Options: `--sanitize address,undefined`, `--cross-file tools/cross/linux-arm64.ini
--arch arm64` (builds and checks, runs nothing), `--buildtype`, `--no-client`,
`--no-tests`, `--no-install`, `--no-smoke`, `--skip-game-build`, `--clean`,
`--meson-option KEY=VALUE`.

## Workflows

| Workflow | Trigger | What it does |
|---|---|---|
| `commit-validation.yml` | pull request | actionlint and shellcheck; script smoke (pyflakes, include case, Zero-SDK, integration skips, profile dry runs); the `pr` profile on Linux x64 GCC and Clang, Linux ARM64, macOS arm64, Windows x64 and ARM64; the `pr` profile under ASan+UBSan on Linux x64 Clang |
| `push-verification.yml` | push to `main` | the `push` profile on Linux x64 and ARM64, macOS arm64, Windows x64 and ARM64; publishes each staged payload as a tarball artifact (modes kept) |
| `linux-arm64-cross.yml` | pull request, push to `main` | the game and the engine cross-compiled for Linux ARM64 on an x64 runner, installed and checked |
| `macos-debug.yml` | manual | a debug build through the `push` profile, host diagnostics, an optional MoltenVK `vulkaninfo` probe; publishes the debug payload |
| `macos-sanitizer.yml` | manual | ASan+UBSan build, instrumentation check, unit suite and smoke |
| `manual-release.yml` | manual | version, packages for every platform, checks, changelog, GitHub release (a draft by default) |
| `discord-release.yml` | release published | the Discord announcement, when `DISCORD_RELEASE_WEBHOOK` is set |

Every build job first runs `.github/actions/prepare`: it fetches `openETQW-game` at
the commit pinned in `tools/ci/companion.ref`, and while the repositories are private
and no `OPENETQW_CROSS_REPO_TOKEN` secret is set, the job skips its build steps and
stays green. The repository variable `OPENETQW_COMPANION_REQUIRED=true` makes an
inaccessible companion an error. Legs marked `experimental: true` report failures
without failing the run; flip one once it has passed on `main`.

## Packages

`tools/build/package_release.py` turns the staged tree into the package users
download. Every package carries the engine, the game module, `base/vkprogs`,
`glslangValidator` next to the engine (the renderer compiles its shaders at run time;
`tools/build/build_glslang.py` builds a pinned, static one), the libraries a user's
system may lack, `licenses/` (the GPL, the ET:QW SDK EULA, the third-party notices,
and one text per bundled binary) and a README.

| Platform | Release assets | Bundled libraries |
|---|---|---|
| Windows x64, ARM64 | `openetqw-<v>-windows-<arch>.zip`, `openetqw-<v>-windows-<arch>-setup.exe` (Inno Setup, per-user install) | SDL3, zlib, the MSVC runtime |
| Linux x64, ARM64 | `openetqw-<v>-linux-<arch>.tar.xz`, `-debug.tar.xz` (split symbols), `openetqw-<v>-<x86_64\|aarch64>.AppImage` | SDL3 (the rest is the system's: Vulkan, OpenGL, zlib) |
| macOS arm64 | `openetqw-<v>-macos-arm64.dmg` when signed and notarized, else `openetqw-<v>-macos-arm64-unsigned.tar.gz` | SDL3, the Vulkan loader and MoltenVK, built or fetched from pinned sources for macOS 11.0 (`tools/build/prepare_macos_deps.py`; MoltenVK stays at 1.4.1, whose dylib runs on 11.0, where 1.4.2's needs 12.0); the packager checks that every library the app names is inside it and runs on 11.0 |

macOS is arm64 only: the owner dropped macOS x64 on 2026-09-30, and the universal2 candidate workflow and its assembler went with it. The macOS app is `openETQW.app` in the self-contained layout the engine expects
(`OpenETQWRuntimeLayout` = `self-contained-v1`): the engine's content in
`Contents/Resources/base`, the game module in `Contents/Frameworks`, MoltenVK found by
the Vulkan loader through `Contents/Resources/vulkan/icd.d`. Without the Apple
signing secrets it is signed ad hoc; macOS then asks the user to allow it on first
launch.

Every package needs the retail data: users extract the Linux or Windows package into
their ET:QW directory, or pass `+set fs_basepath <ET:QW directory>` (the AppImage finds
the data by itself when it is started from, or stored in, the ET:QW directory). The
macOS app always needs `+set fs_basepath`: it takes its content root from
`Contents/Resources`, so a `base/` next to the app is not searched. Saves, configs and the shader
cache go to the per-user directory (`~/.local/share/openetqw`,
`$env:LOCALAPPDATA\openETQW`, `~/Library/Application Support/openETQW`).

## Making a release

Run **Manual Releases** (`manual-release.yml`) from `main`:

- `bump_mode`: `auto` (from the size of the change since the last `v*` tag), `major`,
  `minor` or `patch`; or `version_override`.
- `companion_ref`: empty builds the pinned companion.
- `draft` (default **true**): the release gate in `readme.md` is not cleared, so
  releases stay unpublished drafts until the owner decides otherwise. A draft is not
  announced; publishing it later announces it.

The workflow computes the version, checks that an existing tag of that name points at
the commit being released, builds and validates every platform with the release
version stamped in (`version_track=stable`), packages, checks the exact asset set
(names, archive members, sizes and SHA-256), writes the changelog, and creates or
updates the GitHub release as a draft (a re-run turns it back into one,
retargets it to the commit and removes assets this run did not make). Only when
GitHub is shown to hold exactly those bytes does it set the pre-release flag and,
unless `draft` is set, publish.

Secrets and variables, all optional:

| Name | Effect when set |
|---|---|
| `OPENETQW_CROSS_REPO_TOKEN` | read access to the private companion (required for any build while it is private) |
| `MACOS_DEVELOPER_ID_APPLICATION_CERTIFICATE_BASE64`, `MACOS_DEVELOPER_ID_APPLICATION_CERTIFICATE_PASSWORD`, `MACOS_DEVELOPER_ID_APPLICATION_IDENTITY`, `MACOS_NOTARY_APPLE_ID`, `MACOS_NOTARY_TEAM_ID`, `MACOS_NOTARY_APP_PASSWORD` | a Developer ID signed, notarized and stapled DMG instead of the unsigned tarball (all six are needed) |
| `DISCORD_RELEASE_WEBHOOK` (secret), `DISCORD_RELEASE_MENTIONS`, `DISCORD_RELEASE_AVATAR_URL` (variables) | the Discord announcement |
