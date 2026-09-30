#!/usr/bin/env python3
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("download_page", Path(__file__).with_name("(C) download-page.py"))
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
        self.template = "Mac: {{MAC_URL}}\nWindows: {{WINDOWS_URL}}\n{{VERSION}} / {{BUILD}}\n{{RELEASE_URL}}\n"

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


if __name__ == "__main__":
    unittest.main()
