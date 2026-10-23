#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# scripts/build-macos.sh
# OpenETQW: macOS (arm64) build helper. Run with --help for the options;
# docs/build-scripts.md describes the behaviour.
set -euo pipefail

PLATFORM_NAME="macOS"
SCRIPT_NAME="build-macos.sh"
MODULE_SUFFIX=".dylib"
PLATFORM_HAS_INSTALL_DEPS=false
PLATFORM_DEPS_HELP="                    (Missing Homebrew packages are installed.)
"
# shellcheck source=lib/build-common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/build-common.sh"

# Homebrew formulae: the build tools, the client's libraries, and glslang, whose
# glslangValidator the client runs to compile its Vulkan shaders.
BREW_FORMULAE=(meson ninja pkgconf sdl3 vulkan-headers vulkan-loader molten-vk glslang)

platform_check_deps() {
  [[ "$(uname -s)" == Darwin ]] || error "This is the macOS helper; on Linux use scripts/build-linux.sh"
  [[ "$(uname -m)" == arm64 ]] || error "OpenETQW supports macOS on arm64 only (macOS x64 was dropped on 2026-09-30)"
  xcode-select -p >/dev/null 2>&1 || error "The Xcode command line tools are missing. Install: xcode-select --install"
  command -v python3 >/dev/null || error "python3 not found. Install: brew install python"
  command -v brew >/dev/null || error "Homebrew not found. Install it from https://brew.sh, then run this again"
  local missing=()
  for formula in "${BREW_FORMULAE[@]}"; do
    brew list --formula "$formula" >/dev/null 2>&1 || missing+=("$formula")
  done
  if [[ ${#missing[@]} -gt 0 ]]; then
    warn "Installing missing Homebrew packages: ${missing[*]}"
    run brew install "${missing[@]}"
  else
    info "Dependencies: all Homebrew packages present"
  fi
}

common_main "$@"
