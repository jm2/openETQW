#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The mixer attenuates by distance, keeps looping sounds going, applies the master
volume and sound-class fades, and reports shake amplitude (#436).

The client builds Doom 3's software mixer (#438). Its emitters are spatialised from the
listener: full volume within a shader's minDistance, fading out to its maxDistance
(meters), and nothing beyond. A looping shader keeps mixing, and s_volume_dB scales the
whole mix. This checks those behaviours of the mixer as built, at the main menu (the
menu's sound world: a listener at the origin, no portals). It does not compare them
with retail 1.5, which was not measured.

The Vulkan client starts at the main menu with SDL's dummy audio driver. A looping
retail shader plays from one emitter (testSoundAt <shader> <distance>), with
s_singleEmitter set to it so the menu's music doesn't mix:
  P1 the playing world settles: the same world over a second (the session's menu
     world, once the main menu is up; the game's plays until then)
  P2 at the listener, the mix is loud; between minDistance and maxDistance it is
     quieter; past maxDistance it is silent
  P3 looping: started half a second before its end (SetChannelOffset), the looping
     shader still mixes two seconds later; the control, sounds/ui/main/click started
     past its own end, is silent, so the offset is applied
  P4 s_volume_dB -20 lowers its peak to between 2% and 25% of the 0 dB peak
  P5 FadeSoundClasses: its sound class faded to -60 dB (soundFadeClass) is silent, and
     faded back to 0 dB mixes again
  P6 shakes: with only the ambience playing the world's shake amplitude is 0; a retail
     explosion shader with shakes, played at the listener, raises it above 0

Usage: test_sound_params.py [--client-binary PATH] [--port N] [--log-dir DIR]
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
from test_sound_mix import stats  # noqa: E402
from test_vid_restart import READY, MenuClient  # noqa: E402

# looping retail ambience shaders (pak000 generated/declb/sounds/ambience.sndshdb);
# the first that reports SSF_LOOPING is used
CANDIDATES = ("ambience_war_01", "ambience_desert_global_01", "ambience_waves_01", "ambience_war_02")
SSF_LOOPING = 1 << 5
METERS_TO_UNITS = 1.0 / 0.0254
WINDOW = 1.5  # seconds of each restarted sound whose peak is compared
STARTED = re.compile(r"testSoundAt: emitter (\d+), (\S+) at \S+ units: minDistance (\S+) maxDistance (\S+) "
                     r"volume \S+ flags 0x([0-9a-f]+) length (-?\d+) shakes (\S+) class (\d+)")
CLICK = "sounds/ui/main/click"
# retail shaders likely to shake (pak000 generated/declb/sounds); the first with shakes is used
SHAKERS = ("sounds/deployables/artillery/explode", "sounds/deployables/artillery/impact_medium",
           "sounds/weapons/explosions/underwater")
SHAKE = re.compile(r"soundShakeAmplitude: (\S+)")
WORLD = re.compile(r"soundMixWorld: (-?\d+) of (\d+)")


def play(gfx, shader: str, distance: float, offset_ms: int | None = None):
    """Plays the shader from the test emitter and mixes only that emitter; the
    emitter's index, minDistance, maxDistance, flags and length (ms), or None."""
    mark = gfx.mark()
    gfx.send(f"testSoundAt {shader} {distance:.1f}" + ("" if offset_ms is None else f" {offset_ms}"))
    found = gfx.wait_for(lambda s: STARTED.search(s) or "testSoundAt: no" in s, 15.0, mark)
    if found is None:
        return None
    m = STARTED.search(found[1].text)
    if m is None:
        return None
    # the emitter can change (a new world): mix only the one that plays now
    gfx.send(f"set s_singleEmitter {m.group(1)}")
    return (int(m.group(1)), float(m.group(3)), float(m.group(4)), int(m.group(5), 16), int(m.group(6)),
            float(m.group(7)), int(m.group(8)))


def peak_over(gfx, seconds: float) -> int:
    stats(gfx)  # resets the peak
    loudest = 0
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        time.sleep(0.25)
        now = stats(gfx)
        if now is not None:
            loudest = max(loudest, now[1])
    return loudest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client-binary")
    ap.add_argument("--port", type=int, default=29943)
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
        log = Path(args.log_dir) / "sound-params.log"

    failures: list[str] = []

    def expect(ok: bool, what: str) -> None:
        print(f"  {'ok  ' if ok else 'FAIL'} {what}")
        if not ok:
            failures.append(what)

    os.environ["SDL_AUDIO_DRIVER"] = "dummy"
    gfx = MenuClient(client, data, args.port, connect_to="unused", log_path=log, cvars={"r_vkValidation": "0"})
    try:
        gfx.start()
        if not gfx.wait_for_text(READY, 180.0):
            print("FAIL: the client never finished starting")
            return 1
        gfx.sync()

        def world_index():
            mark = gfx.mark()
            gfx.send("soundMixStats")
            found = gfx.wait_for(lambda s: WORLD.search(s), 15.0, mark)
            return int(WORLD.search(found[1].text).group(1)) if found else None

        seen = []
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            seen.append(world_index())
            if len(seen) >= 4 and seen[-1] is not None and len(set(seen[-4:])) == 1:
                break
            time.sleep(0.3)
        expect(len(seen) >= 4 and len(set(seen[-4:])) == 1 and seen[-1] is not None and seen[-1] >= 0,
               f"P1 the playing world settles (indexes {seen})")

        chosen = None
        for shader in CANDIDATES:
            started = play(gfx, shader, 0.0)
            if started is not None and started[3] & SSF_LOOPING and started[2] > 0.0:
                chosen = (shader, started)
                break
        if chosen is None:
            expect(False, f"a looping shader with a maxDistance among {CANDIDATES}")
            raise SystemExit
        shader, (emitter, min_m, max_m, flags, length_ms, _, sound_class) = chosen
        print(f"     {shader}: emitter {emitter}, minDistance {min_m} m, maxDistance {max_m} m, flags 0x{flags:x},"
              f" length {length_ms} ms")

        # each measurement restarts the sound and takes the same window from its start,
        # so the peaks compare the same stretch of the sample
        def measured(distance):
            play(gfx, shader, distance)
            return peak_over(gfx, WINDOW)

        near = measured(0.0)
        mid_units = (min_m + (max_m - min_m) * 0.6) * METERS_TO_UNITS
        mid = measured(mid_units)
        far = measured(max_m * 1.5 * METERS_TO_UNITS)
        expect(near > 0 and 0 < mid < near and far == 0,
               f"P2 attenuation: peak {near} at the listener, {mid} at {mid_units:.0f} units, {far} past maxDistance")

        click = play(gfx, CLICK, 0.0, 0)
        click_len = click[4] if click else 0
        play(gfx, CLICK, 0.0, click_len + 500)
        past_end = peak_over(gfx, 1.0)
        play(gfx, shader, 0.0, max(0, length_ms - 500))
        time.sleep(2.0)
        later = peak_over(gfx, 1.5)
        expect(click_len > 0 and past_end == 0 and later > 0,
               f"P3 looping: the looping shader started 0.5 s before its end still mixes 2 s later (peak {later});"
               f" the click started past its end ({click_len} ms) is silent (peak {past_end})")

        full = measured(0.0)
        gfx.send("set s_volume_dB -20")
        quieter = measured(0.0)
        gfx.send("set s_volume_dB 0")
        ratio = quieter / full if full else 0.0
        expect(0.02 <= ratio <= 0.25, f"P4 s_volume_dB -20 scales the peak by {ratio:.3f} ({full} to {quieter})")

        play(gfx, shader, 0.0)
        gfx.send(f"soundFadeClass {sound_class} -60 0")
        time.sleep(0.5)
        faded = peak_over(gfx, WINDOW)
        gfx.send(f"soundFadeClass {sound_class} 0 0")
        time.sleep(0.5)
        restored = peak_over(gfx, WINDOW)
        expect(faded == 0 and restored > 0,
               f"P5 class {sound_class} faded to -60 dB mixes peak {faded}, faded back to 0 dB peak {restored}")

        def shake_amplitude():
            mark = gfx.mark()
            gfx.send("soundShakeAmplitude")
            found = gfx.wait_for(lambda s: SHAKE.search(s), 15.0, mark)
            return float(SHAKE.search(found[1].text).group(1)) if found else None

        quiet = shake_amplitude()
        shaker = None
        for name in SHAKERS:
            started = play(gfx, name, 0.0)
            if started is not None and started[5] > 0.0:
                shaker = (name, started)
                break
        loudest = 0.0
        if shaker is not None:
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and loudest <= 0.0:
                time.sleep(0.1)
                now = shake_amplitude()
                loudest = max(loudest, now or 0.0)
        expect(quiet == 0.0 and shaker is not None and loudest > 0.0,
               f"P6 shake amplitude {quiet} with the ambience; {shaker[0] if shaker else 'no shader with shakes among '}"
               f"{'' if shaker else SHAKERS} (shakes {shaker[1][5] if shaker else '-'}) raises it to {loudest}")
        gfx.send("set s_singleEmitter 0")
    except SystemExit:
        pass
    finally:
        code = gfx.quit()
        gfx.cleanup()
    expect(code == 0, f"the client exits 0 (exit {code})")

    if failures:
        print(f"FAIL: {len(failures)} check(s) failed")
        return 1
    print("PASS: the mixer attenuates by distance, loops, applies the master volume and class fades, and reports shakes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
