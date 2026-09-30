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
  meson.project_source_root() / 'src' / 'framework' / 'async' / 'UsercmdCodec.cpp',
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
  meson.project_source_root() / 'src' / 'framework' / 'async' / 'UsercmdCodec.cpp',
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

# 5. sdStringBuilder grown past its base buffer on the stack (the MSVC 19.51 /O2
#    strncpy miscompile the game repository works around; meaningful in optimized builds)
strbuilder_test = executable(
  'openetqw-strbuilder-test',
  files('StrBuilderTest.cpp'),
  include_directories: test_include_dirs,
  cpp_args: ['-D__IDLIB__', '-DSD_SDK_BUILD', '-DSD_PUBLIC_BUILD'],
  dependencies: test_deps,
  link_args: test_link_args,
  install: false,
)
test('strbuilder', strbuilder_test, suite: 'unit', timeout: 30)

# Every package carries the licence texts (GPL, SDK EULA, third-party notices)
test(
  'install-licenses',
  py,
  args: [meson.project_source_root() / 'tools' / 'ci' / 'check_install_licenses.py', meson.project_build_root()],
  suite: 'unit',
  timeout: 30,
)

# 5. Zero-SDK compliance test
test(
  'zero-sdk',
  py,
  args: [meson.project_source_root() / 'tests' / 'test_zero_sdk.py'],
  suite: 'unit',
  timeout: 30,
)

# Release tooling: version bumps, changelog, asset set, source provenance, installer
test(
  'release-tools',
  py,
  args: [meson.project_source_root() / 'tests' / 'test_release_tools.py'],
  suite: 'unit',
  timeout: 60,
)

# Packaging and validation tooling: staged-payload checks, archives, AppRun
test(
  'packaging-tools',
  py,
  args: [meson.project_source_root() / 'tests' / 'test_packaging_tools.py'],
  suite: 'unit',
  timeout: 60,
)

