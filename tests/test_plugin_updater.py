from __future__ import annotations

import json
import os
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
    ReleaseDetails,
    acquire_update_transaction,
    atomic_swap_plugin,
    extract_validated_release,
    is_newer_version,
    normalize_sha256_digest,
    recover_stale_update_transaction,
    release_update_transaction,
    restore_plugin_backup,
    select_latest_release,
    transaction_lock_active,
    validate_download_url,
    validate_python_requirements_unchanged,
)


class PluginUpdaterTests(unittest.TestCase):
    @staticmethod
    def _release(
        version: str = "0.2.36",
        *,
        release_id: int = 236,
        asset_id: int = 336,
        digest: str = "a" * 64,
        prerelease: bool = False,
        draft: bool = False,
        include_asset: bool = True,
    ) -> dict:
        assets = []
        if include_asset:
            assets.append(
                {
                    "id": asset_id,
                    "name": f"astrbot_plugin_whatsapp_adapter-v{version}.zip",
                    "browser_download_url": (
                        f"https://github.com/casama233/astrbot_plugin_whatsapp_adapter/"
                        f"releases/download/v{version}/astrbot_plugin_whatsapp_adapter-v{version}.zip"
                    ),
                    "digest": f"sha256:{digest}",
                }
            )
        return {
            "id": release_id,
            "tag_name": f"v{version}",
            "name": f"v{version}",
            "prerelease": prerelease,
            "draft": draft,
            "published_at": "2026-08-12T00:00:00Z",
            "body": "safe update",
            "target_commitish": "0123456789abcdef",
            "html_url": (
                f"https://github.com/casama233/astrbot_plugin_whatsapp_adapter/releases/tag/v{version}"
            ),
            # Deliberately present: Updater v2 must never use this fallback.
            "zipball_url": (
                f"https://api.github.com/repos/casama233/astrbot_plugin_whatsapp_adapter/zipball/v{version}"
            ),
            "assets": assets,
        }

    def test_selects_highest_stable_release_and_ignores_prereleases(self) -> None:
        payload = [
            self._release("0.2.37", release_id=237, asset_id=337, prerelease=True),
            self._release("0.2.36"),
            self._release("0.2.35", release_id=235, asset_id=335),
        ]

        release = select_latest_release(payload, "0.2.35")

        self.assertEqual(release.version, "0.2.36")
        self.assertEqual(release.notes, "safe update")
        self.assertTrue(is_newer_version(release.version, "0.2.35"))
        self.assertEqual(release.asset_digest, "a" * 64)
        self.assertEqual(
            release.asset_name,
            "astrbot_plugin_whatsapp_adapter-v0.2.36.zip",
        )

    def test_release_candidate_round_trip_is_bound_to_release_asset_and_digest(self) -> None:
        release = select_latest_release([self._release()], "0.2.35")
        restored = ReleaseDetails.from_dict(release.as_dict())

        self.assertEqual(restored, release)
        tampered = release.as_dict()
        tampered["assetId"] = 999
        with self.assertRaises(PluginUpdateError):
            ReleaseDetails.from_dict(tampered)

    def test_requires_exact_release_artifact_and_never_falls_back_to_zipball(self) -> None:
        with self.assertRaises(PluginUpdateError):
            select_latest_release(
                [self._release(include_asset=False)],
                "0.2.35",
            )

        wrong = self._release()
        wrong["assets"][0]["name"] = "source.zip"
        with self.assertRaises(PluginUpdateError):
            select_latest_release([wrong], "0.2.35")

    def test_requires_github_sha256_digest(self) -> None:
        missing = self._release()
        missing["assets"][0]["digest"] = None
        with self.assertRaises(PluginUpdateError):
            select_latest_release([missing], "0.2.35")

        self.assertEqual(normalize_sha256_digest(f"sha256:{'b' * 64}"), "b" * 64)
        with self.assertRaises(PluginUpdateError):
            normalize_sha256_digest("sha256:not-a-digest")

    def test_rejects_untrusted_or_authenticated_download_urls(self) -> None:
        for url in (
            "http://github.com/example/repo.zip",
            "https://example.com/repo.zip",
            "https://token@github.com/example/repo.zip",
            "https://github.com:444/example/repo.zip",
        ):
            with self.subTest(url=url), self.assertRaises(PluginUpdateError):
                validate_download_url(url)

    @staticmethod
    def _write_release(
        archive_path: Path,
        *,
        version: str = "0.2.36",
        unsafe_name: str | None = None,
        symlink: bool = False,
        package_version: str | None = None,
        requirements: str = "aiohttp>=3.9.0\n",
    ) -> None:
        root = "casama233-plugin-deadbeef"
        package_version = package_version or version
        metadata = (
            "name: astrbot_plugin_whatsapp_adapter\n"
            f"version: {version}\n"
            "repo: https://github.com/casama233/astrbot_plugin_whatsapp_adapter\n"
            'astrbot_version: ">=4.24.2,<5"\n'
        )
        package = {
            "name": "astrbot-plugin-whatsapp-adapter-gateway",
            "version": package_version,
        }
        lock = {
            "name": "astrbot-plugin-whatsapp-adapter-gateway",
            "version": package_version,
            "packages": {"": {"version": package_version}},
        }
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr(f"{root}/metadata.yaml", metadata)
            archive.writestr(
                f"{root}/main.py",
                f'PLUGIN_VERSION = "{version}"\nPLUGIN = True\n',
            )
            archive.writestr(f"{root}/package.json", json.dumps(package))
            archive.writestr(f"{root}/package-lock.json", json.dumps(lock))
            archive.writestr(f"{root}/requirements.txt", requirements)
            archive.writestr(f"{root}/gateway/whatsapp-gateway.mjs", "export {};\n")
            if unsafe_name:
                archive.writestr(unsafe_name, "escape")
            if symlink:
                info = zipfile.ZipInfo(f"{root}/linked")
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, "main.py")

    def test_extracts_only_valid_plugin_root_and_checks_version_consistency(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "release.zip"
            destination = root / "staged"
            self._write_release(archive)

            metadata = extract_validated_release(
                archive,
                destination,
                expected_name="astrbot_plugin_whatsapp_adapter",
                expected_version="0.2.36",
            )

            self.assertEqual(metadata["version"], "0.2.36")
            self.assertTrue((destination / "main.py").is_file())
            self.assertFalse((destination / "casama233-plugin-deadbeef").exists())

    def test_rejects_traversal_symlink_wrong_metadata_or_component_version(self) -> None:
        cases = (
            {"unsafe_name": "../escape.txt"},
            {"symlink": True},
            {"version": "0.2.37"},
            {"package_version": "0.2.35"},
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
                        expected_version="0.2.36",
                    )

    def test_python_requirements_must_remain_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            current = root / "current"
            staged = root / "staged"
            current.mkdir()
            staged.mkdir()
            (current / "requirements.txt").write_text(
                "# comment\naiohttp>=3.9.0\n",
                encoding="utf-8",
            )
            (staged / "requirements.txt").write_text(
                "aiohttp>=3.9.0\n",
                encoding="utf-8",
            )
            validate_python_requirements_unchanged(current, staged)

            (staged / "requirements.txt").write_text(
                "aiohttp>=3.10.0\n",
                encoding="utf-8",
            )
            with self.assertRaises(PluginUpdateError):
                validate_python_requirements_unchanged(current, staged)

    def test_atomic_swap_and_restore(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            current = root / "plugin"
            staged_parent = root / "staging"
            staged = staged_parent / "plugin"
            backup = root / "backups" / "backup"
            failed = root / "backups" / "failed"
            current.mkdir()
            staged.mkdir(parents=True)
            (current / "version").write_text("old", encoding="utf-8")
            (staged / "version").write_text("new", encoding="utf-8")

            strategy = atomic_swap_plugin(current, staged, backup)
            self.assertIn(strategy, {"rename-exchange", "rename-pair"})
            self.assertEqual((current / "version").read_text(encoding="utf-8"), "new")
            self.assertEqual((backup / "version").read_text(encoding="utf-8"), "old")

            restore_plugin_backup(current, backup, failed)
            self.assertEqual((current / "version").read_text(encoding="utf-8"), "old")
            self.assertFalse(backup.exists())
            self.assertFalse(failed.exists())

    def test_atomic_swap_restores_current_when_staged_directory_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            current = root / "plugin"
            current.mkdir()
            (current / "version").write_text("old", encoding="utf-8")

            with self.assertRaises(PluginUpdateError):
                atomic_swap_plugin(current, root / "missing", root / "backup")

            self.assertEqual((current / "version").read_text(encoding="utf-8"), "old")

    def test_transaction_lock_is_process_persistent_and_releasable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            lock = Path(temp) / "update.lock"
            acquire_update_transaction(lock, "tx-1")
            self.assertTrue(transaction_lock_active(lock))
            with self.assertRaises(PluginUpdateError):
                acquire_update_transaction(lock, "tx-2")
            release_update_transaction(lock, "tx-1")
            self.assertFalse(lock.exists())

    def test_stale_transaction_from_previous_process_is_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            lock = Path(temp) / "update.lock"
            lock.write_text(
                json.dumps(
                    {
                        "transactionId": "old-tx",
                        "pid": os.getpid() + 100000,
                        "createdAt": 1,
                    }
                ),
                encoding="utf-8",
            )
            stale = recover_stale_update_transaction(lock)
            self.assertEqual(stale["transactionId"], "old-tx")
            self.assertFalse(lock.exists())


if __name__ == "__main__":
    unittest.main()
