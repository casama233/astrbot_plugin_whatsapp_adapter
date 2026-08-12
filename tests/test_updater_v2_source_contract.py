from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class UpdaterV2SourceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.main_source = (ROOT / "main.py").read_text(encoding="utf-8")
        cls.updater_source = (ROOT / "plugin_updater.py").read_text(encoding="utf-8")
        cls.app_source = (ROOT / "pages" / "whatsapp-login" / "app.js").read_text(
            encoding="utf-8"
        )

    def test_perform_update_uses_confirmed_release_argument_not_latest_lookup(self) -> None:
        match = re.search(
            r"async def _perform_update\([\s\S]+?\n    def _local_update_task_active",
            self.main_source,
        )
        self.assertIsNotNone(match)
        body = match.group(0)
        self.assertIn("release: ReleaseDetails", body)
        self.assertIn("transaction_id: str", body)
        self.assertNotIn("_latest_release(", body)
        self.assertNotIn("fetch_latest_release", body)

    def test_install_endpoint_requires_candidate_token_and_expected_version(self) -> None:
        self.assertIn('payload.get("candidateToken")', self.main_source)
        self.assertIn('payload.get("expectedVersion")', self.main_source)
        self.assertIn("release.candidate_token != candidate_token", self.main_source)
        self.assertIn("release.version != expected_version", self.main_source)

    def test_updater_does_not_mutate_global_python_dependencies(self) -> None:
        self.assertNotIn("_ensure_plugin_requirements", self.main_source)
        self.assertIn("validate_python_requirements_unchanged", self.main_source)

    def test_staging_and_backups_live_outside_plugin_scan_directory(self) -> None:
        self.assertIn('".plugin-updates"', self.main_source)
        self.assertIn('".plugin-backups"', self.main_source)
        self.assertNotIn('mkdtemp(prefix=f".{PLUGIN_NAME}-update-", dir=str(PLUGIN_DIR.parent))', self.main_source)

    def test_success_requires_health_gate_before_backup_cleanup(self) -> None:
        reload_start = self.main_source.index("async def _reload_after_update")
        rollback_start = self.main_source.index("async def _rollback_update")
        body = self.main_source[reload_start:rollback_start]
        health_pos = body.index("await self._verify_update_health")
        completed_pos = body.index('"phase": "completed"')
        delete_pos = body.index("shutil.rmtree, backup_dir")
        self.assertLess(health_pos, completed_pos)
        self.assertLess(completed_pos, delete_pos)

    def test_release_source_fallback_is_forbidden(self) -> None:
        self.assertNotIn("zipball_url", self.updater_source)
        self.assertIn("Release 必须且只能包含一个正式 artifact", self.updater_source)
        self.assertIn("expected_sha256", self.updater_source)

    def test_frontend_has_no_modal_confirmation_dependency(self) -> None:
        self.assertNotIn("window.confirm", self.app_source)
        self.assertIn("createTwoStepGate", self.app_source)
        self.assertIn("candidateToken", self.app_source)
        self.assertIn("expectedVersion: latest", self.app_source)


if __name__ == "__main__":
    unittest.main()
