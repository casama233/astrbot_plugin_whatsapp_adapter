# AstrBot WhatsApp Adapter

Connect AstrBot to WhatsApp Web through a local Baileys Gateway, with DMs, groups, media, streaming replies and multiple accounts.

[简体中文](README.md) · [繁體中文](README.zh-TW.md) · English · [Latest Release](https://github.com/casama233/astrbot_plugin_whatsapp_adapter/releases)

## Requirements

AstrBot **4.24.2+**. Use **Node.js 22/24 LTS** and npm; the minimum is **20.9.0**. Node 20 is EOL and retained for compatibility testing. CI covers Ubuntu / Windows, Python 3.11/3.12 and Node 20/22/24.

## Quick start

1. Install from the AstrBot plugin market and keep the default plugin connection settings.
2. Add a WhatsApp platform instance. Select `allowlist` for DMs, put your own international phone number in `allow_from`, and keep groups disabled for the first check.
3. Open the plugin's **WhatsApp Login** management Page. Scan from WhatsApp → **Linked devices**, or request a phone pairing code.
4. Once connected, enable the platform instance and send a test message.

The Page identifies the base Gateway and account affected by its controls. Advanced connection details show each active account's actual port. See [Multi-instance](docs/en/multi-instance.md) for other accounts' login flow.

Plugin connection settings and `default_*` behavior are shared; platform instances hold account access policies. Legacy settings migrate once. The Page's **Effective settings and diagnostics** panel shows active values and sources. Field definitions live in [Configuration](docs/en/configuration.md).

## Troubleshooting and updates

- Missing Node or pending dependencies: check Runtime on the Page and follow [Troubleshooting](docs/en/troubleshooting.md).
- Connected without replies: check that the instance is enabled, access lists allow the sender, and AstrBot wake conditions are met.
- When reporting a problem, use **Refresh and copy diagnostics**. The report contains actual versions, build origin, account endpoints and setting sources, with account identifiers and credentials hidden.
- The Page can install verified GitHub Releases and attempt rollback after a failed update. Keep an independent backup before deployment.

The default Gateway is `127.0.0.1:18789`. Protect its endpoint and the `whatsapp-auth/` credentials. This plugin uses the unofficial WhatsApp Web protocol, so protocol changes may affect connectivity. See [Security and privacy](docs/en/security.md).

## Topic guides

| Guide | Contents |
| --- | --- |
| [User guide](docs/en/index.md) | Installation and first connection |
| [Configuration](docs/en/configuration.md) | Fields, scopes, migration and proxy |
| [Messaging and streaming](docs/en/messaging.md) | UMO, wake, quotes, reactions and albums |
| [Multi-instance](docs/en/multi-instance.md) | Accounts, ports and auth isolation |
| [Development](docs/en/development.md) | Runtime entry points, tests and i18n |
| [Compatibility inventory](docs/compatibility-inventory.md) | External patches and removal criteria |
| [Releasing](RELEASING.md) | Candidate validation and recovery |

[Contributing](CONTRIBUTING.md) · [Changelog](CHANGELOG.md) · [MIT License](LICENSE)