# 6. Client lifecycle & graphics verification test
if build_engine
  campaign_client = meson.project_build_root() / engine_client_binary_name
  client_test_ded = meson.project_build_root() / engine_ded_binary_name
  if host_system == 'windows'
    campaign_client += '.exe'
    client_test_ded += '.exe'
  endif
  # the binaries of this build directory, for the tests that find theirs through
  # etqw_harness.build_binary (the default is <source>/build)
  client_test_env = {'OPENETQW_CLIENT_BINARY': campaign_client, 'OPENETQW_DED_BINARY': client_test_ded}
  test(
    'client-lifecycle',
    py,
    args: [meson.project_source_root() / 'tests' / 'test_client_lifecycle.py'],
    env: client_test_env,
    workdir: meson.project_source_root(),
    suite: 'integration',
    timeout: 120,
  )
  # r_renderAPI defaults to vulkan and falls back to OpenGL when Vulkan is
  # unavailable; three client startups that contend for the display
  test(
    'render-api-fallback',
    py,
    args: [meson.project_source_root() / 'tests' / 'test_render_api_fallback.py',
      '--client-binary', campaign_client],
    workdir: meson.project_source_root(),
    suite: 'integration',
    timeout: 180,
    is_parallel: false,
  )
  # asserts wall-clock frame rates: never run it next to another test
  test(
    'high-refresh-determinism',
    py,
    args: [meson.project_source_root() / 'tests' / 'test_high_refresh_determinism.py'],
    env: client_test_env,
    workdir: meson.project_source_root(),
    suite: 'integration',
    timeout: 180,
    is_parallel: false,
  )
  # M5 step 5a: the vehicle replay (g_determinismTest 2) against the recorded
  # golden; the local merge gate of every game PR touching vehicles or physics
  test(
    'vehicle-physics-golden',
    py,
    args: [meson.project_source_root() / 'tests' / 'test_vehicle_physics_golden.py'],
    env: client_test_env,
    workdir: meson.project_source_root(),
    suite: 'integration',
    timeout: 360,
    is_parallel: false,
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
    env: client_test_env,
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
  # the retail-install check (fs_validateOfficialPaks) on synthetic data: needs no retail data
  retail_install_check_ded = meson.project_build_root() / engine_ded_binary_name
  if host_system == 'windows'
    retail_install_check_ded += '.exe'
  endif
  test(
    'retail-install-check',
    py,
    args: [meson.project_source_root() / 'tests' / 'test_retail_install_check.py', '--ded-binary', retail_install_check_ded],
    workdir: meson.project_source_root(),
    suite: 'unit',
    timeout: 120,
  )

  # com_unlock_maxFPS as a last-write-wins alias of com_maxFPS (M5 step 11, D8), on synthetic
  # data; the game module is staged into the synthetic base/
  test(
    'frame-cap-cvars',
    py,
    args: [meson.project_source_root() / 'tests' / 'test_frame_cap_cvars.py', '--ded-binary', retail_install_check_ded,
           '--game-module', game_module_built],
    workdir: meson.project_source_root(),
    suite: 'unit',
    timeout: 120,
  )

  # the retired com_unlock_* timing CVars (M5 step 12, D9), on synthetic data; the game
  # module is staged into the synthetic base/ (it need not sit beside the binary)
  test(
    'retired-cvars',
    py,
    args: [meson.project_source_root() / 'tests' / 'test_retired_cvars.py', '--ded-binary', retail_install_check_ded,
           '--game-module', game_module_built],
    workdir: meson.project_source_root(),
    suite: 'unit',
    timeout: 120,
  )

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
  # key turning runs at in_yawSpeed a second whatever the client's frame rate
  test(
    'turn-rate',
    py,
    args: [
      meson.project_source_root() / 'tests' / 'test_turn_rate.py',
      '--ded-binary', all_stock_maps_ded,
      '--port', '29990',
      '--log-dir', meson.project_build_root() / 'meson-logs' / 'turn-rate',
    ],
    workdir: meson.project_source_root(),
    suite: ['integration', 'stock-maps'],
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
  # two remote clients through a 'reconnect', a killed-and-restarted client and
  # a serverMapRestart; each client must still see the other move
  test(
    'reconnect-restart',
    py,
    args: [
      meson.project_source_root() / 'tests' / 'test_reconnect_restart.py',
      '--ded-binary', all_stock_maps_ded,
      '--port', '29950',
      '--log-dir', meson.project_build_root() / 'meson-logs' / 'reconnect-restart',
    ],
    workdir: meson.project_source_root(),
    suite: ['integration'],
    protocol: 'exitcode',
    timeout: 600,
    is_parallel: false,
  )
  # the charter's M3 soak: 10 minutes at 2% loss and 40 +/- 20 ms delay without
  # a disconnect (skip it in quick runs with 'meson test --no-suite soak')
  test(
    'lossy-session',
    py,
    args: [
      meson.project_source_root() / 'tests' / 'test_lossy_session.py',
      '--ded-binary', all_stock_maps_ded,
      '--port', '29940',
      '--log-dir', meson.project_build_root() / 'meson-logs' / 'lossy-session',
    ],
    workdir: meson.project_source_root(),
    suite: ['integration', 'soak'],
    protocol: 'exitcode',
    timeout: 1200,
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

# 8. The Vulkan client on a network (needs both binaries; opens a window):
#    joining a dedicated server, and hosting a listen server, each with a
#    headless remote client that must see the Vulkan client's player walk
if build_engine and build_dedicated
  foreach mode : ['join', 'listen']
    test(
      'client-network-' + mode,
      py,
      args: [
        meson.project_source_root() / 'tests' / 'test_client_network.py',
        '--mode', mode,
        '--ded-binary', all_stock_maps_ded,
        '--client-binary', campaign_client,
        '--port', mode == 'join' ? '29930' : '29934',
        '--log-dir', meson.project_build_root() / 'meson-logs' / 'client-network',
      ],
      workdir: meson.project_source_root(),
      suite: ['client', 'integration'],
      protocol: 'exitcode',
      timeout: 600,
      is_parallel: false,
    )
  endforeach
endif

# 9. What the Vulkan client draws, frame by frame (M5): FRAME_POSE lines from the game
#    (g_logFramePoses) while the observer turns and walks and a headless peer walks, at
#    caps 60/120/240; known gaps are listed in the test (needs both binaries; a window)
if build_engine and build_dedicated
  foreach observer : ['join', 'listen']
    test(
      'frame-poses-' + observer,
      py,
      args: [
        meson.project_source_root() / 'tests' / 'test_frame_poses.py',
        '--observer', observer,
        '--ded-binary', all_stock_maps_ded,
        '--client-binary', campaign_client,
        '--port', observer == 'join' ? '29920' : '29924',
        '--log-dir', meson.project_build_root() / 'meson-logs' / 'frame-poses',
      ],
      workdir: meson.project_source_root(),
      suite: ['client', 'integration'],
      protocol: 'exitcode',
      timeout: 600,
      is_parallel: false,
    )
  endforeach
endif
