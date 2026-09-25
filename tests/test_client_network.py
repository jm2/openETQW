#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Vulkan client on a network: joining a dedicated server, or hosting a listen server.

--mode join: a dedicated server, the Vulkan client and a headless remote
  client (the dedicated binary started with +connect). Both clients connect
  and receive snapshots; the Vulkan client joins GDF, the headless one
  Strogg. Each client's copy of the other player is within 2 units of the
  server's, and each client sees the other walk (>= 50 units).
--mode listen: the Vulkan client hosts the map (+spawnServer, a listen
  server; its own player is client 0) and joins GDF; a headless remote
  client connects and joins Strogg. The remote client's copy of the host's
  player matches the host's and it sees the host walk; the remote client
  walks and the host (the server) moves its player >= 50 units. The host's
  own view of the remote player is the server's state, so nothing here checks
  what the host draws of it.
In both modes the Vulkan client's GUIs (HUD, menus, world GUIs) must update about
once per frame during play: with g_guiSpeeds 1 (a cheat; the server allows cheats) its
GUI manager prints one line per update, and 3 s of play need >= 40 of them a second at
the default 60 fps cap (until 2026-09-25 a network session never updated its GUIs).
In both modes every process exits 0 on 'quit', no log has a failure marker
(etqw_harness.scan_failures; for the Vulkan client also the Vulkan error
markers campaign-client-maps uses), and the Vulkan client presented frames and
drew the world (the counters its backend prints at shutdown).

The Vulkan client opens a window and runs from the repository root (it finds
vkprogs/ there). Exit 77 when ETQW_DATA_PATH is unset/invalid or a binary is
missing.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402
from test_campaign_client_maps import BAD_MARKERS, METRICS_RE, find_client  # noqa: E402
from test_reconnect_restart import Failed, check_mutual, check_walk, connect, spawn  # noqa: E402
from test_remote_client_map_change import READY, RemoteClient  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]


class VulkanClient(h.DedicatedServer):
    """The Vulkan client: +connect to a server, or +spawnServer a listen server."""

    def __init__(self, binary, data, port, connect_to=None, map_name=None, log_path=None, cvars=None):
        super().__init__(binary, data, map_name or "client", port, log_path=log_path, cvars=cvars, cwd=REPO_ROOT)
        self.connect_to = connect_to
        self.listen_map = map_name
        self.spawn_commands = ["closeLimbo"]

    def command_line(self):
        args = [str(self.binary), "+set", "fs_basepath", str(self.data_path), "+set", "fs_game", "base",
                "+set", "fs_savepath", str(self.savepath), "+set", "net_port", str(self.port),
                "+set", "r_renderAPI", "vulkan", "+set", "r_fullscreen", "0"]
        for k, v in self.cvars.items():
            args += ["+set", str(k), str(v)]
        if self.listen_map:
            return args + ["+spawnServer", self.listen_map]
        return args + ["+connect", self.connect_to]


def check_gui_updates(gfx, what, seconds=3.0, min_per_second=40.0):
    """The Vulkan client's GUI manager updates about once per frame (g_guiSpeeds 1
    prints one 'numguis:' line per update)."""
    mark = gfx.mark()
    gfx.send("set g_guiSpeeds 1")
    time.sleep(seconds)
    gfx.send("set g_guiSpeeds 0")
    gfx.sync()
    lines = gfx.snapshot(mark)
    refused = [ln.text for ln in lines if "g_guiSpeeds cannot be changed" in ln.text]
    updates = sum(1 for ln in lines if ln.text.startswith("numguis:"))
    print(f"[{what}] GUI updates on the Vulkan client: {updates} in {seconds:.0f} s ({updates / seconds:.0f}/s)")
    if refused:
        raise Failed(f"{what}: g_guiSpeeds was refused ({refused[0]}); the server must allow cheats")
    if updates < min_per_second * seconds:
        raise Failed(f"{what}: the Vulkan client updated its GUIs {updates} times in {seconds:.0f} s"
                     f" (need >= {min_per_second:.0f}/s)")


