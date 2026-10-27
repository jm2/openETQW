#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Player user info between a dedicated server and remote clients.

One dedicated server and two headless remote clients (dedicated binaries
started with +connect):
  A: ui_name Alpha, ui_clanTag TAG
  B: ui_name Alpha (a duplicate), ui_clanTag of 40 characters
Checks:
1. The server stores and validates both: 'status' shows Alpha and Alpha_ (the
   game's duplicate rule), and B's clan tag is cut to 16 characters.
2. Each client receives the other's validated info, with the displayed name
   composed from the clan tag ('TAG^0Alpha'; console output drops the colour
   escape, so the logs show 'TAGAlpha').
3. A bot added on the server reaches both clients' user info.
4. A rename on client A ('set ui_name Renamed') reaches the server and B.
5. Client B takes the bot's name, so the bot defers to a fallback name
   ('GDF Bot N'), then takes that name too. When the bot's next fallback is the
   same name the game's rescan used to loop forever; now B gets 'GDF Bot N_'
   or the bot renames again. The server must keep answering and the two names
   must differ.
6. After 'spawnServer <other map>' both clients get everyone's info again.
6. All processes exit 0 and log no failure marker.
With --drop N every process drops N% of its outgoing packets.

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
SLOT_RE = re.compile(r"^slot\s+(\d+): (\w+)\s+(human|bot)\s+'(.*)'$")
CLIENT_INFO_RE = re.compile(r"^Client user info (\d+): '(.*)'$")
SNAPSHOT_RE = re.compile(r"Client received snapshot (\d+)")
LONG_TAG = "L" * 40


class RemoteClient(h.DedicatedServer):
    """The dedicated binary as a headless client: +connect instead of +spawnServer."""

    def __init__(self, binary, data, server_port, port, cvars, log_path=None):
        super().__init__(binary, data, "", port, log_path=log_path, cvars=cvars)
        self.server_port = server_port

    def command_line(self):
        args = super().command_line()
        assert args[-2] == "+spawnServer"
        return args[:-2] + ["+connect", f"127.0.0.1:{self.server_port}"]


def status_names(srv):
    names = {}
    for ln in srv.command("status") or []:
        m = SLOT_RE.match(ln.text.strip())
        if m:
            names[int(m.group(1))] = (m.group(2), m.group(3), m.group(4))
    return names


def infos(cl, start=0):
    """{client number: last displayed name} from the client's CLIENTINFO lines after start."""
    out = {}
    for ln in cl.snapshot(start):
        m = CLIENT_INFO_RE.match(ln.text.strip())
        if m:
            out[int(m.group(1))] = m.group(2)
    return out


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ded-binary")
    ap.add_argument("--maps", default="quarry,outskirts")
    ap.add_argument("--port", type=int, default=29990)
    ap.add_argument("--drop", type=int, default=0)
    ap.add_argument("--log-dir")
    args = ap.parse_args(argv)
    first, second = [m for m in args.maps.split(",") if m][:2]

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
    lp = (lambda n: log_dir / n) if log_dir else (lambda n: None)

    failures = []
    drop = {"net_forceDrop": str(args.drop)}
    srv = h.DedicatedServer(binary, data, first, args.port, log_path=lp("server.log"),
                            cvars={"si_rules": "sdGameRulesObjective", "si_pure": "0", "g_teamSwitchDelay": "0",
                                   "bot_enable": "1", **drop})
    a = RemoteClient(binary, data, args.port, args.port + 1, log_path=lp("client_a.log"),
                     cvars={"si_pure": "0", "ui_name": "Alpha", "ui_clanTag": "TAG", "net_clientShowSnapshots": "1", **drop})
    b = RemoteClient(binary, data, args.port, args.port + 2, log_path=lp("client_b.log"),
                     cvars={"si_pure": "0", "ui_name": "Alpha", "ui_clanTag": LONG_TAG, "net_clientShowSnapshots": "1", **drop})
    procs = (srv, a, b)
    try:
        srv.start()
        if not srv.wait_for_text(READY, 180.0):
            print("FAIL: server never became ready")
            return 1
        srv.sync()
        slots = {}
        for name, cl in (("A", a), ("B", b)):
            cl.start()
            if not cl.wait_for(lambda s: SNAPSHOT_RE.search(s), 180.0):
                print(f"FAIL: client {name} never received a snapshot")
                return 1
        # 1. server side
        names = status_names(srv)
        print(f"[{first}] server status: {names}")
        humans = {n: v[2] for n, v in names.items() if v[1] == "human"}
        if sorted(humans.values()) != ["Alpha", "Alpha_"]:
            failures.append(f"server names {sorted(humans.values())}, expected ['Alpha', 'Alpha_'] (duplicate rule)")
        slot_a = next((n for n, v in humans.items() if v == "Alpha"), None)
        slot_b = next((n for n, v in humans.items() if v == "Alpha_"), None)
        slots = {"A": slot_a, "B": slot_b}

        # 2. each client has the other's validated info
        def wait_info(cl, num, pred, start=0, timeout=30.0):
            return cl.wait_for(lambda s: (m := CLIENT_INFO_RE.match(s.strip())) and int(m.group(1)) == num and pred(m.group(2)) or None,
                               timeout, start)
        if slot_a is None or slot_b is None:
            failures.append("could not tell the two clients' slots apart")
        else:
            if not wait_info(b, slot_a, lambda n: n == "TAGAlpha"):
                failures.append(f"client B never got A's info 'TAG^0Alpha' (has {infos(b).get(slot_a)!r})")
            got = wait_info(a, slot_b, lambda n: n.endswith("Alpha_"))
            tag = infos(a).get(slot_b, "")[:-len("Alpha_")] if got else None
            print(f"[{first}] A sees B as {infos(a).get(slot_b)!r}, B sees A as {infos(b).get(slot_a)!r}")
            if not got:
                failures.append(f"client A never got B's info '<tag>^0Alpha_' (has {infos(a).get(slot_b)!r})")
            elif tag is None or len(tag) != 16:
                failures.append(f"B's clan tag reached A as {tag!r} (expected it cut to 16 characters)")

        # 3. a bot
        mark_a, mark_b = a.mark(), b.mark()
        srv.send("addbot gdf soldier")
        srv.sync()
        bots = [n for n, v in status_names(srv).items() if v[1] == "bot"]
        print(f"[{first}] bot slots: {bots}")
        if len(bots) != 1:
            failures.append(f"expected one bot slot, status shows {bots}")
        else:
            for name, cl, mark in (("A", a, mark_a), ("B", b, mark_b)):
                if not wait_info(cl, bots[0], lambda n: n != "", mark):
                    failures.append(f"client {name} never got the bot's info")

        # 4. rename on A
        if slot_a is not None:
            mark_s, mark_b = srv.mark(), b.mark()
            a.send("set ui_name Renamed")
            ok_srv = srv.wait_for(lambda s: s.strip() == f"Client {slot_a} user info: 'Renamed'" or None, 30.0, mark_s)
            ok_b = wait_info(b, slot_a, lambda n: n == "TAGRenamed", mark_b)
            print(f"[{first}] rename reached server: {bool(ok_srv)}, client B: {bool(ok_b)}")
            if not ok_srv:
                failures.append("the server never got A's rename")
            if not ok_b:
                failures.append("client B never got A's rename")

        # 5. a bot's name, then its fallback name (server hang regression)
        if len(bots) == 1 and slot_b is not None:
            bot = bots[0]
            bot_name = status_names(srv).get(bot, ("", "", ""))[2]
            b.send(f'set ui_name "{bot_name}"')
            fallback = f"GDF Bot {bot}"
            deadline_ok = False
            deadline = time.time() + 10.0
            while time.time() < deadline:
                names = status_names(srv)
                if names.get(bot, ("", "", ""))[2] == fallback:
                    deadline_ok = True
                    break
                srv.sync()
            print(f"[{first}] B took the bot's name {bot_name!r}; bot renamed to {fallback!r}: {deadline_ok}")
            if not deadline_ok:
                failures.append(f"the bot did not defer to {fallback!r} (status {status_names(srv).get(bot)})")
            mark_s = srv.mark()
            b.send(f'set ui_name "{fallback}"')
            srv.wait_for(lambda s: s.strip() in (f"Client {slot_b} user info: '{fallback}'", f"Client {slot_b} user info: '{fallback}_'") or None, 30.0, mark_s)
            got = None
            deadline = time.time() + 10.0  # user info is applied at most once a second
            while time.time() < deadline:
                names = status_names(srv)  # the server must keep answering
                if not names:
                    break
                b_name, bot_name = names.get(slot_b, ("", "", ""))[2], names.get(bot, ("", "", ""))[2]
                if b_name in (fallback, fallback + "_") and b_name != bot_name:
                    got = (b_name, bot_name)
                    break
                srv.sync()
            print(f"[{first}] B asked for {fallback!r}: server answering {bool(status_names(srv))}, B/bot names {got}")
            if not got:
                failures.append(f"after taking the bot's fallback name: status {status_names(srv)} (B must be {fallback!r} or {fallback + '_'!r}, unlike the bot; a hang returns no status)")

        # 6. map change: everyone's info again
        marks = {"A": a.mark(), "B": b.mark()}
        srv.send(f"spawnServer {second}")
        if not srv.wait_for_text(f"Map: maps/{second}.world", 120.0):
            failures.append(f"server did not load {second}")
        for name, cl in (("A", a), ("B", b)):
            other = slots["B" if name == "A" else "A"]
            loaded = cl.wait_for(lambda s: "Client initializing map" in s and second in s or None, 90.0, marks[name])
            if not loaded:
                failures.append(f"client {name} never loaded {second}")
                continue
            if other is not None and not wait_info(cl, other, lambda n: n != "", loaded[0], 90.0):
                failures.append(f"client {name} got no user info for the other client after the map change")
        names = status_names(srv)
        print(f"[{second}] server status: {names}")
        if slot_a is not None and names.get(slot_a, ("", "", ""))[2] != "Renamed":
            failures.append(f"after the map change the server has {names.get(slot_a)} for A (expected 'Renamed')")

        codes = [p.quit() for p in (b, a, srv)]
        print(f"quit exit codes: B {codes[0]}, A {codes[1]}, server {codes[2]}")
        if any(c != 0 for c in codes):
            failures.append(f"quit exit codes {codes}")
        for name, p in (("server", srv), ("client A", a), ("client B", b)):
            # same exit-code predicate as this file's own quit check below
            run_failed = bool(failures) or any(c != 0 for c in codes)
            for bad in h.scan_failures((l.text for l in p.snapshot()), run_failed=run_failed):
                failures.append(f"{name} log: {bad}")
    finally:
        for p in procs:
            p.cleanup()

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nPASS: user info is validated on the server and reaches every client, across a rename and a map change")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
