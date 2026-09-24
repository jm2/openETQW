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

Exit 77 when ETQW_DATA_PATH is unset/invalid or the binary is missing.
"""

import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402

READY = "------------- ETQW initialized -------------"
SNAPSHOT_RE = re.compile(r"Client received snapshot (\d+)")
GAME_INIT_RE = re.compile(r"Sending game init to client (\d+)")
YAW_SPEED = 20.0      # degrees a second: slow enough that a sample never wraps past 180
HOLD = 2.0            # seconds the turn key is held
TOLERANCE = 0.10      # rate may differ from YAW_SPEED by this fraction


class RemoteClient(h.DedicatedServer):
    """The dedicated binary as a headless client: +connect instead of +spawnServer."""

    def __init__(self, binary, data, server_port, port, log_path=None):
        super().__init__(binary, data, "", port, log_path=log_path,
                         cvars={"si_pure": "0", "ui_name": "TurnClient", "net_clientShowSnapshots": "1",
                                "in_yawspeed": str(YAW_SPEED), "in_anglespeedkey": "1"})
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


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ded-binary")
    ap.add_argument("--map", default="quarry")
    ap.add_argument("--port", type=int, default=29990)
    ap.add_argument("--log-dir")
    args = ap.parse_args(argv)
    try:
        binary = h.find_ded_binary(args.ded_binary)
        data = h.resolve_data_path()
    except h.SkipTest as e:
        print(f"[SKIP] {e}")
        return 77
    h.install_signal_handlers()
    log_dir = Path(args.log_dir) if args.log_dir else None
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)

    failures = []
    srv = h.DedicatedServer(binary, data, args.map, args.port,
                            log_path=(log_dir / "server.log") if log_dir else None,
                            cvars={"si_rules": "sdGameRulesObjective", "si_pure": "0"})
    cl = RemoteClient(binary, data, args.port, args.port + 1,
                      log_path=(log_dir / "client.log") if log_dir else None)
    try:
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
            cl.send(f"+{key}")
            start = time.monotonic()
            time.sleep(HOLD)
            cl.send(f"-{key}")
            held = time.monotonic() - start
            time.sleep(0.5)     # the last usercmds reach the server
            after = usercmd_yaw(srv, client_num)
            label = f"{fps} fps +{key}"
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

        crc = cl.quit()
        src = srv.quit()
        if src != 0 or crc != 0:
            failures.append(f"quit exit codes: server {src}, client {crc}")
        for name, proc in (("server", srv), ("client", cl)):
            for b in h.scan_failures(l.text for l in proc.snapshot()):
                failures.append(f"{name} log: {b}")
    finally:
        cl.cleanup()
        srv.cleanup()

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"\nPASS: key turning runs at {YAW_SPEED} degrees/s at 30 and 240 fps, both ways")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
