from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

import config
from db.sqlite import fetchAll, fetchOne
from features.staff.sessions import views as sessionViews
from runtime import backups as backupRuntime
from runtime import commandScopes as runtimeCommandScopes
from runtime import interaction as interactionRuntime
from runtime import viewBases as runtimeViewBases


def _safeInt(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _allowedOpsUserIds(configModule: Any) -> set[int]:
    out: set[int] = set()
    for raw in (getattr(configModule, "opsAllowedUserIds", []) or []):
        parsed = _safeInt(raw)
        if parsed > 0:
            out.add(parsed)
    if not out:
        fallback = _safeInt(getattr(configModule, "errorMirrorUserId", 0))
        if fallback > 0:
            out.add(fallback)
    return out


def _formatShortJson(value: object, *, maxLen: int = 1000) -> str:
    text = str(value or "").strip()
    if len(text) <= maxLen:
        return text or "{}"
    return f"{text[: maxLen - 3]}..."


def _auditExportColumns() -> list[str]:
    return [
        "eventId",
        "createdAt",
        "guildId",
        "actorId",
        "source",
        "action",
        "severity",
        "targetType",
        "targetId",
        "authorizedBy",
        "detailsJson",
    ]


class FeatureFlagModal(discord.ui.Modal):
    featureKeyInput = discord.ui.TextInput(
        label="Feature key",
        placeholder="e.g. recruitment, ribbons, schedule-event",
        required=True,
        max_length=80,
    )
    enabledInput = discord.ui.TextInput(
        label="Enabled? (true/false)",
        placeholder="true or false",
        required=True,
        max_length=5,
    )
    noteInput = discord.ui.TextInput(
        label="Note (optional)",
        placeholder="Why this changed",
        required=False,
        max_length=200,
    )

    def __init__(self, *, cog: "OpsCog", guildId: int, actorId: int):
        self.cog = cog
        self.guildId = int(guildId)
        self.actorId = int(actorId)
        super().__init__(title="Toggle Feature Flag", timeout=300)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not self.cog.isAllowedUser(int(getattr(interaction.user, "id", 0) or 0)):
            await interactionRuntime.safeInteractionReply(
                interaction,
                content="You are not allowed to use this panel.",
                ephemeral=True,
            )
            return

        featureKey = str(self.featureKeyInput.value or "").strip().lower()
        enabledText = str(self.enabledInput.value or "").strip().lower()
        if not featureKey:
            await interactionRuntime.safeInteractionReply(
                interaction,
                content="Feature key cannot be empty.",
                ephemeral=True,
            )
            return
        if enabledText not in {"true", "false", "on", "off", "1", "0"}:
            await interactionRuntime.safeInteractionReply(
                interaction,
                content="Enabled must be true/false.",
                ephemeral=True,
            )
            return
        enabled = enabledText in {"true", "on", "1"}

        await self.cog.featureFlags.setFlag(
            guildId=self.guildId,
            featureKey=featureKey,
            enabled=enabled,
            actorId=self.actorId,
            note=str(self.noteInput.value or "").strip(),
        )
        await self.cog.auditStream.logEvent(
            source="ops",
            action="feature flag updated",
            guildId=self.guildId,
            actorId=self.actorId,
            targetType="feature_flag",
            targetId=featureKey,
            severity="INFO",
            details={"enabled": enabled, "note": str(self.noteInput.value or "").strip()},
            authorizedBy=f"user:{self.actorId}",
            postToDiscord=True,
        )
        await interactionRuntime.safeInteractionReply(
            interaction,
            content=f"Feature `{featureKey}` set to `{enabled}` for guild `{self.guildId}`.",
            ephemeral=True,
        )


class RetryDeadModal(discord.ui.Modal):
    jobIdInput = discord.ui.TextInput(
        label="Dead job ID",
        placeholder="Numeric retry job ID",
        required=True,
        max_length=24,
    )

    def __init__(self, *, viewRef: "OpsControlView"):
        self.viewRef = viewRef
        super().__init__(title="Retry Dead Job", timeout=300)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not self.viewRef.cog.isAllowedUser(int(getattr(interaction.user, "id", 0) or 0)):
            await interactionRuntime.safeInteractionReply(
                interaction,
                content="You are not allowed to use this panel.",
                ephemeral=True,
            )
            return
        jobId = _safeInt(str(self.jobIdInput.value or "").strip())
        if jobId <= 0:
            await interactionRuntime.safeInteractionReply(
                interaction,
                content="Invalid job ID.",
                ephemeral=True,
            )
            return
        ok = await self.viewRef.cog.retryQueue.retryDeadJob(jobId)
        if not ok:
            await interactionRuntime.safeInteractionReply(
                interaction,
                content=f"Job `{jobId}` was not found.",
                ephemeral=True,
            )
            return
        await self.viewRef.cog.auditStream.logEvent(
            source="ops",
            action="retry dead-letter job",
            guildId=self.viewRef.guildId,
            actorId=int(getattr(interaction.user, "id", 0) or 0),
            targetType="retry_job",
            targetId=str(jobId),
            severity="INFO",
            details={"jobId": jobId},
            authorizedBy=f"user:{int(getattr(interaction.user, 'id', 0) or 0)}",
            postToDiscord=True,
        )
        await interactionRuntime.safeInteractionReply(
            interaction,
            content=f"Queued retry for dead-letter job `{jobId}`.",
            ephemeral=True,
        )


class OpsControlView(runtimeViewBases.OwnerLockedView):
    def __init__(self, *, cog: "OpsCog", ownerUserId: int, guildId: int):
        super().__init__(
            openerId=ownerUserId,
            timeout=900,
            ownerMessage="This ops panel is not yours.",
        )
        self.cog = cog
        self.ownerUserId = int(ownerUserId)
        self.guildId = int(guildId)

    async def buildEmbed(self) -> discord.Embed:
        metrics = await self.cog.metricsExporter.snapshot()
        retryStats = await self.cog.retryQueue.getStats()
        webhookStats = self.cog.webhookHealthWatcher.getStats()
        featureRows = await self.cog.featureFlags.listFlags(self.guildId)
        dashboardCounts = await self.cog._fetchDashboardCounts(self.guildId)
        bgClaimLines = await self.cog._buildBgClaimLines(self.guildId)

        embed = discord.Embed(
            title="Ops Panel",
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
            description=(
                f"Owner tools for <@{self.ownerUserId}> in guild `{self.guildId}`.\n"
                "Use buttons below for flags, backups, retry queue, health, and audit exports."
            ),
        )
        runtimeSection = metrics.get("runtime", {}) if isinstance(metrics, dict) else {}
        processSection = runtimeSection.get("process", {}) if isinstance(runtimeSection, dict) else {}
        embed.add_field(
            name="Runtime Snapshot",
            value=(
                f"uptime: `{int(metrics.get('uptimeSec', 0) or 0)}s`\n"
                f"guilds: `{int((metrics.get('bot', {}) or {}).get('guildCount', 0) or 0)}`\n"
                f"latency: `{float((metrics.get('bot', {}) or {}).get('latencyMs', 0.0) or 0.0):.2f}ms`"
            ),
            inline=True,
        )
        embed.add_field(
            name="Process Resources",
            value=(
                f"cpu(avg): `{processSection.get('cpuPercent', 'n/a')}`\n"
                f"memory(rss): `{processSection.get('rss', 'n/a')}`\n"
                f"threads: `{processSection.get('threads', 'n/a')}`"
            ),
            inline=True,
        )
        retryByStatus = retryStats.get("byStatus", {}) if isinstance(retryStats, dict) else {}
        embed.add_field(
            name="Retry Queue Status",
            value=(
                f"pending: `{int(retryByStatus.get('PENDING', 0) or 0)}`\n"
                f"failed: `{int(retryByStatus.get('FAILED', 0) or 0)}`\n"
                f"dead: `{int(retryByStatus.get('DEAD', 0) or 0)}`"
            ),
            inline=True,
        )
        webhookSummary = webhookStats.get("summary", {}) if isinstance(webhookStats, dict) else {}
        embed.add_field(
            name="Webhook Health",
            value=(
                f"checked: `{int(webhookSummary.get('checked', 0) or 0)}`\n"
                f"missing: `{int(webhookSummary.get('missing', 0) or 0)}`\n"
                f"errors: `{int(webhookSummary.get('errors', 0) or 0)}`"
            ),
            inline=True,
        )
        embed.add_field(
            name="Live Activity",
            value=(
                f"sessions: `{dashboardCounts.get('openSessions', 0)}`\n"
                f"bg queues: `{dashboardCounts.get('bgQueues', 0)}`\n"
                f"events: `{dashboardCounts.get('events', 0)}`"
            ),
            inline=True,
        )
        embed.add_field(
            name="Pending Reviews",
            value=(
                f"applications: `{dashboardCounts.get('applications', 0)}`\n"
                f"ribbons: `{dashboardCounts.get('ribbons', 0)}`\n"
                f"projects: `{dashboardCounts.get('projects', 0)}`"
            ),
            inline=True,
        )
        embed.add_field(
            name="Staff Queues",
            value=(
                f"orbat: `{dashboardCounts.get('orbatRequests', 0)}`\n"
                f"loa: `{dashboardCounts.get('loaRequests', 0)}`\n"
                f"payments: `{dashboardCounts.get('paymentRequests', 0)}`"
            ),
            inline=True,
        )
        embed.add_field(
            name="BG Claims",
            value="\n".join(bgClaimLines) if bgClaimLines else "(none claimed)",
            inline=False,
        )
        embed.add_field(
            name="Feature Flags",
            value=f"overrides: `{len(featureRows)}`",
            inline=True,
        )
        embed.add_field(
            name="Plugins",
            value=f"loaded: `{len(self.cog.pluginRegistry.listPlugins())}`",
            inline=True,
        )
        embed.add_field(
            name="Intel",
            value=(
                f"notes: `{dashboardCounts.get('notes', 0)}`\n"
                f"federation links: `{dashboardCounts.get('federationLinks', 0)}`"
            ),
            inline=True,
        )
        embed.set_footer(text="Tip: Export Audit downloads both CSV and JSON snapshots.")
        return embed

    async def refreshPanel(self, interaction: discord.Interaction) -> None:
        embed = await self.buildEmbed()
        await runtimeViewBases.safeRefreshInteractionMessage(
            interaction,
            embed=embed,
            view=self,
        )

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary, row=0)
    async def refreshBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.refreshPanel(interaction)

    @discord.ui.button(label="Export Audit", style=discord.ButtonStyle.secondary, row=0)
    async def exportAuditBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        rows = await self.cog.auditStream.listEvents(limit=500, guildId=self.guildId)
        if not rows:
            await interactionRuntime.safeInteractionReply(
                interaction,
                content="No audit events found for this guild.",
                ephemeral=True,
            )
            return

        columns = _auditExportColumns()
        jsonRows: list[dict[str, Any]] = []
        for row in rows:
            jsonRows.append({col: row.get(col) for col in columns})

        csvBuffer = io.StringIO()
        writer = csv.DictWriter(csvBuffer, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in jsonRows:
            writer.writerow(row)

        csvBytes = csvBuffer.getvalue().encode("utf-8")
        jsonBytes = json.dumps(jsonRows, ensure_ascii=False, indent=2).encode("utf-8")
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        csvFile = discord.File(
            io.BytesIO(csvBytes),
            filename=f"audit_{self.guildId}_{timestamp}.csv",
        )
        jsonFile = discord.File(
            io.BytesIO(jsonBytes),
            filename=f"audit_{self.guildId}_{timestamp}.json",
        )

        payload = {
            "content": f"Audit export ready (`{len(jsonRows)}` events).",
            "files": [csvFile, jsonFile],
            "ephemeral": True,
        }
        await self.cog.auditStream.logEvent(
            source="ops",
            action="audit export generated",
            guildId=self.guildId,
            actorId=int(getattr(interaction.user, "id", 0) or 0),
            targetType="audit_export",
            targetId=f"guild:{self.guildId}",
            severity="INFO",
            details={"events": len(jsonRows), "formats": ["csv", "json"]},
            authorizedBy=f"user:{int(getattr(interaction.user, 'id', 0) or 0)}",
            postToDiscord=False,
        )
        try:
            if interaction.response.is_done():
                await interaction.followup.send(**payload)
            else:
                await interaction.response.send_message(**payload)
        except (discord.NotFound, discord.HTTPException):
            await interactionRuntime.safeInteractionReply(
                interaction,
                content="Audit export failed to send. Please try again.",
                ephemeral=True,
            )

    @discord.ui.button(label="Toggle Feature", style=discord.ButtonStyle.secondary, row=0)
    async def toggleFeatureBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        modal = FeatureFlagModal(
            cog=self.cog,
            guildId=self.guildId,
            actorId=int(getattr(interaction.user, "id", 0) or 0),
        )
        await interactionRuntime.safeInteractionSendModal(interaction, modal)

    @discord.ui.button(label="List Flags", style=discord.ButtonStyle.secondary, row=0)
    async def listFlagsBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        rows = await self.cog.featureFlags.listFlags(self.guildId)
        if not rows:
            await interactionRuntime.safeInteractionReply(
                interaction,
                content="No feature flag overrides exist for this guild.",
                ephemeral=True,
            )
            return
        lines = [
            f"`{row.get('featureKey')}` = `{bool(int(row.get('enabled') or 0))}`"
            for row in rows[:30]
        ]
        await interactionRuntime.safeInteractionReply(
            interaction,
            content="Feature flags:\n" + "\n".join(lines),
            ephemeral=True,
        )

    @discord.ui.button(label="Backup Now", style=discord.ButtonStyle.success, row=1)
    async def backupNowBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interactionRuntime.safeInteractionDefer(interaction, ephemeral=True)
        backupPath = await backupRuntime.createBackup(self.cog.config, label="ops")
        await self.cog.auditStream.logEvent(
            source="ops",
            action="database backup created",
            guildId=self.guildId,
            actorId=int(getattr(interaction.user, "id", 0) or 0),
            targetType="backup",
            targetId=backupPath.name,
            severity="INFO",
            details={"path": str(backupPath)},
            authorizedBy=f"user:{int(getattr(interaction.user, 'id', 0) or 0)}",
            postToDiscord=True,
        )
        await interactionRuntime.safeInteractionReply(
            interaction,
            content=f"Backup created: `{backupPath.name}`",
            ephemeral=True,
        )

    @discord.ui.button(label="Backups", style=discord.ButtonStyle.secondary, row=1)
    async def listBackupsBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        backups = await backupRuntime.listBackups(self.cog.config, limit=15)
        if not backups:
            await interactionRuntime.safeInteractionReply(
                interaction,
                content="No backups found.",
                ephemeral=True,
            )
            return
        lines = [f"- `{path.name}`" for path in backups]
        await interactionRuntime.safeInteractionReply(
            interaction,
            content="Available backups:\n" + "\n".join(lines),
            ephemeral=True,
        )

    @discord.ui.button(label="Retry Stats", style=discord.ButtonStyle.secondary, row=2)
    async def retryStatsBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        stats = await self.cog.retryQueue.getStats()
        deadJobs = await self.cog.retryQueue.listDeadJobs(limit=10)
        deadLines = [
            f"`{row.get('jobId')}` `{row.get('jobType')}` attempts `{row.get('attempts')}/{row.get('maxAttempts')}`"
            for row in deadJobs
        ]
        payload = (
            f"Retry stats: `{_formatShortJson(stats, maxLen=900)}`\n"
            f"Dead jobs:\n{chr(10).join(deadLines) if deadLines else '(none)'}"
        )
        await interactionRuntime.safeInteractionReply(
            interaction,
            content=payload,
            ephemeral=True,
        )

    @discord.ui.button(label="Retry Dead Job", style=discord.ButtonStyle.secondary, row=2)
    async def retryDeadBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interactionRuntime.safeInteractionSendModal(interaction, RetryDeadModal(viewRef=self))

    @discord.ui.button(label="Run Health Check", style=discord.ButtonStyle.secondary, row=2)
    async def healthCheckBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interactionRuntime.safeInteractionDefer(interaction, ephemeral=True)
        summary = await self.cog.webhookHealthWatcher.runCheck()
        await self.cog.auditStream.logEvent(
            source="ops",
            action="manual webhook health check",
            guildId=self.guildId,
            actorId=int(getattr(interaction.user, "id", 0) or 0),
            targetType="webhook-health",
            targetId="manual",
            severity="INFO",
            details=summary,
            authorizedBy=f"user:{int(getattr(interaction.user, 'id', 0) or 0)}",
            postToDiscord=False,
        )
        await interactionRuntime.safeInteractionReply(
            interaction,
            content=f"Webhook health check summary: `{summary}`",
            ephemeral=True,
        )

    @discord.ui.button(label="Audit Tail", style=discord.ButtonStyle.secondary, row=3)
    async def auditTailBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        rows = await self.cog.auditStream.tail(limit=15, guildId=self.guildId)
        if not rows:
            await interactionRuntime.safeInteractionReply(
                interaction,
                content="No audit events found for this guild.",
                ephemeral=True,
            )
            return
        lines = []
        for row in rows:
            lines.append(
                f"`{row.get('eventId')}` {row.get('severity', 'INFO')} `{row.get('source', '')}` - {row.get('action', '')}"
            )
        await interactionRuntime.safeInteractionReply(
            interaction,
            content="Recent audit events:\n" + "\n".join(lines[:15]),
            ephemeral=True,
        )


class OpsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config = config
        self.allowedUserIds = _allowedOpsUserIds(self.config)

        runtimeServices = getattr(bot, "runtimeServices", {}) or {}
        self.featureFlags = runtimeServices.get("featureFlags")
        self.pluginRegistry = runtimeServices.get("pluginRegistry")
        self.retryQueue = runtimeServices.get("retryQueue")
        self.auditStream = runtimeServices.get("auditStream")
        self.metricsExporter = runtimeServices.get("metricsExporter")
        self.webhookHealthWatcher = runtimeServices.get("webhookHealthWatcher")
        missing = [
            name
            for name, value in {
                "featureFlags": self.featureFlags,
                "pluginRegistry": self.pluginRegistry,
                "retryQueue": self.retryQueue,
                "auditStream": self.auditStream,
                "metricsExporter": self.metricsExporter,
                "webhookHealthWatcher": self.webhookHealthWatcher,
            }.items()
            if value is None
        ]
        if missing:
            raise RuntimeError(f"OpsCog missing runtime services: {', '.join(missing)}")

    async def _fetchDashboardCounts(self, guildId: int) -> dict[str, int]:
        safeGuildId = int(guildId or 0)
        rows = await fetchOne(
            """
            SELECT
                (SELECT COUNT(*) FROM sessions WHERE guildId = ? AND status = 'OPEN') AS openSessions,
                (
                    SELECT COUNT(DISTINCT s.sessionId)
                    FROM sessions s
                    JOIN attendees a ON a.sessionId = s.sessionId
                    WHERE s.guildId = ? AND a.bgStatus = 'PENDING'
                ) AS bgQueues,
                (SELECT COUNT(*) FROM division_applications WHERE guildId = ? AND status IN ('PENDING', 'NEEDS_INFO')) AS applications,
                (
                    SELECT COUNT(*)
                    FROM ribbon_requests
                    WHERE guildId = ? AND status IN ('PENDING', 'NEEDS_INFO')
                ) AS ribbons,
                (SELECT COUNT(*) FROM scheduled_events WHERE guildId = ? AND status = 'ACTIVE') AS events,
                (SELECT COUNT(*) FROM department_projects WHERE guildId = ? AND status IN ('PENDING_APPROVAL', 'APPROVED', 'SUBMITTED')) AS projects,
                (SELECT COUNT(*) FROM orbat_requests WHERE guildId = ? AND status IN ('PENDING', 'NEEDS_INFO')) AS orbatRequests,
                (SELECT COUNT(*) FROM loa_requests WHERE guildId = ? AND status IN ('PENDING', 'NEEDS_INFO')) AS loaRequests,
                (SELECT COUNT(*) FROM anrd_payment_requests WHERE guildId = ? AND status IN ('PENDING', 'NEGOTIATING', 'NEEDS_INFO')) AS paymentRequests,
                (SELECT COUNT(*) FROM assistant_notes WHERE guildId = ?) AS notes,
                (SELECT COUNT(*) FROM guild_federation_links WHERE guildId = ?) AS federationLinks
            """,
            (
                safeGuildId,
                safeGuildId,
                safeGuildId,
                safeGuildId,
                safeGuildId,
                safeGuildId,
                safeGuildId,
                safeGuildId,
                safeGuildId,
                safeGuildId,
                safeGuildId,
            ),
        ) or {}
        return {key: _safeInt(rows.get(key, 0)) for key in rows.keys()}

    async def _buildBgClaimLines(self, guildId: int, *, limit: int = 8) -> list[str]:
        sessions = await fetchAll(
            """
            SELECT DISTINCT s.sessionId
            FROM sessions s
            JOIN attendees a ON a.sessionId = s.sessionId
            WHERE s.guildId = ? AND a.bgStatus = 'PENDING'
            ORDER BY s.sessionId DESC
            LIMIT 10
            """,
            (int(guildId or 0),),
        )
        lines: list[str] = []
        for row in sessions:
            sessionId = _safeInt(row.get("sessionId"))
            if sessionId <= 0:
                continue
            claims = sessionViews.getBgClaimsForSession(sessionId)
            if not claims:
                continue
            for targetUserId, ownerId in claims.items():
                lines.append(f"session `{sessionId}`: <@{targetUserId}> -> <@{ownerId}>")
                if len(lines) >= limit:
                    return lines
        return lines

    def isAllowedUser(self, userId: int) -> bool:
        return int(userId or 0) in self.allowedUserIds

    @app_commands.command(name="ops", description="Owner-only runtime operations panel.")
    async def ops(self, interaction: discord.Interaction) -> None:
        if not runtimeCommandScopes.isInteractionInTestGuild(interaction):
            await interactionRuntime.safeInteractionReply(
                interaction,
                content=runtimeCommandScopes.TEST_GUILD_ONLY_MESSAGE,
                ephemeral=True,
            )
            return
        safeUserId = int(getattr(interaction.user, "id", 0) or 0)
        if not self.isAllowedUser(safeUserId):
            await interactionRuntime.safeInteractionReply(
                interaction,
                content="You are not allowed to use this command.",
                ephemeral=True,
            )
            return
        if interaction.guild is None:
            await interactionRuntime.safeInteractionReply(
                interaction,
                content="Use this command in a server channel.",
                ephemeral=True,
            )
            return
        view = OpsControlView(cog=self, ownerUserId=safeUserId, guildId=int(interaction.guild.id))
        embed = await view.buildEmbed()
        await interactionRuntime.safeInteractionReply(
            interaction,
            embed=embed,
            view=view,
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(OpsCog(bot), guilds=runtimeCommandScopes.getTestGuildObjects())

