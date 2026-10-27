#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""A connected client ignores a stray challengeResponse.

A client resends getChallenge every second until it is answered, so a slow
answer can arrive twice; the second copy reaches a client that has already
connected. The client takes a challengeResponse only while it is challenging
(as Doom 3 does): before, a connected client went back to connecting, sent a
new connect request and reset its channel on the server's answer.

A dedicated server and a headless remote client (the dedicated binary started
with +connect): once the client receives snapshots, a raw UDP peer on the same
address sends the client's port three challengeResponse packets. Over the next
3 s (three connect resend periods) the client must send no connect request and
not connect again, and its snapshots must keep coming. The client must also
print "Unwanted challenge response received." (as Doom 3 does), so a pass
proves the packets reached it.

Exit 77 when ETQW_DATA_PATH is unset/invalid or the binary is missing.
"""

import argparse
import re
import socket
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402
from test_protocol_clean_break import cstr, oob  # noqa: E402
from test_reconnect_restart import Failed, connect, wait_snapshots  # noqa: E402
from test_remote_client_map_change import READY, RemoteClient  # noqa: E402

CONNECTED_FROM_RE = re.compile(r"Client (\d+) connected from 127\.0\.0\.1:(\d+)")
RECONNECT_MARKERS = ("Sending connect request", "Client connected to server")
UNWANTED = "Unwanted challenge response received."


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ded-binary")
    ap.add_argument("--map", default="valley")
    ap.add_argument("--port", type=int, default=29860)
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

    def log(name):
        return (log_dir / f"{name}.log") if log_dir else None

    srv = h.DedicatedServer(binary, data, args.map, args.port, log_path=log("server"),
                            cvars={"si_rules": "sdGameRulesObjective", "si_pure": "0"})
    srv.name = "SERVER"
    cl = RemoteClient(binary, data, args.port, args.port + 1, log_path=log("client"))
    cl.name, cl.slot = "RemoteClient", None
    procs = [("client", cl), ("server", srv)]

    failures = []
    try:
        srv.start()
        if not srv.wait_for_text(READY, 180.0):
            raise Failed("the server never became ready")
        srv.sync()
        connect(srv, cl, "join")
        ports = [int(m.group(2)) for ln in srv.snapshot()
                 if (m := CONNECTED_FROM_RE.search(ln.text)) and int(m.group(1)) == cl.slot]
        if not ports:
            raise Failed(f"no 'Client {cl.slot} connected from' line on the server")
        client_addr = ("127.0.0.1", ports[-1])

        mark, srv_mark = cl.mark(), srv.mark()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            for _ in range(3):
                sock.sendto(oob(cstr("challengeResponse"), struct.pack("<i", 12345)), client_addr)
        print(f"[stray] sent 3 challengeResponse packets to the client at {client_addr[0]}:{client_addr[1]}")
        time.sleep(3.0)
        cl.sync()

        seen = [ln.text for ln in cl.snapshot(mark) if any(m in ln.text for m in RECONNECT_MARKERS)]
        if seen:
            raise Failed(f"the connected client started connecting again: {seen[0]!r}")
        unwanted = sum(1 for ln in cl.snapshot(mark) if UNWANTED in ln.text)
        print(f"[stray] the client printed '{UNWANTED}' {unwanted} times")
        if unwanted == 0:
            raise Failed(f"the client never printed '{UNWANTED}': the stray packets did not reach it")
        again = [ln.text for ln in srv.snapshot(srv_mark) if CONNECTED_FROM_RE.search(ln.text)]
        if again:
            raise Failed(f"the server connected the client again: {again[0]!r}")
        got = wait_snapshots(cl, mark)
        print(f"[stray] no new connect request; snapshots from {got[0]} after the stray packets")
    except Failed as e:
        failures.append(str(e))
    finally:
        codes = {}
        for name, proc in procs:  # the client first, the server last
            codes[name] = proc.quit() if proc.alive() else None
        for name, proc in procs:
            # same exit-code predicate as this file's own quit check below: a
            # process that never ran (None) is already a failure there
            run_failed = bool(failures) or any(c != 0 for c in codes.values())
            for f in h.scan_failures((ln.text for ln in proc.snapshot()), run_failed=run_failed):
                failures.append(f"{name} log line {f['line']}: {f['text']}")
            proc.cleanup()
    if not failures and any(c != 0 for c in codes.values()):
        failures.append(f"quit exit codes: {codes}")

    if failures:
        print("\nFAILURES:\n  - " + "\n  - ".join(failures))
        return 1
    print("\nPASS: a connected client ignored a stray challengeResponse")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
