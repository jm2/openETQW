#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Key turning is per second, not per rendered frame.

One dedicated server and one headless remote client (the dedicated binary with
+connect, as test_remote_client_map_change.py does). The client holds +left for
a fixed time with a 30 fps and then a 240 fps frame cap (com_maxFPS), and +right
at 240 fps (each step is then small, so quantizing the angle every call would
show up as a direction-dependent rate), turning
at in_yawSpeed degrees a second (in_angleSpeedKey, the walking multiplier, is 1); the server reads the yaw of the client's
usercmds (playerPos ... yaw=). Both runs must turn at that rate. The client
also builds a usercmd on every render-only frame (late view-angle sampling),
and each of those used to add a whole tick of turning, so the rate grew with
the frame rate and those angles reached the server.

--listen: the Vulkan client hosts the map instead (+spawnServer, a listen server;
its own player is client 0), joins GDF and turns its own player with the same keys
at the same caps, and the host's playerPos 0 reads the yaw of the usercmds the
server simulates (LocalClientInput builds one a tick). Both runs must turn at the
same rate: the host's authoritative turn rate must not scale with the cap either,
also once M5 step 7 has the host build a usercmd on its render-only frames, as the
client does. The host logs its frame timing (com_logFrameTiming), and a hold at
cap 240 must run at least MIN_FRAMES_PER_TICK frames a tick, or it turned through
no render-only frame. It also logs its draws (g_logFramePoses, a cheat, so the host
sets net_allowCheats 1), and the unlocked ones during each hold (FRAME_POSE ud=1:
ClientUpdateView ran on the draw's frame) are counted: 0 before step 7, which never
builds a render-only usercmd on the host; reported, and asserted by step 7. The
Vulkan client opens a window and runs from the repository root.

Exit 77 when ETQW_DATA_PATH is unset/invalid or the binary is missing.
"""

import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402
from test_campaign_client_maps import BAD_MARKERS, find_client  # noqa: E402
from test_client_network import VulkanClient  # noqa: E402
from test_reconnect_restart import Failed, spawn  # noqa: E402
import test_frame_poses as frame_poses  # noqa: E402

READY = "------------- ETQW initialized -------------"
SNAPSHOT_RE = re.compile(r"Client received snapshot (\d+)")
GAME_INIT_RE = re.compile(r"Sending game init to client (\d+)")
FRAME_TIMING_RE = re.compile(r"FRAME_TIMING: window_ms=([\d.]+) frames=(\d+) ticks=(\d+) cap=(-?\d+)")
YAW_SPEED = 20.0      # degrees a second: slow enough that a sample never wraps past 180
HOLD = 2.0            # seconds the turn key is held
TOLERANCE = 0.10      # rate may differ from YAW_SPEED by this fraction
TURN_CVARS = {"in_yawspeed": str(YAW_SPEED), "in_anglespeedkey": "1"}
MIN_FRAMES_PER_TICK = 2.0   # --listen: a hold above cap 30 must run this many frames a tick


class RemoteClient(h.DedicatedServer):
    """The dedicated binary as a headless client: +connect instead of +spawnServer."""

    def __init__(self, binary, data, server_port, port, log_path=None):
        super().__init__(binary, data, "", port, log_path=log_path,
                         cvars={"si_pure": "0", "ui_name": "TurnClient", "net_clientShowSnapshots": "1", **TURN_CVARS})
        self.server_port = server_port

    def command_line(self):
        args = super().command_line()
        assert args[-2] == "+spawnServer"
        return args[:-2] + ["+connect", f"127.0.0.1:{self.server_port}"]


def usercmd_yaw(srv, client_num):
    for ln in srv.command(f"playerPos {client_num}") or []:
        pos = h.parse_player_pos(ln.text)
        if pos is not None:
            return pos["yaw"]
    return None


def frames_per_tick(proc, begin, end, cap):
    """Frames a game tick over the FRAME_TIMING windows proc printed between two line
    indices at this cap (None without one): how many frames each tick of the hold ran."""
    windows = [m for ln in proc.snapshot(begin, end) if (m := FRAME_TIMING_RE.search(ln.text)) and int(m.group(4)) == cap]
    ticks = sum(int(m.group(3)) for m in windows)
    return sum(int(m.group(2)) for m in windows) / ticks if ticks else None


def unlocked_draws(proc, begin, end):
    """(unlocked draws, all draws) that FRAME_POSE logged between two line indices: ud=1
    is a draw whose frame called ClientUpdateView, which builds a render-only usercmd."""
    poses = [p for ln in proc.snapshot(begin, end) if (p := frame_poses.parse_pose(ln.text))]
    return sum(1 for p in poses if p["ud"] == 1), len(poses)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ded-binary")
    ap.add_argument("--client-binary", help="the Vulkan client (--listen)")
    ap.add_argument("--listen", action="store_true", help="the Vulkan client hosts a listen server and turns its own player")
    ap.add_argument("--map", default="quarry")
    ap.add_argument("--port", type=int, default=29990)
    ap.add_argument("--log-dir")
    args = ap.parse_args(argv)
    try:
        binary = find_client(args.client_binary) if args.listen else h.find_ded_binary(args.ded_binary)
        data = h.resolve_data_path()
    except h.SkipTest as e:
        print(f"[SKIP] {e}")
        return 77
    h.install_signal_handlers()
    log_dir = Path(args.log_dir) if args.log_dir else None
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)

    failures = []
    if args.listen:
        # the host is the server: it reads the yaw of its own player's usercmds. r_vkValidation 0
        # and no vsync, so cap 240 is reached (frame-poses measures the same way)
        srv = cl = VulkanClient(binary, data, args.port, map_name=args.map,
                                log_path=(log_dir / "host.log") if log_dir else None,
                                cvars={"si_rules": "sdGameRulesObjective", "si_pure": "0", "ui_name": "TurnHost",
                                       "com_unlockFPS": "1", "r_swapInterval": "0", "r_vkValidation": "0",
                                       "com_logFrameTiming": "1", "net_allowCheats": "1", "g_logFramePoses": "1",
                                       **TURN_CVARS})
        cl.name, cl.slot = "Host", 0
        procs = [("host", cl)]
    else:
        srv = h.DedicatedServer(binary, data, args.map, args.port,
                                log_path=(log_dir / "server.log") if log_dir else None,
                                cvars={"si_rules": "sdGameRulesObjective", "si_pure": "0"})
        cl = RemoteClient(binary, data, args.port, args.port + 1,
                          log_path=(log_dir / "client.log") if log_dir else None)
        procs = [("client", cl), ("server", srv)]
    try:
        if args.listen:
            cl.start()
            if not cl.wait_for_text(READY, 180.0) or not cl.wait_for_text(f"Map: maps/{args.map}", 180.0):
                print("FAIL: the listen server never loaded the map")
                return 1
            cl.sync(60.0)
            client_num = 0
            try:
                # on a team with the limbo menu closed, so the keys turn the player
                spawn(srv, cl, "gdf", timeout=60.0)
            except Failed as e:
                print(f"FAIL: {e}")
                return 1
        else:
            srv.start()
            if not srv.wait_for_text(READY, 180.0):
                print("FAIL: server never became ready")
                return 1
            srv.sync()
            cl.start()
            hit = srv.wait_for(lambda s: GAME_INIT_RE.search(s), 120.0)
            client_num = int(hit[2].group(1)) if hit else None
            if client_num is None or not cl.wait_for(lambda s: SNAPSHOT_RE.search(s), 180.0):
                print("FAIL: the client never connected and received a snapshot")
                return 1
        if usercmd_yaw(srv, client_num) is None:
            print("FAIL: playerPos prints no yaw= (game module too old?)")
            return 1

        for fps, key in ((30, "left"), (240, "left"), (240, "right")):
            cl.send(f"com_maxFPS {fps}")
            time.sleep(1.0)
            before = usercmd_yaw(srv, client_num)
            begin = cl.mark()
            cl.send(f"+{key}")
            start = time.monotonic()
            time.sleep(HOLD)
            cl.send(f"-{key}")
            held = time.monotonic() - start
            end = cl.mark()
            time.sleep(0.5)     # the last usercmds reach the server
            after = usercmd_yaw(srv, client_num)
            label = f"{fps} fps +{key}"
            if args.listen:
                # each frame of the hold that ran no tick is one the host could sample input on
                per_tick = frames_per_tick(cl, begin, end, fps)
                print(f"[{label}] the host ran " + (f"{per_tick:.2f} frames a tick" if per_tick else "no timed frame")
                      + " during the hold (FRAME_TIMING)")
                if fps > 30 and (per_tick is None or per_tick < MIN_FRAMES_PER_TICK):
                    failures.append(f"{label}: the host ran {per_tick or 0:.2f} frames a tick during the hold"
                                    f" (need >= {MIN_FRAMES_PER_TICK}): no render-only frames to turn through")
                # whether those frames built a render-only usercmd (ClientUpdateView): none before
                # M5 step 7, which asserts it; reported here
                unlocked, drawn = unlocked_draws(cl, begin, end)
                print(f"[{label}] {unlocked} of {drawn} draws during the hold were unlocked (FRAME_POSE ud=1)")
                if drawn == 0:
                    failures.append(f"{label}: no FRAME_POSE draw during the hold (g_logFramePoses refused?)")
            if before is None or after is None:
                failures.append(f"{label}: no yaw from playerPos")
                continue
            # left increases yaw, right decreases it
            turned = ((after - before) if key == "left" else (before - after)) % 360.0
            rate = turned / held
            print(f"[{label}] yaw {before:.2f} -> {after:.2f}: turned {turned:.1f} degrees in {held:.2f} s"
                  f" = {rate:.1f} degrees/s (in_yawSpeed {YAW_SPEED})")
            if abs(rate - YAW_SPEED) > TOLERANCE * YAW_SPEED:
                failures.append(f"{label}: turned at {rate:.1f} degrees/s, expected {YAW_SPEED} +/- {TOLERANCE:.0%}")

        codes = {name: proc.quit() for name, proc in procs}
        if any(c != 0 for c in codes.values()):
            failures.append("quit exit codes: " + ", ".join(f"{name} {c}" for name, c in codes.items()))
        for name, proc in procs:
            for b in h.scan_failures(l.text for l in proc.snapshot()):
                failures.append(f"{name} log: {b}")
            if args.listen:
                failures += [f"{name} log line {i + 1}: {ln.text}" for i, ln in enumerate(proc.snapshot())
                             if any(m in ln.text for m in BAD_MARKERS)]
    finally:
        for _, proc in procs:
            proc.cleanup()

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    where = "on the listen host" if args.listen else "on a remote client"
    print(f"\nPASS: key turning {where} runs at {YAW_SPEED} degrees/s at 30 and 240 fps, both ways")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
