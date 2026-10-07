import discord
from discord.ext import commands
from discord import app_commands
from features.staff.sessions import bgAddQueue
from runtime import interaction as interactionRuntime
from runtime import permissions as runtimePermissions

class SessionsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _safeEphemeral(self, interaction: discord.Interaction, message: str) -> None:
        await interactionRuntime.safeInteractionReply(
            interaction,
            content=message,
            ephemeral=True,
        )

    def canStartBgCheckQueue(self, member: discord.Member) -> bool:
        return runtimePermissions.hasBgCheckCertifiedRole(member)

    @app_commands.command(name="bg-add", description="Add a Discord user to the next BGC spreadsheet.")
    @app_commands.guild_only()
    @app_commands.describe(user="Discord user to include in the next BGC spreadsheet.")
    async def bgAdd(self, interaction: discord.Interaction, user: discord.User):
        if not interaction.guild:
            return await self._safeEphemeral(interaction, "This command can only be used inside a server channel.")
        if not isinstance(interaction.user, discord.Member):
            return await self._safeEphemeral(interaction, "This command can only be used inside a server channel.")
        if not self.canStartBgCheckQueue(interaction.user):
            return await self._safeEphemeral(interaction, "You do not have permission to add users to the next BGC spreadsheet.")
        if bool(getattr(user, "bot", False)):
            return await self._safeEphemeral(interaction, "Bot accounts cannot be added to BGC spreadsheets.")

        await interactionRuntime.safeInteractionDefer(
            interaction,
            ephemeral=True,
            thinking=True,
        )
        result = await bgAddQueue.addPendingUser(
            guildId=int(interaction.guild.id),
            userId=int(user.id),
            addedBy=int(interaction.user.id),
        )
        action = "Added" if bool(result.get("created")) else "Refreshed"
        await self._safeEphemeral(
            interaction,
            (
                f"{action} {user.mention} for the next orientation BGC spreadsheet.\n"
                "This does not create a spreadsheet by itself.\n"
                f"Pending manual additions: `{int(result.get('pendingCount') or 0)}`"
            ),
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(SessionsCog(bot))


