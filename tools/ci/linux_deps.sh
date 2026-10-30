#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Linux build dependencies for the workflows (.github/actions/prepare, and
# linux-arm64-cross.yml). Installs the distribution packages the engine and
# SDL3 need, then builds the pinned SDL3 release into a private prefix,
# because Ubuntu 24.04 ships no SDL3 packages. The prefix is exported through
# $GITHUB_ENV (PKG_CONFIG_PATH, LD_LIBRARY_PATH) when that is set, or printed
# as shell exports otherwise.
#
# Usage: bash tools/ci/linux_deps.sh [SDL3_TAG [TARGET_ARCH]]
#   SDL3_TAG      git tag of libsdl-org/SDL (default $SDL3_TAG or release-3.4.16)
#   TARGET_ARCH   native (default) or arm64: cross-compile for Linux ARM64 from an
#                 x86_64 Ubuntu host (tools/cross/linux-arm64.ini). Enables the
#                 arm64 multiarch archive, installs the aarch64 GCC toolchain and the
#                 arm64 -dev packages, and cross-builds SDL3; the prefix is exported
#                 as SDL3_PKG_CONFIG_DIR for Meson's -Dpkg_config_path.
#   SDL3_PREFIX   install prefix (default $RUNNER_TEMP/sdl3)
#   CC / CXX      compiler used for SDL3 as well; clang is installed on demand

set -euo pipefail

sdl_tag="${1:-${SDL3_TAG:-release-3.4.16}}"
target_arch="${2:-native}"
prefix="${SDL3_PREFIX:-${RUNNER_TEMP:-/tmp}/sdl3}"
case "${target_arch}" in
  native) dev_suffix="" ;;
  arm64) dev_suffix=":arm64" ;;
  *) echo "linux_deps: unsupported target architecture ${target_arch}" >&2; exit 2 ;;
esac

# tools that run on the build machine
host_packages=(
  build-essential
  cmake
  ninja-build
  pkg-config
  file
  wayland-protocols  # architecture-independent protocol XML
  libwayland-bin     # wayland-scanner, run on the build machine
)
# libraries for the target machine
packages=(
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
)
packages=("${packages[@]/%/${dev_suffix}}")
if [[ "${CC:-}" == clang* || "${CXX:-}" == clang* ]]; then
  host_packages+=(clang)
fi

if [[ "${target_arch}" == arm64 ]]; then
  # Ubuntu serves arm64 from ports.ubuntu.com, not the amd64 mirrors
  test "$(dpkg --print-architecture)" = "amd64"
  sudo dpkg --add-architecture arm64
  ubuntu_sources=/etc/apt/sources.list.d/ubuntu.sources
  sudo sed -i '/^Architectures:/d; /^Signed-By:/a Architectures: amd64' "${ubuntu_sources}"
  # Take amd64 from Ubuntu's own archive too, not the runner's mirror list: a
  # Multi-Arch: foreign -bin package (libglib2.0-dev-bin) must match its arm64 -dev
  # package's version exactly, and the mirror can lag ports.ubuntu.com (#360).
  sudo sed -i 's|^URIs: mirror+file:/etc/apt/apt-mirrors.txt|URIs: http://archive.ubuntu.com/ubuntu/|' "${ubuntu_sources}"
  sudo tee /etc/apt/sources.list.d/ubuntu-arm64.sources >/dev/null <<'SOURCES'
Types: deb
URIs: http://ports.ubuntu.com/ubuntu-ports
Suites: noble noble-updates noble-backports noble-security
Components: main universe restricted multiverse
Architectures: arm64
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
SOURCES
  host_packages+=(gcc-aarch64-linux-gnu g++-aarch64-linux-gnu binutils-aarch64-linux-gnu)
fi

for attempt in 1 2 3; do
  if sudo apt-get update && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    "${host_packages[@]}" "${packages[@]}"; then
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

cross_args=()
if [[ "${target_arch}" == arm64 ]]; then
  cross_args=(
    -DCMAKE_SYSTEM_NAME=Linux
    -DCMAKE_SYSTEM_PROCESSOR=aarch64
    -DCMAKE_C_COMPILER=aarch64-linux-gnu-gcc
    -DCMAKE_CXX_COMPILER=aarch64-linux-gnu-g++
    -DCMAKE_FIND_ROOT_PATH=/usr/aarch64-linux-gnu
    -DCMAKE_FIND_ROOT_PATH_MODE_PROGRAM=NEVER
    -DCMAKE_LIBRARY_PATH=/usr/lib/aarch64-linux-gnu
  )
  export PKG_CONFIG_LIBDIR=/usr/lib/aarch64-linux-gnu/pkgconfig:/usr/share/pkgconfig
fi
cmake -S "${src}/SDL" -B "${src}/build" -G Ninja ${cross_args[@]+"${cross_args[@]}"} \
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

if [[ "${target_arch}" == arm64 ]]; then
  # the cross build finds SDL3 through -Dpkg_config_path; nothing here runs it
  file -L "${prefix}/lib/libSDL3.so"
  if [[ -n "${GITHUB_ENV:-}" ]]; then
    echo "SDL3_PKG_CONFIG_DIR=${prefix}/lib/pkgconfig" >> "${GITHUB_ENV}"
  else
    echo "export SDL3_PKG_CONFIG_DIR='${prefix}/lib/pkgconfig'"
  fi
  exit 0
fi

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
