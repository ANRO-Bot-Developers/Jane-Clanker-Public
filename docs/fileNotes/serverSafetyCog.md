# serverSafetyCog.py

[`cogs/operations/serverSafetyCog.py`](../../cogs/operations/serverSafetyCog.py) is the Discord-facing cog for server snapshots.

It owns the `/snapshot-menu` slash command. The `/quarantine` command, the suspicious delete detection that fed it, and the snapshot restore button used to live here and have been removed.

## Load Path

This cog is private-extension loaded through:

- `plugins/private/serverSafetyExtension.py`
- `plugins/private/extensionList.py`

The underlying service functions live under:

- `features/operations/serverSafety/`

## Main Surfaces

- `/snapshot-menu`
  Opens the snapshot create/preview menu.

## Access

Access is layered:

- Discord admin/manage-server permission
- `serverSafetyAllowedUserIds`

Both checks go through `_ensureSnapshotAccess(...)`.

## Snapshot Flow

Snapshot menu actions delegate to the server-safety service:

- `describeGuildSnapshots(...)`
- `buildRestorePreview(...)`
- `createGuildSnapshot(...)`

The restore engine (`applyGuildSnapshot(...)` and the `restore*.py` modules) and the quarantine state store have been deleted.

The menu uses the preview formatter in `features/operations/serverSafety/preview.py`. Keep it small enough for Discord, but useful enough to answer the real question: what differs from the snapshot?

## Things To Be Careful About

- Nothing in this cog changes roles, channels, or members any more. Putting restore or quarantine back means rebuilding it, and it needs its own gate.
- Keep audit logging around snapshot creation.
