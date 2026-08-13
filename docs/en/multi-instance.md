# Multi-instance / multiple accounts

The plugin supports multiple WhatsApp platform instances in one AstrBot process. Every instance needs a unique `id` and its own WhatsApp sign-in.

## Bundled Gateway mode

Keep plugin-level `auto_start_gateway=true`.

If the base `gateway_port` is `18789`:

- `id=whatsapp` keeps `18789`;
- secondary instances search from `18790` upward;
- a secondary instance never steals the base port even if it starts first;
- reconnects keep the runtime endpoint stable unless an actual bind race requires reallocation.

Authentication storage is isolated too:

- default instance → `whatsapp-auth`;
- secondary instance → e.g. `whatsapp-auth-whatsapp2`;
- custom plugin-level `auth_dir` stays as the default-instance path while secondary instances get safe sibling suffixes.

Never copy another account's auth directory to a new instance.

## Login UX

The current **WhatsApp Login** Plugin Page addresses the default/base Gateway. Secondary bundled Gateways can emit their QR code in AstrBot/Gateway logs. This is a UI limitation, not shared-session behavior: secondary runtimes still have isolated ports and auth directories.

## External Gateway mode

With `auto_start_gateway=false`, the plugin does not allocate bundled ports. To prevent two AstrBot platform instances from silently attaching to the same WhatsApp session, one external `host:port` can be owned by only one WhatsApp adapter runtime in the same process.

A valid multi-account external topology requires distinct endpoints, e.g.:

```text
account A → 127.0.0.1:19001
account B → 127.0.0.1:19002
```

The standard plugin WebUI currently exposes one plugin-level external endpoint. If you need several separately configured external Gateways, use separate AstrBot processes/containers (or another deployment layout supplying distinct endpoints) rather than sharing one session.

## Instance ID safety

Instance IDs participate in generated auth-directory names. Unsafe filename characters are normalized, and collision-resistant suffixing prevents path traversal or two distinct IDs collapsing to the same directory. Prefer simple unique IDs such as `whatsapp`, `whatsapp2`, `whatsapp-work`, or `whatsapp-hk`.

## Troubleshooting

If a secondary bundled instance receives a different port than expected, check whether another process already owns the preferred port. The adapter logs the actual allocation.

If an external endpoint ownership error appears, configure a distinct Gateway endpoint. Do not disable the protection; it exists to prevent cross-account session/message leakage.
