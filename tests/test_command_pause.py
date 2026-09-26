from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from runtime.commandPause import (
    CommandPauseController,
    commandNameCandidates,
    normalizeCommandName,
)


def _row(
    *,
    guildId: int,
    commandName: str,
    paused: int = 1,
    pauseMessage: str = "",
    updatedBy: int = 7,
    updatedAt: str = "2026-08-30T00:00:00+00:00",
) -> dict:
    return {
        "guildId": guildId,
        "commandName": commandName,
        "paused": paused,
        "pauseMessage": pauseMessage,
        "updatedBy": updatedBy,
        "updatedAt": updatedAt,
    }


class CommandNameNormalizationTests(unittest.TestCase):
    def test_strips_slash_and_lowercases(self) -> None:
        self.assertEqual(normalizeCommandName("/Link-Hub  Add "), "link-hub add")

    def test_drops_unsupported_characters(self) -> None:
        self.assertEqual(normalizeCommandName("ribbons!!/request"), "ribbons request")

    def test_blank_input_returns_empty(self) -> None:
        self.assertEqual(normalizeCommandName(None), "")

    def test_candidates_run_from_specific_to_general(self) -> None:
        self.assertEqual(
            commandNameCandidates("link-hub section add"),
            ["link-hub section add", "link-hub section", "link-hub"],
        )


class CommandPauseResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def _controller(self, rows: list[dict]) -> CommandPauseController:
        controller = CommandPauseController(defaultMessage="Paused by staff.")
        with patch("runtime.commandPause.fetchAll", AsyncMock(return_value=rows)):
            await controller.loadAll()
        return controller

    async def test_unpaused_command_resolves_to_none(self) -> None:
        controller = await self._controller([])

        self.assertIsNone(controller.resolve(20, "recruitment"))
        self.assertFalse(controller.isPaused(20, "recruitment"))
        self.assertEqual(controller.messageFor(20, "recruitment"), "")

    async def test_global_pause_applies_to_every_guild(self) -> None:
        controller = await self._controller(
            [_row(guildId=0, commandName="recruitment", pauseMessage="Sheet migration until Monday.")]
        )

        entry = controller.resolve(20, "recruitment")

        self.assertIsNotNone(entry)
        self.assertTrue(entry.isGlobal)
        self.assertEqual(controller.messageFor(20, "recruitment"), "Sheet migration until Monday.")
        self.assertEqual(controller.messageFor(99, "recruitment"), "Sheet migration until Monday.")

    async def test_guild_pause_does_not_leak_to_other_guilds(self) -> None:
        controller = await self._controller([_row(guildId=20, commandName="ribbons")])

        self.assertTrue(controller.isPaused(20, "ribbons"))
        self.assertFalse(controller.isPaused(21, "ribbons"))

    async def test_guild_row_overrides_global_pause(self) -> None:
        controller = await self._controller(
            [
                _row(guildId=0, commandName="ribbons", pauseMessage="Global hold."),
                _row(guildId=20, commandName="ribbons", paused=0),
            ]
        )

        self.assertFalse(controller.isPaused(20, "ribbons"))
        self.assertTrue(controller.isPaused(21, "ribbons"))

    async def test_guild_pause_wins_over_global_exemption(self) -> None:
        controller = await self._controller(
            [
                _row(guildId=0, commandName="ribbons", paused=0),
                _row(guildId=20, commandName="ribbons", pauseMessage="Local hold."),
            ]
        )

        self.assertEqual(controller.messageFor(20, "ribbons"), "Local hold.")
        self.assertFalse(controller.isPaused(21, "ribbons"))

    async def test_group_pause_covers_subcommands(self) -> None:
        controller = await self._controller([_row(guildId=0, commandName="link-hub")])

        entry = controller.resolve(20, "link-hub add")

        self.assertIsNotNone(entry)
        self.assertEqual(entry.commandName, "link-hub")

    async def test_subcommand_exemption_beats_group_pause(self) -> None:
        controller = await self._controller(
            [
                _row(guildId=0, commandName="link-hub"),
                _row(guildId=0, commandName="link-hub add", paused=0),
            ]
        )

        self.assertFalse(controller.isPaused(20, "link-hub add"))
        self.assertTrue(controller.isPaused(20, "link-hub remove"))

    async def test_missing_message_falls_back_to_default(self) -> None:
        controller = await self._controller([_row(guildId=0, commandName="recruitment")])

        self.assertEqual(controller.messageFor(20, "recruitment"), "Paused by staff.")

    async def test_load_failure_leaves_commands_running(self) -> None:
        controller = CommandPauseController()
        with patch("runtime.commandPause.fetchAll", AsyncMock(side_effect=RuntimeError("db down"))):
            loaded = await controller.loadAll()

        self.assertEqual(loaded, 0)
        self.assertTrue(controller.isLoaded)
        self.assertFalse(controller.isPaused(20, "recruitment"))


