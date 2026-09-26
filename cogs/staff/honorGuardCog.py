from __future__ import annotations

import asyncio
import re

from datetime import date, datetime, timezone
from typing import Optional, Sequence, TypedDict

import discord
from discord import app_commands
from discord.app_commands import Choice
from discord.ext import commands

import config
from cogs.staff.honorGuardViews import HonorGuardEventFinishModal, HonorGuardEventReviewView, HonorGuardEventSubmitView, HonorGuardEventView, HonorGuardGradingView, HonorGuardPointAwardReviewView, HonorGuardSoloSentryReviewView, HonorGuardEventManageView
from features.staff.clockins.engine import ClockinEngine
from features.staff.clockins.honorGuardAdapter import HonorGuardAdapter
from features.staff.honorGuard import buildScaffoldStatus
from features.staff.honorGuard import rendering as honorGuardRendering
from features.staff.honorGuard import service as honorGuardService
from runtime import cogGuards as runtimeCogGuards
from runtime import interaction as interactionRuntime
from runtime import normalization
from runtime import permissions as runtimePermissions

PLUGIN_MANIFEST = {
    "displayName": "Honor Guard",
    "category": "staff",
    "description": "Honor Guard ORBAT integration status and review-flow backend.",
}

_userIdRegex = re.compile(r"\d{15,22}")
class ManagementRecord(TypedDict):
    eventId: int
    user: discord.Member
    interaction: discord.Interaction
    type: str

_manageObjects: list[ManagementRecord] = []

def _saveRecord(interaction: discord.Interaction, user: discord.Member, eventId: int, type: str) -> None:
    record: ManagementRecord = {
        "eventId": eventId,
        "user": user,
        "interaction": interaction,
        "type": type,
    }
    _manageObjects.append(record)

def _deleteRecord(eventId: int, type: str) -> None:
    global _manageObjects
    _manageObjects = [r for r in _manageObjects if r["eventId"] != eventId or r["type"] != type]

def _getRecordByEventId(eventId: int, type: str) -> ManagementRecord:
    record = next((r for r in _manageObjects if r["eventId"] == eventId and r["type"] == type), None)
    return record

def _checkRecordByEventId(eventId: int, type: str) -> ManagementRecord | None:
    record = next((r for r in _manageObjects if r["eventId"] == eventId and r["type"] == type), None)
    return record

def _getMemberGroup(member: discord.Member) -> Optional[str]:
    honorGuardOfficerRoleIds = _normalizeRoleIdList(getattr(config, "honorGuardOfficerRoleIds", []))
    honorGuardNcoRoleIds = _normalizeRoleIdList(getattr(config, "honorGuardNcoRoleIds", []))
    honorGuardEnlistedRoleIds = _normalizeRoleIdList(getattr(config, "honorGuardEnlistedRoleIds", []))
    if runtimePermissions.hasAnyRole(member, honorGuardOfficerRoleIds):
        return "OFFICER"
    if runtimePermissions.hasAnyRole(member, honorGuardNcoRoleIds):
        return "NCO"
    if runtimePermissions.hasAnyRole(member, honorGuardEnlistedRoleIds):
        return "ENLISTED"
    return None

def _displayChannel(channelId: int) -> str:
    return f"<#{channelId}>" if int(channelId or 0) > 0 else "`not set`"


def _displayText(value: str) -> str:
    text = str(value or "").strip()
    return f"`{text}`" if text else "`not set`"


def _isImageAttachment(attachment: discord.Attachment) -> bool:
    contentType = (attachment.content_type or "").lower()
    if contentType.startswith("image/"):
        return True
    filename = (attachment.filename or "").lower()
    return filename.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"))


def _evidenceLinks(attachments: Sequence[discord.Attachment]) -> list[str]:
    return [attachment.url for attachment in attachments if _isImageAttachment(attachment)]


def _reviewerMention() -> str:
    roleId = int(
        getattr(
            config,
            "honorGuardReviewerPingRoleId",
            getattr(config, "honorGuardReviewerRoleId", 0),
        )
        or 0
    )
    if roleId > 0:
        return f"<@&{roleId}>"
    return ""


def _hasRole(member: discord.Member, roleId: Optional[int]) -> bool:
    return runtimePermissions.hasAnyRole(member, [roleId])


def _normalizeRoleIdList(rawValues: object) -> set[int]:
    return normalization.normalizeIntSet(rawValues)

