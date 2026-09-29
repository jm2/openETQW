# SPDX-License-Identifier: GPL-3.0-or-later

project(
  'openETQW',
  ['c', 'cpp'],
  version: '0.1.0',
  meson_version: '>=1.6.0',
  default_options: [
    'c_std=c11',
    'cpp_std=c++20',
    'b_vscrt=from_buildtype',
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

# `meson install` stages the portable tree a package ships, as openQ4 does: the
# binaries, the game module and base/vkprogs together (the engine finds its
# content and the module next to its executable), the licence texts under
# licenses/. tools/build packages it; nothing here installs into a system prefix.
install_root = get_option('install_root')
if install_root == ''
  install_root = meson.project_source_root() / '.install'
endif
cc = meson.get_compiler('c')
cpp = meson.get_compiler('cpp')

# Darwin / macOS specific flags
if host_system == 'darwin'
  add_languages('objc', 'objcpp', required: false)
  macos_deployment_target = get_option('macos_deployment_target')
  add_project_arguments(
    '-mmacosx-version-min=' + macos_deployment_target,
    language: ['c', 'cpp', 'objc', 'objcpp'],
  )
  add_project_link_arguments(
    '-mmacosx-version-min=' + macos_deployment_target,
    language: ['c', 'cpp', 'objc', 'objcpp'],
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

# minizip's decryption code (crypt.h, the password path in unzip.c) comes from
# Info-ZIP's crypt.c under the Info-ZIP license. The engine never opens encrypted
# paks, so it is compiled out and the binaries carry only zlib-licensed minizip.
# unz_s keeps its crypt fields either way (minizip/unzip_internal.h), so the C++
# files that share it need not see the define.
engine_c_args = ['-DNOUNCRYPT']

if host_system == 'darwin'
  common_engine_defs += ['-DMACOS_X']
elif host_system == 'windows'
  common_engine_defs += [
    '-DWIN32',
    '-D_WINDOWS',
    '-DNOMINMAX',
    '-DWIN32_LEAN_AND_MEAN',
    '-D_CRT_SECURE_NO_WARNINGS',
    '-D_CRT_NONSTDC_NO_WARNINGS',
  ]
endif

if cpp.get_argument_syntax() == 'msvc'
  add_project_arguments(
    '/EHsc',
    '/permissive-',
    '/Zc:__cplusplus',
    '/utf-8',
    '/bigobj',
    language: ['c', 'cpp'],
  )
else
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
    language: ['c', 'cpp', 'objc', 'objcpp'],
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
imgui_inc = include_directories('src/imgui')
qgl_inc = include_directories('src/libs/qglLib')

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
  imgui_inc,
  qgl_inc,
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

threads_dep = dependency('threads', required: true)
zlib_dep = dependency('zlib', required: true)
idlib_dep = cpp.find_library('idLib', dirs: [game_repo_root / 'build'], required: false)

engine_common_deps = [threads_dep, zlib_dep]

if host_system == 'linux'
  dl_dep = cpp.find_library('dl', required: false)
  rt_dep = cpp.find_library('rt', required: false)
  m_dep = cpp.find_library('m', required: false)
  engine_common_deps += [dl_dep, rt_dep, m_dep]
elif host_system == 'windows'
  ws2_32_dep = cpp.find_library('ws2_32', required: true)
  winmm_dep = cpp.find_library('winmm', required: true)
  dbghelp_dep = cpp.find_library('dbghelp', required: true)
  version_dep = cpp.find_library('version', required: true)
  ole32_dep = cpp.find_library('ole32', required: true)
  shell32_dep = cpp.find_library('shell32', required: true)
  # win_perfquery.cpp (PDH), win_dinput.cpp (c_dfDIJoystick2/GUID_*/SysAllocString)
  pdh_dep = cpp.find_library('pdh', required: true)
  dinput8_dep = cpp.find_library('dinput8', required: true)
  dxguid_dep = cpp.find_library('dxguid', required: true)
  oleaut32_dep = cpp.find_library('oleaut32', required: true)
  engine_common_deps += [
    ws2_32_dep,
    winmm_dep,
    dbghelp_dep,
    version_dep,
    ole32_dep,
    shell32_dep,
    pdh_dep,
    dinput8_dep,
    dxguid_dep,
    oleaut32_dep,
  ]
endif

engine_common_link_args = []
if idlib_dep.found()
  engine_common_deps += [idlib_dep]
elif fs_mod.is_file(game_repo_root / 'build' / 'libidLib.a')
  engine_common_link_args += [game_repo_root / 'build' / 'libidLib.a']
elif fs_mod.is_file(game_repo_root / 'build' / 'idLib.lib')
  engine_common_link_args += [game_repo_root / 'build' / 'idLib.lib']
else
  error('Neither libidLib.a nor idLib.lib found in ' + game_repo_root / 'build' + '. Build openETQW-game first.')
endif

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

  sdl3_dep = dependency('sdl3', version: '>=3.2.0', required: true)
  vulkan_dep = dependency('vulkan', version: '>=1.3.0', required: true)

  apple_frameworks_dep = []
  if host_system == 'darwin'
    apple_frameworks_dep = [dependency(
      'appleframeworks',
      modules: ['Cocoa', 'IOKit', 'CoreVideo', 'Metal', 'QuartzCore', 'ApplicationServices', 'OpenGL'],
      required: true,
    )]
  endif

  client_deps = engine_common_deps + [sdl3_dep, vulkan_dep] + apple_frameworks_dep
  client_link_args = engine_common_link_args
  client_cpp_args = common_engine_defs + engine_pch_args + ['-DVK_ENABLE_BETA_EXTENSIONS', '-DUSE_SDL3']
  if host_system == 'linux'
    gl_dep = dependency('gl', required: true)
    client_deps += [gl_dep]
    client_cpp_args += ['-DID_GL_HARDLINK']
  elif host_system == 'windows'
    gl_dep = cpp.find_library('opengl32', required: true)
    client_deps += [gl_dep]
    client_cpp_args += ['-DID_GL_HARDLINK']
  endif

  executable(
    engine_client_binary_name,
    openetqw_client_sources,
    include_directories: engine_include_dirs,
    c_args: engine_c_args,
    cpp_args: client_cpp_args,
    objcpp_args: client_cpp_args,
    dependencies: client_deps,
    link_args: client_link_args,
    export_dynamic: true,
    win_subsystem: host_system == 'windows' ? 'windows' : 'console',
    install: true,
    install_dir: install_root,
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

  ded_deps = engine_common_deps
  if host_system == 'darwin'
    ded_deps += [dependency(
      'appleframeworks',
      modules: ['Cocoa', 'IOKit', 'CoreVideo', 'Metal', 'QuartzCore', 'ApplicationServices', 'OpenGL'],
      required: true,
    )]
  endif
  ded_link_args = engine_common_link_args
  ded_cpp_args = common_engine_defs + ['-DID_DEDICATED'] + engine_pch_args

  executable(
    engine_ded_binary_name,
    openetqw_ded_sources,
    include_directories: engine_include_dirs,
    c_args: engine_c_args,
    cpp_args: ded_cpp_args,
    objcpp_args: ded_cpp_args,
    dependencies: ded_deps,
    link_args: ded_link_args,
    export_dynamic: true,
    win_subsystem: 'console',
    install: true,
    install_dir: install_root,
  )
endif

# Licence texts for every package (owner decision, 2026-09-25): the GPL for the
# engine; the ET:QW SDK EULA, which must accompany each copy (EULA section 5) because
# the engine links the SDK idLib and staged SDK headers (docs/provenance.md section 4);
# and the notices of the third-party code compiled in. SDL3, Vulkan and zlib are
# system dependencies; a package that bundles them adds their notices.
# tools/ci/check_install_licenses.py checks this list.
install_data('LICENSE', install_dir: install_root / 'licenses' / 'openETQW')
install_data(
  'src' / 'imgui' / 'LICENSE.txt',
  'src' / 'recast' / 'License.txt',
  'LICENSES' / 'third-party' / 'minizip.txt',
  rename: ['imgui.txt', 'recastnavigation.txt', 'minizip.txt'],
  install_dir: install_root / 'licenses' / 'openETQW' / 'third-party',
)
if game_repo_root != ''
  install_data(
    game_repo_root / 'EULA_SDK_ETQW.txt',
    game_repo_root / 'legal' / 'EULA SDK ETQW.rtf',
    install_dir: install_root / 'licenses' / 'ETQW-SDK',
  )
endif

if build_engine or build_dedicated
  # the engine's own loose content; the runtime shader cache is generated
  install_subdir(
    'base' / 'vkprogs',
    install_dir: install_root / 'base',
    exclude_directories: ['cache'],
  )
  # the game module, from the companion's build (engine and module ship together)
  game_module_suffix = '.so'
  if host_system == 'windows'
    game_module_suffix = '.dll'
  elif host_system == 'darwin'
    game_module_suffix = '.dylib'
  endif
  game_module_built = game_repo_root / 'build' / game_module_binary_name + game_module_suffix
  if game_repo_root != '' and fs_mod.is_file(game_module_built)
    install_data(game_module_built, install_dir: install_root)
  else
    warning('the game module ' + game_module_binary_name + game_module_suffix +
      ' is not built yet; `meson install` will not stage it (build the game first)')
  endif
endif

if host_system == 'windows'
  win_runtime_dll_deps = [zlib_dep]
  win_runtime_dlls = ['z.dll']
  if build_engine
    win_runtime_dll_deps += [sdl3_dep]
    win_runtime_dlls += ['SDL3.dll']
  endif
  # The DLLs sit in <prefix>/bin.  A pkg-config dependency names its libdir; one
  # found through CMake (no PKG_CONFIG set) does not, so the configured prefixes
  # are searched as well.
  runtime_dll_dirs = []
  foreach dep : win_runtime_dll_deps
    dep_libdir = dep.get_variable(pkgconfig: 'libdir', default_value: '')
    if dep_libdir != ''
      runtime_dll_dirs += [fs_mod.parent(dep_libdir) / 'bin']
    endif
  endforeach
  foreach prefix : get_option('cmake_prefix_path')
    runtime_dll_dirs += [prefix / 'bin']
  endforeach
  foreach pc_dir : get_option('pkg_config_path')
    runtime_dll_dirs += [fs_mod.parent(fs_mod.parent(pc_dir)) / 'bin']
  endforeach
  foreach dll : win_runtime_dlls
    found_dll = false
    foreach dir : runtime_dll_dirs
      if not found_dll and fs_mod.is_file(dir / dll)
        fs_mod.copyfile(dir / dll, dll)
        install_data(dir / dll, install_dir: install_root)
        found_dll = true
      endif
    endforeach
    if not found_dll
      warning(dll + ' was not found next to its dependency; copy it next to the binaries, ' +
        'or set PKG_CONFIG / -Dcmake_prefix_path to the prefix that holds it')
    endif
  endforeach
  # The engine loads the game module from its executable's directory.  POSIX
  # hosts symlink it (readme); on Windows, where a symlink needs privileges, the
  # build copies it, again whenever the game build replaces it.
  game_module_dll = game_repo_root / 'build' / game_module_binary_name + '.dll'
  if game_repo_root != '' and fs_mod.is_file(game_module_dll)
    fs_mod.copyfile(game_module_dll, game_module_binary_name + '.dll')
  endif
endif

if build_tests
  subdir('tests')
  # the dedicated server of this build directory (the scripts default to <source>/build)
  bot_test_ded = meson.project_build_root() / engine_ded_binary_name
  if host_system == 'windows'
    bot_test_ded += '.exe'
  endif
  test(
    'bot-gameplay',
    py,
    args: [meson.project_source_root() / 'tests' / 'test_bot_gameplay.py'],
    env: {'OPENETQW_DED_BINARY': bot_test_ded},
    workdir: meson.project_source_root(),
    suite: 'integration',
    timeout: 120,
  )
  test(
    'valley-match',
    py,
    args: [meson.project_source_root() / 'tests' / 'test_valley_match.py', '--log', meson.project_build_root() / 'meson-logs' / 'valley-match-server.log'],
    env: {'OPENETQW_DED_BINARY': bot_test_ded},
    workdir: meson.project_source_root(),
    suite: 'integration',
    timeout: 180,
  )
endif
