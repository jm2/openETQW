#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""A remote client the server lets go leaves its game, as Doom 3's does (#119, #120).

Until #119/#120 the server never told a client it was dropped: a kicked or
timed-out client, or the clients of a listen host that ran 'disconnect', kept
their map loaded until their own 40 s timeout, and the client's timeout (or a
disconnect message) left the map loaded with the async client inactive. Now
the server sends SERVER_RELIABLE_MESSAGE_DISCONNECT with the reason, and the
client stops the session (the map is shut down) and opens the menu.

--case kick: a dedicated server (valley) and a headless remote client (the
  dedicated binary started with +connect). 'kick <slot>' on the server: the
  client must print 'Disconnected by server: <reason>' and 'Game Map Shutdown'
  within 5 s, receive no snapshot over the next 5 s, and 'reconnect' must bring
  it back (a game init and snapshots).
--case timeout: the same pair, net_serverClientTimeout 3 on the server.
  1. The client is stopped (SIGSTOP) for 6 s: the server times it out, and on
     resuming the client must be told ('Disconnected by server: timed out'),
     shut its map down and receive no snapshots; then 'reconnect'.
  2. The server is stopped for 6 s with net_clientServerTimeout 3 on the
     client: the client must print 'Server connection timed out' and shut its
     map down; with the server resumed, 'reconnect' must work again.
