from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord

from runtime.dailyMessage import DailyMessageTrigger
_POTATO_USER_ID = 331660652672319488
_POTATO_GENERAL_CHANNEL_ID = 1525783767971528764
_POTATO_GREETING = "good to see you, mom"
try:
    _POTATO_TIMEZONE = ZoneInfo("America/Chicago")
except ZoneInfoNotFoundError:
    # Keeps the greeting usable on a minimal Windows install without IANA data.
    _POTATO_TIMEZONE = timezone(timedelta(hours=-6), name="CST")
_POTATO_GREETING_TRIGGER = DailyMessageTrigger(
    key="potatoGreeting",
    userId=_POTATO_USER_ID,
    channelId=_POTATO_GENERAL_CHANNEL_ID,
    content=_POTATO_GREETING,
    timezoneInfo=_POTATO_TIMEZONE,
)


class TextCommandRouter:
    def __init__(self, *, botClient: Any, configModule: Any) -> None:
        self.botClient = botClient
        self.config = configModule

    @staticmethod
    async def handlePotatoGreeting(
        message: discord.Message,
        *,
        nowUtc: datetime | None = None,
    ) -> bool:
        return await _POTATO_GREETING_TRIGGER.handle(message, now=nowUtc)

    def _janeTerminalAllowedUserId(self) -> int:
        try:
            configured = int(
                getattr(self.config, "janeTerminalAllowedUserId", 0)
                or getattr(self.config, "errorMirrorUserId", 0)
                or 0
            )
        except (TypeError, ValueError):
            configured = 0
        return configured if configured > 0 else 0

