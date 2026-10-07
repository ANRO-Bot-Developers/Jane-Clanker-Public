from __future__ import annotations

import asyncio
import logging
import os
from asyncio import AbstractEventLoop
from datetime import datetime, timezone
from typing import Any

import discord

from runtime import backups as runtimeBackups
from runtime import orgProfiles

log = logging.getLogger(__name__)



class BootstrapCoordinator:
    def __init__(
        self,
        *,
        botClient: Any,
        configModule: Any,
        initDbFn: Any,
        loadMultiRegistryFn: Any,
        sessionViews: Any,
        maintenanceCoordinator: Any,
        taskBudgeter: Any,
        recruitmentService: Any,
        helpCommandsModule: Any,
        pluginRegistry: Any,
        extensionNames: list[str],
    ) -> None:
        self.botClient = botClient
        self.config = configModule
        self.initDb = initDbFn
        self.loadMultiRegistry = loadMultiRegistryFn
        self.sessionViews = sessionViews
        self.maintenance = maintenanceCoordinator
        self.taskBudgeter = taskBudgeter
        self.recruitmentService = recruitmentService
        self.helpCommands = helpCommandsModule
        self.pluginRegistry = pluginRegistry
        self.extensionNames = extensionNames

        self.startupGreetingSent = False
        self.readyCommandSyncCompleted = False
        self._guildGlobalCopyPrimedIds: set[int] = set()

    @staticmethod
    def _parseIsoDatetime(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    def _logUserVisibleCommandNamingSanity(self) -> None:
        self.helpCommands.logUserVisibleCommandNamingSanity(self.botClient.tree)

    def _isGuildAllowedForCommands(self, guildId: int | None) -> bool:
        if guildId is None or int(guildId) <= 0:
            return False
        allowedGuildIds = {
            int(rawGuildId)
            for rawGuildId in getattr(self.config, "allowedCommandGuildIds", [])
            if int(rawGuildId) > 0
        }
        if not allowedGuildIds:
            return True
        return int(guildId) in allowedGuildIds

    def _primeGuildCommandSet(self, guild: discord.abc.Snowflake, *, copyGlobals: bool) -> None:
        guildId = int(getattr(guild, "id", 0) or 0)
        if guildId <= 0:
            return
        if not copyGlobals or guildId in self._guildGlobalCopyPrimedIds:
            return
        # Preserve guild-scoped command registrations from individual cogs
        # and only layer the current global commands on top for guild sync.
        self.botClient.tree.copy_global_to(guild=guild)
        self._guildGlobalCopyPrimedIds.add(guildId)

    async def _createUnknownGuildInvite(self, guild: discord.Guild) -> str:
        if not bool(getattr(self.config, "unknownGuildInviteCreationEnabled", False)):
            return ""

        member = getattr(guild, "me", None)
        if member is None:
            return ""
        try:
            maxAgeSec = int(getattr(self.config, "unknownGuildInviteMaxAgeSec", 300) or 300)
        except (TypeError, ValueError):
            maxAgeSec = 300
        try:
            maxUses = int(getattr(self.config, "unknownGuildInviteMaxUses", 1) or 1)
        except (TypeError, ValueError):
            maxUses = 1
        maxAgeSec = max(60, min(3600, maxAgeSec))
        maxUses = max(1, min(10, maxUses))

        for channel in list(getattr(guild, "text_channels", []) or []):
            try:
                permissions = channel.permissions_for(member)
                if not bool(getattr(permissions, "create_instant_invite", False)):
                    continue
                invite = await channel.create_invite(
                    max_age=maxAgeSec,
                    max_uses=maxUses,
                    unique=True,
                    reason="Jane unknown-guild diagnostics (explicitly enabled)",
                )
            except (discord.Forbidden, discord.HTTPException):
                log.warning(
                    "Could not create the configured diagnostic invite in channel %s for guild %s.",
                    getattr(channel, "id", "unknown"),
                    guild.id,
                    exc_info=True,
                )
                continue
            inviteUrl = str(getattr(invite, "url", "") or "").strip()
            if inviteUrl:
                return inviteUrl
        return ""

    async def syncCommandsOnReady(self) -> None:
        if self.readyCommandSyncCompleted:
            return

        syncGlobalOnReady = bool(getattr(self.config, "syncGlobalCommandsOnReady", False))
        syncGuildsOnReady = bool(getattr(self.config, "syncGuildCommandsOnReady", True))
        copyGlobalsToGuildOnReady = bool(getattr(self.config, "copyGlobalCommandsToGuildOnReady", True))
        clearGlobalWhenUsingGuildSync = bool(getattr(self.config, "clearGlobalCommandsWhenUsingGuildSync", False))
        if not syncGlobalOnReady and not syncGuildsOnReady:
            self.readyCommandSyncCompleted = True
            return

        guildStatuses: dict[str, str] = {}
        hadFailures = False
        registeredCommands = self.botClient.tree.get_commands(guild=None, type=discord.AppCommandType.chat_input)
        if not registeredCommands:
            log.warning(
                "No registered global command definitions found before sync. "
                "Command tree may have been cleared earlier in startup."
            )

        if syncGuildsOnReady and clearGlobalWhenUsingGuildSync:
            log.warning(
                "clearGlobalCommandsWhenUsingGuildSync is enabled, but this behavior is disabled "
                "to prevent wiping in-memory command definitions."
            )

        if syncGuildsOnReady:
            for guild in self.botClient.guilds:
                guildLabel = f"{guild.name} ({guild.id})"
                if not self._isGuildAllowedForCommands(int(guild.id)):
                    guildStatuses[guildLabel] = "Skipped - not in allowedCommandGuildIds"

                    try:
                        inviteUrl = await self._createUnknownGuildInvite(guild)
                        if inviteUrl:
                            guildStatuses[guildLabel] = (
                                "Skipped - not in allowedCommandGuildIds | "
                                f"Created bounded diagnostic invite: {inviteUrl}"
                            )
                    except Exception:
                        log.exception(
                            "Guild invite creation attempt failed for guild %s (%s).",
                            guild.id,
                            guild.name,
                        )
                    continue
                syncedGuild = None
                lastGuildError: Exception | None = None
                for attempt in range(1, 3):
                    try:
                        self._primeGuildCommandSet(
                            guild,
                            copyGlobals=copyGlobalsToGuildOnReady,
                        )
                        syncedGuild = await self.botClient.tree.sync(guild=guild)
                        lastGuildError = None
                        break
                    except Exception as exc:
                        lastGuildError = exc
                        log.exception(
                            "Guild command sync attempt %s failed for guild %s (%s).",
                            attempt,
                            guild.id,
                            guild.name,
                        )
                if syncedGuild is not None:
                    guildStatuses[guildLabel] = f"{len(syncedGuild)} command(s)"
                else:
                    hadFailures = True
                    guildStatuses[guildLabel] = f"FAILED: {lastGuildError.__class__.__name__ if lastGuildError is not None else 'Unknown'}"

        globalCountText = "skipped"
        if syncGlobalOnReady:
            syncedGlobal = None
            lastGlobalError: Exception | None = None
            for attempt in range(1, 3):
                try:
                    syncedGlobal = await self.botClient.tree.sync()
                    lastGlobalError = None
                    break
                except Exception as exc:
                    lastGlobalError = exc
                    log.exception("Global command sync attempt %s (on_ready) failed.", attempt)
            if syncedGlobal is not None:
                globalCountText = str(len(syncedGlobal))
            else:
                hadFailures = True
                globalCountText = f"FAILED: {lastGlobalError.__class__.__name__ if lastGlobalError is not None else 'Unknown'}"

        guildLines = (
            "\n".join(f"- {guildName}: {status}" for guildName, status in guildStatuses.items())
            if guildStatuses
            else "- (none)"
        )
        log.info(
            "Command sync complete\nGuilds:\n%s\nGlobal: %s",
            guildLines,
            globalCountText,
        )
        self._logUserVisibleCommandNamingSanity()
        if hadFailures:
            log.warning("Command sync had failures. Jane will retry on the next ready event.")
            return
        self.readyCommandSyncCompleted = True

    async def setupHook(self) -> None:
        await self.initDb()
        if bool(getattr(self.config, "dbRuntimeSnapshotOnStartup", True)):
            try:
                capture = await runtimeBackups.captureRuntimeDbState(self.config, label="startup")
                log.info(
                    "Runtime DB startup snapshot captured: snapshot=%s report=%s%s",
                    capture.get("snapshotPath") or "none",
                    capture.get("reportPath") or "none",
                    f" error={capture.get('snapshotError')}" if capture.get("snapshotError") else "",
                )
            except Exception:
                log.exception("Failed to capture runtime DB startup snapshot.")
        try:
            multiRegistry = self.loadMultiRegistry()
            log.info(
                "Multi-ORBAT registry loaded: %d sheet(s): %s",
                len(multiRegistry),
                ", ".join(sorted(multiRegistry.keys())),
            )
        except Exception:
            log.exception("Failed to load multi-ORBAT registry.")

        for extensionName in self.extensionNames:
            await self.botClient.load_extension(extensionName)
            self.pluginRegistry.registerExtension(extensionName)

        try:
            restoredViews = await self.sessionViews.restorePersistentViews(self.botClient)
            log.info(
                "Session persistent views restored: sessions=%d, bgQueues=%d, bgChecks=%d",
                int(restoredViews.get("sessions", 0)),
                int(restoredViews.get("bgQueues", 0)),
                int(restoredViews.get("bgChecks", 0)),
            )
        except Exception:
            log.exception("Failed to restore session persistent views.")

        self.maintenance.ensureBackgroundTasksStarted()

        serverId = os.getenv("DISCORD_GUILD_ID") or str(getattr(self.config, "serverId", "")) or None
        clearGlobal = os.getenv("CLEAR_GLOBAL_COMMANDS")
        if clearGlobal is None:
            clearGlobal = getattr(self.config, "clearGlobalCommands", False)
        else:
            clearGlobal = clearGlobal.strip().lower() in {"1", "true", "yes"}

        clearGuild = os.getenv("CLEAR_GUILD_COMMANDS")
        if clearGuild is None:
            clearGuild = getattr(self.config, "clearGuildCommands", False)
        else:
            clearGuild = clearGuild.strip().lower() in {"1", "true", "yes"}

        if clearGlobal:
            log.warning(
                "clearGlobalCommands was requested, but global clear is skipped to avoid "
                "wiping in-memory command definitions."
            )

        if clearGuild and serverId:
            guild = discord.Object(id=int(serverId))
            self.botClient.tree.clear_commands(guild=guild)
            await self.botClient.tree.sync(guild=guild)

        syncGlobalInSetup = bool(getattr(self.config, "syncGlobalCommandsInSetupHook", False))
        if syncGlobalInSetup:
            synced = await self.botClient.tree.sync()
            log.info(
                "Synced %d global commands: %s",
                len(synced),
                ", ".join(command.name for command in synced),
            )
        else:
            log.info("Skipped setup_hook global sync (on_ready handles command sync).")

    async def onReady(self) -> None:
        log.info("Discord ready. Logged in as %s", self.botClient.user)
        await self.syncCommandsOnReady()
        if self.startupGreetingSent:
            return

        greetingCooldownSec = max(0, int(getattr(self.config, "startupGreetingCooldownSec", 1800) or 1800))
        if greetingCooldownSec > 0:
            try:
                lastGreetingRaw = await self.recruitmentService.getSetting("startupGreetingLastSentAt")
                lastGreetingAt = self._parseIsoDatetime(lastGreetingRaw)
                if lastGreetingAt is not None:
                    if lastGreetingAt.tzinfo is None:
                        lastGreetingAt = lastGreetingAt.replace(tzinfo=timezone.utc)
                    elapsed = (datetime.now(timezone.utc) - lastGreetingAt.astimezone(timezone.utc)).total_seconds()
                    if elapsed < greetingCooldownSec:
                        log.info(
                            "Startup greeting skipped (cooldown active: %.0fs remaining).",
                            max(0.0, greetingCooldownSec - elapsed),
                        )
                        self.startupGreetingSent = True
                        return
            except Exception:
                log.exception("Failed to evaluate startup greeting cooldown.")

        channelId = int(
            orgProfiles.getOrganizationValue(
                self.config,
                "startupGreetingChannelId",
                orgKey=orgProfiles.getDefaultOrganizationKey(self.config),
                default=0,
            )
            or 0
        )
        if channelId <= 0:
            self.startupGreetingSent = True
            return

        channel = self.botClient.get_channel(channelId)
        if channel is None:
            try:
                channel = await self.taskBudgeter.runDiscord(lambda: self.botClient.fetch_channel(channelId))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                channel = None

        if isinstance(channel, (discord.TextChannel, discord.Thread)):
            try:
                await self.taskBudgeter.runDiscord(lambda: channel.send("Hi everyone!"))
                try:
                    await self.recruitmentService.setSetting(
                        "startupGreetingLastSentAt",
                        datetime.now(timezone.utc).isoformat(),
                    )
                except Exception:
                    log.exception("Failed to persist startup greeting timestamp.")
                self.startupGreetingSent = True
            except (discord.Forbidden, discord.HTTPException):
                log.warning("Startup greeting failed for channel %s.", channelId)
        else:
            log.warning("Startup greeting channel %s is unavailable.", channelId)
            self.startupGreetingSent = True
