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

# 5a. Sub-unit mouse motion (#141): SDL3's float deltas are carried, so slow motion
#     made of sub-unit events (a Retina Mac) is not truncated away
mouse_delta_test = executable(
  'openetqw-mouse-delta-test',
  files('MouseDeltaTest.cpp'),
  include_directories: include_directories('../src/sys/sdl'),
  install: false,
)
test('mouse-delta', mouse_delta_test, suite: 'unit', timeout: 30)

# 5b. idMsgChannel fragmentation: a message whose encoded length is an exact multiple
#     of FRAGMENT_SIZE ends with an empty fragment, which idBitMsg::WriteData is fatal
#     on (the server died on it mid-session); the real channel, compressor and
#     idFile_BitMsg, a captured port
msgchannel_test = executable(
  'openetqw-msgchannel-test',
  files(
    'MsgChannelTest.cpp',
    meson.project_source_root() / 'src' / 'framework' / 'async' / 'MsgChannel.cpp',
    meson.project_source_root() / 'src' / 'framework' / 'Compressor.cpp',
    meson.project_source_root() / 'src' / 'libs' / 'filelib' / 'File.cpp',
  ),
  # compiled as the dedicated server compiles these sources (its defines and the
  # engine's force-included precompiled.h)
  include_directories: engine_include_dirs,
  cpp_args: common_engine_defs + ['-DID_DEDICATED'] + engine_pch_args,
  # zlib for unzip.h's include path; threads for idLib's sdLock, which these
  # sources reference (pthreads on POSIX)
  dependencies: test_deps + [zlib_dep, threads_dep],
  link_args: test_link_args,
  install: false,
)
test('msgchannel', msgchannel_test, suite: 'unit', timeout: 30)

# 5c. Retail data discovery (ledger 068): the base/pak000.pk4 marker in any case,
#     path lists, the stored path, Steam's libraryfolders.vdf and steamapps/common,
#     and the search order, on synthetic trees with the platform hooks stubbed
retail_discovery_test = executable(
  'openetqw-retail-discovery-test',
  files(
    'RetailDiscoveryTest.cpp',
    meson.project_source_root() / 'src' / 'framework' / 'RetailDiscovery.cpp',
  ),
  # compiled as the engine compiles these sources (its defines and the engine's
  # force-included precompiled.h)
  include_directories: engine_include_dirs,
  cpp_args: common_engine_defs + ['-DID_DEDICATED'] + engine_pch_args,
  dependencies: test_deps + [zlib_dep, threads_dep],
  link_args: test_link_args,
  install: false,
)
test('retail-discovery', retail_discovery_test, suite: 'unit', timeout: 30)

# 5d. GUI text layout (#181, docs/gui-text.md): alignment, colour escapes, line
#     ends, word wrap, truncation, kerning, the screen scale, pixel snapping and
#     the advances and line breaks the game reads, on a stand-in face
gui_text_layout_test = executable(
  'openetqw-gui-text-layout-test',
  files(
    'GuiTextLayoutTest.cpp',
    meson.project_source_root() / 'src' / 'renderer' / 'GuiTextLayout.cpp',
  ),
  include_directories: engine_include_dirs + [include_directories('../src/renderer')],
  cpp_args: common_engine_defs + ['-DID_DEDICATED'] + engine_pch_args,
  dependencies: test_deps + [zlib_dep, threads_dep],
  link_args: test_link_args,
  install: false,
)
test('gui-text-layout', gui_text_layout_test, suite: 'unit', timeout: 30)

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

