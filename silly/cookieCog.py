from __future__ import annotations

import random
import re

import discord
from discord import app_commands
from discord.ext import commands


cookieRequestRegex = re.compile(r"\bcookies?\b", re.IGNORECASE)

_cookieMessages = [
    "Here's a cookie for you, {user}! 🍪",
    "Fresh out of the oven, just for {user} 🍪",
    "{user} gets a cookie! Don't let anyone steal it 🍪",
    "One chocolate chip cookie, coming right up {user} 🍪",
    "I saved the last cookie for you, {user} 🍪",
    "*slides {user} a cookie across the table* 🍪",
    "Cookie delivered to {user}. Crumbs are your problem 🍪",
    "{user}, you've been a good soldier. Have a cookie 🍪",
    "Only because you asked nicely, {user} 🍪",
    "Here {user}, still warm 🍪",
]


def buildCookieMessage(userMention: str) -> str:
    return random.choice(_cookieMessages).format(user=userMention)


class CookieCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="cookie", description="Get a cookie from Jane.")
    async def cookie(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            buildCookieMessage(interaction.user.mention),
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CookieCog(bot))
