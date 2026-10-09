#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""A GUI that fails to parse while a map is loaded drops the map cleanly.

The Vulkan client runs a listen server on valley with a deliberately broken
GUI in its save path (guis/oetqw_test_broken.gui: a property whose expression
names a scope that doesn't exist). Then:
1. In the map, 'testGUI oetqw_test_broken' loads it. The parse error reaches
   common->Error, which stops the session (the map is shut down). The GUI
   loader must pass the error on to the engine ('... dropped the map; passing
   it on'), not swallow it with 'sdUserInterfaceLocal::Load failed', which let
   callers carry on in the unloaded world (game issue #49).
2. With no map loaded, the same command fails as a warning ('Load failed'),
   since nothing was shut down.
After each step the client must still be running; it must exit 0 on 'quit'
and log no crash or fatal marker.

Exit 77 when ETQW_DATA_PATH is unset/invalid or the client binary is missing.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402
from test_campaign_client_maps import find_client  # noqa: E402
from test_client_network import VulkanClient  # noqa: E402

BROKEN_GUI = """gui oetqw_test_broken {
	windowDef desktop {
		properties {
			float oetqwBroken = icompare( oetqwNoSuchScope.teamName, "spectating" );
		}
	}
}
"""
PASSED_ON = "dropped the map; passing it on"
SWALLOWED = "sdUserInterfaceLocal::Load failed on 'oetqw_test_broken'"
FATAL = ("FATAL ERROR", "FATAL SIGNAL CAUGHT", "Unhandled exception", "Access violation")


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--client-binary")
    ap.add_argument("--map", default="valley")
    ap.add_argument("--port", type=int, default=29986)
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

    failures = []
    gfx = VulkanClient(vk, data, args.port, map_name=args.map,
                       log_path=(log_dir / "client.log") if log_dir else None,
                       cvars={"r_vkValidation": "0", "com_maxFPS": "60", "si_rules": "sdGameRulesObjective", "si_pure": "0"})
    gui_dir = Path(gfx.savepath) / "base" / "guis"
    gui_dir.mkdir(parents=True, exist_ok=True)
    (gui_dir / "oetqw_test_broken.gui").write_text(BROKEN_GUI, encoding="utf-8", newline="\n")
    try:
        gfx.start()
        if not gfx.wait_for(lambda s: "Map Initialization" in s or None, 240.0):
            print(f"FAIL: the client never loaded {args.map}")
            return 1
        gfx.wait_for_text("ETQW initialized", 60.0)
        time.sleep(5)

        # 1. in the map
        mark = gfx.mark()
        gfx.send("testGUI oetqw_test_broken")
        passed_on = gfx.wait_for(lambda s: PASSED_ON in s or SWALLOWED in s or None, 30.0, mark)
        time.sleep(3)
        lines = [ln.text for ln in gfx.snapshot(mark)]
        print(f"[in map] passed on: {any(PASSED_ON in t for t in lines)}, swallowed: {any(SWALLOWED in t for t in lines)}, "
              f"error banner: {any('ERROR:' in t for t in lines)}")
        if not passed_on or not any(PASSED_ON in t for t in lines):
            failures.append("in the map, the GUI loader did not pass the parse error on")
        if any(SWALLOWED in t for t in lines):
            failures.append("in the map, the GUI loader swallowed the parse error after the map was dropped")
        if not gfx.alive():
            failures.append("the client died after the GUI error in the map")
            return report(failures)

        # 2. without a map (the drop above left the client at the menu)
        mark = gfx.mark()
        gfx.send("testGUI oetqw_test_broken")
        gfx.wait_for(lambda s: PASSED_ON in s or SWALLOWED in s or None, 30.0, mark)
        time.sleep(2)
        lines = [ln.text for ln in gfx.snapshot(mark)]
        print(f"[no map] passed on: {any(PASSED_ON in t for t in lines)}, swallowed: {any(SWALLOWED in t for t in lines)}")
        if not any(SWALLOWED in t for t in lines):
            failures.append("without a map, the failed load was not reported as a warning")
        if any(PASSED_ON in t for t in lines):
            failures.append("without a map, the GUI loader passed the error on although nothing was shut down")
        if not gfx.alive():
            failures.append("the client died after the GUI error without a map")
            return report(failures)

        code = gfx.quit()
        print(f"quit exit code: {code}")
        if code != 0:
            failures.append(f"quit exit code {code}")
        for ln in gfx.snapshot():
            if any(marker in ln.text for marker in FATAL):
                failures.append(f"client log: {ln.text}")
                break
    finally:
        gfx.cleanup()
    return report(failures)


def report(failures):
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nPASS: a GUI parse error in a map passes on as the drop it is; without a map it stays a warning")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
