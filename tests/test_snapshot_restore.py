from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cogs.operations import serverSafetyCog as serverSafetyCogModule
from features.operations.serverSafety import preview as snapshotPreview
from features.operations.serverSafety.snapshotMenu import SnapshotMenuView
from features.operations.serverSafety.snapshotStore import readSnapshot


class _PreviewRole:
    def __init__(
        self,
        roleId: int,
        name: str,
        *,
        position: int,
        permissions: int = 0,
        color: int = 0,
        hoist: bool = False,
        mentionable: bool = False,
        isDefault: bool = False,
    ) -> None:
        self.id = roleId
        self.name = name
        self.position = position
        self.permissions = SimpleNamespace(value=permissions)
        self.color = SimpleNamespace(value=color)
        self.secondary_color = None
        self.tertiary_color = None
        self.hoist = hoist
        self.mentionable = mentionable
        self.managed = False
        self._isDefault = isDefault
        self._snapshotPreviewKind = "role"

    def is_default(self) -> bool:
        return self._isDefault


class _PreviewOverwrite:
    def __init__(self, allow: int, deny: int = 0) -> None:
        self.allow = allow
        self.deny = deny

    def pair(self):
        return SimpleNamespace(value=self.allow), SimpleNamespace(value=self.deny)


class _PreviewChannel:
    def __init__(
        self,
        channelId: int,
        name: str,
        channelType: str,
        *,
        position: int,
        categoryId: int = 0,
        overwrites: dict[object, _PreviewOverwrite] | None = None,
        **settings,
    ) -> None:
        self.id = channelId
        self.name = name
        self.type = channelType
        self.position = position
        self.category_id = categoryId or None
        self.overwrites = dict(overwrites or {})
        self.permissions_synced = bool(settings.pop("permissions_synced", False))
        for key, value in settings.items():
            setattr(self, key, value)


