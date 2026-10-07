from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from runtime.messageRouting import HumanMessageRouter, MessageRoutingMessages

JANE_ID = 999


def _message(content: str = "hello", *, mentionsJane: bool = False, guild: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        content=content,
        author=SimpleNamespace(id=10, bot=False),
        guild=SimpleNamespace(id=20, name="Test Guild") if guild else None,
        channel=SimpleNamespace(send=AsyncMock()),
        mentions=[SimpleNamespace(id=JANE_ID)] if mentionsJane else [],
    )


def _buildRouter(
    *,
    command: tuple[str, str] = ("", ""),
    paused: bool = False,
    commandAllowed: bool = True,
    guildAllowed: bool = True,
    orgGate: tuple[bool, str] = (True, ""),
    secretsHandled: bool = False,
):
    textRouter = SimpleNamespace(
        handlePotatoGreeting=AsyncMock(return_value=False),
        handleJaneSecrets=AsyncMock(return_value=secretsHandled),
    )
    sillyCommands = SimpleNamespace(
        parseMentionCommand=MagicMock(return_value=command),
        handleKillMention=AsyncMock(),
        handleSkinMention=AsyncMock(),
        maybeHandleSillyMentions=AsyncMock(),
    )
    hasCohost = MagicMock(return_value=True)
    mirrorAttempt = AsyncMock()
    router = HumanMessageRouter(
        botClient=SimpleNamespace(user=SimpleNamespace(id=JANE_ID)),
        configModule=SimpleNamespace(),
        pauseController=SimpleNamespace(isPaused=MagicMock(return_value=paused)),
        orgFeatureGateModule=SimpleNamespace(isTokenEnabledForGuild=MagicMock(return_value=orgGate)),
        sillyCommandsModule=sillyCommands,
        textCommandRouterProvider=MagicMock(return_value=textRouter),
        hasCohostPermission=hasCohost,
        isCommandExecutionAllowed=MagicMock(return_value=commandAllowed),
        isGuildAllowedForCommands=MagicMock(return_value=guildAllowed),
        mirrorUnapprovedGuildCommandAttempt=mirrorAttempt,
        messages=MessageRoutingMessages(
            runtimePaused="paused",
            serverNotRecognized="unknown-server",
            organizationFeatureUnavailable="feature-disabled",
            temporaryLock="temporarily-locked",
        ),
    )
    return router, textRouter, sillyCommands, mirrorAttempt, hasCohost


class HumanMessageRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_greeting_and_secrets_run_for_every_message(self) -> None:
        router, textRouter, silly, _mirror, _cohost = _buildRouter()
        message = _message()
        await router.handle(message)
        textRouter.handlePotatoGreeting.assert_awaited_once_with(message)
        textRouter.handleJaneSecrets.assert_awaited_once_with(message)
        silly.maybeHandleSillyMentions.assert_not_awaited()

    async def test_secrets_short_circuit(self) -> None:
        router, _textRouter, silly, _mirror, _cohost = _buildRouter(secretsHandled=True)
        await router.handle(_message(mentionsJane=True))
        silly.parseMentionCommand.assert_not_called()
        silly.maybeHandleSillyMentions.assert_not_awaited()

    async def test_unmentioned_message_does_nothing_else(self) -> None:
        router, _textRouter, silly, _mirror, _cohost = _buildRouter(command=("kill", ""))
        message = _message("kill")
        await router.handle(message)
        silly.handleKillMention.assert_not_awaited()
        message.channel.send.assert_not_awaited()

    async def test_dm_with_kill_word_runs_nothing(self) -> None:
        router, _textRouter, silly, _mirror, _cohost = _buildRouter(command=("kill", ""))
        await router.handle(_message(mentionsJane=True, guild=False))
        silly.handleKillMention.assert_not_awaited()
        silly.maybeHandleSillyMentions.assert_not_awaited()

    async def test_dm_with_non_command_word_gets_silly_reply(self) -> None:
        router, _textRouter, silly, _mirror, _cohost = _buildRouter(command=("horse", ""))
        message = _message(mentionsJane=True, guild=False)
        await router.handle(message)
        silly.maybeHandleSillyMentions.assert_awaited_once_with(message, router.botClient)
        silly.handleKillMention.assert_not_awaited()

    async def test_kill_mention_dispatches(self) -> None:
        router, _textRouter, silly, _mirror, _cohost = _buildRouter(command=("kill", "<@5>"))
        message = _message(mentionsJane=True)
        await router.handle(message)
        silly.handleKillMention.assert_awaited_once_with(message, router.botClient, "<@5>")
        silly.maybeHandleSillyMentions.assert_not_awaited()

    async def test_skin_mention_dispatches_with_cohost_check(self) -> None:
        router, _textRouter, silly, _mirror, hasCohost = _buildRouter(command=("skin", "bob"))
        message = _message(mentionsJane=True)
        await router.handle(message)
        silly.handleSkinMention.assert_awaited_once_with(
            message, router.botClient, "bob", hasSkinPermission=hasCohost
        )

    async def test_other_mention_goes_to_silly_replies(self) -> None:
        router, _textRouter, silly, _mirror, _cohost = _buildRouter(command=("horse", ""))
        message = _message(mentionsJane=True)
        await router.handle(message)
        silly.maybeHandleSillyMentions.assert_awaited_once_with(message, router.botClient)
        silly.handleKillMention.assert_not_awaited()

    async def test_mid_sentence_kill_is_not_a_command(self) -> None:
        # parseMentionCommand returns ("", "") when Jane's mention is not first.
        router, _textRouter, silly, _mirror, _cohost = _buildRouter(command=("", ""))
        message = _message("hey <@999> kill him", mentionsJane=True)
        await router.handle(message)
        silly.handleKillMention.assert_not_awaited()
        silly.maybeHandleSillyMentions.assert_awaited_once()

    async def test_paused_replies_for_commands_and_silences_silly(self) -> None:
        router, _textRouter, silly, _mirror, _cohost = _buildRouter(command=("kill", ""), paused=True)
        message = _message(mentionsJane=True)
        await router.handle(message)
        message.channel.send.assert_awaited_once_with("paused")
        silly.handleKillMention.assert_not_awaited()

        router, _textRouter, silly, _mirror, _cohost = _buildRouter(command=("horse", ""), paused=True)
        message = _message(mentionsJane=True)
        await router.handle(message)
        silly.maybeHandleSillyMentions.assert_not_awaited()
        message.channel.send.assert_not_awaited()

    async def test_unapproved_guild_is_rejected_and_mirrored(self) -> None:
        router, _textRouter, silly, mirror, _cohost = _buildRouter(command=("skin", ""), guildAllowed=False)
        message = _message(mentionsJane=True)
        await router.handle(message)
        await router.stop()
        message.channel.send.assert_awaited_once_with("unknown-server")
        self.assertEqual(mirror.call_args.kwargs["commandName"], "skin")
        silly.handleSkinMention.assert_not_awaited()
        silly.handleKillMention.assert_not_awaited()

    async def test_unapproved_guild_wins_over_paused(self) -> None:
        router, _textRouter, silly, mirror, _cohost = _buildRouter(
            command=("kill", ""), guildAllowed=False, paused=True
        )
        message = _message(mentionsJane=True)
        await router.handle(message)
        await router.stop()
        message.channel.send.assert_awaited_once_with("unknown-server")
        mirror.assert_called_once()
        silly.handleKillMention.assert_not_awaited()

    async def test_temporary_lock_blocks_commands(self) -> None:
        router, _textRouter, silly, _mirror, _cohost = _buildRouter(command=("kill", ""), commandAllowed=False)
        message = _message(mentionsJane=True)
        await router.handle(message)
        message.channel.send.assert_awaited_once_with("temporarily-locked")
        silly.handleKillMention.assert_not_awaited()

    async def test_org_gate_blocks_commands(self) -> None:
        router, _textRouter, silly, _mirror, _cohost = _buildRouter(
            command=("kill", ""), orgGate=(False, "silly")
        )
        message = _message(mentionsJane=True)
        await router.handle(message)
        message.channel.send.assert_awaited_once_with("feature-disabled (`silly`)")
        silly.handleKillMention.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
