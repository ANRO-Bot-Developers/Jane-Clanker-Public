import asyncio
import logging
import os
import sys
import traceback
from pathlib import Path
from datetime import datetime, timezone


def _installEarlyStartupExceptionLog() -> None:
    if bool(getattr(sys, "_jane_early_startup_log_installed", False)):
        return
    setattr(sys, "_jane_early_startup_log_installed", True)
    previousHook = sys.excepthook

    def _writeEarlyException(excType: type[BaseException], excValue: BaseException, excTraceback) -> None:
        try:
            logPath = Path(__file__).resolve().parent / "logs" / "general-errors.log"
            logPath.parent.mkdir(parents=True, exist_ok=True)
            rendered = "".join(traceback.format_exception(excType, excValue, excTraceback)).rstrip()
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            with logPath.open("a", encoding="utf-8") as handle:
                handle.write(
                    f"[{timestamp}] ERROR runtime.startup-fallback\n"
                    "Unhandled exception before Jane finished installing runtime logging.\n"
                    f"{rendered}\n"
                    f"{'-' * 90}\n"
                )
        except Exception:
            pass

    def _earlyStartupExceptionHook(excType: type[BaseException], excValue: BaseException, excTraceback) -> None:
        if issubclass(excType, KeyboardInterrupt):
            previousHook(excType, excValue, excTraceback)
            return
        _writeEarlyException(excType, excValue, excTraceback)
        previousHook(excType, excValue, excTraceback)

    sys.excepthook = _earlyStartupExceptionHook


_installEarlyStartupExceptionLog()

import discord
from discord import app_commands
from discord.ext import commands

import config
from db.sqlite import closeDb, initDb
from features.staff.recruitment import (
    service as recruitmentService,
    sheets as recruitmentSheets,
)
from features.staff.trainingLog import trainingLogService
from features.staff.sessions import (
    service as sessionService,
    views as sessionViews,
)
from features.staff.sessions.Roblox import robloxTransport, robloxUsers
from runtime import (
    auditStream as runtimeAuditStream,
    backups as runtimeBackups,
    botProfile as runtimeBotProfile,
    bootstrap as runtimeBootstrap,
    commandPause as runtimeCommandPause,
    configSanity as runtimeConfigSanity,
    commandPermissions as runtimeCommandPermissions,
    entrypoint as runtimeEntrypoint,
    extensionLayout as runtimeExtensionLayout,
    errorLogging as runtimeErrorLogging,
    errors as runtimeErrors,
    eventLoopWatchdog as runtimeEventLoopWatchdog,
    featureFlags as runtimeFeatureFlags,
    gamblingApi as runtimeGamblingApi,
    helpCommands as runtimeHelpCommands,
    interaction as interactionRuntime,
    janeIdentityWeb as runtimeJaneIdentityWeb,
    johnEventRuntime as runtimeJohnEventRuntime,
    maintenance as runtimeMaintenance,
    messageRouting as runtimeMessageRouting,
    metricsExport as runtimeMetricsExport,
    orgFeatureGate as runtimeOrgFeatureGate,
    orbatAudit as runtimeOrbatAudit,
    pauseState as runtimePauseState,
    permissions as runtimePermissions,
    privateServices as runtimePrivateServices,
    pluginRegistry as runtimePluginRegistry,
    processResources as runtimeProcessResources,
    retryQueue as runtimeRetryQueue,
    shutdown as runtimeShutdown,
    singleInstance as runtimeSingleInstance,
    taskBudgeter,
    taskStats as runtimeTaskStats,
    taskSupervisor as runtimeTaskSupervisor,
    textCommands as runtimeTextCommands,
    trainingLogRuntime as runtimeTrainingLogRuntime,
    webhookHealth as runtimeWebhookHealth,
    webhooks as runtimeWebhooks,
)
from silly import commands as sillyCommands


_privateServices = runtimePrivateServices.loadPrivateServices(configModule=config)
departmentOrbatSheets = _privateServices.departmentOrbatSheets
orbatRoleSync = _privateServices.orbatRoleSync
orbatSheets = _privateServices.orbatSheets
runtimeGitUpdate = _privateServices.gitUpdateModule
runtimeProcessControl = _privateServices.processControlModule

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

