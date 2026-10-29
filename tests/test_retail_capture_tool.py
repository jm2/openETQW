#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""tools/validation/retail_capture.py's offline parts.

The capture itself needs the retail client, mutter and a GPU, so it is run by hand
(docs/retail-comparison.md § 1). Pinned here, with no retail data, display or ports:
  * --out inside this repository is refused before anything starts (AGENTS.md rule 3);
  * write_png writes a PNG whose decoded pixels are the input;
  * --out inside any git work tree is refused (a worktree run guards the main checkout too);
  * bgrx_to_rgb swaps B and R and skips row padding;
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
    sys.path.insert(0, str(REPO_ROOT / "tests"))
    import private_dirs

    # the output guard runs before the compositor or retail start
    proc = subprocess.run([sys.executable, str(TOOL), "--retail-dir", str(REPO_ROOT),
                           "--out", str(REPO_ROOT / "build" / "retail-shots")],
                          capture_output=True, text=True, timeout=30)
    expect(proc.returncode != 0 and "never enter a repository" in proc.stderr,
           f"--out inside the repository is refused (rc {proc.returncode}: {proc.stderr.strip()[-200:]})")
    expect(not (REPO_ROOT / "build" / "retail-shots").exists(), "the refused --out is not created")
    # any git work tree is refused, not only this one (a worktree run must not write into
    # the main checkout or the game repository either)
    with private_dirs.PrivateTempDir(prefix="openetqw-retail-capture-git-") as tmp:
        (Path(tmp) / "repo" / ".git").mkdir(parents=True)
        expect(rc.in_repository(Path(tmp) / "repo" / "a") is None,
               "an empty .git directory is not a repository")
        (Path(tmp) / "repo" / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        expect(rc.in_repository(Path(tmp) / "repo" / "a" / "b") == (Path(tmp) / "repo").resolve(),
               "a directory under another repository's work tree is refused")
        (Path(tmp) / "wt").mkdir()
        (Path(tmp) / "wt" / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt\n")
        expect(rc.in_repository(Path(tmp) / "wt" / "x") == (Path(tmp) / "wt").resolve(),
               "a directory under a git worktree is refused")
        expect(rc.in_repository(Path(tmp).parent) is None or (Path(tmp).parent / ".git").exists(),
               "a directory outside any work tree is allowed")

    w, h = 5, 3
    rgb = bytes((x * 40 + y * 7 + c) % 256 for y in range(h) for x in range(w) for c in range(3))
    with private_dirs.PrivateTempDir(prefix="openetqw-retail-capture-test-") as tmp:
        path = Path(tmp) / "t.png"
        rc.write_png(path, w, h, rgb)
        expect(read_png(path) == (w, h, rgb), "write_png round-trips its pixels")

    m = rc.mean_frames([(1, 1, bytes([10, 20, 31])), (1, 1, bytes([11, 20, 32]))])
    expect(m == (1, 1, bytes([11, 20, 32])), f"mean_frames rounds half up: {m}")

    # a gradient frame at twice 640x480: R = x mod 256, G = y mod 256 (in 640x480 units),
    # B = 7. A box in the wrong place, or
    # scaled wrongly, moves its mean.
    W, H = 1280, 960
    grad = bytes(v for y in range(H) for x in range(W) for v in (x // 2 % 256, y // 2 % 256, 7))
    boxes = rc.box_means(W, H, grad)
    for name, (x0, y0, x1, y1) in rc.BOXES.items():
        want = [round(sum(v % 256 for v in range(x0, x1)) / (x1 - x0), 2),
                round(sum(v % 256 for v in range(y0, y1)) / (y1 - y0), 2), 7.0]
        expect(boxes.get(name) == want, f"box_means {name}: {boxes.get(name)} != {want}")

    # BGRX rows with padding past 4 * w (stride > 4 * w), as XGetImage can return
    w2, h2, stride = 3, 2, 16
    raw = bytearray(stride * h2)
    for y in range(h2):
        for x in range(w2):
            raw[y * stride + x * 4: y * stride + x * 4 + 4] = bytes([10 * x + y, 50 + x, 100 + y, 0xEE])
        raw[y * stride + w2 * 4: (y + 1) * stride] = b"\xAA" * (stride - w2 * 4)
    want = bytes(v for y in range(h2) for x in range(w2) for v in (100 + y, 50 + x, 10 * x + y))
    expect(rc.bgrx_to_rgb(bytes(raw), w2, h2, stride) == want, "bgrx_to_rgb swaps B and R and skips row padding")

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
