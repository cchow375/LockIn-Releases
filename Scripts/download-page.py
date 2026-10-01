#!/usr/bin/env python3
"""Render each platform's verified download from the currently published release.

Writes only the requested Markdown file. It never edits releases, assets, or feeds.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
REQUIRED_SUPPORT = {"appcast.xml", "windows-update.json", "windows-update.json.sig", "release.json", "SHA256SUMS.txt"}


def validate_repo(repo):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("Repository must be an owner/name pair.")


def release_identity(release, repo, expected_tag=None):
    validate_repo(repo)
    if not isinstance(release, dict):
        raise ValueError("Invalid release metadata.")
    if release.get("draft") is not False or release.get("prerelease") is not False:
        raise ValueError("Download links require a published non-prerelease release.")
    tag = release.get("tag_name", "")
    match = re.fullmatch(r"v([0-9]+\.[0-9]+\.[0-9]+)-([1-9][0-9]{0,13})", tag)
    if not match:
        raise ValueError("Unexpected LockIn version/build tag.")
    if expected_tag is not None and tag != expected_tag:
        raise ValueError("Windows download release differs from its carried provenance.")
    version, build = match.groups()
    assets = release.get("assets", [])
    if not isinstance(assets, list):
        raise ValueError("Invalid release asset metadata.")
    by_name = {asset.get("name"): asset for asset in assets if isinstance(asset, dict)}
    if len(by_name) != len(assets):
        raise ValueError("Duplicate or invalid release assets.")
    return tag, version, build, by_name


def checked_asset(assets, name, repo, tag):
    asset = assets.get(name)
    if not asset or asset.get("state") != "uploaded" or not isinstance(asset.get("size"), int) or isinstance(asset["size"], bool) or asset["size"] <= 0:
        raise ValueError(f"Published release is missing a complete {name}.")
    if asset.get("browser_download_url") != f"https://github.com/{repo}/releases/download/{tag}/{name}":
        raise ValueError(f"Unexpected download origin for {name}.")
    return asset


def render(release, repo, template, manifest=None, windows_release=None):
    tag, version, build, assets = release_identity(release, repo)
    mac_name = f"LockIn-{version}-{build}.zip"
    for name in REQUIRED_SUPPORT | {mac_name}:
        checked_asset(assets, name, repo, tag)
    windows_tag, windows_version, windows_build = tag, version, build
    windows_name = f"LockIn-{version}-{build}-Windows-x64.zip"
    carry = None
    if manifest is not None:
        if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 1 or any(
                manifest.get(key) != value for key, value in (("repo", repo), ("tag", tag), ("version", version), ("build", build))):
            raise ValueError("Release manifest identity differs from the published Mac release.")
        carry = manifest.get("windowsCarryForward")
        if carry is not None and manifest.get("windowsAsset") is not None:
            raise ValueError("A release cannot both replace and carry forward Windows.")
        if carry is None and manifest.get("windowsAsset") != windows_name:
            raise ValueError("Release manifest has no matching Windows target.")
    if carry is not None:
        if not isinstance(carry, dict) or windows_release is None:
            raise ValueError("The held Windows target requires its original published release metadata.")
        windows_tag, windows_version, windows_build, windows_assets = release_identity(windows_release, repo, carry.get("tag"))
        windows_name = f"LockIn-{windows_version}-{windows_build}-Windows-x64.zip"
        if (carry.get("tag") != windows_tag or carry.get("version") != windows_version or carry.get("build") != windows_build or
                carry.get("asset") != windows_name or int(windows_build) >= int(build) or
                not isinstance(carry.get("sourceRevision"), str) or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", carry["sourceRevision"]) or
                any(not isinstance(carry.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", carry[key])
                    for key in ("sha256", "metadataSHA256", "signatureSHA256"))):
            raise ValueError("Invalid carried Windows identity or provenance.")
        windows_asset = checked_asset(windows_assets, windows_name, repo, windows_tag)
        if isinstance(carry.get("size"), bool) or carry.get("size") != windows_asset["size"]:
            raise ValueError("Held Windows asset size differs from its signed target.")
        if windows_asset.get("digest") is not None and windows_asset["digest"] != "sha256:" + carry["sha256"]:
            raise ValueError("Held Windows asset checksum differs from its signed target.")
    else:
        checked_asset(assets, windows_name, repo, windows_tag)
    values = {"MAC_URL": f"https://github.com/{repo}/releases/download/{tag}/{mac_name}",
              "WINDOWS_URL": f"https://github.com/{repo}/releases/download/{windows_tag}/{windows_name}"}
    values.update(VERSION=version, BUILD=build, RELEASE_URL=f"https://github.com/{repo}/releases/tag/{tag}")
    values.update(WINDOWS_VERSION=windows_version, WINDOWS_BUILD=windows_build,
                  WINDOWS_RELEASE_URL=f"https://github.com/{repo}/releases/tag/{windows_tag}")
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", value)
    if "{{" in template or "}}" in template:
        raise ValueError("Unresolved download page template value.")
    return template


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="cchow375/LockIn-Releases")
    parser.add_argument("--release-json", type=Path, help="Read a saved REST release response instead of GitHub.")
    parser.add_argument("--manifest-json", type=Path, help="Read a saved release manifest for a Mac-only release.")
    parser.add_argument("--windows-release-json", type=Path, help="Read REST metadata for the held Windows release.")
    parser.add_argument("--template", type=Path, default=ROOT / "(C) Download Page Template.md")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    validate_repo(args.repo)
    if args.release_json:
        release = json.loads(args.release_json.read_text())
    else:
        result = subprocess.run(["gh", "api", f"repos/{args.repo}/releases/latest"], check=True, capture_output=True, text=True)
        release = json.loads(result.stdout)
    tag, version, build, assets = release_identity(release, args.repo)
    manifest = json.loads(args.manifest_json.read_text()) if args.manifest_json else None
    windows_release = json.loads(args.windows_release_json.read_text()) if args.windows_release_json else None
    if manifest is None and f"LockIn-{version}-{build}-Windows-x64.zip" not in assets:
        if args.release_json:
            raise ValueError("Saved Mac-only metadata also requires --manifest-json.")
        asset = checked_asset(assets, "release.json", args.repo, tag)
        with urllib.request.urlopen(asset["browser_download_url"], timeout=30) as response:
            content = response.read(1024 * 1024 + 1)
        if len(content) > 1024 * 1024:
            raise ValueError("Release manifest exceeds the size limit.")
        manifest = json.loads(content)
    if isinstance(manifest, dict) and manifest.get("windowsCarryForward") is not None and windows_release is None:
        if args.release_json:
            raise ValueError("Saved Mac-only metadata also requires --windows-release-json.")
        carry = manifest["windowsCarryForward"]
        if not isinstance(carry, dict) or not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+-[1-9][0-9]{0,13}", carry.get("tag", "")):
            raise ValueError("Invalid carried Windows tag.")
        result = subprocess.run(["gh", "api", f"repos/{args.repo}/releases/tags/{carry['tag']}"], check=True, capture_output=True, text=True)
        windows_release = json.loads(result.stdout)
    page = render(release, args.repo, args.template.read_text(), manifest, windows_release)
    args.output.write_text(page)
    print(f"Wrote direct Mac/Windows links for {release['tag_name']} to {args.output}.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"Download page unchanged: {error}") from error
