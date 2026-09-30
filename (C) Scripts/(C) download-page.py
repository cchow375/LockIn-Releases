#!/usr/bin/env python3
"""Render download documentation from the currently published combined release.

Writes only the requested Markdown file. It never edits releases, assets, or feeds.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parent.parent
REQUIRED_SUPPORT = {"appcast.xml", "windows-update.json", "windows-update.json.sig", "release.json", "SHA256SUMS.txt"}


def validate_repo(repo):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("Repository must be an owner/name pair.")


def render(release, repo, template):
    validate_repo(repo)
    if release.get("draft") is not False or release.get("prerelease") is not False:
        raise ValueError("Download links require a published non-prerelease release.")
    tag = release.get("tag_name", "")
    match = re.fullmatch(r"v([0-9]+\.[0-9]+\.[0-9]+)-([1-9][0-9]{0,13})", tag)
    if not match:
        raise ValueError("Unexpected LockIn version/build tag.")
    version, build = match.groups()
    base = f"https://github.com/{repo}/releases/download/{tag}/"
    names = {"MAC_URL": f"LockIn-{version}-{build}.zip", "WINDOWS_URL": f"LockIn-{version}-{build}-Windows-x64.zip"}
    assets = release.get("assets", [])
    if not isinstance(assets, list):
        raise ValueError("Invalid release asset metadata.")
    by_name = {asset.get("name"): asset for asset in assets if isinstance(asset, dict)}
    if len(by_name) != len(assets):
        raise ValueError("Duplicate or invalid release assets.")
    for name in REQUIRED_SUPPORT | set(names.values()):
        asset = by_name.get(name)
        if not asset or asset.get("state") != "uploaded" or not isinstance(asset.get("size"), int) or asset["size"] <= 0:
            raise ValueError(f"Published combined release is missing a complete {name}.")
        if asset.get("browser_download_url") != base + name:
            raise ValueError(f"Unexpected download origin for {name}.")
    values = {key: base + name for key, name in names.items()}
    values.update(VERSION=version, BUILD=build, RELEASE_URL=f"https://github.com/{repo}/releases/tag/{tag}")
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", value)
    if "{{" in template or "}}" in template:
        raise ValueError("Unresolved download page template value.")
    return template


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="cchow375/LockIn-Releases")
    parser.add_argument("--release-json", type=Path, help="Read a saved REST release response instead of GitHub.")
    parser.add_argument("--template", type=Path, default=ROOT / "(C) Download Page Template.md")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    validate_repo(args.repo)
    if args.release_json:
        release = json.loads(args.release_json.read_text())
    else:
        result = subprocess.run(["gh", "api", f"repos/{args.repo}/releases/latest"], check=True, capture_output=True, text=True)
        release = json.loads(result.stdout)
    page = render(release, args.repo, args.template.read_text())
    args.output.write_text(page)
    print(f"Wrote direct Mac/Windows links for {release['tag_name']} to {args.output}.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"Download page unchanged: {error}") from error