class CommandPauseWriteTests(unittest.IsolatedAsyncioTestCase):
    async def test_set_pause_persists_and_updates_memory(self) -> None:
        controller = CommandPauseController()
        executeMock = AsyncMock()
        with patch("runtime.commandPause.execute", executeMock):
            entry = await controller.setPause(
                commandName="/Recruitment",
                paused=True,
                guildId=20,
                message="  Back Monday.  ",
                actorId=9,
            )

        self.assertEqual(entry.commandName, "recruitment")
        self.assertEqual(entry.message, "Back Monday.")
        self.assertEqual(entry.guildId, 20)
        self.assertTrue(controller.isPaused(20, "recruitment"))
        executeMock.assert_awaited_once()
        params = executeMock.await_args.args[1]
        self.assertEqual(params[0], 20)
        self.assertEqual(params[1], "recruitment")
        self.assertEqual(params[2], 1)
        self.assertEqual(params[3], "Back Monday.")

    async def test_set_pause_rejects_blank_command_name(self) -> None:
        controller = CommandPauseController()
        executeMock = AsyncMock()
        with patch("runtime.commandPause.execute", executeMock):
            entry = await controller.setPause(commandName="   ", paused=True)

        self.assertIsNone(entry)
        executeMock.assert_not_awaited()

    async def test_clear_pause_removes_row_and_memory(self) -> None:
        controller = CommandPauseController()
        with patch("runtime.commandPause.execute", AsyncMock()):
            await controller.setPause(commandName="ribbons", paused=True, guildId=20)
            removed = await controller.clearPause(commandName="ribbons", guildId=20)
            removedAgain = await controller.clearPause(commandName="ribbons", guildId=20)

        self.assertTrue(removed)
        self.assertFalse(removedAgain)
        self.assertFalse(controller.isPaused(20, "ribbons"))

    async def test_list_entries_scopes_to_guild_and_global(self) -> None:
        controller = CommandPauseController()
        with patch("runtime.commandPause.execute", AsyncMock()):
            await controller.setPause(commandName="alpha", paused=True, guildId=0)
            await controller.setPause(commandName="beta", paused=True, guildId=20)
            await controller.setPause(commandName="gamma", paused=True, guildId=21)

        names = [entry.commandName for entry in controller.listEntries(guildId=20)]

        self.assertEqual(names, ["alpha", "beta"])
        self.assertEqual(len(controller.listEntries()), 3)

    async def test_prune_drops_exemptions_that_no_longer_override(self) -> None:
        controller = CommandPauseController()
        with patch("runtime.commandPause.execute", AsyncMock()):
            await controller.setPause(commandName="ribbons", paused=True, guildId=0)
            await controller.setPause(commandName="ribbons", paused=False, guildId=20)
            await controller.clearPause(commandName="ribbons", guildId=0)
            pruned = await controller.pruneRedundantExemptions("ribbons")

        self.assertEqual(pruned, [20])
        self.assertEqual(controller.listEntries(), [])

    async def test_prune_keeps_exemptions_that_still_override(self) -> None:
        controller = CommandPauseController()
        with patch("runtime.commandPause.execute", AsyncMock()):
            await controller.setPause(commandName="link-hub", paused=True, guildId=0)
            await controller.setPause(commandName="link-hub add", paused=False, guildId=20)
            pruned = await controller.pruneRedundantExemptions("link-hub add")

        self.assertEqual(pruned, [])
        self.assertFalse(controller.isPaused(20, "link-hub add"))

    async def test_snapshot_counts_only_active_pauses(self) -> None:
        controller = CommandPauseController()
        with patch("runtime.commandPause.execute", AsyncMock()):
            await controller.setPause(commandName="alpha", paused=True, guildId=0)
            await controller.setPause(commandName="alpha", paused=False, guildId=20)

        snapshot = controller.snapshot()

        self.assertTrue(snapshot["loaded"] is False)
        self.assertEqual(snapshot["pausedCount"], 1)
        self.assertEqual(len(snapshot["entries"]), 2)


if __name__ == "__main__":
    unittest.main()
