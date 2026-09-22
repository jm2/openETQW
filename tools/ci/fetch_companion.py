#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fetch the openETQW-game companion repository at a pinned commit for CI.

The engine build stages headers from, and links idLib out of, a sibling
checkout of jm2/openETQW-game (default ../openETQW-game, overridable with
OPENETQW_GAME_REPO). While both repositories are private, hosted CI cannot
read the companion with the default GITHUB_TOKEN, and the project has chosen
not to configure a cross-repository token. This script therefore:

1. fetches anonymously over https first. This starts working by itself, with
   no workflow edits, on the day the companion repository is made public;
2. only when that is refused for lack of credentials, and only when the
   optional OPENETQW_CROSS_REPO_TOKEN environment variable is non-empty,
   retries once more with that token sent as an http.extraHeader. The header is
   passed through GIT_CONFIG_* environment variables (the way actions/checkout
   does it), never on a command line, never written to disk, never echoed;
3. checks out the commit pinned in tools/ci/companion.ref (or an explicit
   --ref / OPENETQW_COMPANION_REF override such as a branch name) detached and
   verifies that HEAD is exactly that commit.

Every git invocation runs with credential helpers, askpass programs, terminal
prompts and inherited extra headers disabled, so the anonymous attempt is
truly anonymous on developer machines too (no keychain) and can never hang
waiting for a password.

Exit codes
  0   companion fetched and verified (or already present at the pinned commit)
  78  companion not accessible: the host answered "authentication required"
      (private repository, or no such repository) and no token is configured.
      A GitHub '::notice::' and a job-summary entry are emitted. With
      --skip-if-unavailable the exit code is 0 instead, and available=false is
      written to $GITHUB_OUTPUT so the rest of the job can skip cleanly.
  1   real error: unknown pin or ref, network failure after retries, checkout
      verification mismatch, a configured token that was rejected, or an
      unsafe destination directory
  2   usage error
