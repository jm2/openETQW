#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Asset-free unit test for the release tools in tools/build/.

Exercises, in temporary git repositories and directories only:
  openetqw_release_version.py          bump modes, tag parsing, floors, overrides
  generate_release_changelog.py        header, sections, escaping, tracked override
  verify_release_asset_set.py          exact whitelist, archive member safety, JSON manifest
  verify_release_source_provenance.py  expected clean commits and the stage manifest
  build_windows_installer.py           --render-only output and payload checks

Plain Python (no pytest). Exit 0 when every check passes, 1 otherwise.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import lzma
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import private_dirs
import zipfile
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "tools" / "build"
VERSION_TOOL = BUILD / "openetqw_release_version.py"
CHANGELOG_TOOL = BUILD / "generate_release_changelog.py"
ASSET_TOOL = BUILD / "verify_release_asset_set.py"
PROVENANCE_TOOL = BUILD / "verify_release_source_provenance.py"
INSTALLER_TOOL = BUILD / "build_windows_installer.py"

FAILURES: list[str] = []
CHECKS = 0

GIT_ENV = dict(os.environ)
GIT_ENV.update({
    "GIT_AUTHOR_NAME": "openETQW test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "openETQW test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_TERMINAL_PROMPT": "0",
})
GIT_ENV.pop("OPENETQW_GAME_REPO", None)


def check(condition: bool, label: str, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if not condition:
        FAILURES.append(label + (f"\n    {detail.strip()}" if detail.strip() else ""))
        print(f"FAIL {label}")


def run_tool(script: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=GIT_ENV if env is None else env,
        check=False,
    )


def output_of(result: subprocess.CompletedProcess) -> str:
    return f"rc={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve string annotations through sys.modules
    spec.loader.exec_module(module)
    return module


def git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false",
         "-c", "core.autocrlf=false", *args],
        capture_output=True,
        text=True,
        env=GIT_ENV,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {repo}: {completed.stderr.strip()}")
    return completed.stdout.strip()


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-q")
    return path


