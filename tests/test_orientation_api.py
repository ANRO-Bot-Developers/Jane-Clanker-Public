from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from quart import Quart

from API import EndPoints


class OrientationApiParsingTests(unittest.TestCase):
    def test_parse_clock_in_payload_rejects_bad_shapes(self) -> None:
        self.assertIsNone(EndPoints.parseClockInPayload(None))
        self.assertIsNone(EndPoints.parseClockInPayload({}))
        self.assertIsNone(
            EndPoints.parseClockInPayload(
                {
                    "sessionId": "not-a-number",
                    "sessionPassword": "secret",
                    "sessionUserId": 10,
                }
            )
        )
        self.assertIsNone(
            EndPoints.parseClockInPayload(
                {
                    "sessionId": 5,
                    "sessionPassword": 123,
                    "sessionUserId": 10,
                }
            )
        )

    def test_parse_clock_in_payload_accepts_positive_ids(self) -> None:
        self.assertEqual(
            EndPoints.parseClockInPayload(
                {
                    "sessionId": "5",
                    "sessionPassword": "secret",
                    "sessionUserId": 10,
                }
            ),
            (5, "secret", 10),
        )


class OrientationApiRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.app = Quart(__name__)
        EndPoints.registerRoutes(self.app)
        self.client = self.app.test_client()

    async def test_route_rejects_missing_token_before_reading_payload(self) -> None:
        EndPoints.configureApi(botClient=SimpleNamespace(), token="expected-token")

        response = await self.client.post("/enterOrientation", json={})

        self.assertEqual(response.status_code, 403)
        self.assertEqual((await response.get_json())["error"], "unauthorized")

    async def test_route_rejects_invalid_payload_without_crashing(self) -> None:
        EndPoints.configureApi(botClient=SimpleNamespace(), token="expected-token")

        response = await self.client.post(
            "/enterOrientation",
            headers={"X-API-TOKEN": "expected-token"},
            json={"sessionId": "bad"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual((await response.get_json())["error"], "invalid-request")

    async def test_successful_clock_in_schedules_message_refresh(self) -> None:
        botClient = SimpleNamespace()
        EndPoints.configureApi(botClient=botClient, token="expected-token")
        with (
            patch.object(
                EndPoints,
                "attemptClockIn",
                AsyncMock(return_value={"status": "ADDED", "attendeeCount": 4}),
            ) as clockInMock,
            patch.object(
                EndPoints,
                "requestSessionMessageUpdate",
                AsyncMock(),
            ) as updateMock,
        ):
            response = await self.client.post(
                "/enterOrientation",
                headers={"X-API-TOKEN": "expected-token"},
                json={
                    "sessionId": 7,
                    "sessionPassword": "secret",
                    "sessionUserId": 11,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue((await response.get_json())["ok"])
        clockInMock.assert_awaited_once_with(7, 11, "secret")
        updateMock.assert_awaited_once_with(bot=botClient, sessionId=7, delaySec=0.5)


def _sheetPayload(**overrides: object) -> dict:
    payload: dict = {
        "requestId": "cert_1_1",
        "guildId": "1373417102115078215",
        "channelId": "1481180136740225168",
        "messageId": "1500000000000000001",
        "hostId": "1399386519256563793",
        "passedUserIds": ["1427385970529009674", "1374142815109386331"],
    }
    payload.update(overrides)
    return payload


class BgcSpreadsheetPayloadTests(unittest.TestCase):
    def test_accepts_string_ids_without_losing_precision(self) -> None:
        parsed = EndPoints.parseBgcSpreadsheetPayload(_sheetPayload())

        self.assertEqual(
            parsed,
            {
                "requestId": "cert_1_1",
                "guildId": 1373417102115078215,
                "channelId": 1481180136740225168,
                "messageId": 1500000000000000001,
                "hostId": 1399386519256563793,
                "hostName": "",
                "passedUserIds": [1427385970529009674, 1374142815109386331],
            },
        )

    def test_accepts_integer_ids_and_optional_message_location(self) -> None:
        parsed = EndPoints.parseBgcSpreadsheetPayload(
            {
                "requestId": "  cert_2_2  ",
                "guildId": 10,
                "hostId": 20,
                "passedUserIds": [30],
            }
        )

        self.assertEqual(
            parsed,
            {
                "requestId": "cert_2_2",
                "guildId": 10,
                "channelId": 0,
                "messageId": 0,
                "hostId": 20,
                "hostName": "",
                "passedUserIds": [30],
            },
        )

    def test_host_name_is_trimmed_and_bounded(self) -> None:
        self.assertEqual(
            EndPoints.parseBgcSpreadsheetPayload(_sheetPayload(hostName="  [HOST] potater  "))["hostName"],
            "[HOST] potater",
        )
        self.assertEqual(
            len(EndPoints.parseBgcSpreadsheetPayload(_sheetPayload(hostName="x" * 500))["hostName"]),
            100,
        )
        self.assertEqual(EndPoints.parseBgcSpreadsheetPayload(_sheetPayload(hostName=123))["hostName"], "")

    def test_collapses_duplicate_passers_keeping_order(self) -> None:
        parsed = EndPoints.parseBgcSpreadsheetPayload(
            _sheetPayload(passedUserIds=["30", 40, "30", 40, "50"])
        )

        self.assertEqual(parsed["passedUserIds"], [30, 40, 50])

    def test_rejects_bad_shapes(self) -> None:
        self.assertIsNone(EndPoints.parseBgcSpreadsheetPayload(None))
        self.assertIsNone(EndPoints.parseBgcSpreadsheetPayload([]))
        self.assertIsNone(EndPoints.parseBgcSpreadsheetPayload({}))
        self.assertIsNone(EndPoints.parseBgcSpreadsheetPayload(_sheetPayload(requestId="")))
        self.assertIsNone(EndPoints.parseBgcSpreadsheetPayload(_sheetPayload(requestId=123)))
        self.assertIsNone(EndPoints.parseBgcSpreadsheetPayload(_sheetPayload(requestId="x" * 201)))
        self.assertIsNone(EndPoints.parseBgcSpreadsheetPayload(_sheetPayload(guildId="not-a-number")))
        self.assertIsNone(EndPoints.parseBgcSpreadsheetPayload(_sheetPayload(guildId=0)))
        self.assertIsNone(EndPoints.parseBgcSpreadsheetPayload(_sheetPayload(hostId=True)))
        self.assertIsNone(EndPoints.parseBgcSpreadsheetPayload(_sheetPayload(passedUserIds=[])))
        self.assertIsNone(EndPoints.parseBgcSpreadsheetPayload(_sheetPayload(passedUserIds="30")))
        self.assertIsNone(EndPoints.parseBgcSpreadsheetPayload(_sheetPayload(passedUserIds=["30", "bad"])))
        self.assertIsNone(EndPoints.parseBgcSpreadsheetPayload(_sheetPayload(passedUserIds=[1.5])))
        self.assertIsNone(
            EndPoints.parseBgcSpreadsheetPayload(_sheetPayload(passedUserIds=list(range(1, 252))))
        )


class BgcSpreadsheetRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.app = Quart(__name__)
        EndPoints.registerRoutes(self.app)
        self.client = self.app.test_client()
        self.guild = SimpleNamespace(id=1373417102115078215)
        self.bot = SimpleNamespace(
            get_guild=lambda guildId: self.guild if guildId == 1373417102115078215 else None
        )
        EndPoints.configureApi(botClient=self.bot, token="expected-token")
        EndPoints.resetBgcSpreadsheetRequests()
        self.routeMock = AsyncMock(return_value=SimpleNamespace(url="https://sheet", skipped_reason=""))
        patcher = patch.object(
            EndPoints.bgSpreadsheetRouting,
            "routeExternalOrientationSpreadsheet",
            self.routeMock,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    async def _post(self, payload: object, token: str | None = "expected-token"):
        headers = {"X-API-TOKEN": token} if token is not None else {}
        return await self.client.post("/orientation/bgc-spreadsheet", headers=headers, json=payload)

    async def _drainBackgroundTasks(self) -> None:
        for _ in range(5):
            await asyncio.sleep(0)

    async def test_rejects_missing_or_wrong_token(self) -> None:
        missing = await self._post(_sheetPayload(), token=None)
        wrong = await self._post(_sheetPayload(), token="nope")

        self.assertEqual(missing.status_code, 403)
        self.assertEqual(wrong.status_code, 403)
        self.assertEqual((await wrong.get_json())["error"], "unauthorized")
        await self._drainBackgroundTasks()
        self.routeMock.assert_not_awaited()

    async def test_rejects_invalid_payload(self) -> None:
        response = await self._post({"requestId": "cert_1_1"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual((await response.get_json())["error"], "invalid-request")

    async def test_accepts_a_guild_jane_is_not_a_member_of(self) -> None:
        response = await self._post(_sheetPayload(guildId="5", hostName="[HOST] potater"))

        self.assertEqual(response.status_code, 202)
        await self._drainBackgroundTasks()
        self.routeMock.assert_awaited_once_with(
            self.bot,
            None,
            guildId=5,
            requestId="cert_1_1",
            hostId=1399386519256563793,
            hostName="[HOST] potater",
            channelId=1481180136740225168,
            messageId=1500000000000000001,
            passedUserIds=[1427385970529009674, 1374142815109386331],
        )

    async def test_rejected_request_can_be_retried_with_the_same_id(self) -> None:
        EndPoints.configureApi(botClient=None, token="expected-token")
        rejected = await self._post(_sheetPayload())
        EndPoints.configureApi(botClient=self.bot, token="expected-token")
        retried = await self._post(_sheetPayload())

        self.assertEqual(rejected.status_code, 503)
        self.assertEqual(retried.status_code, 202)

    async def test_reports_unavailable_client(self) -> None:
        EndPoints.configureApi(botClient=None, token="expected-token")

        response = await self._post(_sheetPayload())

        self.assertEqual(response.status_code, 503)
        self.assertEqual((await response.get_json())["error"], "discord-client-unavailable")

    async def test_accepts_and_starts_routing_in_the_background(self) -> None:
        response = await self._post(_sheetPayload())

        self.assertEqual(response.status_code, 202)
        self.assertEqual(await response.get_json(), {"ok": True, "status": "accepted"})

        await self._drainBackgroundTasks()
        self.routeMock.assert_awaited_once_with(
            self.bot,
            self.guild,
            guildId=1373417102115078215,
            requestId="cert_1_1",
            hostId=1399386519256563793,
            hostName="",
            channelId=1481180136740225168,
            messageId=1500000000000000001,
            passedUserIds=[1427385970529009674, 1374142815109386331],
        )

    async def test_duplicate_request_does_not_create_a_second_sheet(self) -> None:
        first = await self._post(_sheetPayload())
        second = await self._post(_sheetPayload())

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(await second.get_json(), {"ok": True, "status": "duplicate"})

        await self._drainBackgroundTasks()
        self.assertEqual(self.routeMock.await_count, 1)

    async def test_routing_failure_is_logged_not_raised(self) -> None:
        self.routeMock.side_effect = RuntimeError("google is down")

        with self.assertLogs(EndPoints.log, level="ERROR"):
            response = await self._post(_sheetPayload())
            await self._drainBackgroundTasks()

        self.assertEqual(response.status_code, 202)


if __name__ == "__main__":
    unittest.main()
