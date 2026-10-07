from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from features.staff.sessions import bgAddQueue, bgSpreadsheetQueue, bgSpreadsheetRouting


class BgSpreadsheetRoutingTests(unittest.TestCase):
    def test_clean_orientation_host_name_strips_prefix_and_optional_suffix(self) -> None:
        self.assertEqual(
            bgSpreadsheetRouting._cleanOrientationHostName("[HOST] potater [LOA]"),
            "potater",
        )
        self.assertEqual(
            bgSpreadsheetRouting._cleanOrientationHostName("[HOST] potater"),
            "potater",
        )

    def test_orientation_spreadsheet_date_uses_sheet_title_when_available(self) -> None:
        result = bgSpreadsheetQueue.BgSpreadsheetResult(title="Orientation 2026-05-05")

        self.assertEqual(
            bgSpreadsheetRouting._orientationSpreadsheetDateText(result),
            "5/5/2026",
        )


class BgSpreadsheetRouteFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.guild = SimpleNamespace(id=1373417102115078215)
        self.bot = SimpleNamespace()
        self.channel = MagicMock(spec=discord.TextChannel)
        self.sheetResult = bgSpreadsheetQueue.BgSpreadsheetResult(
            spreadsheet_id="sheet-1",
            title="Orientation 2026-10-04",
            url="https://docs.google.com/spreadsheets/d/sheet-1/edit",
            rows=[
                bgSpreadsheetQueue.BgSpreadsheetRow(discord_id=111, roblox_user="one", inventory="Public"),
                bgSpreadsheetQueue.BgSpreadsheetRow(discord_id=222, roblox_user="two", inventory="Public"),
                bgSpreadsheetQueue.BgSpreadsheetRow(discord_id=999, roblox_user="manual", inventory="Public"),
            ],
        )

        self.createMock = AsyncMock(return_value=self.sheetResult)
        self.pendingMock = AsyncMock(return_value=[999, 111])
        self.consumedMock = AsyncMock(return_value=1)
        self.changeLogMock = AsyncMock()
        self.sendMock = AsyncMock(return_value=SimpleNamespace(id=1))
        self.forumMock = AsyncMock()

        patches = [
            patch.object(bgSpreadsheetQueue, "createSpreadsheetForAttendees", self.createMock),
            patch.object(bgSpreadsheetQueue, "sendBgSpreadsheetChangeLog", self.changeLogMock),
            patch.object(bgAddQueue, "pendingUserIds", self.pendingMock),
            patch.object(bgAddQueue, "markConsumed", self.consumedMock),
            patch.object(bgSpreadsheetRouting.interactionRuntime, "safeChannelSend", self.sendMock),
            patch.object(bgSpreadsheetRouting, "_postOrientationSpreadsheetForumEntries", self.forumMock),
            patch.object(bgSpreadsheetRouting, "_bgSpreadsheetChannelIds", lambda session: [555]),
            patch.dict(
                bgSpreadsheetRouting._deps,
                {"getCachedChannel": AsyncMock(return_value=self.channel)},
            ),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    async def test_external_request_creates_sheet_for_passers_and_manual_additions(self) -> None:
        result = await bgSpreadsheetRouting.routeExternalOrientationSpreadsheet(
            self.bot,
            self.guild,
            requestId="cert_1_1",
            hostId=42,
            channelId=777,
            messageId=888,
            passedUserIds=[111, 222, 111],
        )

        self.assertEqual(result.url, self.sheetResult.url)
        attendees = self.createMock.await_args.args[0]
        self.assertEqual([row["userId"] for row in attendees], [111, 222, 999])
        self.assertEqual(self.createMock.await_args.kwargs["titlePrefix"], "Orientation")
        self.assertEqual(self.createMock.await_args.kwargs["guildId"], 1373417102115078215)
        self.assertIs(self.createMock.await_args.kwargs["sourceGuild"], self.guild)

    async def test_external_request_consumes_manual_additions_without_a_jane_session(self) -> None:
        await bgSpreadsheetRouting.routeExternalOrientationSpreadsheet(
            self.bot,
            self.guild,
            requestId="cert_1_1",
            hostId=42,
            passedUserIds=[111, 222],
        )

        self.consumedMock.assert_awaited_once_with(
            guildId=1373417102115078215,
            userIds=[111, 999],
            sessionId=0,
            spreadsheetId="sheet-1",
        )

    async def test_external_request_posts_link_audit_log_and_forum_entries(self) -> None:
        result = await bgSpreadsheetRouting.routeExternalOrientationSpreadsheet(
            self.bot,
            self.guild,
            requestId="cert_1_1",
            hostId=42,
            channelId=777,
            messageId=888,
            passedUserIds=[111, 222],
        )

        self.assertEqual(result.expected_channel_ids, [555])
        self.assertEqual(result.posted_channel_ids, [555])
        self.assertEqual(
            self.sendMock.await_args.kwargs["content"],
            "Orientation BGC Spreadsheet created: https://docs.google.com/spreadsheets/d/sheet-1/edit",
        )

        logKwargs = self.changeLogMock.await_args.kwargs
        self.assertEqual(logKwargs["requestedBy"], "<@42>")
        self.assertEqual(
            logKwargs["requestMessageUrl"],
            "https://discord.com/channels/1373417102115078215/777/888",
        )
        self.assertIn("Session: John cert_1_1", logKwargs["details"])
        self.assertIn("Candidates: 2", logKwargs["details"])

        forumSession = self.forumMock.await_args.args[2]
        self.assertEqual(forumSession["hostId"], 42)

    async def test_external_request_with_no_valid_passers_creates_nothing(self) -> None:
        result = await bgSpreadsheetRouting.routeExternalOrientationSpreadsheet(
            self.bot,
            self.guild,
            requestId="cert_1_1",
            hostId=42,
            passedUserIds=[0, -5, "not-a-number"],
        )

        self.assertEqual(result.skipped_reason, "No passing attendees need a BGC spreadsheet.")
        self.createMock.assert_not_awaited()
        self.pendingMock.assert_not_awaited()

    async def test_external_request_leaves_manual_additions_pending_when_sheet_is_not_created(self) -> None:
        self.createMock.return_value = bgSpreadsheetQueue.BgSpreadsheetResult(
            skipped_reason="BGC spreadsheet template is not configured."
        )

        result = await bgSpreadsheetRouting.routeExternalOrientationSpreadsheet(
            self.bot,
            self.guild,
            requestId="cert_1_1",
            hostId=42,
            passedUserIds=[111],
        )

        self.assertEqual(result.url, "")
        self.consumedMock.assert_not_awaited()
        self.sendMock.assert_not_awaited()

    async def test_external_request_works_when_jane_is_not_in_the_source_guild(self) -> None:
        result = await bgSpreadsheetRouting.routeExternalOrientationSpreadsheet(
            self.bot,
            None,
            guildId=1373417102115078215,
            requestId="cert_1_1",
            hostId=42,
            hostName="[HOST] potater [LOA]",
            channelId=777,
            messageId=888,
            passedUserIds=[111, 222],
        )

        self.assertEqual(result.url, self.sheetResult.url)
        self.assertIsNone(self.createMock.await_args.kwargs["sourceGuild"])
        self.assertEqual(self.createMock.await_args.kwargs["guildId"], 1373417102115078215)
        self.pendingMock.assert_awaited_once_with(guildId=1373417102115078215)
        self.assertEqual(
            self.changeLogMock.await_args.kwargs["requestMessageUrl"],
            "https://discord.com/channels/1373417102115078215/777/888",
        )
        self.assertIsNone(self.forumMock.await_args.args[1])
        self.assertEqual(self.forumMock.await_args.args[2]["hostName"], "[HOST] potater [LOA]")

    async def test_external_request_without_any_guild_id_creates_nothing(self) -> None:
        result = await bgSpreadsheetRouting.routeExternalOrientationSpreadsheet(
            self.bot,
            None,
            requestId="cert_1_1",
            hostId=42,
            passedUserIds=[111],
        )

        self.assertEqual(result.skipped_reason, "Orientation guild was not provided.")
        self.createMock.assert_not_awaited()

    async def test_host_name_comes_from_the_request_when_jane_cannot_see_the_guild(self) -> None:
        name = await bgSpreadsheetRouting._orientationHostName(
            self.bot,
            None,
            {"hostId": 42, "hostName": "[HOST] potater [LOA]"},
        )

        self.assertEqual(name, "potater")

    async def test_host_name_prefers_the_guild_member_when_jane_can_see_them(self) -> None:
        guild = SimpleNamespace(
            id=1,
            get_member=lambda userId: SimpleNamespace(nick="[CO] realnick", display_name="x", name="y"),
        )

        name = await bgSpreadsheetRouting._orientationHostName(
            self.bot,
            guild,
            {"hostId": 42, "hostName": "stale name"},
        )

        self.assertEqual(name, "realnick")

    async def test_jane_session_routing_is_unchanged(self) -> None:
        session = {"sessionId": 7, "guildId": 1373417102115078215, "channelId": 777, "messageId": 888, "hostId": 42}
        attendees = [{"userId": 111, "examGrade": "PASS"}, {"userId": 222, "examGrade": "PASS"}]
        service = SimpleNamespace(
            getSession=AsyncMock(return_value=session),
            getAttendees=AsyncMock(return_value=attendees),
        )
        ensureBuckets = AsyncMock()

        with patch.dict(
            bgSpreadsheetRouting._deps,
            {
                "service": service,
                "bgCandidates": lambda rows: list(rows),
                "ensureBgReviewBuckets": ensureBuckets,
            },
        ):
            result = await bgSpreadsheetRouting.routeBgcSpreadsheet(self.bot, 7, self.guild)

        ensureBuckets.assert_awaited_once_with(self.bot, 7, self.guild)
        self.assertEqual(result.posted_channel_ids, [555])
        created = self.createMock.await_args.args[0]
        self.assertEqual([row["userId"] for row in created], [111, 222, 999])
        self.consumedMock.assert_awaited_once_with(
            guildId=1373417102115078215,
            userIds=[111, 999],
            sessionId=7,
            spreadsheetId="sheet-1",
        )
        self.assertIn("Session: 7", self.changeLogMock.await_args.kwargs["details"])


if __name__ == "__main__":
    unittest.main()