botClient = commands.Bot(command_prefix="!", intents=intents)
interactionRuntime.installRetrySafeInteractionLayer()
_botStartedAt = datetime.now(timezone.utc)
_lockedPrefixCommandTokens = {
    "!kill",
    "!skin",
    "?janeruntime",
    "?bgleaderboard",
    "?bg-leaderboard",
    "?perm-sim",
    "?permsim",
    "?ruid",
    "?cpurgejane",
    "!pairdbnames",
    "!janeflagsync",
}
_manualTextCommandTokens = _lockedPrefixCommandTokens | {
    "!casinotoggle",
    "!janeterminal",
    "!viewallchannels",
    ":)help",
    "?trainingstats",
    "?hoststats",
}
_runtimeControlAllowedWhilePaused = {
    "pause",
    "restart",
}
_runtimePausedMessage = "Jane is currently paused. Use /pause again to resume actions."
_commandPausedDefaultMessage = "This command is paused right now. Please try again later."
_serverNotRecognizedMessage = (
    "Server not recognized. Please reach out to @AlexYikes for assistance."
)
_organizationFeatureUnavailableMessage = "This feature is not enabled for this organization."
_temporaryLockMessage = "Commands are temporarily restricted to specific staff during rollout."
_allowedCommandGuildIds = {
    int(guildId)
    for guildId in getattr(config, "allowedCommandGuildIds", [])
    if int(guildId) > 0
}
_activeAppCommandInvocations: dict[tuple[int, str], datetime] = {}
_activeInvocationTtlSec = 600
_roleOrbatSyncLastRunByUser: dict[int, datetime] = {}
_featureFlags = runtimeFeatureFlags.FeatureFlagService(configModule=config)
_pluginRegistry = runtimePluginRegistry.PluginRegistry()
_pauseController = runtimePauseState.PauseController()
_commandPauseController = runtimeCommandPause.CommandPauseController(
    defaultMessage=_commandPausedDefaultMessage,
)
_runtimeTaskSupervisor = runtimeTaskSupervisor.TaskSupervisor()
_singleInstanceLock = runtimeSingleInstance.SingleInstanceLock(
    Path(__file__).resolve().parent / "logs" / "jane-runtime.lock"
)
_retryQueue = runtimeRetryQueue.RetryQueueCoordinator(
    taskBudgeter=taskBudgeter,
    pollIntervalSec=int(getattr(config, "retryQueuePollIntervalSec", 6) or 6),
    initialDelaySec=int(getattr(config, "retryQueueInitialDelaySec", 30) or 30),
)
_auditStream = runtimeAuditStream.AuditStream(
    botClient=botClient,
    configModule=config,
    taskBudgeter=taskBudgeter,
)
_webhookHealthWatcher = runtimeWebhookHealth.WebhookHealthWatcher(
    botClient=botClient,
    taskBudgeter=taskBudgeter,
    auditStream=_auditStream,
    checkIntervalSec=int(getattr(config, "webhookHealthCheckIntervalSec", 600) or 600),
    initialDelaySec=int(getattr(config, "webhookHealthInitialDelaySec", 180) or 180),
    maxRowsPerRun=int(getattr(config, "webhookHealthMaxRowsPerRun", 50) or 50),
)
_eventLoopWatchdog = runtimeEventLoopWatchdog.EventLoopWatchdog(configModule=config)
_gitUpdateCoordinator = (
    runtimeGitUpdate.GitUpdateCoordinator(
        botClient=botClient,
        configModule=config,
        pauseController=_pauseController,
        processControlModule=runtimeProcessControl,
        repoRoot=os.path.dirname(os.path.abspath(__file__)),
        auditStream=_auditStream,
    )
    if runtimeGitUpdate is not None and runtimeProcessControl is not None
    else None
)
_textCommandRouter: runtimeTextCommands.TextCommandRouter | None = None
_humanMessageRouter: runtimeMessageRouting.HumanMessageRouter | None = None
_maintenanceCoordinator = runtimeMaintenance.MaintenanceCoordinator(
    botClient=botClient,
    configModule=config,
    recruitmentService=recruitmentService,
    recruitmentSheets=recruitmentSheets,
    departmentOrbatSheets=departmentOrbatSheets,
    orbatSheets=orbatSheets,
    serverSafetyService=_privateServices.serverSafetyService,
    orbatAuditRuntime=_privateServices.orbatAuditRuntime,
    sessionService=sessionService,
    sessionViews=sessionViews,
    taskBudgeter=taskBudgeter,
    configSanityModule=runtimeConfigSanity,
)
_maintenanceCoordinator.pauseController = _pauseController
_bootstrapCoordinator = runtimeBootstrap.BootstrapCoordinator(
    botClient=botClient,
    configModule=config,
    initDbFn=initDb,
    loadMultiRegistryFn=_privateServices.loadMultiOrbatRegistry,
    sessionViews=sessionViews,
    maintenanceCoordinator=_maintenanceCoordinator,
    taskBudgeter=taskBudgeter,
    recruitmentService=recruitmentService,
    helpCommandsModule=runtimeHelpCommands,
    pluginRegistry=_pluginRegistry,
    extensionNames=runtimeExtensionLayout.buildExtensionNames(configModule=config),
)
_errorCoordinator = runtimeErrors.ErrorCoordinator(
    botClient=botClient,
    configModule=config,
    taskBudgeter=taskBudgeter,
    retryQueue=_retryQueue,
)
_metricsExporter: runtimeMetricsExport.MetricsExporter | None = None
_gamblingApiServer: runtimeGamblingApi.GamblingApiServer | None = None
_janeIdentityWebServer: runtimeJaneIdentityWeb.JaneIdentityWebServer | None = None
_botProfileBioStarted = False


_formatUptime = runtimeProcessResources.formatUptime
_discordTimestamp = runtimeProcessResources.discordTimestamp


def _getProcessResourceSnapshot(nowUtc: datetime) -> dict[str, str]:
    return runtimeProcessResources.getProcessResourceSnapshot(
        botStartedAt=_botStartedAt,
        nowUtc=nowUtc,
    )


