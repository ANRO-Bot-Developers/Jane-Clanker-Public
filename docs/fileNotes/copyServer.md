# copyserver.py

[`runtime/prefix/copyserver.py`](../../runtime/prefix/copyserver.py) handles the `!copyserver` command.

This is private, risky tooling. It copies a saved server snapshot into the current guild by using the server-safety snapshot restore machinery.

The smaller state helper is:

- [`runtime/copyServerState.py`](../../runtime/copyServerState.py)

## What It Handles

- checks whether the user can run `!copyserver`
- warns when the command is run inside a guild that is already allowed for normal commands
- loads the source guild snapshot
- estimates what already matches in the target guild
- shows a two-step confirmation view
- creates a backup snapshot of the target guild before applying changes
- applies roles, categories, channels, and permission overwrites through the server-safety restore service
- stores resume state if Discord rate limits, timeouts, or batch limits pause the run
- leaves behind a tiny marker saying this really was a copyserver target
- exposes an auto-retry button for some pause cases

## Why It Is So Large

Most of the size is defensive workflow code.

The dangerous part is not the command token itself. The dangerous part is that restoring a snapshot may create, edit, or remove a lot of server structure. So this file spends a lot of code on preview text, confirmations, backup paths, resume estimates, and status messages.

## Main Pieces

### Preview helpers

The helper functions near the top estimate what will change before Jane does anything destructive.

They compare snapshot roles and channels against the live guild so the confirmation message can show:

- how much is already present
- where a resume would start
- how many roles or channels are still missing
- which cleanup work is expected

### `CopyServerConfirmView`

This view is the command's control panel.

It owns:

- the preview stage
- the final destructive confirmation
- the in-progress state
- paused status messages
- auto-retry state
- cleanup after success or failure

If a button label, confirmation message, or retry behavior is wrong, this class is the place to read.

### `handleCopyServer(...)`

This is the text-command entry point.

It checks the token, permissions, guild state, active-run guard, source-guild config, stored resume state, and latest snapshot before it creates the confirmation view.

## State Files

`runtime/copyServerState.py` stores small JSON records under `runtime/data/copyserver/`.

That state is intentionally separate from the main SQLite database because it is tied to a very specific runtime recovery workflow:

- which target guild was being copied
- which source snapshot was used
- which role Jane was resuming around
- whether a target backup already exists

The temporary resume record still disappears after a real success. A much smaller marker sticks around so `/snapshot-menu` remembers that this is a real copyserver test target. It does not quietly turn on every other destructive command.

## Things To Be Careful About

- Do not bypass the two-step confirmation flow.
- Do not remove the target backup step.
- Do not call snapshot restore helpers directly from new command code unless you understand the resume behavior.
- Keep messages explicit. This command is destructive as fuck.
- Be careful with thread channels. The command intentionally rejects thread usage.
- Resume state should be cleared on real success, not on a temporary pause.
- The little copyserver marker is supposed to survive success. Restore testing relies on it.

## Good Small Edits

- improve preview wording
- add one more resume/status field
- make a warning clearer
- adjust retry timing config
- add tests around pure estimate helpers
