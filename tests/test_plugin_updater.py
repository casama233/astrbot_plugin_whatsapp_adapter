from __future__ import annotations

import stat
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path

sys.modules.setdefault("aiohttp", types.ModuleType("aiohttp"))

from plugin_updater import (  # noqa: E402
    PluginUpdateError,
    atomic_swap_plugin,
    extract_validated_release,
    is_newer_version,
    restore_plugin_backup,
    select_latest_release,
    validate_download_url,
)


class PluginUpdaterTests(unittest.TestCase):
    def test_selects_highest_stable_release_and_ignores_prereleases(self) -> None:
        payload = [
            {
                "tag_name": "v0.3.0-rc1",
                "prerelease": True,
                "draft": False,
                "zipball_url": "https://api.github.com/repos/example/repo/zipball/v0.3.0-rc1",
                "html_url": "https://github.com/example/repo/releases/tag/v0.3.0-rc1",
            },
            {
                "tag_name": "v0.2.29",
                "name": "v0.2.29",
                "prerelease": False,
                "draft": False,
                "published_at": "2026-08-01T00:00:00Z",
                "body": "safe update",
                "zipball_url": "https://api.github.com/repos/example/repo/zipball/v0.2.29",
                "html_url": "https://github.com/example/repo/releases/tag/v0.2.29",
                "assets": [],
            },
            {
                "tag_name": "v0.2.28",
                "prerelease": False,
                "draft": False,
                "zipball_url": "https://api.github.com/repos/example/repo/zipball/v0.2.28",
                "html_url": "https://github.com/example/repo/releases/tag/v0.2.28",
            },
        ]

        release = select_latest_release(payload, "0.2.28")

        self.assertEqual(release.version, "0.2.29")
        self.assertEqual(release.notes, "safe update")
        self.assertTrue(is_newer_version(release.version, "0.2.28"))

    def test_prefers_named_release_zip_asset(self) -> None:
        payload = [
            {
                "tag_name": "v0.2.29",
                "prerelease": False,
                "draft": False,
                "zipball_url": "https://api.github.com/repos/example/repo/zipball/v0.2.29",
                "html_url": "https://github.com/example/repo/releases/tag/v0.2.29",
                "assets": [
                    {
                        "name": "astrbot_plugin_whatsapp_adapter-v0.2.29.zip",
                        "browser_download_url": "https://github.com/example/repo/releases/download/v0.2.29/plugin.zip",
                    }
                ],
            }
        ]

        release = select_latest_release(payload, "0.2.28")

        self.assertEqual(release.asset_name, "astrbot_plugin_whatsapp_adapter-v0.2.29.zip")
        self.assertIn("/releases/download/", release.download_url)

    def test_rejects_untrusted_or_authenticated_download_urls(self) -> None:
        for url in (
            "http://github.com/example/repo.zip",
            "https://example.com/repo.zip",
            "https://token@github.com/example/repo.zip",
            "https://github.com:444/example/repo.zip",
        ):
            with self.subTest(url=url), self.assertRaises(PluginUpdateError):
                validate_download_url(url)

    def test_ignores_unrelated_zip_asset_and_uses_source_archive(self) -> None:
        payload = [
            {
                "tag_name": "v0.2.29",
                "prerelease": False,
                "draft": False,
                "zipball_url": "https://api.github.com/repos/example/repo/zipball/v0.2.29",
                "html_url": "https://github.com/example/repo/releases/tag/v0.2.29",
                "assets": [
                    {
                        "name": "unrelated-backup.zip",
                        "browser_download_url": "https://github.com/example/repo/releases/download/v0.2.29/backup.zip",
                    }
                ],
            }
        ]

        release = select_latest_release(payload, "0.2.28")

        self.assertEqual(release.asset_name, "source-0.2.29.zip")
        self.assertIn("api.github.com", release.download_url)

    @staticmethod
    def _write_release(
        archive_path: Path,
        *,
        version: str = "0.2.29",
        unsafe_name: str | None = None,
        symlink: bool = False,
    ) -> None:
        root = "casama233-plugin-deadbeef"
        metadata = (
            "name: astrbot_plugin_whatsapp_adapter\n"
            f"version: {version}\n"
            "repo: https://github.com/casama233/astrbot_plugin_whatsapp_adapter\n"
            'astrbot_version: ">=4.24.2,<5"\n'
        )
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr(f"{root}/metadata.yaml", metadata)
            archive.writestr(f"{root}/main.py", "PLUGIN = True\n")
            archive.writestr(f"{root}/package-lock.json", "{}\n")
            if unsafe_name:
                archive.writestr(unsafe_name, "escape")
            if symlink:
                info = zipfile.ZipInfo(f"{root}/linked")
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, "main.py")

    def test_extracts_only_valid_plugin_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "release.zip"
            destination = root / "staged"
            self._write_release(archive)

            metadata = extract_validated_release(
                archive,
                destination,
                expected_name="astrbot_plugin_whatsapp_adapter",
                expected_version="0.2.29",
            )

            self.assertEqual(metadata["version"], "0.2.29")
            self.assertTrue((destination / "main.py").is_file())
            self.assertFalse((destination / "casama233-plugin-deadbeef").exists())

    def test_rejects_traversal_symlink_and_wrong_version(self) -> None:
        cases = (
            {"unsafe_name": "../escape.txt"},
            {"symlink": True},
            {"version": "0.2.30"},
        )
        for index, options in enumerate(cases):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                archive = root / f"release-{index}.zip"
                self._write_release(archive, **options)
                with self.assertRaises(PluginUpdateError):
                    extract_validated_release(
                        archive,
                        root / "staged",
                        expected_name="astrbot_plugin_whatsapp_adapter",
                        expected_version="0.2.29",
                    )

    def test_atomic_swap_and_restore(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            current = root / "plugin"
            staged = root / "staged"
            backup = root / "backup"
            failed = root / "failed"
            current.mkdir()
            staged.mkdir()
            (current / "version").write_text("old", encoding="utf-8")
            (staged / "version").write_text("new", encoding="utf-8")

            atomic_swap_plugin(current, staged, backup)
            self.assertEqual((current / "version").read_text(encoding="utf-8"), "new")
            self.assertEqual((backup / "version").read_text(encoding="utf-8"), "old")

            restore_plugin_backup(current, backup, failed)
            self.assertEqual((current / "version").read_text(encoding="utf-8"), "old")
            self.assertFalse(backup.exists())
            self.assertFalse(failed.exists())

    def test_atomic_swap_restores_current_when_second_rename_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            current = root / "plugin"
            current.mkdir()
            (current / "version").write_text("old", encoding="utf-8")

            with self.assertRaises(OSError):
                atomic_swap_plugin(current, root / "missing", root / "backup")

            self.assertEqual((current / "version").read_text(encoding="utf-8"), "old")
            self.assertFalse((root / "backup").exists())


if __name__ == "__main__":
    unittest.main()
