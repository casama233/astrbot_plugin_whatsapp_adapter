# Security and privacy

## Trust boundary

This plugin terminates WhatsApp Web encryption locally through Baileys. After a message is decrypted and converted into an AstrBot event, downstream handling is governed by AstrBot configuration, enabled plugins, LLM providers, and tools. Do not assume WhatsApp end-to-end encryption protects content after it enters AstrBot.

## Gateway exposure

The default `127.0.0.1` binding is intentional. A Gateway started by this plugin receives a random per-process Bearer token, and every HTTP/SSE request must present the same token. The token is passed at runtime and is not stored in plugin configuration.

This is a defense-in-depth control for the internal transport, not a public-Internet identity system. Do not publish the Gateway directly to the Internet. If AstrBot and Gateway must be separated, use a trusted network, firewall rules, and the same `WA_GATEWAY_TOKEN` in both processes.

Event-stream clients are capped to reduce resource exhaustion from accidental or hostile subscriptions.

## Authentication credentials

`whatsapp-auth/` and suffixed multi-instance auth directories contain session credentials. Treat them like account secrets: never commit them, attach them to public issues, or copy one account's directory into another instance. Deleting active auth data requires signing in again.

## Pairing codes and phone numbers

The Login Page keeps the entered number and returned pairing code in transient UI state only. Sensitive pairing values are deliberately excluded from the Page event log and backend success logs. Pairing codes expire quickly and should not be shared.

## Inbound and outbound media

Decrypted inbound media may be stored under plugin data. Protect the AstrBot data directory with appropriate filesystem/container permissions and retention policies.

Outbound `/send/media` sources are restricted:

- `file://` URLs are rejected.
- Local files must be regular files below the Gateway temporary directory by default. Additional trusted roots may be configured with `WA_MEDIA_ALLOWED_ROOTS` using the operating system path separator.
- HTTP/HTTPS media is downloaded into a temporary file before Baileys sends it.
- Remote targets are DNS-resolved and rejected when any resolved address is loopback, private, link-local, multicast, reserved, or otherwise non-public.
- The connection is pinned to a validated address, and every redirect is resolved and checked again to reduce DNS rebinding and redirect-based SSRF.
- Remote downloads have timeout, redirect, and size limits. The default remote-media limit is 32 MiB and can be adjusted with `WA_OUTBOUND_MEDIA_MAX_MB` within the implementation hard cap.

Custom integrations that previously sent arbitrary absolute paths, localhost URLs, or private-network URLs must instead stage files in a trusted media root.

## Access control

Start with `dm_policy=allowlist` and `group_policy=disabled`. Expand `allow_from`, `groups`, and `group_allow_from` only as required. Wildcard `["*"]` significantly broadens exposure.

The Gateway now fails closed before it receives its first valid runtime configuration: inbound messages are dropped during that window rather than being passed through without an allowlist decision.

Rejected SSE events expose only minimal rejection metadata and no longer broadcast message text, phone numbers, or sender JIDs.

## LLM and tools

If AstrBot sends conversation content to a remote LLM, embedding provider, or external tool, that provider receives the content according to its own policy. Native WhatsApp AI tools in this plugin are restricted to the current conversation and do not accept an arbitrary destination JID.

## Proxy credentials

Proxy URLs may contain credentials. Gateway log metadata is designed not to print proxy user/password/path/query. Still protect environment variables and process/container configuration.

## External Gateway and multi-instance deployments

Managed Gateways receive their token automatically. An externally managed Gateway is not launched by the plugin, so the operator must configure the same non-empty `WA_GATEWAY_TOKEN` for the external Gateway and the AstrBot process.

Do not make two runtimes silently share one external Gateway endpoint: sharing a Gateway also shares one WhatsApp session and can cross account/session boundaries.

## Self-updater

The updater trusts stable GitHub Releases from this repository and validates the archive before replacement, including path safety, plugin identity/version compatibility, dependencies, and syntax. Installation uses a staged/atomic swap and attempts rollback when hot reload fails.

## Logs

Operational backend logs are intentionally stable technical diagnostics rather than runtime-localized text. This improves error searching and issue correlation.

The **Plugin Page event log** is user interface and follows the selected AstrBot locale; sensitive phone/pairing values are not logged there.
