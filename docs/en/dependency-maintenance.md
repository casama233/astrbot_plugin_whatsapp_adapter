# Dependency maintenance

Managed startup and staged updates use one bounded installer: `npm ci --omit=dev --no-audit --no-fund --ignore-scripts=false`, the Baileys patch, and core package imports must all pass before writing `node_modules/.astrbot-install.json`. The receipt records the complete manifest/lock/patch fingerprint, host and Node major/ABI/platform/architecture. It detects stale inputs and missing packages; it is not a file authenticity check.

The receipt survives a staged-directory swap, so the next startup can reuse the verified installation on the same host and Node identity. An update initiated by an older updater may still require a first-start reinstall. Release downloads and dependency preparation need network access. Manual `npm ci` removes the receipt.

The Login Page rechecks the configured Node and complete dependency tree on every refresh. It distinguishes verified dependencies, installation needed, missing tooling, and reinstallation blocked by a running managed Gateway. Only a verified tree is ready; having npm available means installation can be attempted. External Gateway mode does not require local Node/npm. Local requirements, Gateway health, and WhatsApp login remain separate signals.

Installation refuses to rebuild a directory used by another managed Gateway. Stop all affected WhatsApp instances before retrying. Staging uses its own directory and can prepare while the live tree remains in use. Ownership survives plugin hot reload within one AstrBot process; independent processes and manually started Gateways must use separate mutable dependency directories.

Cancellation and timeout terminate the child process tree before returning. Windows runs npm-cli.js through the configured Node executable.

Use Node.js 22/24 LTS. The minimum is 20.9.0; Node 20 is EOL and retained only for legacy CI coverage. See the [Node release schedule](https://nodejs.org/en/about/previous-releases). Ubuntu/Windows CI covers Node 20/22/24 and Python 3.11/3.12. `python scripts/verify-dependencies.py` checks real installation, patching, imports and receipt reuse without logging into WhatsApp.