_metricsExporter = runtimeMetricsExport.MetricsExporter(
    botClient=botClient,
    taskBudgeter=taskBudgeter,
    maintenanceCoordinator=_maintenanceCoordinator,
    retryQueue=_retryQueue,
    featureFlags=_featureFlags,
    webhookHealthWatcher=_webhookHealthWatcher,
    auditStream=_auditStream,
    botStartedAt=_botStartedAt,
    getProcessResourceSnapshot=_getProcessResourceSnapshot,
)
_gamblingApiServer = runtimeGamblingApi.GamblingApiServer(
    configModule=config,
    metricsProvider=_metricsExporter.snapshot,
)
_janeIdentityWebServer = runtimeJaneIdentityWeb.JaneIdentityWebServer(
    configModule=config,
    botClient=botClient,
)
_trainingLogCoordinator = trainingLogService.TrainingLogCoordinator(
    botClient=botClient,
    configModule=config,
    taskBudgeter=taskBudgeter,
    recruitmentService=recruitmentService,
    webhookModule=runtimeWebhooks,
)
_trainingLogRuntime = runtimeTrainingLogRuntime.TrainingLogRuntime(
    botClient=botClient,
    configModule=config,
    taskBudgeter=taskBudgeter,
    coordinator=_trainingLogCoordinator,
)
_johnEventCoordinator = runtimeJohnEventRuntime.JohnEventCoordinator(
    botClient=botClient,
    configModule=config,
    taskBudgeter=taskBudgeter,
    orbatSheets=orbatSheets,
    robloxUsersModule=robloxUsers,
    orbatAuditRuntime=runtimeOrbatAudit,
    privateExtensionsEnabled=_privateServices.privateExtensionsEnabled,
)


def _startBotProfileBioTask() -> None:
    global _botProfileBioStarted
    if _botProfileBioStarted:
        return
    _botProfileBioStarted = True
    _runtimeTaskSupervisor.create(
        taskBudgeter.runLowPriorityDiscord(
            lambda: runtimeBotProfile.updateJaneBioOnStartup(
                botClient=botClient,
                configModule=config,
                taskBudgeter=taskBudgeter,
                repoRoot=Path(__file__).resolve().parent,
            )
        ),
        name="jane-profile-bio-update",
    )


def _orbatWeeklyScheduleConfig() -> tuple[int, int, int]:
    hour = int(getattr(config, "orbatOrganizationUtcHour", 3))
    minute = int(getattr(config, "orbatOrganizationUtcMinute", 0))
    weekday = int(getattr(config, "orbatOrganizationUtcWeekday", 6))
    return hour, minute, weekday


def _nonRecruitmentOrbatWritesEnabled() -> bool:
    return bool(getattr(config, "nonRecruitmentOrbatWritesEnabled", False)) and bool(
        _privateServices.privateExtensionsEnabled
    )


async def _safeInteractionSend(
    interaction: discord.Interaction,
    message: str,
    *,
    ephemeral: bool = True,
) -> None:
    await interactionRuntime.safeInteractionReply(
        interaction,
        content=message,
        ephemeral=ephemeral,
    )


def _interactionCommandName(interaction: discord.Interaction) -> str:
    data = interaction.data if isinstance(interaction.data, dict) else {}
    parts: list[str] = []

    rootName = str(data.get("name") or "").strip()
    if rootName:
        parts.append(rootName)

    options = data.get("options")
    while isinstance(options, list) and options:
        first = options[0]
        if not isinstance(first, dict):
            break
        optionType = int(first.get("type") or 0)
        if optionType not in {1, 2}:
            break
        optionName = str(first.get("name") or "").strip()
        if optionName:
            parts.append(optionName)
        options = first.get("options")

    return " ".join(parts).strip() or "unknown"


async def _mirrorUnapprovedGuildCommandAttempt(
    *,
    commandName: str,
    userLabel: str,
    userId: int,
    guildName: str,
    guildId: int,
) -> None:
    targetUserId = int(getattr(config, "errorMirrorUserId", 0) or 0)
    if targetUserId <= 0:
        return

    description = (
        f"**Command:** `{str(commandName or 'unknown').strip()}`\n"
        f"**User:** {str(userLabel or 'Unknown User').strip()} (`{int(userId)}`)\n"
        f"**Server:** {str(guildName or 'Unknown Server').strip()} (`{int(guildId)}`)"
    )

    try:
        targetUser = botClient.get_user(targetUserId)
        if targetUser is None:
            targetUser = await taskBudgeter.runDiscord(lambda: botClient.fetch_user(targetUserId))
        if targetUser is None:
            return
        embed = discord.Embed(
            title="Jane Guild Lock Alert",
            description=description,
            color=discord.Color.orange(),
            timestamp=datetime.now(timezone.utc),
        )
        await taskBudgeter.runDiscord(
            lambda: targetUser.send(
                content="A command was attempted in a non-approved guild.",
                embed=embed,
            )
        )
    except Exception:
        try:
            await _retryQueue.enqueue(
                jobType="error-mirror-dm",
                payload={
                    "targetUserId": int(targetUserId),
                    "content": "A command was attempted in a non-approved guild.",
                    "title": "Jane Guild Lock Alert",
                    "description": description,
                },
                maxAttempts=6,
                initialDelaySec=10,
                source="guild-lock",
            )
        except Exception:
            pass


