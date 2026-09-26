# snapshotRestore.py

[`features/operations/serverSafety/snapshotRestore.py`](../../features/operations/serverSafety/snapshotRestore.py) is the glue layer for Jane's snapshot restore flow.

This is the file that turns:
- snapshot data
- a target guild
- a few flags

into:
- restored roles
- restored categories/channels
- optional member-role restore
- cleanup stats / failure stats / pause stats

## Main Functions

### `applySnapshotPayloadToGuild(...)`

This is the main restore pipeline.

At a high level, it does:

1. restore roles
2. optionally pause if the role batch stops early
3. clean up extras if asked
4. restore categories
5. restore channels
6. finalize ordering
7. optionally restore member roles
8. return a big result payload

That result payload is what the server-safety restore flows read to decide what Jane should say next. A pause, a partial mess, and a clean finish are three different things, so they get three different messages.

### `applySnapshotPathToGuild(...)`

This is the convenient wrapper when you already have a snapshot path on disk.

It reads the snapshot file, then hands off to `applySnapshotPayloadToGuild(...)`.

It can also sanity-check the source guild. `!copyserver` does that before handing a main-server snapshot to a test target, because trusting the filename would be a terrible recovery strategy.

## Files Around It

This file is not doing all the work itself.

It leans on:

- [`features/operations/serverSafety/restoreRoles.py`](../../features/operations/serverSafety/restoreRoles.py)
- [`features/operations/serverSafety/restoreChannels.py`](../../features/operations/serverSafety/restoreChannels.py)
- [`features/operations/serverSafety/restoreMembers.py`](../../features/operations/serverSafety/restoreMembers.py)
- [`features/operations/serverSafety/snapshotStore.py`](../../features/operations/serverSafety/snapshotStore.py)

So if something breaks in "restore," this file usually tells you which lower layer actually owns the problem.

## Things To Be Careful About

- Partial success is normal.
  Jane can restore a lot of a server and still come back with some failure stats. That is not always a total disaster.

- Discord rate limits matter a lot here.
  Especially on roles and channels.

- Stage topics belong to stage instances, not stage channels.
  Discord made those two separate things, so Jane has to restore them separately too.

- `cleanupExtras=True` is not a cute option.
  It means Jane is allowed to delete structure that is not in the snapshot.

## Good Small Edits Here

- improve one returned stat field
- improve one progress message
- make one partial failure easier to understand
- document one restore stage that still feels murky
