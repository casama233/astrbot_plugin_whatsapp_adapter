from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ReleaseWorkflowPolicyTests(unittest.TestCase):
    def test_release_workflow_validates_before_mutating_main(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("mode:", workflow)
        self.assertIn("preflight", workflow)
        self.assertIn("python scripts/release_contract.py validate-marker", workflow)
        self.assertIn("python scripts/release_contract.py validate-archive", workflow)
        self.assertIn("existing tag $tag points to", workflow)
        self.assertIn("RESUME_RELEASE=true", workflow)
        self.assertIn("RELEASE_CHECKSUM", workflow)
        self.assertLess(
            workflow.index("Validate AstrBot release artifact"),
            workflow.index("Push release commit"),
        )
        self.assertNotIn("tests/test_whatsapp_config_policy.py .release", workflow)
        self.assertNotIn("npm version", workflow)

    def test_github_actions_use_immutable_commit_shas(self) -> None:
        for relative_path in (
            ".github/workflows/tests.yml",
            ".github/workflows/release.yml",
        ):
            workflow = (ROOT / relative_path).read_text("utf-8")
            action_refs = re.findall(
                r"^\s*-\s+uses:\s+([^#\s]+)",
                workflow,
                flags=re.MULTILINE,
            )
            self.assertTrue(action_refs, relative_path)
            for action_ref in action_refs:
                self.assertRegex(
                    action_ref,
                    r"^[^@\s]+@[0-9a-f]{40}$",
                    f"{relative_path}: mutable GitHub Action ref {action_ref}",
                )

    def test_release_archive_excludes_development_only_content(self) -> None:
        attributes = (ROOT / ".gitattributes").read_text("utf-8")
        for entry in (
            ".github/ export-ignore",
            ".release/ export-ignore",
            "tests/ export-ignore",
            "scripts/release_contract.py export-ignore",
        ):
            self.assertIn(entry, attributes)


if __name__ == "__main__":
    unittest.main()
