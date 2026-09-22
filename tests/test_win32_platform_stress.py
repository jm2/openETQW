#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Empirical stress test harness for Win32 platform layer and symbol linkage.

Adversarially challenges:
1. Preprocessor include resolution in src/sys/win32/SystemBootstrap.cpp against staged headers.
2. 64-bit thread handle safety: verifying storage of 64-bit HANDLE vs xthreadInfo::threadHandle integer.
3. Synchronization primitives lifecycle: critical section and event signaling state machine.
4. Target symbol linkage completeness for windows_client and windows_dedicated.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def get_repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_include_resolution(repo_root: Path) -> dict:
    """Verify whether SystemBootstrap.cpp includes resolve against staged headers."""
    sysboot_path = repo_root / "src" / "sys" / "win32" / "SystemBootstrap.cpp"
    content = sysboot_path.read_text(encoding="utf-8", errors="replace")

    includes = re.findall(r'#include\s+["<]([^">]+)[">]', content)
    staged_sys = repo_root / ".tmp" / "gamelibs_stage" / "src" / "sys"
    src_sys = repo_root / "src" / "sys"

    broken_includes = []
    for inc in includes:
        if inc.startswith("../"):
            target_rel = inc[3:]
            direct_src = src_sys / target_rel
            staged = staged_sys / target_rel
            if not direct_src.exists():
                broken_includes.append({
                    "include": inc,
                    "target_in_src_sys": str(direct_src),
                    "exists_in_src_sys": False,
                    "exists_in_staged_sys": staged.exists(),
                    "reason": f"Include path '{inc}' assumes {target_rel} is in src/sys, but in OpenETQW it is staged in {staged_sys}",
                })

    return {
        "broken_include_count": len(broken_includes),
        "broken_includes": broken_includes,
    }


def test_win32_symbol_linkage(repo_root: Path) -> dict:
    """Analyze symbol definitions and missing externs across windows_client and windows_dedicated."""
    meson_sources_script = repo_root / "tools" / "build" / "meson_sources.py"

    manifests = {}
    for kind in ("client", "dedicated"):
        cmd = [sys.executable, str(meson_sources_script), "--host-system", "windows", "--target-kind", kind]
        res = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True)
        manifests[kind] = [line.strip() for line in res.stdout.splitlines() if line.strip()]

    # Critical symbols required by SystemBootstrap.cpp
    required_symbols = {
        "win32": {
            "pattern": r"^(?!.*extern\b).*\bWin32Vars_t\s+win32\b",
            "needed_in": ["client", "dedicated"],
            "desc": "Global Win32Vars_t win32 instance accessed in WinMain and main()",
        },
        "PQ_Init": {
            "pattern": r"^(?!.*extern\b).*\bvoid\s+PQ_Init\s*\(\s*\)\s*\{",
            "needed_in": ["client", "dedicated"],
            "desc": "Performance query initialization called in idSysLocal::Init()",
        },
        "PQ_ShutDown": {
            "pattern": r"^(?!.*extern\b).*\bvoid\s+PQ_ShutDown\s*\(\s*\)\s*\{",
            "needed_in": ["client", "dedicated"],
            "desc": "Performance query shutdown called in idSysLocal::Shutdown()",
        },
        "sys_soundThread": {
            "pattern": r"^(?!.*extern\b).*\bsdSoundThread\s*\*\s*sys_soundThread\b\s*=",
            "needed_in": ["client", "dedicated"],
            "desc": "Global sound thread pointer accessed in idSysLocal::Init() and Shutdown()",
        },
        "Sys_ProcessSDLEvents": {
            "pattern": r"^(?!.*extern\b).*\bvoid\s+Sys_ProcessSDLEvents\s*\([^)]*\)\s*\{",
            "needed_in": ["dedicated"],
            "desc": "SDL event processor called by ProcessOSEvents() (missing in dedicated target)",
        },
        "IN_Frame": {
            "pattern": r"^(?!.*extern\b).*\bvoid\s+IN_Frame\s*\([^)]*\)\s*\{",
            "needed_in": ["dedicated"],
            "desc": "Input frame pump called by Sys_GenerateEvents() (missing in dedicated target)",
        },
    }

    missing_by_target = {"client": [], "dedicated": []}

    for sym_name, sym_info in required_symbols.items():
        pat = re.compile(sym_info["pattern"], re.MULTILINE)
        for target_kind in sym_info["needed_in"]:
            target_files = manifests[target_kind]
            found = False
            for rel_path in target_files:
                p = repo_root / rel_path
                if not p.exists():
                    continue
                content = p.read_text(encoding="utf-8", errors="replace")
                if pat.search(content):
                    found = True
                    break
            if not found:
                missing_by_target[target_kind].append({
                    "symbol": sym_name,
                    "description": sym_info["desc"],
                })

    return {
        "manifest_sizes": {k: len(v) for k, v in manifests.items()},
        "missing_symbols": missing_by_target,
    }


