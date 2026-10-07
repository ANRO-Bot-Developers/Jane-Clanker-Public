from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from runtime.optionalImports import importOptionalModule

mcrcon = importOptionalModule("mcrcon")

from db.sqlite import execute, fetchOne, runWriteTransaction
from runtime import interaction as interactionRuntime
from runtime import permissions as runtimePermissions
from runtime.taskSupervisor import cancelTasks
from settings.community import (
    minecraftAllowedRoleIds,
    minecraftAuthenticationToken,
    minecraftCheckCooldownSeconds,
    minecraftRCONAddress,
    minecraftRCONPort,
    minecraftRCONTimeoutSeconds,
    minecraftServerMaxPlayersFallback,
)


log = logging.getLogger(__name__)
_playerCountPattern = re.compile(
    r"there\s+are\s+(?P<online>\d+)\s+of\s+a\s+max\s+of\s+(?P<maximum>\d+)",
    re.IGNORECASE,
)
_shortPlayerCountPattern = re.compile(r"(?P<online>\d+)\s*(?:/|of)\s*(?P<maximum>\d+)", re.IGNORECASE)
_tpsPattern = re.compile(r"(?:mean\s+)?tps\s*:\s*(?P<tps>\d+(?:\.\d+)?)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class MinecraftStatus:
    online: bool
    playerCount: int = 0
    maxPlayers: int = 0
    tps: str = "N/A"


def parsePlayerCount(response: object, *, fallbackMaximum: int) -> tuple[int, int]:
    text = str(response or "")
    match = _playerCountPattern.search(text) or _shortPlayerCountPattern.search(text)
    if match is None:
        return 0, max(1, int(fallbackMaximum or 1))
    return int(match.group("online")), max(1, int(match.group("maximum")))


def parseTps(response: object) -> str:
    matches = list(_tpsPattern.finditer(str(response or "")))
    if not matches:
        return "N/A"
    try:
        return f"{float(matches[-1].group('tps')):.2f}"
    except (TypeError, ValueError):
        return "N/A"


def buildEmbed(status: MinecraftStatus) -> discord.Embed:
    if status.online:
        stateLabel = "Online"
        color = discord.Color.green()
    else:
        stateLabel = "Offline"
        color = discord.Color.red()

    embed = discord.Embed(title="Minecraft Server Status", colour=color)
    embed.add_field(name="Current TPS", value=status.tps, inline=True)
    embed.add_field(
        name="Current player count",
        value=f"{status.playerCount}/{max(1, status.maxPlayers)}",
        inline=True,
    )
    embed.add_field(name="Online Status", value=stateLabel, inline=False)
    return embed


def canRunCommand(member: object) -> bool:
    return runtimePermissions.hasAnyRole(member, minecraftAllowedRoleIds)


class MinecraftCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.statusMessageId = 0
        self.statusChannelId = 0
        self.lastStatus = MinecraftStatus(
            online=False,
            maxPlayers=max(1, int(minecraftServerMaxPlayersFallback or 60)),
        )
        self.loopTask: asyncio.Task[None] | None = None

    async def cog_load(self) -> None:
        message = await self._resolveStoredMessage()
        if message is not None:
            self._startPolling()

    async def cog_unload(self) -> None:
        task = self.loopTask
        self.loopTask = None
        await cancelTasks(task)

    async def _safeEphemeral(self, interaction: discord.Interaction, message: str) -> None:
        await interactionRuntime.safeInteractionReply(
            interaction,
            content=message,
            ephemeral=True,
        )

    async def _storedStatusRow(self) -> dict[str, Any] | None:
        return await fetchOne(
            """
            SELECT statusMessageId, statusChannelId, lastPlayerCount, lastStatus
            FROM mcstatus
            ORDER BY rowid DESC
            LIMIT 1
            """
        )

    async def _replaceStoredStatus(self) -> None:
        async def _write(connection) -> None:
            await connection.execute("DELETE FROM mcstatus")
            await connection.execute(
                """
                INSERT INTO mcstatus
                (statusMessageId, statusChannelId, lastPlayerCount, lastStatus, lastMaintenanceDate)
                VALUES (?, ?, ?, ?, datetime('now'))
                """,
                (
                    self.statusMessageId,
                    self.statusChannelId,
                    self.lastStatus.playerCount,
                    "Online" if self.lastStatus.online else "Offline",
                ),
            )

        await runWriteTransaction(_write)

    async def _updateStoredStatus(self) -> None:
        if self.statusMessageId <= 0:
            return
        await execute(
            """
            UPDATE mcstatus
            SET lastPlayerCount = ?, lastStatus = ?
            WHERE statusMessageId = ?
            """,
            (
                self.lastStatus.playerCount,
                "Online" if self.lastStatus.online else "Offline",
                self.statusMessageId,
            ),
        )

    async def _clearStoredStatus(self) -> None:
        await execute("DELETE FROM mcstatus")

    async def _resolveStoredMessage(self) -> discord.Message | None:
        row = await self._storedStatusRow()
        if row is None:
            return None
        try:
            messageId = int(row.get("statusMessageId") or 0)
            channelId = int(row.get("statusChannelId") or 0)
        except (TypeError, ValueError):
            return None
        if messageId <= 0 or channelId <= 0:
            return None

        self.statusMessageId = messageId
        self.statusChannelId = channelId
        channel = self.bot.get_channel(channelId)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channelId)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return None
        if not hasattr(channel, "fetch_message"):
            return None
        try:
            return await channel.fetch_message(messageId)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    def _queryRconSync(self) -> MinecraftStatus:
        connection = mcrcon.MCRcon(
            host=minecraftRCONAddress,
            password=minecraftAuthenticationToken,
            port=int(minecraftRCONPort),
            timeout=max(1, int(minecraftRCONTimeoutSeconds or 5)),
        )
        connected = False
        try:
            connection.connect()
            connected = True
            playerResponse = connection.command("/list")
            tpsResponse = connection.command("/forge tps")
        finally:
            if connected:
                try:
                    connection.disconnect()
                except Exception:
                    log.warning("Minecraft RCON disconnect failed.", exc_info=True)

        playerCount, maxPlayers = parsePlayerCount(
            playerResponse,
            fallbackMaximum=minecraftServerMaxPlayersFallback,
        )
        return MinecraftStatus(
            online=True,
            playerCount=playerCount,
            maxPlayers=maxPlayers,
            tps=parseTps(tpsResponse),
        )

    async def _pollOnce(self) -> bool:
        message = await self._resolveStoredMessage()
        if message is None:
            log.warning("Minecraft status poll skipped because the stored message is unavailable.")
            return False

        try:
            self.lastStatus = await asyncio.to_thread(self._queryRconSync)
        except Exception:
            self.lastStatus = MinecraftStatus(
                online=False,
                maxPlayers=max(1, int(minecraftServerMaxPlayersFallback or 60)),
            )
            log.warning("Minecraft RCON status check failed; reporting the server offline.", exc_info=True)

        try:
            await message.edit(embed=buildEmbed(self.lastStatus))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            log.warning("Minecraft status message %s could not be updated.", self.statusMessageId, exc_info=True)
            return False
        await self._updateStoredStatus()
        return self.lastStatus.online

    async def _pollLoop(self) -> None:
        intervalSec = max(5, int(minecraftCheckCooldownSeconds or 30))
        while True:
            try:
                await self._pollOnce()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Minecraft status poll failed unexpectedly; the worker will retry.")
            await asyncio.sleep(intervalSec)

    def _startPolling(self) -> None:
        if self.loopTask is not None and not self.loopTask.done():
            return
        task = asyncio.create_task(self._pollLoop(), name="minecraft-server-status")
        self.loopTask = task

        def _done(doneTask: asyncio.Task[None]) -> None:
            if self.loopTask is doneTask:
                self.loopTask = None
            try:
                doneTask.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception("Minecraft status worker crashed.")

        task.add_done_callback(_done)

    async def _stopPolling(self) -> None:
        task = self.loopTask
        self.loopTask = None
        if task is None or task.done() or task is asyncio.current_task():
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _createStatusMessage(self, channel: discord.TextChannel) -> discord.Message:
        self.lastStatus = MinecraftStatus(
            online=False,
            maxPlayers=max(1, int(minecraftServerMaxPlayersFallback or 60)),
        )
        message = await channel.send(embed=buildEmbed(self.lastStatus))
        self.statusMessageId = int(message.id)
        self.statusChannelId = int(message.channel.id)
        await self._replaceStoredStatus()
        return message

    @app_commands.command(
        name="register-status-channel",
        description="Register a channel for the Minecraft server status panel.",
    )
    @app_commands.guild_only()
    async def registerStatusChannel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        if not canRunCommand(interaction.user):
            await self._safeEphemeral(interaction, "You cannot register the Minecraft status channel.")
            return
        await interaction.response.defer(ephemeral=True, thinking=True)

        existing = await self._resolveStoredMessage()
        if existing is not None:
            self._startPolling()
            await interaction.followup.send(
                f"A Minecraft status panel is already registered in <#{self.statusChannelId}>.",
                ephemeral=True,
            )
            return

        await self._clearStoredStatus()
        message = await self._createStatusMessage(channel)
        self._startPolling()
        await interaction.followup.send(
            f"Minecraft status panel registered: {message.jump_url}",
            ephemeral=True,
        )

    @app_commands.command(
        name="restart-minecraft-status",
        description="Restart the Minecraft server status worker.",
    )
    @app_commands.guild_only()
    async def restartMinecraftStatus(self, interaction: discord.Interaction) -> None:
        if not canRunCommand(interaction.user):
            await self._safeEphemeral(interaction, "You cannot restart the Minecraft status worker.")
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        if await self._resolveStoredMessage() is None:
            await interaction.followup.send("No Minecraft status panel is registered.", ephemeral=True)
            return
        await self._stopPolling()
        self._startPolling()
        await interaction.followup.send("Minecraft status worker restarted.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    if mcrcon is None:
        log.warning("Skipping MinecraftCog: 'mcrcon' module is not installed.")
        return
    await bot.add_cog(MinecraftCog(bot))
