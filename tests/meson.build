# SPDX-License-Identifier: GPL-3.0-or-later
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

if host_system == 'linux'
  test_deps += [cpp.find_library('m', required: false)]
elif host_system == 'windows'
  test_deps += [
    cpp.find_library('ws2_32', required: true),
    cpp.find_library('winmm', required: true),
  ]
endif

if idlib_dep.found()
  test_deps += [idlib_dep]
elif fs_mod.is_file(game_repo_root / 'build' / 'libidLib.a')
  test_link_args += [game_repo_root / 'build' / 'libidLib.a']
elif fs_mod.is_file(game_repo_root / 'build' / 'idLib.lib')
  test_link_args += [game_repo_root / 'build' / 'idLib.lib']
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
test('bitmsg', bitmsg_test, suite: 'unit', timeout: 30)

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
test('usercmd-delta', usercmd_test, suite: 'unit', timeout: 30)

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
test('struct-layout', struct_layout_test, suite: 'unit', timeout: 30)

# 4. UserCmd history, ring buffer & transport lifecycle test
usercmd_history_test_src = files(
  'test_usercmd_history.cpp',
  game_stage_root / 'src' / 'idlib' / 'BitMsg.cpp',
)

usercmd_history_test = executable(
  'openetqw-usercmd-history-test',
  usercmd_history_test_src,
  include_directories: test_include_dirs,
  cpp_args: ['-D__IDLIB__', '-DSD_SDK_BUILD', '-DSD_PUBLIC_BUILD'],
  dependencies: test_deps,
  link_args: test_link_args,
  install: false,
)
test('usercmd-history', usercmd_history_test, suite: 'unit', timeout: 30)

# 5. Zero-SDK compliance test
test(
  'zero-sdk',
  py,
  args: [meson.project_source_root() / 'tests' / 'test_zero_sdk.py'],
  suite: 'unit',
  timeout: 30,
)

# 6. Client lifecycle & graphics verification test
if build_engine
  campaign_client = meson.project_build_root() / engine_client_binary_name
  if host_system == 'windows'
    campaign_client += '.exe'
  endif
  test(
    'client-lifecycle',
    py,
    args: [meson.project_source_root() / 'tests' / 'test_client_lifecycle.py'],
    workdir: meson.project_source_root(),
    suite: 'integration',
    timeout: 120,
  )
  test(
    'high-refresh-determinism',
    py,
    args: [meson.project_source_root() / 'tests' / 'test_high_refresh_determinism.py'],
    workdir: meson.project_source_root(),
    suite: 'integration',
    timeout: 180,
  )
  test(
    'campaign-client-maps',
    py,
    args: [
      meson.project_source_root() / 'tests' / 'test_campaign_client_maps.py',
      '--client-binary', campaign_client,
      '--log-dir', meson.project_build_root() / 'meson-logs' / 'campaign-client-maps',
      '--json', meson.project_build_root() / 'meson-logs' / 'campaign-client-maps.json',
    ],
    workdir: meson.project_source_root(),
    suite: ['client', 'integration', 'stock-maps'],
    protocol: 'exitcode',
    timeout: 900,
    is_parallel: false,
  )
  test(
    'vulkan-shadows',
    py,
    args: [meson.project_source_root() / 'tests' / 'test_vulkan_shadows.py'],
    workdir: meson.project_source_root(),
    suite: ['client', 'integration'],
    timeout: 120,
  )
endif