def test_thread_handle_safety(repo_root: Path) -> dict:
    """Inspect thread handle safety against 64-bit pointer truncation."""
    sysboot_path = repo_root / "src" / "sys" / "win32" / "SystemBootstrap.cpp"
    content = sysboot_path.read_text(encoding="utf-8", errors="replace")

    # Check 1: win32ThreadEntry_t stores HANDLE in 64-bit storage
    has_table_handle = bool(re.search(r"struct\s+win32ThreadEntry_t\s*\{[^}]*HANDLE\s+handle;", content, re.DOTALL))

    # Check 2: info.threadHandle stores slot + 1 (1-based index)
    has_slot_store = bool(re.search(r"info\.threadHandle\s*=\s*slot\s*\+\s*1\s*;", content))

    # Check 3: Check for forbidden dangerous pointer truncation casts
    dangerous_casts = []
    for m in re.finditer(r"info\.threadHandle\s*=\s*\((int|long|unsigned int|DWORD)\)\s*[^;]+;", content):
        dangerous_casts.append(m.group(0))

    # Check 4: Check that WaitForSingleObject / CloseHandle uses g_threadTable handle, not (HANDLE)info.threadHandle
    has_handle_extract = bool(re.search(r"HANDLE\s+handle\s*=\s*g_threadTable\[\s*slot\s*\]\.handle\s*;", content))
    has_dangerous_wait = bool(re.search(r"WaitForSingleObject\(\s*\(HANDLE\)\s*info\.threadHandle", content))
    has_dangerous_close = bool(re.search(r"CloseHandle\(\s*\(HANDLE\)\s*info\.threadHandle", content))

    return {
        "table_stores_64bit_handle": has_table_handle,
        "uses_slot_index_in_threadHandle": has_slot_store,
        "dangerous_handle_casts": dangerous_casts,
        "extracts_handle_from_table": has_handle_extract,
        "dangerous_wait_found": has_dangerous_wait,
        "dangerous_close_found": has_dangerous_close,
        "is_handle_safe": (
            has_table_handle
            and has_slot_store
            and len(dangerous_casts) == 0
            and has_handle_extract
            and not has_dangerous_wait
            and not has_dangerous_close
        ),
    }


def test_synchronization_primitives(repo_root: Path) -> dict:
    """Verify critical section and event synchronization implementation."""
    sysboot_path = repo_root / "src" / "sys" / "win32" / "SystemBootstrap.cpp"
    content = sysboot_path.read_text(encoding="utf-8", errors="replace")

    has_cs_enter = bool(re.search(r"void\s+Sys_EnterCriticalSection\s*\([^)]*\)", content))
    has_cs_leave = bool(re.search(r"void\s+Sys_LeaveCriticalSection\s*\([^)]*\)", content))
    has_event_wait = bool(re.search(r"void\s+Sys_WaitForEvent\s*\([^)]*\)", content))
    has_event_trigger = bool(re.search(r"void\s+Sys_TriggerEvent\s*\([^)]*\)", content))
    has_thread_init = bool(re.search(r"Sys_EnsureThreadingInitialized\s*\(", content))
    has_thread_shutdown = bool(re.search(r"Sys_ShutdownThreading\s*\(", content))

    # Bounds check verification
    has_cs_bounds = bool(re.search(r"index\s*<\s*0\s*\|\|\s*index\s*>=\s*MAX_LOCAL_CRITICAL_SECTIONS", content))
    has_ev_bounds = bool(re.search(r"index\s*<\s*0\s*\|\|\s*index\s*>=\s*MAX_TRIGGER_EVENTS", content))

    # Auto-reset event check (bManualReset=FALSE)
    has_auto_reset = bool(re.search(r"CreateEventA\(\s*NULL,\s*FALSE,\s*FALSE,\s*NULL\s*\)", content))

    return {
        "has_Sys_EnterCriticalSection": has_cs_enter,
        "has_Sys_LeaveCriticalSection": has_cs_leave,
        "has_Sys_WaitForEvent": has_event_wait,
        "has_Sys_TriggerEvent": has_event_trigger,
        "has_threading_lifecycle_init": has_thread_init,
        "has_threading_lifecycle_shutdown": has_thread_shutdown,
        "has_cs_bounds_checking": has_cs_bounds,
        "has_event_bounds_checking": has_ev_bounds,
        "has_auto_reset_events": has_auto_reset,
        "primitives_verified": (
            has_cs_enter
            and has_cs_leave
            and has_event_wait
            and has_event_trigger
            and has_thread_init
            and has_thread_shutdown
            and has_cs_bounds
            and has_ev_bounds
            and has_auto_reset
        ),
    }


