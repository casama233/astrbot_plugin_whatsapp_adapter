from __future__ import annotations

import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERIFIER_PATH = ROOT / "scripts" / "verify-release-runtime.py"
SPEC = importlib.util.spec_from_file_location("verify_release_runtime", VERIFIER_PATH)
if SPEC is None or SPEC.loader is None:  # pragma: no cover - repository contract
    raise RuntimeError("cannot load release runtime verifier")
VERIFY_RUNTIME = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY_RUNTIME)


class ReleaseRuntimeArchiveTests(unittest.TestCase):
    def test_every_declared_runtime_file_exists_in_repository(self) -> None:
        missing = sorted(
            relative
            for relative in VERIFY_RUNTIME.REQUIRED_RUNTIME_FILES
            if not (ROOT / relative).is_file()
        )
        self.assertEqual(missing, [])

    def test_complete_runtime_archive_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            archive_path = Path(temp) / "release.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                for relative in sorted(VERIFY_RUNTIME.REQUIRED_RUNTIME_FILES):
                    archive.writestr(
                        f"{VERIFY_RUNTIME.PLUGIN_ROOT}{relative}",
                        f"placeholder for {relative}\n",
                    )
            VERIFY_RUNTIME.validate_release_runtime(archive_path)

    def test_missing_runtime_module_fails_closed(self) -> None:
        missing_name = "gateway/stability-runtime.mjs"
        with tempfile.TemporaryDirectory() as temp:
            archive_path = Path(temp) / "release.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                for relative in sorted(VERIFY_RUNTIME.REQUIRED_RUNTIME_FILES):
                    if relative == missing_name:
                        continue
                    archive.writestr(
                        f"{VERIFY_RUNTIME.PLUGIN_ROOT}{relative}",
                        f"placeholder for {relative}\n",
                    )
            with self.assertRaisesRegex(RuntimeError, "stability-runtime\\.mjs"):
                VERIFY_RUNTIME.validate_release_runtime(archive_path)

    def test_release_workflow_runs_verifier_and_does_not_ship_it(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn(
            'python scripts/verify-release-runtime.py "$RELEASE_ASSET"',
            workflow,
        )
        self.assertIn(
            "scripts/verify-release-runtime.py export-ignore",
            attributes,
        )


if __name__ == "__main__":
    unittest.main()
