# AstrBot WhatsApp Adapter — English Guide

[README](../../README.en.md) · [Configuration](configuration.md) · [Messaging](messaging.md) · [Multi-instance](multi-instance.md) · [Troubleshooting](troubleshooting.md) · [Security](security.md) · [Development](development.md)

## What this plugin does

The adapter connects AstrBot to WhatsApp through a local Node.js Gateway built on Baileys:

```text
AstrBot Python adapter ← HTTP/SSE → local Node.js Gateway ← WhatsApp Web → WhatsApp
```

It supports QR-code and phone-number pairing, private/group access control, media, replies, mentions, streaming edits, presence, album debounce, multiple bundled accounts, and current-conversation native AI tools.

> This uses the unofficial WhatsApp Web protocol, not Meta's official Business Cloud API.

## Install

Requirements: AstrBot `>=4.24.2,<5`, Node.js 20+ and npm, plus network access to WhatsApp Web.

```bash
cd AstrBot/data/plugins
git clone https://github.com/casama233/astrbot_plugin_whatsapp_adapter.git
cd astrbot_plugin_whatsapp_adapter
pip install -r requirements.txt
npm install --omit=dev
```

AstrBot Cloud/market installation can manage Python dependencies, but Node.js and npm must still exist on the host. The Gateway verifies production Node dependencies before startup.

## Sign in

1. Enable the plugin.
2. Add a `whatsapp` platform instance.
3. Open the plugin's **WhatsApp Login** Page.
4. Scan the QR code from WhatsApp → **Linked devices**, or request a phone pairing code.
5. Enable the platform instance after the Gateway reports Connected.

Phone pairing accepts an international number with country/region code. The page does not store or log the entered phone number or one-time pairing code.

## Start safely

```json
{
  "dm_policy": "allowlist",
  "allow_from": ["+85212345678"],
  "group_policy": "disabled"
}
```

Expand access deliberately. `["*"]` means all matching users/groups and should not be used casually.

## Configuration scopes

Plugin-level Gateway connection fields and `default_*` messaging behavior are shared defaults. Platform-instance fields hold account-specific policy/behavior.

```text
built-in defaults < plugin defaults < explicit platform-instance behavior
```

Gateway connection settings are not normal per-instance WebUI fields in the current configuration model. Bundled multi-account runtimes allocate isolated ports and auth directories automatically.

See [Configuration](configuration.md).

## Login Page languages

The management Page follows AstrBot's current locale. `zh-CN`, `zh-TW`, and `en-US` cover connection/account/runtime status, QR and phone pairing, access-policy summaries, updater state/confirmation prompts, and the Page event log. Changing the AstrBot WebUI language re-renders the Page without a plugin restart.

## What to read next

- [Messaging](messaging.md) for streaming, replies, media, identity, and reactions.
- [Multi-instance](multi-instance.md) before adding a second account.
- [Troubleshooting](troubleshooting.md) for connection, proxy, media, and update failures.
- [Security](security.md) before exposing the service or using a personal WhatsApp account.
