#!/usr/bin/env python3
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("download_page", Path(__file__).with_name("download-page.py"))
page = importlib.util.module_from_spec(spec)
spec.loader.exec_module(page)


class DownloadPageTests(unittest.TestCase):
    def setUp(self):
        self.repo = "example/LockIn-Releases"
        self.version, self.build = "1.8.0", "20260929232850"
        self.tag = f"v{self.version}-{self.build}"
        names = page.REQUIRED_SUPPORT | {f"LockIn-{self.version}-{self.build}.zip", f"LockIn-{self.version}-{self.build}-Windows-x64.zip"}
        self.release = {"tag_name": self.tag, "draft": False, "prerelease": False,
                        "assets": [{"name": name, "size": 100, "state": "uploaded", "browser_download_url": f"https://github.com/{self.repo}/releases/download/{self.tag}/{name}"} for name in names]}
        self.template = "Mac: {{MAC_URL}}\nWindows: {{WINDOWS_URL}}\nMac {{VERSION}} / {{BUILD}}\nWindows {{WINDOWS_VERSION}} / {{WINDOWS_BUILD}}\n{{RELEASE_URL}}\n{{WINDOWS_RELEASE_URL}}\n"

    def held_windows(self):
        mac = copy.deepcopy(self.release)
        mac["assets"] = [asset for asset in mac["assets"] if not asset["name"].endswith("-Windows-x64.zip")]
        old_version, old_build = "1.7.0", "20260928000100"
        old_tag = f"v{old_version}-{old_build}"
        name = f"LockIn-{old_version}-{old_build}-Windows-x64.zip"
        windows = {"tag_name": old_tag, "draft": False, "prerelease": False, "assets": [
            {"name": name, "size": 100, "state": "uploaded", "digest": "sha256:" + "b" * 64,
             "browser_download_url": f"https://github.com/{self.repo}/releases/download/{old_tag}/{name}"}]}
        manifest = {"schemaVersion": 1, "repo": self.repo, "version": self.version, "build": self.build, "tag": self.tag,
                    "windowsCarryForward": {"version": old_version, "build": old_build, "tag": old_tag, "asset": name,
                                            "sourceRevision": "a" * 40, "size": 100, "sha256": "b" * 64,
                                            "metadataSHA256": "c" * 64, "signatureSHA256": "d" * 64}}
        return mac, manifest, windows

    def test_direct_links_are_from_the_same_uploaded_release(self):
        result = page.render(self.release, self.repo, self.template)
        self.assertIn(f"/releases/download/{self.tag}/LockIn-{self.version}-{self.build}.zip", result)
        self.assertIn(f"/releases/download/{self.tag}/LockIn-{self.version}-{self.build}-Windows-x64.zip", result)
        self.assertNotIn("{{", result)

    def test_draft_prerelease_and_bad_tags_are_rejected(self):
        for mutation in [{"draft": True}, {"prerelease": True}, {"tag_name": "v1.8.0-[bad]"}]:
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                page.render({**self.release, **mutation}, self.repo, self.template)

    def test_windows_only_or_missing_update_metadata_cannot_replace_links(self):
        for name in [f"LockIn-{self.version}-{self.build}.zip", "windows-update.json.sig", "appcast.xml"]:
            release = copy.deepcopy(self.release)
            release["assets"] = [asset for asset in release["assets"] if asset["name"] != name]
            with self.subTest(name=name), self.assertRaises(ValueError):
                page.render(release, self.repo, self.template)

    def test_external_urls_incomplete_assets_and_duplicate_names_are_rejected(self):
        for mutation in [{"browser_download_url": "https://untrusted.invalid/file.zip"}, {"size": 0}, {"state": "new"}]:
            release = copy.deepcopy(self.release); release["assets"][0].update(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                page.render(release, self.repo, self.template)
        release = copy.deepcopy(self.release); release["assets"].append(release["assets"][0])
        with self.assertRaises(ValueError): page.render(release, self.repo, self.template)

    def test_failed_generation_preserves_existing_readme(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); output = root / "README.md"; output.write_text("Keep this page.")
            metadata = root / "release.json"; metadata.write_text(json.dumps({**self.release, "draft": True}))
            template = root / "template.md"; template.write_text(self.template)
            with self.assertRaises(ValueError):
                page.main(["--repo", self.repo, "--release-json", str(metadata), "--template", str(template), "--output", str(output)])
            self.assertEqual(output.read_text(), "Keep this page.")

    def test_live_mode_only_reads_github_and_writes_requested_markdown(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(page.subprocess, "run") as run:
            root = Path(temporary); template = root / "template.md"; template.write_text(self.template)
            run.return_value.stdout = json.dumps(self.release)
            output = root / "README.md"
            page.main(["--repo", self.repo, "--template", str(template), "--output", str(output)])
            self.assertEqual(run.call_args.args[0], ["gh", "api", f"repos/{self.repo}/releases/latest"])
            self.assertIn(self.tag, output.read_text())

    def test_mac_only_page_links_to_original_windows_version_and_immutable_archive(self):
        mac, manifest, windows = self.held_windows()
        result = page.render(mac, self.repo, self.template, manifest, windows)
        self.assertIn(f"/releases/download/{self.tag}/LockIn-{self.version}-{self.build}.zip", result)
        old = manifest["windowsCarryForward"]
        self.assertIn(f"/releases/download/{old['tag']}/{old['asset']}", result)
        self.assertIn(f"Windows {old['version']} / {old['build']}", result)
        self.assertIn(f"/releases/tag/{old['tag']}", result)
        self.assertNotIn(f"LockIn-{self.version}-{self.build}-Windows-x64.zip", result)
        self.assertNotIn("{{", result)
        with self.assertRaises(ValueError):
            page.render(mac, self.repo, self.template)

    def test_held_windows_requires_original_published_asset_and_matching_provenance(self):
        mac, manifest, windows = self.held_windows()
        for mutation in ({"draft": True}, {"prerelease": True}, {"assets": []}, {"tag_name": self.tag}):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                page.render(mac, self.repo, self.template, manifest, {**windows, **mutation})
        for mutation in ({"size": 99}, {"state": "new"}, {"digest": "sha256:" + "e" * 64},
                         {"browser_download_url": "https://untrusted.invalid/windows.zip"}):
            invalid = copy.deepcopy(windows); invalid["assets"][0].update(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                page.render(mac, self.repo, self.template, manifest, invalid)
        for mutation in ({"tag": None}, {"version": self.version}, {"build": self.build}, {"asset": "../Windows.zip"},
                         {"sourceRevision": "HEAD"}, {"sha256": "bad"}, {"size": True}):
            invalid = copy.deepcopy(manifest); invalid["windowsCarryForward"].update(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                page.render(mac, self.repo, self.template, invalid, windows)

    def test_held_windows_does_not_allow_spoofed_mac_manifest_or_dual_targets(self):
        mac, manifest, windows = self.held_windows()
        for mutation in ({"repo": "other/repo"}, {"tag": windows["tag_name"]}, {"build": "1"}, {"schemaVersion": 2},
                         {"windowsAsset": f"LockIn-{self.version}-{self.build}-Windows-x64.zip"}):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                page.render(mac, self.repo, self.template, {**manifest, **mutation}, windows)
        with self.assertRaises(ValueError):
            page.render(mac, self.repo, self.template, manifest)

    def test_mac_only_live_mode_reads_manifest_and_original_windows_release(self):
        mac, manifest, windows = self.held_windows()
        with tempfile.TemporaryDirectory() as temporary, patch.object(page.subprocess, "run") as run, patch.object(page.urllib.request, "urlopen") as fetch:
            root = Path(temporary); template = root / "template.md"; template.write_text(self.template)
            run.side_effect = [type("Result", (), {"stdout": json.dumps(mac)})(), type("Result", (), {"stdout": json.dumps(windows)})()]
            fetch.return_value.__enter__.return_value.read.return_value = json.dumps(manifest).encode()
            output = root / "README.md"
            page.main(["--repo", self.repo, "--template", str(template), "--output", str(output)])
            self.assertEqual([call.args[0] for call in run.call_args_list], [
                ["gh", "api", f"repos/{self.repo}/releases/latest"],
                ["gh", "api", f"repos/{self.repo}/releases/tags/{windows['tag_name']}"]])
            fetch.assert_called_once_with(f"https://github.com/{self.repo}/releases/download/{self.tag}/release.json", timeout=30)
            self.assertIn(manifest["windowsCarryForward"]["asset"], output.read_text())

    def test_failed_held_windows_generation_preserves_existing_readme(self):
        mac, manifest, windows = self.held_windows()
        windows["assets"] = []
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); output = root / "README.md"; output.write_text("Keep the working downloads.")
            template = root / "template.md"; template.write_text(self.template)
            files = []
            for name, content in (("latest.json", mac), ("manifest.json", manifest), ("windows.json", windows)):
                path = root / name; path.write_text(json.dumps(content)); files.append(path)
            with self.assertRaises(ValueError):
                page.main(["--repo", self.repo, "--release-json", str(files[0]), "--manifest-json", str(files[1]),
                           "--windows-release-json", str(files[2]), "--template", str(template), "--output", str(output)])
            self.assertEqual(output.read_text(), "Keep the working downloads.")

    def test_real_template_resolves_for_combined_and_mac_only_releases(self):
        template = (Path(__file__).resolve().parent.parent / "(C) Download Page Template.md").read_text()
        self.assertNotIn("{{", page.render(self.release, self.repo, template))
        mac, manifest, windows = self.held_windows()
        result = page.render(mac, self.repo, template, manifest, windows)
        self.assertNotIn("{{", result)
        self.assertIn("Version 1.7.0", result)


if __name__ == "__main__":
    unittest.main()
