from __future__ import annotations

import unittest
from types import SimpleNamespace

from features.staff.bgIntelligence import externalSources, rendering, scoring


class _FakeResponse:
    def __init__(self, status: int, payload, headers=None):
        self.status = status
        self._payload = payload
        self.headers = headers or {}

    async def json(self, content_type=None):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    def __init__(self, response: _FakeResponse):
        self.response = response
        self.urls: list[str] = []
        self.headers: list[dict] = []

    def get(self, url, headers=None, timeout=None):
        self.urls.append(url)
        self.headers.append(headers or {})
        return self.response


def _config(**overrides):
    base = {
        "bgIntelligenceRotectorEnabled": True,
        "bgIntelligenceRotectorDiscordEnabled": True,
        "bgIntelligenceRaywardApiKey": "rwd_test",
        "bgIntelligenceRaywardApiBaseUrl": "https://roscoe.rayward.app",
        "bgIntelligenceRaywardTimeoutSec": 5,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


_confirmedRoblox = {
    "id": 456,
    "flagType": 2,
    "statusLabel": "Unsafe",
    "category": "condo",
    "categoryLabel": "Condo",
    "reviewer": {"username": "mod", "displayName": "Mod"},
    "reasons": [
        {
            "type": "condo",
            "title": "Condo Activity",
            "sources": [
                {"id": "trap", "label": "Trap", "summary": "Joined a monitored condo game"},
                {"id": "discord", "label": "Discord"},
            ],
            "evidence": [
                {"kind": "discordUser", "discordId": "1000****0001"},
                {
                    "kind": "discordGuild",
                    "discordId": "1000****0001",
                    "safeName": "Tracked Server A",
                    "name": "UNSAFE LIVE NAME discord.gg/bad",
                    "types": ["CONDO"],
                    "joinedAt": 1700000000,
                    "firstSeen": None,
                    "lastSeen": None,
                    "staff": True,
                },
                {"kind": "somethingNew", "value": 1},
            ],
        },
        {"type": "profile", "title": "User Profile", "sources": [{"id": "rotector", "label": "Rotector"}]},
    ],
}


class RotectorNormalizeTests(unittest.TestCase):
    def test_confirmed_roblox_flag_becomes_match(self):
        matches, summary = externalSources._normalizeRotectorPayload(
            _confirmedRoblox, subjectType="roblox", subjectId=456, ownRobloxUserId=456
        )

        self.assertEqual(summary["flagType"], 2)
        self.assertEqual(len(matches), 1)
        match = matches[0]
        self.assertTrue(match["actionable"])
        self.assertEqual(match["flagName"], "Confirmed")
        self.assertEqual([reason["type"] for reason in match["reasons"]], ["condo", "profile"])
        evidence = match["reasons"][0]["evidence"]
        self.assertEqual(len(evidence), 2)  # unknown kind is skipped
        self.assertIn("Tracked Server A", evidence[1])
        self.assertNotIn("UNSAFE LIVE NAME", " ".join(evidence))
        self.assertIn("2023-11-14", evidence[1])

    def test_unflagged_is_not_a_match(self):
        matches, summary = externalSources._normalizeRotectorPayload(
            {"id": 456, "flagType": 0}, subjectType="roblox", subjectId=456
        )
        self.assertEqual(matches, [])
        self.assertEqual(summary["flagName"], "Unflagged")

    def test_queued_is_not_a_match(self):
        matches, _ = externalSources._normalizeRotectorPayload(
            {"id": 456, "flagType": "3"}, subjectType="roblox", subjectId=456
        )
        self.assertEqual(matches, [])

    def test_discord_linked_accounts_exclude_scanned_roblox_account(self):
        payload = {
            "id": "123",
            "flagType": 0,
            "linkedRobloxAccounts": [
                {"robloxUserId": 456, "robloxUsername": "Self", "flagType": 2},
                {"robloxUserId": 789, "robloxUsername": "OtherAlt", "flagType": 1},
            ],
        }
        matches, summary = externalSources._normalizeRotectorPayload(
            payload, subjectType="discord", subjectId=123, ownRobloxUserId=456
        )
        self.assertEqual(summary["linkedRobloxAccountCount"], 1)
        self.assertEqual(len(matches), 1)
        self.assertFalse(matches[0]["actionable"])
        self.assertEqual(matches[0]["linkedRobloxAccounts"][0]["robloxUsername"], "OtherAlt")


class RotectorRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_roblox_lookup_hits_rayward_with_bearer_key(self):
        session = _FakeSession(_FakeResponse(200, {"success": True, "data": _confirmedRoblox}))

        result = await externalSources.scanRotectorRoblox(robloxUserId=456, session=session, configModule=_config())

        self.assertEqual(result.status, "OK")
        self.assertEqual(session.urls, ["https://roscoe.rayward.app/v2/lookup/rotector/roblox/user/456"])
        self.assertEqual(session.headers[0]["Authorization"], "Bearer rwd_test")
        self.assertEqual(len(result.matches), 1)

    async def test_discord_lookup_uses_discord_route(self):
        session = _FakeSession(_FakeResponse(200, {"success": True, "data": {"id": "123", "flagType": 0}}))

        result = await externalSources.scanRotectorDiscord(
            discordUserId=123, robloxUserId=456, session=session, configModule=_config()
        )

        self.assertEqual(result.status, "OK")
        self.assertTrue(session.urls[0].endswith("/v2/lookup/rotector/discord/user/123"))

    async def test_503_is_an_error_not_unflagged(self):
        session = _FakeSession(
            _FakeResponse(503, {"success": False, "error": "Upstream down", "requestId": "abc", "code": "UNAVAILABLE"})
        )

        result = await externalSources.scanRotectorRoblox(robloxUserId=456, session=session, configModule=_config())

        self.assertEqual(result.status, "ERROR")
        self.assertEqual(result.matches, [])
        self.assertIn("status is unknown", result.error)
        self.assertIn("abc", result.error)

    async def test_daily_quota_reports_retry_after(self):
        session = _FakeSession(
            _FakeResponse(429, {"success": False, "error": "Quota", "code": "DAILY_QUOTA_EXCEEDED"}, {"Retry-After": "3600"})
        )

        result = await externalSources.scanRotectorRoblox(robloxUserId=456, session=session, configModule=_config())

        self.assertEqual(result.status, "ERROR")
        self.assertIn("Daily lookup quota", result.error)
        self.assertIn("3600", result.error)

    async def test_missing_key_skips_without_request(self):
        session = _FakeSession(_FakeResponse(200, {}))

        result = await externalSources.scanRotectorRoblox(
            robloxUserId=456, session=session, configModule=_config(bgIntelligenceRaywardApiKey="")
        )

        self.assertEqual(result.status, "SKIPPED")
        self.assertEqual(result.summary["reason"], "missing_api_key")
        self.assertEqual(session.urls, [])


def _report(**overrides):
    base = {
        "externalSourceStatus": "OK",
        "externalSourceError": None,
        "externalSourceMatches": [],
        "externalSourceDetails": [],
        "robloxUsername": "TargetUser",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class RotectorScoringAndRenderingTests(unittest.TestCase):
    def _confirmedMatch(self):
        matches, _ = externalSources._normalizeRotectorPayload(
            _confirmedRoblox, subjectType="roblox", subjectId=456, ownRobloxUserId=456
        )
        return matches[0]

    def test_confirmed_flag_scores_and_sets_review_floor(self):
        report = _report(
            externalSourceMatches=[self._confirmedMatch()],
            externalSourceDetails=[{"source": "Rotector", "status": "OK", "subjectType": "roblox", "summary": {}}],
        )

        delta, _, signals, floor = scoring._scoreExternalSources(report)

        self.assertEqual(delta, 55)
        self.assertEqual(floor, 65)
        self.assertTrue(any("Rotector database" in signal.label for signal in signals))

    def test_unflagged_rotector_is_never_reassuring(self):
        report = _report(
            externalSourceDetails=[
                {"source": "Rotector", "status": "OK", "subjectType": "roblox", "summary": {"flagType": 0}},
            ],
        )

        delta, _, signals, _ = scoring._scoreExternalSources(report)

        self.assertEqual(delta, 0)
        self.assertFalse(any(signal.kind == "reassuring" for signal in signals))

    def test_process_states_score_below_findings(self):
        for flagType in (3, 4, 5, 6, 8):
            points, _, _ = scoring._rotectorFlagWeight({"flagType": flagType})
            self.assertLess(points, 42, flagType)

    def test_rendering_attributes_rotector_and_never_says_safe(self):
        confirmed = _report(
            externalSourceMatches=[self._confirmedMatch()],
            externalSourceDetails=[{"source": "Rotector", "status": "OK", "subjectType": "roblox", "summary": {"flagType": 2, "flagName": "Confirmed"}}],
        )
        lines = "\n".join(rendering._recordDetailLines(confirmed))
        self.assertIn("Rotector database", lines)
        self.assertIn("Condo Activity", lines)
        self.assertNotIn("UNSAFE LIVE NAME", lines)

        unflagged = _report(
            externalSourceDetails=[{"source": "Rotector", "status": "OK", "subjectType": "roblox", "summary": {"flagType": 0}}],
        )
        overview = rendering._overviewRotectorRecordLine(unflagged)
        self.assertIn("not a safety clearance", overview)
        self.assertNotIn("Safe", overview)


if __name__ == "__main__":
    unittest.main()
