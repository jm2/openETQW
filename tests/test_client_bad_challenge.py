#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""A client whose challenge the server forgot asks for a new one.

The server forgets a challenge 10 s after it was issued, the next time anyone
asks for one (idAsyncServer::ProcessChallengeMessage). A client that holds its
challenge longer, because the server refuses its connects for a while, then
gets "Bad challenge" for every connect. Doom 3's client goes back to
challenging on SERVER_PRINT_BADCHALLENGE; before this did the same, it resent
the stale challenge forever and the server never sent it a game init (one
possible cause of #216).

A dedicated server that needs a password (si_needPass 1) and a headless remote
client (the dedicated binary started with +connect) without it: the server
refuses the client's connects ("Invalid password") while the client keeps its
challenge. After 11 s a raw UDP peer asks the server for a challenge, which
expires the client's. Then the client sets the password. Its next connect must
get "Bad challenge" (the test forced the stale challenge), and the client must
then ask for a new challenge, connect, get a game init and receive snapshots.

Exit 77 when ETQW_DATA_PATH is unset/invalid or the binary is missing.
"""

import argparse
import socket
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402
from test_protocol_clean_break import cstr, oob  # noqa: E402
from test_reconnect_restart import Failed, wait_snapshots  # noqa: E402
from test_remote_client_map_change import GAME_INIT_RE, READY, RemoteClient  # noqa: E402

PASSWORD = "letmein"
CHALLENGE_LIFETIME = 10.0  # idAsyncServer::ProcessChallengeMessage


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ded-binary")
    ap.add_argument("--map", default="valley")
    ap.add_argument("--port", type=int, default=29870)
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
                            cvars={"si_rules": "sdGameRulesObjective", "si_pure": "0",
                                   "si_needPass": "1", "g_password": PASSWORD})
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

        cl.start()
        if not cl.wait_for(lambda s: "Invalid password" in s or None, 120.0, 0):
            raise Failed("the server never refused the client's connect without the password")
        refused_at = time.monotonic()
        print("[refused] the server refuses the client's connects without the password")

        # the client's challenge is now older than its lifetime; any getChallenge expires it
        time.sleep(CHALLENGE_LIFETIME + 1.0 - (time.monotonic() - refused_at))
        srv_mark = srv.mark()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(oob(cstr("getChallenge"), struct.pack("<i", 4242)), ("127.0.0.1", args.port))
        if not srv.wait_for(lambda s: "(id: 4242," in s or None, 10.0, srv_mark):
            raise Failed("the server never answered the raw peer's getChallenge")
        print("[expire] a raw peer asked for a challenge; the client's has expired")

        cl_mark, srv_mark = cl.mark(), srv.mark()
        cl.send(f"set password {PASSWORD}")
        if not cl.wait_for(lambda s: "Bad challenge" in s or None, 15.0, cl_mark):
            raise Failed("the client's next connect did not get 'Bad challenge': the test did not force a stale challenge")
        print("[stale] the client's connect got 'Bad challenge'")

        hit = srv.wait_for(lambda s: GAME_INIT_RE.search(s), 30.0, srv_mark)
        if not hit:
            tail = [ln.text for ln in cl.snapshot(cl_mark)][-4:]
            raise Failed(f"the client never recovered from 'Bad challenge' (client log ends {tail})")
        cl.slot = int(hit[2].group(1))
        rechallenged = any("Sending challenge request" in ln.text for ln in cl.snapshot(cl_mark))
        if not rechallenged:
            raise Failed("the client connected without asking for a new challenge")
        got = wait_snapshots(cl, cl_mark)
        print(f"[recovered] the client asked for a new challenge and is client {cl.slot}; snapshots from {got[0]}")
    except Failed as e:
        failures.append(str(e))
    finally:
        codes = {}
        for name, proc in procs:  # the client first, the server last
            codes[name] = proc.quit() if proc.alive() else None
        for name, proc in procs:
            for f in h.scan_failures(ln.text for ln in proc.snapshot()):
                failures.append(f"{name} log line {f['line']}: {f['text']}")
            proc.cleanup()
    if not failures and any(c != 0 for c in codes.values()):
        failures.append(f"quit exit codes: {codes}")

    if failures:
        print("\nFAILURES:\n  - " + "\n  - ".join(failures))
        return 1
    print("\nPASS: a client with an expired challenge asked for a new one and connected")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
