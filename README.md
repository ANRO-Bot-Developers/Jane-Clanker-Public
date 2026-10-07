# Jane

Jane is a Discord bot built for ANRO.

This is not a polished SaaS app or a library. It is a fairly large, somewhat practical bot that grew around real staff workflows, Discord moderation/admin tooling, recruitment, ORBAT work, BG checks, reminders, voice chats, and a pile of internal quality-of-life features.

The goal of these docs is not to document every function. The goal is to make the repo easier to understand, run, and split into public/private pieces without guesswork.

## Start Here

- [Windows Dev Setup](docs/newMachineSetup.md)
- [Architecture](docs/architecture.md)
- [Extending Jane](docs/extendingJane.md)
- [Deployment](docs/deployment.md)
- [Operations](docs/operations.md)
- [Auto Git Update](docs/autoGitUpdate.md)
- [Public / Private Split](docs/publicPrivateSplit.md)
- [Feature Map](docs/features/README.md)
- [New Dev Tasks](docs/newDevTasks.md)
- [File Notes](docs/fileNotes/README.md)

## Repo Layout

- `bot.py`
  Main entrypoint. Builds the runtime services, loads extensions, and starts the bot.
- `config.py`
  Main config file. Loads `.env`, keeps most server-specific IDs/settings, and exposes the runtime flags.
- `cogs/`
  Slash-command cogs, grouped by domain.
- `features/`
  The actual feature logic, grouped by domain.
- `runtime/`
  Cross-cutting runtime pieces like startup, maintenance, logging, retries, git update, metrics, and webhook helpers.
- `db/`
  SQLite layer and schema setup.
- `plugins/`
  Optional extension layers for the public/private split.
- `silly/`
  Legacy fun/oddball slice. Some of it is still useful, some of it is just old Jane history.
- `tools/`
  Repo utilities, including the public export script.

## Current Folder Grouping

- `cogs/community`
- `cogs/operations`
- `cogs/staff`
- `features/community`
- `features/operations`
- `features/staff`
- `silly/gambling*`
- `runtime/gamblingApi.py`

There are still a couple oddballs hanging around, like `cogs/applicationsCog.py`, but the structure is much saner than it used to be.

## Running Jane

Basic flow:

1. Create or activate a virtualenv.
2. Install dependencies.
3. Copy [`.env.example`](.env.example) to `.env`.
4. Fill in the required secrets/tokens. In the Discord Developer Portal, only the Server Members privileged intent is required; Jane does not use the Message Content intent.
5. Adjust `config.py` for any server-specific IDs or behavior.
6. Start Jane.

Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
.\.venv\Scripts\python.exe bot.py
```

Linux:

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
./.venv/bin/python bot.py
```

Keep paths in `.env` repo-relative where possible. Jane resolves runtime data from the repo instead of assuming the terminal started in a particular folder, which keeps local Windows work and the Linux host on speaking terms.

For the actual Linux service setup, see [Deployment](docs/deployment.md).

## Local Checks

If you are working on Jane locally, install the extra tools with:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

On Linux, the same command is:

```bash
./.venv/bin/python -m pip install -r requirements-dev.txt
```

Then you can run the local tests and basic code checks with:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check . --select E9,F63,F7,F82
```

Linux uses the matching venv paths:

```bash
./.venv/bin/python -m pytest -q
./.venv/bin/ruff check . --select E9,F63,F7,F82
```

The `tests/` folder stays with the project and is visible to Git so future maintainers inherit the same safety net.

## Public / Private Repo Split

Jane now has the beginnings of a proper split:

- core extensions load normally
- public plugins can be added under `plugins/public`
- private-only plugins can be added under `plugins/private`

The production bot should keep using the private repo.

If you want a public-safe copy, export one with:

```powershell
python tools\exportPublicRepo.py C:\path\to\jane-public --clean
```

`--clean` is safe to use against a cloned copy of the public repo. Jane preserves the target repo's `.git` directory and replaces the working tree around it.

That export path does a secret scan and a smoke test so the public copy is less likely to be broken or embarrassing.

## A Note From Potato

Jane grew far beyond what I originally planned for her. She's finnicky, great at giving migraines, and threw me into the hospital for a bit. But she matters to me. If you're taking care of her next, be patient with her, keep making her better, and if you ever feel even the SLIGHTEST amount of burnout; Just stop. Take care of yourself, yeah?

Take care of her for me.

- potato
