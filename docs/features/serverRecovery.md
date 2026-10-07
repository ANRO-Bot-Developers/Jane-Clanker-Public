# Server Recovery

This is the "oh no, fix the server" map for Jane's snapshot-based recovery system.

The goal is not perfection. Discord will not let a bot perfectly rebuild every possible thing. The goal is to keep enough structure saved that the important parts can be rebuilt: roles, channels, permissions, and member role assignments. Jane saves and previews snapshots; she no longer applies them.

## Where It Lives

- `cogs/operations/serverSafetyCog.py`
- `features/operations/serverSafety/snapshotStore.py`
- `features/operations/serverSafety/preview.py`
- `runtime/maintenance.py`

## Snapshot Contents

A snapshot stores the boring-but-critical stuff:

- guild ID and name
- role names, permissions, colors, hoist state, mentionable state, managed/default flags, and position
- member IDs, display names, and non-managed role IDs
- category, text, forum, voice, and stage channel metadata
- channel permission overwrites for roles and members
- snapshot kind and label
- offsite mirror metadata when enabled

Snapshots intentionally skip channels filtered by the server-safety filters.

## Snapshot Storage

Local snapshots are stored under:

- `backups/serverSnapshots`

Offsite mirrors are stored under:

- `backups/serverSnapshotsOffsite`

The exact directories can be overridden with:

- `serverSafetySnapshotDir`
- `serverSafetyOffsiteSnapshotDir`

## Retention

Retention is intentionally small, because hoarding stale recovery files is how you end up confidently restoring the wrong disaster:

- `serverSafetyWeeklySnapshotKeepCount`
  Defaults to `2`.

- `serverSafetyManualSnapshotKeepCount`
  Defaults to `1`.

This is deliberate. The weekly flow keeps two snapshots so a bad weekly snapshot does not immediately erase the previous good one.

## Weekly Snapshots

Weekly snapshots run from `runtime/maintenance.py`.

Jane chooses target guilds in this order:

1. `serverSafetyWeeklySnapshotGuildIds`
2. `allowedCommandGuildIds`
3. every guild Jane is in

Weekly snapshots do not run while Jane is paused.

## Manual Snapshots

Use `/snapshot-menu`.

The menu is the human-friendly surface for:

- show known snapshots
- create a manual snapshot
- preview what a restore of the selected snapshot would change

Snapshot controls require:

- administrator or manage-server permission
- a user ID allowed by `serverSafetyAllowedUserIds`, if configured

The **Restore Selected** button and the reviewer DM approval step have been removed. The restore engine behind them has been deleted too, so Jane cannot apply a snapshot at all.

The preview is a little chunky, but it is not trying to write a novel. It names missing roles, categories, and channels, then gives useful counts for role settings, channel permissions/layout, category changes, and member roles. Jane also calls out extra live stuff and leaves it alone.

## Emergency Checklist

If the server is actively on fire, do this slowly and deliberately:

1. Pause Jane if other automation might make the situation worse.
2. Open `/snapshot-menu` in the damaged guild.
3. Select the newest known-good snapshot.
4. Use preview to see what differs from the snapshot.
5. Confirm the snapshot is for the same guild.
6. Fix the server by hand from the preview. Jane no longer has a restore button.
7. Check audit logs and `logs/general-errors.log`.
8. Create a new manual snapshot only after the server is stable again.

## Config Checklist

- `serverSafetySnapshotDir`
- `serverSafetyOffsiteSnapshotDir`
- `serverSafetyOffsiteSnapshotsEnabled`
- `serverSafetyWeeklySnapshotKeepCount`
- `serverSafetyManualSnapshotKeepCount`
- `serverSafetyWeeklySnapshotGuildIds`
- `serverSafetyAllowedUserIds`
- `serverSafetyIgnoredCategoryIds`
- `serverSafetyPreservedChannelIds`

## Quarantine Note

The `/quarantine` command, its panel, the Begin/End quarantine buttons, and the suspicious-delete alert that offered to start one have all been removed. The quarantine state store and the `serverSafetyQuarantine*`, `serverSafetyAlert*`, and `serverSafetyRestoreVerif*` settings are gone as well.

## Safe Edit Rules

- Keep audit logging around snapshot creation.
- Do not add destructive behavior back without a very obvious config gate.
- Do not reduce retention below two weekly snapshots.
- Keep offsite mirroring boring and predictable.
- Test snapshot and preview changes in a test guild before trusting them in production.
