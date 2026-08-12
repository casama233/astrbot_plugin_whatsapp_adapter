from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "_conf_schema.json"
I18N_DIR = ROOT / ".astrbot-plugin" / "i18n"
PAGE_DIR = ROOT / "pages" / "whatsapp-login"
LOCALES = ("en-US", "zh-CN", "zh-TW")
CJK_RE = re.compile(r"[\u3400-\u9fff]")
DATA_KEY_RE = re.compile(r'data-i18n(?:-(?:title|placeholder|aria-label))?="([^"]+)"')
CALL_KEY_RE = re.compile(r"\b(?:t|tf)\(\s*[\"']([^\"']+)[\"']")
UPDATE_PHASES = (
    "idle",
    "queued",
    "checking",
    "available",
    "up_to_date",
    "check_failed",
    "downloading",
    "validating",
    "installing_dependencies",
    "quiescing",
    "installing",
    "reloading",
    "health_checking",
    "rolling_back",
    "completed",
    "failed",
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def get_path(source: dict[str, Any], path: str) -> Any:
    current: Any = source
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


class PluginI18nCoverageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = load_json(SCHEMA)
        self.locales = {
            locale: load_json(I18N_DIR / f"{locale}.json")
            for locale in LOCALES
        }

    def test_plugin_metadata_and_schema_are_localized(self) -> None:
        for locale, resource in self.locales.items():
            metadata = resource.get("metadata", {})
            for key in ("display_name", "short_desc", "desc"):
                self.assertTrue(
                    isinstance(metadata.get(key), str) and metadata[key].strip(),
                    f"{locale} missing metadata.{key}",
                )

            config = resource.get("config", {})
            for key, meta in self.schema.items():
                if not isinstance(meta, dict) or meta.get("invisible"):
                    continue
                localized = config.get(key)
                self.assertIsInstance(localized, dict, f"{locale} missing config.{key}")
                for attr in ("description", "hint"):
                    if not meta.get(attr):
                        continue
                    self.assertTrue(
                        isinstance(localized.get(attr), str) and localized[attr].strip(),
                        f"{locale} missing config.{key}.{attr}",
                    )
                options = meta.get("options")
                if isinstance(options, list):
                    labels = localized.get("labels")
                    self.assertIsInstance(labels, list, f"{locale} missing config.{key}.labels")
                    self.assertEqual(
                        len(labels), len(options),
                        f"{locale} config.{key}.labels length mismatch",
                    )
                    self.assertTrue(
                        all(isinstance(label, str) and label.strip() for label in labels),
                        f"{locale} config.{key}.labels contains an empty label",
                    )

    def test_plugin_page_keys_are_covered_by_every_locale(self) -> None:
        sources = {
            path.name: path.read_text(encoding="utf-8")
            for path in (
                PAGE_DIR / "index.html",
                PAGE_DIR / "app.js",
                PAGE_DIR / "sandbox-confirm.js",
            )
        }
        keys: set[str] = set(DATA_KEY_RE.findall(sources["index.html"]))
        for name in ("app.js", "sandbox-confirm.js"):
            keys.update(CALL_KEY_RE.findall(sources[name]))
        keys.update(f"update.phase.{phase}" for phase in UPDATE_PHASES)

        for locale, resource in self.locales.items():
            page = get_path(resource, "pages.whatsapp-login")
            self.assertIsInstance(page, dict, f"{locale} missing pages.whatsapp-login")
            missing = sorted(key for key in keys if get_path(page, key) is None)
            self.assertFalse(
                missing,
                f"{locale} missing WhatsApp Login Page keys: {missing}",
            )

    def test_plugin_page_source_has_no_hardcoded_cjk_ui_copy(self) -> None:
        for path in (
            PAGE_DIR / "index.html",
            PAGE_DIR / "app.js",
            PAGE_DIR / "sandbox-confirm.js",
        ):
            source = path.read_text(encoding="utf-8")
            self.assertIsNone(
                CJK_RE.search(source),
                f"{path.relative_to(ROOT)} contains hardcoded CJK UI text",
            )

    def test_plugin_page_reacts_to_runtime_locale_changes(self) -> None:
        source = (PAGE_DIR / "app.js").read_text(encoding="utf-8")
        self.assertIn("bridge.getLocale", source)
        self.assertIn("bridge.onContext", source)

    def test_updater_v2_contract_survives_localization(self) -> None:
        source = (PAGE_DIR / "app.js").read_text(encoding="utf-8")
        self.assertIn('createTwoStepGate', source)
        self.assertIn('release.candidateToken', source)
        self.assertIn('candidateToken,', source)
        self.assertIn('expectedVersion: latest', source)
        self.assertNotIn('window.confirm', source)
        self.assertNotIn('.innerHTML', source)


if __name__ == "__main__":
    unittest.main()
