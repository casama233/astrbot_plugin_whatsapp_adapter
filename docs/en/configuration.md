# Configuration reference

This page documents the current configuration model. Avoid copying old examples that expose historical hidden fields.

## Precedence

```text
built-in runtime defaults < plugin-level defaults < explicit platform-instance behavior
```

Connection fields belong to plugin configuration. Account-specific access and message behavior belong to platform instances.

## Plugin-level Gateway settings

| Key | Default | Meaning |
|---|---:|---|
| `gateway_host` | `127.0.0.1` | Base listen host for the bundled Gateway |
| `gateway_port` | `18789` | Base HTTP/SSE port for the default instance |
| `auto_start_gateway` | `true` | Start the bundled Node.js Gateway |
| `node_executable` | `node` | Node.js executable/path |
| `auth_dir` | empty | Base WhatsApp authentication directory |
| `log_level` | `info` | `silent/fatal/error/warn/info/debug/trace` |

With bundled multi-instance mode, secondary accounts automatically get later available ports and separate suffixed auth directories.

If `auto_start_gateway=false`, the adapter treats the configured endpoint as external. One external `host:port` may not be silently shared by multiple runtimes in the same AstrBot process.

## Plugin-level messaging defaults

| Key | Default | Meaning |
|---|---:|---|
| `default_link_preview_single_url` | `true` | Preview only a plain message containing one URL |
| `default_typing_indicator` | `true` | Send composing presence while replying |
| `default_send_read_receipts` | `true` | Mark accepted inbound messages read |
| `default_mark_online` | `false` | Keep global `available`; when false, reply activity may still be briefly visible |
| `default_parse_inbound_formatting` | `true` | Convert WhatsApp formatting to Markdown |
| `default_media_album_debounce_seconds` | `2.5` | Merge short same-sender image bursts; `0` disables |
| `default_streaming_edit_throttle` | `1.0` | Minimum interval between streaming edits |

The streaming runtime clamps the edit interval to at least `0.1s`.

## Platform-instance settings

| Key | Default | Meaning |
|---|---:|---|
| `dm_policy` | `allowlist` | `allowlist/open/disabled` |
| `allow_from` | `[]` | Allowed private senders; `["*"]` allows all |
| `group_policy` | `disabled` | `allowlist/open/disabled` |
| `groups` | `[]` | Allowed group JIDs; `["*"]` allows all |
| `group_allow_from` | `[]` | Allowed senders inside groups; empty falls back to `allow_from` |
| `media_caption_mode` | `separate` | `separate` or `caption` |
| `ignore_self_messages` | `false` | Ignore messages sent by the bot account itself |
| `pre_ack_emoji` | `true` | Enable pre-response reaction |
| `pre_ack_emojis` | `👀` | Pre-response reaction |
| `pre_ack_private` | `true` | Pre-ack private messages |
| `pre_ack_public` | `mentions` | `always/mentions/never` in groups |
| `pre_ack_done_emoji` | `✅` | Completion reaction |
| `apply_ephemeral` | `false` | Apply the chat's disappearing-message timer to outbound messages |

`caption` applies to ordinary rich-media chains. Streaming media is flushed separately from streaming text.

## Access-control examples

```json
{
  "dm_policy": "allowlist",
  "allow_from": ["+85212345678"],
  "group_policy": "allowlist",
  "groups": ["120363000000000000@g.us"],
  "group_allow_from": ["+85212345678"]
}
```

## Proxy environment variables

The Gateway recognizes `HTTPS_PROXY` (preferred), `HTTP_PROXY`, lowercase equivalents, and `NO_PROXY` / `no_proxy`.

```bash
HTTPS_PROXY=http://host.docker.internal:7897
NO_PROXY=localhost,127.0.0.1
```

Only `http://` and `https://` proxy URLs are supported. Proxy credentials/path/query are not printed in Gateway logs.

## Fixed/internal behavior

Text/media protocol limits and AstrBot command matching are not intended as user-configurable plugin settings. Wake prefixes and `CommandFilter` behavior are owned by AstrBot Core. Historical fields may still be recognized for migration, but should not be used in new configurations.
