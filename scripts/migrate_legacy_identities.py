#!/usr/bin/env python3
"""Reconcile legacy WhatsApp LID/UMO projections in AstrBot data.

The migration is conservative and idempotent: it derives proven LID -> PN
aliases from the adapter's own mapping state, backs up every changed file, and
only rewrites known plugin JSON/SQLite fields.  Run without ``--apply`` for a
dry run.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


UMO_RE = re.compile(r"whatsapp:(FriendMessage|GroupMessage):([^\s\"']+)")


def load_aliases(data_dir: Path) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for path in data_dir.rglob("astrbot-lid-mappings-v1.json"):
        try:
            mappings = json.loads(path.read_text("utf-8-sig")).get("lidToPn", {})
        except (OSError, ValueError, AttributeError):
            continue
        for lid, pn in mappings.items():
            lid_id = str(lid).split("@", 1)[0].split(":", 1)[0]
            pn_id = str(pn).split("@", 1)[0].split(":", 1)[0]
            if lid_id.isdigit() and pn_id.isdigit():
                aliases[lid_id] = pn_id
                aliases[f"lid-{lid_id}"] = pn_id
                aliases[f"{lid_id}@lid"] = pn_id
    return aliases


def canonical_id(value: str, aliases: dict[str, str]) -> str:
    raw = str(value).strip()
    return aliases.get(raw, raw)


def canonical_text(value: str, aliases: dict[str, str]) -> str:
    def repl(match: re.Match[str]) -> str:
        kind, session = match.groups()
        if kind == "GroupMessage":
            session = session.removesuffix("@g.us")
        else:
            session = canonical_id(session, aliases)
        return f"whatsapp:{kind}:{session}"

    result = UMO_RE.sub(repl, value)
    return result


def canonical_memory_text(value: str, aliases: dict[str, str]) -> str:
    result = canonical_text(value, aliases)
    for legacy, public in sorted(aliases.items(), key=lambda item: -len(item[0])):
        if legacy.startswith("lid-") or legacy.endswith("@lid"):
            result = result.replace(legacy, public)
        elif legacy.isdigit():
            result = re.sub(rf"(?<!\d){re.escape(legacy)}(?!\d)", public, result)
    return result


def canonical_json(value: Any, aliases: dict[str, str]) -> Any:
    if isinstance(value, str):
        return canonical_text(value, aliases)
    if isinstance(value, list):
        return [canonical_json(item, aliases) for item in value]
    if isinstance(value, dict):
        # JSON object keys can be identifiers, secrets, or user-defined names.
        # Rewriting them generically can collide and silently discard a value.
        return {str(key): canonical_json(item, aliases) for key, item in value.items()}
    return value


def canonical_origin_map(value: Any, aliases: dict[str, str]) -> Any:
    """Project keys in unified_msg_origins without losing collisions."""

    if not isinstance(value, dict):
        return canonical_json(value, aliases)
    projected: dict[str, Any] = {}
    owners: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        candidate = canonical_text(key, aliases)
        candidate = aliases.get(candidate, candidate)
        item = canonical_json(raw_value, aliases)
        if candidate in projected and projected[candidate] != item:
            # Keep the legacy key when two source records disagree. This is
            # preferable to inventing precedence and destroying either value.
            previous_key = owners[candidate]
            if key == candidate and previous_key != candidate:
                projected[previous_key] = projected[candidate]
                owners[previous_key] = previous_key
            else:
                candidate = key
        projected[candidate] = item
        owners[candidate] = key
    return projected


def history_map(items: list[str]) -> dict[str, int]:
    result: dict[str, int] = defaultdict(int)
    for item in items or []:
        date, sep, count = str(item).partition(":")
        if sep and count.isdigit():
            result[date] += int(count)
    return dict(result)


def merge_users(users: list[dict[str, Any]], group_id: str, aliases: dict[str, str]) -> list[dict[str, Any]]:
    proven_public_ids = set(aliases.values())
    nickname_ids: dict[str, set[str]] = defaultdict(set)
    for user in users:
        uid = canonical_id(str(user.get("user_id", "")), aliases)
        if uid != group_id:
            nickname_ids[str(user.get("nickname", ""))].add(uid)
    canonical_nicknames: dict[str, set[str]] = defaultdict(set)
    for nickname, ids in nickname_ids.items():
        for uid in ids:
            if uid.endswith("0") and uid[:-1] in proven_public_ids and uid[:-1] in ids:
                canonical_nicknames[nickname].add(uid[:-1])
            else:
                canonical_nicknames[nickname].add(uid)

    merged: dict[str, dict[str, Any]] = {}
    for original in users:
        item = dict(original)
        uid = canonical_id(str(item.get("user_id", "")), aliases)
        # One historical adapter build appended a zero to projected PN IDs.
        # Repair that shape only in statistics, and only when a proven PN plus
        # the exact same nickname already exists. Never apply this heuristic to
        # configs, UMO values, cron jobs, or memory text.
        nickname_matches = canonical_nicknames.get(str(item.get("nickname", "")), set())
        if (
            uid.endswith("0")
            and uid[:-1] in proven_public_ids
            and uid[:-1] in nickname_matches
        ):
            uid = uid[:-1]
        if uid == group_id:
            matches = nickname_matches
            if len(matches) == 1:
                uid = next(iter(matches))
        item["user_id"] = uid
        current = merged.get(uid)
        if current is None:
            merged[uid] = item
            continue
        current["message_count"] = int(current.get("message_count", 0)) + int(item.get("message_count", 0))
        histories = history_map(current.get("history", []))
        for date, count in history_map(item.get("history", [])).items():
            histories[date] = histories.get(date, 0) + count
        current["history"] = [f"{date}:{histories[date]}" for date in sorted(histories)]
        current["first_message_time"] = min(
            value for value in (current.get("first_message_time"), item.get("first_message_time")) if value is not None
        )
        current["last_message_time"] = max(
            value for value in (current.get("last_message_time"), item.get("last_message_time")) if value is not None
        )
        current["last_date"] = max(str(current.get("last_date", "")), str(item.get("last_date", "")))
        current["sticker_count"] = int(current.get("sticker_count", 0)) + int(item.get("sticker_count", 0))
        dates = dict(current.get("sticker_dates", {}))
        for date, count in dict(item.get("sticker_dates", {})).items():
            dates[date] = int(dates.get(date, 0)) + int(count)
        if dates:
            current["sticker_dates"] = dates
        if item.get("last_message_time", 0) >= current.get("last_message_time", 0):
            current["nickname"] = item.get("nickname", current.get("nickname", ""))
    return list(merged.values())


class Migration:
    def __init__(self, data_dir: Path, apply: bool) -> None:
        self.data_dir = data_dir
        self.apply = apply
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.backup_dir = data_dir / "plugin_data" / "astrbot_plugin_whatsapp_adapter" / "identity-migration-backups" / stamp
        self.changed: list[Path] = []

    def write_json(self, path: Path, value: Any) -> None:
        old = path.read_text("utf-8-sig")
        new = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if json.loads(old) == value:
            return
        self.changed.append(path)
        if not self.apply:
            return
        target = self.backup_dir / path.relative_to(self.data_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        file_descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            original_mode = path.stat().st_mode & 0o777
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
                handle.write(new)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, original_mode)
            os.replace(temporary, path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def migrate_json(
        self,
        path: Path,
        aliases: dict[str, str],
        *,
        stats: bool = False,
        origin_map: bool = False,
    ) -> None:
        value = json.loads(path.read_text("utf-8-sig"))
        if stats:
            value["users"] = merge_users(value.get("users", []), str(value.get("group_id", "")), aliases)
        elif origin_map:
            value = canonical_origin_map(value, aliases)
        else:
            value = canonical_json(value, aliases)
        self.write_json(path, value)

    def migrate_cron(self, aliases: dict[str, str]) -> None:
        path = self.data_dir / "data_v4.db"
        if not path.exists():
            return
        connection = sqlite3.connect(path)
        try:
            rows = connection.execute("SELECT id, payload, description FROM cron_jobs").fetchall()
            updates = []
            for row_id, payload, description in rows:
                projected_payload = canonical_text(payload or "", aliases)
                projected_description = canonical_text(description or "", aliases)
                if (projected_payload, projected_description) != (payload or "", description or ""):
                    updates.append((projected_payload, projected_description, row_id))
            if not updates:
                return
            self.changed.append(path)
            if self.apply:
                target = self.backup_dir / path.name
                target.parent.mkdir(parents=True, exist_ok=True)
                backup = sqlite3.connect(target)
                connection.backup(backup)
                backup.close()
                connection.executemany("UPDATE cron_jobs SET payload=?, description=? WHERE id=?", updates)
                connection.commit()
        finally:
            connection.close()

    def migrate_rollpig(self, aliases: dict[str, str]) -> None:
        path = self.data_dir / "plugin_data" / "astrbot_plugin_rollpig_plus" / "rollpig.db"
        if not path.exists():
            return
        connection = sqlite3.connect(path)
        try:
            legacy_ids = {
                str(row[0])
                for row in connection.execute(
                    "SELECT raw_id FROM identities WHERE namespace='legacy' AND identity_type='user'"
                )
            }
            claims = [
                ("users", legacy_id, f"v2|whatsapp@whatsapp|user|{aliases[legacy_id]}")
                for legacy_id in sorted(legacy_ids & aliases.keys())
            ]
            existing = set(connection.execute(
                "SELECT claim_kind, legacy_id, namespaced_id FROM identity_claims"
            ))
            claims = [claim for claim in claims if claim not in existing]
            if not claims:
                return
            self.changed.append(path)
            if self.apply:
                target = self.backup_dir / path.relative_to(self.data_dir)
                target.parent.mkdir(parents=True, exist_ok=True)
                backup = sqlite3.connect(target)
                connection.backup(backup)
                backup.close()
                connection.executemany(
                    "INSERT INTO identity_claims(claim_kind, legacy_id, namespaced_id) VALUES (?, ?, ?) "
                    "ON CONFLICT(claim_kind, legacy_id) DO UPDATE SET namespaced_id=excluded.namespaced_id",
                    claims,
                )
                connection.commit()
        finally:
            connection.close()

    def migrate_angel_memory(self, aliases: dict[str, str]) -> None:
        path = self.data_dir / "plugin_data" / "astrbot_plugin_angel_memory" / "memory_center" / "index" / "simple_memory.db"
        if not path.exists():
            return
        connection = sqlite3.connect(path)
        try:
            tags = dict(connection.execute("SELECT name, id FROM global_tags"))
            tag_merges: list[tuple[int, str]] = []
            for legacy, public in aliases.items():
                legacy_id = tags.get(legacy)
                if legacy_id is None:
                    continue
                tag_merges.append((legacy_id, public))

            text_updates: list[tuple[str, str, str, float, str]] = []
            for memory_id, judgment, reasoning, scope, updated_at in connection.execute(
                "SELECT id, judgment, reasoning, memory_scope, updated_at FROM memory_records"
            ):
                projected = (
                    canonical_memory_text(judgment or "", aliases),
                    canonical_memory_text(reasoning or "", aliases),
                    canonical_memory_text(scope or "", aliases),
                )
                if projected != (judgment or "", reasoning or "", scope or ""):
                    text_updates.append((*projected, max(float(updated_at or 0), datetime.now(timezone.utc).timestamp()), memory_id))

            if not tag_merges and not text_updates:
                return
            self.changed.append(path)
            if not self.apply:
                return
            target = self.backup_dir / path.relative_to(self.data_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            backup = sqlite3.connect(target)
            connection.backup(backup)
            backup.close()
            for legacy_id, public in tag_merges:
                public_id = tags.get(public)
                if public_id is None:
                    cursor = connection.execute("INSERT INTO global_tags(name) VALUES (?)", (public,))
                    public_id = int(cursor.lastrowid)
                    tags[public] = public_id
                connection.execute(
                    "INSERT OR IGNORE INTO memory_tag_rel(memory_id, tag_id) "
                    "SELECT memory_id, ? FROM memory_tag_rel WHERE tag_id=?",
                    (public_id, legacy_id),
                )
                connection.execute("DELETE FROM memory_tag_rel WHERE tag_id=?", (legacy_id,))
                connection.execute("DELETE FROM global_tags WHERE id=?", (legacy_id,))
            connection.executemany(
                "UPDATE memory_records SET judgment=?, reasoning=?, memory_scope=?, updated_at=? WHERE id=?",
                text_updates,
            )
            connection.commit()
        finally:
            connection.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    data_dir = args.data_dir.resolve()
    aliases = load_aliases(data_dir)
    migration = Migration(data_dir, args.apply)

    stats_root = data_dir / "plugin_data" / "message_stats"
    for path in sorted((stats_root / "groups").glob("*.json")):
        migration.migrate_json(path, aliases, stats=True)
    umo_aliases = stats_root / "unified_msg_origins.json"
    if umo_aliases.exists():
        migration.migrate_json(umo_aliases, aliases, origin_map=True)

    selected = [
        data_dir / "cmd_config.json",
        data_dir / "plugin_data" / "astrbot_plugin_daily_ai_news" / "subscriptions.json",
        data_dir / "plugin_data" / "astrbot_plugin_rollpig_plus" / "pig_history.json",
    ]
    selected.extend(sorted((data_dir / "config").glob("*.json")))
    for path in selected:
        if path.exists():
            migration.migrate_json(path, aliases)
    migration.migrate_cron(aliases)
    migration.migrate_rollpig(aliases)
    migration.migrate_angel_memory(aliases)

    print(json.dumps({
        "mode": "apply" if args.apply else "dry-run",
        "proven_aliases": len(aliases) // 3,
        "changed_files": [str(path) for path in migration.changed],
        "backup_dir": str(migration.backup_dir) if migration.apply and migration.changed else None,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
