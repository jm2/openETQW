#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
idle-client-memory (#367): a client left idle in a game must not keep growing.

Before the fix, the Windows client grew by 18-28 MB a minute while idle. Windows' sound thread
hands the SDL3 audio backend a 4096-sample mix buffer every USERCMD_MSEC: about 124,000 samples
a second, against the 44,100 a second the device plays. The backend put every buffer into its
SDL audio stream, and SDL keeps everything it is given. Where the device had gone away (a
disconnected remote session), nothing was played at all. A client with no audio device (a locked
or headless desktop) never opened the stream, so it stayed flat.

One single-player Vulkan client on valley, with SDL's dummy audio driver. It plays at 44.1 kHz in
real time, so an audio device is open on any host, with or without a desktop:

  M1  the client opened its audio stream ("SDL3 Audio initialized"). Without the stream the
      leak's path does not run, so the test fails rather than pass.
  M2  after WARMUP seconds in the game, the process's private bytes (Windows; the resident set
      elsewhere) grow by less than MAX_GROWTH_MB over WINDOW seconds. Before the fix they grew
      by 27-34 MB in 90 s on Windows x64; with it, a one-time step of about 5 MB some 30-40 s
      after the map loads, then nothing. WARMUP is meant to put that step before the window.

Exit 0 pass, 1 fail, 77 no client binary or no retail data.
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402
from test_campaign_client_maps import BAD_MARKERS, find_client  # noqa: E402
from test_frame_poses import DevmapClient  # noqa: E402
from test_reconnect_restart import Failed  # noqa: E402
from test_remote_client_map_change import READY  # noqa: E402

WARMUP = 45.0
WINDOW = 90.0
SAMPLE_EVERY = 10.0
MAX_GROWTH_MB = 12.0
AUDIO_OPENED = "SDL3 Audio initialized"


def private_mb(pid):
    """The process's private bytes in MB on Windows (the commit charge, which a working-set trim
    does not lower), its resident set elsewhere."""
    if sys.platform == "win32":
        import ctypes
        import ctypes.wintypes as wt

        class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
            _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD)] + [
                (name, ctypes.c_size_t) for name in (
                    "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                    "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage",
                    "PrivateUsage")]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            raise OSError(ctypes.get_last_error(), "OpenProcess")
        try:
            counters = PROCESS_MEMORY_COUNTERS_EX()
            counters.cb = ctypes.sizeof(counters)
            if not kernel32.K32GetProcessMemoryInfo(ctypes.c_void_p(handle), ctypes.byref(counters), counters.cb):
                raise OSError(ctypes.get_last_error(), "K32GetProcessMemoryInfo")
            return counters.PrivateUsage / 2**20
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
    return int(subprocess.check_output(["ps", "-o", "rss=", "-p", str(pid)]).decode().strip()) / 1024


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client-binary")
    ap.add_argument("--map", default="valley")
    ap.add_argument("--port", type=int, default=29991)
    ap.add_argument("--log-dir")
    args = ap.parse_args(argv)
    try:
        vk = find_client(args.client_binary)
        data = h.resolve_data_path()
    except h.SkipTest as e:
        print(f"[SKIP] {e}")
        return 77
    h.install_signal_handlers()
    log_dir = Path(args.log_dir) if args.log_dir else None
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)

    # the client inherits this: an audio device that plays in real time on any host (M1)
    os.environ["SDL_AUDIO_DRIVER"] = "dummy"
    cl = DevmapClient(vk, data, args.port, map_name=args.map,
                      log_path=(log_dir / "devmap.log") if log_dir else None,
                      cvars={"si_rules": "sdGameRulesObjective", "ui_name": "Player", "r_vkValidation": "0"})
    failures = []
    code = None
    try:
        cl.start()
        if not cl.wait_for_text(READY, 180.0) or not cl.wait_for_text(f"Map: maps/{args.map}", 180.0):
            raise Failed("the single-player game never loaded the map")
        if cl.sync(60.0) is None:
            raise Failed("the client never answered a console echo after the map loaded")

        opened = any(AUDIO_OPENED in ln.text for ln in cl.snapshot())
        print(f"  M1 {'pass' if opened else 'FAIL'}: audio stream {'opened' if opened else 'not opened'}"
              f" with SDL_AUDIO_DRIVER=dummy")
        if not opened:
            raise Failed(f"M1: no '{AUDIO_OPENED}' line: the audio path this test judges did not run")

        time.sleep(WARMUP)
        samples = []
        start = time.monotonic()
        while True:
            if not cl.alive():
                raise Failed("M2: the client exited while idle")
            samples.append((time.monotonic() - start, private_mb(cl.proc.pid)))
            if samples[-1][0] >= WINDOW:
                break
            time.sleep(SAMPLE_EVERY)
        growth = samples[-1][1] - samples[0][1]
        ok = growth < MAX_GROWTH_MB
        print("  samples (s, MB): " + ", ".join(f"{t:.0f} {mb:.1f}" for t, mb in samples))
        print(f"  M2 {'pass' if ok else 'FAIL'}: grew {growth:+.1f} MB in {samples[-1][0]:.0f} s idle"
              f" (need < {MAX_GROWTH_MB:.0f} MB)")
        if not ok:
            failures.append(f"M2: the idle client grew {growth:+.1f} MB in {samples[-1][0]:.0f} s"
                            f" (from {samples[0][1]:.1f} MB to {samples[-1][1]:.1f} MB)")
    except Failed as e:
        failures.append(str(e))
    finally:
        code = cl.quit() if cl.alive() else None
        lines = [ln.text for ln in cl.snapshot()]
        failures.extend(f"client log line {i + 1}: {t}" for i, t in enumerate(lines) if any(m in t for m in BAD_MARKERS))
        for f in h.scan_failures(lines, run_failed=bool(failures) or code != 0):
            failures.append(f"client log line {f['line']}: {f['text']}")
        cl.cleanup()
    print(f"quit exit code: {code}")
    if not failures and code != 0:
        failures.append(f"quit exit code: {code}")
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nPASS")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