# 7. All-stock-maps verification (stock-maps phase): one fresh dedicated server
#    per stock map; load, entities, map scripts, AAS/nav, bot traversal and
#    bot think rate. Retail data comes only from ETQW_DATA_PATH, which meson
#    passes through from the calling environment (unset or incomplete => the
#    script exits 77 = SKIP). The binary is the one this build dir produces.
if build_dedicated
  all_stock_maps_ded = meson.project_build_root() / engine_ded_binary_name
  if host_system == 'windows'
    all_stock_maps_ded += '.exe'
  endif
  test(
    'all-stock-maps',
    py,
    args: [
      meson.project_source_root() / 'tests' / 'test_all_stock_maps.py',
      '--ded-binary', all_stock_maps_ded,
      '--jobs', '1',
      '--log-dir', meson.project_build_root() / 'meson-logs' / 'all-stock-maps',
      '--json', meson.project_build_root() / 'meson-logs' / 'all-stock-maps.json',
    ],
    workdir: meson.project_source_root(),
    suite: ['integration', 'stock-maps'],
    protocol: 'exitcode',
    timeout: 1800,
    is_parallel: false,
  )
  test(
    'protocol-clean-break',
    py,
    args: [
      meson.project_source_root() / 'tests' / 'test_protocol_clean_break.py',
      '--ded-binary', all_stock_maps_ded,
    ],
    workdir: meson.project_source_root(),
    suite: ['integration'],
    protocol: 'exitcode',
    timeout: 300,
    is_parallel: false,
  )
  test(
    'inprocess-map-change',
    py,
    args: [
      meson.project_source_root() / 'tests' / 'test_inprocess_map_change.py',
      '--ded-binary', all_stock_maps_ded,
      '--log', meson.project_build_root() / 'meson-logs' / 'inprocess-map-change.log',
    ],
    workdir: meson.project_source_root(),
    suite: ['integration', 'stock-maps'],
    protocol: 'exitcode',
    timeout: 900,
    is_parallel: false,
  )
  test(
    'remote-client-map-change',
    py,
    args: [
      meson.project_source_root() / 'tests' / 'test_remote_client_map_change.py',
      '--ded-binary', all_stock_maps_ded,
      '--base-check',
      '--log-dir', meson.project_build_root() / 'meson-logs' / 'remote-client-map-change',
    ],
    workdir: meson.project_source_root(),
    suite: ['integration', 'stock-maps'],
    protocol: 'exitcode',
    timeout: 600,
    is_parallel: false,
  )
  # streamed snapshots under latency, jitter and loss, with the
  # client/server snapshot base check on
  test(
    'remote-client-map-change-latency',
    py,
    args: [
      meson.project_source_root() / 'tests' / 'test_remote_client_map_change.py',
      '--ded-binary', all_stock_maps_ded,
      '--base-check',
      '--latency', '60',
      '--jitter', '40',
      '--drop', '10',
      '--min-snapshots', '40',
      '--port', '29980',
      '--log-dir', meson.project_build_root() / 'meson-logs' / 'remote-client-map-change-latency',
    ],
    workdir: meson.project_source_root(),
    suite: ['integration', 'stock-maps'],
    protocol: 'exitcode',
    timeout: 600,
    is_parallel: false,
  )
  # two remote clients: spawns, replication, movement and weapon input over UDP
  test(
    'multiclient-gameplay',
    py,
    args: [meson.project_source_root() / 'tests' / 'test_multiclient_gameplay.py', '--ded-binary', all_stock_maps_ded, '--base-check'],
    workdir: meson.project_source_root(),
    suite: ['integration'],
    protocol: 'exitcode',
    timeout: 600,
    is_parallel: false,
  )
  # the same over a simulated 80-120 ms, 5% loss link: client prediction time
  # sync (the client runs ahead so its usercmds arrive in time)
  test(
    'multiclient-gameplay-latency',
    py,
    args: [
      meson.project_source_root() / 'tests' / 'test_multiclient_gameplay.py',
      '--ded-binary', all_stock_maps_ded,
      '--base-check', '--latency', '80', '--jitter', '40', '--drop', '5',
    ],
    workdir: meson.project_source_root(),
    suite: ['integration'],
    protocol: 'exitcode',
    timeout: 600,
    is_parallel: false,
  )
  test(
    'userinfo',
    py,
    args: [
      meson.project_source_root() / 'tests' / 'test_userinfo.py',
      '--ded-binary', all_stock_maps_ded,
      '--log-dir', meson.project_build_root() / 'meson-logs' / 'userinfo',
    ],
    workdir: meson.project_source_root(),
    suite: ['integration', 'stock-maps'],
    protocol: 'exitcode',
    timeout: 600,
    is_parallel: false,
  )
  test(
    'userinfo-loss',
    py,
    args: [
      meson.project_source_root() / 'tests' / 'test_userinfo.py',
      '--ded-binary', all_stock_maps_ded,
      '--drop', '20',
      '--port', '29975',
      '--log-dir', meson.project_build_root() / 'meson-logs' / 'userinfo-loss',
    ],
    workdir: meson.project_source_root(),
    suite: ['integration', 'stock-maps'],
    protocol: 'exitcode',
    timeout: 600,
    is_parallel: false,
  )
  # the same with 20% packet loss each way (net_forceDrop), connecting straight
  # into outskirts: streamed snapshots superseding lost ones, the reliable game init
  test(
    'remote-client-map-change-loss',
    py,
    args: [
      meson.project_source_root() / 'tests' / 'test_remote_client_map_change.py',
      '--ded-binary', all_stock_maps_ded,
      '--maps', 'outskirts,quarry',
      '--drop', '20',
      '--min-snapshots', '20',
      '--port', '29970',
      '--log-dir', meson.project_build_root() / 'meson-logs' / 'remote-client-map-change-loss',
    ],
    workdir: meson.project_source_root(),
    suite: ['integration', 'stock-maps'],
    protocol: 'exitcode',
    timeout: 600,
    is_parallel: false,
  )
endif
