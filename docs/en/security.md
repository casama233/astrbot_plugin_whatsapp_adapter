# Security and privacy

## Trust boundary

This plugin terminates WhatsApp Web encryption locally through Baileys. After a message is decrypted and converted into an AstrBot event, downstream handling is governed by AstrBot configuration, enabled plugins, LLM providers, and tools. Do not assume WhatsApp end-to-end encryption protects content after it enters AstrBot.

## Gateway exposure

The default `127.0.0.1` binding is intentional. The Gateway HTTP/SSE API is an internal plugin transport and does not implement a public-Internet authentication model. Do not publish it directly to the Internet. If AstrBot and Gateway must be separated, use a trusted network and additional access controls.

## Authentication credentials

`whatsapp-auth/` and suffixed multi-instance auth directories contain session credentials. Treat them like account secrets: never commit them, attach them to public issues, or copy one account's directory into another instance. Deleting active auth data requires signing in again.

## Pairing codes and phone numbers

The Login Page keeps the entered number and returned pairing code in transient UI state only. Sensitive pairing values are deliberately excluded from the Page event log and backend success logs. Pairing codes expire quickly and should not be shared.

## Inbound media

Decrypted inbound media may be stored under plugin data. Protect the AstrBot data directory with appropriate filesystem/container permissions and retention policies.

## Access control

Start with `dm_policy=allowlist` and `group_policy=disabled`. Expand `allow_from`, `groups`, and `group_allow_from` only as required. Wildcard `["*"]` significantly broadens exposure.

## LLM and tools

If AstrBot sends conversation content to a remote LLM, embedding provider, or external tool, that provider receives the content according to its own policy. Native WhatsApp AI tools in this plugin are restricted to the current conversation and do not accept an arbitrary destination JID.

## Proxy credentials

Proxy URLs may contain credentials. Gateway log metadata is designed not to print proxy user/password/path/query. Still protect environment variables and process/container configuration.

## Self-updater

The updater trusts stable GitHub Releases from this repository and validates the archive before replacement, including path safety, plugin identity/version compatibility, dependencies, and syntax. Installation uses a staged/atomic swap and attempts rollback when hot reload fails.

## Logs

Operational backend logs are intentionally stable technical diagnostics rather than runtime-localized text. This improves error searching and issue correlation.

The **Plugin Page event log** is user interface and follows the selected AstrBot locale; sensitive phone/pairing values are not logged there.
