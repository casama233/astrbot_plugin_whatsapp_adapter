"""WhatsApp 斜線指令收集與匹配（對齊 AstrBot CommandFilter）。"""

from __future__ import annotations

import re
from typing import Any

from astrbot import logger


def collect_registered_commands() -> list[str]:
    """收集已啟用插件的 CommandFilter 指令名（含別名）。"""
    commands: list[str] = []
    try:
        from astrbot.core.star.filter.command import CommandFilter
        from astrbot.core.star.filter.command_group import CommandGroupFilter
        from astrbot.core.star.star import star_handlers_registry, star_map
    except Exception as exc:
        logger.debug("WhatsApp command collection skipped: %s", exc)
        return commands

    for handler_md in star_handlers_registry:
        module = star_map.get(handler_md.handler_module_path)
        if not module or not module.activated or not handler_md.enabled:
            continue
        for event_filter in handler_md.event_filters:
            if isinstance(event_filter, CommandGroupFilter):
                continue
            if not isinstance(event_filter, CommandFilter):
                continue
            if event_filter.parent_command_names and event_filter.parent_command_names != [""]:
                continue
            names = [event_filter.command_name, *list(event_filter.alias or [])]
            for name in names:
                normalized = str(name or "").strip().lower()
                if normalized and normalized not in commands:
                    commands.append(normalized)
    return sorted(commands)


def normalize_command_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def message_matches_command(text: str, commands: list[str], prefix: str = "/") -> bool:
    """判斷入站文字是否為已註冊的 /指令。"""
    if not commands:
        return False
    message = normalize_command_text(text)
    if not message.startswith(prefix):
        return False
    body = message[len(prefix) :]
    token = body.split(" ", 1)[0].lower()
    if not token:
        return False
    return token in commands
