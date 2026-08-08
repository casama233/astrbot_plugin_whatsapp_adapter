"""Legacy command-prefix compatibility for pre-0.2.20 configurations."""

from __future__ import annotations

import re

from astrbot import logger


def collect_registered_commands() -> list[str]:
    commands: list[str] = []
    try:
        from astrbot.core.star.filter.command import CommandFilter
        from astrbot.core.star.filter.command_group import CommandGroupFilter
        from astrbot.core.star.star import star_map
        try:
            # AstrBot 4.27+
            from astrbot.core.star.star_handler import star_handlers_registry
        except ImportError:
            # Older AstrBot compatibility
            from astrbot.core.star.star import star_handlers_registry
    except Exception as exc:
        logger.debug("WhatsApp legacy command collection skipped: %s", exc)
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
            for name in [event_filter.command_name, *list(event_filter.alias or [])]:
                normalized = str(name or "").strip().lower()
                if normalized and normalized not in commands:
                    commands.append(normalized)
    return sorted(commands)


def message_matches_command(text: str, commands: list[str], prefix: str = "/") -> bool:
    if not commands or not prefix:
        return False
    message = re.sub(r"\s+", " ", str(text or "").strip())
    if not message.startswith(prefix):
        return False
    token = message[len(prefix):].split(" ", 1)[0].lower()
    return bool(token and token in commands)
