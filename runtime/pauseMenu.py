from __future__ import annotations

from typing import Any

import discord

from runtime import interaction as interactionRuntime
from runtime import viewBases as runtimeViewBases

commandsPerPage = 25


class CommandPauseMessageModal(discord.ui.Modal):
    """Collects the custom notice shown to users who run a paused command."""

    def __init__(
        self,
        *,
        parentView: "PauseMenuView",
        commandName: str,
        scopeLabel: str,
        defaultMessage: str = "",
    ) -> None:
        super().__init__(title=f"Pause /{commandName}"[:45])
        self.parentView = parentView
        self.commandName = commandName
        self.messageInput = discord.ui.TextInput(
            label="Message shown to users",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=1000,
            default=(defaultMessage or None),
            placeholder=f"Scope: {scopeLabel}. Leave blank to use Jane's default notice.",
        )
        self.add_item(self.messageInput)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.parentView.noticeText = await self.parentView.cog.pauseCommandFromPanel(
            interaction,
            view=self.parentView,
            commandName=self.commandName,
            message=str(self.messageInput.value or ""),
        )
        await self.parentView.refresh(interaction)


class PauseMenuView(runtimeViewBases.OwnerLockedView):
    def __init__(self, *, cog: Any, openerId: int) -> None:
        super().__init__(
            openerId=openerId,
            timeout=900,
            ownerMessage="This pause panel belongs to someone else.",
        )
        self.cog = cog
        self.noticeText = ""
        self.selectedCommand = ""
        self.globalScope = False
        self.pageIndex = 0
        self.commandNames: list[str] = []

    @property
    def pageCount(self) -> int:
        if not self.commandNames:
            return 1
        return max(1, (len(self.commandNames) + commandsPerPage - 1) // commandsPerPage)

    def commandsOnCurrentPage(self) -> list[str]:
        start = self.pageIndex * commandsPerPage
        return self.commandNames[start:start + commandsPerPage]

    async def refresh(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return
        self.cog.configurePauseButtons(self, guild=guild)
        embed = self.cog.buildPauseEmbed(guild, noticeText=self.noticeText, view=self)
        await runtimeViewBases.safeRefreshInteractionMessage(
            interaction,
            embed=embed,
            view=self,
        )

    @discord.ui.button(label="Pause Jane", style=discord.ButtonStyle.danger, row=0)
    async def toggleBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.noticeText = await self.cog.togglePauseFromPanel(interaction)
        await self.refresh(interaction)

    @discord.ui.select(placeholder="Select a command to pause or resume.", row=1)
    async def commandSelect(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        self.selectedCommand = str(select.values[0]) if select.values else ""
        self.noticeText = f"Selected `/{self.selectedCommand}`." if self.selectedCommand else ""
        await self.refresh(interaction)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, row=2)
    async def prevPageBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.pageIndex = max(0, self.pageIndex - 1)
        await self.refresh(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=2)
    async def nextPageBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.pageIndex = min(self.pageCount - 1, self.pageIndex + 1)
        await self.refresh(interaction)

    @discord.ui.button(label="Scope: This Server", style=discord.ButtonStyle.secondary, row=2)
    async def scopeBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.globalScope = not self.globalScope
        self.noticeText = (
            "Scope set to every server."
            if self.globalScope
            else "Scope set to this server only."
        )
        await self.refresh(interaction)

    @discord.ui.button(label="Pause Command", style=discord.ButtonStyle.danger, row=3)
    async def pauseCommandBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not self.selectedCommand:
            self.noticeText = "Select a command first."
            await self.refresh(interaction)
            return
        modal = CommandPauseMessageModal(
            parentView=self,
            commandName=self.selectedCommand,
            scopeLabel=self.cog.scopeLabel(self),
            defaultMessage=self.cog.existingPauseMessage(self, guild=interaction.guild),
        )
        await interactionRuntime.safeInteractionSendModal(interaction, modal)

    @discord.ui.button(label="Resume Command", style=discord.ButtonStyle.success, row=3)
    async def resumeCommandBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not self.selectedCommand:
            self.noticeText = "Select a command first."
            await self.refresh(interaction)
            return
        self.noticeText = await self.cog.resumeCommandFromPanel(
            interaction,
            view=self,
            commandName=self.selectedCommand,
        )
        await self.refresh(interaction)