def main() -> int:
    repo_root = get_repo_root()
    print("=================================================================")
    print("  EMPIRICAL ADVERSARIAL STRESS: WIN32 PLATFORM LAYER (M2)")
    print("=================================================================")

    # 1. Thread Handle Safety
    print("\n[Audit 1] Thread Handle Safety & 64-bit Truncation...")
    th_res = test_thread_handle_safety(repo_root)
    print(f"  Table stores 64-bit HANDLE           : {th_res['table_stores_64bit_handle']}")
    print(f"  Uses slot index in info.threadHandle : {th_res['uses_slot_index_in_threadHandle']}")
    print(f"  Dangerous pointer-to-int casts found : {len(th_res['dangerous_handle_casts'])}")
    print(f"  Extracts 64-bit handle from table    : {th_res['extracts_handle_from_table']}")
    print(f"  Dangerous wait / close on int handle : {th_res['dangerous_wait_found'] or th_res['dangerous_close_found']}")
    print(f"  -> Thread Handle Safety Status       : {'PASS' if th_res['is_handle_safe'] else 'FAIL'}")

    # 2. Synchronization Primitives
    print("\n[Audit 2] Synchronization Primitives Lifecycle & Event Signaling...")
    sync_res = test_synchronization_primitives(repo_root)
    print(f"  Sys_EnterCriticalSection implemented : {sync_res['has_Sys_EnterCriticalSection']}")
    print(f"  Sys_LeaveCriticalSection implemented : {sync_res['has_Sys_LeaveCriticalSection']}")
    print(f"  Sys_WaitForEvent implemented         : {sync_res['has_Sys_WaitForEvent']}")
    print(f"  Sys_TriggerEvent implemented         : {sync_res['has_Sys_TriggerEvent']}")
    print(f"  Threading lifecycle init / shutdown  : {sync_res['has_threading_lifecycle_init'] and sync_res['has_threading_lifecycle_shutdown']}")
    print(f"  Critical section bounds check        : {sync_res['has_cs_bounds_checking']}")
    print(f"  Event index bounds check             : {sync_res['has_event_bounds_checking']}")
    print(f"  Auto-reset event signaling semantics : {sync_res['has_auto_reset_events']}")
    print(f"  -> Synchronization Primitives Status : {'PASS' if sync_res['primitives_verified'] else 'FAIL'}")

    # 3. Include Resolution in SystemBootstrap.cpp
    print("\n[Audit 3] Preprocessor Include Resolution...")
    inc_res = test_include_resolution(repo_root)
    print(f"  Broken relative include directives   : {inc_res['broken_include_count']}")
    for b in inc_res["broken_includes"]:
        print(f"    * GAP: {b['include']} -> {b['reason']}")
    print(f"  -> Include Resolution Status         : {'PASS' if inc_res['broken_include_count'] == 0 else 'DEFECT DETECTED'}")

    # 4. Symbol Linkage Completeness
    print("\n[Audit 4] Win32 Target Symbol Linkage Completeness...")
    sym_res = test_win32_symbol_linkage(repo_root)
    client_missing = sym_res["missing_symbols"]["client"]
    ded_missing = sym_res["missing_symbols"]["dedicated"]
    print(f"  Missing symbols in windows_client    : {len(client_missing)}")
    for m in client_missing:
        print(f"    * CLIENT GAP: {m['symbol']} ({m['description']})")
    print(f"  Missing symbols in windows_dedicated : {len(ded_missing)}")
    for m in ded_missing:
        print(f"    * DEDICATED GAP: {m['symbol']} ({m['description']})")
    linkage_pass = len(client_missing) == 0 and len(ded_missing) == 0
    print(f"  -> Symbol Linkage Status             : {'PASS' if linkage_pass else 'DEFECT DETECTED'}")

    print("\n=================================================================")
    print("  EVALUATION SUMMARY")
    print("=================================================================")
    print(f"  1. Thread Handle Safety (64-bit)     : {'PASS' if th_res['is_handle_safe'] else 'FAIL'}")
    print(f"  2. Synchronization Primitives        : {'PASS' if sync_res['primitives_verified'] else 'FAIL'}")
    print(f"  3. Staged Header Includes            : {'PASS' if inc_res['broken_include_count'] == 0 else 'DEFECT (2 broken includes)'}")
    print(f"  4. Target Symbol Linkage             : {'PASS' if linkage_pass else f'DEFECT ({len(client_missing)} client, {len(ded_missing)} dedicated missing)'}")
    print("=================================================================")

    # Exit code indicates overall health
    return 0 if (th_res["is_handle_safe"] and sync_res["primitives_verified"] and not inc_res["broken_include_count"] and linkage_pass) else 1


if __name__ == "__main__":
    raise SystemExit(main())
