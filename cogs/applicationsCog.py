from __future__ import annotations

import asyncio
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from features.staff.applications.panel import ApplicationsPanelView
from features.staff.applications.cogMixins.configMixin import ApplicationsConfigMixin
from features.staff.applications.cogMixins.flowMixin import ApplicationsFlowMixin
from features.staff.applications.cogMixins.opsMixin import ApplicationsOpsMixin
from runtime import interaction as interactionRuntime


class ApplicationsCog(ApplicationsConfigMixin, ApplicationsFlowMixin, ApplicationsOpsMixin, commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.divisions: dict[str, dict[str, Any]] = {}
        self.divisionOrder: list[str] = []
        self.appLocks: dict[int, asyncio.Lock] = {}
        self.divisionsConfigPath: Optional[str] = None
        self.loadDivisionConfig()

    @app_commands.command(name="applications", description="Open the applications manager panel.")
    async def applicationsPanel(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await self.safeReply(interaction, "This command can only be used in a server.")
        if not self.canUseApplicationsPanel(interaction.user):
            return await self.safeReply(interaction, "You are not authorized to use the applications manager panel.")

        embed = discord.Embed(
            title="Applications Manager",
            description=(
                "Use the panel buttons below to manage applications.\n"
                "This combines hub posting and staff queue tools."
            ),
            color=discord.Color.blurple(),
        )
        await interactionRuntime.safeInteractionReply(
            interaction,
            embed=embed,
            view=ApplicationsPanelView(
                self,
                canBulkClose=self.isServerAdministrator(interaction.user),
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ApplicationsCog(bot))

