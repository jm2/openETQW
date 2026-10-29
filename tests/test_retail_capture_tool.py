#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""tools/validation/retail_capture.py's offline parts.

The capture itself needs the retail client, mutter and a GPU, so it is run by hand
(docs/retail-comparison.md § 1). Pinned here, with no retail data, display or ports:
  * --out inside either repository is refused before anything starts (AGENTS.md rule 3);
  * write_png writes a PNG whose decoded pixels are the input;
  * mean_frames rounds the per-pixel mean, and box_means scales the § 4 boxes to the grab;
  * without --views, the views are screenshot_set.py's VIEWS (and EXTRA_VIEWS) around the
    map's anchor, so a retail capture lines up with ours.
"""

from __future__ import annotations

import importlib.util
import struct
import subprocess
import sys
import zlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL = REPO_ROOT / "tools" / "validation" / "retail_capture.py"

failures: list[str] = []


def expect(cond: bool, what: str) -> None:
    if not cond:
        failures.append(what)
        print(f"FAIL: {what}")


def load():
    spec = importlib.util.spec_from_file_location("retail_capture", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_png(path: Path) -> tuple[int, int, bytes]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, w, h = 8, b"", 0, 0
    while pos < len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]
        tag, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + n]
        if tag == b"IHDR":
            w, h = struct.unpack(">II", body[:8])
        elif tag == b"IDAT":
            idat += body
        pos += 12 + n
    raw = zlib.decompress(idat)
    rows = [raw[y * (w * 3 + 1) + 1:(y + 1) * (w * 3 + 1)] for y in range(h)]
    expect(all(raw[y * (w * 3 + 1)] == 0 for y in range(h)), "PNG rows use filter 0")
    return w, h, b"".join(rows)


def main() -> int:
    rc = load()

    # the output guard runs before the compositor or retail start
    proc = subprocess.run([sys.executable, str(TOOL), "--retail-dir", str(REPO_ROOT),
                           "--out", str(REPO_ROOT / "build" / "retail-shots")],
                          capture_output=True, text=True, timeout=30)
    expect(proc.returncode != 0 and "never enter a repository" in proc.stderr,
           f"--out inside the repository is refused (rc {proc.returncode}: {proc.stderr.strip()[-200:]})")
    expect(not (REPO_ROOT / "build" / "retail-shots").exists(), "the refused --out is not created")

    w, h = 5, 3
    rgb = bytes((x * 40 + y * 7 + c) % 256 for y in range(h) for x in range(w) for c in range(3))
    sys.path.insert(0, str(REPO_ROOT / "tests"))
    import private_dirs
    with private_dirs.PrivateTempDir(prefix="openetqw-retail-capture-test-") as tmp:
        path = Path(tmp) / "t.png"
        rc.write_png(path, w, h, rgb)
        expect(read_png(path) == (w, h, rgb), "write_png round-trips its pixels")

    m = rc.mean_frames([(1, 1, bytes([10, 20, 31])), (1, 1, bytes([11, 20, 32]))])
    expect(m == (1, 1, bytes([11, 20, 32])), f"mean_frames rounds half up: {m}")

    W, H = 1280, 960
    flat = bytes([100, 150, 200]) * (W * H)
    boxes = rc.box_means(W, H, flat)
    expect(set(boxes) == set(rc.BOXES) and all(v == [100.0, 150.0, 200.0] for v in boxes.values()),
           f"box_means over a flat frame: {boxes}")

    class Args:
        views = None
        anchors = None
        map = "valley"
    views = rc.views_for(Args, rc.load_screenshot_set())
    ss = rc.load_screenshot_set()
    expect([v["name"] for v in views] == [v[0] for v in ss.VIEWS] + [v[0] for v in ss.EXTRA_VIEWS.get("valley", [])],
           "the default views are screenshot_set.py's")
    fwd = views[0]
    expect((fwd["x"], fwd["y"], fwd["z"], fwd["yaw"]) == tuple(ss.ANCHORS["valley"]),
           f"the forward view is the anchor itself: {fwd}")
    expect(all("noclip" in v for v in views), "every default view says whether it needs noclip")

    if failures:
        print(f"FAIL: retail capture tool ({len(failures)})")
        return 1
    print("PASS: retail capture tool (output guard, PNG, frame mean, boxes, views)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
