# openETQW tests/meson.build

test_include_dirs = [
  root_inc,
  src_inc,
  staged_root_inc,
  staged_inc,
  staged_game_inc,
  staged_idlib_inc,
]

idlib_dep = cpp.find_library('idLib', dirs: [game_repo_root / 'build'], required: false)
test_deps = []
test_link_args = []
if idlib_dep.found()
  test_deps += [idlib_dep]
elif fs_mod.is_file(game_repo_root / 'build' / 'libidLib.a')
  test_link_args += [game_repo_root / 'build' / 'libidLib.a']
endif

# 1. BitMsg unit test
bitmsg_test_src = files(
  'BitMsgTest.cpp',
  game_stage_root / 'src' / 'idlib' / 'BitMsg.cpp',
)

bitmsg_test = executable(
  'openetqw-bitmsg-test',
  bitmsg_test_src,
  include_directories: test_include_dirs,
  cpp_args: ['-D__IDLIB__', '-DSD_SDK_BUILD', '-DSD_PUBLIC_BUILD'],
  dependencies: test_deps,
  link_args: test_link_args,
  install: false,
)
test('bitmsg', bitmsg_test, timeout: 30)

# 2. UserCmd delta serialization & sequence test
usercmd_test_src = files(
  'UserCmdDeltaTest.cpp',
  game_stage_root / 'src' / 'idlib' / 'BitMsg.cpp',
)

usercmd_test = executable(
  'openetqw-usercmd-delta-test',
  usercmd_test_src,
  include_directories: test_include_dirs,
  cpp_args: ['-D__IDLIB__', '-DSD_SDK_BUILD', '-DSD_PUBLIC_BUILD'],
  dependencies: test_deps,
  link_args: test_link_args,
  install: false,
)
test('usercmd-delta', usercmd_test, timeout: 30)

# 3. Struct layout and static assertions test
struct_layout_test = executable(
  'openetqw-struct-layout-test',
  files('StructLayoutTest.cpp'),
  include_directories: test_include_dirs,
  cpp_args: [
    '-DSD_SDK_BUILD',
    '-DSD_PUBLIC_BUILD',
    '-DSD_USE_DRAWVERT_SIZE_32',
    '-DSD_USE_INDEX_SIZE_16',
  ],
  dependencies: test_deps,
  link_args: test_link_args,
  install: false,
)
test('struct-layout', struct_layout_test, timeout: 30)

# 4. Zero-SDK compliance test
test(
  'zero-sdk',
  py,
  args: [meson.project_source_root() / 'tests' / 'test_zero_sdk.py'],
  timeout: 30,
)
