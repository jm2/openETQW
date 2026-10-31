#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""A fresh profile starts at a graphics preset picked from the GPU (#387).

Retail 1.5 picks a fresh profile's preset from device.info, a table of 2007-era
NVIDIA and ATI PCI IDs, and starts every GPU the table doesn't list at its lowest
preset. By owner decision (docs/compat-ledger.md 082), openETQW runs the table's
entry for a GPU it lists and starts every other GPU at the high preset
(specs/highspec.dat). Before this, nothing ran: com_machineSpec stayed -1 and the
settings menu's com_last*Level values were unset, so it showed the lowest preset.

  F0 every preset device.info's entries exec, with the .dat extension the engine
     gives an extension-less one, is a file in the retail paks (read in place)
The Vulkan client then hosts valley three times on one profile:
  F1 the first start, on a fresh profile, logs its choice ("machine spec: GPU
     vvvv:dddd is not in device.info; the high preset" on any current GPU) and
     runs the preset: com_machineSpec is 2 and the six com_last*Level values are 2.
     A GPU the table lists instead runs its entry and sets com_machineSpec to the
     entry's level; the levels are then not checked
  F2 the client saves the profile (etqwconfig.cfg) with com_machineSpec 2
  F3 a saved profile keeps its own settings: with r_shaderQuality 2 and
     com_machineSpec -1 written into etqwconfig.cfg (an openETQW profile from
     before this change), the second start logs no choice and r_shaderQuality
     stays 2
  F4 "+safe" still skips the saved profile: the third start, on the same profile,
     logs no choice and has r_shaderQuality at its default, 0

Usage: test_fresh_profile_spec.py [--client-binary PATH] [--port N] [--log-dir DIR]
Exit status: 0 pass, 1 fail, 77 skip (no client binary or retail data).
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402
from test_campaign_client_maps import find_client  # noqa: E402
from test_client_network import VulkanClient  # noqa: E402
from test_reconnect_restart import Failed  # noqa: E402

LEVELS = ("com_lastGraphicsLevel", "com_lastGraphicsDetailLevel", "com_lastCPULevel",
          "com_lastCPUDetailLevel", "com_lastFoliageLevel", "com_lastLightingLevel")
CHOICE = re.compile(r"machine spec: (GPU ([0-9a-f]+):([0-9a-f]+) is (not )?in device\.info|"
                    r"the GPU's PCI IDs are unknown)")
EXEC = re.compile(r"^\s*exec\s+(\S+)", re.IGNORECASE | re.MULTILINE)


class Client(VulkanClient):
    """The Vulkan client, with extra command-line arguments before the map."""

    def __init__(self, *args, extra=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.extra = list(extra)

    def command_line(self):
        args = super().command_line()
        return args[:-2] + self.extra + args[-2:]


def cvar_value(gfx, name: str) -> str | None:
    """The CVar's value as the console prints it, or None if it doesn't exist."""
    mark = gfx.mark()
    gfx.send(name)
    gfx.sync()
    want = re.compile(rf'"{re.escape(name)}" is:"([^"]*)"', re.IGNORECASE)
    for line in gfx.snapshot(mark):
        m = want.search(line.text)
        if m:
            return m.group(1)
    return None


def pak_files(data: Path) -> tuple[set[str], str]:
    """Every file name in the base paks (lower case), and device.info's text."""
    names: set[str] = set()
    device_info = ""
    for pak in sorted((data / "base").glob("*.pk4")):
        with zipfile.ZipFile(pak) as z:
            for name in z.namelist():
                names.add(name.lower())
                if name.lower() == "device.info":
                    device_info = z.read(name).decode("latin-1")
    return names, device_info


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client-binary")
    ap.add_argument("--port", type=int, default=29926)
    ap.add_argument("--log-dir")
    args = ap.parse_args()
    try:
        client = find_client(args.client_binary)
        data = h.resolve_data_path()
    except h.SkipTest as e:
        print(f"[SKIP] {e}")
        return h.EXIT_SKIP
    h.install_signal_handlers()
    logs = [None, None, None]
    if args.log_dir:
        Path(args.log_dir).mkdir(parents=True, exist_ok=True)
        logs = [Path(args.log_dir) / f"fresh-profile-spec-{i}.log" for i in (1, 2, 3)]

    failures: list[str] = []

    def expect(ok: bool, what: str) -> None:
        print(f"  {'ok  ' if ok else 'FAIL'} {what}")
        if not ok:
            failures.append(what)

    print("0. device.info's presets")
    names, device_info = pak_files(Path(data))
    execs = sorted(set(EXEC.findall(device_info)))
    resolved = [e if "." in Path(e).name else e + ".dat" for e in execs]
    missing = [r for r in resolved if r.lower() not in names]
    expect(bool(execs) and not missing,
           f"F0 device.info's {len(execs)} exec'd presets are files in the paks as {resolved} (missing: {missing})")

    with h.PrivateTempDir(prefix="openetqw-fresh-spec-") as tmp:
        profile = Path(tmp) / "profile"
        profile.mkdir()

        def start(log, extra=()):
            gfx = Client(client, data, args.port, map_name="valley", log_path=log, extra=extra,
                         cvars={"si_rules": "sdGameRulesObjective", "r_vkValidation": "0"})
            gfx.name = "VulkanClient"
            gfx.slot = 0
            # the starts share one profile, which the test's directory removes
            gfx.savepath = profile
            gfx._own_savepath = False
            gfx.start()
            if not gfx.wait_for_text("Spawning entities", 180.0):
                raise Failed("the listen server never loaded valley")
            gfx.sync()
            return gfx

        print("1. a fresh profile")
        gfx = start(logs[0])
        try:
            choice = [m for line in gfx.snapshot() if (m := CHOICE.search(line.text))]
            expect(len(choice) == 1, f"F1 the client logs one preset choice ({[m.group(0) for m in choice]})")
            spec = cvar_value(gfx, "com_machineSpec")
            if choice and choice[0].group(4) is None and choice[0].group(2):
                expect(spec in ("0", "1", "2"), f"F1 a listed GPU's entry sets com_machineSpec ({spec})")
            else:
                expect(spec == "2", f"F1 com_machineSpec is 2 ({spec})")
                levels = {name: cvar_value(gfx, name) for name in LEVELS}
                expect(all(v == "2" for v in levels.values()), f"F1 the menu's levels are 2 ({levels})")
        except Failed as e:
            failures.append(str(e))
            print(f"FAIL: {e}")
        finally:
            code = gfx.quit()
        expect(code == 0, f"the first client exits 0 (exit {code})")

        configs = sorted(profile.rglob("etqwconfig.cfg"))
        text = configs[0].read_text(encoding="utf-8", errors="replace") if configs else ""
        saved = re.search(r'seta com_machineSpec "(-?\d+)"', text, re.IGNORECASE)
        expect(saved is not None and saved.group(1) != "-1",
               f"F2 the profile is saved with com_machineSpec set ({saved.group(0) if saved else 'no line'})")

        if configs:
            print("2. the saved profile, as an older openETQW profile")
            text = re.sub(r'seta com_machineSpec "[^"]*"\n?', "", text, flags=re.IGNORECASE)
            text = re.sub(r'seta r_shaderQuality "[^"]*"\n?', "", text, flags=re.IGNORECASE)
            configs[0].write_text(text + 'seta com_machineSpec "-1"\nseta r_shaderQuality "2"\n', encoding="utf-8")
            gfx = start(logs[1])
            try:
                choice = [line.text for line in gfx.snapshot() if CHOICE.search(line.text)]
                expect(not choice, f"F3 a saved profile gets no preset ({choice})")
                quality = cvar_value(gfx, "r_shaderQuality")
                expect(quality == "2", f"F3 its r_shaderQuality stays 2 ({quality})")
            except Failed as e:
                failures.append(str(e))
                print(f"FAIL: {e}")
            finally:
                code = gfx.quit()
            expect(code == 0, f"the second client exits 0 (exit {code})")

            print("3. the same profile in safe mode")
            gfx = start(logs[2], extra=["+safe"])
            try:
                choice = [line.text for line in gfx.snapshot() if CHOICE.search(line.text)]
                expect(not choice, f"F4 safe mode gets no preset ({choice})")
                quality = cvar_value(gfx, "r_shaderQuality")
                expect(quality == "0", f"F4 safe mode leaves the saved profile unread: r_shaderQuality {quality}")
            except Failed as e:
                failures.append(str(e))
                print(f"FAIL: {e}")
            finally:
                code = gfx.quit()
            expect(code == 0, f"the third client exits 0 (exit {code})")

    if failures:
        print(f"FAIL: {len(failures)} check(s) failed")
        return 1
    print("PASS: a fresh profile starts at a preset picked from the GPU, and a saved one keeps its own")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
