#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""A remote client that hits an error in a map leaves the server cleanly.

One dedicated server (valley) and one headless remote client (the dedicated
binary started with +connect) with a deliberately broken GUI in its save path
(guis/oetqw_test_broken.gui, as test_gui_load_error.py writes). Once the
client receives snapshots, 'testGUI oetqw_test_broken' on the client fails to
parse: common->Error stops the session (the map is shut down) and the GUI
loader passes the error on (game #50). Then:
1. The client must print the ERROR banner and 'Game Map Shutdown', and the
   server must drop it ('client <n> dropped: disconnected'): the session's
   Stop disconnects the async client, as Doom 3's does. Before engine #116 the
   client stayed connected and kept predicting and reading snapshots on an
   unloaded game.
2. Over the next 5 s the client must receive no snapshot.
3. 'reconnect' must bring it back: a new game init from the server and
   snapshots again.
4. 'disconnect' must leave the server the same way (dropped: disconnected,
   no snapshots after).
Both processes must still be running, exit 0 on 'quit' and log no crash or
fatal marker.

With --at load the error comes during the client's load of the server's map
instead: the Vulkan client (--client-binary; the headless binary loads no HUD
GUIs) connects with a broken 'gui scoreboard' decl in its save path, which
the game loads per map (sdPlayerProperties::InitGUIs, from OnMapStart). The
game swallows that error (no map was torn down, game #50), but the session
was stopped, which left the server: the load must be abandoned as a drop
('the load of maps/<map>.world was stopped'), the server must drop the client
('disconnected'), and the client must stay up, read no snapshots and exit 0
on 'quit'. Before the fix the load finished as a game of the client's own.

Exit 77 when ETQW_DATA_PATH is unset/invalid or the binary is missing.
"""

import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402
from test_campaign_client_maps import find_client  # noqa: E402
from test_client_network import VulkanClient  # noqa: E402
from test_gui_load_error import BROKEN_GUI, FATAL  # noqa: E402
from test_reconnect_restart import Failed, wait_snapshots  # noqa: E402
from test_remote_client_map_change import GAME_INIT_RE, RemoteClient, snapshots  # noqa: E402

PASSED_ON = "dropped the map; passing it on"
# the same broken property in a stock GUI that every map load creates
BROKEN_SCOREBOARD = BROKEN_GUI.replace("gui oetqw_test_broken", "gui scoreboard")
ABANDONED = "was stopped: the client left the server"


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ded-binary")
    ap.add_argument("--client-binary", help="the Vulkan client, for --at load")
    ap.add_argument("--at", choices=("map", "load"), default="map")
    ap.add_argument("--map", default="valley")
    ap.add_argument("--port", type=int, default=29996)
    ap.add_argument("--log-dir")
    args = ap.parse_args(argv)

    try:
        binary = h.find_ded_binary(args.ded_binary)
        data = h.resolve_data_path()
        vk = find_client(args.client_binary) if args.at == "load" else None
    except h.SkipTest as e:
        print(f"[SKIP] {e}")
        return 77
    h.install_signal_handlers()
    log_dir = Path(args.log_dir) if args.log_dir else None
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)

    if args.at == "load":
        return run_load(binary, vk, data, args, log_dir)

    failures = []
    srv = h.DedicatedServer(binary, data, args.map, args.port,
                            log_path=(log_dir / "server.log") if log_dir else None,
                            cvars={"si_rules": "sdGameRulesObjective", "si_pure": "0"})
    cl = RemoteClient(binary, data, args.port, args.port + 1, log_path=(log_dir / "client.log") if log_dir else None)
    cl.name = "RemoteClient"
    gui_dir = Path(cl.savepath) / "base" / "guis"
    gui_dir.mkdir(parents=True, exist_ok=True)
    (gui_dir / "oetqw_test_broken.gui").write_text(BROKEN_GUI, encoding="utf-8", newline="\n")
    try:
        srv.start()
        if not srv.wait_for_text("Spawning entities", 180.0):
            print("FAIL: server never loaded the map")
            return 1
        srv.sync()
        mark = srv.mark()
        cl.start()
        hit = srv.wait_for(lambda s: GAME_INIT_RE.search(s), 120.0, mark)
        if not hit:
            print("FAIL: the server never sent the client a game init")
            return 1
        slot = int(hit[2].group(1))
        wait_snapshots(cl, 0)
        print(f"client {slot} receives snapshots")
        time.sleep(2)

        # 1. the error in the map
        srv_mark, cl_mark = srv.mark(), cl.mark()
        cl.send("testGUI oetqw_test_broken")
        dropped_re = re.compile(rf"client {slot} dropped: (.*)")
        dropped = srv.wait_for(lambda s: dropped_re.search(s), 15.0, srv_mark)
        # the loader's 'passing it on' is the last line of the error
        cl.wait_for(lambda s: PASSED_ON in s or None, 15.0, cl_mark)
        lines = [ln.text for ln in cl.snapshot(cl_mark)]
        banner = any(t.startswith("ERROR:") and "oetqwNoSuchScope" in t for t in lines)
        shutdown = any("Game Map Shutdown" in t for t in lines)
        passed_on = any(PASSED_ON in t for t in lines)
        print(f"[error] client: error banner {banner}, map shut down {shutdown}, passed on {passed_on}; "
              f"server: {dropped[2].group(0) if dropped else 'client not dropped'}")
        if not banner:
            failures.append("no ERROR banner for the broken GUI on the client")
        if not shutdown:
            failures.append("the error did not shut the client's map down")
        if not passed_on:
            failures.append("the GUI loader did not pass the client's error on")
        if not dropped:
            failures.append(f"the server did not drop client {slot} within 15 s of its error")
        elif dropped[2].group(1).strip() != "disconnected":
            failures.append(f"the server dropped client {slot} for '{dropped[2].group(1).strip()}', not a disconnect")

        # 2. no snapshots after the drop
        quiet = cl.mark()
        time.sleep(5)
        late = snapshots(cl, quiet)
        print(f"[after] snapshots received in the 5 s after the drop: {len(late)}")
        if late:
            failures.append(f"the client received {len(late)} snapshots after its error dropped the map")
        if not cl.alive() or not srv.alive():
            failures.append("a process died after the client's error")
            return report(failures)

        # 3. reconnect
        srv_mark, cl_mark = srv.mark(), cl.mark()
        cl.send("reconnect")
        again = srv.wait_for(lambda s: GAME_INIT_RE.search(s), 120.0, srv_mark)
        if not again:
            failures.append("after 'reconnect' the server sent no game init")
        else:
            try:
                got = wait_snapshots(cl, cl_mark)
                print(f"[reconnect] client {again[2].group(1)}; snapshots from {got[0]}")
            except Failed as e:
                failures.append(f"after 'reconnect': {e}")
            else:
                # 4. the disconnect command leaves the server the same way
                slot = int(again[2].group(1))
                dropped_re = re.compile(rf"client {slot} dropped: (.*)")
                srv_mark = srv.mark()
                cl.send("disconnect")
                gone = srv.wait_for(lambda s: dropped_re.search(s), 15.0, srv_mark)
                quiet = cl.mark()
                time.sleep(3)
                late = snapshots(cl, quiet)
                print(f"[disconnect] server: {gone[2].group(0) if gone else 'client not dropped'}; "
                      f"snapshots after: {len(late)}")
                if not gone or gone[2].group(1).strip() != "disconnected":
                    failures.append(f"'disconnect' did not disconnect client {slot} from the server")
                if late:
                    failures.append(f"the client received {len(late)} snapshots after 'disconnect'")

        for proc, who in ((cl, "client"), (srv, "server")):
            code = proc.quit()
            print(f"{who} quit exit code: {code}")
            if code != 0:
                failures.append(f"{who} quit exit code {code}")
            for ln in proc.snapshot():
                if any(marker in ln.text for marker in FATAL):
                    failures.append(f"{who} log: {ln.text}")
                    break
    except Failed as e:
        failures.append(str(e))
    finally:
        cl.cleanup()
        srv.cleanup()
    return report(failures)


def run_load(binary, vk, data, args, log_dir):
    failures = []
    srv = h.DedicatedServer(binary, data, args.map, args.port,
                            log_path=(log_dir / "server.log") if log_dir else None,
                            cvars={"si_rules": "sdGameRulesObjective", "si_pure": "0"})
    cl = VulkanClient(vk, data, args.port + 1, connect_to=f"127.0.0.1:{args.port}",
                      log_path=(log_dir / "client.log") if log_dir else None,
                      cvars={"r_vkValidation": "0", "com_maxFPS": "60", "si_pure": "0",
                             "net_clientShowSnapshots": "1"})
    gui_dir = Path(cl.savepath) / "base" / "guis"
    gui_dir.mkdir(parents=True, exist_ok=True)
    (gui_dir / "oetqw_test_scoreboard.gui").write_text(BROKEN_SCOREBOARD, encoding="utf-8", newline="\n")
    try:
        srv.start()
        if not srv.wait_for_text("Spawning entities", 180.0):
            print("FAIL: server never loaded the map")
            return 1
        srv.sync()
        srv_mark = srv.mark()
        cl.start()
        hit = srv.wait_for(lambda s: GAME_INIT_RE.search(s), 180.0, srv_mark)
        if not hit:
            print("FAIL: the server never sent the client a game init")
            return 1
        slot = int(hit[2].group(1))
        dropped_re = re.compile(rf"client {slot} dropped: (.*)")
        dropped = srv.wait_for(lambda s: dropped_re.search(s), 180.0, srv_mark)
        cl.wait_for(lambda s: ABANDONED in s or None, 30.0)
        time.sleep(2)
        lines = [ln.text for ln in cl.snapshot()]
        gui_error = any(t.startswith("ERROR:") and "oetqwNoSuchScope" in t for t in lines)
        abandoned = any(t.startswith("ERROR:") and ABANDONED in t for t in lines)
        print(f"[load] client {slot}: GUI error {gui_error}, load abandoned {abandoned}; "
              f"server: {dropped[2].group(0) if dropped else 'client not dropped'}")
        if not gui_error:
            failures.append("no ERROR for the broken scoreboard GUI during the client's map load")
        if not abandoned:
            failures.append("the client's map load was not abandoned after the error stopped the session")
        if not dropped:
            failures.append(f"the server did not drop client {slot}")
        elif dropped[2].group(1).strip() != "disconnected":
            failures.append(f"the server dropped client {slot} for '{dropped[2].group(1).strip()}', not a disconnect")

        quiet = cl.mark()
        time.sleep(5)
        late = snapshots(cl, quiet)
        print(f"[after] snapshots received in the 5 s after: {len(late)}")
        if late:
            failures.append(f"the client received {len(late)} snapshots after its load was abandoned")
        if not cl.alive() or not srv.alive():
            failures.append("a process died after the client's load error")
            return report(failures)

        for proc, who in ((cl, "client"), (srv, "server")):
            code = proc.quit()
            print(f"{who} quit exit code: {code}")
            if code != 0:
                failures.append(f"{who} quit exit code {code}")
            for ln in proc.snapshot():
                if any(marker in ln.text for marker in FATAL):
                    failures.append(f"{who} log: {ln.text}")
                    break
    finally:
        cl.cleanup()
        srv.cleanup()
    return report(failures)


def report(failures):
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nPASS: a remote client's error in a map or its load disconnects it cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
