#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Network clean break from retail ET:QW 1.5 (CHARTER D7).

OpenETQW speaks its own protocol (major 0x4F45 'OE'). This test runs the
connectionless handshake against a dedicated server as a raw UDP peer:
getChallenge -> challengeResponse -> connect. A connect that announces a retail
ET:QW protocol major (10, and the other id/Splash Damage majors) must be refused
with the server's bad-protocol print naming the OpenETQW protocol. A connect
with the OpenETQW protocol must get past that check.

Exit 77 when ETQW_DATA_PATH is unset/invalid or the binary is missing.
"""

import argparse
import socket
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402

READY = "------------- ETQW initialized -------------"
OPENETQW_MAJOR = 0x4F45
OPENETQW_MINOR = 5
# id / Splash Damage majors from Licensee.h: 1 Doom 3, 2 Quake 4, 10-14 ET:QW builds, 22 Quake 4 Xbox 360
RETAIL_MAJORS = (10, 11, 12, 13, 14, 1, 2, 22)


def oob(*parts: bytes) -> bytes:
    return struct.pack("<h", -1) + b"".join(parts)


def cstr(s: str) -> bytes:
    return s.encode("ascii") + b"\0"


def read_cstr(data: bytes, pos: int) -> tuple[str, int]:
    end = data.index(b"\0", pos)
    return data[pos:end].decode("latin1"), end + 1


def handshake(sock, addr, client_id: int, protocol: int, timeout: float = 5.0):
    """Returns (command, payload-string) of the server's answer to connect."""
    sock.settimeout(timeout)
    sock.sendto(oob(cstr("getChallenge"), struct.pack("<i", client_id)), addr)
    data, _ = sock.recvfrom(4096)
    if struct.unpack_from("<h", data)[0] != -1:
        raise RuntimeError("challenge reply is not connectionless")
    cmd, pos = read_cstr(data, 2)
    if cmd != "challengeResponse":
        raise RuntimeError(f"unexpected challenge reply '{cmd}'")
    challenge = struct.unpack_from("<i", data, pos)[0]
    connect = oob(cstr("connect"), struct.pack("<ihiihi", protocol, 0, 0, challenge, client_id, 25000),
                  cstr("0123456789a"), cstr(""))
    sock.sendto(connect, addr)
    while True:
        data, _ = sock.recvfrom(4096)
        if struct.unpack_from("<h", data)[0] != -1:
            continue
        cmd, pos = read_cstr(data, 2)
        if cmd == "print":
            opcode = data[pos]
            text, _ = read_cstr(data, pos + 1)
            return cmd, f"{opcode}:{text}"
        return cmd, ""


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ded-binary")
    ap.add_argument("--map", default="quarry")
    ap.add_argument("--port", type=int, default=29960)
    args = ap.parse_args(argv)
    try:
        binary = h.find_ded_binary(args.ded_binary)
        data = h.resolve_data_path()
    except h.SkipTest as e:
        print(f"[SKIP] {e}")
        return 77
    h.install_signal_handlers()

    failures = []
    srv = h.DedicatedServer(binary, data, args.map, args.port,
                            cvars={"si_rules": "sdGameRulesObjective", "si_pure": "0"})
    try:
        srv.start()
        if not srv.wait_for_text(READY, 180.0):
            print("FAIL: server never became ready")
            return 1
        srv.sync()
        addr = ("127.0.0.1", args.port)
        expected = f"Server uses protocol {OPENETQW_MAJOR}.{OPENETQW_MINOR}"
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            for i, major in enumerate(RETAIL_MAJORS):
                protocol = (major << 16) + 41
                try:
                    cmd, text = handshake(sock, addr, 1000 + i, protocol)
                except (OSError, RuntimeError) as e:
                    failures.append(f"major {major}: handshake failed: {e}")
                    continue
                ok = cmd == "print" and expected in text
                print(f"[{'OK' if ok else 'FAIL'}] connect with protocol {major}.41 -> {cmd} {text!r}")
                if not ok:
                    failures.append(f"major {major}: expected refusal '{expected}', got {cmd} {text!r}")
            cmd, text = handshake(sock, addr, 2000, (OPENETQW_MAJOR << 16) + OPENETQW_MINOR)
            # only connectResponse proves the connect got through; a print (bad
            # challenge, server full, ...) would mean it was refused before or after
            # the protocol check
            ok = cmd == "connectResponse"
            print(f"[{'OK' if ok else 'FAIL'}] connect with the OpenETQW protocol -> {cmd} {text!r} (accepted)")
            if not ok:
                failures.append(f"OpenETQW protocol connect not accepted: {cmd} {text!r}")
        rc = srv.quit()
        if rc != 0:
            failures.append(f"quit exit code {rc}")
    finally:
        srv.cleanup()

    if failures:
        print("\nFAILURES:\n  - " + "\n  - ".join(failures))
        return 1
    print("\nPASS: retail protocol majors are refused; the OpenETQW protocol connect is accepted")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
