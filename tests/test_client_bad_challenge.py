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

A second client (net_clientServerTimeout 20) never gets the password, and the
raw peer asks for a challenge every 2 s, so its challenge goes bad every cycle
and it keeps asking for new ones. It must get "Bad challenge" at least once and
still log "Server connection timed out" within 30 s of its first refusal: the
timeout runs from the first challengeResponse of a connect, and new challenges
do not restart it, so a client the server never accepts still gives up.

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
TIMEOUT_CLIENT = 20.0  # the second client's net_clientServerTimeout (s)


def get_challenge(port, client_id):
    """A raw peer's getChallenge: the server expires every challenge older than its lifetime."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.sendto(oob(cstr("getChallenge"), struct.pack("<i", client_id)), ("127.0.0.1", port))


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
    cl2 = RemoteClient(binary, data, args.port, args.port + 2, log_path=log("client2"),
                       net={"net_clientServerTimeout": str(int(TIMEOUT_CLIENT))})
    cl2.name, cl2.slot = "RemoteClient2", None
    procs = [("client2", cl2), ("client", cl), ("server", srv)]

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

        # the client's challenge is now older than its lifetime; any getChallenge expires it.
        # Marks first: the client's next connect may land as soon as the challenge expires.
        time.sleep(CHALLENGE_LIFETIME + 1.0 - (time.monotonic() - refused_at))
        cl_mark, srv_mark = cl.mark(), srv.mark()
        get_challenge(args.port, 4242)
        if not srv.wait_for(lambda s: "(id: 4242," in s or None, 10.0, srv_mark):
            raise Failed("the server never answered the raw peer's getChallenge")
        print("[expire] a raw peer asked for a challenge; the client's has expired")

        if not cl.wait_for(lambda s: "Bad challenge" in s or None, 15.0, cl_mark):
            raise Failed("the client's next connect did not get 'Bad challenge': the test did not force a stale challenge")
        print("[stale] the client's connect got 'Bad challenge'")
        cl.send(f"set password {PASSWORD}")

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

        # A challenge that keeps going bad (never the password, and a raw peer expires
        # it every cycle) must still end in the client's timeout, counted from its first
        # challengeResponse: new challenges do not restart it.
        cl2.start()
        if not cl2.wait_for(lambda s: "Invalid password" in s or None, 120.0, 0):
            raise Failed("the server never refused the second client's connect")
        refused_at = time.monotonic()
        deadline = refused_at + TIMEOUT_CLIENT + 10.0
        timed_out = None
        while time.monotonic() < deadline and not timed_out:
            get_challenge(args.port, 4243)
            timed_out = cl2.wait_for(lambda s: "Server connection timed out" in s or None, 2.0, 0)
        bad = sum(1 for ln in cl2.snapshot() if "Bad challenge" in ln.text)
        print(f"[timeout] the second client got 'Bad challenge' {bad} times; timed out: {bool(timed_out)}"
              f" ({time.monotonic() - refused_at:.0f} s after its first refusal)")
        if bad == 0:
            raise Failed("the second client never got 'Bad challenge': the test did not force a stale challenge")
        if not timed_out:
            raise Failed(f"a client stuck on bad challenges did not time out within {TIMEOUT_CLIENT + 10:.0f} s"
                         f" of its first refusal (net_clientServerTimeout {TIMEOUT_CLIENT:.0f})")
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
    print("\nPASS: a client with an expired challenge asked for a new one and connected")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
