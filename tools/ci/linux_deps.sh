#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Linux build dependencies for .github/workflows/ci.yml (jobs 'build', Linux
# legs, and 'sanitizer'). Installs the distribution packages the engine and
# SDL3 need, then builds the pinned SDL3 release into a private prefix,
# because Ubuntu 24.04 ships no SDL3 packages. The prefix is exported through
# $GITHUB_ENV (PKG_CONFIG_PATH, LD_LIBRARY_PATH) when that is set, or printed
# as shell exports otherwise.
#
# Usage: bash tools/ci/linux_deps.sh [SDL3_TAG]
#   SDL3_TAG      git tag of libsdl-org/SDL (default $SDL3_TAG or release-3.4.16)
#   SDL3_PREFIX   install prefix (default $RUNNER_TEMP/sdl3)
#   CC / CXX      compiler used for SDL3 as well; clang is installed on demand

set -euo pipefail

sdl_tag="${1:-${SDL3_TAG:-release-3.4.16}}"
prefix="${SDL3_PREFIX:-${RUNNER_TEMP:-/tmp}/sdl3}"

packages=(
  build-essential
  cmake
  ninja-build
  pkg-config
  zlib1g-dev
  libvulkan-dev
  libasound2-dev
  libdbus-1-dev
  libdecor-0-dev
  libegl1-mesa-dev
  libgl1-mesa-dev
  libpulse-dev
  libudev-dev
  libwayland-dev
  libx11-dev
  libxcursor-dev
  libxext-dev
  libxfixes-dev
  libxi-dev
  libxkbcommon-dev
  libxrandr-dev
  libxss-dev
  libxtst-dev
  wayland-protocols
)
if [[ "${CC:-}" == clang* || "${CXX:-}" == clang* ]]; then
  packages+=(clang)
fi

for attempt in 1 2 3; do
  if sudo apt-get update && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${packages[@]}"; then
    break
  fi
  if [[ "${attempt}" -eq 3 ]]; then
    echo "linux_deps: package installation failed after ${attempt} attempts" >&2
    exit 1
  fi
  echo "linux_deps: package installation attempt ${attempt} failed; retrying in 15 seconds"
  sleep 15
done

src="$(mktemp -d "${RUNNER_TEMP:-/tmp}/sdl3-src.XXXXXX")"
for attempt in 1 2 3; do
  if git clone --quiet --depth 1 --branch "${sdl_tag}" https://github.com/libsdl-org/SDL.git "${src}/SDL"; then
    break
  fi
  if [[ "${attempt}" -eq 3 ]]; then
    echo "linux_deps: cloning SDL ${sdl_tag} failed after ${attempt} attempts" >&2
    exit 1
  fi
  rm -rf "${src}/SDL"
  sleep 15
done
echo "linux_deps: SDL ${sdl_tag} at $(git -C "${src}/SDL" rev-parse HEAD)"

cmake -S "${src}/SDL" -B "${src}/build" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="${prefix}" \
  -DCMAKE_INSTALL_LIBDIR=lib \
  -DSDL_SHARED=ON \
  -DSDL_STATIC=OFF \
  -DSDL_TESTS=OFF \
  -DSDL_EXAMPLES=OFF
cmake --build "${src}/build"
cmake --install "${src}/build"
rm -rf "${src}"

pkg_config_path="${prefix}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
ld_library_path="${prefix}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
PKG_CONFIG_PATH="${pkg_config_path}" pkg-config --modversion sdl3

if [[ -n "${GITHUB_ENV:-}" ]]; then
  {
    echo "PKG_CONFIG_PATH=${pkg_config_path}"
    echo "LD_LIBRARY_PATH=${ld_library_path}"
  } >> "${GITHUB_ENV}"
else
  echo "export PKG_CONFIG_PATH='${pkg_config_path}'"
  echo "export LD_LIBRARY_PATH='${ld_library_path}'"
fi
