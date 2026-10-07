from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import discord

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MessageRoutingMessages:
    runtimePaused: str
    serverNotRecognized: str
    organizationFeatureUnavailable: str
    temporaryLock: str


_MENTION_COMMANDS = frozenset({"kill", "skin"})


class HumanMessageRouter:
    """Route non-bot messages. Only DMs and messages that mention Jane carry text."""

    def __init__(
        self,
        *,
        botClient,
        configModule,
        pauseController,
        orgFeatureGateModule,
        sillyCommandsModule,
        textCommandRouterProvider: Callable[[], Any],
        hasCohostPermission: Callable[[discord.Member], bool],
        isCommandExecutionAllowed: Callable[[int], bool],
        isGuildAllowedForCommands: Callable[[int], bool],
        mirrorUnapprovedGuildCommandAttempt: Callable[..., Awaitable[None]],
        messages: MessageRoutingMessages,
    ) -> None:
        self.botClient = botClient
        self.config = configModule
        self.pauseController = pauseController
        self.orgFeatureGate = orgFeatureGateModule
        self.sillyCommands = sillyCommandsModule
        self.textCommandRouterProvider = textCommandRouterProvider
        self.hasCohostPermission = hasCohostPermission
        self.isCommandExecutionAllowed = isCommandExecutionAllowed
        self.isGuildAllowedForCommands = isGuildAllowedForCommands
        self.mirrorUnapprovedGuildCommandAttempt = mirrorUnapprovedGuildCommandAttempt
        self.messages = messages
        self._backgroundTasks: set[asyncio.Task] = set()

    @staticmethod
    def _guildId(message: discord.Message) -> int:
        return int(getattr(getattr(message, "guild", None), "id", 0) or 0)

    async def _sendQuietly(self, message: discord.Message, content: str) -> None:
        try:
            await message.channel.send(content)
        except Exception:
            pass

    async def _passesOrganizationGate(
        self,
        message: discord.Message,
        guildId: int,
        token: str,
    ) -> bool:
        enabled, featureKey = self.orgFeatureGate.isTokenEnabledForGuild(
            self.config,
            guildId,
            token,
        )
        if enabled:
            return True
        await self._sendQuietly(
            message,
            f"{self.messages.organizationFeatureUnavailable} (`{featureKey}`)",
        )
        return False

    def _startBackgroundTask(self, awaitable: Awaitable[None], *, name: str) -> None:
        task = asyncio.create_task(awaitable, name=name)
        self._backgroundTasks.add(task)

        def _done(doneTask: asyncio.Task) -> None:
            self._backgroundTasks.discard(doneTask)
            if doneTask.cancelled():
                return
            try:
                doneTask.result()
            except Exception:
                log.exception("Human message routing background task failed: %s", name)

        task.add_done_callback(_done)

    async def _rejectUnapprovedGuild(
        self,
        message: discord.Message,
        *,
        token: str,
        guildId: int,
    ) -> None:
        if guildId > 0:
            self._startBackgroundTask(
                self.mirrorUnapprovedGuildCommandAttempt(
                    commandName=token,
                    userLabel=str(message.author),
                    userId=int(message.author.id),
                    guildName=str(getattr(message.guild, "name", "Unknown Server")),
                    guildId=guildId,
                ),
                name=f"unapproved-guild-command-{guildId}-{int(message.author.id)}",
            )
        await self._sendQuietly(message, self.messages.serverNotRecognized)

    async def _handleMentionCommand(self, message: discord.Message, word: str, rest: str) -> None:
        guildId = self._guildId(message)
        if not await self._passesOrganizationGate(message, guildId, f"!{word}"):
            return
        if not self.isGuildAllowedForCommands(guildId):
            await self._rejectUnapprovedGuild(message, token=word, guildId=guildId)
            return
        if self.pauseController.isPaused():
            await self._sendQuietly(message, self.messages.runtimePaused)
            return
        if not self.isCommandExecutionAllowed(int(message.author.id)):
            await self._sendQuietly(message, self.messages.temporaryLock)
            return
        if word == "kill":
            await self.sillyCommands.handleKillMention(message, self.botClient, rest)
            return
        await self.sillyCommands.handleSkinMention(
            message,
            self.botClient,
            rest,
            hasSkinPermission=self.hasCohostPermission,
        )

    async def handle(self, message: discord.Message) -> None:
        textRouter = self.textCommandRouterProvider()
        await textRouter.handlePotatoGreeting(message)
        if await textRouter.handleJaneSecrets(message):
            return

        botUser = getattr(self.botClient, "user", None)
        if botUser is None:
            return
        if not any(int(user.id) == int(botUser.id) for user in message.mentions):
            return

        word, rest = self.sillyCommands.parseMentionCommand(message.content or "", int(botUser.id))
        if word in _MENTION_COMMANDS:
            # kill/skin need a server; silly replies below also work in DMs.
            if getattr(message, "guild", None) is not None:
                await self._handleMentionCommand(message, word, rest)
            return
        if self.pauseController.isPaused():
            return
        await self.sillyCommands.maybeHandleSillyMentions(message, self.botClient)

    async def stop(self) -> None:
        tasks = set(self._backgroundTasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._backgroundTasks.clear()
