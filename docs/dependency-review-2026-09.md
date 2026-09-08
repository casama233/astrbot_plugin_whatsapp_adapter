# Dependency review — 2026-09-09

This release keeps the protocol stack stable and refreshes the supported sharp patch. Package versions were checked against the npm registry and the upstream release pages on 2026-09-08 UTC.

| Component | Decision and evidence |
| --- | --- |
| [Baileys 7.0.0-rc14](https://github.com/WhiskeySockets/Baileys/releases/tag/v7.0.0-rc14) | Still the current release and the exact protocol version used by this plugin. Retain the pin and the documented ephemeral metadata postinstall patch. |
| [sharp 0.35.4](https://github.com/lovell/sharp/releases/tag/v0.35.4) | Update the lock from 0.35.3 with its matching native packages/libvips 1.3.3. The lock diff changes only these 27 sharp/native entries. Fresh install, native media/dependency smoke tests and all 127 Node tests pass on local Node 22; the shared six-job CI checks Node 20/22/24 on Windows/Linux. |
| [pino 10.3.1](https://github.com/pinojs/pino/releases/tag/v10.3.1) | Evaluated in an isolated copy: all 127 Node tests pass on Linux/Node 22. Baileys rc14 still requires `pino ^9.6`, so the experiment installs both 9.14.0 and 10.3.1. Retain the existing 9.x range and locked 9.14.0 to avoid adding a second major logger without a required fix; fresh npm audit reports zero vulnerabilities. Reconsider with the next Baileys upgrade. |
| [AstrBot 4.24.2](https://github.com/AstrBotDevs/AstrBot/releases/tag/v4.24.2) / [4.28.0](https://github.com/AstrBotDevs/AstrBot/releases/tag/v4.28.0) | Real source imports and configuration stores, JSON request/response, initialization, saved migration, adapter reload, auth-directory stability and group UMO all pass. The minimum version needs the small Quart JSON boundary documented in the compatibility inventory. |
| aiohttp | Uses AstrBot's shared Python environment. The inspected local deployment provides 3.14.3. This plugin release does not replace shared host dependencies. |

Node 20.9.0 is the compatibility floor required by the sharp tree. Node 22 or 24 is recommended; Node 20 remains in regression coverage for existing deployments. Framework integration is reproducible with `python tests/integration/verify_astrbot.py --framework /path/to/AstrBot` in an environment with that framework's dependencies. It uses temporary configuration and never logs in to WhatsApp or sends a message.
