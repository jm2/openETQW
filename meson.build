# SPDX-License-Identifier: GPL-3.0-or-later

project(
  'openETQW',
  ['c', 'cpp'],
  version: '0.1.0',
  meson_version: '>=1.6.0',
  default_options: [
    'c_std=c11',
    'cpp_std=c++20',
    'b_vscrt=static_from_buildtype',
  ],
)

host_system = host_machine.system()
host_cpu_family = host_machine.cpu_family()

if not ['windows', 'linux', 'darwin'].contains(host_system)
  error('Unsupported host system for openETQW Meson build: ' + host_system)
endif

binary_arch = host_cpu_family
if host_cpu_family in ['x86_64', 'x64']
  binary_arch = 'x64'
elif host_cpu_family == 'x86'
  binary_arch = 'x86'
elif host_cpu_family == 'aarch64'
  binary_arch = 'arm64'
endif

engine_client_binary_name = 'openETQW-client_' + binary_arch
engine_ded_binary_name = 'openETQW-ded_' + binary_arch
game_module_binary_name = 'game_' + binary_arch

py = find_program('python3', 'python', native: true, required: true)
fs_mod = import('fs')
cc = meson.get_compiler('c')
cpp = meson.get_compiler('cpp')

# Darwin / macOS specific flags
if host_system == 'darwin'
  add_languages('objc', 'objcpp', required: false)
  macos_deployment_target = get_option('macos_deployment_target')
  add_project_arguments(
    '-mmacosx-version-min=' + macos_deployment_target,
    language: ['c', 'cpp'],
  )
  add_project_link_arguments(
    '-mmacosx-version-min=' + macos_deployment_target,
    language: ['c', 'cpp'],
  )
endif

# Floating-point contraction determinism (-ffp-contract=off)
foreach fp_arg : ['-ffp-contract=off']
  if cpp.has_argument(fp_arg) and cc.has_argument(fp_arg)
    add_project_arguments(fp_arg, language: ['c', 'cpp'])
    add_project_link_arguments(fp_arg, language: ['c', 'cpp'])
  endif
endforeach

common_engine_defs = [
  '-DSD_SDK_BUILD',
  '-DSD_PUBLIC_BUILD',
  '-DSD_USE_DRAWVERT_SIZE_32',
  '-DSD_USE_INDEX_SIZE_16',
  '-DETQW_ENGINE_RECONSTRUCTION',
  '-DID_ENABLE_CURL=0',
]

if host_system == 'darwin'
  common_engine_defs += ['-DMACOS_X']
endif

if cpp.get_argument_syntax() != 'msvc'
  add_project_arguments(
    '-fno-strict-aliasing',
    '-Wno-unused-parameter',
    '-Wno-sign-compare',
    '-Wno-invalid-offsetof',
    '-Wno-register',
    '-Wno-reorder-ctor',
    '-Wno-switch',
    '-Wno-deprecated-non-prototype',
    '-Wno-parentheses-equality',
    language: ['c', 'cpp'],
  )
endif

engine_pch_args = []
if cpp.get_argument_syntax() == 'msvc'
  engine_pch_args += ['/FI' + (meson.project_source_root() / 'src' / 'framework' / 'precompiled.h')]
else
  engine_pch_args += ['-include', meson.project_source_root() / 'src' / 'framework' / 'precompiled.h']
endif

# Companion game repo discovery and dynamic staging
build_engine = get_option('build_engine')
build_dedicated = get_option('build_dedicated')
build_games = get_option('build_games')
build_tests = get_option('build_tests')

game_repo_root = ''
game_stage_root = ''
game_stage_game_root = ''

if build_engine or build_dedicated or build_games or build_tests
  game_repo_discovery = run_command(
    py,
    '-c',
    'import os, pathlib, sys; root = pathlib.Path(sys.argv[1]); raw = os.environ.get("OPENETQW_GAME_REPO", "").strip(); repo = pathlib.Path(raw) if raw else (root / ".." / "openETQW-game"); print(repo.resolve().as_posix())',
    meson.project_source_root(),
    check: true,
  )
  game_repo_root = game_repo_discovery.stdout().strip()
  if game_repo_root == '' or not fs_mod.is_dir(game_repo_root)
    error('Failed to resolve openETQW-game repository at: ' + game_repo_root + '. Set OPENETQW_GAME_REPO or clone to ../openETQW-game.')
  endif

  game_stage_result = run_command(
    py,
    meson.project_source_root() / 'tools' / 'build' / 'stage_game.py',
    meson.project_source_root(),
    game_repo_root,
    meson.project_source_root() / '.tmp' / 'gamelibs_stage',
    check: true,
  )
  game_stage_root = game_stage_result.stdout().strip()
  if game_stage_root == ''
    error('Failed to stage game headers from openETQW-game.')
  endif

  game_stage_manifest = game_stage_root / 'openetqw_gamelibs_stage_manifest.json'
  if not fs_mod.is_file(game_stage_manifest)
    error('Staged openETQW game manifest not found: ' + game_stage_manifest)
  endif

  game_stage_game_root = game_stage_root / 'src' / 'game'
  if not fs_mod.is_dir(game_stage_game_root)
    error('Staged game source directory not found: ' + game_stage_game_root)
  endif
