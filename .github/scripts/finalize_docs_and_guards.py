from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

readme_path = ROOT / "README.md"
readme = readme_path.read_text("utf-8")
readme = readme.replace(
    "3. **平台实例配置**：某个 WhatsApp 账号独有的连接覆盖、访问控制、媒体 caption、忽略自身消息、reaction 与消失消息行为。",
    "3. **平台实例配置**：某个 WhatsApp 账号独有的访问控制、媒体 caption、忽略自身消息、reaction 与消失消息行为。",
)
readme = readme.replace(
    '''专用号码，仅私聊：
```json
{
  "gateway_host": "127.0.0.1",
  "gateway_port": 18789,
  "allow_from": ["+15551234567"],
''',
    '''专用号码，仅私聊（Gateway 地址和端口在插件配置页设置）：
```json
{
  "allow_from": ["+15551234567"],
''',
)
readme_path.write_text(readme, "utf-8")

doc_path = ROOT / "docs/zh-CN.md"
doc = doc_path.read_text("utf-8")
doc = doc.replace(
    "- **平台实例配置**：账号连接覆盖、访问控制、caption、忽略自身消息、reaction 和消失消息。",
    "- **平台实例配置**：账号访问控制、caption、忽略自身消息、reaction 和消失消息。",
)
doc_path.write_text(doc, "utf-8")

changelog_path = ROOT / "CHANGELOG.md"
changelog = changelog_path.read_text("utf-8")
changelog = changelog.replace(
    "- 移除重复的指令前缀、指令扫描与唤醒判断层，完全沿用 AstrBot 的 `wake_prefix` 和 CommandFilter 流程。",
    "- 移除用户可配置的第二套指令前缀；正常流程统一沿用 AstrBot 的 `wake_prefix` 和 CommandFilter，仅为旧版非 `/` 前缀保留一个版本的隐藏兼容扫描。",
)
changelog_path.write_text(changelog, "utf-8")

test_path = ROOT / "tests/test_whatsapp_config_policy.py"
test = test_path.read_text("utf-8")
test = test.replace(
    '''        self.assertIn("get_runtime_plugin_defaults()", adapter)
        self.assertIn("extract_legacy_behavior_overrides(platform_config)", adapter)
''',
    '''        self.assertIn("RUNTIME_DEFAULT_CONFIG: dict[str, Any] = {\\n    **BASE_GATEWAY_CONFIG,", adapter)
        self.assertNotIn("DEFAULT_CONFIG: dict[str, Any] = {\\n    **BASE_GATEWAY_CONFIG,", adapter)
        self.assertIn("get_runtime_plugin_defaults()", adapter)
        self.assertIn("extract_legacy_behavior_overrides(platform_config)", adapter)
''',
    1,
)
test_path.write_text(test, "utf-8")

precommit_path = ROOT / ".pre-commit-config.yaml"
config = precommit_path.read_text("utf-8")
hook = '''      - id: finalize-docs-and-guards
        name: Finalize docs and regression guards
        entry: python .github/scripts/finalize_docs_and_guards.py
        language: system
        pass_filenames: false
'''
precommit_path.write_text(config.replace(hook, ""), "utf-8")
Path(__file__).unlink(missing_ok=True)
try:
    (ROOT / ".github/scripts").rmdir()
except OSError:
    pass