def _toPositiveInt(value: object, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return int(default)
    return parsed if parsed > 0 else int(default)

def _parseUserIdList(rawText: str) -> list[int]:
    out: list[int] = []
    for match in _userIdRegex.findall(str(rawText or "")):
        parsed = _toPositiveInt(match)
        if parsed <= 0 or parsed in out:
            continue
        out.append(parsed)
    return out


class HonorGuardCog(runtimeCogGuards.InteractionGuardMixin, commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._eventLocks: dict[int, asyncio.Lock] = {}
        self._clockInAdapter = HonorGuardAdapter()
        self._clockInEngine = ClockinEngine(bot, self._clockInAdapter)
        
    async def cog_load(self) -> None:
        await self._restoreReviewViews()
        await self._restoreEventViews()

    async def _restoreReviewViews(self) -> None:
        # Handle review clicks on messages whose view was not restored below
        self.bot.add_view(HonorGuardPointAwardReviewView(self, 0))
        self.bot.add_view(HonorGuardSoloSentryReviewView(self, 0))
        self.bot.add_view(HonorGuardEventReviewView(self, 0))
        pointAwardRows = await honorGuardService.listPointAwardPendingStatuses()
        for row in pointAwardRows:
            messageId = int(row.get("messageId") or 0)
            if messageId <= 0:
                continue
            self.bot.add_view(
                HonorGuardPointAwardReviewView(self, int(row["submissionId"])),
                message_id=messageId,
            )
        soloSentryRows = await honorGuardService.listSoloSentryPendingStatuses()
        for row in soloSentryRows:
            messageId = int(row.get("messageId") or 0)
            if messageId <= 0:
                continue
            self.bot.add_view(
                HonorGuardSoloSentryReviewView(self, int(row["submissionId"])),
                message_id=messageId,
            )
        eventRows = await honorGuardService.listEventPendingStatuses()
        for row in eventRows:
            messageId = int(row.get("messageId") or 0)
            if messageId <= 0:
                continue
            self.bot.add_view(
                HonorGuardEventReviewView(self, int(row["submissionId"])),
                message_id=messageId,
            )

    async def _restoreEventViews(self) -> None:
        await self._clockInEngine.restoreOpenViews(
            lambda eventId: HonorGuardEventView(self, eventId),
        )

    def _isInHonorGuard(self, member: discord.Member) -> bool:
        honorGuardRoleIds = int(getattr(config, "honorGuardRoleId", 0) or 0)
        if not honorGuardRoleIds:
            return False
        return _hasRole(member, honorGuardRoleIds)

    def _canAwardPoints(self, member: discord.Member, awarded_user: discord.Member) -> bool:
        if member.id == awarded_user.id:
            return True
        honorGuardReviewerRoleId = int(getattr(config, "honorGuardReviewerRoleId", 0) or 0)
        if honorGuardReviewerRoleId <= 0:
            return True
        return _hasRole(member, honorGuardReviewerRoleId)

    def _canCreateClockIn(self, member: discord.Member, event_type: str) -> bool:
        sgmRoles = getattr(config, "honorGuardSeniorGuardsmanRoleIds", []) or []
        psRole = int(getattr(config, "honorGuardPlatoonSergeantRoleId", 0) or 0)
        poPlusRoles = getattr(config, "honorGuardParadeOfficerPlusRoleIds", []) or []
        ccRole = int(getattr(config, "honorGuardReviewerRoleId", 0) or 0)
        allowed = False
        needsCC = False
        if event_type == "orientation":
            requiredRoleIds = [*sgmRoles, psRole, *poPlusRoles]
        elif event_type == "sentry":
            requiredRoleIds = [*poPlusRoles]
        elif event_type == "drill":
            requiredRoleIds = [psRole, *poPlusRoles]
        elif event_type == "jge":
            requiredRoleIds = [psRole, *poPlusRoles]
            needsCC = True
        elif event_type == "nco":
            requiredRoleIds = [*poPlusRoles]
            needsCC = True
        else:
            requiredRoleIds = [*poPlusRoles]
        for roleId in requiredRoleIds:
            if roleId != 0 and _hasRole(member, roleId):
                allowed = True
                break
        if needsCC and not _hasRole(member, ccRole):
            allowed = False
        return allowed

    def _honorGuardCommandGuildIds(self) -> set[int]:
        configuredGuildIds = _normalizeRoleIdList(getattr(config, "honorGuardCommandGuildIds", []))
        if configuredGuildIds:
            return configuredGuildIds
        fallbackGuildIds = [
            getattr(config, "serverId", 0),
            getattr(config, "serverIdTesting", 0),
            *(getattr(config, "testGuildIds", []) or []),
        ]
        return _normalizeRoleIdList(fallbackGuildIds)

    async def _ensureHonorGuardCommandGuild(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            await self._safeReply(
                interaction,
                "This command can only be used in a server channel.",
            )
            return False
        allowedGuildIds = self._honorGuardCommandGuildIds()
        if not allowedGuildIds or int(interaction.guild.id) in allowedGuildIds:
            return True
        await self._safeReply(
            interaction,
            "Honor Guard commands can only be used in the CE server or configured test servers.",
        )
        return False

    @staticmethod
    def _memberDisplayName(member: discord.Member) -> str:
        return str(
            getattr(member, "display_name", None)
            or getattr(member, "global_name", None)
            or getattr(member, "name", None)
            or member.id
        ).strip()

    async def _resolveReviewChannel(
        self,
        guild: discord.Guild,
        fallback: Optional[discord.abc.Messageable],
        *,
        channelId: Optional[int] = None,
    ) -> Optional[discord.abc.Messageable]:
        targetChannelId = int(channelId or 0)
        if targetChannelId > 0:
            channel = self.bot.get_channel(targetChannelId)
            if channel is None:
                channel = guild.get_channel(targetChannelId)
            if channel is None:
                channel = await interactionRuntime.safeFetchChannel(self.bot, targetChannelId)
            if channel is not None:
                return channel
        return fallback

    @app_commands.command(
        name="honor-guard-status",
        description="Show the current Honor Guard ORBAT integration wiring.",
    )
    async def honorGuardStatus(self, interaction: discord.Interaction) -> None:
        member = await self._requireAdminOrManageGuild(interaction)
        if member is None:
            return
        status = buildScaffoldStatus(configModule=config)
        summary = [
            f"Enabled: `{status.config.enabled}`",
            f"Review channel: {_displayChannel(status.config.reviewChannelId)}",
            f"Log channel: {_displayChannel(status.config.logChannelId)}",
            f"Archive channel: {_displayChannel(status.config.archiveChannelId)}",
            f"Spreadsheet: {_displayText(status.config.spreadsheetId)}",
            f"Member sheet: {_displayText(status.config.memberSheetName)}",
            f"Archive sheet: {_displayText(status.config.archiveSheetName)}",
            f"Event hosts sheet: {_displayText(status.config.eventHostsSheetName)}",
        ]
        embed = discord.Embed(
            title="Honor Guard Integration",
            description="Backend tables, point rules, and sheet adapter are wired. Review commands are still separate.",
            color=discord.Color.gold(),
        )
        embed.add_field(name="Current Wiring", value="\n".join(summary), inline=False)
        embed.add_field(
            name="DB Tables",
            value="\n".join(f"`{name}`" for name in status.plannedDbTables),
            inline=False,
        )
        if status.sheetProblems:
            embed.add_field(
                name="Sheet Adapter Warnings",
                value="\n".join(f"- {problem}" for problem in status.sheetProblems),
                inline=False,
            )
        embed.add_field(
            name="Next Milestones",
            value="\n".join(status.nextMilestones),
            inline=False,
        )
        await self._safeReply(interaction, embed=embed)

    @app_commands.command(
        name="honorguard-award-points",
        description="Award points to a member of the Honor Guard.",
    )
    @app_commands.describe(
        awarded_user="User you want to award",
        awarded_points="Amount of points you want to award",
        reason="The reason for the award",
    )
    @app_commands.rename(awarded_user="awarded-user")
    @app_commands.rename(awarded_points="awarded-points")
    async def honorGuardAwardPoints(
        self,
        interaction: discord.Interaction,
        awarded_user: discord.Member,
        reason: str,
        awarded_points: int,
    ) -> None:
        if not await self._ensureHonorGuardCommandGuild(interaction):
            return
        if not interaction.channel or not isinstance(interaction.user, discord.Member):
            await self._safeReply(
                interaction,
                "This command can only be used in a server channel.",
            )
            return
        
        if not self._isInHonorGuard(interaction.user):
            await self._safeReply(interaction, "Only members of the Honor Guard can use this command.")
            return


        if not self._canAwardPoints(interaction.user, awarded_user):
            await self._safeReply(
                interaction,
                "You do not have permission to award Honor Guard points.",
            )
            return
        if int(awarded_points or 0) <= 0 :
            await self._safeReply(
                interaction,
                "Honor Guard point awards cannot be zero or negative.",
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        submissionId = await honorGuardService.createPointAwardSubmission(
            guildId=int(interaction.guild.id),
            channelId=int(interaction.channel.id),
            submitterId=int(interaction.user.id),
            awardedUserId=int(awarded_user.id),
            awardedPoints=int(awarded_points or 0),
            reason=str(reason or "").strip(),
            awardedUserDisplayName=self._memberDisplayName(awarded_user),
        )
        submission = await honorGuardService.getPointAwardSubmission(submissionId)
        if not submission:
            await interaction.followup.send(
                "Failed to create point award submission.",
                ephemeral=True,
            )
            return

        embed = honorGuardRendering.buildPointAwardEmbed(submission)
        view = HonorGuardPointAwardReviewView(self, submissionId)
        reviewMessage = await self._postHonorGuardForReview(
            guild=interaction.guild,
            fallbackChannel=interaction.channel,
            embed=embed,
            view=view,
            reviewChannelId=int(getattr(config, "honorGuardReviewChannelId", 0) or 0),
        )
        if not reviewMessage:
            await interaction.followup.send(
                "Submission saved, but I could not post it for review.",
                ephemeral=True,
            )
            return

        await honorGuardService.setPointAwardMessageId(submissionId, reviewMessage.id)
        await interaction.followup.send(
            "Submitted point award log.",
            ephemeral=True,
        )

    @app_commands.command(
        name="honorguard-solo-sentry",
        description="Submit a solo sentry log for Honor Guard review.",
    )
    @app_commands.describe(
        duty_date="Duty date in YYYY-MM-DD format.",
        image="Primary sentry screenshot.",
        extra_image="Second sentry screenshot.",
    )
    @app_commands.rename(duty_date="duty-date")
    @app_commands.rename(extra_image="extra-image")
    async def honorGuardSoloSentry(
        self,
        interaction: discord.Interaction,
        image: discord.Attachment,
        extra_image: discord.Attachment,
        duty_date: app_commands.Timestamp = datetime.now(tz=timezone.utc),
    ) -> None:
        if not await self._ensureHonorGuardCommandGuild(interaction):
            return
        if not interaction.channel or not isinstance(interaction.user, discord.Member):
            await self._safeReply(
                interaction,
                "This command can only be used in a server channel.",
            )
            return

        if not self._isInHonorGuard(interaction.user):
            await self._safeReply(
                interaction,
                "Only members of the Honor Guard can use this command.",
            )
            return

        try:
            normalizedDutyDate = date.fromtimestamp(duty_date.astimezone(timezone.utc).timestamp()).isoformat()
        except ValueError:
            await self._safeReply(
                interaction,
                "Duty date must use the @time format.",
            )
            return

        attachments = [image, extra_image]
        imageUrls = _evidenceLinks(attachments)
        if len(imageUrls) < 2:
            await self._safeReply(
                interaction,
                "Two valid image attachments are required for solo sentry logs.",
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        imageFiles: list[discord.File] = []
        for attachment in attachments:
            if not _isImageAttachment(attachment):
                continue
            try:
                imageFiles.append(await attachment.to_file())
            except (discord.HTTPException, OSError):
                continue
        if len(imageFiles) < 2:
            await interaction.followup.send(
                "I could not copy both sentry screenshots for review. Please try again.",
                ephemeral=True,
            )
            return

        try:
            submissionId = await honorGuardService.createSoloSentrySubmission(
                guildId=int(interaction.guild.id),
                channelId=int(interaction.channel.id),
                submitterId=int(interaction.user.id),
                targetUserId=int(interaction.user.id),
                targetDisplayName=self._memberDisplayName(interaction.user),
                dutyDate=normalizedDutyDate,
                imageUrls=imageUrls,
            )
        except ValueError as exc:
            await interaction.followup.send(
                str(exc),
                ephemeral=True,
            )
            return

        submission = await honorGuardService.getSoloSentrySubmission(submissionId)
        if not submission:
            await interaction.followup.send(
                "Failed to create solo sentry submission.",
                ephemeral=True,
            )
            return

        embed = honorGuardRendering.buildSoloSentrySubmissionEmbed(submission)
        view = HonorGuardSoloSentryReviewView(self, submissionId)
        reviewMessage = await self._postHonorGuardForReview(
            guild=interaction.guild,
            fallbackChannel=interaction.channel,
            embed=embed,
            view=view,
            files=imageFiles,
            reviewChannelId=int(getattr(config, "honorGuardReviewChannelId", 0) or 0),
        )
        if not reviewMessage:
            await interaction.followup.send(
                "Submission saved, but I could not post it for review.",
                ephemeral=True,
            )
            return

        await honorGuardService.setSubmissionMessageId(submissionId, reviewMessage.id)
        await interaction.followup.send(
            "Submitted solo sentry log.",
            ephemeral=True,
        )

    @app_commands.command(
        name="honorguard-event-log",
        description="Create a clock-in for an Honor Guard event.",
    )
    @app_commands.choices(
        event_type=[
            Choice(name="Training", value="drill"),
            Choice(name="Orientation", value="orientation"),
            Choice(name="Sentry", value="sentry"),
            Choice(name="Tryout", value="tryout"),
            Choice(name="Inspection", value="inspection"),
            Choice(name="Game Night", value="gamenight"),
            Choice(name="Junior Guardsman Exam", value="jge"),
            Choice(name="Non-Commissioned Officer Exam", value="ncoe"),
        ]
    )
    @app_commands.choices(
        platoon=[
            Choice(name="None", value="none"),
            Choice(name="Ceremonial", value="cgp"),
            Choice(name="Cavalry", value="cmp"),
            Choice(name="Executive", value="egp"),
        ]
    )
    @app_commands.rename(event_type="event-type")
    @app_commands.rename(event_time="event-time")
    @app_commands.rename(event_description="event-description")
    async def honorGuardEventLog(
        self,
        interaction: discord.Interaction,
        event_type : Choice[str],
        event_description: str,
        event_time: app_commands.Timestamp = datetime.now(tz=timezone.utc),
        platoon: Choice[str] = None,
        host: Optional[discord.Member] = None,
        supervisors: Optional[str] = None,
        cohosts: Optional[str] = None,
    ) -> None:
        if not await self._ensureHonorGuardCommandGuild(interaction):
            return
        if not interaction.channel or not isinstance(interaction.user, discord.Member):
            await self._safeReply(
                interaction,
                "This command can only be used in a server channel.",
            )
            return
        if not self._canCreateClockIn(interaction.user, event_type.value):
            await self._safeReply(
                interaction,
                "You do not have permission to create this Honor Guard clock-in.",
            )
            return
        platoonValue = platoon.value if platoon else "none"
        if platoonValue.upper() != "NONE" and platoonValue.upper() not in getattr(config, "honorGuardActivePlatoons", [""]):
            await self._safeReply(
                interaction,
                "This platoon is not active for Honor Guard events.",
            )
        

        if not host:
            host = interaction.user
        coHostIds = _parseUserIdList(str(cohosts or ""))
        supervisorIds = _parseUserIdList(str(supervisors or ""))

        guild = self.bot.get_guild(int(interaction.guild.id))
        resolved: list[tuple[int, discord.Member, str, str]] = []
        seenUserIds: list[int] = []
        missingRankMentions: list[str] = []
        unknownMemberIds: list[int] = []

        def _consider(userId: int, member: Optional[discord.Member], role: str) -> None:
            if userId in seenUserIds:
                return
            seenUserIds.append(userId)
            if member is None:
                unknownMemberIds.append(userId)
                return
            group = _getMemberGroup(member)
            if group is None:
                missingRankMentions.append(member.mention)
                return
            resolved.append((userId, member, group, role))

        _consider(int(host.id), host, "HOST")
        for supervisorId in supervisorIds:
            _consider(supervisorId, guild.get_member(supervisorId) if guild else None, "SUPERVISOR")
        for coHostId in coHostIds:
            _consider(coHostId, guild.get_member(coHostId) if guild else None, "COHOST")

        if missingRankMentions or unknownMemberIds:
            parts = []
            if missingRankMentions:
                parts.append(f"missing an Honor Guard rank role: {', '.join(missingRankMentions)}")
            if unknownMemberIds:
                parts.append(f"not found in this server: {', '.join(f'`{uid}`' for uid in unknownMemberIds)}")
            await self._safeReply(
                interaction,
                "Cannot create event clock-in — " + "; ".join(parts) + ".",
            )
            return

        eventId = await self._clockInEngine.createSession(
            guildId=int(interaction.guild.id),
            channelId=int(interaction.channel.id),
            hostId=int(host.id),
            maxAttendeeLimit=99,
            eventType=event_type.value,
            eventTitle=str(event_description or "").strip(),
            eventDate=event_time.astimezone(tz=timezone.utc).replace(tzinfo=None).isoformat(timespec="minutes"),
            platoon=platoonValue,
        )
        for userId, _member, group, role in resolved:
            await self._clockInEngine.addAttendee(eventId, userId, memberGroup=group, participantRole=role, guildId=int(interaction.guild.id))
        event = await self._clockInEngine.getSession(int(eventId))
        if not event:
            await self._safeReply(
                interaction,
                "Could not create event clock-in.",
            )
            return
        embed = self._clockInAdapter.buildEmbed(event, await self._clockInEngine.listAttendees(eventId))
        view = HonorGuardEventView(self, eventId)
        message = await interactionRuntime.safeChannelSend(interaction.channel, embed=embed, view=view)
        if message is None:
            await self._safeReply(
                interaction,
                "Could not create the event clock-in message in this channel.",
            )
            return
        await self._clockInEngine.setSessionMessageId(int(eventId), int(message.id))
        await self._safeReply(
            interaction,
            "Group event clock-in created.",
        )


    @app_commands.command(
        name="honorguard-schedule-event",
        description="Schedule an event for Honor Guard.",
    )
    async def honorGuardScheduleEvent(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        event_description: str,
    ) -> None:
        _ = member
        _ = event_description
        await self._safeReply(
            interaction,
            "Honor Guard event scheduling is not wired yet.",
        )

    @app_commands.command(
        name="honorguard-quota-cycle",
        description="Cycle the quota for Honor Guard.",
    )
    async def honorGuardQuotaCycle(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
    ) -> None:
        _ = member
        await self._safeReply(
            interaction,
            "Honor Guard quota cycling is not wired yet.",
        )

    async def _canManageEvent(self, interaction: discord.Interaction, event: dict) -> bool:
        if interaction.user.id == int(event.get("hostId") or 0):
            return True
        else:
            attendees = await self._clockInEngine.listAttendees(int(event.get("eventId") or 0))
            for attendee in attendees:
                if attendee.get("participantRole") == "SUPERVISOR" and int(attendee.get("userId") or 0) == interaction.user.id:
                    return True
        return False

    async def _updateEventMessage(
        self,
        eventId: int,
        *,
        message: Optional[discord.Message] = None,
    ) -> None:
        await self._clockInEngine.updateClockinMessage(
            int(eventId),
            viewFactory=lambda sessionId: HonorGuardEventView(self, sessionId),
            message=message,
        )

    async def _refreshEventMessageFromInteraction(
        self,
        eventId: int,
        interaction: discord.Interaction,
    ) -> None:
        if isinstance(interaction.message, discord.Message):
            await self._updateEventMessage(eventId, message=interaction.message)
            return
        await self._updateEventMessage(eventId)

    async def _deleteEventClockinMessage(
        self,
        event: dict,
        *,
        message: Optional[discord.Message] = None,
    ) -> None:
        await self._clockInEngine.deleteClockinMessage(
            event,
            message=message,
        )

    async def _collectTwoImageEvidenceMessage(
        self,
        *,
        channel: discord.abc.Messageable,
        userId: int,
        timeoutSec: float = 180.0,
    ) -> Optional[discord.Message]:
        channelId = getattr(channel, "id", None)
        if channelId is None:
            return None

        def check(message: discord.Message) -> bool:
            # We only accept the submitter's next message in this channel with
            # exactly two image attachments.
            if message.author.id != userId:
                return False
            if message.channel.id != channelId:
                return False
            images = [att for att in message.attachments if _isImageAttachment(att)]
            return len(images) == 2

        try:
            message = await self.bot.wait_for("message", check=check, timeout=timeoutSec)
        except asyncio.TimeoutError:
            return None
        return message

    async def _resolveConfiguredMessageChannel(
        self,
        guild: discord.Guild,
        channelId: int,
    ) -> Optional[discord.abc.Messageable]:
        if int(channelId or 0) <= 0:
            return None
        channel = self.bot.get_channel(int(channelId))
        if channel is None:
            channel = guild.get_channel(int(channelId))
        if channel is None:
            channel = await interactionRuntime.safeFetchChannel(self.bot, int(channelId))
        if isinstance(channel, (discord.TextChannel, discord.Thread)):
            return channel
        return None

    async def handleEventJoin(self, interaction: discord.Interaction, eventId: int) -> None:
        if interaction.user.bot:
            await self._safeReply(interaction, "Bots cannot join patrols.")
            return
        event = await self._clockInEngine.getSession(int(eventId))
        if not event:
            await self._safeReply(interaction, "This event session no longer exists.")
            return
        if str(event.get("status") or "").upper() != "OPEN":
            await self._safeReply(interaction, "This event is no longer open.")
            return
        if interaction.user.id == int(event.get("hostId") or 0):
            await self._safeReply(interaction, "You are the host of this event and cannot clock in as an attendee.")
            return
            
        if not self._isInHonorGuard(interaction.user):
            await self._safeReply(interaction, "Only members of the Honor Guard can join this event.")
            return
        
        attendees = await self._clockInEngine.listAttendees(int(eventId))
        if any(attendee.get("userId") == interaction.user.id and attendee.get("participantRole") != "HOST" for attendee in attendees):
            await self._safeReply(interaction, "You are already clocked in to this event.")
            # await self._clockInEngine.removeAttendee(int(eventId), interaction.user.id)
          	# await self._safeReply(interaction, "You have clocked out of this event.")       We will decide later if we will allow clock outs or not
         	# await self._refreshEventMessageFromInteraction(eventId, interaction)
            return
            
        memberGroup = _getMemberGroup(interaction.user)
        if memberGroup is None:
            await self._safeReply(interaction, "You do not have an Honor Guard rank role and cannot clock in.")
            return

        await self._clockInEngine.addAttendee(int(eventId), int(interaction.user.id), memberGroup=memberGroup, guildId=int(interaction.guild.id))
        await self._safeReply(interaction, "You have been added to this event.")
        await self._refreshEventMessageFromInteraction(eventId, interaction)

    async def handleEventDelete(self, interaction: discord.Interaction, eventId: int) -> None:
        event = await self._clockInEngine.getSession(int(eventId))
        if not event:
            await self._safeReply(interaction, "This event session no longer exists.")
            return
        if not await self._canManageEvent(interaction, event):
            await self._safeReply(interaction, "Only the event host and supervisors can delete this event.")
            return
        await self._clockInEngine.updateSessionStatus(int(eventId), "CANCELED")
        await self._safeReply(interaction, "Event deleted.")
        await self._refreshEventMessageFromInteraction(eventId, interaction)

    async def openEventManage(self, interaction: discord.Interaction, eventId: int) -> None:
        lock = self._eventLocks.setdefault(eventId, asyncio.Lock())
        async with lock:
            event = await self._clockInEngine.getSession(int(eventId))
            if not event:
                await self._safeReply(interaction, "This event session no longer exists.")
                return
            if not await self._canManageEvent(interaction, event):
                await self._safeReply(interaction, "Only the event host and supervisors can manage attendees.")
                return
            if str(event.get("status") or "").upper() != "OPEN":
                await self._safeReply(interaction, "This event is not open.")
                return
            submitRecord = _checkRecordByEventId(eventId, "SUBMIT")
            submit2Record = _checkRecordByEventId(eventId, "SUBMIT2")
            if submitRecord != None or submit2Record != None:
                await self._safeReply(interaction, f"This event is currently being submitted by {submitRecord.get('user').display_name if submitRecord else submit2Record.get('user').display_name} and cannot be managed until submission is closed.")
                return
            record = _checkRecordByEventId(eventId, "MANAGE")
            if record != None:
                await self._safeReply(interaction, f"This event is already being managed by {record.get('user').display_name}.")
                return
            attendees = await self._clockInEngine.listAttendees(int(eventId))
            if not attendees or len(attendees) == 1: # only Host Record
                await self._safeReply(interaction, "No attendees to manage.")
                return
            _saveRecord(interaction, interaction.user, int(eventId), "MANAGE")
            enbed = self._clockInAdapter.buildEventManageEmbed(event, attendees)
            await interactionRuntime.safeInteractionReply(
                interaction,
                embed=enbed,
                view=HonorGuardEventManageView(self, eventId, interaction.guild.id, interaction.user.id, attendees),
                ephemeral=True,
            )

    async def handleAssignAttendee(self, interaction: discord.Interaction, eventId: int, selectedAttendee: dict) -> None:
        event = await self._clockInEngine.getSession(int(eventId))
        if not event:
            await self._safeReply(interaction, "This event session no longer exists.")
            return
        if not await self._canManageEvent(interaction, event):
            await self._safeReply(interaction, "Only the event host and supervisors can edit co-hosts.")
            return
        if str(event.get("status") or "").upper() != "OPEN":
            await self._safeReply(interaction, "This event is not open.")
            return

        await self._clockInEngine.removeAttendee(int(eventId), selectedAttendee.get("userId"))
        await self._clockInEngine.addAttendee(int(eventId), int(selectedAttendee.get("userId")), participantRole="ATTENDEE", memberGroup=selectedAttendee.get("memberGroup"), guildId=int(interaction.guild.id))

    async def handleAssignCohost(self, interaction: discord.Interaction, eventId: int, selectedAttendee: dict) -> None:
        event = await self._clockInEngine.getSession(int(eventId))
        if not event:
            await self._safeReply(interaction, "This event session no longer exists.")
            return
        if not await self._canManageEvent(interaction, event):
            await self._safeReply(interaction, "Only the event host and supervisors can edit co-hosts.")
            return
        if str(event.get("status") or "").upper() != "OPEN":
            await self._safeReply(interaction, "This event is not open.")
            return

        await self._clockInEngine.removeAttendee(int(eventId), selectedAttendee.get("userId"))
        await self._clockInEngine.addAttendee(int(eventId), int(selectedAttendee.get("userId")), participantRole="COHOST", memberGroup=selectedAttendee.get("memberGroup"), guildId=int(interaction.guild.id))

    async def handleAssignSupervisor(self, interaction: discord.Interaction, eventId: int, selectedAttendee: dict) -> None:
        event = await self._clockInEngine.getSession(int(eventId))
        if not event:
            await self._safeReply(interaction, "This event session no longer exists.")
            return
        if not await self._canManageEvent(interaction, event):
            await self._safeReply(interaction, "Only the event host and supervisors can edit co-hosts.")
            return
        if str(event.get("status") or "").upper() != "OPEN":
            await self._safeReply(interaction, "This event is not open.")
            return

        await self._clockInEngine.removeAttendee(int(eventId), selectedAttendee.get("userId"))
        await self._clockInEngine.addAttendee(int(eventId), int(selectedAttendee.get("userId")), memberGroup=selectedAttendee.get("memberGroup"), participantRole="SUPERVISOR", guildId=int(interaction.guild.id))

    async def handleRemoveAttendee(self, interaction: discord.Interaction, eventId: int, selectedAttendee: dict) -> None:
        event = await self._clockInEngine.getSession(int(eventId))
        if not event:
            await self._safeReply(interaction, "This event session no longer exists.")
            return
        if not await self._canManageEvent(interaction, event):
            await self._safeReply(interaction, "Only the event host and supervisors can remove attendees.")
            return
        if str(event.get("status") or "").upper() != "OPEN":
            await self._safeReply(interaction, "This event is not open.")
            return

        await self._clockInEngine.removeAttendee(int(eventId), selectedAttendee.get("userId"))

    async def closeEventManage(self, eventId: int) -> None:
        event = await self._clockInEngine.getSession(int(eventId))
        if not event:
            return
        record = _getRecordByEventId(eventId, "MANAGE")
        if record:
            try:
                await record.get("interaction").delete_original_response()
            except:
                pass
        await self._updateEventMessage(eventId)
        _deleteRecord(eventId, "MANAGE")

    async def handleEditPoints(self, interaction: discord.Interaction, eventId: int, attendee: dict, new_points: float, type: str) -> None:
        event = await self._clockInEngine.getSession(int(eventId))
        if not event:
            await self._safeReply(interaction, "This event session no longer exists.")
            return
        if str(event.get("status") or "").upper() != "FINISHED":
            await self._safeReply(interaction, "This event is not finished.")
            return

        if type == "QUOTA":
            points = honorGuardService.HonorGuardPointDeltas(quotaPoints=new_points, eventPoints=attendee.get("eventPoints", 0))
        else:
            points = honorGuardService.HonorGuardPointDeltas(quotaPoints=attendee.get("quotaPoints", 0), eventPoints=new_points)
        await interaction.response.defer(ephemeral=True, thinking=False)
        await honorGuardService.updateAttendeePoints(recordId=int(attendee.get("recordId") or 0), points=points)

    async def handleEditAllPoints(self, interaction: discord.Interaction, eventId: int, new_points: float, type: str) -> None:
        event = await self._clockInEngine.getSession(int(eventId))
        if not event:
            await self._safeReply(interaction, "This event session no longer exists.")
            return
        if str(event.get("status") or "").upper() != "FINISHED":
            await self._safeReply(interaction, "This event is not finished.")
            return

        await interaction.response.defer(ephemeral=True, thinking=False)
        if type == "QUOTA":
            for attendee in await self._clockInEngine.listAttendees(int(eventId)):
                if attendee.get("memberGroup") == "OFFICER":
                    continue
                if attendee.get("participantRole") != "ATTENDEE":
                    continue
                points = honorGuardService.HonorGuardPointDeltas(quotaPoints=new_points, eventPoints=attendee.get("eventPoints"))
                await honorGuardService.updateAttendeePoints(recordId=int(attendee.get("recordId") or 0), points=points)
        else:
            for attendee in await self._clockInEngine.listAttendees(int(eventId)):
                if attendee.get("memberGroup") == "OFFICER":
                    continue
                if attendee.get("participantRole") != "ATTENDEE":
                    continue
                points = honorGuardService.HonorGuardPointDeltas(quotaPoints=attendee.get("quotaPoints"), eventPoints=new_points)
                await honorGuardService.updateAttendeePoints(recordId=int(attendee.get("recordId") or 0), points=points)
	
    async def finishEvent(self, interaction: discord.Interaction, eventId: int) -> None:
        lock = self._eventLocks.setdefault(eventId, asyncio.Lock())
        async with lock:
            event = await self._clockInEngine.getSession(int(eventId))
            if not event:
                await self._safeReply(interaction, "This event session no longer exists.")
                return
            if not await self._canManageEvent(interaction, event):
                await self._safeReply(interaction, "Only the event host and supervisors can submit this event.")
                return
            if str(event.get("status") or "").upper() != "OPEN":
                await self._safeReply(interaction, "This event is not open.")
                return
            manageRecord = _checkRecordByEventId(eventId, "MANAGE")
            if manageRecord != None:
                await self._safeReply(interaction, f"This event is currently being managed by {manageRecord.get('user').display_name} and cannot be submitted until management is closed.")
                return
            record = _checkRecordByEventId(eventId, "SUBMIT")
            record2 = _checkRecordByEventId(eventId, "SUBMIT2")
            if record != None or record2 != None:
                await self._safeReply(interaction, f"This event is already being submitted by {record.get('user').display_name if record else record2.get('user').display_name}.")
                return
            
            attendees = await self._clockInEngine.listAttendees(int(eventId))
            normalAttendees = [attendee for attendee in attendees if attendee.get("participantRole") == "ATTENDEE"]
            if not normalAttendees or len(normalAttendees) == 0: # only Host Record
                await self._safeReply(interaction, "No attendees found for this event.")
                return
            
            if event.get("eventType") in ["jge", "ncoe"]:
                embed = honorGuardRendering.buildHonorGuardGradingEmbed(event, interaction.user.id, normalAttendees, 0)
                await self._safeReply(interaction, embed=embed, view=HonorGuardGradingView(self, eventId, interaction.user.id, normalAttendees))
                _saveRecord(interaction, interaction.user, int(eventId), "SUBMIT")
                await self._clockInEngine.updateSessionStatus(int(eventId), "GRADING")
                return
            else:
                await interaction.response.send_modal(HonorGuardEventFinishModal(self, event))
                _saveRecord(interaction, interaction.user, int(eventId), "SUBMIT")
                await self._clockInEngine.updateSessionStatus(int(eventId), "FINISHED")

            await self._updateEventMessage(eventId)

    async def finishGrading(self, interaction: discord.Interaction, eventId: int) -> None:
        event = await self._clockInEngine.getSession(int(eventId))
        if not event:
            await self._safeReply(interaction, "This event session no longer exists.")
            return
        if str(event.get("status") or "").upper() != "GRADING":
            await self._safeReply(interaction, "This event is not in grading status.")
            return
        record = _checkRecordByEventId(eventId, "SUBMIT")
        if not record:
            await self._safeReply(interaction, "No grading submission found.")
            return
        if record.get("user").id != interaction.user.id:
            await self._safeReply(interaction, f"Wrong grading submission found.")
            return

        _deleteRecord(eventId, "SUBMIT")

        attendees = await self._clockInEngine.listAttendees(int(eventId))
        normalAttendees = [attendee for attendee in attendees if attendee.get("participantRole") == "ATTENDEE"]
        for attendee in attendees:
            points = honorGuardService.calculatePointDeltas(configModule=config, memberGroup=attendee.get("memberGroup"), eventType=event.get("eventType"), participantRole=attendee.get("participantRole"), attendeeCount=len(normalAttendees), durationMinutes=0, passed=attendee.get("examGrade", "") == "PASS")
            await honorGuardService.updateAttendeePoints(recordId=int(attendee.get("recordId")), points=points)

        attendees = await self._clockInEngine.listAttendees(int(eventId))

        _saveRecord(interaction, interaction.user, int(eventId), "SUBMIT2")
        enbed = self._clockInAdapter.buildSubmitEmbed(event, attendees)
        view = HonorGuardEventSubmitView(self, interaction.user.id, eventId, interaction.guild.id, attendees)
        await interactionRuntime.safeInteractionReply(
            interaction,
            embed=enbed,
            view=view,
            ephemeral=True,
        )
        await self._clockInEngine.updateSessionStatus(int(eventId), "FINISHED")
        await self._updateEventMessage(eventId)

    async def timeoutTimeModal(self, eventId: int) -> None:
        event = await self._clockInEngine.getSession(int(eventId))
        if not event:
            return
        record = _getRecordByEventId(eventId, "SUBMIT")
        if record:
            await self._clockInEngine.updateSessionStatus(int(eventId), "OPEN")
            await self._updateEventMessage(eventId)
            _deleteRecord(eventId, "SUBMIT")

    async def timeoutGradingView(self, eventId: int) -> None:
        event = await self._clockInEngine.getSession(int(eventId))
        if not event:
            return
        record = _getRecordByEventId(eventId, "SUBMIT")
        if record:
            try:
                await record.get("interaction").delete_original_response()
            except:
                pass
            await self._clockInEngine.updateSessionStatus(int(eventId), "OPEN")
            await self._updateEventMessage(eventId)
            _deleteRecord(eventId, "SUBMIT")

    async def openSubmitEvent(self, interaction: discord.Interaction, eventId: int, durationMinutes: int) -> None:
        lock = self._eventLocks.setdefault(eventId, asyncio.Lock())
        async with lock:
            event = await self._clockInEngine.getSession(int(eventId))
            if not event:
                await self._safeReply(interaction, "This event session no longer exists.")
                return
            if not await self._canManageEvent(interaction, event):
                await self._safeReply(interaction, "Only the event host and supervisors can submit this event.")
                return
            if str(event.get("status") or "").upper() != "FINISHED":
                await self._safeReply(interaction, "This event is not finished.")
                return
            record2 = _checkRecordByEventId(eventId, "SUBMIT2")
            if record2 != None:
                await self._safeReply(interaction, f"This event is already being submitted by {record2.get('user').display_name}.")
                return
            record = _checkRecordByEventId(eventId, "SUBMIT")
            if not record:
                await self._safeReply(interaction, "No time submission found.")
                return
            if record.get("user").id != interaction.user.id:
                await self._safeReply(interaction, f"Wrong time submission found.")
                return

            _deleteRecord(eventId, "SUBMIT")

            await honorGuardService.setEventRecordDuration(int(eventId), durationMinutes)
            
            attendees = await self._clockInEngine.listAttendees(int(eventId))
            for attendee in attendees:
                points = honorGuardService.calculatePointDeltas(configModule=config, memberGroup=attendee.get("memberGroup"), eventType=event.get("eventType"), participantRole=attendee.get("participantRole"), attendeeCount=len(attendees), durationMinutes=durationMinutes)
                await honorGuardService.updateAttendeePoints(recordId=int(attendee.get("recordId")), points=points)

            attendees = await self._clockInEngine.listAttendees(int(eventId))

            _saveRecord(interaction, interaction.user, int(eventId), "SUBMIT2")
            enbed = self._clockInAdapter.buildSubmitEmbed(event, attendees)
            view = HonorGuardEventSubmitView(self, interaction.user.id, eventId, interaction.guild.id, attendees)
            await interactionRuntime.safeInteractionReply(
                interaction,
                embed=enbed,
                view=view,
                ephemeral=True,
            )
            await self._clockInEngine.updateSessionStatus(int(eventId), "FINISHED")
            await self._updateEventMessage(eventId)

    async def closeEventSubmit(self, eventId: int) -> None:
        event = await self._clockInEngine.getSession(int(eventId))
        if not event:
            return
        record = _getRecordByEventId(eventId, "SUBMIT2")
        if record:
            try:
                await record.get("interaction").delete_original_response()
            except:
                pass
            await self._clockInEngine.updateSessionStatus(int(eventId), "OPEN")
            await self._updateEventMessage(eventId)
            _deleteRecord(eventId, "SUBMIT2")

    async def handleEventSubmit(
        self,
        interaction: discord.Interaction,
        eventId: int,
    ) -> None:
        lock = self._eventLocks.setdefault(eventId, asyncio.Lock())
        async with lock:
            # Guard against double-finalize clicks while one reviewer is still
            # uploading evidence / posting the review message.
            event = await self._clockInEngine.getSession(int(eventId))
            if not event:
                await self._safeReply(interaction, "This event session no longer exists.")
                return
            if not await self._canManageEvent(interaction, event):
                await self._safeReply(interaction, "Only the event host and supervisors can submit this event.")
                return
            if str(event.get("status") or "").upper() != "FINISHED":
                await self._safeReply(interaction, "This event is not finished.")
                return

            # await self._safeReply(interaction, "Not wired yet")
            # return

            allAttendees = await self._clockInEngine.listAttendees(int(eventId))

            evidenceChannelId = int(
                getattr(
                    config,
                    "honorGuardEventEvidenceChannelId",
                    getattr(config, "honorGuardChannelId", 0),
                )
                or 0
            )
            evidenceChannel = await self._resolveConfiguredMessageChannel(
                interaction.guild,
                evidenceChannelId,
            )
            if evidenceChannel is None:
                evidenceChannel = interaction.channel

            await self._safeReply(interaction, f"Upload two event screenshots in <#{evidenceChannel.id}> within 3 minutes.")
            # We reuse the evidence collector so solo/group flows behave the same.
            evidenceMessage = await self._collectTwoImageEvidenceMessage(
                channel=evidenceChannel,
                userId=interaction.user.id,
            )
            if evidenceMessage is None:
                await interaction.followup.send(
                    "Timed out waiting for two image screenshots. Event is still open.",
                    ephemeral=True,
                )
                return
            imageUrls = []
            imageFiles: list[discord.File] = []
            for attachment in evidenceMessage.attachments:
                if not _isImageAttachment(attachment):
                    continue
                try:
                    imageFiles.append(await attachment.to_file())
                    imageUrls.append(attachment.url)
                except (discord.HTTPException, OSError):
                    continue
            if len(imageFiles) < 2:
                await interaction.followup.send(
                    "I could not copy both sentry screenshots for review. Please try again.",
                    ephemeral=True,
                )
                return
            submissionId = await honorGuardService.createEventSubmission(
                eventId=int(eventId),
                event=event,
                submitterId=int(interaction.user.id),
                imageUrls=imageUrls,
                evidenceMessageUrl=evidenceMessage.jump_url
            )
            submission = await honorGuardService.getEventSubmission(submissionId)
            if not submission:
                await interaction.followup.send(
                    "Failed to create event submission.",
                    ephemeral=True,
                )
                return

            embed = honorGuardRendering.buildEventReviewEmbed(submission, event, allAttendees)
            reviewView = HonorGuardEventReviewView(self, submissionId)
            reviewMessage = await self._postHonorGuardForReview(
                guild=interaction.guild,
                fallbackChannel=interaction.channel,
                embed=embed,
                view=reviewView,
                files=imageFiles,
                reviewChannelId=int(getattr(config, "honorGuardReviewChannelId", 0) or 0),
            )
            if not reviewMessage:
                await interaction.followup.send(
                    "Could not post this submission for review.",
                    ephemeral=True,
                )
                return

            await honorGuardService.setSubmissionMessageId(
                submissionId,
                reviewMessage.id,
            )
            await self._clockInEngine.updateSessionStatus(int(eventId), "SUBMITTED")

            await self._deleteEventClockinMessage(event)

            await interaction.followup.send(
                "Event submitted for review.",
                ephemeral=True,
            )
            record = _getRecordByEventId(eventId, "SUBMIT2")
            if record:
                await record.get("interaction").delete_original_response()
            _deleteRecord(eventId, "SUBMIT2")

    async def _postHonorGuardForReview(
        self,
        *,
        guild: discord.Guild,
        fallbackChannel: Optional[discord.abc.Messageable],
        embed: discord.Embed,
        view: discord.ui.View,
        files: Optional[list[discord.File]] = None,
        reviewChannelId: Optional[int] = None,
    ) -> Optional[discord.Message]:
        channel = await self._resolveReviewChannel(
            guild,
            fallbackChannel,
            channelId=reviewChannelId,
        )
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return None
        mention = _reviewerMention()
        content = mention or None
        allowedMentions = discord.AllowedMentions(roles=True, users=True)
        return await interactionRuntime.safeChannelSend(
            channel,
            content=content,
            embed=embed,
            view=view,
            files=files or [],
            allowed_mentions=allowedMentions,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HonorGuardCog(bot))