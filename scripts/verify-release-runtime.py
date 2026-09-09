from __future__ import annotations

import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

PLUGIN_ROOT = "astrbot_plugin_whatsapp_adapter/"

# Files whose absence makes the published plugin fail at import, Gateway build,
# dependency install, or the stability/security lifecycle layer. Keep this gate
# independent from metadata/version validation so an otherwise well-formed ZIP
# cannot be published with a partial runtime tree.
REQUIRED_RUNTIME_FILES = frozenset(
    {
        "__init__.py",
        "_conf_schema.json",
        "main.py",
        "metadata.yaml",
        "requirements.txt",
        "package.json",
        "package-lock.json",
        "plugin_updater.py",
        "update_lifecycle.py",
        "gateway_security.py",
        "gateway_stability.py",
        "gateway_dependencies.py",
        "gateway_runtime.py",
        "whatsapp_adapter.py",
        "_whatsapp_adapter_impl.py",
        "album_caption_compat.py",
        "group_name_compat.py",
        "member_tag_compat.py",
        "whatsapp_multi_instance.py",
        "whatsapp_client.py",
        "whatsapp_commands.py",
        "whatsapp_components.py",
        "whatsapp_event.py",
        "_whatsapp_event_impl.py",
        "whatsapp_config_policy.py",
        "whatsapp_config_migration.py",
        "whatsapp_diagnostics.py",
        "whatsapp_web.py",
        "whatsapp_reaction_journal.py",
        "whatsapp_streaming_concurrency.py",
        "whatsapp_helpers.py",
        "_whatsapp_helpers_impl.py",
        "whatsapp_identity.py",
        "whatsapp_ai_tools.py",
        "whatsapp_chunking.py",
        "scripts/patch-baileys-ephemeral.mjs",
        "gateway/whatsapp-gateway.mjs",
        "gateway/whatsapp-gateway-impl.mjs",
        "gateway/security-runtime.mjs",
        "gateway/stability-runtime.mjs",
        "gateway/session-lifecycle.mjs",
        "gateway/allowlist-identity.mjs",
        "gateway/message-cache.mjs",
        "gateway/media-download-compat.mjs",
        "gateway/message-normalization.mjs",
        "gateway/identity-compat.mjs",
        "gateway/runtime-identity.mjs",
        "gateway/native-tools.mjs",
        "gateway/pairing-code-compat.mjs",
        "gateway/proxy-compat.mjs",
        "gateway/outbound-mention-names.mjs",
    }
)


def validate_release_runtime(archive_path: Path, *, require_build_info: bool = False) -> None:
    try:
        archive = zipfile.ZipFile(archive_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise RuntimeError("release artifact is not a readable ZIP") from exc

    with archive:
        names = {
            name.replace("\\", "/").rstrip("/")
            for name in archive.namelist()
            if name and not name.endswith("/")
        }
        if require_build_info:
            try:
                info = json.loads(archive.read(f"{PLUGIN_ROOT}.build-info.json"))
                package = json.loads(archive.read(f"{PLUGIN_ROOT}package.json"))
                if info['version'] != package['version'] or not re.fullmatch(r'[0-9a-f]{40}', info['sourceCommit']):
                    raise ValueError('invalid build identity')
                runtime_names = {name[len(PLUGIN_ROOT):] for name in names
                                 if name.startswith(PLUGIN_ROOT) and
                                 Path(name).suffix in {'.py', '.mjs', '.js', '.json', '.yaml', '.html', '.css', '.svg'}
                                 and name != f'{PLUGIN_ROOT}.build-info.json'}
                if set(info['files']) != runtime_names:
                    raise ValueError('build manifest does not cover the complete runtime')
                for name, expected in info['files'].items():
                    actual = hashlib.sha256(archive.read(f'{PLUGIN_ROOT}{name}').replace(b'\r\n', b'\n')).hexdigest()
                    if actual != expected:
                        raise ValueError(f'build manifest mismatch: {name}')
            except (KeyError, ValueError, TypeError) as exc:
                raise RuntimeError(f'release build identity invalid: {exc}') from exc

    missing = sorted(
        relative
        for relative in REQUIRED_RUNTIME_FILES
        if f"{PLUGIN_ROOT}{relative}" not in names
    )
    if missing:
        raise RuntimeError(
            "release ZIP is missing runtime-critical files: " + ", ".join(missing)
        )


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    require_build_info = '--require-build-info' in args
    if require_build_info:
        args.remove('--require-build-info')
    if len(args) != 1:
        print("usage: verify-release-runtime.py <release.zip> [--require-build-info]", file=sys.stderr)
        return 2
    try:
        validate_release_runtime(Path(args[0]), require_build_info=require_build_info)
    except (RuntimeError, OSError) as exc:
        print(f"release runtime contract failed: {exc}", file=sys.stderr)
        return 1
    print(f"release runtime contract passed: {len(REQUIRED_RUNTIME_FILES)} required files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
