# Security and privacy

## Trust boundary

This plugin terminates WhatsApp Web encryption locally through Baileys. After decryption, downstream handling is governed by AstrBot configuration, enabled plugins, LLM providers, and tools. WhatsApp end-to-end encryption does not protect content after it enters these services.

## Gateway exposure

Keep the default `127.0.0.1` binding. Managed Gateways receive a random per-process Bearer token; every HTTP/SSE request must authenticate. The token is passed at runtime, not stored in plugin configuration. Event-stream clients are capped.

This is defense in depth for an internal transport, not a public-Internet identity system. Do not publish the Gateway directly. For separated containers/hosts, use a trusted network, firewalls, and the same non-empty `WA_GATEWAY_TOKEN` in both processes.

## Credentials

`whatsapp-auth/` and suffixed multi-instance directories contain account secrets. Never commit them, attach them to public issues, share them with another account, or store them with broad permissions. Treat backups as secrets. After suspected exposure, unlink the device in WhatsApp and sign in again.

QR values and phone pairing codes are short-lived credentials. The Login Page keeps pairing values in transient UI state, excluding them from its event log and backend success logs. External proxies, browser extensions, or collectors may still record them.

## Local media: AstrBot compatibility with credential protection

**A managed Gateway defaults to the AstrBot `data/` root plus its temporary directory, not only temp.** This preserves normal plugin output in `data/temp/`, `data/temp_images/`, and individual `plugin_data/` directories. Ordinary documents such as JSON reports are not blocked by extension.

Upstream examples checked on 2026-09-08: [Telegram](https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/platform/sources/telegram/tg_event.py) consumes component `convert_to_file_path()` / `get_file()` results; [Discord](https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/platform/sources/discord/discord_platform_event.py) uses `MediaResolver` / `get_file()`. Their local-source handling does not require every plugin to write into one temp directory. This plugin also has an HTTP Gateway boundary, so authentication and source checks remain necessary.

`WA_MEDIA_ALLOWED_ROOTS` is interpreted as follows:

| Setting | Managed Gateway | Standalone external Gateway |
| --- | --- | --- |
| Unset | Defaults to AstrBot `data/` | Does not automatically allow AstrBot `data/` |
| Non-empty | Preserves the operator's root list | Uses the supplied list |
| Explicit empty string | Temporary directory only | Temporary directory only |

Use the operating system path separator (`:` on Linux/macOS, `;` on Windows). The temporary directory is always allowed. An explicit list replaces the managed automatic `data/` root; the launcher does not silently append it again.

Local sources must be regular files, checked against canonical `realpath` roots. The following credential locations are denied even when also covered by an allowed root:

- The current effective `WA_AUTH_DIR`, or the default auth path when unset.
- `whatsapp-auth` and `whatsapp-auth-*` directories under this plugin's `WA_DATA_DIR`, including default secondary accounts.
- All nested sessions, active-session metadata, and key files in those directories.

Symlink aliases do not remove those restrictions. Raw Gateway `file://` media URLs remain rejected. Caller-owned local files are not deleted after sending; Gateway-created outbound download files are cleaned up.

**This is not a sandbox for AstrBot plugins or a detector for every secret.** Other plugin configuration, databases, credentials copied elsewhere, and other custom-account directories still require operator isolation. For a strict deployment, explicitly configure narrow output roots and never mix secrets with sendable files. Python plugins already execute with the host process's permissions.

## Remote and decrypted media

HTTP/HTTPS media is downloaded to a temporary file before Baileys sends it. Non-public IP ranges are rejected, connections are pinned to validated DNS addresses, and every redirect is checked again. Redirect, size, inactivity, and absolute deadline limits remain in force. The default remote outbound limit is 32 MiB, configurable with `WA_OUTBOUND_MEDIA_MAX_MB` within a hard cap.

Do not substitute localhost/private-network URLs to bypass root configuration. Local paths must exist in the Gateway container when services are separated.

Inbound media is already decrypted and may be stored under AstrBot temporary or plugin data directories. Apply filesystem/container permissions, cleanup, and retention policies; do not sync all plugin data to public storage.

## Access control, LLMs, and tools

Start with `dm_policy=allowlist` and `group_policy=disabled`, allowing only a test number. Expand `allow_from`, `groups`, and `group_allow_from` deliberately. Wildcard `["*"]` broadens exposure. These policies control forwarding to AstrBot, not what the WhatsApp account itself receives.

The Gateway drops inbound messages before its first valid configuration. Rejected SSE events contain minimal reason/message-ID/timestamp metadata, not message bodies, phone numbers, or sender JIDs.

LLM, embedding/RAG, external tool, and other plugin use depends on AstrBot configuration and those services' data policies. Native poll, contact, and event AI tools are limited to the current conversation, have no arbitrary target parameter, and are checked in Python and the Gateway.

## Multi-instance and proxy deployments

Do not make two runtimes silently share an external Gateway: they would share one WhatsApp session. Keep ownership-conflict checks and separate each account's port and auth directory. External Gateway tokens must be configured on both sides.

`HTTPS_PROXY`, `HTTP_PROXY`, and `NO_PROXY` are supported. Proxy metadata is redacted, but environment variables and container configuration may contain credentials and require protection.

## Self-updater

The updater trusts this repository's stable GitHub Releases. It verifies the candidate, official artifact digest, trusted HTTPS origin, archive paths/types/sizes, plugin identity/version and AstrBot compatibility, then stages dependencies and checks syntax before swapping directories and performing post-reload health checks. Rollback paths are retained on failure.

Auth under `plugin_data` must remain untouched. Python requirements changes cannot be applied through the built-in updater; use AstrBot's plugin manager and restart rather than mutating its shared Python environment. Repository/supply-chain trust and platform power-loss risks remain. Keep separate code and plugin-data backups; see [releasing](../../RELEASING.md).

## Logs and unofficial protocol risk

Before sharing diagnostics, remove phone numbers, identifying JIDs, QR/pairing values, auth contents, tokens/cookies, proxy credentials, API keys, and unapproved message bodies/media URLs. Never attach the complete auth directory. Backend diagnostics remain technical text; Plugin Page UI follows the selected locale.

This is unofficial Baileys/WhatsApp Web, not Meta's Business Cloud API. Protocol, linked-device, message-feature, and account-enforcement changes can affect operation. Assess suitability before using critical business or valuable accounts.