def _invocationKey(
    interaction: discord.Interaction,
    command: app_commands.Command | app_commands.ContextMenu | None = None,
) -> tuple[int, str]:
    commandName = ""
    if command is not None:
        commandName = str(getattr(command, "qualified_name", "") or getattr(command, "name", ""))
    if not commandName:
        data = interaction.data if isinstance(interaction.data, dict) else {}
        commandName = str(data.get("name") or "unknown")
    return int(interaction.user.id), commandName.lower()


def _pruneActiveInvocations() -> None:
    if not _activeAppCommandInvocations:
        return
    now = datetime.now(timezone.utc)
    expired: list[tuple[int, str]] = []
    for key, startedAt in _activeAppCommandInvocations.items():
        if (now - startedAt).total_seconds() > _activeInvocationTtlSec:
            expired.append(key)
    for key in expired:
        _activeAppCommandInvocations.pop(key, None)


async def _maybeSyncRoleBasedOrbats(member: discord.Member, guildId: int) -> None:
    if not _nonRecruitmentOrbatWritesEnabled():
        return
    if not bool(getattr(config, "roleOrbatSyncEnabled", True)):
        return
    if not getattr(config, "deptSpreadsheetId", ""):
        return

    minIntervalSec = max(60, int(getattr(config, "roleOrbatSyncMinIntervalSec", 600) or 600))
    nowUtc = datetime.now(timezone.utc)
    lastRun = _roleOrbatSyncLastRunByUser.get(member.id)
    if lastRun and (nowUtc - lastRun).total_seconds() < minIntervalSec:
        return
    _roleOrbatSyncLastRunByUser[member.id] = nowUtc

    try:
        syncSummary = await orbatRoleSync.syncMemberRoleOrbats(member, guildId)
        if not isinstance(syncSummary, dict):
            return
        if not bool(syncSummary.get("changed", False)):
            return
        for result in syncSummary.get("results", []):
            if not isinstance(result, dict):
                continue
            if not (
                result.get("moved")
                or result.get("updated")
                or result.get("rankUpdated")
                or result.get("created")
            ):
                continue
            logging.info(
                "Role-based ORBAT sync applied for %s (%s): syncType=%s moved=%s updated=%s rankUpdated=%s created=%s section=%s targetRank=%s",
                member.id,
                result.get("robloxUsername", "unknown"),
                result.get("syncType", "unknown"),
                result.get("moved"),
                result.get("updated"),
                result.get("rankUpdated"),
                result.get("created"),
                result.get("section"),
                result.get("targetRank"),
            )
            try:
                payload = runtimeOrbatAudit.buildRoleSyncChangeLog(
                    memberMention=member.mention,
                    result=result,
                )
                if payload:
                    await runtimeOrbatAudit.sendOrbatChangeLog(
                        botClient,
                        **payload,
                    )
            except Exception:
                logging.exception(
                    "Failed to emit role-based ORBAT audit log for member %s.",
                    member.id,
                )
    except Exception:
        logging.exception("Role-based ORBAT sync failed for member %s.", member.id)


async def _scheduleRoleBasedOrbatSync(member: discord.Member, guildId: int) -> None:
    delaySec = max(0.0, float(getattr(config, "roleOrbatSyncBackgroundDelaySec", 5) or 0))
    if delaySec > 0:
        await asyncio.sleep(delaySec)
    await taskBudgeter.runBackground(lambda: _maybeSyncRoleBasedOrbats(member, guildId))


async def _postRuntimeWebhookMessage(
    message: discord.Message,
    embed: discord.Embed,
) -> bool:
    return await runtimeWebhooks.sendOwnedWebhookMessage(
        botClient=botClient,
        channel=message.channel,
        webhookName="Jane Runtime",
        embed=embed,
        username="Jane Runtime",
        avatarUrl=botClient.user.display_avatar.url if botClient.user else None,
        reason="Runtime diagnostics command",
    )


async def _postTerminalWebhookMessage(
    message: discord.Message,
    content: str,
) -> bool:
    return await runtimeWebhooks.sendOwnedWebhookMessage(
        botClient=botClient,
        channel=message.channel,
        webhookName="Jane Terminal",
        content=content,
        username="Jane Terminal",
        avatarUrl=botClient.user.display_avatar.url if botClient.user else None,
        reason="Read-only terminal diagnostics command",
    )


async def _postCopyServerWebhookMessage(
    message: discord.Message,
    content: str,
    view: discord.ui.View,
) -> bool:
    return await runtimeWebhooks.sendOwnedWebhookMessage(
        botClient=botClient,
        channel=message.channel,
        webhookName="Jane Copyserver",
        content=content,
        view=view,
        username="Jane Copyserver",
        avatarUrl=botClient.user.display_avatar.url if botClient.user else None,
        reason="Hidden copyserver confirmation",
    )


