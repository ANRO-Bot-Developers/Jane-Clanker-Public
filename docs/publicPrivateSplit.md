# Public / Private Split

Jane is moving toward a split where:

- the normal bot framework can live in a public repo
- the more sensitive or destructive pieces stay in a private repo

This is the current state of that plan.

## Current Shape

Core startup loads extensions through:

- `runtime/extensionLayout.py`
- `plugins/public/extensionList.py`
- `plugins/private/extensionList.py`

Private-capable service loading is centralized in:

- `runtime/privateServices.py`

That means the repo no longer has to pretend everything is either fully public or fully private.

## What Counts As Private

Right now, private-only pieces include things like:

- server safety
- runtime control
- DM-only runtime secret management
- private plugin wrappers

The private optional extensions currently listed are:

- `plugins.private.orbatExtension`
- `plugins.private.serverSafetyExtension`
- `plugins.private.runtimeControlExtension`
- `plugins.private.linkHubExtension`

Private wrappers are not the whole story. If a private wrapper imports a normal-looking cog or service, that underlying code also needs to be treated as private during export.

For example, Link Hub is loaded through `plugins.private.linkHubExtension`, but the real code lives in:

- `cogs/operations/linkHubCog.py`
- `features/operations/linkHub/`

Those paths should stay out of the public export with the wrapper.

## Hard Gates For Risky Stuff

Just having the file is not supposed to be enough.

Risky actions should also require:

- allowed user checks
- allowed guild checks
- cooldowns
- audit logging

That way a mistaken merge is still annoying, but not automatically catastrophic.

## Public Export

The public-safe export path is:

```powershell
python tools\exportPublicRepo.py C:\path\to\jane-public --clean
```

That target can be a normal folder or a cloned working copy of the public repo. `--clean` keeps the target repo's `.git` directory so you can export straight into the public clone and then commit from there.

That export currently:

- strips known private-only paths
- strips private runtime secret command hooks from public files
- strips application Google Form URLs from `configData/divisions.json`
- rewrites the private extension list to an empty scaffold
- sanitizes parts of `config.py`
- runs a secret scan
- runs a public smoke test

The smoke test currently does:

- `compileall`
- import smoke on core modules
- import smoke on exported extensions

## When Adding A Private Thing

The boring checklist is:

- put the optional loader in `plugins/private`
- keep the normal runtime guard in place, usually `JANE_ENABLE_PRIVATE_EXTENSIONS`
- add the real cog, service, tests, and helper files to the public exporter blocklist if they are private
- make sure the public export still compiles after the private pieces are stripped

Do not rely on "it is not loaded" as the only privacy boundary. If the code itself should not be public, export should remove it.

## Public Branches

Public branches can be useful for outside work, but they may not be cleanly based on the current private repo by the time they come back.

Before porting a public branch into private:

- compare the branch against the current private model
- avoid copying exported config churn or public-only cleanup
- keep private-only runtime and safety tooling from being deleted
- prefer small ports of the useful behavior over broad checkouts

## Important Rule

The production bot should keep using the private repo.

The public repo is for:

- sharing code
- learning from the structure
- outside contributions
- general visibility

It is not the source Jane should blindly pull production updates from.

## Before Publishing A Public Repo

- keep `.env` and credentials out of the public repo!!!!
