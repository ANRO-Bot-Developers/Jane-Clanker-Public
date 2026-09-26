from __future__ import annotations

import base64
import json
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import characters
import config
import discord
from cogs.staff.bgIntelligenceCog import (
    BgIntelDetailsView,
    BgIntelligenceCog,
    BgIntelProgressRelay,
    _updateBgIntelSheetLinkSafe,
)
from features.staff.bgIntelligence import rendering, scoring, service
from features.staff.sessions import bgBuckets, bgSpreadsheetQueue
from features.staff.sessions.Roblox import robloxAssets, robloxBadges, robloxGamepasses, robloxInventoryApi, robloxInventoryVisual


def _report(**overrides):
    base = {
        "discordUserId": 123,
        "discordDisplayName": "Reviewer Target",
        "discordUsername": "target",
        "reviewBucket": bgBuckets.adultBgReviewBucket,
        "reviewBucketSource": "manual",
        "identitySource": "rover",
        "robloxUserId": 456,
        "robloxUsername": "TargetUser",
        "roverError": None,
        "robloxCreated": "2020-01-01T00:00:00Z",
        "robloxAgeDays": 1500,
        "usernameHistoryScanStatus": "OK",
        "usernameHistoryScanError": None,
        "previousRobloxUsernames": [],
        "altScanStatus": "OK",
        "altScanError": None,
        "altMatches": [],
        "directMatches": [],
        "externalSourceStatus": "SKIPPED",
        "externalSourceError": None,
        "externalSourceMatches": [],
        "externalSourceDetails": [],
        "connectionScanStatus": "OK",
        "connectionScanError": None,
        "connectionSummary": {"friends": 10, "followers": 2, "following": 3},
        "friendIdsScanStatus": "OK",
        "friendIdsScanError": None,
        "friendUserIds": [],
        "groupScanStatus": "OK",
        "groupScanError": None,
        "groupSummary": {"totalGroups": 0},
        "groups": [],
        "flaggedGroups": [],
        "flagMatches": [],
        "inventoryScanStatus": "OK",
        "inventoryScanError": None,
        "inventorySummary": {
            "uniqueAssetCount": 0,
            "knownValueRobux": 0,
            "complete": True,
            "valueSource": "test",
        },
        "flaggedItems": [],
        "gamepassScanStatus": "OK",
        "gamepassScanError": None,
        "gamepassSummary": {
            "totalGamepasses": 1,
            "totalRobux": 25,
            "pricedGamepasses": 1,
            "unpricedGamepasses": 0,
            "complete": True,
        },
        "ownedGamepasses": [{"id": 99, "name": "Pass", "price": 25}],
        "favoriteGameScanStatus": "OK",
        "favoriteGameScanError": None,
        "favoriteGames": [{"name": "Game", "universeId": 1, "placeId": 2}],
        "flaggedFavoriteGames": [],
        "outfitScanStatus": "OK",
        "outfitScanError": None,
        "outfits": [],
        "badgeScanStatus": "OK",
        "badgeScanError": None,
        "flaggedBadges": [],
        "badgeHistoryScanStatus": "OK",
        "badgeHistoryScanError": None,
        "badgeHistorySample": [],
        "badgeTimelineSummary": {
            "sampleSize": 0,
            "datedBadges": 0,
            "awardDateStatus": "OK",
            "historyComplete": True,
            "quality": "none",
        },
        "priorReportSummary": {
            "totalRecent": 1,
            "highRiskRecent": 0,
            "noScoreRecent": 0,
            "queueApprovals": 1,
            "queueRejections": 0,
            "rows": [],
        },
        "privateInventoryDmSent": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _dotnet_ticks(value: datetime) -> int:
    epoch = datetime(1, 1, 1, tzinfo=timezone.utc)
    normalized = value.astimezone(timezone.utc)
    delta = normalized - epoch
    return ((delta.days * 86400 + delta.seconds) * 10_000_000) + (delta.microseconds * 10)


def _badge_cursor(badge_id: int, awarded_at: datetime) -> str:
    payload = {"key": f"{int(badge_id)}:{_dotnet_ticks(awarded_at)}"}
    raw = json.dumps(payload, separators=(",", ":")) + "\nchecksum"
    return base64.b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


class BgIntelligenceScoringTests(unittest.TestCase):
    def test_banned_user_match_sets_hard_minimum(self):
        report = _report(
            directMatches=[
                {
                    "type": "banned_user",
                    "value": 456,
                    "minimumScore": 95,
                    "note": "test ban",
                }
            ]
        )

        score = scoring.scoreReport(report)

        self.assertTrue(score.scored)
        self.assertGreaterEqual(score.score, 95)
        self.assertEqual(score.hardMinimum, 95)
        self.assertTrue(any("known banned Roblox ID" in signal.label for signal in score.signals))

    def test_previous_username_match_is_scored_but_capped_below_ban_override(self):
        report = _report(
            previousRobloxUsernames=["OldName"],
            directMatches=[
                {
                    "type": "previous_username",
                    "value": "OldName",
                    "minimumScore": 60,
                }
            ],
        )

        score = scoring.scoreReport(report)

        self.assertTrue(score.scored)
        self.assertGreaterEqual(score.score, 60)
        self.assertLess(score.hardMinimum, 80)
        self.assertTrue(any("Prior Roblox username" in signal.label for signal in score.signals))

    def test_known_member_alt_match_adds_contextual_risk(self):
        report = _report(
            robloxUsername="TargetUserAlt",
            altMatches=[
                {
                    "candidateUsername": "TargetUserAlt",
                    "candidateKind": "current_username",
                    "knownRobloxUsername": "TargetUser",
                    "knownDiscordUserId": 999,
                    "source": "orbat_member_mirror",
                    "reason": "known member username with an alt/back-up marker",
                    "strength": "moderate",
                    "evidenceType": "name_variant",
                }
            ],
        )

        score = scoring.scoreReport(report)

        self.assertTrue(score.scored)
        self.assertTrue(any("Alt/identity evidence" in signal.label for signal in score.signals))

    def test_cleared_alt_match_is_not_scored_as_risk(self):
        report = _report(
            altMatches=[
                {
                    "strength": "cleared",
                    "evidenceType": "staff_alt_link",
                    "knownRobloxUsername": "KnownUser",
                    "reason": "Staff cleared this relation.",
                }
            ],
        )

        score = scoring.scoreReport(report)

        self.assertTrue(score.scored)
        self.assertTrue(any("cleared/not-alt" in signal.label for signal in score.signals))
        self.assertFalse(any("Alt/identity evidence" in signal.label for signal in score.signals))

    def test_missing_identity_returns_identity_review(self):
        report = _report(
            robloxUserId=None,
            robloxUsername=None,
            externalSourceStatus="OK",
            externalSourceMatches=[],
            externalSourceDetails=[],
        )

        score = scoring.scoreReport(report)

        self.assertFalse(score.scored)
        self.assertEqual(score.outcome, "needs_identity")
        self.assertEqual(score.band, "Needs Identity Review")

    def test_configured_group_flag_stays_reviewable_after_clean_context(self):
        report = _report(
            flaggedGroups=[
                {"id": 1001, "name": "Flagged Group", "role": "Member", "rank": 1},
            ],
            flagMatches=[
                {
                    "type": "keyword",
                    "value": "flagged",
                    "context": "group",
                    "groupId": 1001,
                    "groupName": "Flagged Group",
                }
            ],
        )

        score = scoring.scoreReport(report)

        self.assertTrue(score.scored)
        self.assertGreaterEqual(score.score, 40)
        self.assertGreaterEqual(score.signals[-1].points, 40)
        self.assertTrue(any("Review floor" in signal.label for signal in score.signals))

    def test_low_external_record_stays_reviewable_after_clean_context(self):
        report = _report(
            externalSourceStatus="OK",
            externalSourceDetails=[{"source": "TASE", "status": "OK", "summary": {}}],
            externalSourceMatches=[
                {
                    "source": "TASE",
                    "scoreSum": 20,
                    "guildCount": 1,
                    "pastOffender": False,
                }
            ],
        )

        score = scoring.scoreReport(report)

        self.assertTrue(score.scored)
        self.assertGreaterEqual(score.score, 35)
        self.assertEqual(score.band, "Mild Review")

    def test_connection_footprint_affects_established_accounts_lightly(self):
        noPrior = {
            "totalRecent": 0,
            "highRiskRecent": 0,
            "noScoreRecent": 0,
            "queueApprovals": 0,
            "queueRejections": 0,
            "rows": [],
        }
        thinReport = _report(
            connectionSummary={"friends": 0, "followers": 0, "following": 0},
            priorReportSummary=noPrior,
        )
        establishedReport = _report(
            connectionSummary={"friends": 100, "followers": 25, "following": 30},
            priorReportSummary=noPrior,
        )

        thinScore = scoring.scoreReport(thinReport)
        establishedScore = scoring.scoreReport(establishedReport)

        self.assertGreater(thinScore.score, establishedScore.score)
        self.assertTrue(any("social footprint" in signal.label for signal in thinScore.signals))
        self.assertTrue(any("social footprint looks established" in signal.label for signal in establishedScore.signals))

    def test_repeated_no_score_results_do_not_add_risk_points(self):
        baselinePrior = {
            "totalRecent": 0,
            "highRiskRecent": 0,
            "noScoreRecent": 0,
            "queueApprovals": 0,
            "queueRejections": 0,
            "rows": [],
        }
        repeatedNoScorePrior = {
            "totalRecent": 2,
            "highRiskRecent": 0,
            "noScoreRecent": 2,
            "queueApprovals": 0,
            "queueRejections": 0,
            "rows": [],
        }

        baselineScore = scoring.scoreReport(_report(priorReportSummary=baselinePrior))
        repeatedNoScoreScore = scoring.scoreReport(_report(priorReportSummary=repeatedNoScorePrior))

        self.assertEqual(repeatedNoScoreScore.score, baselineScore.score)
        self.assertLess(repeatedNoScoreScore.confidence, baselineScore.confidence)
        self.assertFalse(any(signal.points > 0 and "no-score" in signal.label.lower() for signal in repeatedNoScoreScore.signals))


class BgIntelligenceSheetLinkTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_google_oauth_token_skips_sheet_update_without_traceback(self):
        report = _report()
        score = scoring.scoreReport(report)
        missingTokenError = FileNotFoundError(
            "Google OAuth token file is missing. Set config.googleOauthTokenPath or "
            "GOOGLE_OAUTH_TOKEN_PATH to an authorized Google OAuth user token JSON."
        )

        with (
            patch.object(
                bgSpreadsheetQueue,
                "updateLatestBgIntelSheetLink",
                AsyncMock(side_effect=missingTokenError),
            ),
            self.assertLogs("cogs.staff.bgIntelligenceCog", level="WARNING") as logs,
        ):
            result = await _updateBgIntelSheetLinkSafe(
                report=report,
                riskScore=score,
                reportId=42,
                message=SimpleNamespace(jump_url="https://discord.com/channels/1/2/3"),
                guildId=1,
            )

        self.assertFalse(result.updated)
        self.assertIn("Google OAuth token is missing", result.reason)
        self.assertIn("Jane Intel sheet link update skipped", result.reason)
        self.assertFalse(any("Traceback" in line for line in logs.output))


class BgIntelligenceRenderingTests(unittest.TestCase):
    def test_text_report_includes_full_release_sections(self):
        report = _report()
        score = scoring.scoreReport(report)

        text = rendering.buildReportText(report, score=score, reportId=42)

        self.assertIn("Decision Readiness", text)
        self.assertIn("Source Checks", text)
        self.assertIn("Alt / Identity Evidence", text)
        self.assertIn("Gamepasses", text)
        self.assertIn("Favorite Game Sample", text)
        self.assertIn("Jane History", text)

    def test_embed_footer_respects_optional_text_report(self):
        report = _report()
        score = scoring.scoreReport(report)

        without_text = rendering.buildReportEmbed(report, score=score, includeTextReport=False)
        with_text = rendering.buildReportEmbed(report, score=score, includeTextReport=True)

        self.assertNotIn("Full text report", without_text.footer.text or "")
        self.assertIn("Full text report", with_text.footer.text or "")

    def test_debug_timings_appear_in_text_report_and_overview(self):
        report = _report(
            debugTimingSummary={
                "totalSeconds": 12.345,
                "uiSeconds": 1.25,
                "steps": [
                    {"label": "Loading scan rules...", "seconds": 0.5},
                    {"label": "Checking inventory...", "seconds": 4.25},
                ],
            }
        )
        score = scoring.scoreReport(report)

        text = rendering.buildReportText(report, score=score, reportId=42)
        embed = rendering.buildReportEmbed(report, score=score)

        self.assertIn("Debug Timings", text)
        self.assertIn("Total scan time", text)
        self.assertIn("Progress UI latency", text)
        self.assertIn("[Debug] Timings", [field.name for field in embed.fields])
        self.assertTrue(any("Checking inventory" in field.value for field in embed.fields if field.name == "[Debug] Timings"))

    def test_debug_section_embed_keeps_full_timing_list(self):
        report = _report(
            debugTimingSummary={
                "totalSeconds": 48.5,
                "steps": [
                    {"label": f"Step {index}", "seconds": float(index)}
                    for index in range(1, 35)
                ],
            }
        )
        score = scoring.scoreReport(report)

        embed = rendering.buildPublicSectionEmbed(report, score=score, section="debug")
        fieldText = "\n".join(field.value for field in embed.fields)

        self.assertGreater(len(embed.fields), 1)
        self.assertIn("Total scan time", fieldText)
        self.assertIn("Step 34", fieldText)

    def test_overview_embed_matches_summary_layout(self):
        report = _report(
            directMatches=[
                {
                    "type": "banned_user",
                    "value": 456,
                    "minimumScore": 95,
                    "note": "test ban",
                }
            ],
            altMatches=[
                {
                    "candidateUsername": "TargetUserAlt",
                    "knownRobloxUsername": "TargetUser",
                    "reason": "known member username with an alt/back-up marker",
                    "strength": "moderate",
                }
            ],
        )
        score = scoring.scoreReport(report)

        embed = rendering.buildReportEmbed(report, score=score)
        fieldNames = [field.name for field in embed.fields]
        fieldText = "\n".join(field.value for field in embed.fields)

        self.assertEqual(
            fieldNames,
            [
                "[Scan] Detection Summary",
                "[Profile] Profile Information",
                "[Connections] Connections",
                "[Groups] Groups",
                "[Inventory] Inventory",
                "[Gamepasses] Gamepasses",
                "[Favorites] Favorites",
                "[Records] TASE Records",
                "[Records] Rotector Records",
                "[Badges] Badges",
            ],
        )
        self.assertIn("Review Band", embed.fields[0].value)
        self.assertNotIn("route", fieldText.lower())
        self.assertNotIn("[Direct] Direct Rule Matches", fieldNames)
        self.assertNotIn("[Alt] Alt / Identity Evidence", fieldNames)

    def test_expanded_sections_keep_detailed_content(self):
        report = _report(
            directMatches=[
                {
                    "type": "banned_user",
                    "value": 456,
                    "minimumScore": 95,
                    "note": "test ban",
                }
            ],
            inventorySummary={
                "itemsScanned": 25,
                "pagesScanned": 2,
                "uniqueAssetCount": 10,
                "uniqueGamepassCount": 1,
                "knownValueRobux": 100,
                "pricedAssetCount": 4,
                "unpricedAssetCount": 6,
                "complete": True,
                "valueSource": "test",
            },
        )
        score = scoring.scoreReport(report)

        scanEmbed = rendering.buildPublicSectionEmbed(report, score=score, section="scan")
        inventoryEmbed = rendering.buildPublicSectionEmbed(report, score=score, section="inventory")

        self.assertIn("Direct rule matches", scanEmbed.fields[0].value)
        self.assertIn("Items scanned", inventoryEmbed.fields[0].value)
        self.assertIn("Known current asset value", inventoryEmbed.fields[0].value)

    def test_inventory_section_lists_flag_links_and_reasons(self):
        report = _report(
            inventorySummary={
                "itemsScanned": 8,
                "pagesScanned": 1,
                "uniqueAssetCount": 4,
                "uniqueGamepassCount": 0,
                "knownValueRobux": 150,
                "pricedAssetCount": 3,
                "unpricedAssetCount": 1,
                "complete": True,
                "flaggedItemCount": 2,
                "visualMatchedCount": 1,
                "visualCandidateCount": 3,
                "visualReferenceCount": 2,
                "keywordMatchCount": 1,
                "normalizedKeywordMatchCount": 0,
                "fuzzyKeywordMatchCount": 0,
                "suspiciousCreatorCount": 1,
                "multiSignalMatchCount": 0,
            },
            flaggedItems=[
                {
                    "id": 200,
                    "name": "Disputed Shirt",
                    "itemType": "Shirt",
                    "creatorId": 10,
                    "creatorName": "Maker",
                    "matchType": "keyword",
                    "matchMode": "exact",
                    "keyword": "soviet",
                    "matchCount": 1,
                },
                {
                    "id": 201,
                    "name": "Copied Hat",
                    "itemType": "Hat",
                    "creatorId": 11,
                    "creatorName": "Uploader",
                    "matchType": "visual",
                    "matchMode": "thumbnail_hash",
                    "referenceItemId": 100,
                    "matchCount": 1,
                },
            ],
        )
        score = scoring.scoreReport(report)

        inventoryEmbed = rendering.buildPublicSectionEmbed(report, score=score, section="inventory")
        fieldText = "\n".join(field.value for field in inventoryEmbed.fields)

        self.assertIn("https://www.roblox.com/catalog/200", fieldText)
        self.assertIn("item name matched keyword `soviet`", fieldText)
        self.assertIn("https://www.roblox.com/catalog/201", fieldText)
        self.assertIn("thumbnail similarity to [item 100](https://www.roblox.com/catalog/100)", fieldText)

    def test_detection_summary_lists_inventory_match_reasons(self):
        report = _report(
            inventorySummary={
                "itemsScanned": 8,
                "pagesScanned": 1,
                "uniqueAssetCount": 4,
                "uniqueGamepassCount": 0,
                "knownValueRobux": 150,
                "pricedAssetCount": 3,
                "unpricedAssetCount": 1,
                "complete": True,
                "flaggedItemCount": 3,
                "exactItemMatchCount": 1,
                "visualMatchedCount": 1,
                "visualCandidateCount": 3,
                "visualReferenceCount": 2,
                "keywordMatchCount": 1,
                "normalizedKeywordMatchCount": 0,
                "fuzzyKeywordMatchCount": 0,
                "suspiciousCreatorCount": 0,
                "multiSignalMatchCount": 0,
            },
            flaggedItems=[
                {
                    "id": 200,
                    "name": "Meru Shirt",
                    "matchType": "keyword",
                    "matchMode": "exact",
                    "keyword": "meru",
                    "matchCount": 1,
                },
                {
                    "id": 201,
                    "name": "Copied Hat",
                    "matchType": "visual",
                    "matchMode": "thumbnail_hash",
                    "referenceItemId": 100,
                    "referenceItemName": "Flagged Hat",
                    "matchCount": 1,
                },
                {
                    "id": 202,
                    "name": "Known Bad Item",
                    "matchType": "item",
                    "matchMode": "exact",
                    "matchCount": 1,
                },
            ],
        )
        score = scoring.scoreReport(report)

        embed = rendering.buildReportEmbed(report, score=score)
        detectionField = next(field for field in embed.fields if field.name == "[Scan] Detection Summary")

        self.assertIn("Item summary:", detectionField.value)
        self.assertIn("[Meru Shirt](https://www.roblox.com/catalog/200) - matched keyword `meru`", detectionField.value)
        self.assertIn(
            "[Copied Hat](https://www.roblox.com/catalog/201) - similar to [Flagged Hat](https://www.roblox.com/catalog/100)",
            detectionField.value,
        )
        self.assertIn("[Known Bad Item](https://www.roblox.com/catalog/202) - previously flagged item", detectionField.value)


class RobloxBadgeTimelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_badge_history_applies_cursor_boundary_award_dates(self):
        oldRequestJson = robloxBadges._requestJson
        oldCacheGet = robloxBadges._cacheGet
        oldCacheSet = robloxBadges._cacheSet
        awardedAt = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

        async def fakeRequestJson(*args, **kwargs):
            return 200, {
                "data": [
                    {
                        "id": 111,
                        "name": "First",
                        "created": "2020-01-01T00:00:00Z",
                        "statistics": {"awardedCount": 10},
                    },
                    {
                        "id": 222,
                        "name": "Boundary",
                        "created": "2020-01-02T00:00:00Z",
                        "statistics": {"awardedCount": 20},
                    },
                ],
                "nextPageCursor": _badge_cursor(222, awardedAt),
            }

        try:
            robloxBadges._requestJson = fakeRequestJson
            robloxBadges._cacheGet = lambda *args, **kwargs: None
            robloxBadges._cacheSet = lambda *args, **kwargs: None

            result = await robloxBadges.fetchRobloxUserBadges(456, limit=10, maxPages=1)
        finally:
            robloxBadges._requestJson = oldRequestJson
            robloxBadges._cacheGet = oldCacheGet
            robloxBadges._cacheSet = oldCacheSet

        self.assertIsNone(result.error)
        self.assertEqual(result.badges[1]["awardedDate"], "2024-01-02T03:04:05Z")
        self.assertEqual(result.badges[1]["awardedDateSource"], "badge_history_next_cursor")

    async def test_badge_history_uses_open_cloud_inventory_dates(self):
        oldRequestJson = robloxBadges._requestJson
        oldCacheGet = robloxBadges._cacheGet
        oldCacheSet = robloxBadges._cacheSet
        oldInventoryApiKey = robloxBadges._badgeInventoryApiKey
        oldPopulateDetails = robloxBadges._populateBadgeHistoryDetails

        async def fakeRequestJson(*args, **kwargs):
            url = args[1] if len(args) > 1 else kwargs.get("url")
            params = kwargs.get("params") or {}
            if str(url).startswith("https://apis.roblox.com/cloud/v2/users/456/inventory-items"):
                if params.get("pageToken") == "page-2":
                    return 200, {
                        "inventoryItems": [
                            {
                                "badgeDetails": {"badgeId": "20"},
                                "addTime": "2024-01-03T00:00:00Z",
                            }
                        ]
                    }
                return 200, {
                    "inventoryItems": [
                        {
                            "badgeDetails": {"badgeId": "10"},
                            "addTime": "2024-01-02T03:04:05Z",
                        }
                    ],
                    "nextPageToken": "page-2",
                }
            raise AssertionError(f"Unexpected URL {url!r}")

        async def fakePopulateDetails(badges):
            return None

        try:
            robloxBadges._requestJson = fakeRequestJson
            robloxBadges._cacheGet = lambda *args, **kwargs: None
            robloxBadges._cacheSet = lambda *args, **kwargs: None
            robloxBadges._badgeInventoryApiKey = lambda: "token"
            robloxBadges._populateBadgeHistoryDetails = fakePopulateDetails

            result = await robloxBadges.fetchRobloxUserBadges(456, limit=10, maxPages=2)
        finally:
            robloxBadges._requestJson = oldRequestJson
            robloxBadges._cacheGet = oldCacheGet
            robloxBadges._cacheSet = oldCacheSet
            robloxBadges._badgeInventoryApiKey = oldInventoryApiKey
            robloxBadges._populateBadgeHistoryDetails = oldPopulateDetails

        self.assertIsNone(result.error)
        self.assertIsNone(result.nextCursor)
        self.assertEqual([badge["id"] for badge in result.badges], [20, 10])
        self.assertEqual(result.badges[0]["awardedDateSource"], "open_cloud_inventory")

    async def test_badge_award_403_reports_roblox_unavailable(self):
        oldRequestJson = robloxBadges._requestJson
        oldCacheGet = robloxBadges._cacheGet
        oldCacheSet = robloxBadges._cacheSet
        oldDelay = robloxBadges._badgeAwardLookupDelaySec

        async def fakeRequestJson(*args, **kwargs):
            return 403, {"errors": [{"message": "Request Context Failure: response code is not 200"}]}

        try:
            robloxBadges._requestJson = fakeRequestJson
            robloxBadges._cacheGet = lambda *args, **kwargs: None
            robloxBadges._cacheSet = lambda *args, **kwargs: None
            robloxBadges._badgeAwardLookupDelaySec = lambda: 0.0

            result = await robloxBadges.fetchRobloxBadgeAwards(456, {999}, batchSize=1)
        finally:
            robloxBadges._requestJson = oldRequestJson
            robloxBadges._cacheGet = oldCacheGet
            robloxBadges._cacheSet = oldCacheSet
            robloxBadges._badgeAwardLookupDelaySec = oldDelay

        self.assertEqual(result.status, 403)
        self.assertIn("unavailable from Roblox", result.error or "")
        self.assertIn("Request Context Failure", result.error or "")

    def test_badge_timeline_summary_tracks_partial_date_sources(self):
        summary = service._buildBadgeTimelineSummary(
            [
                {
                    "id": 123,
                    "awardedDate": "2024-01-02T03:04:05Z",
                    "awardedDateSource": "badge_history_next_cursor",
                }
            ],
            awardDateStatus="PARTIAL",
            awardDateError="Badge award-date lookup is unavailable from Roblox (403).",
        )

        self.assertEqual(summary["datedBadges"], 1)
        self.assertEqual(summary["awardDateStatus"], "PARTIAL")
        self.assertEqual(summary["awardDateSources"], {"badge_history_next_cursor": 1})

    def test_badge_overview_does_not_report_zero_dated_awards_on_api_error(self):
        report = _report(
            badgeTimelineSummary={
                "sampleSize": 125,
                "datedBadges": 0,
                "awardDateStatus": "ERROR",
                "historyComplete": True,
                "quality": "undated",
                "awardDateError": "Badge award-date lookup is unavailable from Roblox (403).",
            }
        )

        line = rendering._overviewBadgeLine(report)

        self.assertIn("Roblox award dates are currently unavailable", line)
        self.assertNotIn("0** dated", line)

    def test_badge_graph_file_builds_from_open_cloud_award_dates(self):
        report = _report(
            badgeHistorySample=[
                {
                    "id": 10,
                    "awardedDate": "2024-01-02T03:04:05Z",
                    "awardedDateSource": "open_cloud_inventory",
                },
                {
                    "id": 20,
                    "awardedDate": "2024-01-03T00:00:00Z",
                    "awardedDateSource": "open_cloud_inventory",
                },
            ]
        )

        graphFile = rendering.buildBadgeTimelineGraphFile(report)

        self.assertIsNotNone(graphFile)
        self.assertEqual(graphFile.filename, "bg-intel-badge-timeline.png")

    def test_all_badges_have_award_dates_requires_every_badge_to_be_dated(self):
        self.assertTrue(
            service._allBadgesHaveAwardDates(
                [
                    {"id": 10, "awardedDate": "2024-01-02T03:04:05Z"},
                    {"id": 20, "awardedDate": "2024-01-03T00:00:00Z"},
                ]
            )
        )
        self.assertFalse(
            service._allBadgesHaveAwardDates(
                [
                    {"id": 10, "awardedDate": "2024-01-02T03:04:05Z"},
                    {"id": 20},
                ]
            )
        )


class RobloxInventoryValueTests(unittest.TestCase):
    def test_inventory_value_excludes_self_created_assets(self):
        summary = robloxInventoryApi._inventoryValueSummary(
            {
                1: {"price": 100, "creatorId": 456, "creatorType": "User"},
                2: {"price": 50, "creatorId": 999},
                3: {"price": 25},
                4: {"price": None, "creatorId": 999},
                5: {"price": 200, "creatorId": 456, "creatorType": "Group"},
            },
            ownerRobloxUserId=456,
            uniqueAssetCount=5,
        )

        self.assertEqual(summary["knownValueRobux"], 275)
        self.assertEqual(summary["pricedAssetCount"], 3)
        self.assertEqual(summary["unpricedAssetCount"], 1)
        self.assertEqual(summary["selfCreatedAssetCount"], 1)
        self.assertEqual(summary["selfCreatedPricedAssetCount"], 1)
        self.assertEqual(summary["selfCreatedRobuxExcluded"], 100)

    def test_gamepass_value_excludes_self_created_gamepasses(self):
        summary = robloxGamepasses._gamepassValueSummary(
            [
                {"price": 100, "creatorId": 456, "creatorType": "User"},
                {"price": 50, "creatorId": 999},
                {"price": None, "creatorId": 999},
                {"price": 200, "creatorId": 456, "creatorType": "Group"},
            ],
            ownerRobloxUserId=456,
        )

        self.assertEqual(summary["totalRobux"], 250)
        self.assertEqual(summary["pricedGamepasses"], 2)
        self.assertEqual(summary["unpricedGamepasses"], 1)
        self.assertEqual(summary["selfCreatedGamepassCount"], 1)
        self.assertEqual(summary["selfCreatedPricedGamepassCount"], 1)
        self.assertEqual(summary["selfCreatedRobuxExcluded"], 100)


class RobloxAssetPriceTests(unittest.IsolatedAsyncioTestCase):
    async def test_price_lookup_caps_individual_fallback_after_batch(self):
        oldBatchLookup = robloxAssets._fetchCatalogAssetPricesBatch
        oldRequestJson = robloxAssets._requestJson
        oldFallbackMax = config.robloxAssetPriceFallbackMaxAssets
        requestedIds = []

        async def fakeBatchLookup(assetIds, headers):
            return {}, [int(assetId) for assetId in assetIds], "batch miss"

        async def fakeRequestJson(method, url, **kwargs):
            assetId = int(str(url).rstrip("/").split("/")[-2])
            requestedIds.append(assetId)
            return 200, {
                "Name": f"Asset {assetId}",
                "PriceInRobux": 10,
                "Creator": {"Id": 99, "Name": "Creator", "CreatorType": "User"},
                "AssetTypeId": 11,
                "AssetType": "Shirt",
            }

        try:
            config.robloxAssetPriceFallbackMaxAssets = 2
            robloxAssets._fetchCatalogAssetPricesBatch = fakeBatchLookup
            robloxAssets._requestJson = fakeRequestJson

            prices, error = await robloxAssets.fetchCatalogAssetPrices([910001, 910002, 910003, 910004])
        finally:
            robloxAssets._fetchCatalogAssetPricesBatch = oldBatchLookup
            robloxAssets._requestJson = oldRequestJson
            config.robloxAssetPriceFallbackMaxAssets = oldFallbackMax

        self.assertEqual(requestedIds, [910001, 910002])
        self.assertEqual(sorted(prices.keys()), [910001, 910002])
        self.assertIn("Skipped 2 individual asset price lookup", error or "")


class RobloxInventoryVisualTests(unittest.IsolatedAsyncioTestCase):
    async def test_visual_hash_matching_requires_compatible_asset_types(self):
        oldHashLookup = robloxInventoryVisual.fetchRobloxAssetThumbnailHashes
        oldPriceLookup = robloxInventoryVisual.robloxAssets.fetchCatalogAssetPrices
        oldEnabled = config.bgIntelligenceInventoryVisualMatchingEnabled
        oldDistance = config.bgIntelligenceInventoryVisualHashDistanceMax
        hashLookups = []

        async def fakeHashLookup(assetIds):
            hashLookups.append([int(assetId) for assetId in assetIds])
            return {int(assetId): "0" * 16 for assetId in assetIds}, None

        async def fakePriceLookup(assetIds):
            details = {
                100: {"name": "Flagged Shirt", "assetTypeId": 11, "assetTypeName": "Shirt"},
            }
            return {int(assetId): details[int(assetId)] for assetId in assetIds if int(assetId) in details}, None

        try:
            config.bgIntelligenceInventoryVisualMatchingEnabled = True
            config.bgIntelligenceInventoryVisualHashDistanceMax = 3
            robloxInventoryVisual.fetchRobloxAssetThumbnailHashes = fakeHashLookup
            robloxInventoryVisual.robloxAssets.fetchCatalogAssetPrices = fakePriceLookup

            flaggedItemsById = {}
            summary = await robloxInventoryVisual.applyInventoryVisualMatches(
                flaggedItemsById=flaggedItemsById,
                candidateItems=[
                    {
                        "id": 200,
                        "name": "Brown Hat",
                        "itemType": "Hat",
                        "assetTypeId": 8,
                        "visualCategory": "hat",
                    },
                    {
                        "id": 201,
                        "name": "Black Green Shirt",
                        "itemType": "Shirt",
                        "assetTypeId": 11,
                        "visualCategory": "classic_shirt",
                    },
                ],
                referenceItemIds={100},
                referenceHashes={100: "0" * 16},
            )
        finally:
            robloxInventoryVisual.fetchRobloxAssetThumbnailHashes = oldHashLookup
            robloxInventoryVisual.robloxAssets.fetchCatalogAssetPrices = oldPriceLookup
            config.bgIntelligenceInventoryVisualMatchingEnabled = oldEnabled
            config.bgIntelligenceInventoryVisualHashDistanceMax = oldDistance

        self.assertEqual(summary["matchedCount"], 1)
        self.assertEqual(summary["candidateCount"], 2)
        self.assertEqual(summary["comparedCandidateCount"], 1)
        self.assertGreaterEqual(summary["skippedTypeMismatchCount"], 1)
        self.assertEqual(hashLookups, [[201]])
        self.assertNotIn(200, flaggedItemsById)
        self.assertEqual(flaggedItemsById[201]["matchType"], "visual")
        self.assertEqual(flaggedItemsById[201]["referenceItemName"], "Flagged Shirt")

    async def test_visual_hash_matching_rejects_shirt_vs_tshirt(self):
        oldHashLookup = robloxInventoryVisual.fetchRobloxAssetThumbnailHashes
        oldPriceLookup = robloxInventoryVisual.robloxAssets.fetchCatalogAssetPrices
        oldEnabled = config.bgIntelligenceInventoryVisualMatchingEnabled
        oldDistance = config.bgIntelligenceInventoryVisualHashDistanceMax
        hashLookups = []

        async def fakeHashLookup(assetIds):
            hashLookups.append([int(assetId) for assetId in assetIds])
            return {int(assetId): "0" * 16 for assetId in assetIds}, None

        async def fakePriceLookup(assetIds):
            details = {
                100: {"assetTypeId": 2, "assetTypeName": "TShirt"},
            }
            return {int(assetId): details[int(assetId)] for assetId in assetIds if int(assetId) in details}, None

        try:
            config.bgIntelligenceInventoryVisualMatchingEnabled = True
            config.bgIntelligenceInventoryVisualHashDistanceMax = 6
            robloxInventoryVisual.fetchRobloxAssetThumbnailHashes = fakeHashLookup
            robloxInventoryVisual.robloxAssets.fetchCatalogAssetPrices = fakePriceLookup

            flaggedItemsById = {}
            summary = await robloxInventoryVisual.applyInventoryVisualMatches(
                flaggedItemsById=flaggedItemsById,
                candidateItems=[
                    {
                        "id": 201,
                        "name": "Black Green Shirt",
                        "itemType": "Shirt",
                        "assetTypeId": 11,
                        "visualCategory": "classic_shirt",
                    },
                ],
                referenceItemIds={100},
                referenceHashes={100: "0" * 16},
            )
        finally:
            robloxInventoryVisual.fetchRobloxAssetThumbnailHashes = oldHashLookup
            robloxInventoryVisual.robloxAssets.fetchCatalogAssetPrices = oldPriceLookup
            config.bgIntelligenceInventoryVisualMatchingEnabled = oldEnabled
            config.bgIntelligenceInventoryVisualHashDistanceMax = oldDistance

        self.assertEqual(summary["matchedCount"], 0)
        self.assertEqual(summary["comparedCandidateCount"], 0)
        self.assertGreaterEqual(summary["skippedTypeMismatchCount"], 1)
        self.assertEqual(hashLookups, [])
        self.assertEqual(flaggedItemsById, {})

    async def test_visual_hash_matching_rejects_palette_mismatch(self):
        oldSignatureLookup = robloxInventoryVisual.fetchRobloxAssetVisualSignatures
        oldPriceLookup = robloxInventoryVisual.robloxAssets.fetchCatalogAssetPrices
        oldEnabled = config.bgIntelligenceInventoryVisualMatchingEnabled
        oldDistance = config.bgIntelligenceInventoryVisualHashDistanceMax
        oldColorEnabled = config.bgIntelligenceInventoryVisualColorMatchingEnabled
        oldColorDistance = config.bgIntelligenceInventoryVisualColorDistanceMax

        redSignature = json.dumps(
            {"v": 3, "bins": [1000, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], "mode": "detailed"},
            separators=(",", ":"),
        )
        blueSignature = json.dumps(
            {"v": 3, "bins": [0, 0, 0, 0, 0, 0, 0, 1000, 0, 0, 0, 0, 0, 0, 0], "mode": "detailed"},
            separators=(",", ":"),
        )

        async def fakeSignatureLookup(assetIds):
            return {
                int(assetId): {
                    "thumbnailHash": "0" * 16,
                    "colorSignature": blueSignature,
                    "colorSignatureVersion": 3,
                }
                for assetId in assetIds
            }, None

        async def fakePriceLookup(assetIds):
            details = {
                100: {"name": "Flagged Shirt", "assetTypeId": 11, "assetTypeName": "Shirt"},
            }
            return {int(assetId): details[int(assetId)] for assetId in assetIds if int(assetId) in details}, None

        try:
            config.bgIntelligenceInventoryVisualMatchingEnabled = True
            config.bgIntelligenceInventoryVisualHashDistanceMax = 3
            config.bgIntelligenceInventoryVisualColorMatchingEnabled = True
            config.bgIntelligenceInventoryVisualColorDistanceMax = 0.58
            robloxInventoryVisual.fetchRobloxAssetVisualSignatures = fakeSignatureLookup
            robloxInventoryVisual.robloxAssets.fetchCatalogAssetPrices = fakePriceLookup

            flaggedItemsById = {}
            summary = await robloxInventoryVisual.applyInventoryVisualMatches(
                flaggedItemsById=flaggedItemsById,
                candidateItems=[
                    {
                        "id": 201,
                        "name": "Blue Shirt",
                        "itemType": "Shirt",
                        "assetTypeId": 11,
                        "visualCategory": "classic_shirt",
                    },
                ],
                referenceItemIds={100},
                referenceHashes={100: "0" * 16},
                referenceColorSignatures={100: redSignature},
            )
        finally:
            robloxInventoryVisual.fetchRobloxAssetVisualSignatures = oldSignatureLookup
            robloxInventoryVisual.robloxAssets.fetchCatalogAssetPrices = oldPriceLookup
            config.bgIntelligenceInventoryVisualMatchingEnabled = oldEnabled
            config.bgIntelligenceInventoryVisualHashDistanceMax = oldDistance
            config.bgIntelligenceInventoryVisualColorMatchingEnabled = oldColorEnabled
            config.bgIntelligenceInventoryVisualColorDistanceMax = oldColorDistance

        self.assertEqual(summary["matchedCount"], 0)
        self.assertEqual(summary["comparedCandidateCount"], 1)
        self.assertEqual(summary["skippedColorMismatchCount"], 1)
        self.assertEqual(flaggedItemsById, {})


class BgIntelligenceViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_inventory_section_shows_dispute_button_only_when_flags_exist(self):
        report = _report(
            flaggedItems=[
                {
                    "id": 200,
                    "name": "Disputed Shirt",
                    "matchType": "keyword",
                    "matchMode": "exact",
                    "keyword": "soviet",
                }
            ]
        )
        score = scoring.scoreReport(report)
        view = BgIntelDetailsView(ownerId=1, report=report, riskScore=score, reportId=5)

        view._rebuildControls("overview")
        self.assertFalse(any(getattr(child, "label", "") == "Dispute Flag" for child in view.children))

        view._rebuildControls("inventory")
        self.assertTrue(any(getattr(child, "label", "") == "Dispute Flag" for child in view.children))

    async def test_rerun_requests_username_for_discord_scan_without_roblox_identity(self):
        report = _report(robloxUserId=None, robloxUsername=None, roverError="No Roblox account linked via RoVer.")
        score = scoring.scoreReport(report)

        view = BgIntelDetailsView(ownerId=1, report=report, riskScore=score, reportId=0)

        self.assertTrue(view._needsRobloxUsernameForRerun())

    async def test_rerun_does_not_request_username_when_roblox_identity_exists(self):
        report = _report(robloxUserId=456, robloxUsername="TargetUser")
        score = scoring.scoreReport(report)

        view = BgIntelDetailsView(ownerId=1, report=report, riskScore=score, reportId=0)

        self.assertFalse(view._needsRobloxUsernameForRerun())

    async def test_standard_view_exposes_report_button_without_auto_attachment(self):
        report = _report()
        score = scoring.scoreReport(report)

        view = BgIntelDetailsView(ownerId=1, report=report, riskScore=score, reportId=0)
        _, files = view._buildPublicPayload("overview")
        labels = {getattr(child, "label", None) for child in view.children}

        self.assertFalse(any(file.filename.endswith(".txt") for file in files))
        self.assertIn("Full Text Report", labels)

    async def test_debug_view_forces_text_report_attachment(self):
        report = _report(
            debugTimingSummary={
                "totalSeconds": 5.0,
                "steps": [{"label": "Scanning...", "seconds": 5.0}],
            }
        )
        score = scoring.scoreReport(report)

        view = BgIntelDetailsView(
            ownerId=1,
            report=report,
            riskScore=score,
            reportId=0,
            includeTextReport=False,
            debugMode=True,
        )
        _, files = view._buildPublicPayload("overview")

        self.assertTrue(any(file.filename.endswith(".txt") for file in files))

    async def test_debug_view_exposes_debug_dropdown_section(self):
        report = _report(
            debugTimingSummary={
                "totalSeconds": 5.0,
                "steps": [{"label": "Scanning...", "seconds": 5.0}],
            }
        )
        score = scoring.scoreReport(report)

        view = BgIntelDetailsView(
            ownerId=1,
            report=report,
            riskScore=score,
            reportId=0,
            debugMode=True,
        )
        select = next(child for child in view.children if isinstance(child, discord.ui.Select))

        self.assertIn("debug", [option.value for option in select.options])


class BgIntelligenceCommandTests(unittest.TestCase):
    def test_parse_discord_id_accepts_mentions_and_plain_ids(self):
        self.assertEqual(BgIntelligenceCog._parseDiscordId("123456789012345678"), 123456789012345678)
        self.assertEqual(BgIntelligenceCog._parseDiscordId("<@123456789012345678>"), 123456789012345678)
        self.assertEqual(BgIntelligenceCog._parseDiscordId("<@!123456789012345678>"), 123456789012345678)
        self.assertIsNone(BgIntelligenceCog._parseDiscordId("TargetUser"))


class BgIntelProgressRelayTests(unittest.IsolatedAsyncioTestCase):
    async def test_progress_relay_throttles_non_forced_updates(self):
        sent: list[str] = []
        clock = {"value": 10.0}

        async def updater(status: str) -> bool:
            sent.append(status)
            return True

        relay = BgIntelProgressRelay(
            updater=updater,
            minIntervalSec=1.0,
            nowFactory=lambda: clock["value"],
        )
        await relay.update("Checking Discord membership and main-server lookup...")
        clock["value"] = 10.2
        await relay.update("Loading scan rules...")
        clock["value"] = 10.4
        await relay.update("Checking RoVer for the linked Roblox account...")
        clock["value"] = 10.6
        await relay.update("Reviewing inventory and item values [scanning inventory pages]...")

        self.assertEqual(
            sent,
            [
                "Checking Discord membership and main-server lookup...",
                "Reviewing inventory and item values [scanning inventory pages]...",
            ],
        )

class CharacterAltMatcherTests(unittest.TestCase):
    def test_exact_same_username_is_not_alt_similarity_evidence(self):
        reason, strength, ratio = service._nameSimilarityReason(
            "PotaterGaming",
            "PotaterGaming",
            altWords=[],
            fuzzyEnabled=True,
            fuzzyMinSimilarity=0.9,
            fuzzyMinLength=5,
        )

        self.assertIsNone(reason)
        self.assertEqual(strength, "weak")
        self.assertIsNone(ratio)

    def test_alt_marker_suffix_matches_known_username(self):
        reason = characters.username_alt_match_reason("KnownUserBackup", "KnownUser")

        self.assertIsNotNone(reason)
        self.assertIn("alt", reason or "")

    def test_alt_marker_prefix_with_leetspeak_core_matches_known_username(self):
        reason = characters.username_alt_match_reason("BackupKn0wnUser", "KnownUser")

        self.assertIsNotNone(reason)
        self.assertIn("alt", reason or "")

    def test_alternate_characters_match_known_username(self):
        self.assertTrue(characters.looks_like_username_alt("Kn0wn_User", "KnownUser"))

    def test_trailing_digits_match_known_username(self):
        self.assertTrue(characters.looks_like_username_alt("KnownUser123", "KnownUser"))

    def test_arbitrary_contains_does_not_match(self):
        self.assertFalse(characters.looks_like_username_alt("RandomKnownUserThing", "KnownUser"))

    def test_exact_same_username_does_not_match(self):
        self.assertFalse(characters.looks_like_username_alt("KnownUser", "KnownUser"))


if __name__ == "__main__":
    unittest.main()
