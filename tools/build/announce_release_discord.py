#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Announce a published openETQW release on Discord.

The role of openQ4's .github/scripts/announce-release-discord.mjs (jm2/openQ4
b37b1456), in Python so the release tooling needs one runtime: reads the
release (gh release view) and posts one embed to the webhook in
$DISCORD_WEBHOOK_URL. Without a webhook it prints a notice and exits 0, so the
announcement stays optional until the owner sets DISCORD_RELEASE_WEBHOOK.
A draft is never announced (it has no tag or permanent download URLs).

Environment:
  DISCORD_WEBHOOK_URL   webhook URL (secret); empty skips the announcement
  RELEASE_TAG           the tag, else the release in $GITHUB_EVENT_PATH
  DISCORD_RELEASE_MENTIONS, DISCORD_RELEASE_AVATAR_URL   optional
Usage: announce_release_discord.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request

MAX_DESCRIPTION = 3500


def release_tag() -> str:
    tag = os.environ.get("RELEASE_TAG", "").strip()
    if tag:
        return tag
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    if event_path and os.path.isfile(event_path):
        with open(event_path, encoding="utf-8") as handle:
            return json.load(handle).get("release", {}).get("tag_name", "")
    return ""


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="print the payload instead of posting it")
    args = parser.parse_args(argv)
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook and not args.dry_run:
        print("::notice title=Discord announcement skipped::DISCORD_RELEASE_WEBHOOK is not set")
        return 0
    tag = release_tag()
    if not tag:
        print("announce_release_discord: no release tag (RELEASE_TAG or a release event)", file=sys.stderr)
        return 2
    view = subprocess.run(["gh", "release", "view", tag, "--json", "name,url,body,isDraft,isPrerelease,assets"],
                          capture_output=True, text=True)
    if view.returncode != 0:
        print(f"announce_release_discord: gh release view {tag} failed: {view.stderr.strip()}", file=sys.stderr)
        return 1
    release = json.loads(view.stdout)
    if release.get("isDraft"):
        print(f"announce_release_discord: {tag} is a draft; not announced")
        return 0
    body = (release.get("body") or "").strip()
    if len(body) > MAX_DESCRIPTION:
        body = body[:MAX_DESCRIPTION].rsplit("\n", 1)[0] + "\n..."
    assets = [a["name"] for a in release.get("assets", [])]
    payload = {
        "username": "openETQW Releases",
        "content": os.environ.get("DISCORD_RELEASE_MENTIONS", "").strip() or None,
        "embeds": [{
            "title": release.get("name") or f"openETQW {tag}",
            "url": release.get("url"),
            "description": body or "A new openETQW release is out.",
            "color": 0xD98C1F,
            "fields": [{"name": "Downloads", "value": "\n".join(assets)[:1000] or "see the release page"}],
            "footer": {"text": "pre-release" if release.get("isPrerelease") else "release"},
        }],
        "allowed_mentions": {"parse": ["roles", "users"]},
    }
    avatar = os.environ.get("DISCORD_RELEASE_AVATAR_URL", "").strip()
    if avatar:
        payload["avatar_url"] = avatar
    if args.dry_run:
        print(json.dumps(payload, indent=2))
        return 0
    request = urllib.request.Request(webhook, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "User-Agent": "openETQW-release"})
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status not in (200, 204):
            print(f"announce_release_discord: webhook returned {response.status}", file=sys.stderr)
            return 1
    print(f"announce_release_discord: announced {tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
