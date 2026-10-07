from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from runtime import evidenceUpload


def _attachment(filename: str, contentType: str | None = None, *, fails: bool = False, size: int = 0):
    toFile = AsyncMock(side_effect=discord.HTTPException(SimpleNamespace(status=500, reason="x"), "x")) if fails else AsyncMock(return_value=f"file:{filename}")
    return SimpleNamespace(filename=filename, content_type=contentType, to_file=toFile, size=size)


class SelectEvidenceAttachmentsTests(unittest.TestCase):
    def test_accepts_two_images(self) -> None:
        files = [_attachment("a.png", "image/png"), _attachment("b.JPG")]
        chosen, error = evidenceUpload.selectEvidenceAttachments(files, imagesOnly=True, minFiles=2, maxFiles=2)
        self.assertEqual(chosen, files)
        self.assertIsNone(error)

    def test_one_image_is_rejected_with_count(self) -> None:
        chosen, error = evidenceUpload.selectEvidenceAttachments(
            [_attachment("a.png", "image/png")], imagesOnly=True, minFiles=2, maxFiles=2
        )
        self.assertEqual(chosen, [])
        self.assertIn("2", error)

    def test_non_images_do_not_count(self) -> None:
        files = [_attachment("a.png", "image/png"), _attachment("notes.pdf", "application/pdf")]
        chosen, error = evidenceUpload.selectEvidenceAttachments(files, imagesOnly=True, minFiles=2, maxFiles=2)
        self.assertEqual(chosen, [])
        self.assertIn("image", error)

    def test_any_file_allowed_when_not_images_only(self) -> None:
        files = [_attachment("notes.pdf", "application/pdf")]
        chosen, error = evidenceUpload.selectEvidenceAttachments(files, imagesOnly=False, minFiles=1, maxFiles=10)
        self.assertEqual(chosen, files)
        self.assertIsNone(error)

    def test_extra_files_are_trimmed_to_max(self) -> None:
        files = [_attachment(f"{i}.png", "image/png") for i in range(4)]
        chosen, error = evidenceUpload.selectEvidenceAttachments(files, imagesOnly=True, minFiles=2, maxFiles=2)
        self.assertEqual(chosen, files[:2])
        self.assertIsNone(error)

    def test_empty_upload(self) -> None:
        chosen, error = evidenceUpload.selectEvidenceAttachments([], imagesOnly=False, minFiles=1, maxFiles=10)
        self.assertEqual(chosen, [])
        self.assertIsNotNone(error)


class RepostEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_reposts_files_and_returns_janes_message(self) -> None:
        posted = SimpleNamespace(jump_url="https://discord.test/jump", attachments=["x", "y"])
        channel = SimpleNamespace(send=AsyncMock(return_value=posted))
        files = [_attachment("a.png"), _attachment("b.png")]
        result = await evidenceUpload.repostEvidence(channel, files, uploaderId=42)
        self.assertIs(result, posted)
        kwargs = channel.send.await_args.kwargs
        self.assertEqual(kwargs["files"], ["file:a.png", "file:b.png"])
        self.assertIn("<@42>", kwargs["content"])

    async def test_download_failure_returns_none_without_posting(self) -> None:
        channel = SimpleNamespace(send=AsyncMock())
        result = await evidenceUpload.repostEvidence(
            channel, [_attachment("a.png"), _attachment("b.png", fails=True)], uploaderId=42
        )
        self.assertIsNone(result)
        channel.send.assert_not_awaited()

    async def test_missing_permission_returns_none(self) -> None:
        forbidden = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Missing Permissions")
        channel = SimpleNamespace(send=AsyncMock(side_effect=forbidden))
        result = await evidenceUpload.repostEvidence(channel, [_attachment("a.png")], uploaderId=42)
        self.assertIsNone(result)


