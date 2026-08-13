# Troubleshooting

## Login Page has no QR code

Check that Node.js 20+ and npm are available, production Node dependencies can be installed/read, the configured base Gateway port is not unexpectedly occupied, and AstrBot can reach WhatsApp Web directly or through your proxy. The Login Page runtime card reports missing dependencies.

If the session is explicitly invalid/logged out/QR-expired, use the Page retry/reset flow to create a fresh login session.

## Phone pairing code fails

Use an international number including country/region code. Pairing is available only for a session that is not already registered. Requests are serialized/cooldown-limited and sensitive values are not written to the Page event log.

## Connected but inbound messages are ignored

Check:

- DMs: `dm_policy` + `allow_from`
- groups: `group_policy` + `groups` + `group_allow_from`

Use E.164-style numbers where possible. `group_allow_from` falls back to `allow_from` when empty. Reaction-only inbound messages are intentionally ignored.

## Group chat does not trigger

The default `group_policy` is `disabled`. Enable `allowlist` or `open` explicitly and verify group JID, group sender allowlist, and AstrBot wake/command rules. Wake prefixes are controlled by AstrBot Core, not a separate WhatsApp-only command system.

## LID / number identity looks inconsistent

WhatsApp may deliver LID and PN identity information at different times. The Gateway persists discovered LID→PN mappings and restores them after restart. Until a mapping is known, the stable public projection is `lid-N`; once first exposed it remains stable instead of silently moving to another UMO.

## Images arrive separately instead of one album

Check `default_media_album_debounce_seconds`; `0` disables merging. The merge is intentionally conservative: text, replies, other media, large timestamp gaps, sender/chat changes, and some captioned group cases create boundaries.

## Streaming creates a new message instead of editing

This is an expected fallback when WhatsApp no longer allows editing, no editable message ID is available, rendered chunks cannot be safely reconciled, or an edit request fails. The adapter stops unsafe editing and delivers the remaining/final content without duplicating already visible text where possible.

## Proxy problems

```bash
HTTPS_PROXY=http://host.docker.internal:7897
NO_PROXY=localhost,127.0.0.1
```

Both WebSocket and media traffic evaluate proxy bypass rules. Only HTTP/HTTPS proxy URLs are supported. Use `debug` Gateway logging temporarily if needed, then return to `info`; credentials are redacted from proxy log metadata.

## Media send/download failure

Check source reachability, container path mapping, file size, disk permissions/space, and the Gateway log. Partial failed inbound downloads are cleaned up rather than intentionally retained.

## Update fails

The updater only accepts stable releases from this repository. It validates source URL, archive structure/path safety, plugin identity/version/AstrBot compatibility, dependencies, and Python syntax before swapping directories. If hot reload fails, the previous plugin directory is restored; `plugin_data` and WhatsApp auth credentials remain outside the replaced directory.

Check AstrBot logs for technical details. The localized Plugin Page intentionally shows stable user-facing states rather than embedding backend diagnostics into UI translations.

## Multiple accounts connect to the wrong session

Do not share auth directories or external Gateway endpoints. See [Multi-instance](multi-instance.md).