def _hasCohostPermission(member: discord.Member) -> bool:
    return runtimePermissions.hasCohostPermission(member)


def _isCommandExecutionAllowed(userId: int) -> bool:
    return runtimePermissions.isCommandExecutionAllowed(userId)


def _isGuildAllowedForCommands(guildId: int | None) -> bool:
    if guildId is None or guildId <= 0:
        return False
    if not _allowedCommandGuildIds:
        return True
    return guildId in _allowedCommandGuildIds


def _persistAllowedCommandGuildId(guildId: int) -> bool:
    settingsPath = Path(__file__).resolve().parent / "settings" / "core.py"
    source = settingsPath.read_text(encoding="utf-8")
    newline = "\r\n" if "\r\n" in source else "\n"
    lines = source.splitlines()

    startIndex = -1
    endIndex = -1
    for index, line in enumerate(lines):
        if line.strip() == "allowedCommandGuildIds = [":
            startIndex = index
            continue
        if startIndex >= 0 and line.strip() == "]":
            endIndex = index
            break

    if startIndex < 0 or endIndex <= startIndex:
        raise RuntimeError("allowedCommandGuildIds block not found in settings/core.py")

    for line in lines[startIndex + 1 : endIndex]:
        raw = str(line or "").strip().rstrip(",")
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            continue
        if parsed == int(guildId):
            return False

    lines.insert(endIndex, f"    {int(guildId)},")
    trailingNewline = newline if source.endswith(("\n", "\r\n")) else ""
    settingsPath.write_text(newline.join(lines) + trailingNewline, encoding="utf-8")
    return True


def _allowGuildForCommands(guildId: int | None) -> str:
    if guildId is None:
        return "invalid"
    try:
        guildIdInt = int(guildId)
    except (TypeError, ValueError):
        return "invalid"
    if guildIdInt <= 0:
        return "invalid"

    configuredGuildIds: list[int] = []
    for raw in (getattr(config, "allowedCommandGuildIds", []) or []):
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            configuredGuildIds.append(parsed)
    alreadyAllowed = guildIdInt in _allowedCommandGuildIds and guildIdInt in configuredGuildIds

    if guildIdInt not in _allowedCommandGuildIds:
        _allowedCommandGuildIds.add(guildIdInt)
    if guildIdInt not in configuredGuildIds:
        configuredGuildIds.append(guildIdInt)
        setattr(config, "allowedCommandGuildIds", configuredGuildIds)
    runtimePermissions.clearPermissionCaches()

    if alreadyAllowed:
        return "already"

    try:
        wroteConfig = _persistAllowedCommandGuildId(guildIdInt)
    except Exception:
        logging.exception("Failed to persist allowed command guild %s into settings/core.py.", guildIdInt)
        return "runtime-only"

    return "added" if wroteConfig else "already"


def _getTextCommandRouter() -> runtimeTextCommands.TextCommandRouter:
    global _textCommandRouter
    if _textCommandRouter is None:
        _textCommandRouter = runtimeTextCommands.TextCommandRouter(
            botClient=botClient,
            configModule=config,
            sessionService=sessionService,
            sessionViews=sessionViews,
            taskBudgeter=taskBudgeter,
            helpCommandsModule=runtimeHelpCommands,
            permissionsModule=runtimePermissions,
            maintenanceCoordinator=_maintenanceCoordinator,
            botStartedAt=_botStartedAt,
            formatUptime=_formatUptime,
            discordTimestamp=_discordTimestamp,
            getProcessResourceSnapshot=_getProcessResourceSnapshot,
            sendRuntimeWebhookMessage=_postRuntimeWebhookMessage,
            sendTerminalWebhookMessage=_postTerminalWebhookMessage,
            sendCopyServerWebhookMessage=_postCopyServerWebhookMessage,
            hasCohostPermission=_hasCohostPermission,
            isGuildAllowedForCommands=_isGuildAllowedForCommands,
            allowGuildForCommands=_allowGuildForCommands,
            orbatWeeklyScheduleConfig=_orbatWeeklyScheduleConfig,
            trainingLogCoordinator=_trainingLogCoordinator,
            serverSafetyService=_privateServices.serverSafetyService,
            gitUpdateCoordinator=_gitUpdateCoordinator,
            generalErrorLogPath=str(getattr(botClient, "runtimeServices", {}).get("generalErrorLogPath", "") or ""),
        )
    return _textCommandRouter


