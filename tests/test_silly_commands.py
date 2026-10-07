from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from silly import commands as sillyCommands

JANE_ID = 999


def _member(userId: int, name: str = "Bob", *, admin: bool = False, roles=()):
    return SimpleNamespace(
        id=userId,
        name=name,
        display_name=name,
        mention=f"<@{userId}>",
        bot=False,
        system=False,
        roles=list(roles),
        guild_permissions=SimpleNamespace(administrator=admin),
        edit=AsyncMock(),
    )


class ParseMentionCommandTests(unittest.TestCase):
    def test_plain_mention_prefix(self) -> None:
        self.assertEqual(
            sillyCommands.parseMentionCommand(f"<@{JANE_ID}> kill <@5>", JANE_ID),
            ("kill", "<@5>"),
        )

    def test_nickname_mention_and_bang_and_case(self) -> None:
        self.assertEqual(
            sillyCommands.parseMentionCommand(f"  <@!{JANE_ID}>  !SKIN bob smith ", JANE_ID),
            ("skin", "bob smith"),
        )

    def test_mention_not_at_start_is_not_a_command(self) -> None:
        self.assertEqual(
            sillyCommands.parseMentionCommand(f"hey <@{JANE_ID}> kill him", JANE_ID),
            ("", ""),
        )

    def test_other_users_mention_is_not_a_command(self) -> None:
        self.assertEqual(sillyCommands.parseMentionCommand("<@5> kill", JANE_ID), ("", ""))

    def test_empty_content(self) -> None:
        self.assertEqual(sillyCommands.parseMentionCommand("", JANE_ID), ("", ""))

    def test_strip_bot_mention_both_forms(self) -> None:
        self.assertEqual(sillyCommands.stripBotMention(f"<@{JANE_ID}> horse", JANE_ID), "horse")
        self.assertEqual(sillyCommands.stripBotMention(f"👀 <@!{JANE_ID}>", JANE_ID), "👀")


class ResolveTextTargetTests(unittest.IsolatedAsyncioTestCase):
    async def test_reply_target_skips_janes_own_mention(self) -> None:
        jane = _member(JANE_ID, "Jane")
        victim = _member(5, "Victim")
        guild = SimpleNamespace(get_member=lambda userId: {JANE_ID: jane, 5: victim}.get(userId))
        message = SimpleNamespace(
            guild=guild,
            mentions=[jane],
            reference=SimpleNamespace(resolved=None, message_id=0),
            channel=SimpleNamespace(),
        )
        message.reference.resolved = None
        with patch.object(sillyCommands, "_referencedAuthorId", AsyncMock(return_value=5)):
            target = await sillyCommands.resolveTextTarget(message, "", botUserId=JANE_ID)
        self.assertIs(target, victim)

    async def test_second_mention_is_target_when_no_argument_text(self) -> None:
        jane = _member(JANE_ID, "Jane")
        victim = _member(5, "Victim")
        guild = SimpleNamespace(get_member=lambda userId: {JANE_ID: jane, 5: victim}.get(userId))
        message = SimpleNamespace(guild=guild, mentions=[jane, victim], reference=None, channel=SimpleNamespace())
        target = await sillyCommands.resolveTextTarget(message, "", botUserId=JANE_ID)
        self.assertIs(target, victim)

    async def test_no_target_returns_none(self) -> None:
        jane = _member(JANE_ID, "Jane")
        guild = SimpleNamespace(get_member=lambda userId: jane if userId == JANE_ID else None)
        message = SimpleNamespace(guild=guild, mentions=[jane], reference=None, channel=SimpleNamespace())
        self.assertIsNone(await sillyCommands.resolveTextTarget(message, "", botUserId=JANE_ID))


class KillOutcomeTests(unittest.TestCase):
    def test_requires_mr_hr(self) -> None:
        with patch.object(sillyCommands.runtimePermissions, "hasMiddleHighRankRole", return_value=False):
            outcome = sillyCommands.buildKillOutcome(_member(1), _member(2))
        self.assertIsNone(outcome.embed)
        self.assertTrue(outcome.ephemeral)
        self.assertIn("MR/HR", outcome.text)

    def test_missing_target_gives_usage(self) -> None:
        with patch.object(sillyCommands.runtimePermissions, "hasMiddleHighRankRole", return_value=True):
            outcome = sillyCommands.buildKillOutcome(_member(1), None)
        self.assertIn("@Jane kill", outcome.text)
        self.assertTrue(outcome.ephemeral)

    def test_success_builds_embed_and_never_edits(self) -> None:
        actor, target = _member(1, "Actor"), _member(2, "Target")
        with patch.object(sillyCommands.runtimePermissions, "hasMiddleHighRankRole", return_value=True):
            outcome = sillyCommands.buildKillOutcome(actor, target)
        self.assertEqual(outcome.embed.title, "Execution Scheduled")
        self.assertIn("<@2>", outcome.embed.description)
        target.edit.assert_not_awaited()
        actor.edit.assert_not_awaited()