endif

root_inc = include_directories('.')
src_inc = include_directories('src')
framework_inc = include_directories('src/framework')
async_inc = include_directories('src/framework/async')
cm_inc = include_directories('src/cm')
decllib_inc = include_directories('src/decllib')
bse_inc = include_directories('src/bse')
sys_inc = include_directories('src/sys')
renderer_inc = include_directories('src/renderer')
sound_inc = include_directories('src/sound')
navigation_inc = include_directories('src/navigation')
detour_inc = include_directories('src/recast/Detour/Include')
recast_inc = include_directories('src/recast/Recast/Include')
sdnet_inc = include_directories('src/sdnet')

staged_root_inc = include_directories('.tmp/gamelibs_stage')
staged_inc = include_directories('.tmp/gamelibs_stage/src')
staged_framework_inc = include_directories('.tmp/gamelibs_stage/src/framework')
staged_async_inc = include_directories('.tmp/gamelibs_stage/src/framework/async')
staged_cm_inc = include_directories('.tmp/gamelibs_stage/src/cm')
staged_decllib_inc = include_directories('.tmp/gamelibs_stage/src/decllib')
staged_bse_inc = include_directories('.tmp/gamelibs_stage/src/bse')
staged_sys_inc = include_directories('.tmp/gamelibs_stage/src/sys')
staged_renderer_inc = include_directories('.tmp/gamelibs_stage/src/renderer')
staged_sound_inc = include_directories('.tmp/gamelibs_stage/src/sound')
staged_sdnet_inc = include_directories('.tmp/gamelibs_stage/src/sdnet')
staged_game_inc = include_directories('.tmp/gamelibs_stage/src/game')
staged_idlib_inc = include_directories('.tmp/gamelibs_stage/src/idlib')

engine_include_dirs = [
  root_inc,
  src_inc,
  framework_inc,
  async_inc,
  cm_inc,
  decllib_inc,
  bse_inc,
  sys_inc,
  renderer_inc,
  sound_inc,
  navigation_inc,
  detour_inc,
  recast_inc,
  sdnet_inc,
  staged_root_inc,
  staged_inc,
  staged_framework_inc,
  staged_async_inc,
  staged_cm_inc,
  staged_decllib_inc,
  staged_bse_inc,
  staged_sys_inc,
  staged_renderer_inc,
  staged_sound_inc,
  staged_sdnet_inc,
  staged_game_inc,
  staged_idlib_inc,
]

if build_engine
  engine_source_discovery = run_command(
    py,
    meson.project_source_root() / 'tools' / 'build' / 'meson_sources.py',
    '--host-system', host_system,
    '--platform-backend', get_option('platform_backend'),
    '--target-kind', 'client',
    check: true,
  )
  engine_source_paths = engine_source_discovery.stdout().strip().split('\n')
  openetqw_client_sources = files(engine_source_paths)

  executable(
    engine_client_binary_name,
    openetqw_client_sources,
    include_directories: engine_include_dirs,
    cpp_args: common_engine_defs,
    install: true,
  )

endif

if build_dedicated
  ded_source_discovery = run_command(
    py,
    meson.project_source_root() / 'tools' / 'build' / 'meson_sources.py',
    '--host-system', host_system,
    '--platform-backend', get_option('platform_backend'),
    '--target-kind', 'dedicated',
    check: true,
  )
  ded_source_paths = ded_source_discovery.stdout().strip().split('\n')
  openetqw_ded_sources = files(ded_source_paths)

  threads_dep = dependency('threads', required: true)
  zlib_dep = dependency('zlib', required: true)
  idlib_dep = cpp.find_library('idLib', dirs: [game_repo_root / 'build'], required: false)

  ded_deps = [threads_dep, zlib_dep]
  ded_link_args = []
  if idlib_dep.found()
    ded_deps += [idlib_dep]
  elif fs_mod.is_file(game_repo_root / 'build' / 'libidLib.a')
    ded_link_args += [game_repo_root / 'build' / 'libidLib.a']
  else
    error('libidLib.a not found in ' + game_repo_root / 'build' + '. Build openETQW-game first.')
  endif

  executable(
    engine_ded_binary_name,
    openetqw_ded_sources,
    include_directories: engine_include_dirs,
    cpp_args: common_engine_defs + ['-DID_DEDICATED'] + engine_pch_args,
    dependencies: ded_deps,
    link_args: ded_link_args,
    install: true,
  )
endif

if build_tests
  subdir('tests')
endif
