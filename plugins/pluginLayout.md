# Plugin Layout

Jane's extension loading is split into three layers:

- `core`
  Existing built-in cogs and legacy `silly` extensions that ship with the main repo.
- `plugins.public`
  Optional public-safe extensions that can exist in the public repo.
- `plugins.private`
  Optional private-only extensions that should exist only in private deployments.

The current startup flow loads:

1. built-in core extensions from `runtime/extensionLayout.py`
2. any extra extensions listed in `config.extraExtensionNames`
3. any optional modules listed in:
   - `plugins/public/extensionList.py`
   - `plugins/private/extensionList.py`

Private extensions are only loaded when `config.enablePrivateExtensions` is truthy.

Jane no longer ships a shared destructive-action gate. A private extension that adds a destructive action has to bring its own allowed-user check, allowed-guild check, and cooldown.

This keeps Jane's current layout stable while giving the repo a clear place to move public-safe and private-only extensions over time.
