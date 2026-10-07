# Operations

This is the short practical doc for running Jane without remembering every operational detail.

## Useful Runtime Tools

- `/pause`
  Pause or unpause the bot, or pause a single command with a custom notice

### Pausing a single command

The `/pause` panel has a command picker below the global pause button.

1. Pick the command from the dropdown (paginated, 25 per page).
2. Set the scope with the **Scope** button: this server only, or every server.
3. **Pause Command** opens a modal for the notice users see when they run it.
   Leave it blank to use Jane's default notice.
4. **Resume Command** lifts the pause for the current scope. Resuming in guild
   scope while a global pause is active records an exemption instead, so that
   one server keeps the command while everyone else stays paused.

Command pauses live in the `command_pauses` table and survive restarts, so a
pause set for a maintenance window stays in place until someone lifts it.
Pausing a group (`/link-hub`) also pauses its subcommands (`/link-hub add`).
`/pause` can never be paused, so runtime control stays reachable.

## Logs

Main general log:

- `logs/general-errors.log`

That is the first place to look if Jane starts acting strange or a background task is blowing up.

The live terminal is a little friendlier: colors when the console supports them,
plain text when output is redirected, and a small Jane banner on startup. Set
`JANE_CONSOLE_COLOR=always` or `never` if the automatic choice gets it wrong.
The file log stays plain either way.

## Snapshots / Recovery

Server safety and snapshots live under:

- `backups/serverSnapshots`
- `backups/serverSnapshotsOffsite`

The practical recovery runbook is [Server Recovery](features/serverRecovery.md).

The `/quarantine` command and the snapshot restore button have been removed. `/snapshot-menu` can still create and preview snapshots, and `/ops` can still take database backups, but Jane has no code that applies a snapshot or restores a database backup any more.

## High-Risk Feature Runbooks

- [Sessions And BG Checks](features/sessions.md)
- [Best Of](features/bestOf.md)
- [Auto Git Update](autoGitUpdate.md)

## Common Operational Notes

### If Jane seems online but weird

Check:

- `logs/general-errors.log`

### If commands are missing

Check:

- whether command clear flags are set
- whether the bot synced commands on startup
- whether the bot is connected to the right guild

Jane does not create invites for unrecognized guilds by default. There is an
opt-in diagnostic for it, but normal installs should leave that off.

### If restart/update behavior is odd

Check:

- `JANE_ENABLE_AUTO_GIT_UPDATE`
- whether the host is supervisor-managed

Normal shutdown now waits for Jane's background work to stop before SQLite is
closed. If shutdown hangs, the runtime logs should show which cleanup step was
the problem.

### If a feature works locally but not on the server

Usually it is one of these:

- missing env var
- wrong server/channel/role ID in `config.py`
- path issue
- host-specific file not present

## Good Habits

- keep `.env` host-specific
- keep `config.py` in sync with the actual server
- avoid absolute machine paths
- don't treat the public export like production source
- test risky changes on the test server first when possible

## What Not To Do

- don't assume old backup JSON metadata paths are meaningful on a new machine
- don't let auto-update become "blindly trust every push forever"