def write(path: Path, text: str = "data\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def commit(repo: Path, files: dict[str, str], subject: str) -> str:
    for relative, text in files.items():
        write(repo / relative, text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", subject)
    return git(repo, "rev-parse", "HEAD")


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# openetqw_release_version.py
# ---------------------------------------------------------------------------

def release_version(repo: Path, *args: str) -> tuple[subprocess.CompletedProcess, dict[str, str]]:
    result = run_tool(VERSION_TOOL, "--source-root", str(repo), *args)
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key] = value
    return result, values


def expect_version(repo: Path, args: list[str], label: str, **expected: str) -> dict[str, str]:
    result, values = release_version(repo, *args)
    ok = result.returncode == 0 and all(values.get(key) == value for key, value in expected.items())
    check(ok, f"release version: {label}", output_of(result))
    return values


def test_release_version_first_part(repo: Path) -> None:
    commit(repo, {"meson.build": "project('openETQW', 'cpp', version: '0.1.0')\n"}, "Initial import")

    expect_version(repo, [], "no tag uses the meson.build floor",
                   version="0.1.0", version_tag="0.1.0", release_tag="v0.1.0",
                   release_name="openETQW 0.1.0", release_scale="floor",
                   latest_release_tag="", current_version="0.1.0", bump_mode="auto")

    git(repo, "tag", "v0.1.0")
    expect_version(repo, ["--bump-mode", "auto"], "auto with no new commits reuses the tag",
                   version="0.1.0", release_tag="v0.1.0", release_scale="current",
                   latest_release_tag="v0.1.0", latest_release_version="0.1.0")

    commit(repo, {"docs/notes.md": "notes\n", "STATUS.md": "status\n"}, "Document the release process")
    values = expect_version(repo, [], "docs-only change is a patch",
                            version="0.1.1", release_tag="v0.1.1", release_scale="patch",
                            analysis_score="0", commit_count="1", changed_files="2")
    check("Only documentation" in values.get("release_reason", ""), "release version: docs-only reason",
          values.get("release_reason", ""))

    for mode, version, scale, normalized in (
        ("major", "1.0.0", "major", "major"),
        ("major (x..)", "1.0.0", "major", "major"),
        ("minor", "0.2.0", "minor", "minor"),
        ("Minor (.x.)", "0.2.0", "minor", "minor"),
        ("patch", "0.1.1", "patch", "patch"),
        ("patch (..x)", "0.1.1", "patch", "patch"),
        ("serial", "0.1.1", "patch", "patch"),
        ("serial (..x)", "0.1.1", "patch", "patch"),
    ):
        expect_version(repo, ["--bump-mode", mode], f"bump mode {mode!r}",
                       version=version, release_tag=f"v{version}", release_scale=scale,
                       bump_mode=normalized, release_name=f"openETQW {version}")

    result, _ = release_version(repo, "--bump-mode", "sideways")
    check(result.returncode == 2 and "bump mode must be one of" in result.stderr,
          "release version: unknown bump mode is a usage error", output_of(result))

    big = "\n".join(f"line {index}" for index in range(500)) + "\n"
    commit(repo, {
        "src/framework/Common.cpp": big,
        "src/renderer/tr_main.cpp": big,
        "src/bse/BSE_Manager.cpp": big,
    }, "Rework the *frame* loop [renderer] (see `r_swap`)")
    expect_version(repo, [], "three major subsystems with large churn is a minor",
                   version="0.2.0", release_tag="v0.2.0", release_scale="minor",
                   commit_count="2", changed_files="5", added_lines="1502")

    result, _ = release_version(repo, "--version-override", "0.0.9")
    check(result.returncode != 0 and "must not be lower than the configured project version" in result.stderr,
          "release version: override below the meson.build floor is refused", output_of(result))
    result, _ = release_version(repo, "--version-override", "0.3")
    check(result.returncode != 0 and "numeric major.minor.patch" in result.stderr,
          "release version: malformed override is refused", output_of(result))
    expect_version(repo, ["--version-override", "0.3.0", "--bump-mode", "major"], "override wins",
                   version="0.3.0", release_tag="v0.3.0", release_scale="override",
                   latest_release_tag="v0.1.0", bump_mode="major")
    expect_version(repo, ["--current-version", "0.5.0"], "configured version ahead of the tag is used",
                   version="0.5.0", release_tag="v0.5.0", release_scale="floor", current_version="0.5.0")

    module = load_module("openetqw_release_version_under_test", VERSION_TOOL)
    for path, expected in (
        ("src/framework/Common.cpp", ("engine", 4, True)),
        ("src/renderer/vulkan/VkBackend.cpp", ("renderer", 4, True)),
        ("src/sys/win32/win_main.cpp", ("platform", 3, True)),
        ("src/libs/zlib/inflate.c", ("dependencies", 2, False)),
        ("src/decllib/DeclType.cpp", ("engine", 3, False)),
        ("base/vkprogs/interaction.frag", ("shaders", 3, False)),
        ("base/etqwconfig.cfg", ("content", 1, False)),
        ("tests/test_zero_sdk.py", ("tests", 1, False)),
        ("tools/ci/fetch_companion.py", ("build", 2, False)),
        (".github/workflows/release.yml", ("workflow", 2, False)),
        ("meson.build", ("build", 2, False)),
        ("meson_options.txt", ("build", 2, False)),
        ("docs/provenance.md", ("docs", 0, False)),
        ("docs/provenance-manifest.json", ("docs", 0, False)),
        ("CONTRIBUTING.md", ("docs", 0, False)),
        ("readme.md", ("docs", 0, False)),
        ("LICENSE", ("misc", 1, False)),
    ):
        actual = module.classify_path(path)
        check(actual == expected, f"release version: classify {path}", f"got {actual!r}, want {expected!r}")


def test_release_version_tags(repo: Path) -> None:
    for tag in ("v0.1.9", "v0.1.10", "v0.2", "v0.2.0-rc1", "nightly-0.9.0", "0.9.0"):
        git(repo, "tag", tag)
    expect_version(repo, [], "numeric tag ordering ignores non-release tags",
                   latest_release_tag="v0.1.10", latest_release_version="0.1.10",
                   version="0.1.10", release_tag="v0.1.10", release_scale="current")
    commit(repo, {"tools/ci/helper.py": "print('x')\n"}, "Add a CI helper")
    values = expect_version(repo, [], "small non-doc change is a patch",
                            version="0.1.11", release_tag="v0.1.11", release_scale="patch")
    check("build" in values.get("release_reason", ""), "release version: patch reason names the category",
          values.get("release_reason", ""))


# ---------------------------------------------------------------------------
# generate_release_changelog.py
# ---------------------------------------------------------------------------

def test_changelog(repo: Path, work: Path) -> None:
    head = git(repo, "rev-parse", "HEAD")
    output = work / "notes" / "changelog.md"
    common = ["--version", "0.2.0", "--version-tag", "0.2.0", "--release-tag", "v0.2.0",
              "--source-root", str(repo)]
    result = run_tool(CHANGELOG_TOOL, *common, "--release-scale", "minor",
                      "--release-reason", "Changed 5 files | engine",
                      "--run-id", "42", "--run-url", "https://github.com/jm2/openETQW/actions/runs/42",
                      "--output", str(output))
    check(result.returncode == 0 and output.is_file(), "changelog: generated", output_of(result))
    text = output.read_text(encoding="utf-8") if output.is_file() else ""
    for needle in (
        "## openETQW 0.2.0",
        "| Version | `0.2.0` |",
        "| Release tag | `v0.2.0` |",
        f"| Commit | [`{head[:8]}`](https://github.com/jm2/openETQW/commit/{head}) |",
        "| Release scale | `minor` |",
        "| Version decision | Changed 5 files \\| engine |",
        "| Workflow | [42](https://github.com/jm2/openETQW/actions/runs/42) |",
        "| Since | [`v0.1.0`](https://github.com/jm2/openETQW/releases/tag/v0.1.0) "
        f"([compare](https://github.com/jm2/openETQW/compare/v0.1.0...{head})) |",
        "### Highlights",
        "- Document the release process",
        "- Rework the \\*frame\\* loop \\[renderer\\] \\(see \\`r\\_swap\\`\\)",
        "### Change Log",
    ):
        check(needle in text, f"changelog: contains {needle[:60]!r}", text)
    check("Initial import" not in text, "changelog: commits before the previous tag are left out", text)
    check("openQ4" not in text, "changelog: no openQ4 names", text)

    for label, extra in (
        ("unsafe repo slug", ["--repo", "jm2/../evil"]),
        ("release tag without v", ["--release-tag", "0.2.0"]),
        ("non-https run URL", ["--run-url", "http://example.invalid/run"]),
    ):
        args = common + extra + ["--output", str(work / "notes" / "rejected.md")]
        result = run_tool(CHANGELOG_TOOL, *args)
        check(result.returncode != 0 and "error:" in result.stderr, f"changelog: rejects {label}", output_of(result))

    override = write(repo / "docs" / "releases" / "v0.2.0.md",
                     "\n# openETQW v0.2.0 Release Notes\n\nHand-written notes for this release.\n")
    output_override = work / "notes" / "changelog-override.md"
    result = run_tool(CHANGELOG_TOOL, *common, "--output", str(output_override))
    text = output_override.read_text(encoding="utf-8") if output_override.is_file() else ""
    check(result.returncode == 0 and "Hand-written notes for this release." in text
          and "### Change Log" not in text and "Release Notes" not in text,
          "changelog: tracked docs/releases override replaces the generated sections", output_of(result) + text)
    shutil.rmtree(override.parent)


# ---------------------------------------------------------------------------
# verify_release_asset_set.py
# ---------------------------------------------------------------------------

def add_tar_file(archive: tarfile.TarFile, name: str, data: bytes = b"payload\n") -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = 0o644
    archive.addfile(info, io.BytesIO(data))


def add_tar_link(archive: tarfile.TarFile, name: str, target: str, kind: bytes = tarfile.SYMTYPE) -> None:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = target
    archive.addfile(info)


def make_tar(path: Path, members: list[tuple[str, str, str]]) -> None:
    mode = "w:xz" if path.name.endswith(".tar.xz") else "w:gz"
    with tarfile.open(path, mode) as archive:
        for kind, name, target in members:
            if kind == "file":
                add_tar_file(archive, name)
            elif kind == "symlink":
                add_tar_link(archive, name, target)
            elif kind == "fifo":
                add_tar_link(archive, name, "", tarfile.FIFOTYPE)


def make_zip(path: Path, names: list[str]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in names:
            archive.writestr(zipfile.ZipInfo(name), b"payload\n")


def release_asset_names(tag: str) -> list[str]:
    return [
        f"openetqw-{tag}-windows-x64.zip",
        f"openetqw-{tag}-windows-arm64.zip",
        f"openetqw-{tag}-windows-x64-setup.exe",
        f"openetqw-{tag}-windows-arm64-setup.exe",
        f"openetqw-{tag}-linux-x64.tar.xz",
        f"openetqw-{tag}-linux-arm64.tar.xz",
        f"openetqw-{tag}-linux-x64-debug.tar.xz",
        f"openetqw-{tag}-linux-arm64-debug.tar.xz",
        f"openetqw-{tag}-x86_64.AppImage",
        f"openetqw-{tag}-aarch64.AppImage",
        f"openetqw-{tag}-macos-arm64-unsigned.tar.gz",
    ]


def build_asset(path: Path) -> None:
    root = path.name.split(".tar")[0]
    if path.name.endswith(".zip"):
        make_zip(path, ["openETQW-client_x64.exe", "base/vkprogs/interaction.spv", "licenses/openETQW/LICENSE"])
    elif path.name.endswith((".tar.xz", ".tar.gz")):
        make_tar(path, [
            ("file", f"{root}/openetqw-client", ""),
            ("file", f"{root}/lib/libSDL3.so.0.2.0", ""),
            ("symlink", f"{root}/lib/libSDL3.so.0", "libSDL3.so.0.2.0"),
        ])
    else:
        path.write_bytes(b"MZ-not-really-an-executable\n")


def verify_assets(artifact_dir: Path, expected: list[str], *mode: str) -> subprocess.CompletedProcess:
    args = ["--artifact-dir", str(artifact_dir), *mode]
    for name in expected:
        args += ["--expected", name]
    return run_tool(ASSET_TOOL, *args)


def expect_asset_rejected(work: Path, name: str, builder, needle: str, label: str) -> None:
    artifact_dir = private_dirs.make_private_dir("reject-", dir=work)
    builder(artifact_dir / name)
    result = verify_assets(artifact_dir, [name], "--manifest", str(artifact_dir.parent / f"{artifact_dir.name}.json"))
    check(result.returncode == 1 and needle in result.stderr, f"asset set: rejects {label}", output_of(result))


def test_asset_set(work: Path) -> None:
    artifact_dir = work / "assets"
    artifact_dir.mkdir()
    expected = release_asset_names("0.1.0")
    for name in expected:
        build_asset(artifact_dir / name)

    manifest = work / "asset-manifest.json"
    result = verify_assets(artifact_dir, expected, "--manifest", str(manifest))
    check(result.returncode == 0 and "11 files" in result.stdout, "asset set: accepts the exact release set",
          output_of(result))
    data = json.loads(manifest.read_text(encoding="utf-8")) if manifest.is_file() else {}
    assets = data.get("assets", [])
    check(data.get("format") == 1 and [item.get("name") for item in assets] == expected,
          "asset set: manifest lists every approved name in order", json.dumps(data, indent=1))
    check(all(item.get("size") == (artifact_dir / item["name"]).stat().st_size
              and item.get("sha256") == sha256_of(artifact_dir / item["name"]) for item in assets),
          "asset set: manifest records size and SHA-256", json.dumps(data, indent=1))

    result = verify_assets(artifact_dir, expected, "--manifest", str(manifest))
    check(result.returncode == 1 and "must not already exist" in result.stderr,
          "asset set: never overwrites a manifest", output_of(result))

    published = [{"name": item["name"], "size": item["size"], "digest": f"sha256:{item['sha256']}"}
                 for item in assets]
    published_json = write(work / "published.json", json.dumps(published))
    result = verify_assets(artifact_dir, expected, "--published-assets", str(published_json))
    check(result.returncode == 0 and "published size/SHA-256 verified" in result.stdout,
          "asset set: accepts matching published assets", output_of(result))
    if published:
        published[0]["digest"] = "sha256:" + "0" * 64
    write(published_json, json.dumps(published))
    result = verify_assets(artifact_dir, expected, "--published-assets", str(published_json))
    check(result.returncode == 1 and "mismatch" in result.stderr,
          "asset set: rejects a published digest mismatch", output_of(result))

    extra = write(artifact_dir / "openetqw-0.1.0-notes.txt")
    result = verify_assets(artifact_dir, expected, "--manifest", str(work / "m-extra.json"))
    check(result.returncode == 1 and "unexpected: 'openetqw-0.1.0-notes.txt'" in result.stderr,
          "asset set: rejects an unexpected file", output_of(result))
    extra.unlink()

    result = verify_assets(artifact_dir, expected + ["openetqw-0.1.0-macos-arm64.dmg"],
                           "--manifest", str(work / "m-missing.json"))
    check(result.returncode == 1 and "missing: 'openetqw-0.1.0-macos-arm64.dmg'" in result.stderr,
          "asset set: rejects a missing asset", output_of(result))

    for bad_name in ("openq4-0.1.0-linux-x64.tar.xz", "openetqw-0.1.0/../x.zip", "SHA256SUMS.txt"):
        result = verify_assets(artifact_dir, [bad_name], "--manifest", str(work / "m-bad.json"))
        check(result.returncode == 1 and "unsafe expected release artifact name" in result.stderr,
              f"asset set: rejects expected name {bad_name!r}", output_of(result))

    result = verify_assets(artifact_dir, expected + [expected[0]], "--manifest", str(work / "m-dup.json"))
    check(result.returncode == 1 and "duplicate" in result.stderr, "asset set: rejects duplicate names",
          output_of(result))

    rejects = work / "rejects"
    rejects.mkdir()
    expect_asset_rejected(rejects, "openetqw-0.1.0-linux-x64.tar.xz",
                          lambda p: make_tar(p, [("file", "ok/file", ""), ("file", "../evil.sh", "")]),
                          "unsafe member path", "a tar.xz member with ../")
    expect_asset_rejected(rejects, "openetqw-0.1.0-linux-x64.tar.xz",
                          lambda p: make_tar(p, [("file", "/etc/cron.d/evil", "")]),
                          "unsafe member path", "a tar.xz member with an absolute path")
    expect_asset_rejected(rejects, "openetqw-0.1.0-macos-arm64-unsigned.tar.gz",
                          lambda p: make_tar(p, [("symlink", "openETQW.app/escape", "../../etc")]),
                          "symlink leaving the archive", "a tar.gz symlink leaving the archive")
    expect_asset_rejected(rejects, "openetqw-0.1.0-linux-x64.tar.xz",
                          lambda p: make_tar(p, [("fifo", "pkg/pipe", "")]),
                          "special-file member", "a tar.xz fifo member")
    expect_asset_rejected(rejects, "openetqw-0.1.0-windows-x64.zip",
                          lambda p: make_zip(p, ["openETQW-client_x64.exe", "..\\..\\evil.dll"]),
                          "unsafe member path", "a zip member with ..\\")
    expect_asset_rejected(rejects, "openetqw-0.1.0-windows-arm64.zip",
                          lambda p: make_zip(p, ["C:/Windows/evil.dll"]),
                          "unsafe member path", "a zip member with a drive letter")
    expect_asset_rejected(rejects, "openetqw-0.1.0-x86_64.AppImage",
                          lambda p: p.write_bytes(b""), "is empty", "an empty AppImage")

    def truncated(path: Path) -> None:
        build_asset(path)
        path.write_bytes(path.read_bytes()[:-16])

    expect_asset_rejected(rejects, "openetqw-0.1.0-linux-arm64.tar.xz", truncated,
                          "truncated or corrupt", "a truncated tar.xz")
    expect_asset_rejected(rejects, "openetqw-0.1.0-linux-arm64-debug.tar.xz",
                          lambda p: p.write_bytes(lzma.compress(b"not a tar archive" * 64)),
                          "truncated or corrupt", "an xz stream that is not a tar")


# ---------------------------------------------------------------------------
# verify_release_source_provenance.py
# ---------------------------------------------------------------------------

def write_stage_manifest(stage: Path, engine_sha: str, game_sha: str, *, engine_dirty: bool = False) -> Path:
    staged = [stage / "src" / "game" / "Game.h", stage / "src" / "idlib" / "Lib.h"]
    manifest = {
        "format": 1,
        "projectGitCommit": engine_sha,
        "projectGitDirty": engine_dirty,
        "gameLibsGitCommit": game_sha,
        "gameLibsGitDirty": False,
        "fileCount": len(staged),
        "files": [{"path": p.relative_to(stage).as_posix(), "sha256": sha256_of(p)} for p in staged],
    }
    return write(stage / "openetqw_gamelibs_stage_manifest.json", json.dumps(manifest, indent=2))


def test_provenance(work: Path) -> None:
    base = work / "provenance"
    engine = init_repo(base / "openETQW")
    game = init_repo(base / "openETQW-game")
    engine_sha = commit(engine, {"meson.build": "project('openETQW')\n"}, "Engine")
    game_sha = commit(game, {"src/game/Game.h": "// game\n", "src/idlib/Lib.h": "// idlib\n"}, "Game")
    stage = base / "stage"
    write(stage / "src" / "game" / "Game.h", "// game\n")
    write(stage / "src" / "idlib" / "Lib.h", "// idlib\n")
    manifest = write_stage_manifest(stage, engine_sha, game_sha)

    def verify(expected_engine: str = engine_sha, expected_game: str = game_sha, *extra: str,
               env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        return run_tool(PROVENANCE_TOOL, "--engine-root", str(engine), "--stage-manifest", str(manifest),
                        "--expected-engine-commit", expected_engine, "--expected-game-commit", expected_game,
                        *extra, env=env)

    result = verify(engine_sha, game_sha, "--game-repo", str(game))
    check(result.returncode == 0 and f"openETQW={engine_sha} openETQW-game={game_sha}" in result.stdout,
          "provenance: accepts clean checkouts at the expected commits", output_of(result))
    result = verify(engine_sha.upper(), game_sha)
    check(result.returncode == 0, "provenance: companion defaults to ../openETQW-game (upper-case SHA ok)",
          output_of(result))
    env = dict(GIT_ENV, OPENETQW_GAME_REPO=str(base / "no-such-companion"))
    result = verify(env=env)
    check(result.returncode == 1 and "openETQW-game repository is unavailable" in result.stderr,
          "provenance: OPENETQW_GAME_REPO selects the companion", output_of(result))

    result = verify(game_sha, game_sha, "--game-repo", str(game))
    check(result.returncode == 1 and "openETQW checked-out commit" in result.stderr
          and "does not match expected" in result.stderr,
          "provenance: rejects an unexpected engine commit", output_of(result))
    result = verify(engine_sha, engine_sha, "--game-repo", str(game))
    check(result.returncode == 1 and "openETQW-game checked-out commit" in result.stderr,
          "provenance: rejects an unexpected companion commit", output_of(result))
    result = verify(engine_sha[:12], game_sha, "--game-repo", str(game))
    check(result.returncode == 1 and "full 40-character" in result.stderr,
          "provenance: rejects an abbreviated SHA", output_of(result))

    untracked = write(game / "stray.txt")
    result = verify(engine_sha, game_sha, "--game-repo", str(game))
    check(result.returncode == 1 and "openETQW-game checkout is dirty" in result.stderr,
          "provenance: rejects an untracked file in the companion", output_of(result))
    untracked.unlink()
    write(engine / "meson.build", "project('changed')\n")
    result = verify(engine_sha, game_sha, "--game-repo", str(game))
    check(result.returncode == 1 and "openETQW checkout is dirty" in result.stderr,
          "provenance: rejects a modified engine checkout", output_of(result))
    git(engine, "checkout", "--", "meson.build")

    write(stage / "src" / "game" / "Game.h", "// tampered\n")
    result = verify(engine_sha, game_sha, "--game-repo", str(game))
    check(result.returncode == 1 and "hash mismatch for src/game/Game.h" in result.stderr,
          "provenance: rejects a staged file that changed after staging", output_of(result))
    write(stage / "src" / "game" / "Game.h", "// game\n")

    write_stage_manifest(stage, engine_sha, game_sha, engine_dirty=True)
    result = verify(engine_sha, game_sha, "--game-repo", str(game))
    check(result.returncode == 1 and "does not record a clean openETQW checkout" in result.stderr,
          "provenance: rejects a stage manifest from a dirty engine", output_of(result))
    write_stage_manifest(stage, game_sha, game_sha)
    result = verify(engine_sha, game_sha, "--game-repo", str(game))
    check(result.returncode == 1 and "staged openETQW commit" in result.stderr,
          "provenance: rejects a stage manifest from another engine commit", output_of(result))
    write_stage_manifest(stage, engine_sha, game_sha)
    result = verify(engine_sha, game_sha, "--game-repo", str(game))
    check(result.returncode == 0, "provenance: accepts again after the fixes", output_of(result))


# ---------------------------------------------------------------------------
# build_windows_installer.py
# ---------------------------------------------------------------------------

def make_windows_package(path: Path, arch: str) -> Path:
    for relative in (
        f"openETQW-client_{arch}.exe",
        f"openETQW-ded_{arch}.exe",
        f"game_{arch}.dll",
        "SDL3.dll",
        "z.dll",
        "glslangValidator.exe",
        "README.txt",
        "licenses/openETQW/LICENSE",
        "licenses/ETQW-SDK/EULA.txt",
        "base/vkprogs/interaction.frag",
    ):
        write(path / relative)
    return path


def render_installer(package: Path, output: Path, arch: str, basename: str, *extra: str) -> subprocess.CompletedProcess:
    return run_tool(INSTALLER_TOOL, "--package-dir", str(package), "--version", "0.1.0", "--arch", arch,
                    "--output-dir", str(output), "--output-basename", basename, "--render-only", *extra)


def test_installer(work: Path) -> None:
    module = load_module("build_windows_installer_under_test", INSTALLER_TOOL)
    check(module.validate_version("0.1.0") == "0.1.0", "installer: importable, validates versions")
    quoted_package = Path('C:/release/openetqw "quoted"')
    rendered = module.render_installer_script(
        '#define PackageSource "@@PACKAGE_SOURCE@@"\n@@SETUP_ICON_DIRECTIVE@@\n',
        package_dir=quoted_package,
        output_dir=Path("C:/out"),
        output_basename="openetqw-0.1.0-windows-x64-setup",
        version="0.1.0",
        arch="x64",
        setup_icon_file=None,
    )
    # the path as this host spells it: backslashes on Windows
    escaped = str(quoted_package).replace('"', '""')
    check(f'#define PackageSource "{escaped}"' in rendered and "SetupIconFile" not in rendered,
          "installer: escapes quotes for Inno Setup and omits an absent icon", rendered)

    output = work / "installer-out"
    package = make_windows_package(work / "win-x64", "x64")
    basename = "openetqw-0.1.0-windows-x64-setup"
    result = render_installer(package, output, "x64", basename)
    iss_path = output / f"{basename}.iss"
    check(result.returncode == 0 and iss_path.is_file(), "installer: --render-only writes the .iss", output_of(result))
    text = iss_path.read_text(encoding="utf-8") if iss_path.is_file() else ""
    for needle in (
        '#define MyAppName "openETQW"',
        '#define MyAppVersion "0.1.0"',
        '#define MyAppPublisher "openETQW contributors"',
        '#define MyAppURL "https://github.com/jm2/openETQW"',
        '#define MyAppExeName "openETQW-client_x64.exe"',
        '#define MyAppServerExeName "openETQW-ded_x64.exe"',
        '#define MyAppInstallRegKey "Software\\\\openETQW"',
        f'#define PackageSource "{package.resolve()}"',
        "AppId={{D5E05AEC-F8E3-4F42-9250-CA4A7EDBF1DC}",
        f'LicenseFile="{package.resolve() / "licenses" / "openETQW" / "LICENSE"}"',
        f'OutputDir="{output.resolve()}"',
        f"OutputBaseFilename={basename}",
        "PrivilegesRequired=lowest",
        "ArchitecturesAllowed=x64compatible",
        "ArchitecturesInstallIn64BitMode=x64compatible",
        "VersionInfoVersion=0.1.0",
        'ValueName: "InstallArch"; ValueData: "x64"',
        "{localappdata}\\Programs\\openETQW",
        'Name: "{group}\\openETQW Dedicated Server"; Filename: "{app}\\{#MyAppServerExeName}"',
        'Name: "desktopicon"',
    ):
        check(needle in text, f"installer: x64 script contains {needle[:60]!r}", text)
    check("@@" not in text, "installer: every placeholder token is filled", text)
    # the uninstaller removes every value it wrote under Software\openETQW, so the
    # key goes too and a later setup does not report an install that is gone
    install_values = [line for line in text.splitlines() if line.startswith('Root: HKA; Subkey: "{#MyAppInstallRegKey}"')]
    check(len(install_values) == 5 and all("uninsdeletevalue" in line for line in install_values),
          "installer: the uninstaller deletes each Software\\openETQW value", "\n".join(install_values))
    directives = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(";")).lower()
    for forbidden in ("openq4", "SetupIconFile", "protocol", "docs\\index.html", "8FD95F4F", "AppMutex"):
        check(forbidden.lower() not in directives, f"installer: x64 script has no {forbidden!r}", text)

    icon = write(work / "icons" / "openetqw.ico", "ico\n")
    package_arm64 = make_windows_package(work / "win-arm64", "arm64")
    basename = "openetqw-0.1.0-windows-arm64-setup"
    result = render_installer(package_arm64, output, "arm64", basename, "--setup-icon", str(icon))
    iss_path = output / f"{basename}.iss"
    text = iss_path.read_text(encoding="utf-8") if iss_path.is_file() else ""
    for needle in (
        '#define MyAppExeName "openETQW-client_arm64.exe"',
        "ArchitecturesAllowed=arm64",
        "ArchitecturesInstallIn64BitMode=arm64",
        f'SetupIconFile="{icon.resolve()}"',
        f"OutputBaseFilename={basename}",
    ):
        check(result.returncode == 0 and needle in text, f"installer: arm64 script contains {needle[:60]!r}",
              output_of(result) + text)

    result = render_installer(package, output, "arm64", "openetqw-0.1.0-windows-arm64-setup")
    check(result.returncode == 1 and "openETQW-client_arm64.exe" in result.stderr,
          "installer: rejects a package built for another arch", output_of(result))
    (package / "z.dll").unlink()
    result = render_installer(package, output, "x64", "x")
    check(result.returncode == 1 and "z.dll" in result.stderr, "installer: rejects a package without z.dll",
          output_of(result))
    write(package / "z.dll")
    shutil.rmtree(package / "base" / "vkprogs")
    result = render_installer(package, output, "x64", "x")
    check(result.returncode == 1 and "vkprogs" in result.stderr, "installer: rejects a package without vkprogs",
          output_of(result))
    write(package / "base" / "vkprogs" / "interaction.frag")
    result = render_installer(package, output, "x64", "x", "--setup-icon", str(work / "icons" / "missing.ico"))
    check(result.returncode == 1 and "installer icon not found" in result.stderr,
          "installer: rejects a missing --setup-icon", output_of(result))
    result = render_installer(package, output, "x64", "../escape")
    check(result.returncode == 1 and "file-name-safe" in result.stderr,
          "installer: rejects an unsafe output basename", output_of(result))
    result = run_tool(INSTALLER_TOOL, "--package-dir", str(package), "--version", "0.1/bad", "--arch", "x64",
                      "--output-dir", str(output), "--output-basename", "x", "--render-only")
    check(result.returncode == 1 and "dotted numeric" in result.stderr, "installer: rejects a bad version",
          output_of(result))


# ---------------------------------------------------------------------------

def remove_tree(path: Path) -> None:
    def make_writable_and_retry(func, target, _exc) -> None:
        os.chmod(target, stat.S_IWRITE)
        func(target)

    try:
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=make_writable_and_retry)
        else:
            shutil.rmtree(path, onerror=make_writable_and_retry)
    except OSError as exc:
        print(f"warning: could not remove {path}: {exc}", file=sys.stderr)


def test_iscc_discovery(work: Path) -> None:
    # resolve_iscc_path reads the environment, so the whole lookup is pointed at
    # directories made here: Program Files, %LOCALAPPDATA% and PATH
    module = load_module("build_windows_installer_iscc", INSTALLER_TOOL)
    machine = work / "iscc" / "ProgramFiles"
    per_user = work / "iscc" / "LocalAppData"
    empty_path = work / "iscc" / "empty-path"
    for directory in (machine, per_user, empty_path):
        directory.mkdir(parents=True)
    names = ("INNO_SETUP_ISCC", "ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA", "PATH")
    saved = {name: os.environ.get(name) for name in names}
    try:
        os.environ.pop("INNO_SETUP_ISCC", None)
        os.environ["ProgramFiles(x86)"] = str(machine)
        os.environ["ProgramFiles"] = str(machine)
        os.environ["LOCALAPPDATA"] = str(per_user)
        os.environ["PATH"] = str(empty_path)
        def resolve() -> Path | str:
            try:
                return module.resolve_iscc_path(None)
            except FileNotFoundError as exc:
                return str(exc)

        user_iscc = write(per_user / "Programs" / "Inno Setup 6" / "ISCC.exe", "iscc\n")
        found = resolve()
        check(found == user_iscc.resolve(), "installer: finds Inno Setup installed per user (%LOCALAPPDATA%\\Programs)",
              str(found))
        machine_iscc = write(machine / "Inno Setup 6" / "ISCC.exe", "iscc\n")
        found = resolve()
        check(found == machine_iscc.resolve(), "installer: a machine-wide Inno Setup comes before a per-user one",
              str(found))
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def main() -> int:
    if shutil.which("git") is None:
        print("SKIP: git is not on PATH")
        return 77
    work = private_dirs.make_private_dir("openetqw-release-tools-")
    try:
        repo = init_repo(work / "engine")
        test_release_version_first_part(repo)
        test_changelog(repo, work)
        test_release_version_tags(repo)
        test_asset_set(work)
        test_provenance(work)
        test_installer(work)
        test_iscc_discovery(work)
    finally:
        remove_tree(work)

    if FAILURES:
        print(f"\n{len(FAILURES)} of {CHECKS} checks failed:")
        for failure in FAILURES:
            print(f"- {failure}")
        return 1
    print(f"release tools: all {CHECKS} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
