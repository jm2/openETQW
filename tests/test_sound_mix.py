#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The client mixes game sound into the audio device (#436).

Until #436 only a placeholder sound system was built (SoundSystemBootstrap.cpp): it
opened Bink movies for GUI cinematics, mixed nothing, and wrote silence to the audio
device. The client now builds Doom 3's software mixer behind ETQW's sound interfaces.
Its sound thread mixes the playing sound world into idAudioHardware, and retail's
samples load from generated/ogg.

The Vulkan client starts at the main menu with SDL's dummy audio driver (a device on
every host, played at real time), then:
  A1 soundMixStats reports the audio hardware and a playing sound world
  A2 mix buffers are written: their count grows over two seconds
  A3 "testSound sounds/ui/main/click" (a retail GUI shader, generated/ogg) mixes a
     non-silent buffer: the peak sample is above 0
  A4 the click's sample decoded: no "Couldn't find/open/load sound" warning
Then it hosts valley (the game's sound world):
  A5 the mixer plays the game's sound world: map ambience mixes a non-silent buffer
     within ten seconds of the map loading

Usage: test_sound_mix.py [--client-binary PATH] [--port N] [--log-dir DIR]
Exit status: 0 pass, 1 fail, 77 skip (no client binary or retail data).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402
from test_campaign_client_maps import find_client  # noqa: E402
from test_client_network import VulkanClient  # noqa: E402
from test_vid_restart import READY, MenuClient  # noqa: E402

STATS = re.compile(r"soundMixStats: (\d+) mix buffers, peak (\d+), hardware (\w+), playing world (\w+)")
SHADER = "sounds/ui/main/click"
UNLOADED = re.compile(r"Couldn't (find|open|load) sound")


def stats(gfx) -> tuple[int, int, str, str] | None:
    mark = gfx.mark()
    gfx.send("soundMixStats")
    found = gfx.wait_for(lambda s: STATS.search(s), 15.0, mark)
    if found is None:
        return None
    m = STATS.search(found[1].text)
    return int(m.group(1)), int(m.group(2)), m.group(3), m.group(4)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client-binary")
    ap.add_argument("--port", type=int, default=29942)
    ap.add_argument("--log-dir")
    args = ap.parse_args()
    try:
        client = find_client(args.client_binary)
        data = h.resolve_data_path()
    except h.SkipTest as e:
        print(f"[SKIP] {e}")
        return h.EXIT_SKIP
    h.install_signal_handlers()
    log = None
    if args.log_dir:
        Path(args.log_dir).mkdir(parents=True, exist_ok=True)
        log = Path(args.log_dir) / "sound-mix.log"

    failures: list[str] = []

    def expect(ok: bool, what: str) -> None:
        print(f"  {'ok  ' if ok else 'FAIL'} {what}")
        if not ok:
            failures.append(what)

    # a device on every host, desktop or not, consumed at real time
    os.environ["SDL_AUDIO_DRIVER"] = "dummy"
    gfx = MenuClient(client, data, args.port, connect_to="unused", log_path=log, cvars={"r_vkValidation": "0"})
    try:
        gfx.start()
        if not gfx.wait_for_text(READY, 180.0):
            print("FAIL: the client never finished starting")
            return 1
        gfx.sync()

        first = stats(gfx)
        expect(first is not None and first[2] == "yes" and first[3] == "yes",
               f"A1 the mixer has the audio hardware and a playing sound world ({first})")
        time.sleep(2.0)
        second = stats(gfx)
        expect(first is not None and second is not None and second[0] > first[0],
               f"A2 mix buffers are written ({first[0] if first else None} then {second[0] if second else None})")

        mark = gfx.mark()
        gfx.send(f"testSound {SHADER}")
        loudest = 0
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and loudest == 0:
            time.sleep(0.25)
            now = stats(gfx)
            if now is not None:
                loudest = max(loudest, now[1])
        expect(loudest > 0, f"A3 testSound {SHADER} mixes a non-silent buffer (peak {loudest})")
        missing = [ln.text.strip() for ln in gfx.snapshot() if UNLOADED.search(ln.text)]
        expect(not missing, f"A4 its sample loads ({missing[:2]})")
    finally:
        code = gfx.quit()
        gfx.cleanup()
    expect(code == 0, f"the client exits 0 (exit {code})")

    game_log = Path(args.log_dir) / "sound-mix-valley.log" if args.log_dir else None
    gfx = VulkanClient(client, data, args.port, map_name="valley", log_path=game_log,
                       cvars={"si_rules": "sdGameRulesObjective", "r_vkValidation": "0"})
    try:
        gfx.start()
        if not gfx.wait_for_text("Spawning entities", 180.0):
            expect(False, "A5 the listen server loads valley")
        else:
            gfx.sync()
            stats(gfx)
            loudest = 0
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline and loudest == 0:
                time.sleep(0.5)
                now = stats(gfx)
                if now is not None:
                    loudest = max(loudest, now[1])
            expect(loudest > 0, f"A5 valley's sound world mixes a non-silent buffer (peak {loudest})")
    finally:
        code = gfx.quit()
        gfx.cleanup()
    expect(code == 0, f"the valley client exits 0 (exit {code})")

    if failures:
        print(f"FAIL: {len(failures)} check(s) failed")
        return 1
    print("PASS: the client mixes game sound into the audio device")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
