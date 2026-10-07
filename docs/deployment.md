# Deployment

This is the practical "how do we keep Jane alive on the server" doc.

The normal split is a Windows machine for development and a Linux server for production. Jane supports both; the examples below use Linux because that is where she is expected to live. For a fresh Windows dev machine, use [New Machine Setup](newMachineSetup.md).

## What Lives On The Host

- `.env`
  Secrets and runtime flags
- `config.py`
  Guild IDs, channel IDs, role IDs, and feature settings
- `bot.py`
  Jane's entry point
- `bot.db`
  Live SQLite state; back it up and keep it out of Git

Use `.env` for tokens, API keys, credential paths, and host-specific flags. Use `config.py` for the normal server layout and feature tuning. It is not a perfect split, but it is the one Jane currently understands.

## Linux Setup

Jane needs Git and Python 3.11 or newer. From the checked-out private repo:

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env`, check the server IDs in `config.py`, then give her a test start:

```bash
./.venv/bin/python bot.py
```

Run Jane as a normal service account that owns the repo and runtime files. She does not need root, and giving a Discord bot root would be a fairly exciting way to learn that lesson.

## systemd

A small unit is enough. Adjust the user and `/opt/jane-clanker` paths to match the server:

```ini
[Unit]
Description=Jane Clanker Discord bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=jane
Group=jane
WorkingDirectory=/opt/jane-clanker
Environment=PYTHONUNBUFFERED=1
Environment=JANE_SUPERVISOR_MANAGED=1
ExecStart=/opt/jane-clanker/.venv/bin/python /opt/jane-clanker/bot.py
Restart=on-failure
RestartSec=10
TimeoutStopSec=45

[Install]
WantedBy=multi-user.target
```

Save it as `/etc/systemd/system/jane.service`, then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now jane
sudo systemctl status jane
```

Logs are available through both Jane's normal log files and systemd:

```bash
journalctl -u jane -f
```

`JANE_SUPERVISOR_MANAGED=1` tells Jane to hand intentional restarts back to systemd. Unexpected failures also restart through `Restart=on-failure`, while a normal `systemctl stop jane` stays stopped. Jane handles Linux's termination signal as a clean shutdown request.

## Paths And Environment

Prefer repo-relative paths in `.env`:

```env
ORBAT_GOOGLE_CREDENTIALS_PATH=localOnly/credentials/jane-clanker-service-account.json
```

Avoid paths copied from a particular machine:

```env
ORBAT_GOOGLE_CREDENTIALS_PATH=C:\Users\someone\Desktop\jane-clanker.json
```

Jane anchors her database, logs, task stats, secrets, and other runtime data to the repo. She can start from a different working directory, although setting `WorkingDirectory` in the service still keeps tools and humans less confused.

By default Jane looks for `.env` beside `bot.py`. If the server keeps it elsewhere, set `JANE_ENV_PATH` in the service environment. A relative override is resolved from the repo root.

## Important Flags

The usual production values include:

- `DISCORD_BOT_TOKEN`
- `ROBLOX_OPEN_CLOUD_API_KEY`
- `ROBLOX_INVENTORY_API_KEY`
- `ROVER_API_KEY`
- `ORBAT_GOOGLE_CREDENTIALS_PATH`
- `MINECRAFT_RCON_TOKEN` when Minecraft status is enabled
- `JANE_ENABLE_PRIVATE_EXTENSIONS`
- `JANE_ENABLE_AUTO_GIT_UPDATE`
- `JANE_INSTALL_REQUIREMENTS_ON_UPDATE`

The optional Orientation API uses:

- `JANE_ORIENTATION_API_ENABLED`
- `JANE_ORIENTATION_API_HOST`
- `JANE_ORIENTATION_API_PORT`
- `JANE_ORIENTATION_API_TOKEN`

It stays local by default. If it needs to be public, put it behind a tunnel or reverse proxy instead of binding it casually to the internet. Older installs can still use `JANE_FLASK_API_TOKEN`, but new ones should use `JANE_ORIENTATION_API_TOKEN`.

## Updates

Jane can check Git, pull safe changes, sync `requirements.txt`, and restart herself. The full behavior lives in [Auto Git Update](autoGitUpdate.md).

Dependency updates use the exact Python executable already running Jane. A venv gets a normal pip install; an externally managed Linux Python gets the compatibility flag it requires. Production should still use a venv so Jane is not rearranging the server's system Python.

The updater protects live state including:

- `bot.db`, its WAL, and other SQLite files
- `configData/divisions.json`
- runtime data and snapshot folders under `backups/`

A bad push can still be a bad night, so keep automatic updates conservative until the server setup has had a proper shakeout.

## Health

Useful places to look:

- `logs/general-errors.log`
- `journalctl -u jane -f`
