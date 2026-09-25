# Retail 1.5 reference

What the retail ET:QW 1.5 game module and data do, where OpenETQW's decisions depend on it (the owner's answers of 2026-09-25: "match what 1.5 retail does"). Behaviour is described in words, with function names and addresses so it can be checked again; no disassembly is reproduced (CHARTER §11). The §2 and §3 findings were checked by a second, independent reading of the binary. The §4 defaults and the §5 findings were not re-checked: the `com_unlock_*` defaults were read in two separate passes, and `r_swapInterval` and §5 were read once. The §3 list of what render frames leave in tick state, and the listen-host remark, are inferences from the binary and the SDK, not traced results.

## 1. Sources

- The official Linux 1.5 client installer (`ETQW-client-1.5-full.x86.run`, a zip with an ELF prefix) holds the 1.5 patch paks and the three retail 1.5 game modules: `game000.pk4` (Windows `gamex86.dll`), `game001.pk4` (macOS `game.so`, i386, **with symbols**) and `game002.pk4` (Linux `gamex86.so`, stripped). The engine `etqw.x86` is in the same installer. Addresses below are in the macOS `game.so` unless noted.
- The repository's SDK is the ET:QW 1.5 SDK. Where this file says "the SDK", it means the installer-extracted SDK sources. The game repository's first commit already carries jmarshall's edits in the unlock code (`weaponOffsetLog`, the `#if 0` in `idEntity::Present`), so it is not a pristine SDK for those files.
- None of these files is in either repository. They are the owner's retail data (CHARTER §11).

## 2. Bot action class tags (`validClasses`)