def _getHumanMessageRouter() -> runtimeMessageRouting.HumanMessageRouter:
    global _humanMessageRouter
    if _humanMessageRouter is None:
        _humanMessageRouter = runtimeMessageRouting.HumanMessageRouter(
            botClient=botClient,
            configModule=config,
            pauseController=_pauseController,
            orgFeatureGateModule=runtimeOrgFeatureGate,
            sillyCommandsModule=sillyCommands,
            textCommandRouterProvider=_getTextCommandRouter,
            trainingStatsHandler=_trainingLogCoordinator.handleTrainingStats,
            hasCohostPermission=_hasCohostPermission,
            isCommandExecutionAllowed=_isCommandExecutionAllowed,
            isGuildAllowedForCommands=_isGuildAllowedForCommands,
            mirrorUnapprovedGuildCommandAttempt=_mirrorUnapprovedGuildCommandAttempt,
            manualTextCommandTokens=_manualTextCommandTokens,
            lockedPrefixCommandTokens=_lockedPrefixCommandTokens,
            messages=runtimeMessageRouting.MessageRoutingMessages(
                runtimePaused=_runtimePausedMessage,
                serverNotRecognized=_serverNotRecognizedMessage,
                organizationFeatureUnavailable=_organizationFeatureUnavailableMessage,
                temporaryLock=_temporaryLockMessage,
            ),
        )
    return _humanMessageRouter

async def _retryErrorMirrorDmHandler(payload: dict) -> None:
    targetUserId = int(payload.get("targetUserId") or 0)
    if targetUserId <= 0:
        return

    targetUser = botClient.get_user(targetUserId)
    if targetUser is None:
        targetUser = await taskBudgeter.runDiscord(lambda: botClient.fetch_user(targetUserId))
    if targetUser is None:
        raise RuntimeError("target user unavailable")

    title = str(payload.get("title") or "Jane Error Mirror").strip()[:200]
    description = str(payload.get("description") or "").strip()
    if len(description) > 3800:
        description = f"{description[:3797]}..."
    embed = discord.Embed(
        title=title,
        description=description or "(no description)",
        color=discord.Color.red(),
        timestamp=datetime.now(timezone.utc),
    )
    content = str(payload.get("content") or "").strip()
    await taskBudgeter.runDiscord(lambda: targetUser.send(content=content or None, embed=embed))


@botClient.event
async def setup_hook() -> None:
    loop = asyncio.get_running_loop()
    runtimeErrorLogging.installAsyncioExceptionLogging(loop)
    _retryQueue.registerHandler("error-mirror-dm", _retryErrorMirrorDmHandler)
    runtimeErrors.installErrorMirrorLogging(coordinator=_errorCoordinator, loop=loop)
    _eventLoopWatchdog.start()
    _retryQueue.start()
    _webhookHealthWatcher.start()
    botClient.runtimeServices = {
        "featureFlags": _featureFlags,
        "pluginRegistry": _pluginRegistry,
        "pauseController": _pauseController,
        "commandPauseController": _commandPauseController,
        "retryQueue": _retryQueue,
        "auditStream": _auditStream,
        "metricsExporter": _metricsExporter,
        "webhookHealthWatcher": _webhookHealthWatcher,
        "johnEventCoordinator": _johnEventCoordinator,
        "janeIdentityWebServer": _janeIdentityWebServer,
        "gitUpdateCoordinator": _gitUpdateCoordinator,
        "generalErrorLogPath": runtimeErrorLogging.currentProcessLogSummary(configModule=config),
        "createBgCheckQueue": (
            lambda *, guild, channel, actor, sourceMessage=None: _getTextCommandRouter().createBgCheckQueue(
                guild=guild,
                channel=channel,
                actor=actor,
                sourceMessage=sourceMessage,
            )
        ),
    }
    await _bootstrapCoordinator.setupHook()
    loadedCommandPauses = await _commandPauseController.loadAll()
    logging.info("Command pause rows loaded: %d", loadedCommandPauses)
    await _gamblingApiServer.start()
    await _janeIdentityWebServer.start()
    if _gitUpdateCoordinator is not None:
        _gitUpdateCoordinator.start()
    _trainingLogRuntime.start()


@botClient.event
async def on_ready() -> None:
    botClient.loop_ref = asyncio.get_running_loop()
    await _bootstrapCoordinator.onReady()
    logging.info("on_ready reached; ensuring training log startup sync task is running.")
    _startBotProfileBioTask()
    _trainingLogRuntime.start()
    _johnEventCoordinator.start()


@botClient.check
async def prefixCommandSafetyCheck(ctx: commands.Context) -> bool:
    guildId = int(getattr(getattr(ctx, "guild", None), "id", 0) or 0)
    if not _isGuildAllowedForCommands(guildId):
        if guildId > 0:
            _runtimeTaskSupervisor.create(
                _mirrorUnapprovedGuildCommandAttempt(
                    commandName=str(getattr(getattr(ctx, "command", None), "qualified_name", "unknown")),
                    userLabel=str(getattr(ctx, "author", "Unknown User")),
                    userId=int(getattr(getattr(ctx, "author", None), "id", 0) or 0),
                    guildName=str(getattr(getattr(ctx, "guild", None), "name", "Unknown Server")),
                    guildId=guildId,
                ),
                name=f"prefix-guild-lock-alert:{guildId}",
            )
        try:
            await ctx.reply(
                _serverNotRecognizedMessage,
                mention_author=False,
            )
        except Exception:
            pass
        return False
    commandName = str(getattr(getattr(ctx, "command", None), "qualified_name", "") or getattr(getattr(ctx, "command", None), "name", "") or "").strip().lower()
    orgFeatureEnabled, orgFeatureKey = runtimeOrgFeatureGate.isCommandEnabledForGuild(config, guildId, commandName)
    if not orgFeatureEnabled:
        try:
            await ctx.reply(
                f"{_organizationFeatureUnavailableMessage} (`{orgFeatureKey}`)",
                mention_author=False,
            )
        except Exception:
            pass
        return False
    if _isCommandExecutionAllowed(int(ctx.author.id)):
        return True
    try:
        await ctx.reply(
            _temporaryLockMessage,
            mention_author=False,
        )
    except Exception:
        pass
    return False


