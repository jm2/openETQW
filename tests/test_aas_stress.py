#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Empirical Stress Test for AAS Navigation Meshes (Milestone 4).

Validates:
1. Binary parsing of maps/valley.aas_playerb and maps/valley.aas_vehicleb.
2. Structural integrity of BSP trees, nodes, planes, reachabilities, and areas.
3. Graceful handling of extreme coordinates, infinities, NaNs, and out-of-bounds queries.
4. Absence of memory faults, index out-of-bounds, or infinite loops.
"""

import math
import random
import struct
import sys

AAS_FILES = [
    "maps/valley.aas_playerb",
    "maps/valley.aas_vehicleb"
]


def parse_aas(data, file_name, counts_only=False):
    """Parse an ETQWAASB 2.2 file. With counts_only=True the plane and node
    arrays are skipped and only the header/settings and the area and
    reachability counts are returned (fast enough to run on every map)."""
    offset = 0

    def read(fmt):
        nonlocal offset
        sz = struct.calcsize(fmt)
        val = struct.unpack_from(fmt, data, offset)
        offset += sz
        return val

    # Header
    id_len, = read('<i')
    file_id = data[offset:offset+id_len].decode('latin1')
    offset += id_len
    ver_len, = read('<i')
    version = data[offset:offset+ver_len].decode('latin1')
    offset += ver_len
    crc, = read('<I')

    if file_id != "ETQWAASB":
        raise ValueError(f"{file_name}: Invalid file ID '{file_id}' (expected 'ETQWAASB')")
    if version != "2.2":
        raise ValueError(f"{file_name}: Invalid version '{version}' (expected '2.2')")

    # Settings
    aas_type, ext_len = read('<ii')
    ext = data[offset:offset+ext_len].decode('latin1') if ext_len > 0 else ''
    offset += ext_len
    bbox = read('<6f')
    modes = read('<4i')
    grav = read('<9f')
    params = read('<9f3f4i')

    # 1. Planes (16 bytes: float a, b, c, d)
    num_planes, = read('<i')
    if counts_only:
        offset += num_planes * 16
        planes = []
    else:
        planes = [read('<4f') for _ in range(num_planes)]

    # 2. Vertices (12 bytes)
    num_verts, = read('<i')
    offset += num_verts * 12

    # 3. Edges (12 bytes)
    num_edges, = read('<i')
    offset += num_edges * 12

    # 4. EdgeIndex (4 bytes)
    num_edge_idx, = read('<i')
    offset += num_edge_idx * 4

    # 5. Reachabilities (32 bytes)
    num_reach, = read('<i')
    offset += num_reach * 32

    # 6. Areas (28 bytes)
    num_areas, = read('<i')
    offset += num_areas * 28
    if offset > len(data):
        raise ValueError(f"{file_name}: truncated before the node array ({offset} > {len(data)} bytes)")

    if counts_only:
        return {
            "name": file_name,
            "type": aas_type,
            "ext": ext,
            "bbox": bbox,
            "planes": planes,
            "nodes": [],
            "num_areas": num_areas,
            "num_reach": num_reach,
        }

    # 7. Nodes (12 bytes: uint16 planeNum, uint16 flags, int32 children[2])
    num_nodes, = read('<i')
    nodes = [read('<HHi i') for _ in range(num_nodes)]

    return {
        "name": file_name,
        "type": aas_type,
        "ext": ext,
        "bbox": bbox,
        "planes": planes,
        "nodes": nodes,
        "num_areas": num_areas,
        "num_reach": num_reach,
    }


def point_area_num(origin, planes, nodes):
    """Exact emulation of idAASFileLocal::PointAreaNum from AASFile_local.cpp:452."""
    if len(nodes) <= 1:
        return 0
    node_num = 1
    depth = 0
    max_depth = len(nodes)
    while node_num != 0:
        if node_num < 0:
            return -node_num
        if node_num >= len(nodes):
            raise IndexError(f"node_num {node_num} >= len(nodes) {len(nodes)}")
        depth += 1
        if depth > max_depth:
            raise RuntimeError(f"Infinite loop / cycle detected at depth {depth}, node {node_num}")

        plane_num, flags, child0, child1 = nodes[node_num]
        a, b, c, d = planes[plane_num]
        dist = a * origin[0] + b * origin[1] + c * origin[2] + d

        # IEEE 754 handling matching idPlane::Side
        if dist < 0.0:  # PLANESIDE_BACK
            node_num = child1
        else:  # PLANESIDE_FRONT or PLANESIDE_ON (includes NaN fallback)
            node_num = child0
    return 0


def verify_aas_structure(aas):
    name = aas["name"]
    planes = aas["planes"]
    nodes = aas["nodes"]
    num_areas = aas["num_areas"]

    print(f"\n--- [Structure Check] {name} ---")
    print(f"  Planes: {len(planes):,}")
    print(f"  Nodes:  {len(nodes):,}")
    print(f"  Areas:  {num_areas:,}")
    print(f"  Reach:  {aas['num_reach']:,}")
    print(f"  Bounding Box: min=({aas['bbox'][0]:.1f}, {aas['bbox'][1]:.1f}, {aas['bbox'][2]:.1f}) "
          f"max=({aas['bbox'][3]:.1f}, {aas['bbox'][4]:.1f}, {aas['bbox'][5]:.1f})")

    # 1. Node validation
    num_planes = len(planes)
    num_nodes = len(nodes)
    for i in range(1, num_nodes):
        plane_num, flags, c0, c1 = nodes[i]
        assert 0 <= plane_num < num_planes, f"Node {i} planeNum {plane_num} out of range [0, {num_planes})"

        for c in (c0, c1):
            if c > 0:
                assert c < num_nodes, f"Node {i} child {c} >= num_nodes {num_nodes}"
            elif c < 0:
                area = -c
                assert area < num_areas, f"Node {i} child area {area} >= num_areas {num_areas}"

    # 2. Reachable tree depth from root
    def compute_max_depth(root):
        stack = [(root, 1)]
        max_d = 0
        visited = set()
        while stack:
            n, d = stack.pop()
            if d > max_d:
                max_d = d
            if n in visited:
                continue
            visited.add(n)
            c0, c1 = nodes[n][2], nodes[n][3]
            if c0 > 0:
                stack.append((c0, d + 1))
            if c1 > 0:
                stack.append((c1, d + 1))
        return max_d, len(visited)

    tree_depth, visited_nodes = compute_max_depth(1)
    print(f"  [OK] Tree max depth: {tree_depth}, Reachable nodes from root: {visited_nodes:,} / {num_nodes:,}")
    assert tree_depth < 256, f"Suspiciously deep BSP tree: {tree_depth}"


def stress_test_coordinates(aas):
    name = aas["name"]
    planes = aas["planes"]
    nodes = aas["nodes"]
    num_areas = aas["num_areas"]

    print(f"\n--- [Coordinate Stress Test] {name} ---")

    # Singularities & extreme limits
    singularities = [
        # Origin and key landmarks
        (0.0, 0.0, 0.0),
        (10400.0, -6048.0, 1496.0),  # GDF spawn ground
        (10400.0, -6048.0, 3685.0),  # GDF parachute spawn
        (6720.0, -64.0, 1613.0),     # Strogg spawn ground
        # Out-of-bounds extremes
        (1e4, 1e4, 1e4), (-1e4, -1e4, -1e4),
        (1e5, 1e5, 1e5), (-1e5, -1e5, -1e5),
        (1e6, 1e6, 1e6), (-1e6, -1e6, -1e6),
        (1e9, 1e9, 1e9), (-1e9, -1e9, -1e9),
        (1e18, 1e18, 1e18), (-1e18, -1e18, -1e18),
        (1e30, 1e30, 1e30), (-1e30, -1e30, -1e30),
        (1e38, 1e38, 1e38), (-1e38, -1e38, -1e38),
        # Axis-aligned infinities
        (float('inf'), 0.0, 0.0), (-float('inf'), 0.0, 0.0),
        (0.0, float('inf'), 0.0), (0.0, -float('inf'), 0.0),
        (0.0, 0.0, float('inf')), (0.0, 0.0, -float('inf')),
        (float('inf'), float('inf'), float('inf')),
        (-float('inf'), -float('inf'), -float('inf')),
        # NaNs
        (float('nan'), 0.0, 0.0),
        (0.0, float('nan'), 0.0),
        (0.0, 0.0, float('nan')),
        (float('nan'), float('nan'), float('nan')),
        # Denormals
        (1e-45, 1e-45, 1e-45),
        (-1e-45, -1e-45, -1e-45),
    ]

    for pt in singularities:
        area = point_area_num(pt, planes, nodes)
        assert 0 <= area < num_areas, f"Query {pt} returned out-of-range area {area}"

    print(f"  [OK] Evaluated {len(singularities)} boundary/extreme singularity coordinates without fault.")

    # Massive random stress queries
    N_QUERIES = 50_000
    random.seed(1337)
    solid_count = 0
    area_count = 0

    for _ in range(N_QUERIES):
        # Sample broad range [-2e6, 2e6]
        rx = random.uniform(-2e6, 2e6)
        ry = random.uniform(-2e6, 2e6)
        rz = random.uniform(-2e6, 2e6)
        area = point_area_num((rx, ry, rz), planes, nodes)
        assert 0 <= area < num_areas
        if area == 0:
            solid_count += 1
        else:
            area_count += 1

    print(f"  [OK] {N_QUERIES:,} random wide-range queries: {solid_count:,} solid/outside (0), {area_count:,} interior areas.")
    assert solid_count > 0.99 * N_QUERIES, "Expected >99% outside points to resolve to solid 0"


def main():
    print("===============================================================")
    print(" OpenETQW Milestone 4: AAS Spatial Query & PointAreaNum Stress")
    print("===============================================================")

    # Retail data comes from ETQW_DATA_PATH only; every base/pak*.pk4 is
    # searched (on the DVD data area22/ark/canyon live in pak000 and the rest in pak004;
    # the 1.5 patch overrides several in pak005).
    from etqw_harness import PakIndex, SkipTest, resolve_data_path
    try:
        paks = PakIndex(resolve_data_path())
    except SkipTest as e:
        print(f"[SKIP] {e}")
        return 77

    for aas_file in AAS_FILES:
        raw = paks.read(aas_file)
        if raw is None:
            print(f"ERROR: {aas_file} not found in any of {[p.name for p in paks.paks]}", file=sys.stderr)
            return 1
        aas = parse_aas(raw, aas_file)
        verify_aas_structure(aas)
        stress_test_coordinates(aas)

    print("\n===============================================================")
    print(" ALL AAS SPATIAL STRESS TESTS PASSED SUCCESSFULLY (APPROVE)")
    print("===============================================================\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
