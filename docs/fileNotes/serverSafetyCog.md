# serverSafetyCog.py

[`cogs/operations/serverSafetyCog.py`](../../cogs/operations/serverSafetyCog.py) is the Discord-facing cog for server safety controls.

It owns the `/quarantine` and `/snapshot-menu` slash commands, persistent safety views, suspicious delete detection, quarantine entry/release, and snapshot restore UI.

## Load Path

This cog is private-extension loaded through:

- `plugins/private/serverSafetyExtension.py`
- `plugins/private/extensionList.py`

The underlying service functions live under:

- `features/operations/serverSafety/`

## Main Surfaces

- `/quarantine`
  Opens the quarantine control panel.

- `/snapshot-menu`
  Opens the snapshot create/preview/restore menu.

- `on_guild_channel_delete(...)`
  Records channel deletions and sends an incident alert after the configured threshold.

- `on_guild_role_delete(...)`
  Records role deletions and sends an incident alert after the configured threshold.

## Access And Safety Gates

Access is layered:

- Discord admin/manage-server permission
- `serverSafetyAllowedUserIds`
- destructive action gate for quarantine and restore actions
- feature config such as `serverSafetyQuarantineEnabled`

Snapshots and quarantine are intentionally separated into `_ensureSnapshotAccess(...)` and `_ensureQuarantineAccess(...)`.

## Quarantine State

The cog keeps short-lived in-memory state for:

- recent security events by guild
- quarantine locks
- cached quarantine state
- pending suspicious-activity incidents

Persistent quarantine state is saved through `features.operations.serverSafety.service`.

There are two quarantine scopes:

- member quarantine, which removes roles and may timeout the suspected member
- guild quarantine, which strips risky role permissions and locks writable channels

## Snapshot Flow

Snapshot menu actions delegate to the server-safety service:

- `describeGuildSnapshots(...)`
- `buildRestorePreview(...)`
- `createGuildSnapshot(...)`
- `applyGuildSnapshot(...)`

Restore is destructive, so it stays behind the destructive gate. The copyserver carve-out is intentionally tiny and only applies to snapshot restore.

Main-server restores get one more stop sign. Jane DMs the configured reviewer using the requester's main-server display name, then sits on her hands until that person clicks **Continue** and types `confirm restore`. Pending approvals only live in memory, so they expire normally and a restart safely kills them.

The bypass list skips that DM dance, but it does not skip the destructive gate or audit trail. Please keep that list boring and tiny.

The menu and reviewer DM share the preview formatter in `features/operations/serverSafety/preview.py`. Keep it small enough for Discord, but useful enough to answer the real question: what is Jane about to mess with?

The result formatter lives in `features/operations/serverSafety/restoreReport.py`. Paused and partial restores need to stay honest here. A Discord timeout should never come back saying that the restore was complete.

## Things To Be Careful About

- Quarantine is currently disabled in config. Treat re-enable work as high-risk operational work.
- Permission hierarchy matters. The bot cannot edit roles above its top role or channels it cannot manage.
- Delete detection no longer auto-starts quarantine; it sends an alert with a manual start option.
- The alert channel falls back through several candidates. Changing this can make incident alerts disappear.
- Keep audit logging around quarantine and snapshot restore actions.