async def interactionSafetyCheck(interaction: discord.Interaction) -> bool:
    if interaction.type is not discord.InteractionType.application_command:
        return True
    commandName = ""
    if isinstance(interaction.data, dict):
        commandName = str(interaction.data.get("name") or "").strip().lower()
    guildId = int(getattr(getattr(interaction, "guild", None), "id", 0) or 0)
    if not _isGuildAllowedForCommands(guildId):
        if guildId > 0:
            _runtimeTaskSupervisor.create(
                _mirrorUnapprovedGuildCommandAttempt(
                    commandName=_interactionCommandName(interaction),
                    userLabel=str(getattr(interaction, "user", "Unknown User")),
                    userId=int(getattr(getattr(interaction, "user", None), "id", 0) or 0),
                    guildName=str(getattr(getattr(interaction, "guild", None), "name", "Unknown Server")),
                    guildId=guildId,
                ),
                name=f"interaction-guild-lock-alert:{guildId}",
            )
        await _safeInteractionSend(
            interaction,
            _serverNotRecognizedMessage,
            ephemeral=True,
        )
        return False
    if not _isCommandExecutionAllowed(int(interaction.user.id)):
        await _safeInteractionSend(
            interaction,
            _temporaryLockMessage,
            ephemeral=True,
        )
        return False
    orgFeatureEnabled, orgFeatureKey = runtimeOrgFeatureGate.isCommandEnabledForGuild(config, guildId, commandName)
    if not orgFeatureEnabled:
        await _safeInteractionSend(
            interaction,
            f"{_organizationFeatureUnavailableMessage} (`{orgFeatureKey}`)",
            ephemeral=True,
        )
        return False
    if commandName not in _runtimeControlAllowedWhilePaused:
        if _pauseController.isPaused():
            await _safeInteractionSend(
                interaction,
                _runtimePausedMessage,
                ephemeral=True,
            )
            return False
        pausedCommand = _commandPauseController.resolve(guildId, _interactionCommandName(interaction))
        if pausedCommand is not None:
            await _safeInteractionSend(
                interaction,
                pausedCommand.message or _commandPausedDefaultMessage,
                ephemeral=True,
            )
            return False
    featureEnabled, featureKey, featureCacheHit = _featureFlags.isCommandEnabledCached(guildId, commandName)
    if not featureCacheHit:
        _featureFlags.refreshCommandFlagCacheSoon(guildId, commandName)
    if not featureEnabled:
        await _safeInteractionSend(
            interaction,
            f"This command is disabled in this server (feature `{featureKey}`).",
            ephemeral=True,
        )
        return False
    permissionResult = runtimeCommandPermissions.checkInteraction(
        config,
        interaction,
        command=getattr(interaction, "command", None),
        tree=botClient.tree,
    )
    if not permissionResult.allowed:
        await _safeInteractionSend(
            interaction,
            permissionResult.message or "You do not have permission to use this command.",
            ephemeral=True,
        )
        return False
    _pruneActiveInvocations()
    key = _invocationKey(interaction)
    if key in _activeAppCommandInvocations:
        await _safeInteractionSend(
            interaction,
            "That command is already running for you. Please wait for it to finish.",
            ephemeral=True,
        )
        return False
    _activeAppCommandInvocations[key] = datetime.now(timezone.utc)
    if isinstance(interaction.user, discord.Member):
        _runtimeTaskSupervisor.create(
            _scheduleRoleBasedOrbatSync(interaction.user, interaction.guild.id),
            name=f"role-orbat-sync:{interaction.user.id}",
        )
    return True


botClient.tree.interaction_check = interactionSafetyCheck


@botClient.event
async def on_app_command_completion(
    interaction: discord.Interaction,
    command: app_commands.Command | app_commands.ContextMenu,
) -> None:
    _activeAppCommandInvocations.pop(_invocationKey(interaction, command), None)
    _activeAppCommandInvocations.pop(_invocationKey(interaction), None)


