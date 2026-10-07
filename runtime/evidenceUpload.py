from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

import discord

log = logging.getLogger(__name__)

_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
_MAX_MODAL_FILES = 10
_DEFAULT_FILESIZE_LIMIT = 10 * 1024 * 1024


def isImageAttachment(attachment: Any) -> bool:
    contentType = str(getattr(attachment, "content_type", "") or "").lower()
    if contentType.startswith("image/"):
        return True
    filename = str(getattr(attachment, "filename", "") or "").lower()
    return filename.endswith(_IMAGE_EXTENSIONS)


def selectEvidenceAttachments(
    attachments: Sequence[Any],
    *,
    imagesOnly: bool,
    minFiles: int,
    maxFiles: int,
) -> tuple[list[Any], str | None]:
    """Return (files to keep, error text). An error means nothing should be submitted."""
    usable = [item for item in attachments if not imagesOnly or isImageAttachment(item)]
    if len(usable) < max(1, int(minFiles)):
        kind = "image file" if imagesOnly else "file"
        plural = "" if minFiles == 1 else "s"
        return [], f"I need at least {int(minFiles)} {kind}{plural}. Press the button to try again."
    return usable[: max(1, int(maxFiles))], None


async def repostEvidence(channel: Any, attachments: Sequence[Any], *, uploaderId: int) -> discord.Message | None:
    """Copy modal uploads into a channel so the URLs outlive the interaction."""
    files: list[Any] = []
    try:
        for attachment in attachments:
            files.append(await attachment.to_file())
    except (discord.HTTPException, OSError):
        log.warning("Could not download an evidence upload from user %s.", uploaderId)
        return None
    try:
        return await channel.send(
            content=f"Evidence uploaded by <@{int(uploaderId)}>.",
            files=files,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except (discord.Forbidden, discord.HTTPException):
        log.warning("Could not post evidence from user %s into channel %s.", uploaderId, getattr(channel, "id", "?"))
        return None


def _uploadSizeLimit(channel: Any) -> int:
    limit = getattr(getattr(channel, "guild", None), "filesize_limit", None)
    if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0:
        return limit
    return _DEFAULT_FILESIZE_LIMIT


@dataclass(slots=True)
class _UploadSession:
    userId: int
    evidenceChannel: Any
    minFiles: int
    maxFiles: int
    imagesOnly: bool
    future: asyncio.Future
    closed: bool = False
    succeeded: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_EXPIRED_TEXT = "The upload window expired, so those files were not submitted. Please start again."


async def _handleSubmission(session: _UploadSession, interaction: discord.Interaction, attachments: Sequence[Any]) -> None:
    await interaction.response.defer(ephemeral=True)
    async with session.lock:
        if session.succeeded:
            await interaction.followup.send("That upload was already received.", ephemeral=True)
            return
        if session.closed:
            await interaction.followup.send(_EXPIRED_TEXT, ephemeral=True)
            return
        chosen, error = selectEvidenceAttachments(
            attachments,
            imagesOnly=session.imagesOnly,
            minFiles=session.minFiles,
            maxFiles=session.maxFiles,
        )
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return
        limit = _uploadSizeLimit(session.evidenceChannel)
        if any(int(getattr(item, "size", 0) or 0) > limit for item in chosen):
            limitMb = limit // (1024 * 1024)
            await interaction.followup.send(
                f"One or more files are larger than this server's upload limit ({limitMb} MB). "
                "Press the button to try again with smaller files.",
                ephemeral=True,
            )
            return
        posted = await repostEvidence(session.evidenceChannel, chosen, uploaderId=session.userId)
        if posted is None:
            await interaction.followup.send(
                "I could not save those files to the evidence channel. Press the button to try again, "
                "or tell staff if it keeps failing.",
                ephemeral=True,
            )
            return
        if session.closed:
            # The collector stopped waiting while we were posting; nothing will use this message.
            try:
                await posted.delete()
            except discord.HTTPException:
                log.warning("Could not delete orphaned evidence message for user %s.", session.userId)
            await interaction.followup.send(_EXPIRED_TEXT, ephemeral=True)
            return
        session.succeeded = True
        if not session.future.done():
            session.future.set_result(posted)
        await interaction.followup.send("Files received.", ephemeral=True)


class _EvidenceModal(discord.ui.Modal):
    def __init__(self, session: _UploadSession, *, title: str, fileLabel: str) -> None:
        super().__init__(title=title[:45], timeout=600)
        self._session = session
        self.upload = discord.ui.FileUpload(
            custom_id="evidence-files",
            required=True,
            min_values=max(1, min(session.minFiles, _MAX_MODAL_FILES)),
            max_values=max(1, min(session.maxFiles, _MAX_MODAL_FILES)),
        )
        self.add_item(discord.ui.Label(text=fileLabel[:45], component=self.upload))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _handleSubmission(self._session, interaction, list(self.upload.values))

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("Evidence upload modal failed for user %s.", getattr(interaction.user, "id", "?"), exc_info=error)
        text = "Something went wrong saving your files. Press the button to try again."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
        except discord.HTTPException:
            pass


class _EvidenceUploadView(discord.ui.View):
    def __init__(self, session: _UploadSession, *, modalTitle: str, fileLabel: str, timeoutSec: float) -> None:
        super().__init__(timeout=timeoutSec)
        self._session = session
        self._modalTitle = modalTitle
        self._fileLabel = fileLabel

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) == int(self._session.userId):
            return True
        await interaction.response.send_message("This upload button is not for you.", ephemeral=True)
        return False

    @discord.ui.button(label="Upload files", style=discord.ButtonStyle.primary)
    async def upload(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.send_modal(
            _EvidenceModal(self._session, title=self._modalTitle, fileLabel=self._fileLabel)
        )


async def collectEvidenceUpload(
    interaction: discord.Interaction,
    *,
    prompt: str,
    evidenceChannel: Any,
    minFiles: int,
    maxFiles: int,
    imagesOnly: bool = True,
    timeoutSec: float = 180.0,
    modalTitle: str = "Upload evidence",
    fileLabel: str = "Screenshots",
) -> discord.Message | None:
    """Prompt the user with an upload button and wait for a valid modal upload.

    Works whether or not the interaction has already been answered, including
    after a modal submit (Discord does not allow answering a modal with a modal,
    hence the button). Returns Jane's re-post in ``evidenceChannel`` or None on timeout.
    """
    session = _UploadSession(
        userId=int(interaction.user.id),
        evidenceChannel=evidenceChannel,
        minFiles=int(minFiles),
        maxFiles=int(maxFiles),
        imagesOnly=bool(imagesOnly),
        future=asyncio.get_running_loop().create_future(),
    )
    view = _EvidenceUploadView(session, modalTitle=modalTitle, fileLabel=fileLabel, timeoutSec=timeoutSec)
    if interaction.response.is_done():
        await interaction.followup.send(prompt, view=view, ephemeral=True)
    else:
        await interaction.response.send_message(prompt, view=view, ephemeral=True)
    try:
        return await asyncio.wait_for(session.future, timeout=timeoutSec)
    except asyncio.TimeoutError:
        return None
    finally:
        session.closed = True
        view.stop()