class SkinOutcomeTests(unittest.TestCase):
    def setUp(self) -> None:
        sillyCommands._skinCommandNextAllowedAtByUser.clear()

    def test_permission_denied(self) -> None:
        outcome = sillyCommands.buildSkinOutcome(_member(1), _member(2), hasSkinPermission=lambda _m: False)
        self.assertEqual(outcome.text, "You do not have permission to skin users.")
        self.assertTrue(outcome.ephemeral)

    def test_success_sends_embed_and_never_edits_nickname(self) -> None:
        actor, target = _member(1, "Actor", admin=True), _member(2, "Target")
        outcome = sillyCommands.buildSkinOutcome(actor, target, hasSkinPermission=lambda _m: True)
        self.assertEqual(outcome.embed.title, "Skinning has been completed")
        self.assertIn("<@2>", outcome.embed.description)
        target.edit.assert_not_awaited()
        actor.edit.assert_not_awaited()

    def test_same_target_can_be_skinned_twice(self) -> None:
        actor, target = _member(1, "Actor", admin=True), _member(2, "[BUM-SKINNED] Target")
        outcome = sillyCommands.buildSkinOutcome(actor, target, hasSkinPermission=lambda _m: True)
        self.assertIsNotNone(outcome.embed)

    def test_reverse_skin_only_replies(self) -> None:
        actor = _member(1, "Actor", admin=True)
        target = _member(sillyCommands._unknownUserId, "Unknown")
        outcome = sillyCommands.buildSkinOutcome(actor, target, hasSkinPermission=lambda _m: True)
        self.assertEqual(outcome.text, "Sorry, but no. Get skinned. heh.")
        self.assertIsNone(outcome.embed)
        actor.edit.assert_not_awaited()

    def test_refuses_bots_jane_and_mom(self) -> None:
        actor = _member(1, "Actor", admin=True)
        bot = _member(2, "Bot")
        bot.bot = True
        allow = lambda _m: True
        self.assertIn("bots", sillyCommands.buildSkinOutcome(actor, bot, hasSkinPermission=allow).text)
        self.assertIn("myself", sillyCommands.buildSkinOutcome(actor, _member(sillyCommands._janeUserId), hasSkinPermission=allow).text)
        self.assertIn("mom", sillyCommands.buildSkinOutcome(actor, _member(sillyCommands._momUserId), hasSkinPermission=allow).text)

    def test_cooldown_blocks_second_use_for_non_admin(self) -> None:
        actor, target = _member(1, "Actor"), _member(2, "Target")
        allow = lambda _m: True
        first = sillyCommands.buildSkinOutcome(actor, target, hasSkinPermission=allow)
        second = sillyCommands.buildSkinOutcome(actor, target, hasSkinPermission=allow)
        self.assertIsNotNone(first.embed)
        self.assertIn("cooldown", second.text)
        self.assertTrue(second.ephemeral)

    def test_missing_target_gives_usage(self) -> None:
        outcome = sillyCommands.buildSkinOutcome(_member(1, admin=True), None, hasSkinPermission=lambda _m: True)
        self.assertIn("@Jane skin", outcome.text)


class MentionHandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        memberPatch = patch.object(sillyCommands, "_isGuildMember", return_value=True)
        memberPatch.start()
        self.addCleanup(memberPatch.stop)

    async def test_non_member_author_is_ignored(self) -> None:
        message = SimpleNamespace(author=_member(1), guild=SimpleNamespace(), channel=SimpleNamespace(), mentions=[], reference=None)
        botClient = SimpleNamespace(user=SimpleNamespace(id=JANE_ID))
        with (
            patch.object(sillyCommands, "_isGuildMember", return_value=False),
            patch.object(sillyCommands, "resolveTextTarget", AsyncMock()) as resolveTarget,
            patch.object(sillyCommands, "sendKillEmbed", AsyncMock()) as sendKill,
            patch.object(sillyCommands, "sendSkinEmbed", AsyncMock()) as sendSkin,
            patch.object(sillyCommands, "_tryChannelSend", AsyncMock()) as sendText,
        ):
            await sillyCommands.handleKillMention(message, botClient, "<@2>")
            await sillyCommands.handleSkinMention(message, botClient, "<@2>", hasSkinPermission=lambda _m: True)
        resolveTarget.assert_not_awaited()
        sendKill.assert_not_awaited()
        sendSkin.assert_not_awaited()
        sendText.assert_not_awaited()

    async def test_kill_mention_sends_embed_through_webhook_helper(self) -> None:
        actor, target = _member(1, "Actor"), _member(2, "Target")
        message = SimpleNamespace(author=actor, guild=SimpleNamespace(), channel=SimpleNamespace(), mentions=[], reference=None)
        botClient = SimpleNamespace(user=SimpleNamespace(id=JANE_ID))
        with (
            patch.object(sillyCommands.runtimePermissions, "hasMiddleHighRankRole", return_value=True),
            patch.object(sillyCommands, "resolveTextTarget", AsyncMock(return_value=target)),
            patch.object(sillyCommands, "sendKillEmbed", AsyncMock(return_value=True)) as sendEmbed,
            patch.object(sillyCommands, "_tryChannelSend", AsyncMock(return_value=True)) as sendText,
        ):
            await sillyCommands.handleKillMention(message, botClient, "<@2>")
        sendEmbed.assert_awaited_once()
        sendText.assert_not_awaited()

    async def test_skin_mention_refusal_is_plain_text(self) -> None:
        actor = _member(1, "Actor")
        message = SimpleNamespace(author=actor, guild=SimpleNamespace(), channel=SimpleNamespace(), mentions=[], reference=None)
        botClient = SimpleNamespace(user=SimpleNamespace(id=JANE_ID))
        with (
            patch.object(sillyCommands, "resolveTextTarget", AsyncMock(return_value=_member(2))),
            patch.object(sillyCommands, "sendSkinEmbed", AsyncMock()) as sendEmbed,
            patch.object(sillyCommands, "_tryChannelSend", AsyncMock(return_value=True)) as sendText,
        ):
            await sillyCommands.handleSkinMention(message, botClient, "<@2>", hasSkinPermission=lambda _m: False)
        sendEmbed.assert_not_awaited()
        sendText.assert_awaited_once_with(message.channel, "You do not have permission to skin users.")