@botClient.tree.error
async def onAppCommandError(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    _activeAppCommandInvocations.pop(_invocationKey(interaction), None)
    commandName = ""
    if isinstance(interaction.data, dict):
        commandName = str(interaction.data.get("name") or "")
    await _auditStream.logEvent(
        source="app-command",
        action="command error",
        guildId=int(getattr(getattr(interaction, "guild", None), "id", 0) or 0),
        actorId=int(getattr(getattr(interaction, "user", None), "id", 0) or 0),
        targetType="command",
        targetId=commandName or "unknown",
        severity="ERROR",
        details={"errorType": error.__class__.__name__, "error": str(error)},
        authorizedBy="runtime",
        postToDiscord=False,
    )
    await _errorCoordinator.handleAppCommandError(
        interaction=interaction,
        error=error,
        safeInteractionSend=lambda itx, message: _safeInteractionSend(
            itx,
            message,
            ephemeral=True,
        ),
    )


@botClient.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
    await _errorCoordinator.handlePrefixCommandError(ctx=ctx, error=error)


_runtimeCleanup = runtimeShutdown.OnceAsyncCleanup(taskName="jane-runtime-cleanup")
_originalBotClientClose = botClient.close


async def _runRuntimeCleanupServices() -> None:
    async def _runCleanupStep(label: str, awaitableFactory) -> None:
        try:
            await awaitableFactory()
        except Exception:
            logging.exception("Runtime cleanup step failed: %s", label)

    await _runCleanupStep("maintenance tasks", _maintenanceCoordinator.stopBackgroundTasks)
    await _runCleanupStep("event loop watchdog", _eventLoopWatchdog.stop)
    if _gitUpdateCoordinator is not None:
        await _runCleanupStep("git updater", _gitUpdateCoordinator.stop)
    await _runCleanupStep("webhook health watcher", _webhookHealthWatcher.stop)
    await _runCleanupStep("retry queue", _retryQueue.stop)
    await _runCleanupStep("feature flag refreshes", _featureFlags.stop)
    await _runCleanupStep("Jane Identity web callback", _janeIdentityWebServer.stop)
    await _runCleanupStep("gambling API", _gamblingApiServer.stop)
    await _runCleanupStep("John event backfill", _johnEventCoordinator.stop)
    await _runCleanupStep("training log tasks", _trainingLogRuntime.stop)
    if _humanMessageRouter is not None:
        await _runCleanupStep("human message routing tasks", _humanMessageRouter.stop)
    await _runCleanupStep("supervised runtime tasks", _runtimeTaskSupervisor.stop)
    await _runCleanupStep("Roblox HTTP session", robloxTransport.closeHttpSession)
    if bool(getattr(config, "dbRuntimeSnapshotOnShutdown", True)):
        async def _captureDbShutdownSnapshot() -> None:
            label = (
                "restart"
                if runtimeProcessControl is not None and runtimeProcessControl.restartRequested()
                else "shutdown"
            )
            capture = await runtimeBackups.captureRuntimeDbState(config, label=label)
            logging.info(
                "Runtime DB %s snapshot captured: snapshot=%s report=%s%s",
                label,
                capture.get("snapshotPath") or "none",
                capture.get("reportPath") or "none",
                f" error={capture.get('snapshotError')}" if capture.get("snapshotError") else "",
            )

        await _runCleanupStep("runtime DB snapshot", _captureDbShutdownSnapshot)
    await _runCleanupStep("runtime task stats", runtimeTaskStats.shutdown)
    await _runCleanupStep("SQLite connection", closeDb)


async def _cleanupRuntimeServices() -> None:
    await _runtimeCleanup.run(_runRuntimeCleanupServices)


async def _closeBotClientWithCleanup() -> None:
    await _cleanupRuntimeServices()
    await _originalBotClientClose()


botClient.close = _closeBotClientWithCleanup  # type: ignore[method-assign]


@botClient.event
async def on_close() -> None:
    await _cleanupRuntimeServices()

@botClient.event
async def on_message(message: discord.Message) -> None:
    _trainingLogRuntime.scheduleCapture(message)
    if not message.author.bot:
        await _getHumanMessageRouter().handle(message)
        return
    parsedEvents = await _johnEventCoordinator.parse(message)
    for event in parsedEvents:
        try:
            await _johnEventCoordinator.handleIngestedEvent(message, event)
        except Exception:
            logging.exception(
                "Event ingest handler failed (source=%s type=%s message=%s).",
                event.source,
                event.eventType,
                message.id,
            )
    return await botClient.process_commands(message)


@botClient.event
async def on_message_edit(before: discord.Message, after: discord.Message) -> None:
    if int(getattr(before, "id", 0) or 0) != int(getattr(after, "id", 0) or 0):
        return
    _trainingLogRuntime.scheduleCapture(after)

@botClient.listen("on_interaction")
async def handleRobloxRetry(interaction: discord.Interaction) -> None:
    if interaction.response.is_done():
        return
    try:
        handled = await sessionViews.handleRobloxRetryInteraction(interaction)
    except Exception:
        logging.exception("Roblox retry handler failed.")
        return
    if handled:
        return
    try:
        handled = await sessionViews.handleInventoryRetryInteraction(interaction)
    except Exception:
        logging.exception("Inventory retry handler failed.")
        return
    if handled:
        return
    try:
        handled = await sessionViews.handleSessionControlFallbackInteraction(interaction)
    except Exception:
        logging.exception("Session control fallback handler failed.")
        return
    if handled:
        return


if __name__ == "__main__":
    runtimeEntrypoint.runMain(
        botClient=botClient,
        configModule=config,
        singleInstanceLock=_singleInstanceLock,
        processControlModule=runtimeProcessControl,
        scriptPath=__file__,
    )

