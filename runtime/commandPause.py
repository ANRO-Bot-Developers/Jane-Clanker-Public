from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from db.sqlite import execute, fetchAll

log = logging.getLogger(__name__)

GLOBAL_SCOPE_ID = 0
# Runtime controls must stay reachable so a pause can always be undone.
# Mirrors _runtimeControlAllowedWhilePaused in bot.py.
EXEMPT_COMMAND_NAMES = frozenset({"pause", "restart"})
_maxPauseMessageLength = 1500
_defaultPauseMessage = "This command is paused right now. Please try again later."


def _safeInt(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def normalizeCommandName(value: object) -> str:
    """Normalize a slash command path to the form stored in the database.

    Accepts "/Link-Hub Add", "link-hub add", or "link-hub" and returns a
    lowercase space-separated path such as "link-hub add".
    """
    text = str(value or "").strip().lower().lstrip("/")
    cleaned = "".join(ch if (ch.isalnum() or ch in {"-", "_", " "}) else " " for ch in text)
    return " ".join(cleaned.split())


def commandNameCandidates(commandName: str) -> list[str]:
    """Return the command path and each of its ancestors, most specific first."""
    normalized = normalizeCommandName(commandName)
    if not normalized:
        return []
    parts = normalized.split(" ")
    return [" ".join(parts[: index + 1]) for index in range(len(parts))][::-1]


@dataclass(frozen=True, slots=True)
class CommandPauseEntry:
    guildId: int
    commandName: str
    paused: bool
    message: str
    updatedBy: int
    updatedAt: str

    @property
    def isGlobal(self) -> bool:
        return int(self.guildId) == GLOBAL_SCOPE_ID

    @property
    def scopeLabel(self) -> str:
        return "Global" if self.isGlobal else f"Guild {self.guildId}"

    def asDict(self) -> dict[str, Any]:
        return {
            "guildId": int(self.guildId),
            "commandName": str(self.commandName),
            "paused": bool(self.paused),
            "message": str(self.message),
            "updatedBy": int(self.updatedBy),
            "updatedAt": str(self.updatedAt),
        }


class CommandPauseController:
    """Per-command pause state with a custom notice, persisted in SQLite.

    Rows are mirrored in memory so command gating stays synchronous. A guild
    row overrides a global row for the same command path, and a row with
    paused = 0 is an explicit exemption that lets a guild keep running a
    command that is paused globally.
    """

    def __init__(self, *, defaultMessage: str = _defaultPauseMessage) -> None:
        self.defaultMessage = str(defaultMessage or _defaultPauseMessage).strip() or _defaultPauseMessage
        self._entries: dict[tuple[int, str], CommandPauseEntry] = {}
        self._loaded = False

    @property
    def isLoaded(self) -> bool:
        return self._loaded

    def _key(self, guildId: int, commandName: str) -> tuple[int, str]:
        return max(_safeInt(guildId), 0), normalizeCommandName(commandName)

    def _rowToEntry(self, row: dict[str, Any]) -> CommandPauseEntry | None:
        commandName = normalizeCommandName(row.get("commandName"))
        if not commandName:
            return None
        return CommandPauseEntry(
            guildId=max(_safeInt(row.get("guildId")), 0),
            commandName=commandName,
            paused=bool(_safeInt(row.get("paused"))),
            message=str(row.get("pauseMessage") or "").strip(),
            updatedBy=_safeInt(row.get("updatedBy")),
            updatedAt=str(row.get("updatedAt") or ""),
        )

    async def loadAll(self) -> int:
        """Load persisted pauses into memory. Safe to call more than once."""
        try:
            rows = await fetchAll(
                """
                SELECT guildId, commandName, paused, pauseMessage, updatedBy, updatedAt
                FROM command_pauses
                ORDER BY guildId ASC, commandName ASC
                """,
                (),
            )
        except Exception:
            log.exception("Failed to load command pauses; treating all commands as active.")
            self._loaded = True
            return 0

        entries: dict[tuple[int, str], CommandPauseEntry] = {}
        for row in rows or []:
            entry = self._rowToEntry(row)
            if entry is None:
                continue
            entries[(entry.guildId, entry.commandName)] = entry
        self._entries = entries
        self._loaded = True
        return len(entries)

    def resolve(self, guildId: int, commandName: str) -> CommandPauseEntry | None:
        """Return the pause entry blocking this command, or None if it may run.

        Checks the full command path first, then each parent group, so pausing
        a group pauses its subcommands. Guild rows win over global rows.
        """
        safeGuildId = max(_safeInt(guildId), 0)
        scopeIds = (safeGuildId, GLOBAL_SCOPE_ID) if safeGuildId > 0 else (GLOBAL_SCOPE_ID,)
        for candidate in commandNameCandidates(commandName):
            for scopeId in scopeIds:
                entry = self._entries.get((scopeId, candidate))
                if entry is None:
                    continue
                return entry if entry.paused else None
        return None

    def isPaused(self, guildId: int, commandName: str) -> bool:
        return self.resolve(guildId, commandName) is not None

    def messageFor(self, guildId: int, commandName: str) -> str:
        entry = self.resolve(guildId, commandName)
        if entry is None:
            return ""
        return entry.message or self.defaultMessage

    def getEntry(self, guildId: int, commandName: str) -> CommandPauseEntry | None:
        return self._entries.get(self._key(guildId, commandName))

    def listEntries(self, *, guildId: int | None = None) -> list[CommandPauseEntry]:
        """List stored entries. Passing a guild id returns that guild's rows plus global rows."""
        entries = list(self._entries.values())
        if guildId is not None:
            safeGuildId = max(_safeInt(guildId), 0)
            entries = [
                entry
                for entry in entries
                if entry.guildId in {safeGuildId, GLOBAL_SCOPE_ID}
            ]
        return sorted(entries, key=lambda entry: (entry.guildId, entry.commandName))

    async def setPause(
        self,
        *,
        commandName: str,
        paused: bool,
        guildId: int = GLOBAL_SCOPE_ID,
        message: str = "",
        actorId: int = 0,
    ) -> CommandPauseEntry | None:
        safeGuildId, safeCommandName = self._key(guildId, commandName)
        if not safeCommandName:
            return None
        entry = CommandPauseEntry(
            guildId=safeGuildId,
            commandName=safeCommandName,
            paused=bool(paused),
            message=str(message or "").strip()[:_maxPauseMessageLength],
            updatedBy=_safeInt(actorId),
            updatedAt=datetime.now(timezone.utc).isoformat(),
        )
        await execute(
            """
            INSERT INTO command_pauses (
                guildId, commandName, paused, pauseMessage, updatedBy, updatedAt
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(guildId, commandName)
            DO UPDATE SET
                paused = excluded.paused,
                pauseMessage = excluded.pauseMessage,
                updatedBy = excluded.updatedBy,
                updatedAt = excluded.updatedAt
            """,
            (
                entry.guildId,
                entry.commandName,
                1 if entry.paused else 0,
                entry.message,
                entry.updatedBy,
                entry.updatedAt,
            ),
        )
        self._entries[(entry.guildId, entry.commandName)] = entry
        return entry

    async def clearPause(self, *, commandName: str, guildId: int = GLOBAL_SCOPE_ID) -> bool:
        """Remove a stored row so the command inherits the next scope up."""
        key = self._key(guildId, commandName)
        if not key[1]:
            return False
        await execute(
            "DELETE FROM command_pauses WHERE guildId = ? AND commandName = ?",
            (key[0], key[1]),
        )
        return self._entries.pop(key, None) is not None

    async def pruneRedundantExemptions(self, commandName: str) -> list[int]:
        """Drop paused = 0 rows that no longer override anything.

        Called after a wider pause is lifted so stale "allowed here" rows do
        not linger in the pause panel.
        """
        normalized = normalizeCommandName(commandName)
        if not normalized:
            return []
        candidates = [
            entry
            for entry in self._entries.values()
            if not entry.paused and entry.commandName == normalized and entry.guildId > 0
        ]
        prunedGuildIds: list[int] = []
        for entry in candidates:
            key = (entry.guildId, entry.commandName)
            self._entries.pop(key, None)
            if self.resolve(entry.guildId, entry.commandName) is not None:
                self._entries[key] = entry
                continue
            await execute(
                "DELETE FROM command_pauses WHERE guildId = ? AND commandName = ?",
                (entry.guildId, entry.commandName),
            )
            prunedGuildIds.append(entry.guildId)
        return prunedGuildIds

    def snapshot(self) -> dict[str, Any]:
        entries = self.listEntries()
        return {
            "loaded": bool(self._loaded),
            "pausedCount": sum(1 for entry in entries if entry.paused),
            "entries": [entry.asDict() for entry in entries],
        }
