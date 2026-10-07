from __future__ import annotations

import unittest

from cogs.staff import sessionCog


class SessionsCogCommandTests(unittest.TestCase):
    def _commandNames(self) -> list[str]:
        return [command.name for command in sessionCog.SessionsCog.__cog_app_commands__]

    def test_orientation_command_is_gone_after_cutover_to_john(self) -> None:
        self.assertNotIn("orientation", self._commandNames())

    def test_bg_add_command_is_still_registered(self) -> None:
        self.assertIn("bg-add", self._commandNames())


if __name__ == "__main__":
    unittest.main()
