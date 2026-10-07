from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from runtime import permissions as runtimePermissions
from silly import commands as sillyCommands


async def _deliver(
    interaction: discord.Interaction,
    outcome: sillyCommands.SillyOutcome,
    sendEmbed,
) -> None:
    if outcome.embed is None:
        await interaction.response.send_message(
            outcome.text or "Nothing to do.",
            ephemeral=outcome.ephemeral,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return
    # The joke is posted through the themed webhook, same as the text form;
    # the interaction itself only needs a quiet acknowledgement.
    await interaction.response.defer(ephemeral=True)
    sent = await sendEmbed(interaction.channel, interaction.client, outcome.embed)
    await interaction.followup.send("Done." if sent else "I couldn't post that here.", ephemeral=True)


class SillyCommandsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="kill", description="Schedule a fake reactor-themed execution message.")
    @app_commands.guild_only()
    @app_commands.describe(member="Who is being executed.")
    async def kill(self, interaction: discord.Interaction, member: discord.Member) -> None:
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return
        outcome = sillyCommands.buildKillOutcome(interaction.user, member)
        await _deliver(interaction, outcome, sillyCommands.sendKillEmbed)

    @app_commands.command(name="skin", description="Post the skin joke message about a member.")
    @app_commands.guild_only()
    @app_commands.describe(member="Who is being skinned.")
    async def skin(self, interaction: discord.Interaction, member: discord.Member) -> None:
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return
        outcome = sillyCommands.buildSkinOutcome(
            interaction.user,
            member,
            hasSkinPermission=runtimePermissions.hasCohostPermission,
        )
        await _deliver(interaction, outcome, sillyCommands.sendSkinEmbed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SillyCommandsCog(bot))