- `idBotThreadData::LoadActions` (0xd529c) reads the `validClasses` key as a bool: the value is parsed as an integer and only "nonzero" is kept, so the stored field is 0 or 1 (the SDK's `idDict::GetBool`). The neighbouring integer keys (`deployableType`, `targetRouteID`) keep the full value, which is how the two reads are told apart. The Linux module (around 0x78a7bd) and the Windows module (around 0x104fd9cf) do the same.
- `idBotAI::Bot_CheckForTeamGoals` (0x5b72c) tests the field against a class bit (soldier 1, medic 2, engineer 4, field ops 8, covert ops 16) at three places: camp, roam and vehicle grab. A tagged action (value 1) therefore passes only for soldiers. A disguised covert ops bot skips the check at the camp and roam sites.
- **In retail 1.5, every bot action with a nonzero `validClasses` is soldier-only**, whatever classes the map names. OpenETQW reads it with `GetBool` again (owner decision B10; game PR #11); from 2026-09-22 until then it had read a class bitmask (`GetInt`). This affects 46 tagged uses on ark, outskirts, refinery, slipgate, valley and volcano (ledger 047); objective actions never read the field.

## 3. Frame unlock (M5 plan D2, D4, D5)

- **No vehicle seat unlocks.** `idPlayer::IsFPSUnlock` (0x3dd320) matches the SDK check for check. It returns false when the viewed player has a proxy entity, which covers every seat of every vehicle and the jetpack, with no seat or vehicle-type exception. It also returns false for a remote camera, a teleport, the deploy menu, third person, a dead or limbo local view player, and prone. It calls no vehicle or view-mode code.
- **`idGameLocal::ClientUpdateView`** (0x1518a8) is the SDK function. It has no vehicle branch. The sub-tick fraction is `(33 - timeLeft) / 33`, and it gives up when `timeLeft` is 33. On a render-only frame it writes:
  - the local player's `renderView` origin and axis;
  - the weapon's render entity origin and axis, as a rigid transform about the view origin;
  - bound GUIs and effects;
  - `renderEntity.origin` of the interpolated entities.
- **The tick draw** (`idPlayerView::RenderPlayerView`, 0x43734c) moves the view origin back to the previous tick's logged origin and shifts the weapon's render origin by the same amount. OpenETQW instead rebuilds the weapon pose from the view-space pose logged on the previous tick; when no weapon pose was logged for that tick, it applies the same shift as retail.
- **Vehicle views never interpolate.** The `CalculateViewPos` of `sdDampedVehicleView` (0x598d18), its free-pivot variant (0x598b60), `sdSmoothVehicleView` (0x59b050), its locked variant and `sdIcarusVehicleView`, and `sdTransport::UpdateModelTransform` (0x54852e) never read the unlock state. Called without a full update, the smooth view only returns its stored third-person pose, and the damped view skips damping. OpenETQW's added interpolation in these functions has no retail counterpart (M5 plan step 6).
- **Remote entities interpolate by origin only** (`idEntity::Present`, 0x108224). The producer is active in retail; OpenETQW has it compiled out (`#if 0`, M5 plan D5). It runs during the tick when `com_unlockFPS`, `g_unlock_interpolateMoving` and `canUnlockFrames` are set, the frame is not unlocked, the entity opted in, and its `aorFlags` do not have `AOR_INHIBIT_IK` set (0x10839f), so entities beyond the AoR IK cutoff are not interpolated. It stores the origin, draws the entity one tick behind, and render-only frames write only the lerped origin.
  - It interpolates no axes and no skeletal joints: animation is evaluated at the tick's time, which render frames never change. So "pose" means the entity origin (M5 plan D2).
  - Entities that opt in: players, items, projectiles, transports, and script entities with `option_unlock_interpolate`.
- **What render frames leave in tick state** (M5 plan D4). These are side channels. No traced path reaches the local player's movement or aim input *(inferred: which tick code reads the render view before `CalculateView` was not traced exhaustively, and in the tick after a player mounts a gun, `weapon_mounted::Fire` takes its origin and direction from the view the render frames wrote; `docs/frame-path.md` §6.9)*:
  - the local player's `renderView`, until `CalculateView` late in the next `idPlayer::Think`;
  - each interpolated entity's `renderEntity.origin` and `lastPushedOrigin`;
  - player combat models, which `Present` relinks at the drawn origin (`OnUpdateVisuals` → `idActor::LinkCombat`, 0x3b64f8), so traces between frames see the drawn position.

  On a listen host the game allows unlocking, so this could reach authoritative traces. The 1.4 patch notes list "Fixed jittery bots & vehicles when playing against the computer with unlockFPS on"; what that fix changed is unknown. Whether the retail engine calls `ClientUpdateView` on a listen host's render-only frames was not traced.

## 4. CVar defaults

- Game: `g_unlock_updateAngles`, `g_unlock_updateViewpos`, `g_unlock_interpolateMoving` and `g_unlock_viewStyle` all default to 1.
- Engine (`etqw.x86`): `com_unlockFPS` 1, `com_unlock_maxFPS` 60, `com_unlock_timingMethod` 2, `com_unlock_avgFrames` 4, `com_unlock_safetyMargin` 0.1, `r_swapInterval` 0 (vsync off).
- OpenETQW registers the same defaults. The three timing CVars (`com_unlock_timingMethod`, `com_unlock_avgFrames`, `com_unlock_safetyMargin`) do nothing in OpenETQW; M5 plan D9 removes them. `r_swapInterval` selects the Vulkan present mode.

## 5. The 1.5 data against the DVD data

Until 2026-09-25 OpenETQW ran on the unpatched English DVD paks. The 1.5 patch paks (`pak005`–`pak008`, `zpak_english001`–`003`) supply 2,121 files: 139 new, and 1,982 that override DVD files (1,273 changed, 709 shipped again unchanged).

- **Scripts.** The repository's SDK scripts (`src/compiledscript/scripts`) are the 1.5 scripts. 197 of 204 files are identical to the 1.5 tree, 6 differ only in the `scripts/` versus `script/` include prefix, and `maps/etqwmap.script` has no retail counterpart. Only 92 match the DVD scripts.
  - `events.script` declares 885 distinct events in 1.5 and 905 on the DVD. The 68 DVD-only events are exactly the stubs OpenETQW had added (`EV_ScriptMissing_*`), and 48 events exist only in 1.5.
- **Script-event signatures.** Retail 1.5 uses the SDK forms, confirmed by the symbols:
  - `sendNetworkEvent( float clientIndex, boolean isRepeaterClient, string message )`, whose `idEntity` handler (0x107e6e) also runs the event for the local player;
  - the objective manager's `OnWriteInitialReliableMessages( float, float )` (0x4572f1);
  - `setPlayerPickupRequestTime( entity )` (0x3d00be);
  - `broadcastToolTip` with `wstring` arguments (0x4e3fe2);
  - `heal( float )`, `repair( float )`, `allocEntityTask( float, entity )`.

  The DVD scripts declare older forms. OpenETQW's PR #1 had fitted the handlers to those, which breaks on 1.5 (ledger 050).
- **`player::OnNewTask`** exists in 1.5 (`player/player.script`, empty body) and not on the DVD (ledger 030).
- **Maps.** The entity counts change on ark (899 → 897 classnames) and volcano (1336 → 1332); the other maps keep their counts (quarry 415) with changed keys.
  - Player AAS areas/reach: ark 7207/38159, canyon 10673/53160, quarry 8848/45839, valley 9379/47595.
  - Vehicle AAS: ark 4489/20305, quarry 5417/22084, valley 6367/25981. Every 1.5 AAS file keeps format 2.2.
  - `.nav` graphs: five differ from the DVD. Area22 (83 → 93 nodes), island (50 → 68), sewer (58, unchanged) and valley (164 → 183) move from version 7 to version 8; ark stays version 7 and gains a node (71 → 72). Volcano ships one (version 8, 14 nodes); the DVD had none.
- **Unchanged:** no 1.5 pak ships `default.cfg`; `pakmeta.conf` is only in `pak000`; the binary formats are the same (procb `mapProcFile010`, collision 2.70, decl binaries).
- Retail 1.5 ran the scripts as compiled-script modules (`compiledscriptx86.dll` and its macOS and Linux equivalents); OpenETQW interprets them. Differences were not examined.

## 6. Not examined

- The Linux and Windows modules beyond the `validClasses` reads (their unlock code is assumed to match macOS: same CVar strings, same SDK).
- The retail engine's listen-host frame loop and timing methods 1 and 2.
- The DVD (pre-1.5) game module.