# No private host, user, address or home path in tracked files (owner, 2026-10-29)
test(
  'no-pii',
  py,
  args: [meson.project_source_root() / 'tools' / 'ci' / 'check_no_pii.py'],
  suite: 'unit',
  timeout: 60,
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

# The build helpers (scripts/build-*): option conflicts, step order, one build type
test(
  'build-scripts',
  py,
  args: [meson.project_source_root() / 'tests' / 'test_build_scripts.py'],
  suite: 'unit',
  timeout: 120,
)

# Failure-marker classification: a bot-thread pacing warning fails only a failed run (#294)
test(
  'harness-failure-markers',
  py,
  args: [meson.project_source_root() / 'tests' / 'test_harness_failure_markers.py'],
  suite: 'unit',
  timeout: 30,
)

# Harness private dirs: no mkdtemp owner-only DACL on Windows, so a sandboxed engine
# can write its SPIR-V cache under fs_savepath (openai/codex#19791)
test(
  'harness-private-dirs',
  py,
  args: [meson.project_source_root() / 'tests' / 'test_harness_private_dirs.py'],
  suite: 'unit',
  timeout: 30,
)

# The retail reference capture tool's offline parts: the output guard, PNG, boxes, views
test(
  'retail-capture-tool',
  py,
  args: [meson.project_source_root() / 'tests' / 'test_retail_capture_tool.py'],
  suite: 'unit',
  timeout: 30,
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
    timeout: 360,
    is_parallel: false,
  )
  # the same replay on a listen host (+spawnServer; M5 plan step 5's listen variant): the
  # Vulkan client is the server; it also checks that the host's draws between ticks are
  # unlocked draws (step 7 has the host call ClientUpdateView on its render-only frames)
  test(
    'high-refresh-determinism-listen',
    py,
    args: [meson.project_source_root() / 'tests' / 'test_high_refresh_determinism.py', '--listen', '--port', '29964'],
    env: client_test_env,
    workdir: meson.project_source_root(),
    suite: ['client', 'integration'],
    timeout: 360,
    is_parallel: false,
  )
  # key turning on a listen host: the Vulkan client turns its own player at caps 30 and
  # 240, and the server's usercmds must turn at in_yawSpeed a second at both
  test(
    'turn-rate-listen',
    py,
    args: [
      meson.project_source_root() / 'tests' / 'test_turn_rate.py',
      '--listen',
      '--client-binary', campaign_client,
      '--port', '29994',
      '--log-dir', meson.project_build_root() / 'meson-logs' / 'turn-rate-listen',
    ],
    workdir: meson.project_source_root(),
    suite: ['client', 'integration'],
    protocol: 'exitcode',
    timeout: 300,
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
  # M3: the cross-OS client/server pair; the server runs on another host, started there
  # by hand (OPENETQW_REMOTE_SERVER=host:port; SKIP without it)
  test(
    'cross-os',
    py,
    args: [meson.project_source_root() / 'tests' / 'test_cross_os.py',
           '--log-dir', meson.project_build_root() / 'meson-logs' / 'cross-os'],
    env: client_test_env,
    workdir: meson.project_source_root(),
    suite: ['client', 'integration'],
    timeout: 900,
    is_parallel: false,
  )
  # M5 step 14b: the hull-lead measurement (g_determinismTest 4, two caps): every vehicle
  # class spawned, entered, moved and identical across caps; prints the predictor table
  test(
    'lead-measure',
    py,
    args: [meson.project_source_root() / 'tests' / 'test_lead_measure.py'],
    env: client_test_env,
    workdir: meson.project_source_root(),
    suite: 'integration',
    timeout: 600,
    is_parallel: false,
  )
  # M5 step 13: the vehicle seat gate (g_determinismTest 3, four runs): an excluded
  # seat never unlocks, keys match, and the gate changes nothing drawn or simulated
  test(
    'seat-gate',
    py,
    args: [meson.project_source_root() / 'tests' / 'test_seat_gate.py'],
    env: client_test_env,
    workdir: meson.project_source_root(),
    suite: 'integration',
    timeout: 660,
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
  # a GUI that fails to parse while a map is loaded (game #49): the error is
  # passed on as the drop it is, not swallowed; at the menu it stays a warning
  test(
    'gui-load-error',
    py,
    args: [
      meson.project_source_root() / 'tests' / 'test_gui_load_error.py',
      '--client-binary', campaign_client,
      '--port', '29988',
      '--log-dir', meson.project_build_root() / 'meson-logs' / 'gui-load-error',
    ],
    workdir: meson.project_source_root(),
    suite: ['client', 'integration'],
    protocol: 'exitcode',
    timeout: 600,
    is_parallel: false,
  )
  # game #51: a GUI owned by a HUD module is freed by MapShutdown while its
  # Load is still on the stack; the catch must not read through the freed this
  test(
    'gui-hud-reload-error',
    py,
    args: [
      meson.project_source_root() / 'tests' / 'test_gui_hud_reload_error.py',
      '--client-binary', campaign_client,
      '--port', '29989',
      '--log-dir', meson.project_build_root() / 'meson-logs' / 'gui-hud-reload-error',
    ],
    workdir: meson.project_source_root(),
    suite: ['client', 'integration'],
    protocol: 'exitcode',
    timeout: 600,
    is_parallel: false,
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
    args: [meson.project_source_root() / 'tests' / 'test_retail_install_check.py', '--ded-binary', retail_install_check_ded,
           '--game-module', game_module_built],
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
  # a connected client ignores a late, duplicate challengeResponse (#216)
  test(
    'client-stray-challenge',
    py,
    args: [
      meson.project_source_root() / 'tests' / 'test_client_stray_challenge.py',
      '--ded-binary', all_stock_maps_ded,
      '--port', '29860',
      '--log-dir', meson.project_build_root() / 'meson-logs' / 'client-stray-challenge',
    ],
    workdir: meson.project_source_root(),
    suite: ['integration'],
    protocol: 'exitcode',
    timeout: 300,
    is_parallel: false,
  )
  # a client whose challenge the server forgot asks for a new one (may fix #216)
  test(
    'client-bad-challenge',
    py,
    args: [
      meson.project_source_root() / 'tests' / 'test_client_bad_challenge.py',
      '--ded-binary', all_stock_maps_ded,
      '--port', '29870',
      '--log-dir', meson.project_build_root() / 'meson-logs' / 'client-bad-challenge',
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
  # M4: damage and kills on a dedicated server, from the game's KILL lines
  # (g_logGameEvents) and damageClient: a hit, a kill with both teams and the
  # decl, a respawn, a world kill and a team kill, nothing else dying
  test(
    'm4-damage-kills',
    py,
    args: [
      meson.project_source_root() / 'tests' / 'test_m4_damage_kills.py',
      '--ded-binary', all_stock_maps_ded,
      '--port', '29958',
      '--log-dir', meson.project_build_root() / 'meson-logs' / 'm4-damage-kills',
    ],
    workdir: meson.project_source_root(),
    suite: ['integration', 'm4'],
    protocol: 'exitcode',
    timeout: 600,
    is_parallel: false,
  )
  # M4: every class of both teams spawns with its own kit (the game's SPAWN
  # lines): role items present, on no other class; team items on one team
  test(
    'm4-classes',
    py,
    args: [
      meson.project_source_root() / 'tests' / 'test_m4_classes.py',
      '--ded-binary', all_stock_maps_ded,
      '--port', '29962',
      '--log-dir', meson.project_build_root() / 'meson-logs' / 'm4-classes',
    ],
    workdir: meson.project_source_root(),
    suite: ['integration', 'm4'],
    protocol: 'exitcode',
    timeout: 300,
    is_parallel: false,
  )
  # M4: a deployable is requested with the deploy checks on, carried in and
  # landed at the spot, for each team (deployStatus, g_debugDeployables)
  test(
    'm4-deployable',
    py,
    args: [
      meson.project_source_root() / 'tests' / 'test_m4_deployable.py',
      '--ded-binary', all_stock_maps_ded,
      '--port', '29966',
      '--log-dir', meson.project_build_root() / 'meson-logs' / 'm4-deployable',
    ],
    workdir: meson.project_source_root(),
    suite: ['integration', 'm4'],
    protocol: 'exitcode',
    timeout: 400,
    is_parallel: false,
  )
  # M4: an objective match ends both ways (the game's MATCH lines): the
  # defenders win on the time limit, the attackers on the last objective
  test(
    'm4-match-end',
    py,
    args: [
      meson.project_source_root() / 'tests' / 'test_m4_match_end.py',
      '--ded-binary', all_stock_maps_ded,
      '--port', '29972',
      '--log-dir', meson.project_build_root() / 'meson-logs' / 'm4-match-end',
    ],
    workdir: meson.project_source_root(),
    suite: ['integration', 'm4'],
    protocol: 'exitcode',
    timeout: 400,
    is_parallel: false,
  )
  # M4: a stopwatch pair (the game's STOPWATCH lines): round 1 times out and sets
  # the time to beat, the teams swap, round 2's attackers beat it on objectives
  test(
    'm4-stopwatch',
    py,
    args: [
      meson.project_source_root() / 'tests' / 'test_m4_stopwatch.py',
      '--ded-binary', all_stock_maps_ded,
      '--port', '29985',
      '--log-dir', meson.project_build_root() / 'meson-logs' / 'm4-stopwatch',
    ],
    workdir: meson.project_source_root(),
    suite: ['integration', 'm4'],
    protocol: 'exitcode',
    timeout: 400,
    is_parallel: false,
  )
  # M4: a campaign plays its three maps (the frame command "map", the game's
  # CAMPAIGN lines), a connected client follows each, then an admin map change
  # (ports 29986 and 29987: the server and the headless client)
  test(
    'm4-campaign',
    py,
    args: [
      meson.project_source_root() / 'tests' / 'test_m4_campaign.py',
      '--ded-binary', all_stock_maps_ded,
      '--port', '29986',
      '--log-dir', meson.project_build_root() / 'meson-logs' / 'm4-campaign',
    ],
    workdir: meson.project_source_root(),
    suite: ['integration', 'm4'],
    protocol: 'exitcode',
    timeout: 900,
    is_parallel: false,
  )
  # M4: two live bots play a full time-limited match and complete no objective
  # (the game's MATCH and OBJECTIVE_STATUS lines, positions during the match)
  test(
    'm4-bot-match',
    py,
    args: [
      meson.project_source_root() / 'tests' / 'test_m4_bot_match.py',
      '--ded-binary', all_stock_maps_ded,
      '--port', '29992',
      '--log-dir', meson.project_build_root() / 'meson-logs' / 'm4-bot-match',
    ],
    workdir: meson.project_source_root(),
    suite: ['integration', 'm4'],
    protocol: 'exitcode',
    timeout: 400,
    is_parallel: false,
  )
  # M4: a started match runs to its time limit on maps beyond the campaign's
  # three (quarry and island), each read from the game's MATCH lines
  test(
    'm4-offcampaign-match',
    py,
    args: [
      meson.project_source_root() / 'tests' / 'test_m4_offcampaign_match.py',
      '--ded-binary', all_stock_maps_ded,
      '--port', '29993',
      '--log-dir', meson.project_build_root() / 'meson-logs' / 'm4-offcampaign-match',
    ],
    workdir: meson.project_source_root(),
    suite: ['integration', 'm4'],
    protocol: 'exitcode',
    timeout: 800,
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
  # a headless remote client (the dedicated binary with +connect) put in a
  # vehicle by the server: it survives, its seat replicates (g_logVehicleViews
  # VIEW_STATE on both sides), exitVehicle clears it (badger, then titan)
  test(
    'headless-client-vehicle',
    py,
    args: [
      meson.project_source_root() / 'tests' / 'test_headless_vehicle.py',
      '--ded-binary', all_stock_maps_ded,
      '--port', '29983',
      '--log-dir', meson.project_build_root() / 'meson-logs' / 'headless-client-vehicle',
    ],
    workdir: meson.project_source_root(),
    suite: ['integration'],
    protocol: 'exitcode',
    timeout: 600,
    is_parallel: false,
  )
  # a weapon's tracer reaches another client, not just the shooter (M3): the
  # server broadcasts EVENT_TRACER, and the shooter and a headless observer
  # receive the same start and end; playerPos is polled every 0.25 s
  test(
    'weapon-fire-observer',
    py,
    args: [
      meson.project_source_root() / 'tests' / 'test_m3_weapon_fire.py',
      '--ded-binary', all_stock_maps_ded,
      '--port', '29974',
      '--log-dir', meson.project_build_root() / 'meson-logs' / 'weapon-fire-observer',
    ],
    workdir: meson.project_source_root(),
    suite: ['integration'],
    protocol: 'exitcode',
    timeout: 600,
    is_parallel: false,
  )
  # a remote client whose GUI fails to parse in a map (engine #116): the drop
  # disconnects it from the server, it reads no more snapshots, and it can reconnect
  test(
    'client-error-drop',
    py,
    args: [
      meson.project_source_root() / 'tests' / 'test_client_error_drop.py',
      '--ded-binary', all_stock_maps_ded,
      '--port', '29996',
      '--log-dir', meson.project_build_root() / 'meson-logs' / 'client-error-drop',
    ],
    workdir: meson.project_source_root(),
    suite: ['integration'],
    protocol: 'exitcode',
    timeout: 600,
    is_parallel: false,
  )
  # the same during the client's load of the server's map (the Vulkan client: the
  # headless binary loads no HUD GUIs): the load is abandoned as a drop (#118 review)
  if build_engine
    test(
      'client-error-load-drop',
      py,
      args: [
        meson.project_source_root() / 'tests' / 'test_client_error_drop.py',
        '--at', 'load',
        '--ded-binary', all_stock_maps_ded,
        '--client-binary', campaign_client,
        '--port', '29998',
        '--log-dir', meson.project_build_root() / 'meson-logs' / 'client-error-load-drop',
      ],
      workdir: meson.project_source_root(),
      suite: ['client', 'integration'],
      protocol: 'exitcode',
      timeout: 600,
      is_parallel: false,
    )
  endif
  # the server lets a remote client go (#119): a kick, or its timeout of the client
  # (and the client's of the server); the client is told, shuts its map down, reads
  # no snapshots and can reconnect
  foreach drop_case : [['kick', '29944'], ['timeout', '29946']]
    test(
      'client-server-' + drop_case[0],
      py,
      args: [
        meson.project_source_root() / 'tests' / 'test_client_server_drop.py',
        '--case', drop_case[0],
        '--ded-binary', all_stock_maps_ded,
        '--port', drop_case[1],
        '--log-dir', meson.project_build_root() / 'meson-logs' / ('client-server-' + drop_case[0]),
      ],
      workdir: meson.project_source_root(),
      suite: ['integration'],
      protocol: 'exitcode',
      timeout: 600,
      is_parallel: false,
    )
  endforeach
  # 'disconnect' on a listen host ends its server (#120): the remote client is told
  # and joins another server (ports 29954 host, 29955 client, 29956 the second server)
  if build_engine
    test(
      'listen-host-disconnect',
      py,
      args: [
        meson.project_source_root() / 'tests' / 'test_client_server_drop.py',
        '--case', 'listen',
        '--ded-binary', all_stock_maps_ded,
        '--client-binary', campaign_client,
        '--port', '29954',
        '--log-dir', meson.project_build_root() / 'meson-logs' / 'listen-host-disconnect',
      ],
      workdir: meson.project_source_root(),
      suite: ['client', 'integration'],
      protocol: 'exitcode',
      timeout: 600,
      is_parallel: false,
    )
  endif
  # out-of-order delivery (net_forceReorder): 20% of the packets each process
  # receives are held back up to 60 ms so later ones overtake them. The map
  # change's reliable game init and the snapshot stream must survive it, with
  # the snapshot base checked; the test requires that packets were reordered.
  test(
    'remote-client-map-change-reorder',
    py,
    args: [
      meson.project_source_root() / 'tests' / 'test_remote_client_map_change.py',
      '--ded-binary', all_stock_maps_ded,
      '--maps', 'outskirts,quarry',
      '--latency', '20',
      '--reorder', '20',
      '--reorder-delay', '60',
      '--base-check',
      '--port', '29977',
      '--log-dir', meson.project_build_root() / 'meson-logs' / 'remote-client-map-change-reorder',
    ],
    workdir: meson.project_source_root(),
    suite: ['integration', 'stock-maps'],
    protocol: 'exitcode',
    timeout: 600,
    is_parallel: false,
  )
  # two clients and a server with the same reordering on a 40 ms link: spawns,
  # mutual replication within tolerance, peer movement, the attack usercmd
  test(
    'multiclient-gameplay-reorder',
    py,
    args: [
      meson.project_source_root() / 'tests' / 'test_multiclient_gameplay.py',
      '--ded-binary', all_stock_maps_ded,
      '--base-check', '--latency', '40', '--reorder', '20', '--reorder-delay', '60',
      '--log-dir', meson.project_build_root() / 'meson-logs' / 'multiclient-gameplay-reorder',
    ],
    workdir: meson.project_source_root(),
    suite: ['integration'],
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
  frame_poses_ports = {'join': '29920', 'listen': '29924', 'devmap': '29928'}
  foreach observer : ['join', 'listen', 'devmap']
    test(
      'frame-poses-' + observer,
      py,
      args: [
        meson.project_source_root() / 'tests' / 'test_frame_poses.py',
        '--observer', observer,
        '--ded-binary', all_stock_maps_ded,
        '--client-binary', campaign_client,
        '--port', frame_poses_ports[observer],
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

# 8b. The M2 render-world parity test (queue §3 item 8): the same three scripted scenes
#    on a Vulkan listen host (the real render world) and a dedicated server
#    (idRenderWorldHeadless); the outcomes are compared and the numbers are the
#    evidence for the owner's headless-render-world question (queue §4)
if build_engine and build_dedicated
  test(
    'm2-render-world-parity',
    py,
    args: [
      meson.project_source_root() / 'tests' / 'test_m2_render_world_parity.py',
      '--ded-binary', all_stock_maps_ded,
      '--client-binary', campaign_client,
      '--ded-port', '29982',
      '--listen-port', '29984',
      '--log-dir', meson.project_build_root() / 'meson-logs' / 'm2-render-world-parity',
    ],
    workdir: meson.project_source_root(),
    suite: ['client', 'integration', 'm2'],
    protocol: 'exitcode',
    timeout: 600,
    is_parallel: false,
  )
endif

# 9a. What the Vulkan client draws from a vehicle seat the seat gate allows (M5 step 14,
#     stage 3): single player and a joined client, the badger's driver seat driving at caps 60/120/240, with
#     an unlisted negative control and the chase camera and icarus as guards
if build_engine and build_dedicated
  seat_poses_ports = {'devmap': '29932', 'join': '29934', 'listen': '29938'}
  foreach mode : ['devmap', 'join', 'listen']
    test(
      'seat-poses-' + mode,
      py,
      args: [
        meson.project_source_root() / 'tests' / 'test_seat_poses.py',
        '--mode', mode,
        '--ded-binary', all_stock_maps_ded,
        '--client-binary', campaign_client,
        '--port', seat_poses_ports[mode],
        '--log-dir', meson.project_build_root() / 'meson-logs' / 'seat-poses',
      ],
      workdir: meson.project_source_root(),
      suite: ['client', 'integration'],
      protocol: 'exitcode',
      timeout: 600,
      is_parallel: false,
    )
  endforeach
  # M5 step 14b: the hull-lead measurement driven by the player's own commands, on a
  #     network client and a listen host (g_determinismTest 4 with g_leadMeasureDrive 0):
  #     every class entered and driven, and each presented class's VEH_PRED table
  lead_measure_ports = {'join': '29840', 'listen': '29844'}
  foreach mode : ['join', 'listen']
    test(
      'lead-measure-' + mode,
      py,
      args: [
        meson.project_source_root() / 'tests' / 'test_lead_measure_client.py',
        '--mode', mode,
        '--ded-binary', all_stock_maps_ded,
        '--client-binary', campaign_client,
        '--port', lead_measure_ports[mode],
        '--log-dir', meson.project_build_root() / 'meson-logs' / 'lead-measure',
      ],
      workdir: meson.project_source_root(),
      suite: ['client', 'integration'],
      protocol: 'exitcode',
      timeout: 600,
      is_parallel: false,
    )
  endforeach
endif

# 10. Vehicle views on the network (M5 step 6, owner decision 4): the listen host puts a
#     remote client in the badger and cycles it into a smooth view; AOR_VIEW and
#     VIEW_STATE lines from the game (g_logVehicleViews) show whether the server's and the
#     client's area-of-relevance calls see the tick's view; known gaps are listed in the
#     test (two Vulkan clients, each with a window: the headless dedicated binary cannot
#     sit in a vehicle as a client)
if build_engine
  test(
    'vehicle-view-net',
    py,
    args: [
      meson.project_source_root() / 'tests' / 'test_vehicle_view_net.py',
      '--client-binary', campaign_client,
      '--port', '29910',
      '--log-dir', meson.project_build_root() / 'meson-logs' / 'vehicle-view-net',
    ],
    workdir: meson.project_source_root(),
    suite: ['client', 'integration'],
    protocol: 'exitcode',
    timeout: 600,
    is_parallel: false,
  )
endif

# 11. Aircraft re-prediction (M5 step 14a): a Vulkan listen host on valley seats a Vulkan
#     pilot client in an anansi; AIR_CTL lines (g_logAirControl) from the server and from
#     each tick the client predicts or replays are paired by tick. With
#     g_airControlHistory on, the first tick replayed after each snapshot starts from the
#     snapshot tick's cyclic base and collective, as on the server; off, it does not
#     (the negative control). At loopback latency and with net_forceLatency 50.
if build_engine
  test(
    'aircraft-reprediction',
    py,
    args: [
      meson.project_source_root() / 'tests' / 'test_aircraft_reprediction.py',
      '--client-binary', campaign_client,
      '--port', '29900',
      '--log-dir', meson.project_build_root() / 'meson-logs' / 'aircraft-reprediction',
    ],
    workdir: meson.project_source_root(),
    suite: ['client', 'integration'],
    protocol: 'exitcode',
    timeout: 600,
    is_parallel: false,
  )
endif

# 12. Mixed-cap live session (M5 step 15): a dedicated server, two Vulkan clients at
#     different caps (60/240, 120/144, 45/90) and a headless peer on valley; each client
#     passes the frame-pose checks of step 3 against its own cap, logs that cap on every
#     draw, and turns at 90 deg/s whatever its cap; the other client is checked as a
#     remote player (known gap until step 8: remote players change once per tick)
if build_engine and build_dedicated
  test(
    'mixed-caps',
    py,
    args: [
      meson.project_source_root() / 'tests' / 'test_mixed_caps.py',
      '--ded-binary', all_stock_maps_ded,
      '--client-binary', campaign_client,
      '--port', '29880',
      '--log-dir', meson.project_build_root() / 'meson-logs' / 'mixed-caps',
    ],
    workdir: meson.project_source_root(),
    suite: ['client', 'integration'],
    protocol: 'exitcode',
    timeout: 600,
    is_parallel: false,
  )
endif

# 12b. The swapchain-image acquire before the fraction (#335): a single-player Vulkan client
#      with FIFO presentation; with r_vkPrepareAhead the time from BeginFrame to the draw
#      holds no acquire wait, and with it off (the negative control) it does where the
#      acquire blocks
if build_engine
  test(
    'acquire-ahead',
    py,
    args: [
      meson.project_source_root() / 'tests' / 'test_acquire_ahead.py',
      '--client-binary', campaign_client,
      '--port', '29930',
      '--log-dir', meson.project_build_root() / 'meson-logs' / 'acquire-ahead',
    ],
    workdir: meson.project_source_root(),
    suite: ['client', 'integration'],
    protocol: 'exitcode',
    timeout: 300,
    is_parallel: false,
  )
endif

# 12c. Acceleration-structure builds prewarmed at ray-tracing init (#338): two single-player
#      Vulkan clients on valley with ray tracing turn a full circle; with the prewarm no build
#      in play is the first of its configuration, and without it (the negative control) the
#      static BLAS, deforming BLAS and TLAS first builds are reported. Skips without ray tracing.
if build_engine
  test(
    'rt-build-prewarm',
    py,
    args: [
      meson.project_source_root() / 'tests' / 'test_rt_build_prewarm.py',
      '--client-binary', campaign_client,
      '--port', '29890',
      '--log-dir', meson.project_build_root() / 'meson-logs' / 'rt-build-prewarm',
    ],
    workdir: meson.project_source_root(),
    suite: ['client', 'integration'],
    protocol: 'exitcode',
    timeout: 600,
    is_parallel: false,
  )
endif

# 12d. A Vulkan frame that fails after its swapchain image was acquired (#337): the cheat
#      r_vkTestFailFrame injects failures in BeginFrame and EndFrame, more than the
#      swapchain holds images; frames must go on, with no validation error
if build_engine
  test(
    'abandon-acquired-image',
    py,
    args: [
      meson.project_source_root() / 'tests' / 'test_abandon_acquired_image.py',
      '--client-binary', campaign_client,
      '--port', '29931',
      '--log-dir', meson.project_build_root() / 'meson-logs' / 'abandon-acquired-image',
    ],
    workdir: meson.project_source_root(),
    suite: ['client', 'integration'],
    protocol: 'exitcode',
    timeout: 300,
    is_parallel: false,
  )
endif

# 12e. A Vulkan frame that fails in EndFrame, after it was recorded (#343): its uploads are
#      submitted alone, and the first layout transitions and BLAS builds it recorded are rolled
#      back; injected on a frame that creates images and on frames that recreate the swapchain,
#      the validation layer must report no layout error
if build_engine
  test(
    'failed-frame-uploads',
    py,
    args: [
      meson.project_source_root() / 'tests' / 'test_failed_frame_uploads.py',
      '--client-binary', campaign_client,
      '--port', '29936',
      '--log-dir', meson.project_build_root() / 'meson-logs' / 'failed-frame-uploads',
    ],
    workdir: meson.project_source_root(),
    suite: ['client', 'integration'],
    protocol: 'exitcode',
    timeout: 300,
    is_parallel: false,
  )
endif