def render_counters(proc):
    for ln in proc.snapshot():
        m = METRICS_RE.search(ln.text)
        if m:
            return {"frames": int(m.group(1)), "world_draws": int(m.group(2))}
    return None


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=("join", "listen"), help="required (checked after the skip checks)")
    ap.add_argument("--ded-binary")
    ap.add_argument("--client-binary")
    ap.add_argument("--map", default="valley")
    ap.add_argument("--port", type=int, default=29930)
    ap.add_argument("--log-dir")
    args = ap.parse_args(argv)

    try:
        ded = h.find_ded_binary(args.ded_binary)
        vk = find_client(args.client_binary)
        data = h.resolve_data_path()
    except h.SkipTest as e:
        print(f"[SKIP] {e}")
        return 77
    if args.mode is None:
        ap.error("--mode is required")
    h.install_signal_handlers()
    log_dir = Path(args.log_dir) if args.log_dir else None
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)

    def log(name):
        return (log_dir / f"{args.mode}-{name}.log") if log_dir else None

    # net_allowCheats: g_guiSpeeds (the GUI update count below) is a cheat
    game = {"si_rules": "sdGameRulesObjective", "si_pure": "0", "g_teamSwitchDelay": "0", "net_allowCheats": "1"}
    if args.mode == "join":
        srv = h.DedicatedServer(ded, data, args.map, args.port, log_path=log("server"), cvars=game)
        srv.name = "SERVER"
        gfx = VulkanClient(vk, data, args.port + 1, connect_to=f"127.0.0.1:{args.port}", log_path=log("vulkan"),
                           cvars={"si_pure": "0", "ui_name": "VulkanClient", "net_clientShowSnapshots": "1"})
        procs = [("vulkan", gfx), ("server", srv)]
    else:
        gfx = VulkanClient(vk, data, args.port, map_name=args.map, log_path=log("host"),
                           cvars={**game, "ui_name": "Host"})
        srv = gfx
        procs = [("host", gfx)]
    gfx.name, gfx.slot = "VulkanClient" if args.mode == "join" else "Host", None
    remote = RemoteClient(ded, data, args.port, args.port + 2, log_path=log("remote"))
    remote.cvars["ui_name"] = "RemoteClient"
    remote.name, remote.slot = "RemoteClient", None
    procs.insert(0, ("remote", remote))

    failures = []
    try:
        if args.mode == "join":
            srv.start()
            if not srv.wait_for_text(READY, 180.0):
                raise Failed("the server never became ready")
            srv.sync()
            connect(srv, gfx, "join")
        else:
            gfx.start()
            if not gfx.wait_for_text(READY, 180.0) or not gfx.wait_for_text(f"Map: maps/{args.map}", 180.0):
                raise Failed("the listen server never loaded the map")
            gfx.sync(60.0)
            gfx.slot = 0
            print(f"[listen] the host loaded {args.map}; its player is client 0")
        spawn(srv, gfx, "gdf", timeout=60.0)
        connect(srv, remote, args.mode)
        spawn(srv, remote, "strogg")
        check_mutual(srv, gfx, remote, args.mode)
        check_walk(srv, gfx, remote, args.mode)
        check_walk(srv, remote, gfx, args.mode)
        check_gui_updates(gfx, args.mode)
    except Failed as e:
        failures.append(str(e))
    finally:
        codes = {}
        for name, proc in procs:  # clients first, the server (or host) last
            codes[name] = proc.quit() if proc.alive() else None
        for name, proc in procs:
            for f in h.scan_failures(ln.text for ln in proc.snapshot()):
                failures.append(f"{name} log line {f['line']}: {f['text']}")
            if proc is gfx:
                for i, ln in enumerate(proc.snapshot()):
                    if any(m in ln.text for m in BAD_MARKERS):
                        failures.append(f"{name} log line {i + 1}: {ln.text}")
            proc.cleanup()
    counters = render_counters(gfx)
    print(f"quit exit codes: {codes}; Vulkan counters: {counters}")
    if not failures:
        if any(c != 0 for c in codes.values()):
            failures.append(f"quit exit codes: {codes}")
        if not counters or counters["frames"] <= 0 or counters["world_draws"] <= 0:
            failures.append(f"the Vulkan client presented no frames or drew no world: {counters}")
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    if args.mode == "join":
        print("\nPASS: the Vulkan client joined a dedicated server; both clients saw each other walk")
    else:
        print("\nPASS: the Vulkan client hosted a listen server; the remote client saw the host walk,"
              " and the host moved the remote player")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