"""

from __future__ import annotations

import argparse
import base64
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPO = "jm2/openETQW-game"
DEFAULT_PIN_FILE = ROOT / "tools" / "ci" / "companion.ref"
DEFAULT_TOKEN_ENV = "OPENETQW_CROSS_REPO_TOKEN"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_UNAVAILABLE = 78

FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
PARTIAL_SHA_RE = re.compile(r"^[0-9a-f]{7,39}$")
GIT_MIN_VERSION = (2, 31)  # GIT_CONFIG_COUNT / GIT_CONFIG_KEY_n support
FETCH_TIMEOUT_SECONDS = 600

# Substrings (lower-cased) of git's stderr. GitHub answers 401 for both private
# and nonexistent repositories when no credentials are sent, so "auth" covers
# both "private" and "not found".
AUTH_PATTERNS = (
    "could not read username",
    "could not read password",
    "terminal prompts disabled",
    "authentication failed",
    "authentication required",
    "invalid username or password",
    "repository not found",
    "the requested url returned error: 401",
    "the requested url returned error: 403",
    "the requested url returned error: 404",
    "http 401",
    "http 403",
    "http 404",
    "permission denied",
    "access denied",
    "write access to repository not granted",
)
BADREF_PATTERNS = (
    "couldn't find remote ref",
    "could not find remote ref",
    "not our ref",
    "no such remote ref",
    "unadvertised object",
    "invalid refspec",
    "is not a valid ref name",
    "bad object",
)
NETWORK_PATTERNS = (
    "could not resolve host",
    "temporary failure in name resolution",
    "failed to connect",
    "couldn't connect to server",
    "connection timed out",
    "operation timed out",
    "timed out",
    "connection reset",
    "connection refused",
    "early eof",
    "rpc failed",
    "remote end hung up unexpectedly",
    "unexpected disconnect",
    "the requested url returned error: 5",
    "http 5",
    "ssl",
    "tls",
    "gnutls",
    "http/2 stream",
    "transfer closed",
    "network is unreachable",
)


class CompanionError(Exception):
    """A real failure; exit code EXIT_ERROR."""


class CompanionUnavailable(Exception):
    """The companion cannot be read with the credentials available; exit 78."""


class UsageError(Exception):
    """Bad invocation; exit code EXIT_USAGE."""


@dataclass
class GitResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclass
class FetchAttempt:
    ok: bool
    kind: str  # "ok", "auth", "badref", "network", "other"
    detail: str
    attempts: int


# ---------------------------------------------------------------------------
# GitHub Actions plumbing
# ---------------------------------------------------------------------------

def in_github_actions() -> bool:
    return os.environ.get("GITHUB_ACTIONS", "").lower() == "true"


def _escape_data(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_property(value: str) -> str:
    return _escape_data(value).replace(":", "%3A").replace(",", "%2C")


def gha_command(command: str, message: str, title: str = "") -> None:
    """Print a workflow command (::notice:: etc.); plain text off Actions."""
    if in_github_actions():
        props = f" title={_escape_property(title)}" if title else ""
        print(f"::{command}{props}::{_escape_data(message)}", flush=True)
    else:
        prefix = f"{command.upper()}: {title}: " if title else f"{command.upper()}: "
        print(prefix + message, file=sys.stderr, flush=True)


def gha_mask(value: str) -> None:
    if in_github_actions() and value:
        print(f"::add-mask::{value}", flush=True)


def append_file(env_name: str, text: str) -> None:
    target = os.environ.get(env_name, "").strip()
    if not target:
        return
    with open(target, "a", encoding="utf-8") as handle:
        handle.write(text)


def write_outputs(values: dict[str, str]) -> None:
    lines = "".join(f"{key}={value}\n" for key, value in values.items())
    append_file("GITHUB_OUTPUT", lines)


def write_summary(markdown: str) -> None:
    append_file("GITHUB_STEP_SUMMARY", markdown.rstrip("\n") + "\n\n")


# ---------------------------------------------------------------------------
# git helpers
# ---------------------------------------------------------------------------

class Redactor:
    def __init__(self) -> None:
        self._secrets: list[str] = []

    def add(self, secret: str) -> None:
        if secret and secret not in self._secrets:
            self._secrets.append(secret)

    def __call__(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return text


REDACT = Redactor()


def isolated_git_env(url_prefix: str, extra: Optional[list[tuple[str, str]]] = None) -> dict[str, str]:
    """Environment for a network git call that cannot pick up ambient creds."""
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")) or key in (
            "GIT_CONFIG_COUNT",
            "GIT_CONFIG_PARAMETERS",
        ):
            del env[key]
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = ""
    env["SSH_ASKPASS"] = ""
    env["GCM_INTERACTIVE"] = "never"
    env["GIT_LFS_SKIP_SMUDGE"] = "1"
    config: list[tuple[str, str]] = [
        # An empty value resets a multi-valued key inherited from system,
        # global or repository configuration.
        ("credential.helper", ""),
        ("core.askPass", ""),
        ("http.extraHeader", ""),
        (f"http.{url_prefix}.extraHeader", ""),
        ("init.defaultBranch", "companion"),
        ("advice.detachedHead", "false"),
        ("protocol.version", "2"),
    ]
    config.extend(extra or [])
    env["GIT_CONFIG_COUNT"] = str(len(config))
    for index, (key, value) in enumerate(config):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value
    return env


def run_git(args: list[str], env: dict[str, str], timeout: float = 120.0) -> GitResult:
    try:
        completed = subprocess.run(
            ["git", *args],
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise CompanionError("git executable not found on PATH") from exc
    except subprocess.TimeoutExpired:
        return GitResult(124, "", f"git {' '.join(args[:3])} timed out after {timeout:.0f}s (operation timed out)")
    return GitResult(completed.returncode, REDACT(completed.stdout), REDACT(completed.stderr))


def require_git_version() -> str:
    result = run_git(["--version"], os.environ.copy())
    match = re.search(r"(\d+)\.(\d+)", result.stdout)
    if not result.ok or not match:
        raise CompanionError(f"cannot determine git version: {result.stderr.strip() or result.stdout.strip()}")
    version = (int(match.group(1)), int(match.group(2)))
    if version < GIT_MIN_VERSION:
        raise CompanionError(
            f"git {version[0]}.{version[1]} is too old; GIT_CONFIG_COUNT needs git "
            f">= {GIT_MIN_VERSION[0]}.{GIT_MIN_VERSION[1]}"
        )
    return result.stdout.strip()


def classify(stderr: str) -> str:
    text = stderr.lower()
    if any(pattern in text for pattern in AUTH_PATTERNS):
        return "auth"
    if any(pattern in text for pattern in BADREF_PATTERNS):
        return "badref"
    if any(pattern in text for pattern in NETWORK_PATTERNS):
        return "network"
    return "other"


def last_lines(text: str, count: int = 12) -> str:
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return "\n".join(lines[-count:])


# ---------------------------------------------------------------------------
# pin / destination handling
# ---------------------------------------------------------------------------

def read_pin(pin_file: Path) -> str:
    if not pin_file.is_file():
        raise UsageError(f"pin file not found: {pin_file}")
    values = [
        line.strip()
        for line in pin_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if len(values) != 1:
        raise UsageError(f"{pin_file} must contain exactly one commit SHA (found {len(values)} entries)")
    pin = values[0].lower()
    if not FULL_SHA_RE.match(pin):
        raise UsageError(f"{pin_file} must hold a full 40-character commit SHA, found {values[0]!r}")
    return pin


def resolve_ref(explicit: Optional[str], pin_file: Path) -> tuple[str, str]:
    """Return (ref, origin) where origin is 'pin', 'override' or 'argument'."""
    if explicit:
        ref, origin = explicit.strip(), "argument"
    else:
        env_ref = os.environ.get("OPENETQW_COMPANION_REF", "").strip()
        if env_ref:
            ref, origin = env_ref, "override"
        else:
            return read_pin(pin_file), "pin"
    if not ref or ref.startswith("-") or any(ch.isspace() for ch in ref) or ".." in ref:
        raise UsageError(f"invalid companion ref: {ref!r}")
    if PARTIAL_SHA_RE.match(ref.lower()) and not FULL_SHA_RE.match(ref.lower()):
        # A 7..39 hex string is almost certainly an abbreviated SHA, which a
        # shallow fetch cannot resolve. Branch names like 'deadbeef' lose.
        raise UsageError(f"abbreviated commit SHAs cannot be fetched; pass the full 40-character SHA: {ref!r}")
    if FULL_SHA_RE.match(ref.lower()):
        ref = ref.lower()
    return ref, origin


def default_destination() -> Path:
    raw = os.environ.get("OPENETQW_GAME_REPO", "").strip()
    return Path(raw).expanduser() if raw else ROOT.parent / "openETQW-game"


def local_head(dest: Path) -> tuple[str, bool]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    head = run_git(["-C", str(dest), "rev-parse", "--verify", "HEAD^{commit}"], env)
    if not head.ok:
        return "", False
    dirty = run_git(["-C", str(dest), "status", "--porcelain", "--untracked-files=no"], env)
    return head.stdout.strip(), bool(dirty.stdout.strip()) or not dirty.ok


def prepare_destination(dest: Path, expected_sha: Optional[str], replace: bool) -> str:
    """Return 'fresh' (safe to create) or 'reuse' (already at expected_sha)."""
    if dest.is_symlink():
        raise CompanionError(f"refusing to use a symlinked destination: {dest}")
    if not dest.exists():
        return "fresh"
    if not dest.is_dir():
        raise CompanionError(f"destination exists and is not a directory: {dest}")
    if not any(dest.iterdir()):
        return "fresh"
    if expected_sha and (dest / ".git").exists():
        head, dirty = local_head(dest)
        if head == expected_sha and not dirty:
            return "reuse"
    if replace:
        shutil.rmtree(dest)
        return "fresh"
    raise CompanionError(
        f"destination {dest} already exists and is not a clean checkout of "
        f"{expected_sha or 'the requested ref'}; refusing to overwrite it (pass --replace, "
        "or point --dest / OPENETQW_GAME_REPO somewhere else)"
    )


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------

def attempt_fetch(dest: Path, ref: str, env: dict[str, str], attempts: int, delay: float) -> FetchAttempt:
    detail = ""
    for attempt in range(1, attempts + 1):
        result = run_git(
            ["-C", str(dest), "fetch", "--no-tags", "--depth", "1", "origin", ref],
            env,
            timeout=FETCH_TIMEOUT_SECONDS,
        )
        if result.ok:
            return FetchAttempt(True, "ok", "", attempt)
        detail = last_lines(result.stderr or result.stdout)
        kind = classify(result.stderr + "\n" + result.stdout)
        if kind != "network" or attempt == attempts:
            return FetchAttempt(False, kind, detail, attempt)
        wait = delay * attempt
        print(f"fetch_companion: network error on attempt {attempt}/{attempts}; retrying in {wait:.0f}s", flush=True)
        print("  " + detail.replace("\n", "\n  "), flush=True)
        time.sleep(wait)
    return FetchAttempt(False, "network", detail, attempts)


def auth_header_config(url_prefix: str, token: str) -> list[tuple[str, str]]:
    basic = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
    REDACT.add(token)
    REDACT.add(basic)
    gha_mask(basic)
    return [(f"http.{url_prefix}.extraHeader", f"AUTHORIZATION: basic {basic}")]


def fetch_companion(args: argparse.Namespace) -> dict[str, str]:
    ref, ref_origin = resolve_ref(args.ref, args.pin_file)
    is_sha = bool(FULL_SHA_RE.match(ref))
    if args.url:
        url = args.url
    else:
        server = args.server_url.rstrip("/")
        if urlsplit(server).scheme != "https":
            raise UsageError(f"--server-url must be an https URL, got {args.server_url!r}")
        url = f"{server}/{args.repo}.git"
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http", "file") or (parts.scheme != "file" and not parts.netloc):
        raise UsageError(f"unsupported companion URL {url!r}; use https:// (or file:// for local tests)")
    # Scope for inherited/injected http.<url>.extraHeader values.
    url_prefix = f"{parts.scheme}://{parts.netloc}/" if parts.netloc else "https://github.com/"
    dest = args.dest.resolve()

    print(f"fetch_companion: {args.repo} @ {ref} ({ref_origin}) -> {dest}", flush=True)
    print(f"fetch_companion: {require_git_version()}", flush=True)

    state = prepare_destination(dest, ref if is_sha else None, args.replace)
    if state == "reuse":
        print(f"fetch_companion: {dest} is already a clean checkout of {ref}; nothing to fetch", flush=True)
        return describe_checkout(dest, args.repo, ref, ref_origin, "existing checkout")

    created = False
    try:
        base_env = isolated_git_env(url_prefix)
        dest.parent.mkdir(parents=True, exist_ok=True)
        created = not dest.exists()
        for git_args in (["init", "-q", str(dest)], ["-C", str(dest), "remote", "add", "origin", url]):
            result = run_git(git_args, base_env)
            if not result.ok:
                raise CompanionError(f"git {git_args[0]} failed: {last_lines(result.stderr)}")

        source = "anonymous"
        outcome = attempt_fetch(dest, ref, base_env, args.attempts, args.retry_delay)
        if not outcome.ok and outcome.kind == "auth":
            token = os.environ.get(args.token_env, "").strip()
            if not token:
                raise CompanionUnavailable(
                    f"{args.repo} is not readable anonymously (private, or not found) and "
                    f"{args.token_env} is not set"
                )
            print(f"fetch_companion: anonymous fetch refused; retrying with {args.token_env}", flush=True)
            source = "token"
            token_env = isolated_git_env(url_prefix, auth_header_config(url_prefix, token))
            outcome = attempt_fetch(dest, ref, token_env, args.attempts, args.retry_delay)
            if not outcome.ok and outcome.kind == "auth":
                raise CompanionError(
                    f"{args.token_env} is set but was rejected for {args.repo}; fix or remove the secret\n"
                    + outcome.detail
                )
        if not outcome.ok:
            if outcome.kind == "badref":
                hint = (
                    "the pinned commit is not reachable in the companion repository; "
                    "update tools/ci/companion.ref with tools/ci/bump_companion.py"
                    if ref_origin == "pin"
                    else "the requested companion ref does not exist"
                )
                raise CompanionError(f"cannot fetch {args.repo} {ref}: {hint}\n{outcome.detail}")
            if outcome.kind == "network":
                raise CompanionError(
                    f"network failure fetching {args.repo} after {outcome.attempts} attempt(s)\n{outcome.detail}"
                )
            raise CompanionError(f"git fetch of {args.repo} {ref} failed\n{outcome.detail}")

        checkout = run_git(["-C", str(dest), "checkout", "-q", "--detach", "FETCH_HEAD"], base_env)
        if not checkout.ok:
            raise CompanionError(f"git checkout of FETCH_HEAD failed: {last_lines(checkout.stderr)}")
        fetched = run_git(["-C", str(dest), "rev-parse", "--verify", "FETCH_HEAD^{commit}"], base_env).stdout.strip()
        expected = ref if is_sha else fetched
        head = run_git(["-C", str(dest), "rev-parse", "--verify", "HEAD^{commit}"], base_env).stdout.strip()
        if not FULL_SHA_RE.match(head) or head != expected:
            raise CompanionError(f"checkout verification failed: HEAD is {head or '<none>'}, expected {expected}")
        for required in ("meson.build", "src/game", "src/idlib"):
            if not (dest / required).exists():
                raise CompanionError(f"fetched tree does not look like openETQW-game: {required} is missing")
        return describe_checkout(dest, args.repo, ref, ref_origin, source)
    except BaseException:
        if created and dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        raise


def describe_checkout(dest: Path, repo: str, ref: str, ref_origin: str, source: str) -> dict[str, str]:
    env = os.environ.copy()
    info = run_git(["-C", str(dest), "log", "-1", "--format=%H%x09%cI%x09%s"], env).stdout.strip()
    sha, _, rest = info.partition("\t")
    date, _, subject = rest.partition("\t")
    print(f"fetch_companion: OK {repo} {sha} ({date}) {subject}", flush=True)
    override_note = "" if ref_origin == "pin" else f" (override `{ref}`, not the pinned commit)"
    write_summary(
        "### Companion repository\n"
        f"- `{repo}` at `{sha}`{override_note}\n"
        f"- fetched via: {source}\n"
        f"- commit: {date} {subject}\n"
        f"- path: `{dest}`"
    )
    return {
        "available": "true",
        "sha": sha,
        "ref": ref,
        "ref-source": ref_origin,
        "source": source,
        "path": dest.as_posix(),
    }


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Exit codes: 0 ok, 78 companion not accessible, 1 error, 2 usage.",
    )
    parser.add_argument(
        "--repo",
        default=os.environ.get("OPENETQW_COMPANION_REPO", "").strip() or DEFAULT_REPO,
        help=f"companion repository as OWNER/NAME (default {DEFAULT_REPO}, env OPENETQW_COMPANION_REPO)",
    )
    parser.add_argument(
        "--server-url",
        default=os.environ.get("GITHUB_SERVER_URL", "").strip() or "https://github.com",
        help="git host base URL (default $GITHUB_SERVER_URL or https://github.com)",
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("OPENETQW_COMPANION_URL", "").strip() or None,
        help="full clone URL overriding --server-url/--repo (mirrors, file:// for tests; env OPENETQW_COMPANION_URL)",
    )
    parser.add_argument(
        "--ref",
        default=None,
        help="commit SHA, branch or tag to fetch instead of the pin (env OPENETQW_COMPANION_REF)",
    )
    parser.add_argument("--pin-file", type=Path, default=DEFAULT_PIN_FILE, help="file holding the pinned commit SHA")
    parser.add_argument(
        "--dest",
        type=Path,
        default=None,
        help="checkout directory (default $OPENETQW_GAME_REPO or <engine>/../openETQW-game)",
    )
    parser.add_argument(
        "--skip-if-unavailable",
        action="store_true",
        help="exit 0 (with available=false in $GITHUB_OUTPUT) instead of 78 when not accessible",
    )
    parser.add_argument("--replace", action="store_true", help="replace a non-matching existing destination")
    parser.add_argument("--attempts", type=int, default=3, help="network attempts per credential mode (default 3)")
    parser.add_argument("--retry-delay", type=float, default=10.0, help="base seconds between network retries")
    parser.add_argument(
        "--token-env",
        default=DEFAULT_TOKEN_ENV,
        help=f"environment variable holding the optional fallback token (default {DEFAULT_TOKEN_ENV})",
    )
    args = parser.parse_args(argv)
    if args.attempts < 1:
        parser.error("--attempts must be >= 1")
    if not re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", args.repo):
        parser.error(f"--repo must look like OWNER/NAME, got {args.repo!r}")
    if args.dest is None:
        args.dest = default_destination()
    return args


def main(argv: list[str]) -> int:
    try:
        args = parse_args(argv)
    except SystemExit as exc:  # argparse
        return EXIT_USAGE if exc.code not in (0, None) else EXIT_OK
    try:
        outputs = fetch_companion(args)
    except CompanionUnavailable as exc:
        message = f"companion repo not accessible - skipped until repos are public ({exc})"
        gha_command("notice", message, title="Companion repo not accessible")
        write_summary(
            "### SKIPPED: companion repo not accessible\n"
            f"`{args.repo}` could not be fetched anonymously (it is private, or requires authentication) "
            f"and `{args.token_env}` is not set, so every step of this job that needs the companion "
            "checkout was skipped. This is expected while the repositories are private; it starts "
            "working automatically, with no workflow changes, once the companion is publicly readable."
        )
        write_outputs({"available": "false", "sha": "", "ref": "", "ref-source": "", "source": "", "path": ""})
        print(f"fetch_companion: {message}", flush=True)
        return EXIT_OK if args.skip_if_unavailable else EXIT_UNAVAILABLE
    except UsageError as exc:
        gha_command("error", str(exc), title="fetch_companion usage")
        return EXIT_USAGE
    except CompanionError as exc:
        text = REDACT(str(exc))
        gha_command("error", text.splitlines()[0], title="Companion fetch failed")
        print(f"fetch_companion: ERROR: {text}", file=sys.stderr, flush=True)
        write_outputs({"available": "false"})
        return EXIT_ERROR
    write_outputs(outputs)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
