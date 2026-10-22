#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# scripts/lib/build-common.sh
# OpenETQW: the part of the build helpers that macOS and Linux share. Sourced by
# scripts/build-macos.sh and scripts/build-linux.sh, which set the platform's
# dependency check (platform_check_deps) and module suffix before calling
# common_main. docs/build-scripts.md describes the behaviour.

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
if [[ ! -t 1 ]]; then RED=''; GREEN=''; YELLOW=''; NC=''; fi
info()  { echo -e "${GREEN}[openetqw]${NC} $*"; }
warn()  { echo -e "${YELLOW}[openetqw]${NC} $*" >&2; }
error() { echo -e "${RED}[openetqw]${NC} $*" >&2; exit 1; }

ENGINE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
GAME_URL="https://github.com/jm2/openETQW-game.git"

common_usage() {
  cat <<EOF
OpenETQW: ${PLATFORM_NAME} build helper.

Usage:
  ./scripts/${SCRIPT_NAME} [options]

With no options: checks the build dependencies, finds the game repository
(\$OPENETQW_GAME_REPO, else ../openETQW-game, cloned at the engine's pinned
commit if missing), builds the game module and idLib, then the engine's client,
dedicated server and unit tests, all with one build type, and puts the game
module next to the binaries. An existing build directory is reconfigured, so
changed game headers are always staged again.

Build:
  --debug           Build type debug (default: debugoptimized).
  --release         Build type release.
  --clean           Wipe both build directories first.
  --build-dir DIR   Engine build directory (default: build). The game always
                    builds in its repository's build/, where the engine looks.
  --sync-game       Check the game repository out at the engine's pinned commit
                    (tools/ci/companion.ref) first. Refuses if it has changes.
  --no-deps         Skip the dependency check.
${PLATFORM_DEPS_HELP}
After the build (choose at most one):
  --test            Run the asset-free unit suite.
  --run             Start the client (main menu; with --map, a devmap session).
  --server          Start a dedicated server on --map (default valley).
  --map NAME        The map for --run or --server.

Instead of a build:
  --validate        Run tools/validation/openetqw_validate.py pr, the merge
                    gate's local check (its own build directories).

Other:
  --dry-run         Print the commands without running them.
  -h, --help        Show this help and exit.

Retail data: --run and --server use \$ETQW_DATA_PATH when it is set (the
install root whose base/ holds pak000.pk4); otherwise the engine looks for it
itself (readme, Install and packages).
EOF
}

usage_error() {
  echo "$*" >&2
  common_usage >&2
  exit 2
}

# Print a command, then run it unless this is a dry run.
run() {
  echo "+ $*"
  if ! "$DRY_RUN"; then "$@"; fi
}

