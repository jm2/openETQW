#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""A failed reload of a HUD-owned GUI drops the map without a stale instance.

The Vulkan client runs a listen server on valley with a deliberately broken
GUI in its save path (guis/oetqw_test_broken.gui: a property whose expression
names a scope that doesn't exist).  'testHudReload' reloads the player HUD in
place, as OnReloadGUI does.  The load error reaches common->Error, which stops
the session before it throws; MapShutdown then frees the HUD module and with
it the GUI instance whose Load is still on the stack (game issue #51).

The catch must report the requested GUI name, not read through the freed
instance, and the client must stay alive and exit 0 on 'quit'.  The catch at
bb8e0c5 already used the passed name and the static trace helpers, so this
passes before game #51; it guards the invariant for a catch that would read the
instance instead (a `GetName()`/`this->name` variant kills the client here).

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
PASSED_ON = "sdUserInterfaceLocal::Load: 'oetqw_test_broken' failed and the error dropped the map; passing it on"
FATAL = ("FATAL ERROR", "FATAL SIGNAL CAUGHT", "Unhandled exception", "Access violation")


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--client-binary")
    ap.add_argument("--map", default="valley")
    ap.add_argument("--port", type=int, default=29989)
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

        # An unknown name fails without dropping the map.  The HUD must be
        # cleared and the previous GUI restored, not left half-loaded.
        mark = gfx.mark()
        gfx.send("testHudReload oetqw_no_such_gui")
        gfx.wait_for(lambda s: "testHudReload: result" in s or None, 30.0, mark)
        lines = [ln.text for ln in gfx.snapshot(mark)]
        restored = any("testHudReload: result loaded=0 restored=1 active=1" in t for t in lines)
        unknown_shutdown = any("Game Map Shutdown" in t for t in lines)
        print(f"[hud reload] unknown-name restore: {restored}, map shut down: {unknown_shutdown}")
        if not restored:
            failures.append("an unknown GUI name did not restore the active HUD")
        if unknown_shutdown:
            failures.append("an unknown GUI name shut the map down")

        mark = gfx.mark()
        gfx.send("testHudReload oetqw_test_broken")
        gfx.wait_for(lambda s: "dropped the map; passing it on" in s or "Load failed" in s or None, 30.0, mark)
        time.sleep(3)
        lines = [ln.text for ln in gfx.snapshot(mark)]
        passed_on = any(PASSED_ON in t for t in lines)
        shutdown = any("Game Map Shutdown" in t for t in lines)
        print(f"[hud reload] passed on: {passed_on}, map shut down: {shutdown}")
        if not passed_on:
            failures.append("the HUD reload did not report the requested GUI name and pass the error on")
            for t in lines:
                if "sdUserInterfaceLocal::Load" in t or "testHudReload" in t:
                    print(f"  log: {t}")
        if not shutdown:
            failures.append("the HUD reload error did not shut the map down")
        if not gfx.alive():
            failures.append("the client died after the HUD reload error")
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
    print("\nPASS: a HUD-owned GUI reload that drops the map reports the name and survives")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
