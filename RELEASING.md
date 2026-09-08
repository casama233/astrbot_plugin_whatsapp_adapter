# Release process

This repository uses a marker-driven release workflow. Version numbers must not be edited manually in feature pull requests.

## Contract

A release is valid only when all of these remain true:

- `metadata.yaml`, `main.py`, `package.json`, and both root version fields in `package-lock.json` use the same stable `x.y.z` version.
- `metadata.yaml` keeps the AstrBot market identity `casama233/astrbot_plugin_whatsapp_adapter` and the canonical HTTPS repository URL.
- `astrbot_version` is a PEP-440-style AstrBot compatibility range without a `v` prefix.
- This package omits `support_platforms`. AstrBot defines that field as a list of already-known adapter keys used by plugins that consume those adapters; this project itself provides the WhatsApp adapter.
- The release ZIP has one `astrbot_plugin_whatsapp_adapter/` root, includes `metadata.yaml`, `main.py`, `package.json`, and `package-lock.json`, contains no development junk, and is at most 16 MiB for AstrBot marketplace compatibility.
- GitHub release tag `vX.Y.Z` resolves to the exact validated release commit.
- The named release ZIP is `astrbot_plugin_whatsapp_adapter-vX.Y.Z.zip`, which is the preferred asset consumed by the built-in self-updater.

`scripts/release_contract.py` enforces this contract in normal CI and again during release publication.

## Normal release

1. Merge feature/fix pull requests into `main`; do not bump versions in those PRs.
2. Choose the next stable SemVer version. It must be strictly greater than the current version.
3. Add exactly one marker under `.release/` named either `X.Y.Z.json` or `vX.Y.Z.json`.
4. Merge that marker to `main`. The `Release` workflow automatically performs the full release.

Example marker:

```json
{
  "version": "0.2.32",
  "previous_version": "0.2.31",
  "date": "2026-08-11",
  "commit_subject": "improve private media and streaming behavior",
  "notes": [
    "Merge short private image bursts while preserving per-image captions.",
    "Keep concurrent streaming replies isolated and coordinate typing presence."
  ]
}
```

The marker has a closed schema: misspelled or additional fields fail the release instead of being silently ignored.

## What the workflow does

Before changing `main`, the workflow:

1. validates the marker and current repository contract;
2. verifies `previous_version` exactly matches the current repository version;
3. rejects equal/downgrade/non-stable versions;
4. updates every version source and prepends the changelog section;
5. removes the marker from the release commit;
6. writes `.build-info.json` and creates a local release commit, preserved in a Git bundle;
7. validates that exact commit through the shared `validate-runtime.yml` workflow: Windows and Ubuntu, Node 20/22/24, Python tests, Node tests and real managed dependency installation;
8. restores the same validated commit by SHA and builds its ZIP;
9. validates the ZIP against AstrBot's 16 MiB limit, the self-updater requirements and the complete runtime build manifest;
10. generates a SHA-256 sidecar.

Only after all of those checks succeed does it push the release commit to `main` and create/update the GitHub Release.

Ordinary pull requests and release candidates call the same validation workflow. The release candidate and final ZIP/checksum are retained as Actions artifacts for seven days, including preflight runs. Downloaded bundles must resolve to the prepared commit SHA before any test or publication step proceeds.

The build identity records the marker/source commit and hashes of shipped runtime files. It deliberately does not claim to contain its own generated release commit SHA. The Page reports local source overlays as `modified_release` and reports missing provenance as `unknown`.

## Manual preflight

The `Release` workflow has a `workflow_dispatch` entry point. Select a branch that contains one release marker and run it with `mode=preflight`.

Preflight performs the same version update, tests, release commit creation, ZIP build, and archive validation inside the runner, but it does **not** push a commit, tag, or GitHub Release.

This is the recommended way to verify a release marker before merging it to `main`.

## Manual publish and recovery

`mode=publish` is accepted only when the workflow is run from `main`.

Normally it is unnecessary because a marker pushed to `main` publishes automatically. It exists as a recovery path. If a previous run already pushed the validated `Release vX.Y.Z: ...` commit but failed later while creating/uploading the GitHub Release, rerunning the workflow safely detects that release commit and resumes publication instead of attempting another version bump.

Recovery refuses to proceed when:

- `main` moved to an unrelated commit;
- the marker is stale;
- the target version is already present in a non-release commit;
- an existing `vX.Y.Z` tag points to a different commit.

## Published assets

A successful GitHub Release contains:

- `astrbot_plugin_whatsapp_adapter-vX.Y.Z.zip`
- `astrbot_plugin_whatsapp_adapter-vX.Y.Z.zip.sha256`

The ZIP is intentionally built with `git archive` and `.gitattributes` export exclusions so that `tests/`, `.github/`, `.release/`, caches, `node_modules/`, and release-only validation scripts are not shipped to AstrBot users.