class SnapshotPayloadTests(unittest.TestCase):
    def test_read_snapshot_rejects_payload_for_another_guild(self) -> None:
        with tempfile.TemporaryDirectory() as tempDir:
            path = Path(tempDir) / "snapshot.json"
            path.write_text(json.dumps({"guild": {"id": 100}}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "belongs to guild 100"):
                readSnapshot(path, expectedGuildId=200)

    def test_server_safety_cog_only_exposes_snapshot_menu(self) -> None:
        commandNames = {
            command.name for command in serverSafetyCogModule.ServerSafetyCog.__cog_app_commands__
        }
        self.assertEqual(commandNames, {"snapshot-menu"})

    def test_restore_preview_summarizes_meaningful_differences(self) -> None:
        defaultRole = _PreviewRole(100, "@everyone", position=0, isDefault=True)
        changedRole = _PreviewRole(
            1,
            "Alpha Renamed",
            position=3,
            permissions=2,
            color=11,
            hoist=True,
        )
        fallbackRole = _PreviewRole(40, "Stable Role", position=4)
        extraRole = _PreviewRole(3, "Extra Role", position=5)

        category = _PreviewChannel(
            10,
            "Ops",
            "category",
            position=2,
            overwrites={changedRole: _PreviewOverwrite(0)},
        )
        missingFromSnapshotCategory = _PreviewChannel(
            12,
            "Extra Category",
            "category",
            position=3,
        )
        channel = _PreviewChannel(
            20,
            "general",
            "text",
            position=2,
            categoryId=10,
            overwrites={changedRole: _PreviewOverwrite(0)},
            topic="Live topic",
            nsfw=False,
            slowmode_delay=0,
        )
        extraChannel = _PreviewChannel(
            22,
            "extra-chat",
            "text",
            position=3,
            categoryId=10,
            topic="",
            nsfw=False,
            slowmode_delay=0,
        )
        misplacedAnnouncement = _PreviewChannel(
            23,
            "announcements",
            "text",
            position=1,
            topic="",
            nsfw=False,
            slowmode_delay=0,
        )
        member = SimpleNamespace(id=50, roles=[defaultRole, changedRole, fallbackRole])
        guild = SimpleNamespace(
            id=500,
            default_role=defaultRole,
            roles=[defaultRole, changedRole, fallbackRole, extraRole],
            channels=[
                category,
                missingFromSnapshotCategory,
                channel,
                extraChannel,
                misplacedAnnouncement,
            ],
            get_member=lambda memberId: member if memberId == 50 else None,
        )
        snapshot = {
            "roles": [
                {
                    "id": 100,
                    "name": "@everyone",
                    "position": 0,
                    "permissions": 0,
                    "color": 0,
                    "hoist": False,
                    "mentionable": False,
                    "managed": False,
                    "isDefault": True,
                },
                {
                    "id": 1,
                    "name": "Alpha",
                    "position": 1,
                    "permissions": 1,
                    "color": 10,
                    "hoist": False,
                    "mentionable": False,
                    "managed": False,
                    "isDefault": False,
                },
                {
                    "id": 2,
                    "name": "Missing Role",
                    "position": 2,
                    "permissions": 0,
                    "color": 0,
                    "hoist": False,
                    "mentionable": False,
                    "managed": False,
                    "isDefault": False,
                },
                {
                    "id": 4,
                    "name": "Stable Role",
                    "position": 4,
                    "permissions": 0,
                    "color": 0,
                    "hoist": False,
                    "mentionable": False,
                    "managed": False,
                    "isDefault": False,
                },
            ],
            "channels": [
                {
                    "id": 10,
                    "name": "Ops",
                    "type": "category",
                    "position": 1,
                    "overwrites": [{"kind": "role", "id": 1, "allow": 1, "deny": 0}],
                },
                {
                    "id": 11,
                    "name": "Missing Category",
                    "type": "category",
                    "position": 4,
                    "overwrites": [],
                },
                {
                    "id": 20,
                    "name": "general",
                    "type": "text",
                    "position": 1,
                    "categoryId": 10,
                    "permissionsSynced": False,
                    "overwrites": [{"kind": "role", "id": 1, "allow": 1, "deny": 0}],
                    "topic": "Snapshot topic",
                    "nsfw": False,
                    "slowmodeDelay": 0,
                },
                {
                    "id": 21,
                    "name": "announcements",
                    "type": "text",
                    "position": 1,
                    "categoryId": 11,
                    "permissionsSynced": False,
                    "overwrites": [],
                    "topic": "",
                    "nsfw": False,
                    "slowmodeDelay": 0,
                },
            ],
            "members": [
                {"userId": 50, "displayName": "Present", "roleIds": [1, 2]},
                {"userId": 51, "displayName": "Gone", "roleIds": []},
            ],
        }
        config = SimpleNamespace(
            serverSafetyIgnoredCategoryIds=[],
            serverSafetyPreservedChannelIds=[],
        )

        with (
            patch.object(snapshotPreview, "findSnapshotPath", return_value=Path("snapshot.json")),
            patch.object(snapshotPreview, "readSnapshot", return_value=snapshot),
            patch.object(snapshotPreview, "describeOffsiteSnapshot", return_value={"mirrored": True}),
        ):
            preview = snapshotPreview.buildRestorePreview(
                config,
                guild,
                snapshotFile="snapshot.json",
            )

        self.assertEqual(preview["missingRoles"], ["Missing Role"])
        self.assertEqual(preview["missingCategories"], ["Missing Category"])
        self.assertEqual(preview["missingChannels"], ["announcements"])
        self.assertEqual(preview["roleNameDifferenceCount"], 1)
        self.assertEqual(preview["roleColorDifferenceCount"], 1)
        self.assertEqual(preview["rolePositionDifferenceCount"], 1)
        self.assertEqual(preview["rolePermissionDifferenceCount"], 1)
        self.assertEqual(preview["roleDisplayDifferenceCount"], 1)
        self.assertEqual(preview["categoryPositionDifferenceCount"], 1)
        self.assertEqual(preview["categoryPermissionDifferenceCount"], 1)
        self.assertEqual(preview["channelPermissionDifferenceCount"], 1)
        self.assertEqual(preview["channelSettingDifferenceCount"], 1)
        self.assertEqual(preview["channelLayoutDifferenceCount"], 1)
        self.assertEqual(preview["membersWithRoleChanges"], 1)
        self.assertEqual(preview["memberRolesAdded"], 1)
        self.assertEqual(preview["memberRolesRemoved"], 1)
        self.assertEqual(preview["missingMemberCount"], 1)
        self.assertEqual(preview["extraRoleCount"], 1)
        self.assertEqual(preview["extraCategoryCount"], 1)
        self.assertEqual(preview["extraChannelCount"], 2)

        formatted = snapshotPreview.formatRestorePreview(preview)
        self.assertIn("Missing roles (1): `Missing Role`", formatted)
        self.assertIn("Channel differences", formatted)
        self.assertIn("Extra live items kept", formatted)
        self.assertLessEqual(len(formatted), 1024)


class SnapshotRestoreAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_menu_disables_controls_while_work_is_running(self) -> None:
        view = SnapshotMenuView(
            cog=SimpleNamespace(),
            openerId=1,
            snapshotRows=[
                {
                    "fileName": "snapshot.json",
                    "snapshotKind": "manual",
                }
            ],
            selectedSnapshotFile="snapshot.json",
        )
        interaction = SimpleNamespace()
        with patch.object(
            serverSafetyCogModule.runtimeViewBases,
            "safeRefreshInteractionMessage",
            new=AsyncMock(),
        ) as refresh:
            await view.acknowledgeWork(interaction)

        self.assertTrue(all(getattr(item, "disabled", False) for item in view.children))
        self.assertNotIn("Restore Selected", [getattr(item, "label", "") for item in view.children])
        refresh.assert_awaited_once_with(interaction, view=view)
