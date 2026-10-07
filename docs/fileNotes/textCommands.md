# textCommands.py

Jane runs without the Message Content intent, so text commands only exist where
Discord still delivers message text.

## What is left

- `!janesecrets` (and aliases): DM-only, head-developer only. Lives in
  [`runtime/prefix/secrets.py`](../../runtime/prefix/secrets.py).
- `@Jane kill <user>` and `@Jane skin <user>`: the message must start with
  Jane's mention. Same code as `/kill` and `/skin` in
  [`silly/commands.py`](../../silly/commands.py).
- Mention-gated silly replies in `silly/commands.py`.
- The daily greeting in `TextCommandRouter.handlePotatoGreeting`, which looks
  at author and channel only.

## Order

[`runtime/messageRouting.py`](../../runtime/messageRouting.py) owns the order:
greeting, DM secrets, then (only if Jane is mentioned in a server) kill/skin,
then silly replies.

## Adding a text trigger

It must be a DM or require an @Jane mention. Anything else will receive an
empty `message.content`. Prefer a slash command.
