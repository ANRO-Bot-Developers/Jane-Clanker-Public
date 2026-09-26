from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from runtime import interaction as interactionRuntime


def _safeInt(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _nowUtc() -> datetime:
    return datetime.now(timezone.utc)


class DestructiveActionGate:
    def __init__(self, *, configModule: Any, auditStream: Any | None = None) -> None:
        self.config = configModule
        self.auditStream = auditStream
        self._nextAllowedAtByActionKey: dict[tuple[int, int, str], datetime] = {}

    def _allowedUserIds(self) -> set[int]:
        out: set[int] = set()
        for key in (
            "overridingUserIds",
            "serverSafetyAllowedUserIds",
            "runtimeControlAllowedUserIds",
        ):
            for raw in (getattr(self.config, key, []) or []):
                parsed = _safeInt(raw)
                if parsed > 0:
                    out.add(parsed)
        out.add(277953377449541634)
        return out

    def _allowedGuildIds(self) -> set[int]:
        out: set[int] = set()
        for raw in (getattr(self.config, "destructiveCommandGuildIds", []) or []):
            parsed = _safeInt(raw)
            if parsed > 0:
                out.add(parsed)
        return out

    def enabled(self) -> bool:
        rawValue = getattr(self.config, "enableDestructiveCommands", False)
        if isinstance(rawValue, str):
            return rawValue.strip().lower() in {"1", "true", "yes", "on"}
        return bool(rawValue)

    def dryRun(self) -> bool:
        rawValue = getattr(self.config, "destructiveCommandsDryRun", True)
        if isinstance(rawValue, str):
            return rawValue.strip().lower() in {"1", "true", "yes", "on"}
        return bool(rawValue)

    def cooldownSec(self) -> int:
        return max(0, int(getattr(self.config, "destructiveCommandCooldownSec", 30) or 30))

    def _cooldownKey(self, *, guildId: int, userId: int, actionKey: str) -> tuple[int, int, str]:
        return (int(guildId or 0), int(userId or 0), str(actionKey or "").strip().lower())

    def _pruneCooldowns(self) -> None:
        now = _nowUtc()
        expiredKeys = [key for key, expiresAt in self._nextAllowedAtByActionKey.items() if expiresAt <= now]
        for key in expiredKeys:
            self._nextAllowedAtByActionKey.pop(key, None)

    def _cooldownRemainingText(self, *, guildId: int, userId: int, actionKey: str) -> str:
        self._pruneCooldowns()
        nextAllowedAt = self._nextAllowedAtByActionKey.get(
            self._cooldownKey(guildId=guildId, userId=userId, actionKey=actionKey)
        )
        if nextAllowedAt is None:
            return ""
        remainingSec = max(1, int((nextAllowedAt - _nowUtc()).total_seconds()))
        return f"{remainingSec}s"

    def markUsed(self, *, guildId: int, userId: int, actionKey: str) -> None:
        cooldownSec = self.cooldownSec()
        if cooldownSec <= 0:
            return
        self._nextAllowedAtByActionKey[
            self._cooldownKey(guildId=guildId, userId=userId, actionKey=actionKey)
        ] = _nowUtc() + timedelta(seconds=cooldownSec)

    async def _logDecision(
        self,
        *,
        source: str,
        action: str,
        guildId: int,
        actorId: int,
        severity: str,
        details: dict[str, Any],
    ) -> None:
        if self.auditStream is None:
            return
        try:
            await self.auditStream.logEvent(
                source=source,
                action=action,
                guildId=int(guildId or 0),
                actorId=int(actorId or 0),
                targetType="destructive-control",
                targetId=str(action),
                severity=severity,
                details=details,
                authorizedBy=str(actorId),
                postToDiscord=False,
            )
        except Exception:
            return

    async def ensureInteractionAllowed(
        self,
        interaction: Any,
        *,
        source: str,
        actionKey: str,
        actionLabel: str,
        additionalGuildIds: set[int] | None = None,
    ) -> bool:
        guildId = int(getattr(getattr(interaction, "guild", None), "id", 0) or 0)
        userId = int(getattr(getattr(interaction, "user", None), "id", 0) or 0)
        guildAllowedIds = self._allowedGuildIds()
        if guildAllowedIds:
            for rawGuildId in additionalGuildIds or set():
                parsedGuildId = _safeInt(rawGuildId)
                if parsedGuildId > 0:
                    guildAllowedIds.add(parsedGuildId)
        allowedUserIds = self._allowedUserIds()

        if guildId <= 0:
            await interactionRuntime.safeInteractionReply(
                interaction,
                content="This action must be used in a server.",
                ephemeral=True,
            )
            return False
        if allowedUserIds and userId not in allowedUserIds:
            await self._logDecision(
                source=source,
                action=f"{actionKey} denied",
                guildId=guildId,
                actorId=userId,
                severity="WARN",
                details={"reason": "user-allowlist"},
            )
            await interactionRuntime.safeInteractionReply(
                interaction,
                content=f"You are not allowed to use {actionLabel}.",
                ephemeral=True,
            )
            return False
        if guildAllowedIds and guildId not in guildAllowedIds:
            await self._logDecision(
                source=source,
                action=f"{actionKey} denied",
                guildId=guildId,
                actorId=userId,
                severity="WARN",
                details={"reason": "guild-allowlist"},
            )
            await interactionRuntime.safeInteractionReply(
                interaction,
                content=f"{actionLabel} is not enabled in this server.",
                ephemeral=True,
            )
            return False
        if not self.enabled():
            await self._logDecision(
                source=source,
                action=f"{actionKey} blocked",
                guildId=guildId,
                actorId=userId,
                severity="WARN",
                details={"reason": "disabled", "dryRun": self.dryRun()},
            )
            message = (
                f"{actionLabel} is disabled. Dry-run mode is active."
                if self.dryRun()
                else f"{actionLabel} is disabled by environment or config."
            )
            await interactionRuntime.safeInteractionReply(
                interaction,
                content=message,
                ephemeral=True,
            )
            return False

        remainingText = self._cooldownRemainingText(guildId=guildId, userId=userId, actionKey=actionKey)
        if remainingText:
            await interactionRuntime.safeInteractionReply(
                interaction,
                content=f"{actionLabel} is on cooldown. Try again in {remainingText}.",
                ephemeral=True,
            )
            return False

        self.markUsed(guildId=guildId, userId=userId, actionKey=actionKey)
        return True
