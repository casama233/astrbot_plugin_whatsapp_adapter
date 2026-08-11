from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "release_contract",
    ROOT / "scripts" / "release_contract.py",
)
assert SPEC and SPEC.loader
release_contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_contract)


class ReleaseContractTests(unittest.TestCase):
    def _copy_repo_contract_files(self, target: Path) -> None:
        for name in (
            "metadata.yaml",
            "main.py",
            "package.json",
            "package-lock.json",
            "CHANGELOG.md",
        ):
            shutil.copy2(ROOT / name, target / name)
        (target / ".release").mkdir()

    def test_current_repository_contract_is_consistent(self) -> None:
        contract = release_contract.validate_repo(ROOT)
        self.assertEqual(contract.version, "0.2.31")
        self.assertEqual(contract.metadata["name"], "astrbot_plugin_whatsapp_adapter")
        self.assertNotIn("support_platforms", contract.metadata)

    def test_semver_rejects_same_downgrade_prerelease_and_leading_zero(self) -> None:
        self.assertEqual(release_contract.parse_semver("1.2.3"), (1, 2, 3))
        for value in ("1.2", "v1.2.3", "1.2.3-beta.1", "01.2.3"):
            with self.subTest(value=value):
                with self.assertRaises(release_contract.ReleaseContractError):
                    release_contract.parse_semver(value)

    def test_marker_requires_newer_version_current_previous_and_canonical_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._copy_repo_contract_files(root)
            marker = root / ".release" / "0.2.32.json"
            marker.write_text(
                json.dumps(
                    {
                        "version": "0.2.32",
                        "previous_version": "0.2.31",
                        "date": "2026-08-11",
                        "commit_subject": "harden release workflow",
                        "notes": ["Validate the release contract."],
                    }
                ),
                encoding="utf-8",
            )
            spec = release_contract.load_marker(marker, root)
            self.assertEqual(spec["version"], "0.2.32")

            bad = json.loads(marker.read_text(encoding="utf-8"))
            bad["version"] = "0.2.31"
            marker.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaises(release_contract.ReleaseContractError):
                release_contract.load_marker(marker, root)

    def test_marker_rejects_unknown_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._copy_repo_contract_files(root)
            marker = root / ".release" / "0.2.32.json"
            marker.write_text(
                json.dumps(
                    {
                        "version": "0.2.32",
                        "previous_version": "0.2.31",
                        "date": "2026-08-11",
                        "commit_subject": "release",
                        "notes": ["note"],
                        "typo_field": True,
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(release_contract.ReleaseContractError):
                release_contract.load_marker(marker, root)

    def test_apply_marker_updates_every_version_source_and_removes_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._copy_repo_contract_files(root)
            marker = root / ".release" / "v0.2.32.json"
            marker.write_text(
                json.dumps(
                    {
                        "version": "0.2.32",
                        "previous_version": "0.2.31",
                        "date": "2026-08-11",
                        "commit_subject": "release contract",
                        "notes": ["Keep all version sources synchronized."],
                    }
                ),
                encoding="utf-8",
            )
            release_contract.apply_marker(marker, root)
            self.assertFalse(marker.exists())
            self.assertEqual(release_contract.validate_repo(root).version, "0.2.32")
            self.assertIn(
                "## [0.2.32] - 2026-08-11",
                (root / "CHANGELOG.md").read_text(encoding="utf-8"),
            )

    def test_archive_validation_matches_astrbot_and_self_updater_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            archive_path = tmp_path / "plugin.zip"
            prefix = "astrbot_plugin_whatsapp_adapter/"
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name in ("metadata.yaml", "main.py", "package.json", "package-lock.json"):
                    archive.write(ROOT / name, prefix + name)
            result = release_contract.validate_archive(archive_path, "0.2.31")
            self.assertEqual(result["version"], "0.2.31")
            self.assertLessEqual(result["size"], release_contract.ASTRBOT_MARKET_MAX_ZIP_BYTES)

    def test_archive_rejects_development_junk_and_wrong_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            archive_path = tmp_path / "bad.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("wrong-root/tests/test.py", "pass")
            with self.assertRaises(release_contract.ReleaseContractError):
                release_contract.validate_archive(archive_path, "0.2.31")


if __name__ == "__main__":
    unittest.main()
