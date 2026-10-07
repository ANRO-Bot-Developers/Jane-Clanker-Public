# Auto Git Update

This is the practical map for Jane's Git pull and restart behavior.

The updater exists so the production bot can pull safe code updates without trampling live runtime data. It is conservative by design, because "the bot updated itself into a hole" is not a hobby we need.

## Where It Lives

- `runtime/gitUpdate.py`
- `runtime/configMerge.py`
- `runtime/processControl.py`
- `runtime/pauseState.py`
- `cogs/operations/runtimeControlCog.py`
- `bot.py`

## Main Config

- `JANE_ENABLE_AUTO_GIT_UPDATE`
  Enables scheduled update checks. If this is not set to `1`, the background updater will not run.

- `autoGitUpdateRemote`
  Defaults to `origin`.

- `autoGitUpdateBranch`
  Empty means use the current branch.

- `autoGitUpdateCheckIntervalSec`
  Scheduled check interval.

- `autoGitUpdateInitialDelaySec`
  Startup delay before the updater begins checking.

- `autoGitUpdatePauseDrainSec`
  Pause drain time before pulling.

- `JANE_INSTALL_REQUIREMENTS_ON_UPDATE`
  Defaults to off unless enabled in the environment. When a pulled update changes `requirements.txt`, Jane runs pip with the same Python executable that is already running the bot before she requests the restart. Venvs get a normal install. An externally managed Linux Python gets `--break-system-packages` only when its marker says that is needed.

- `autoGitUpdateDependencyInstallTimeoutSec`
  Dependency install timeout in seconds. Defaults to 600.

- `autoGitUpdateGitCommandTimeoutSec`
  Timeout for each Git command in seconds. Defaults to 120.

- `autoGitUpdatePreservePaths`
  Extra runtime paths to preserve during pull. These extend the built-in defaults.

- `autoGitUpdateBackupDir`
  Where the pre-pull backup of preserved paths is written. Empty means a `.jane-git-update` folder beside the repo, on the same disk. A relative value is resolved against the repo's parent folder. The folder must be outside the repo, and it must be on a real disk: do not point it at `/tmp` or any other RAM-backed filesystem.

## Backup Location And Leftovers

Before pulling, Jane copies every preserved path into a fresh `jane-git-update-*` folder and removes it when the update finishes. Preserved data can run to gigabytes.

The backup used to go to the system temp directory. On a server where `/tmp` is tmpfs that means RAM: a large backup exhausted memory, the kernel killed Jane mid-update, the folder was never cleaned up, and every later start was killed again because the leftover folder still held the memory. The backup now goes beside the repo instead.

Two guards go with that:

- Jane measures the preserved paths first and refuses to start the backup if the target disk does not have room for it. The update fails with `Not enough free space` in the log and Jane keeps running on the current code.
- If the copy fails part-way, the partial folder is removed. Nothing has been pulled or deleted at that point.

A folder can still be left behind if Jane is killed outright during an update. At startup she logs a warning listing any `jane-git-update-*` folder she finds in the backup location or the system temp directory. She does not delete them herself, because after an interrupted restore that folder can be the only copy of a preserved file. Compare it against the live repo, copy back anything missing, then remove it.

## Always-Preserved Paths

These are always treated as local runtime state:

- `bot.db`
- `bot.db-shm`
- `bot.db-wal`
- `*.db`
- `*.db-shm`
- `*.db-wal`
- `divisions.json`
- `configData/divisions.json`

The default preserved folders are:

- `backups/serverSnapshots`
- `backups/serverSnapshotsOffsite`

Dirty files under preserved paths do not block the update. They are backed up before pull and restored afterward.

## Managed Merge Paths

`config.py` and the focused modules under `settings/` are special.

If local managed settings files differ from `HEAD`, Jane does the careful little dance before pulling:

1. Parse top-level assignments from `HEAD`.
2. Parse top-level assignments from the local file.
3. Record local assignment values that differ from `HEAD`.
4. Pull from Git.
5. Reapply those local values to the new file if the same assignment still exists.

This lets new upstream config fields arrive while keeping local server-specific values.

The merge is syntax-based, not magic. It only understands top-level assignments, so do not ask it to read your soul.

## What Blocks An Update

Jane skips pull if:

- auto-update is disabled
- Jane is already paused
- the local branch has commits ahead of remote
- local code/config files changed outside preserved paths and managed config files
- only preserved runtime paths changed upstream
- the repo cannot fetch, inspect, or pull cleanly
- the server cannot resolve/connect to GitHub

Dirty snapshot or database files should not block the whole pull.

## Scheduled Flow

1. Wait for `autoGitUpdateInitialDelaySec`.
2. Fetch the configured remote and branch.
3. Compare `HEAD` to `remote/branch`.
4. Inspect upstream changed paths and local dirty paths.
5. If safe, pause Jane.
6. Back up preserved paths.
7. Re-check dirty paths, then temporarily stash the whole safe dirty worktree without pathspecs.
8. Pull with `git pull --ff-only`.
9. Reapply managed settings local values when needed.
10. Restore preserved runtime paths.
11. If `requirements.txt` changed, run pip install against the updated requirements file.
12. Drop the temporary stash.
13. Restart Jane if code changed.

## Manual Restarts

The `/restart` slash command and the manual restart flow behind it have been removed. Restart Jane from the host. Only the scheduled updater pulls from GitHub.

## Failure Recovery

If the updater fails during apply:

- preserved paths are restored from the temp backup when possible
- merge-managed files are restored when possible
- the temporary stash is dropped only when safe
- Jane unpauses if the updater paused her
- errors are logged and may be written to the audit stream

If a log says a temporary stash was kept, inspect it before doing any destructive Git cleanup. That stash is probably Jane trying to hand you the dropped groceries.

## Troubleshooting

- Jane says local changes block the update.
  Run `git status --short` and look for files outside preserved paths and `config.py`.

- Jane keeps skipping because local commits exist.
  The server branch has commits not on GitHub. Decide whether to push, reset intentionally, or stop auto-pull.

- `config.py` lost a local value.
  Check whether the assignment was renamed or removed upstream. Missing names are logged.

- Pull works manually but not through Jane.
  Check branch config, Git availability, and whether Jane is paused.

- Jane's terminal shows `gitEnabled no`.
  Scheduled auto-update is off. Set `JANE_ENABLE_AUTO_GIT_UPDATE=1` on the host if you want background pulls.

- Jane's terminal shows `gitResult network-unavailable`.
  The host could not resolve/connect to GitHub. Check DNS/network on the server first; Jane will retry on the next scheduled interval.

- Jane's terminal shows an old `gitCheck` time.
  Either scheduled auto-update is disabled, the updater worker is stopped, or Jane has not reached `autoGitUpdateInitialDelaySec` yet.

- Stash fails because a snapshot path disappeared.
  That should not happen in the rebuilt updater. Jane no longer passes individual snapshot pathspecs to `git stash push`; she re-checks the worktree and stashes safe local changes in one command.

- Update happens but no restart occurs.
  Check whether upstream changed only preserved runtime paths.

## Safe Edit Rules

- Keep `.db` files always preserved.
- Keep snapshot folders preserved.
- Do not broaden auto-merge beyond top-level assignments without tests.
- Do not replace `--ff-only` with a merge pull.
- Treat any kept stash as important local data.
