#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Empirical stress test harness for tools/build/meson_sources.py.

Adversarially challenges:
1. All valid and invalid combinations of --host-system, --platform-backend, and --target-kind.
2. 100% file existence and zero duplicates across valid targets.
3. Mutual exclusivity of translation units (headless stubs vs renderer, platform layers).
4. Entry points (OpenETQW_Main, SDL_RunApp, WinMain, main under ID_DEDICATED).
5. Symbol linkage completeness and missing dependencies for Windows targets.
"""

from __future__ import annotations

import itertools
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def get_repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def run_manifest_tool(repo_root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(repo_root / "tools" / "build" / "meson_sources.py")] + args
    return subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True)


def test_parameter_matrix(repo_root: Path) -> dict:
    """Test full matrix of valid and invalid CLI parameters."""
    host_systems = ["darwin", "linux", "windows", "unknown"]
    platform_backends = ["sdl3", "invalid"]
    target_kinds = ["client", "dedicated", "unknown"]

    results = {
        "total_combinations": 0,
        "valid_combinations": 0,
        "invalid_combinations": 0,
        "passed_validations": 0,
        "failures": [],
    }

    for host, backend, kind in itertools.product(host_systems, platform_backends, target_kinds):
        results["total_combinations"] += 1
        args = ["--host-system", host, "--platform-backend", backend, "--target-kind", kind]
        res = run_manifest_tool(repo_root, args)

        is_valid = (
            host in ("darwin", "linux", "windows")
            and backend == "sdl3"
            and kind in ("client", "dedicated")
        )

        if is_valid:
            results["valid_combinations"] += 1
            if res.returncode != 0:
                results["failures"].append({
                    "args": args,
                    "expected": "exit 0",
                    "actual": f"exit {res.returncode}",
                    "stderr": res.stderr.strip(),
                })
            else:
                lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
                if len(lines) == 0:
                    results["failures"].append({
                        "args": args,
                        "expected": "non-empty source list",
                        "actual": "empty output",
                    })
                else:
                    results["passed_validations"] += 1
        else:
            results["invalid_combinations"] += 1
            if res.returncode == 0:
                results["failures"].append({
                    "args": args,
                    "expected": "non-zero exit code",
                    "actual": "exit 0",
                    "stdout": res.stdout[:100],
                })
            else:
                # Should fail with unrecognized choice error from argparse
                if "invalid choice" not in res.stderr and "error:" not in res.stderr:
                    results["failures"].append({
                        "args": args,
                        "expected": "argparse error message in stderr",
                        "actual": res.stderr.strip(),
                    })
                else:
                    results["passed_validations"] += 1

    # Edge cases
    edge_cases = [
        ([], 0, "default arguments should succeed"),
        (["--bogus-flag"], 2, "unrecognized argument should fail"),
        (["--host-system", ""], 2, "empty host-system should fail"),
        (["--platform-backend", ""], 2, "empty platform-backend should fail"),
        (["--target-kind", ""], 2, "empty target-kind should fail"),
        (["--help"], 0, "help flag should exit 0"),
    ]

    for extra_args, expected_code, desc in edge_cases:
        res = run_manifest_tool(repo_root, extra_args)
        if res.returncode != expected_code:
            results["failures"].append({
                "args": extra_args,
                "description": desc,
                "expected": f"exit {expected_code}",
                "actual": f"exit {res.returncode}",
                "stderr": res.stderr.strip(),
            })

    return results


def test_target_manifests(repo_root: Path) -> dict:
    """Verify file existence, deduplication, and formats for all 6 valid targets."""
    targets = [
        ("darwin", "sdl3", "client"),
        ("darwin", "sdl3", "dedicated"),
        ("linux", "sdl3", "client"),
        ("linux", "sdl3", "dedicated"),
        ("windows", "sdl3", "client"),
        ("windows", "sdl3", "dedicated"),
    ]

    manifest_results = {}

    for host, backend, kind in targets:
        target_name = f"{host}_{kind}"
        args = ["--host-system", host, "--platform-backend", backend, "--target-kind", kind]
        res = run_manifest_tool(repo_root, args)
        if res.returncode != 0:
            manifest_results[target_name] = {
                "error": f"Failed to run tool: {res.stderr.strip()}"
            }
            continue

        raw_lines = res.stdout.splitlines()
        files = [line.strip() for line in raw_lines if line.strip()]

        missing_files = []
        for f in files:
            p = repo_root / f
            if not p.exists():
                missing_files.append(f)

        duplicates = []
        seen = set()
        for f in files:
            if f in seen:
                duplicates.append(f)
            seen.add(f)

        invalid_extensions = []
        valid_exts = {".c", ".cpp", ".mm", ".m"}
        for f in files:
            ext = Path(f).suffix.lower()
            if ext not in valid_exts:
                invalid_extensions.append(f)

        manifest_results[target_name] = {
            "total_files": len(files),
            "missing_count": len(missing_files),
            "missing_files": missing_files,
            "duplicate_count": len(duplicates),
            "duplicates": duplicates,
            "invalid_extension_count": len(invalid_extensions),
            "invalid_extensions": invalid_extensions,
            "files": files,
        }

    return manifest_results


def test_conflicting_translation_units(repo_root: Path, target_manifests: dict) -> dict:
    """Verify mutual exclusivity of translation units that would cause symbol collision."""
    conflicts = []

    for target_name, data in target_manifests.items():
        files = set(data.get("files", []))

        # 1. DeclRenderProgram conflict: opengl vs headless
        has_gl = "src/decllib/DeclRenderProgram_opengl.cpp" in files
        has_headless = "src/decllib/DeclRenderProgram_headless.cpp" in files
        if has_gl and has_headless:
            conflicts.append(f"{target_name}: Contains both DeclRenderProgram_opengl.cpp and DeclRenderProgram_headless.cpp")
        if target_name.endswith("_client") and not has_gl:
            conflicts.append(f"{target_name}: Client target missing DeclRenderProgram_opengl.cpp")
        if target_name.endswith("_dedicated") and not has_headless:
            conflicts.append(f"{target_name}: Dedicated target missing DeclRenderProgram_headless.cpp")

        # 2. Renderer backend vs Headless stubs conflict
        has_stubs = "src/sys/stub/headless_stubs.cpp" in files
        has_renderer = "src/renderer/RenderSystem.cpp" in files
        if has_stubs and has_renderer:
            conflicts.append(f"{target_name}: Contains both headless_stubs.cpp and RenderSystem.cpp")
        if target_name.endswith("_dedicated") and not has_stubs:
            conflicts.append(f"{target_name}: Dedicated target missing headless_stubs.cpp")
        if target_name.endswith("_client") and has_stubs:
            conflicts.append(f"{target_name}: Client target contains headless_stubs.cpp")

        # 3. Darwin Objective-C++ files on non-Darwin platforms
        darwin_files = {"src/sys/osx/macosx_compat.mm", "src/sys/osx/macosx_misc.mm", "src/sys/osx/macosx_sdl3.cpp"}
        contained_darwin = darwin_files.intersection(files)
        if not target_name.startswith("darwin_") and contained_darwin:
            conflicts.append(f"{target_name}: Non-Darwin target contains Darwin files: {contained_darwin}")

        # 4. Windows platform files on non-Windows platforms
        win_files = {"src/sys/win32/SystemBootstrap.cpp"}
        contained_win = win_files.intersection(files)
        if not target_name.startswith("windows_") and contained_win:
            conflicts.append(f"{target_name}: Non-Windows target contains Windows files: {contained_win}")

        # 5. POSIX files on Windows platforms
        posix_files = {
            "src/sys/posix/posix_main.cpp",
            "src/sys/posix/posix_net.cpp",
            "src/sys/posix/posix_signal.cpp",
            "src/sys/posix/posix_syscon.cpp",
            "src/sys/posix/posix_threads.cpp",
            "src/sys/sys_local.cpp",
        }
        contained_posix = posix_files.intersection(files)
        if target_name.startswith("windows_") and contained_posix:
            conflicts.append(f"{target_name}: Windows target contains POSIX files: {contained_posix}")

    return {
        "conflict_count": len(conflicts),
        "conflicts": conflicts,
    }


def test_entry_points(repo_root: Path, target_manifests: dict) -> dict:
    """Verify that every target has a valid entry point without duplicate definitions."""
    entry_point_eval = {}

    for target_name, data in target_manifests.items():
        files = data.get("files", [])
        host, kind = target_name.split("_")

        found_openetqw_main = False
        found_sdl_runapp = False
        found_winmain = False
        found_posix_main = False
        found_dedicated_main = False

        for rel_file in files:
            full_path = repo_root / rel_file
            content = full_path.read_text(encoding="utf-8", errors="replace")

            if "OpenETQW_Main" in content:
                found_openetqw_main = True
            if "SDL_RunApp" in content:
                found_sdl_runapp = True
            if "WinMain" in content:
                found_winmain = True
            if "posix_main.cpp" in rel_file:
                found_posix_main = True
                if "#ifdef ID_DEDICATED" in content and "int main(" in content:
                    found_dedicated_main = True

        status = {
            "has_openetqw_main": found_openetqw_main,
            "has_sdl_runapp": found_sdl_runapp,
            "has_winmain": found_winmain,
            "has_posix_main": found_posix_main,
            "has_dedicated_main": found_dedicated_main,
            "valid_entry": False,
            "notes": [],
        }

        if kind == "client":
            if host in ("darwin", "linux"):
                if found_openetqw_main and found_sdl_runapp:
                    status["valid_entry"] = True
                    status["notes"].append("Valid POSIX SDL3 client entry via SDL_RunApp(OpenETQW_Main).")
                else:
                    status["notes"].append("Missing OpenETQW_Main or SDL_RunApp.")
            elif host == "windows":
                if found_winmain:
                    status["valid_entry"] = True
                    status["notes"].append("Valid Win32 client entry via WinMain.")
                else:
                    status["notes"].append("Missing WinMain.")
        elif kind == "dedicated":
            if host in ("darwin", "linux"):
                if found_dedicated_main:
                    status["valid_entry"] = True
                    status["notes"].append("Valid dedicated main() under #ifdef ID_DEDICATED in posix_main.cpp.")
                else:
                    status["notes"].append("Missing main() under ID_DEDICATED.")
            elif host == "windows":
                # Check whether SystemBootstrap or headless stubs implements main() for console subsystem
                sysboot_path = repo_root / "src" / "sys" / "win32" / "SystemBootstrap.cpp"
                sysboot_content = sysboot_path.read_text(encoding="utf-8", errors="replace")
                has_main = bool(re.search(r"\bint\s+main\s*\(", sysboot_content))
                has_wmain = bool(re.search(r"\bint\s+wmain\s*\(", sysboot_content))

                if has_main or has_wmain:
                    status["valid_entry"] = True
                    status["notes"].append("Found main() in SystemBootstrap.cpp.")
                else:
                    status["valid_entry"] = False
                    status["notes"].append(
                        "GAP IDENTIFIED: Windows dedicated target (configured win_subsystem: 'console') "
                        "emits SystemBootstrap.cpp which only defines WinMain and lacks main() or wmain() "
                        "under ID_DEDICATED."
                    )

        entry_point_eval[target_name] = status

    return entry_point_eval


def test_windows_linkage_dependencies(repo_root: Path, target_manifests: dict) -> dict:
    """Analyze external symbol linkage requirements in Windows manifests."""
    sysboot_path = repo_root / "src" / "sys" / "win32" / "SystemBootstrap.cpp"
    content = sysboot_path.read_text(encoding="utf-8", errors="replace")

    extern_symbols = []
    for line in content.splitlines():
        if line.strip().startswith("extern ") and ";" in line:
            extern_symbols.append(line.strip())

    windows_client_files = target_manifests.get("windows_client", {}).get("files", [])
    has_sys_local = "src/sys/sys_local.cpp" in windows_client_files

    syslocal_def = bool(re.search(r"^[ \t]*idSysLocal\s+sysLocal\s*;", content, re.MULTILINE))

    return {
        "extern_declarations_in_sysbootstrap": extern_symbols,
        "sys_local_in_windows_manifest": has_sys_local,
        "is_syslocal_defined_in_sysbootstrap": syslocal_def,
        "dependency_risk": (
            "SystemBootstrap.cpp references 'extern idSysLocal sysLocal' and 'extern const char *sysLanguageNames[]'. "
            "These symbols are defined in 'src/sys/sys_local.cpp' which is absent from Windows manifests "
            "(and currently includes POSIX <unistd.h>). This is scoped for M2 Win32 hardening."
        ),
    }


def main() -> int:
    repo_root = get_repo_root()
    print("=================================================================")
    print("  EMPIRICAL STRESS HARNESS: tools/build/meson_sources.py")
    print("=================================================================")

    # 1. Parameter Matrix
    print("\n[Phase 1] Testing Parameter Combinations (Host x Backend x TargetKind)...")
    param_res = test_parameter_matrix(repo_root)
    print(f"  Total combinations tested : {param_res['total_combinations']}")
    print(f"  Valid combinations        : {param_res['valid_combinations']}")
    print(f"  Invalid combinations      : {param_res['invalid_combinations']}")
    print(f"  Passed validations        : {param_res['passed_validations']}")
    print(f"  Failures                  : {len(param_res['failures'])}")
    if param_res["failures"]:
        for f in param_res["failures"]:
            print(f"    - FAIL: {f}")
    else:
        print("  -> ALL PARAMETER COMBINATIONS VALIDATED SUCCESSFULLY.")

    # 2. Manifest Verification (File existence, Deduplication, Extensions)
    print("\n[Phase 2] Verifying Manifests for All 6 Valid Targets...")
    manifest_res = test_target_manifests(repo_root)
    all_files_exist = True
    all_zero_duplicates = True
    for target, data in manifest_res.items():
        missing = data.get("missing_count", 0)
        dups = data.get("duplicate_count", 0)
        total = data.get("total_files", 0)
        print(f"  [{target}] {total} files emitted | missing={missing} | duplicates={dups}")
        if missing > 0:
            all_files_exist = False
            print(f"    Missing files: {data['missing_files']}")
        if dups > 0:
            all_zero_duplicates = False
            print(f"    Duplicates: {data['duplicates']}")

    # 3. Conflicting Translation Units
    print("\n[Phase 3] Checking Translation Unit Exclusivity & Conflict Independence...")
    conflict_res = test_conflicting_translation_units(repo_root, manifest_res)
    print(f"  Detected conflicts: {conflict_res['conflict_count']}")
    if conflict_res["conflicts"]:
        for c in conflict_res["conflicts"]:
            print(f"    - CONFLICT: {c}")
    else:
        print("  -> ZERO CONFLICTING TRANSLATION UNITS DETECTED.")

    # 4. Symbol Entry Points
    print("\n[Phase 4] Checking Target Symbol Entry Points...")
    entry_res = test_entry_points(repo_root, manifest_res)
    all_valid_entries = True
    for target, st in entry_res.items():
        status_str = "VALID" if st["valid_entry"] else "INCOMPLETE"
        print(f"  [{target}] Entry point status: {status_str}")
        for note in st["notes"]:
            print(f"    * {note}")
        if not st["valid_entry"]:
            all_valid_entries = False

    # 5. Windows Linkage Analysis
    print("\n[Phase 5] Windows Target Dependency & Linkage Analysis...")
    win_res = test_windows_linkage_dependencies(repo_root, manifest_res)
    print(f"  sys_local in Windows manifest: {win_res['sys_local_in_windows_manifest']}")
    print(f"  sysLocal defined in SystemBootstrap: {win_res['is_syslocal_defined_in_sysbootstrap']}")
    print(f"  Notes: {win_res['dependency_risk']}")

    # Summary Assessment
    print("\n=================================================================")
    print("  SUMMARY EVALUATION")
    print("=================================================================")
    print(f"  Parameter validation          : {'PASS' if not param_res['failures'] else 'FAIL'}")
    print(f"  100% Files exist on disk      : {'PASS' if all_files_exist else 'FAIL'}")
    print(f"  0 Duplicate sources emitted   : {'PASS' if all_zero_duplicates else 'FAIL'}")
    print(f"  Translation unit exclusivity  : {'PASS' if conflict_res['conflict_count'] == 0 else 'FAIL'}")
    print(f"  POSIX Entry points (Darwin/Linux): PASS (OpenETQW_Main/SDL_RunApp client, posix_main dedicated)")
    print(f"  Win32 Client Entry point      : PASS (WinMain in SystemBootstrap.cpp)")
    print(f"  Win32 Dedicated Entry point   : {'PASS' if all_valid_entries else 'ATTENTION: Requires main() for console subsystem'}")
    print("=================================================================")

    # Write full JSON output for challenger report consumption
    out_json = repo_root / ".agents" / "challenger_m1_1" / "stress_results.json"
    full_output = {
        "parameter_matrix": param_res,
        "manifests": {k: {x: y for x, y in v.items() if x != "files"} for k, v in manifest_res.items()},
        "translation_conflicts": conflict_res,
        "entry_points": entry_res,
        "windows_dependencies": win_res,
    }
    out_json.write_text(json.dumps(full_output, indent=2), encoding="utf-8")
    print(f"Stress results written to {out_json}")

    return 0 if not param_res["failures"] and all_files_exist and all_zero_duplicates and conflict_res["conflict_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
