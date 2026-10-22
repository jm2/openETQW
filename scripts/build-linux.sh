#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# scripts/build-linux.sh
# OpenETQW: Linux (x64 and arm64) build helper. Run with --help for the options;
# docs/build-scripts.md describes the behaviour.
set -euo pipefail

PLATFORM_NAME="Linux"
SCRIPT_NAME="build-linux.sh"
MODULE_SUFFIX=".so"
PLATFORM_HAS_INSTALL_DEPS=true
PLATFORM_DEPS_HELP="  --install-deps    Install missing packages with sudo and the distribution's
                    package manager (dnf, apt or pacman). Without it, the
                    install command is printed.
"
# shellcheck source=lib/build-common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/build-common.sh"

# What each tool or library is called per package manager. pkg-config names are
# checked for libraries, commands for tools.
#               check                     dnf                     apt                      pacman
DEPS=(
  "cmd:c++                                gcc-c++                 g++                      gcc"
  "cmd:meson                              meson                   meson                    meson"
  "cmd:ninja                              ninja-build             ninja-build              ninja"
  "cmd:pkg-config                         pkgconf                 pkg-config               pkgconf"
  "cmd:glslangValidator                   glslang                 glslang-tools            glslang"
  "pc:sdl3                                SDL3-devel              libsdl3-dev              sdl3"
  "pc:vulkan                              vulkan-loader-devel     libvulkan-dev            vulkan-icd-loader"
  "hdr:vulkan/vulkan.h                    vulkan-headers          libvulkan-dev            vulkan-headers"
  "pc:zlib                                zlib-ng-compat-devel    zlib1g-dev               zlib"
  "pc:gl                                  mesa-libGL-devel        libgl-dev                libglvnd"
)

package_manager() {
  if command -v dnf >/dev/null; then echo dnf
  elif command -v apt-get >/dev/null; then echo apt
  elif command -v pacman >/dev/null; then echo pacman
  else echo ""; fi
}

dep_present() {
  local check="$1"
  case "$check" in
    cmd:*) command -v "${check#cmd:}" >/dev/null ;;
    pc:*)  command -v pkg-config >/dev/null && pkg-config --exists "${check#pc:}" ;;
    hdr:*) [[ -f "/usr/include/${check#hdr:}" || -f "/usr/local/include/${check#hdr:}" ]] ;;
  esac
}

platform_check_deps() {
  [[ "$(uname -s)" == Linux ]] || error "This is the Linux helper; on macOS use scripts/build-macos.sh"
  command -v python3 >/dev/null || error "python3 not found; install it with your package manager"
  local pm; pm="$(package_manager)"
  local column
  case "$pm" in dnf) column=2 ;; apt) column=3 ;; pacman) column=4 ;; *) column=0 ;; esac
  local missing=() packages=()
  for row in "${DEPS[@]}"; do
    read -r -a fields <<< "$row"
    if ! dep_present "${fields[0]}"; then
      missing+=("${fields[0]#*:}")
      [[ "$column" -gt 0 ]] && packages+=("${fields[$((column - 1))]}")
    fi
  done
  if [[ ${#missing[@]} -eq 0 ]]; then
    info "Dependencies: all present"
    return
  fi
  warn "Missing: ${missing[*]}"
  [[ "$column" -gt 0 ]] || error "No dnf, apt or pacman found; install the equivalents of the above by hand (readme, Build on Linux)"
  # several checks can map to one package
  local unique=()
  while IFS= read -r p; do unique+=("$p"); done < <(printf '%s\n' "${packages[@]}" | awk '!seen[$0]++')
  local cmd
  case "$pm" in
    dnf)    cmd=(sudo dnf install -y "${unique[@]}") ;;
    apt)    cmd=(sudo apt-get install -y "${unique[@]}") ;;
    pacman) cmd=(sudo pacman -S --needed "${unique[@]}") ;;
  esac
  if [[ "$pm" == apt ]]; then
    warn "Ubuntu 24.04 and Debian 12 have no SDL3 package; tools/ci/linux_deps.sh builds it into a private prefix."
  fi
  if "$INSTALL_DEPS"; then
    run "${cmd[@]}"
  else
    error "Install them with:  ${cmd[*]}
    (or rerun with --install-deps, which runs that command)"
  fi
}

common_main "$@"
