from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

adapter_path = ROOT / "whatsapp_adapter.py"
adapter = adapter_path.read_text("utf-8")
adapter = adapter.replace(
    "from .whatsapp_commands import collect_registered_commands, message_matches_command\n",
    "",
)
adapter = adapter.replace(
    '    "command_prefix": "/",\n    "register_commands": True,\n',
    "",
)
adapter = adapter.replace(
    "        self._registered_commands: list[str] = []\n",
    "",
)
adapter = adapter.replace("        self._refresh_registered_commands()\n", "")
adapter = adapter.replace(
    '''        is_command = self._message_matches_command(message.message_str or "")
        prefix = str(self.config.get("command_prefix") or "/")
        has_prefix = (message.message_str or "").strip().startswith(prefix)
''',
    "",
)
adapter = adapter.replace(
    '''                should_ack = group_mode == "always" or (
                    group_mode == "mentions" and (is_self_mentioned or is_reply_to_self or is_command)
                )
            if should_ack:
                if not is_command:
                    event.is_at_or_wake_command = True
                    event.is_wake = True
                await self._pre_ack(event)
        if is_command:
            event.is_at_or_wake_command = True
''',
    '''                should_ack = group_mode == "always" or (
                    group_mode == "mentions" and (is_self_mentioned or is_reply_to_self)
                )
            if should_ack:
                event.is_at_or_wake_command = True
                event.is_wake = True
                await self._pre_ack(event)
''',
)
adapter = adapter.replace(
    '            "Committing WhatsApp event: session=%s sender=%s raw_sender=%s message_id=%s text_len=%s self_mentioned=%s reply_to_self=%s is_private=%s is_command=%s",\n',
    '            "Committing WhatsApp event: session=%s sender=%s raw_sender=%s message_id=%s text_len=%s self_mentioned=%s reply_to_self=%s is_private=%s",\n',
)
adapter = adapter.replace(
    '''            is_reply_to_self,
            is_private,
            is_command,
''',
    '''            is_reply_to_self,
            is_private,
''',
)
adapter, method_count = re.subn(
    r'''(?ms)^    def _refresh_registered_commands\(self\) -> None:\n.*?^    def _message_matches_command\(self, text: str\) -> bool:\n.*?^        return message_matches_command\(text, self\._registered_commands, prefix=prefix\)\n\n''',
    "",
    adapter,
    count=1,
)
if method_count != 1:
    raise RuntimeError("custom command methods not found")
if any(token in adapter for token in ("collect_registered_commands", "message_matches_command", "_refresh_registered_commands", "_registered_commands")):
    raise RuntimeError("custom command layer was not fully removed")
adapter_path.write_text(adapter, "utf-8")

main_path = ROOT / "main.py"
main = main_path.read_text("utf-8")
main = main.replace("                adapter._refresh_registered_commands()\n", "")
main = main.replace("                inst._refresh_registered_commands()\n", "")
main_path.write_text(main, "utf-8")

readme_path = ROOT / "README.md"
readme = readme_path.read_text("utf-8")
readme = readme.replace("、流式输出和斜线指令。", "和流式输出。")
readme = re.sub(r"(?m)^\s*\* 斜线指令系统：.*\n", "", readme)
readme = re.sub(
    r"(?ms)^### 指令\n\n键 类型 默认值 说明\n`command_prefix`.*?\n`register_commands`.*?\n(?=### 消息)",
    "",
    readme,
)
readme = re.sub(
    r"(?ms)^## 斜线指令\n.*?(?=^## 推荐配置)",
    "",
    readme,
)
readme = readme.replace(
    "配置合并顺序：内置默认值 < 平台实例配置 < 插件配置页，最终以插件配置页为准。",
    "配置分层：协议限制与 AstrBot 通用指令行为使用内置/Core 设置；插件页提供通用 `default_*` 默认值；平台实例页只保留账号特定行为。",
)
readme_path.write_text(readme, "utf-8")

changelog_path = ROOT / "CHANGELOG.md"
changelog = changelog_path.read_text("utf-8")
changelog = changelog.replace(
    "- 移除重复的指令前缀与斜线指令配置，沿用 AstrBot 的指令体系。",
    "- 移除重复的指令前缀、指令扫描与唤醒判断层，完全沿用 AstrBot 的 `wake_prefix` 和 CommandFilter 流程。",
)
changelog_path.write_text(changelog, "utf-8")

test_path = ROOT / "tests/test_whatsapp_config_policy.py"
test = test_path.read_text("utf-8")
needle = '''    def test_adapter_filters_stale_platform_keys(self) -> None:
'''
insert = '''    def test_astrbot_owns_command_wake_handling(self) -> None:
        adapter_source = (ROOT / "whatsapp_adapter.py").read_text("utf-8")
        main_source = (ROOT / "main.py").read_text("utf-8")
        runtime_keys = _top_level_dict_keys(adapter_source, "RUNTIME_DEFAULT_CONFIG")
        self.assertNotIn("command_prefix", runtime_keys)
        self.assertNotIn("register_commands", runtime_keys)
        for token in (
            "collect_registered_commands",
            "message_matches_command",
            "_refresh_registered_commands",
            "_registered_commands",
        ):
            self.assertNotIn(token, adapter_source)
            self.assertNotIn(token, main_source)

'''
if needle not in test:
    raise RuntimeError("test insertion point not found")
test = test.replace(needle, insert + needle, 1)
test_path.write_text(test, "utf-8")

config_path = ROOT / ".pre-commit-config.yaml"
config = config_path.read_text("utf-8")
hook = '''      - id: remove-duplicate-command-layer
        name: Remove duplicate command layer
        entry: python .github/scripts/remove_duplicate_command_layer.py
        language: system
        pass_filenames: false
'''
config_path.write_text(config.replace(hook, ""), "utf-8")
Path(__file__).unlink(missing_ok=True)
