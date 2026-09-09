# Update recovery and cancellation

The built-in updater verifies and prepares the release in a staging directory before stopping accounts. It does not modify the shared Python environment or replace WhatsApp authentication data.

## Before cutover

Download and dependency preparation remain cancellable. Threaded archive extraction must settle before the transaction can release its lock or dispose of its staging directory. A cancelled preparation never stops an account or switches the installed code.

## Once accounts start stopping

Quiescing, directory replacement, reload, health verification and any rollback run as one protected operation. A caller cancellation request is deferred until this operation has settled, including repeated cancellation requests. The transaction lock stays held throughout. A successfully completed update remains `completed`; cancellation does not overwrite its real outcome with a generic failure.

Recovery uses the account IDs captured before stopping (not a fresh active-registry lookup) and AstrBot's full `PlatformManager.reload` path. It verifies both the new run task and the Gateway HTTP health response. Accounts disabled or removed in the meantime are not restarted. A previously running management-page Gateway is recovered when necessary. Failure of one account does not skip recovery attempts for the others.

If new-version health fails, its running Gateways/tasks are stopped before restoring the backup. The restored version must pass runtime health too: successful plugin registration alone is insufficient. Failed recovery is reported as `recoveryError` or `rollbackError`; staging/remaining backup directories are retained for inspection, not silently discarded.

HTTP health is not a WhatsApp login or message-delivery test. Forced process termination, power loss, external processes modifying the same installation, and filesystem failures beyond the updater's rollback capabilities still require operator recovery. Keep separate code and authentication-data backups.

## Continuous evidence

The shared validation workflow runs the ordinary six OS/Node jobs plus real AstrBot 4.24.2 and 4.28.0 integration jobs. Release candidates use the same exact-commit checkout and must pass all jobs before publication. Integration covers native PlatformManager task ownership, quiescence and restart, configuration persistence, Web JSON compatibility and stable group sessions; only Gateway network operations are mocked. It never logs in to WhatsApp or sends a message.

Framework dependencies come from each checked-out release's declared requirements, so they are not an immutable transitive lock. The job retains the plugin/framework commit IDs, resolved environment and integration log for seven days to make the tested environment explicit. Update the framework matrix when the supported release range changes.