class SillyMentionGateTests(unittest.IsolatedAsyncioTestCase):
    def _message(self, content: str, *, authorId: int, mentionsJane: bool):
        jane = SimpleNamespace(id=JANE_ID)
        return SimpleNamespace(
            content=content,
            author=SimpleNamespace(id=authorId, bot=False, mention=f"<@{authorId}>"),
            mentions=[jane] if mentionsJane else [],
            channel=SimpleNamespace(),
        )

    async def _run(self, message):
        botClient = SimpleNamespace(user=SimpleNamespace(id=JANE_ID))
        with patch.object(sillyCommands, "_tryChannelSend", AsyncMock(return_value=True)) as send:
            await sillyCommands.maybeHandleSillyMentions(message, botClient)
        return send

    # The user-id sets are captured by reference in _directSillyResponses,
    # so tests add to them in place instead of patching the module attribute.
    def setUp(self) -> None:
        for userIds in (sillyCommands._horseUserIds, sillyCommands._eyesUserIds):
            userIds.add(7)
            self.addCleanup(userIds.discard, 7)

    async def test_horse_without_mention_is_ignored(self) -> None:
        send = await self._run(self._message("horse", authorId=7, mentionsJane=False))
        send.assert_not_awaited()

    async def test_horse_with_mention_matches_exactly(self) -> None:
        send = await self._run(self._message(f"<@{JANE_ID}> horse", authorId=7, mentionsJane=True))
        send.assert_awaited_once()
        self.assertEqual(send.await_args.args[1], sillyCommands._horseGifUrl)

    async def test_eyes_with_nickname_form_mention(self) -> None:
        send = await self._run(self._message(f"👀 <@!{JANE_ID}>", authorId=7, mentionsJane=True))
        send.assert_awaited_once()
        self.assertEqual(send.await_args.args[1], sillyCommands._eyesGifUrl)

    async def test_not_a_furry_requires_mention(self) -> None:
        furry = sillyCommands._furryUserId
        ignored = await self._run(self._message("i'm not a furry", authorId=furry, mentionsJane=False))
        ignored.assert_not_awaited()
        replied = await self._run(self._message(f"<@{JANE_ID}> i'm not a furry", authorId=furry, mentionsJane=True))
        replied.assert_awaited_once()

    def test_removed_handlers_are_gone(self) -> None:
        for name in ("maybeHandleSixtySevenSpam", "handleCasinoToggleCommand", "handleKillCommand", "handleSkinCommand"):
            self.assertFalse(hasattr(sillyCommands, name), name)


class SlashDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def _interaction(self):
        return SimpleNamespace(
            response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
            channel=SimpleNamespace(),
            client=SimpleNamespace(),
        )

    async def test_text_outcome_respects_ephemeral_flag(self) -> None:
        from silly import sillyCommandsCog

        interaction = self._interaction()
        sendEmbed = AsyncMock()
        await sillyCommandsCog._deliver(
            interaction,
            sillyCommands.SillyOutcome(text="nope", ephemeral=True),
            sendEmbed,
        )
        self.assertEqual(interaction.response.send_message.await_args.args[0], "nope")
        self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])
        sendEmbed.assert_not_awaited()

    async def test_embed_outcome_goes_through_webhook_sender(self) -> None:
        import discord
        from silly import sillyCommandsCog

        interaction = self._interaction()
        sendEmbed = AsyncMock(return_value=True)
        embed = discord.Embed(title="x")
        await sillyCommandsCog._deliver(interaction, sillyCommands.SillyOutcome(embed=embed), sendEmbed)
        sendEmbed.assert_awaited_once_with(interaction.channel, interaction.client, embed)
        interaction.followup.send.assert_awaited_once_with("Done.", ephemeral=True)


if __name__ == "__main__":
    unittest.main()
