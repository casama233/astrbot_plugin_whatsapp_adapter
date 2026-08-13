# Development guide

## High-level structure

```text
main.py                         plugin registration, Page APIs, Updater v2, AI tools
whatsapp_adapter.py             Platform wrapper and runtime patches
_whatsapp_adapter_impl.py       main platform-adapter implementation
whatsapp_event.py               MessageEvent wrapper
_whatsapp_event_impl.py         normal and streaming delivery
whatsapp_client.py              Gateway HTTP client/process management
whatsapp_config_policy.py       configuration scopes and migration
whatsapp_identity.py            PN/LID identity helpers
whatsapp_multi_instance.py      port/auth/runtime ownership
gateway/                        Node.js Gateway and compatibility modules
pages/whatsapp-login/           localized login/status/Updater v2 Page
.astrbot-plugin/i18n/           plugin metadata/config/Page locales
tests/                          Python regression tests
scripts/*.test.mjs              Node/Page/script regression tests
```

Protocol compatibility fixes should stay focused and test-backed. Prefer a small compatibility module over scattering temporary Baileys workarounds across unrelated message paths.

## Configuration scopes

Before adding a field, decide whether it belongs to plugin-level Gateway connection settings, shared `default_*` behavior, or per-account platform-instance behavior. Do not reintroduce Gateway fields as ordinary platform-instance UI fields after they have moved to plugin scope.

## Multi-instance

`whatsapp_multi_instance.py` owns the default base port, secondary port allocation, auth-directory isolation, endpoint leases/ownership, bind-race recovery, and reload/terminate cleanup. Changes must not allow auth sharing, silent external-endpoint sharing, premature port release, or a secondary instance stealing the base port.

## Streaming

The streaming implementation must record partial real delivery immediately, avoid resending already-visible prefixes after edit failure, distinguish “sent but uneditable” from “not sent,” keep per-event streaming state isolated, and coordinate typing presence across concurrent replies.

## Plugin Page i18n

Supported locales are `zh-CN`, `zh-TW`, and `en-US`.

The Page uses the AstrBot Plugin Page bridge:

```js
await bridge.ready();
bridge.t("pages.whatsapp-login.some_key", "English fallback");
bridge.getLocale();
bridge.onContext((context) => { /* locale changed */ });
```

Static markup uses `data-i18n`, `data-i18n-title`, `data-i18n-placeholder`, and `data-i18n-aria-label`. Dynamic connection state, updater state, pairing messages, and the Page event log are translated in `app.js`. Time formatting follows the active locale through `Intl.DateTimeFormat`.

`tests/test_plugin_i18n_coverage.py` enforces plugin metadata coverage, `_conf_schema.json` description/hint coverage, option-label coverage, Login Page key coverage, no hardcoded CJK UI copy in Page source, and runtime locale hooks.

When adding or changing visible config/Page text, update all three locale resources in the same PR.

### Why backend logs are not runtime-localized

Python/Node backend logs are an operational diagnostic interface, not one browser user's UI. Stable technical wording is better for grep, issue search, aggregation, and multi-user deployments. User-facing Page state, Page event logs, confirmation prompts, and config text are localized instead.

If API errors later need stronger localization, prefer stable error codes that the Page maps to translated text rather than changing backend log strings based on browser locale.

## Updater v2 and confirmation contract

Current main uses a **release-pinned transaction v2** updater. The Login Page must preserve these properties:

- `update/check` resolves and pins a Release candidate identity/artifact digest;
- installation submits that same `release.candidateToken` plus `expectedVersion`;
- two-step confirmation uses `createTwoStepGate` from `two-step-action.js`, without iframe modal dialogs;
- transaction state is durable, so the Page resumes polling `update/status` after requests are interrupted by hot reload;
- the frontend's 30-minute polling limit must not mark a still-running backend transaction failed;
- `quiescing`, `health_checking`, and `rolling_back` remain visible transaction phases;
- backend health-check failure drives rollback.

`sandbox-confirm.js` is now only a harmless compatibility asset for cached old Page HTML. Do not restore the old `window.confirm` shim.

`scripts/plugin-page-sandbox-confirm.test.mjs` locks down the modal-free flow, absence of dynamic HTML sinks, `createTwoStepGate`, and exact candidate identity submission. Localization must layer on top of those tests, not replace them.

## Local checks

```bash
python scripts/release_contract.py validate-repo
python -m compileall -q .
python -m unittest discover -v tests
npm ci
node --test gateway/*.test.mjs scripts/*.test.mjs
```

CI also exercises supported Windows paths for Updater v2 behavior; updater, Page, or filesystem changes should not be judged only from one operating system.

## Release contract

Normal feature/fix PRs do not manually bump versions. Releases use a marker under `.release/`; the workflow synchronizes version sources, updates the changelog, runs tests, validates the archive, generates a checksum, and publishes the release.

See [../../RELEASING.md](../../RELEASING.md).

## Documentation maintenance

Chinese source documentation lives under `docs/` plus `README.md`. English documentation lives under `docs/en/` plus `README.en.md`. Behavioral facts should come from current code/config/tests, not copied historical README text. Keep one detailed source of truth per topic and use navigation links rather than duplicating long sections everywhere.