--case listen: the Vulkan client hosts a listen server (+spawnServer valley)
  and the headless client joins it. 'disconnect' on the host must end its
  server too: the remote client is told ('Disconnected by server: server shut
  down') within 5 s instead of timing out, shuts its map down and receives no
  snapshots; 'kick 1' on the host then says 'server is not running'. The
  remote client must then join a second (dedicated) server and receive
  snapshots there.
Every process must exit 0 on 'quit' and log no crash or fatal marker.

Exit 77 when ETQW_DATA_PATH is unset/invalid, a binary is missing, or (--case
timeout) the platform has no SIGSTOP.
"""

import argparse
import os
import re
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402
from test_campaign_client_maps import find_client  # noqa: E402
from test_client_network import READY, VulkanClient  # noqa: E402
from test_gui_load_error import FATAL  # noqa: E402
from test_reconnect_restart import Failed, wait_snapshots  # noqa: E402
from test_remote_client_map_change import GAME_INIT_RE, RemoteClient, snapshots  # noqa: E402

TOLD_RE = re.compile(r"Disconnected by server: (.*)")
SHUTDOWN = "Game Map Shutdown"
TIMED_OUT = "Server connection timed out"
TOLD_WITHIN = 5.0  # well inside the client's own 40 s timeout: the server told it


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ded-binary")
    ap.add_argument("--client-binary", help="the Vulkan client, for --case listen")
    ap.add_argument("--case", choices=("kick", "timeout", "listen"), default="kick")
    ap.add_argument("--map", default="valley")
    ap.add_argument("--port", type=int, default=29944)  # meson: client-server-kick
    ap.add_argument("--log-dir")
    args = ap.parse_args(argv)

    try:
        binary = h.find_ded_binary(args.ded_binary)
        data = h.resolve_data_path()
        vk = find_client(args.client_binary) if args.case == "listen" else None
    except h.SkipTest as e:
        print(f"[SKIP] {e}")
        return 77
    if args.case == "timeout" and not hasattr(signal, "SIGSTOP"):
        print("[SKIP] no SIGSTOP on this platform")
        return 77
    h.install_signal_handlers()
    log_dir = Path(args.log_dir) if args.log_dir else None
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)

    def log(name):
        return (log_dir / f"{name}.log") if log_dir else None

    if args.case == "listen":
        return run_listen(binary, vk, data, args, log)

    failures = []
    srv_cvars = {"si_rules": "sdGameRulesObjective", "si_pure": "0"}
    if args.case == "timeout":
        srv_cvars["net_serverClientTimeout"] = "3"
    srv = h.DedicatedServer(binary, data, args.map, args.port, log_path=log("server"), cvars=srv_cvars)
    cl = RemoteClient(binary, data, args.port, args.port + 1, log_path=log("client"))
    cl.name = "RemoteClient"
    procs = [("client", cl), ("server", srv)]
    try:
        srv.start()
        if not srv.wait_for_text("Spawning entities", 180.0):
            raise Failed("the server never loaded the map")
        srv.sync()
        slot = join(srv, cl, srv.mark())

        if args.case == "kick":
            srv_mark, cl_mark = srv.mark(), cl.mark()
            srv.send(f"kick {slot}")
            dropped = srv.wait_for(lambda s: re.search(rf"client {slot} dropped: (.*)", s), 15.0, srv_mark)
            print(f"[kick] server: {dropped[2].group(0) if dropped else 'client not dropped'}")
            if not dropped:
                failures.append(f"'kick {slot}' did not drop the client")
            check_told(cl, cl_mark, None, "kick", failures)
            check_quiet(cl, "kick", failures)
            slot = rejoin(srv, cl, "kick", failures)
        else:
            # 1. the server times the client out
            srv_mark, cl_mark = srv.mark(), cl.mark()
            pause(cl, 6.0)
            dropped = srv.wait_for(lambda s: re.search(rf"client {slot} dropped: (.*)", s), 5.0, srv_mark)
            print(f"[server timeout] server: {dropped[2].group(0) if dropped else 'client not dropped'}")
            if not dropped or dropped[2].group(1).strip() != "timed out":
                failures.append(f"the server did not time client {slot} out while it was stopped")
            check_told(cl, cl_mark, "timed out", "server timeout", failures)
            check_quiet(cl, "server timeout", failures)
            slot = rejoin(srv, cl, "server timeout", failures)

            # 2. the client times the server out
            if slot is not None:
                cl.send("set net_clientServerTimeout 3")
                cl.sync()
                cl_mark = cl.mark()
                pause(srv, 6.0)
                cl.wait_for(lambda s: SHUTDOWN in s or None, 10.0, cl_mark)
                lines = [ln.text for ln in cl.snapshot(cl_mark)]
                timed_out = any(TIMED_OUT in t for t in lines)
                shutdown = any(SHUTDOWN in t for t in lines)
                print(f"[client timeout] client: timed out {timed_out}, map shut down {shutdown}")
                if not timed_out:
                    failures.append("the client did not time the stopped server out")
                if not shutdown:
                    failures.append("the client's timeout did not shut its map down")
                check_quiet(cl, "client timeout", failures)
                cl.send("set net_clientServerTimeout 40")
                rejoin(srv, cl, "client timeout", failures)
        if not cl.alive() or not srv.alive():
            failures.append("a process died")
    except Failed as e:
        failures.append(str(e))
    finally:
        finish(procs, failures)
    return report(failures, args.case)


def run_listen(binary, vk, data, args, log):
    failures = []
    host = VulkanClient(vk, data, args.port, map_name=args.map, log_path=log("host"),
                        cvars={"r_vkValidation": "0", "com_maxFPS": "60", "si_pure": "0",
                               "si_rules": "sdGameRulesObjective"})
    cl = RemoteClient(binary, data, args.port, args.port + 1, log_path=log("client"))
    cl.name = "RemoteClient"
    other = h.DedicatedServer(binary, data, args.map, args.port + 2, log_path=log("server"),
                              cvars={"si_rules": "sdGameRulesObjective", "si_pure": "0"})
    procs = [("client", cl), ("host", host), ("server", other)]
    try:
        host.start()
        other.start()
        if not host.wait_for_text(READY, 180.0) or not host.wait_for_text(f"Map: maps/{args.map}", 180.0):
            raise Failed("the listen server never loaded the map")
        host.sync(60.0)
        if not other.wait_for_text("Spawning entities", 180.0):
            raise Failed("the second server never loaded the map")
        other.sync()
        join(host, cl, host.mark())

        host_mark, cl_mark = host.mark(), cl.mark()
        host.send("disconnect")
        check_told(cl, cl_mark, "server shut down", "listen disconnect", failures)
        check_quiet(cl, "listen disconnect", failures)
        host.sync(30.0)
        reply = host.command("kick 1")
        stopped = reply is not None and any("server is not running" in ln.text for ln in reply)
        print(f"[listen disconnect] host server stopped: {stopped}")
        if not stopped:
            failures.append("after 'disconnect' the listen host's server still runs")
        if any("dropped: timed out" in ln.text for ln in host.snapshot(host_mark)):
            failures.append("the host timed its remote client out instead of telling it")

        # the remote client joins another server
        other_mark, cl_mark = other.mark(), cl.mark()
        cl.send(f"connect 127.0.0.1:{args.port + 2}")
        hit = other.wait_for(lambda s: GAME_INIT_RE.search(s), 120.0, other_mark)
        if not hit:
            failures.append("the remote client could not join a second server after the host's disconnect")
        else:
            got = wait_snapshots(cl, cl_mark)
            print(f"[elsewhere] client {hit[2].group(1)} on the second server; snapshots from {got[0]}")
        if not cl.alive() or not host.alive() or not other.alive():
            failures.append("a process died")
    except Failed as e:
        failures.append(str(e))
    finally:
        finish(procs, failures)
    return report(failures, "listen")


def join(srv, cl, mark):
    """Start the client and wait for its game init (on the server, from line mark) and snapshots; its slot."""
    cl.start()
    hit = srv.wait_for(lambda s: GAME_INIT_RE.search(s), 180.0, mark)
    if not hit:
        raise Failed("the server never sent the client a game init")
    slot = int(hit[2].group(1))
    wait_snapshots(cl, 0)
    print(f"client {slot} receives snapshots")
    time.sleep(2)
    return slot


def pause(proc, seconds):
    os.kill(proc.proc.pid, signal.SIGSTOP)
    try:
        time.sleep(seconds)
    finally:
        os.kill(proc.proc.pid, signal.SIGCONT)


def check_told(cl, cl_mark, reason, what, failures):
    """The client was told it was dropped (with the reason) and shut its map down, in TOLD_WITHIN s."""
    start = time.time()
    told = cl.wait_for(lambda s: TOLD_RE.search(s), TOLD_WITHIN + 6.0, cl_mark)
    cl.wait_for(lambda s: SHUTDOWN in s or None, 5.0, cl_mark)
    took = time.time() - start
    shutdown = any(SHUTDOWN in ln.text for ln in cl.snapshot(cl_mark))
    print(f"[{what}] client: {told[2].group(0) if told else 'not told'} ({took:.1f} s), map shut down {shutdown}")
    if not told:
        failures.append(f"{what}: the client was not told it was dropped")
    elif reason is not None and told[2].group(1).strip() != reason:
        failures.append(f"{what}: the client was told '{told[2].group(1).strip()}', not '{reason}'")
    if not shutdown:
        failures.append(f"{what}: the client's map was not shut down")


def check_quiet(cl, what, failures):
    quiet = cl.mark()
    time.sleep(5)
    late = snapshots(cl, quiet)
    print(f"[{what}] snapshots received in the 5 s after: {len(late)}")
    if late:
        failures.append(f"{what}: the client received {len(late)} snapshots after it was dropped")


def rejoin(srv, cl, what, failures):
    srv_mark, cl_mark = srv.mark(), cl.mark()
    cl.send("reconnect")
    hit = srv.wait_for(lambda s: GAME_INIT_RE.search(s), 120.0, srv_mark)
    if not hit:
        failures.append(f"{what}: after 'reconnect' the server sent no game init")
        return None
    try:
        got = wait_snapshots(cl, cl_mark)
    except Failed as e:
        failures.append(f"{what}: after 'reconnect': {e}")
        return None
    print(f"[{what}] reconnected as client {hit[2].group(1)}; snapshots from {got[0]}")
    time.sleep(2)
    return int(hit[2].group(1))


def finish(procs, failures):
    for who, proc in procs:  # clients first
        if proc.proc is None:
            continue
        code = proc.quit() if proc.alive() else None
        print(f"{who} quit exit code: {code}")
        if code != 0:
            failures.append(f"{who} quit exit code {code}")
        for ln in proc.snapshot():
            if any(marker in ln.text for marker in FATAL):
                failures.append(f"{who} log: {ln.text}")
                break
    for _, proc in procs:
        proc.cleanup()


def report(failures, case):
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"\nPASS ({case}): the dropped client was told and left its game")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
