<div align="center">

<img src="./logo.png" alt="AstrBot WhatsApp Adapter" width="168" />

# AstrBot WhatsApp Adapter

**Connect AstrBot to WhatsApp Web / Baileys through a local Gateway**<br>
QR login · DMs & groups · Rich media · Streaming replies · Multi-account · Management Page

[简体中文](README.md) · [繁體中文](README.zh-TW.md) · **English**

[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/casama233/astrbot_plugin_whatsapp_adapter?label=version&color=ff69b4)](https://github.com/casama233/astrbot_plugin_whatsapp_adapter/releases)
[![AstrBot](https://img.shields.io/badge/AstrBot-4.24.2%2B-orange.svg)](https://github.com/AstrBotDevs/AstrBot)
[![Node.js](https://img.shields.io/badge/Node.js-20%2B-brightgreen.svg)](https://nodejs.org/)
[![Python CI](https://img.shields.io/badge/Python_CI-3.11-blue.svg)](https://github.com/casama233/astrbot_plugin_whatsapp_adapter/actions/workflows/tests.yml)
[![Tests](https://github.com/casama233/astrbot_plugin_whatsapp_adapter/actions/workflows/tests.yml/badge.svg)](https://github.com/casama233/astrbot_plugin_whatsapp_adapter/actions/workflows/tests.yml)
[![GitHub Stars](https://img.shields.io/github/stars/casama233/astrbot_plugin_whatsapp_adapter?style=flat&logo=github)](https://github.com/casama233/astrbot_plugin_whatsapp_adapter/stargazers)
![Visitors](https://count.kjchmc.cn/get/@astrbot_plugin_whatsapp_adapter?theme=gelbooru)

</div>

> [!IMPORTANT]
> This project uses the **unofficial WhatsApp Web protocol stack / Baileys**. It is **not** Meta's official WhatsApp Business Cloud API. Protocol changes may temporarily break compatibility, so evaluate account and operational risk before production use.

## ✨ Highlights

| Capability | What it provides |
| --- | --- |
| 🔐 **QR / phone pairing** | Sign in the default account from the AstrBot Plugin Page, with phone-number pairing support |
| 💬 **DMs and groups** | Independent `allowlist` / `open` / `disabled` policies with group-member access control |
| 🖼️ **Rich media** | Images, audio, video, documents, stickers, locations, contacts, buttons, lists, polls and more |
| ⚡ **Streaming replies** | Send once and edit incrementally; safely fall back when WhatsApp can no longer edit |
| 🧩 **Reply / mention / identity** | AstrBot Reply/At compatibility plus PN/LID normalization |
| 🆔 **Stable public UMO IDs** | PN is numeric, unresolved LID uses `lid-N`, and transport JIDs stay out of public session IDs |
| 🧠 **AstrBot-aligned wake semantics** | Quoting the bot alone does not wake it; actual @mentions, mention-all, commands, and normal wake conditions do |
| 🖼️ **Image burst coalescing** | Merge short private image bursts while preserving captions, mentions and ordering where possible |
| 👥 **Multi-instance** | Bundled Gateways isolate ports and auth directories for multiple accounts |
| 🌐 **Proxy support** | `HTTPS_PROXY`, `HTTP_PROXY`, and `NO_PROXY` |
| 🔄 **Updater v2** | Release-pinned candidates, artifact verification, durable transactions, health checks and rollback |
| 🌍 **Three UI locales** | `zh-CN`, `zh-TW`, and `en-US`, with runtime locale updates on the management Page |

> [!NOTE]
> The current WhatsApp Login Page primarily manages the **default/base Gateway instance**. QR codes for secondary bundled instances are mainly exposed through AstrBot / Gateway logs.

## 🚀 Quick start

Prefer AstrBot's plugin market / Cloud flow. The host still needs **Node.js 20+** and npm.

<details>
<summary><strong>Manual installation</strong></summary>

```bash
cd AstrBot/data/plugins
git clone https://github.com/casama233/astrbot_plugin_whatsapp_adapter.git
cd astrbot_plugin_whatsapp_adapter
pip install -r requirements.txt
npm install --omit=dev
```

Restart AstrBot or reload the plugin afterwards.

</details>

A safe first-test platform configuration is:

```json
{
  "dm_policy": "allowlist",
  "allow_from": ["+85212345678"],
  "group_policy": "disabled"
}
```

> [!TIP]
> Start by allowing only your own test number. Verify login, media and streaming before widening access.

Then keep plugin-level `auto_start_gateway=true`, open the **WhatsApp Login** Page, and scan the QR code from WhatsApp → **Linked devices** or request a phone pairing code.

## 🧱 Architecture

```text
AstrBot
  │ Python Platform Adapter / HTTP + SSE
  ▼
Local WhatsApp Gateway (Node.js)
  │ Baileys / WhatsApp Web
  ▼
WhatsApp
```

The default Gateway listens on `127.0.0.1:18789`.

> [!WARNING]
> Do not expose the Gateway HTTP/SSE endpoint directly to the public Internet. Treat `whatsapp-auth/` as account credentials and never commit or share it.

## ⚙️ Configuration model

| Scope | Examples | Purpose |
| --- | --- | --- |
| Plugin-level Gateway | `gateway_host`, `gateway_port`, `auto_start_gateway`, `auth_dir` | Gateway process/connection and base auth path |
| Plugin-level messaging defaults | `default_typing_indicator`, `default_streaming_edit_throttle`, etc. | Shared defaults for WhatsApp instances |
| Platform instance | `dm_policy`, `groups`, `pre_ack_*`, `apply_ephemeral` | Per-account policy and behavior |

> [!IMPORTANT]
> `default_streaming_edit_throttle` currently defaults to **1.0 seconds**. The standard WebUI exposes one plugin-level external Gateway endpoint; multiple external-Gateway accounts are better isolated into separate AstrBot processes/containers.

See [Configuration reference](docs/en/configuration.md).

## 🆔 UMO and wake semantics

WhatsApp PN, LID, Hosted, device JIDs and `@g.us` remain transport metadata in `raw_message` / `target_jid`. Public IDs use a stable projection:

| Context | `session_id` |
| --- | --- |
| DM | confirmed PN as a numeric ID; unresolved LID as `lid-N` |
| Group, session isolation off | group-JID local part (numeric or legacy `number-number`) |
| Group, session isolation on | `userID_groupID` |

The first public projection is persisted, so later PN/LID resolution does not silently move the UMO. If one contact was previously exposed under two projections, a confirmed mapping performs one merge toward the earliest projection. Legacy PN/LID/group-JID sessions remain accepted for proactive-send compatibility.

> [!NOTE]
> Reply metadata, sender nickname and quoted message ID are preserved for downstream plugins, but **quoting a bot message does not impersonate an @mention**. Group replies are triggered only by real @bot, mention-all, commands, or other normal AstrBot wake conditions. Pre-ack reactions are also independent from wake state.

## 🌍 Languages and documentation

| Language | README | Guide |
| --- | --- | --- |
| 简体中文 | [README.md](README.md) | [docs/zh-CN.md](docs/zh-CN.md) |
| 繁體中文 | [README.zh-TW.md](README.zh-TW.md) | [docs/zh-TW.md](docs/zh-TW.md) |
| English | **This document** | [docs/en/index.md](docs/en/index.md) |

Topic guides:

- [Configuration](docs/en/configuration.md)
- [Messaging & streaming](docs/en/messaging.md)
- [Multi-instance](docs/en/multi-instance.md)
- [Troubleshooting](docs/en/troubleshooting.md)
- [Security & privacy](docs/en/security.md)
- [Development](docs/en/development.md)
- [Contributing](CONTRIBUTING.md)
- [Release process](RELEASING.md)
- [Changelog](CHANGELOG.md)

## ⚡ Messaging notes

- **Streaming**: edit no more frequently than the configured `1.0s` default and avoid duplicating already-delivered content on fallback.
- **Reactions**: outbound pre-ack/done reactions are supported; inbound reaction-only events are currently ignored.
- **Replies**: quoted metadata is preserved, but Reply alone does not count as a group wake signal.
- **Image bursts**: short private image bursts can be coalesced; text/replies/non-image messages form boundaries.
- **Concurrent streams**: per-event streaming state and coordinated typing presence prevent replies from interfering with one another.

See [Messaging & streaming](docs/en/messaging.md).

## 🔄 Updater v2

The management Page installs stable GitHub Releases using an exact pinned candidate and asset identity, verifies the formal artifact digest and archive, quiesces active runtimes, persists transaction state, reloads the plugin, performs a health gate, and retains a rollback path when validation fails.

> [!CAUTION]
> Self-update cannot remove every platform-level hard-power-loss window. Keep independent backups of the plugin directory and `plugin_data` for production deployments.

## 🛠️ Development & CI

CI runs on Ubuntu and Windows, using Python 3.11 for project tests and Node.js 20.

```bash
python scripts/release_contract.py validate-repo
python -m compileall -q .
python -m unittest discover -v tests
npm ci
node --test gateway/*.test.mjs scripts/*.test.mjs
```

Plugin Page i18n is regression-tested by `tests/test_plugin_i18n_coverage.py`; visible configuration or Page strings should update all three locale files in the same PR.

## 📄 License

[MIT License](LICENSE).