common_parse_args() {
  BUILDTYPE=debugoptimized; BUILDTYPE_SET=false
  CLEAN=false; SYNC_GAME=false; NO_DEPS=false; INSTALL_DEPS=false
  TEST=false; RUN=false; SERVER=false; VALIDATE=false; DRY_RUN=false
  MAP=""; BUILD_DIR="build"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -h|--help)     common_usage; exit 0 ;;
      --debug|--release)
        "$BUILDTYPE_SET" && usage_error "Choose at most one of --debug and --release"
        BUILDTYPE="${1#--}"; BUILDTYPE_SET=true ;;
      --clean)       CLEAN=true ;;
      --sync-game)   SYNC_GAME=true ;;
      --no-deps)     NO_DEPS=true ;;
      --install-deps)
        "$PLATFORM_HAS_INSTALL_DEPS" || usage_error "Unknown option: $1"
        INSTALL_DEPS=true ;;
      --test)        TEST=true ;;
      --run)         RUN=true ;;
      --server)      SERVER=true ;;
      --validate)    VALIDATE=true ;;
      --dry-run)     DRY_RUN=true ;;
      --map)
        [[ $# -ge 2 && -n "$2" && "$2" != -* ]] || usage_error "--map needs a map name"
        MAP="$2"; shift ;;
      --build-dir)
        [[ $# -ge 2 && -n "$2" && "$2" != -* ]] || usage_error "--build-dir needs a directory"
        BUILD_DIR="$2"; shift ;;
      *)             usage_error "Unknown option: $1" ;;
    esac
    shift
  done

  local after=0
  for selected in "$TEST" "$RUN" "$SERVER"; do
    if "$selected"; then after=$((after + 1)); fi
  done
  [[ "$after" -le 1 ]] || usage_error "Choose at most one of --test, --run and --server"
  if [[ -n "$MAP" ]] && ! "$RUN" && ! "$SERVER"; then
    usage_error "--map needs --run or --server"
  fi
  if "$VALIDATE" && { [[ "$after" -gt 0 ]] || "$CLEAN" || "$BUILDTYPE_SET" || "$SYNC_GAME" || [[ "$BUILD_DIR" != build ]]; }; then
    usage_error "--validate cannot be combined with build or launch options"
  fi
  if "$NO_DEPS" && "$INSTALL_DEPS"; then
    usage_error "--no-deps and --install-deps cannot be combined"
  fi
  case "$BUILD_DIR" in /*) ;; *) BUILD_DIR="$ENGINE_ROOT/$BUILD_DIR" ;; esac
}

host_arch_tag() {
  case "$(uname -m)" in
    arm64|aarch64) echo arm64 ;;
    x86_64|amd64)  echo x64 ;;
    *) error "Unsupported architecture: $(uname -m)" ;;
  esac
}

pinned_ref() {
  local ref
  ref="$(tr -d '[:space:]' < "$ENGINE_ROOT/tools/ci/companion.ref")"
  [[ "$ref" =~ ^[0-9a-f]{40}$ ]] || error "tools/ci/companion.ref does not hold a commit ID"
  echo "$ref"
}

# Find (or clone) the game repository and check it against the pin.
resolve_game_repo() {
  local pin; pin="$(pinned_ref)"
  if [[ -n "${OPENETQW_GAME_REPO:-}" ]]; then
    GAME_REPO="$OPENETQW_GAME_REPO"
  else
    GAME_REPO="$ENGINE_ROOT/../openETQW-game"
  fi
  if [[ ! -e "$GAME_REPO" ]]; then
    [[ -z "${OPENETQW_GAME_REPO:-}" ]] || error "OPENETQW_GAME_REPO is set to $GAME_REPO, which does not exist"
    info "The game repository is not at $GAME_REPO; cloning it at the pinned commit ${pin:0:12}"
    run git clone "$GAME_URL" "$GAME_REPO"
    run git -C "$GAME_REPO" checkout --detach "$pin"
    "$DRY_RUN" || GAME_REPO="$(cd "$GAME_REPO" && pwd)"
    return
  fi
  GAME_REPO="$(cd "$GAME_REPO" && pwd)"
  [[ -f "$GAME_REPO/meson.build" ]] || error "$GAME_REPO is not an openETQW-game checkout (no meson.build)"
  git -C "$GAME_REPO" rev-parse --git-dir >/dev/null 2>&1 || { warn "$GAME_REPO is not a git checkout; not checking it against the pin"; return; }
  local head; head="$(git -C "$GAME_REPO" rev-parse HEAD)"
  if [[ "$head" == "$pin" ]]; then
    info "Game repository: $GAME_REPO at the pinned commit ${pin:0:12}"
    return
  fi
  if "$SYNC_GAME"; then
    [[ -z "$(git -C "$GAME_REPO" status --porcelain --untracked-files=no)" ]] \
      || error "--sync-game: $GAME_REPO has uncommitted changes; commit or stash them first"
    if ! git -C "$GAME_REPO" cat-file -e "${pin}^{commit}" 2>/dev/null; then
      run git -C "$GAME_REPO" fetch origin
    fi
    run git -C "$GAME_REPO" checkout --detach "$pin"
  else
    warn "Game repository $GAME_REPO is at ${head:0:12}, not the engine's pinned ${pin:0:12}."
    warn "Building anyway; --sync-game checks out the pinned commit."
  fi
}

# Configure (or reconfigure) a meson build directory, then compile it.
meson_build() {
  local source="$1" build="$2"; shift 2
  if "$CLEAN" && [[ -d "$build" ]]; then
    run rm -rf "$build"
  fi
  if ! "$CLEAN" && [[ -f "$build/meson-private/coredata.dat" ]]; then
    run meson setup --reconfigure "$build" "$source" "--buildtype=$BUILDTYPE" "$@"
  else
    run meson setup "$build" "$source" "--buildtype=$BUILDTYPE" "$@"
  fi
  run meson compile -C "$build"
}

engine_bin() { echo "$BUILD_DIR/openETQW-$1_$ARCH"; }

data_args() {
  if [[ -n "${ETQW_DATA_PATH:-}" ]]; then
    printf '%s\n' +set fs_basepath "$ETQW_DATA_PATH"
  fi
}

common_main() {
  common_parse_args "$@"
  ARCH="$(host_arch_tag)"

  if "$VALIDATE"; then
    "$NO_DEPS" || platform_check_deps
    # the validator builds whatever checkout it is given; check it against the pin as a build does
    resolve_game_repo
    cd "$ENGINE_ROOT" || exit 1
    run python3 tools/validation/openetqw_validate.py pr --game-repo "$GAME_REPO"
    exit 0
  fi

  "$NO_DEPS" || platform_check_deps
  resolve_game_repo

  info "Building the game module and idLib ($BUILDTYPE)"
  meson_build "$GAME_REPO" "$GAME_REPO/build"

  info "Building the engine ($BUILDTYPE)"
  # the engine finds the game through OPENETQW_GAME_REPO when it is not ../openETQW-game
  export OPENETQW_GAME_REPO="$GAME_REPO"
  meson_build "$ENGINE_ROOT" "$BUILD_DIR" -Dbuild_engine=true -Dbuild_dedicated=true -Dbuild_tests=true

  local module="game_${ARCH}${MODULE_SUFFIX}"
  # The engine loads the game module from its executable's directory.
  run ln -sfn "$GAME_REPO/build/$module" "$BUILD_DIR/$module"

  info "Built:"
  info "  client           $(engine_bin client)"
  info "  dedicated server $(engine_bin ded)"
  info "  game module      $BUILD_DIR/$module -> $GAME_REPO/build/$module"

  local data=()
  while IFS= read -r line; do data+=("$line"); done < <(data_args)
  if "$TEST"; then
    run meson test -C "$BUILD_DIR" --suite unit --print-errorlogs
  elif "$RUN"; then
    cd "$ENGINE_ROOT" || exit 1
    if [[ -n "$MAP" ]]; then
      run "$(engine_bin client)" ${data[@]+"${data[@]}"} +set fs_game base +set si_rules sdGameRulesObjective +devmap "$MAP"
    else
      run "$(engine_bin client)" ${data[@]+"${data[@]}"}
    fi
  elif "$SERVER"; then
    cd "$ENGINE_ROOT" || exit 1
    run "$(engine_bin ded)" +set dedicated 1 ${data[@]+"${data[@]}"} +set fs_game base \
      +set si_rules sdGameRulesObjective +set si_pure 0 +spawnServer "${MAP:-valley}"
  fi
}
