import unittest

from features.staff.recruitment import rendering


def _submission(**overrides) -> dict:
    submission = {
        "recruitUserId": 404,
        "submitterId": 101,
        "passedOrientation": 0,
        "points": 2,
        "status": "PENDING",
    }
    submission.update(overrides)
    return submission


def _serverCheck(embed) -> str | None:
    for field in embed.fields:
        if field.name == "Server Check":
            return field.value
    return None


class RecruitmentEmbedServerCheckTests(unittest.TestCase):
    def test_found(self) -> None:
        embed = rendering.buildRecruitmentEmbed(_submission(recruitLookupStatus=rendering.recruitLookupFound))
        self.assertIn("Jane found this user", _serverCheck(embed))

    def test_not_found(self) -> None:
        embed = rendering.buildRecruitmentEmbed(_submission(recruitLookupStatus=rendering.recruitLookupNotFound))
        self.assertIn("couldn't find this user", _serverCheck(embed))

    def test_unavailable(self) -> None:
        embed = rendering.buildRecruitmentEmbed(_submission(recruitLookupStatus=rendering.recruitLookupUnavailable))
        self.assertIn("verify manually", _serverCheck(embed))

    def test_legacy_submission_has_no_server_check(self) -> None:
        self.assertIsNone(_serverCheck(rendering.buildRecruitmentEmbed(_submission())))
        self.assertIsNone(_serverCheck(rendering.buildRecruitmentEmbed(_submission(recruitLookupStatus=""))))


if __name__ == "__main__":
    unittest.main()