class SubmissionHandlingTests(unittest.IsolatedAsyncioTestCase):
    def _session(self, **overrides):
        loopFuture = asyncio.get_running_loop().create_future()
        defaults = dict(
            userId=42,
            evidenceChannel=SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(jump_url="u", attachments=[1, 2]))),
            minFiles=2,
            maxFiles=2,
            imagesOnly=True,
            future=loopFuture,
        )
        defaults.update(overrides)
        return evidenceUpload._UploadSession(**defaults)

    def _interaction(self):
        return SimpleNamespace(
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def test_too_few_images_keeps_flow_open(self) -> None:
        session = self._session()
        interaction = self._interaction()
        await evidenceUpload._handleSubmission(session, interaction, [_attachment("a.png", "image/png")])
        self.assertFalse(session.future.done())
        self.assertIn("2", interaction.followup.send.await_args.args[0])

    async def test_valid_upload_resolves_future_with_reposted_message(self) -> None:
        session = self._session()
        interaction = self._interaction()
        await evidenceUpload._handleSubmission(
            session, interaction, [_attachment("a.png", "image/png"), _attachment("b.png", "image/png")]
        )
        self.assertTrue(session.future.done())
        self.assertEqual(session.future.result().jump_url, "u")

    async def test_oversized_file_is_rejected_before_download(self) -> None:
        channel = SimpleNamespace(
            send=AsyncMock(),
            guild=SimpleNamespace(filesize_limit=5 * 1024 * 1024),
        )
        session = self._session(evidenceChannel=channel)
        interaction = self._interaction()
        big = _attachment("big.png", "image/png", size=6 * 1024 * 1024)
        small = _attachment("small.png", "image/png", size=1024)
        await evidenceUpload._handleSubmission(session, interaction, [big, small])
        self.assertIn("upload limit", interaction.followup.send.await_args.args[0])
        self.assertIn("5 MB", interaction.followup.send.await_args.args[0])
        channel.send.assert_not_awaited()
        big.to_file.assert_not_awaited()
        small.to_file.assert_not_awaited()
        self.assertFalse(session.future.done())

    async def test_files_within_guild_limit_succeed(self) -> None:
        channel = SimpleNamespace(
            send=AsyncMock(return_value=SimpleNamespace(jump_url="u", attachments=[1, 2])),
            guild=SimpleNamespace(filesize_limit=50 * 1024 * 1024),
        )
        session = self._session(evidenceChannel=channel)
        interaction = self._interaction()
        files = [
            _attachment("a.png", "image/png", size=20 * 1024 * 1024),
            _attachment("b.png", "image/png", size=20 * 1024 * 1024),
        ]
        await evidenceUpload._handleSubmission(session, interaction, files)
        self.assertTrue(session.future.done())
        channel.send.assert_awaited_once()

    async def test_fallback_limit_applies_without_guild(self) -> None:
        channel = SimpleNamespace(send=AsyncMock())
        session = self._session(evidenceChannel=channel)
        interaction = self._interaction()
        files = [
            _attachment("a.png", "image/png", size=11 * 1024 * 1024),
            _attachment("b.png", "image/png", size=1),
        ]
        await evidenceUpload._handleSubmission(session, interaction, files)
        self.assertIn("10 MB", interaction.followup.send.await_args.args[0])
        channel.send.assert_not_awaited()
        self.assertFalse(session.future.done())

    async def test_repost_failure_tells_uploader_and_keeps_flow_open(self) -> None:
        forbidden = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Missing Permissions")
        session = self._session(evidenceChannel=SimpleNamespace(send=AsyncMock(side_effect=forbidden)))
        interaction = self._interaction()
        await evidenceUpload._handleSubmission(
            session, interaction, [_attachment("a.png", "image/png"), _attachment("b.png", "image/png")]
        )
        self.assertFalse(session.future.done())
        self.assertIn("could not", interaction.followup.send.await_args.args[0].lower())

    async def test_second_submit_after_success_is_ignored(self) -> None:
        session = self._session()
        files = [_attachment("a.png", "image/png"), _attachment("b.png", "image/png")]
        await evidenceUpload._handleSubmission(session, self._interaction(), files)
        late = self._interaction()
        await evidenceUpload._handleSubmission(session, late, files)
        self.assertEqual(session.evidenceChannel.send.await_count, 1)

    async def test_late_submit_after_close_gets_expired_message(self) -> None:
        session = self._session()
        session.closed = True
        interaction = self._interaction()
        await evidenceUpload._handleSubmission(
            session, interaction, [_attachment("a.png", "image/png"), _attachment("b.png", "image/png")]
        )
        session.evidenceChannel.send.assert_not_awaited()
        text = interaction.followup.send.await_args.args[0].lower()
        self.assertIn("expired", text)
        self.assertNotIn("received", text)

    async def test_close_during_repost_deletes_message_and_reports_expiry(self) -> None:
        posted = SimpleNamespace(jump_url="u", attachments=[1, 2], delete=AsyncMock())
        session = self._session()

        async def sendAndClose(**_kwargs):
            session.closed = True
            return posted

        session.evidenceChannel = SimpleNamespace(send=AsyncMock(side_effect=sendAndClose))
        interaction = self._interaction()
        await evidenceUpload._handleSubmission(
            session, interaction, [_attachment("a.png", "image/png"), _attachment("b.png", "image/png")]
        )
        posted.delete.assert_awaited_once()
        self.assertFalse(session.future.done())
        text = interaction.followup.send.await_args.args[0].lower()
        self.assertIn("expired", text)
        self.assertNotIn("files received", text)

    async def test_concurrent_submissions_post_once(self) -> None:
        session = self._session()
        files = [_attachment("a.png", "image/png"), _attachment("b.png", "image/png")]
        await asyncio.gather(
            evidenceUpload._handleSubmission(session, self._interaction(), files),
            evidenceUpload._handleSubmission(session, self._interaction(), files),
        )
        self.assertEqual(session.evidenceChannel.send.await_count, 1)


class CollectEvidenceUploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_returns_none_and_closes_session(self) -> None:
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=42),
            response=SimpleNamespace(is_done=lambda: False, send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        result = await evidenceUpload.collectEvidenceUpload(
            interaction,
            prompt="go",
            evidenceChannel=SimpleNamespace(send=AsyncMock()),
            minFiles=1,
            maxFiles=1,
            timeoutSec=0.01,
        )
        self.assertIsNone(result)
        view = interaction.response.send_message.await_args.kwargs["view"]
        self.assertTrue(view._session.closed)
        self.assertFalse(view._session.succeeded)


if __name__ == "__main__":
    unittest.main()
