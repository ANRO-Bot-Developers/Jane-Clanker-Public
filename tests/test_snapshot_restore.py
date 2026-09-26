from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from cogs.operations import serverSafetyCog as serverSafetyCogModule
from features.operations.serverSafety import preview as snapshotPreview
from features.operations.serverSafety import restoreChannels
from features.operations.serverSafety.restoreMembers import restoreMemberRoles
from features.operations.serverSafety.restoreApproval import matchesRestoreConfirmation
from features.operations.serverSafety.restoreReport import formatSnapshotRestoreResult
from features.operations.serverSafety.snapshotMenu import SnapshotMenuView
from features.operations.serverSafety.snapshotStore import readSnapshot
from runtime import copyServerState
from runtime.destructiveControl import DestructiveActionGate


def _forbidden() -> discord.Forbidden:
    response = SimpleNamespace(status=403, reason="Forbidden")
    return discord.Forbidden(response, {"message": "nope", "code": 50013})


class _FakeRole:
    def __init__(self, roleId: int, *, position: int = 1) -> None:
        self.id = roleId
        self.position = position
        self.managed = False

    def is_default(self) -> bool:
        return False


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


class _FakeMember:
    def __init__(self, memberId: int, roles: list[_FakeRole], *, fail: bool = False) -> None:
        self.id = memberId
        self.display_name = f"member-{memberId}"
        self.roles = roles
        self.fail = fail
        self.editedRoles: list[_FakeRole] = []

    async def edit(self, *, roles: list[_FakeRole], reason: str) -> None:
        del reason
        if self.fail:
            raise _forbidden()
        self.editedRoles = list(roles)


class _FakeStageInstance:
    def __init__(self) -> None:
        self.topics: list[str] = []

    async def edit(self, *, topic: str, reason: str) -> None:
        del reason
        self.topics.append(topic)


class _FakeStageChannel(discord.StageChannel):
    def __init__(self, channelId: int, stageInstance: _FakeStageInstance) -> None:
        self.id = channelId
        self.name = "stage-room"
        self.category_id = None
        self._fakeStageInstance = stageInstance
        self.editOptions: dict[str, object] = {}

    @property
    def instance(self) -> _FakeStageInstance:
        return self._fakeStageInstance

    async def edit(self, **options):
        self.editOptions = dict(options)
        return self


class _FakeCategoryChannel(discord.CategoryChannel):
    def __init__(self, channelId: int) -> None:
        self.id = channelId
        self.name = "staff"
        self.position = 1

    async def edit(self, **options):
        del options
        raise _forbidden()


async def _runImmediately(awaitable, **kwargs):
    del kwargs
    return await awaitable


class SnapshotPayloadTests(unittest.TestCase):
    def test_read_snapshot_rejects_payload_for_another_guild(self) -> None:
        with tempfile.TemporaryDirectory() as tempDir:
            path = Path(tempDir) / "snapshot.json"
            path.write_text(json.dumps({"guild": {"id": 100}}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "belongs to guild 100"):
                readSnapshot(path, expectedGuildId=200)

    def test_restore_report_distinguishes_success_pause_and_failure(self) -> None:
        success = formatSnapshotRestoreResult({"snapshotFile": "good.json"})
        paused = formatSnapshotRestoreResult(
            {
                "snapshotFile": "paused.json",
                "batchPaused": True,
                "pauseReason": "timeout-create",
                "contiguousCompletedRoles": 12,
                "totalRoles": 20,
            }
        )
        failed = formatSnapshotRestoreResult(
            {
                "snapshotFile": "partial.json",
                "hadFailures": True,
                "channelFailures": 2,
                "failedChannelNames": ["general"],
            }
        )

        self.assertIn("Restore complete", success)
        self.assertIn("Restore paused", paused)
        self.assertIn("12/20", paused)
        self.assertIn("finished with failures", failed)
        self.assertIn("channels: general", failed)

    def test_restore_confirmation_phrase_is_explicit(self) -> None:
        self.assertTrue(matchesRestoreConfirmation("confirm restore"))
        self.assertTrue(matchesRestoreConfirmation("  CONFIRM RESTORE  "))
        self.assertFalse(matchesRestoreConfirmation("confirm"))
        self.assertFalse(matchesRestoreConfirmation("restore"))

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


class CopyServerProvenanceTests(unittest.TestCase):
    def test_provenance_survives_resume_state_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tempDir:
            root = Path(tempDir)
            legacyPath = root / "legacy.json"
            with (
                patch.object(copyServerState, "_stateRoot", return_value=root),
                patch.object(copyServerState, "_legacyStatePath", return_value=legacyPath),
            ):
                copyServerState.saveGuildState(
                    200,
                    sourceGuildId=100,
                    sourceGuildLabel="Main",
                    snapshotPath="main.json",
                )
                self.assertEqual(copyServerState.loadGuildProvenance(200)["sourceGuildId"], 100)

                copyServerState.saveGuildProvenance(
                    200,
                    sourceGuildId=100,
                    sourceGuildLabel="Main",
                    snapshotPath="main.json",
                )
                copyServerState.clearGuildState(200)

                provenance = copyServerState.loadGuildProvenance(200)
                self.assertIsNotNone(provenance)
                self.assertEqual(provenance["sourceGuildId"], 100)

    def test_restore_host_requires_main_or_approved_copy_target(self) -> None:
        cog = object.__new__(serverSafetyCogModule.ServerSafetyCog)
        with (
            patch.object(serverSafetyCogModule.config, "serverId", 100),
            patch.object(serverSafetyCogModule.config, "allowedCommandGuildIds", [100, 200]),
            patch.object(
                serverSafetyCogModule.runtimeCopyServerState,
                "loadGuildProvenance",
                return_value={"sourceGuildId": 100},
            ),
        ):
            self.assertTrue(cog._isSnapshotRestoreHost(100))
            self.assertTrue(cog._isSnapshotRestoreHost(200))
            self.assertFalse(cog._isSnapshotRestoreHost(300))

        commandNames = {
            command.name for command in serverSafetyCogModule.ServerSafetyCog.__cog_app_commands__
        }
        self.assertNotIn("snapshot-restore", commandNames)
        self.assertIn("snapshot-menu", commandNames)

    def test_restore_host_migrates_old_copyserver_backup(self) -> None:
        cog = object.__new__(serverSafetyCogModule.ServerSafetyCog)
        with tempfile.TemporaryDirectory() as tempDir:
            backup = Path(tempDir) / "guild_200_20260101_copyserver_pre_20260101.json"
            backup.write_text("{}", encoding="utf-8")
            with (
                patch.object(serverSafetyCogModule.config, "serverId", 100),
                patch.object(serverSafetyCogModule.config, "allowedCommandGuildIds", [100, 200]),
                patch.object(
                    serverSafetyCogModule.runtimeCopyServerState,
                    "loadGuildProvenance",
                    return_value=None,
                ),
                patch.object(serverSafetyCogModule, "snapshotDir", return_value=Path(tempDir)),
                patch.object(
                    serverSafetyCogModule.runtimeCopyServerState,
                    "saveGuildProvenance",
                ) as saveProvenance,
            ):
                self.assertTrue(cog._isSnapshotRestoreHost(200))

        saveProvenance.assert_called_once()


class SnapshotRestoreAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_main_server_restore_waits_for_role_holder_dm(self) -> None:
        verifier = SimpleNamespace(id=2, bot=False, send=AsyncMock())
        verifierRole = SimpleNamespace(id=500, members=[verifier])
        mainServerRequester = SimpleNamespace(
            id=1,
            display_name="Main Server Name",
            name="requester",
        )
        guild = SimpleNamespace(
            id=100,
            get_role=lambda roleId: verifierRole if roleId == 500 else None,
            get_member=lambda memberId: mainServerRequester if memberId == 1 else None,
        )
        interaction = SimpleNamespace(
            guild=guild,
            user=SimpleNamespace(
                id=1,
                display_name="Basic Discord Name",
                name="requester",
                mention="<@1>",
            ),
        )
        cog = object.__new__(serverSafetyCogModule.ServerSafetyCog)
        cog.pendingSnapshotRestoreApprovals = {}
        cog.snapshotRestoreApprovalLock = asyncio.Lock()
        cog.destructiveGate = SimpleNamespace(
            ensureInteractionAllowed=AsyncMock(return_value=True)
        )
        cog._ensureSnapshotAccess = AsyncMock(return_value=True)
        cog._logAudit = AsyncMock()

        applySnapshot = AsyncMock()
        with (
            patch.object(serverSafetyCogModule.config, "serverId", 100),
            patch.object(serverSafetyCogModule.config, "serverSafetyRestoreVerifierRoleId", 500),
            patch.object(
                serverSafetyCogModule.config,
                "serverSafetyRestoreVerificationTimeoutSec",
                21600,
            ),
            patch.object(
                serverSafetyCogModule.serverSafetyService,
                "buildRestorePreview",
                return_value={
                    "snapshotFile": "snapshot.json",
                    "missingChannels": ["staff-chat"],
                    "missingChannelCount": 1,
                    "channelPermissionDifferenceCount": 2,
                },
            ),
            patch.object(
                serverSafetyCogModule.serverSafetyService,
                "applyGuildSnapshot",
                new=applySnapshot,
            ),
        ):
            message = await cog.restoreSelectedSnapshotFromMenu(
                interaction,
                snapshotFile="snapshot.json",
                restoreGuildOverride=True,
            )

        self.assertIn("waiting for final verification", message)
        applySnapshot.assert_not_awaited()
        verifier.send.assert_awaited_once()
        dmContent = verifier.send.await_args.args[0]
        self.assertIn("Main Server Name (1) has attempted to restore", dmContent)
        self.assertNotIn("Basic Discord Name", dmContent)
        self.assertIn("confirm restore", dmContent)
        self.assertIn("expires in 6 hours", dmContent)
        self.assertIn("Missing channels (1): `staff-chat`", dmContent)
        self.assertIn("permissions 2", dmContent)
        self.assertEqual(len(cog.pendingSnapshotRestoreApprovals), 1)

    async def test_configured_user_bypasses_main_server_verification(self) -> None:
        guild = SimpleNamespace(id=100)
        interaction = SimpleNamespace(
            guild=guild,
            user=SimpleNamespace(id=686584178849873922, mention="<@686584178849873922>"),
        )
        cog = object.__new__(serverSafetyCogModule.ServerSafetyCog)
        cog.pendingSnapshotRestoreApprovals = {}
        cog.snapshotRestoreApprovalLock = asyncio.Lock()
        cog.destructiveGate = SimpleNamespace(
            ensureInteractionAllowed=AsyncMock(return_value=True)
        )
        cog._ensureSnapshotAccess = AsyncMock(return_value=True)
        cog._applySnapshotRestore = AsyncMock(return_value="Restore complete.")

        with (
            patch.object(serverSafetyCogModule.config, "serverId", 100),
            patch.object(
                serverSafetyCogModule.config,
                "serverSafetyRestoreVerificationBypassUserIds",
                [686584178849873922],
            ),
            patch.object(
                serverSafetyCogModule.interactionRuntime,
                "safeInteractionDefer",
                new=AsyncMock(return_value=True),
            ) as defer,
        ):
            message = await cog.restoreSelectedSnapshotFromMenu(
                interaction,
                snapshotFile="snapshot.json",
                restoreGuildOverride=True,
            )

        self.assertEqual(message, "Restore complete.")
        self.assertEqual(cog.pendingSnapshotRestoreApprovals, {})
        defer.assert_awaited_once_with(interaction, ephemeral=True, thinking=False)
        cog._applySnapshotRestore.assert_awaited_once()
        self.assertEqual(
            cog._applySnapshotRestore.await_args.kwargs["verificationDetails"],
            {
                "bypassed": True,
                "bypassUserId": 686584178849873922,
            },
        )

    async def test_wrong_confirmation_keeps_restore_pending(self) -> None:
        cog = object.__new__(serverSafetyCogModule.ServerSafetyCog)
        cog.pendingSnapshotRestoreApprovals = {
            "request": {
                "expiresAt": serverSafetyCogModule._nowUtc()
                + serverSafetyCogModule.timedelta(minutes=10),
            }
        }
        cog.snapshotRestoreApprovalLock = asyncio.Lock()
        interaction = SimpleNamespace(user=SimpleNamespace(id=2))

        with patch.object(
            serverSafetyCogModule.interactionRuntime,
            "safeInteractionReply",
            new=AsyncMock(),
        ) as reply:
            await cog.confirmMainGuildSnapshotRestore(
                interaction,
                requestId="request",
                confirmationText="confirm",
            )

        self.assertIn("request", cog.pendingSnapshotRestoreApprovals)
        self.assertIn("did not match", reply.await_args.kwargs["content"])

    async def test_requester_cannot_approve_their_own_restore(self) -> None:
        cog = object.__new__(serverSafetyCogModule.ServerSafetyCog)
        cog.pendingSnapshotRestoreApprovals = {
            "request": {
                "requesterId": 1,
                "approverIds": [1],
                "expiresAt": serverSafetyCogModule._nowUtc()
                + serverSafetyCogModule.timedelta(minutes=10),
            }
        }
        cog.snapshotRestoreApprovalLock = asyncio.Lock()
        interaction = SimpleNamespace(user=SimpleNamespace(id=1))

        with patch.object(
            serverSafetyCogModule.interactionRuntime,
            "safeInteractionReply",
            new=AsyncMock(),
        ) as reply:
            await cog.confirmMainGuildSnapshotRestore(
                interaction,
                requestId="request",
                confirmationText="confirm restore",
            )

        self.assertIn("request", cog.pendingSnapshotRestoreApprovals)
        self.assertIn("cannot approve", reply.await_args.kwargs["content"])

    async def test_role_holder_confirmation_claims_and_runs_restore(self) -> None:
        verifierRole = SimpleNamespace(id=500)
        verifierMember = SimpleNamespace(id=2, roles=[verifierRole])
        requester = SimpleNamespace(id=1, send=AsyncMock())
        guild = SimpleNamespace(
            id=100,
            get_member=lambda memberId: verifierMember if memberId == 2 else requester,
        )
        cog = object.__new__(serverSafetyCogModule.ServerSafetyCog)
        cog.bot = SimpleNamespace(get_guild=lambda guildId: guild if guildId == 100 else None)
        cog.pendingSnapshotRestoreApprovals = {
            "request": {
                "guildId": 100,
                "snapshotFile": "snapshot.json",
                "requesterId": 1,
                "requesterMention": "<@1>",
                "verifierRoleId": 500,
                "approverIds": [2],
                "expiresAt": serverSafetyCogModule._nowUtc()
                + serverSafetyCogModule.timedelta(minutes=10),
            }
        }
        cog.snapshotRestoreApprovalLock = asyncio.Lock()
        cog._applySnapshotRestore = AsyncMock(return_value="Restore complete.")
        interaction = SimpleNamespace(
            user=SimpleNamespace(
                id=2,
                mention="<@2>",
                display_name="Verifier",
                send=AsyncMock(),
            ),
        )

        with (
            patch.object(
                serverSafetyCogModule.interactionRuntime,
                "safeInteractionDefer",
                new=AsyncMock(return_value=True),
            ) as defer,
            patch.object(
                serverSafetyCogModule.interactionRuntime,
                "safeInteractionReply",
                new=AsyncMock(return_value=True),
            ) as reply,
        ):
            await cog.confirmMainGuildSnapshotRestore(
                interaction,
                requestId="request",
                confirmationText="confirm restore",
            )

        self.assertNotIn("request", cog.pendingSnapshotRestoreApprovals)
        defer.assert_awaited_once_with(interaction, ephemeral=True, thinking=True)
        cog._applySnapshotRestore.assert_awaited_once()
        self.assertEqual(cog._applySnapshotRestore.await_args.kwargs["actorId"], 1)
        self.assertEqual(cog._applySnapshotRestore.await_args.kwargs["verificationDetails"]["approverId"], 2)
        self.assertIn("verification accepted", reply.await_args.kwargs["content"])
        interaction.user.send.assert_awaited_once()
        requester.send.assert_awaited_once()

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
        refresh.assert_awaited_once_with(interaction, view=view)

    async def test_menu_restore_reports_pause_and_defers_message_update(self) -> None:
        cog = object.__new__(serverSafetyCogModule.ServerSafetyCog)
        cog.destructiveGate = SimpleNamespace(
            ensureInteractionAllowed=AsyncMock(return_value=True)
        )
        cog._ensureSnapshotAccess = AsyncMock(return_value=True)
        cog._logAudit = AsyncMock()
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=20),
            user=SimpleNamespace(id=1, mention="<@1>"),
        )
        result = {
            "snapshotFile": "paused.json",
            "batchPaused": True,
            "pauseReason": "timeout-create",
            "contiguousCompletedRoles": 4,
            "totalRoles": 10,
        }

        with (
            patch.object(
                serverSafetyCogModule.serverSafetyService,
                "applyGuildSnapshot",
                new=AsyncMock(return_value=result),
            ),
            patch.object(
                serverSafetyCogModule.interactionRuntime,
                "safeInteractionDefer",
                new=AsyncMock(return_value=True),
            ) as defer,
        ):
            message = await cog.restoreSelectedSnapshotFromMenu(
                interaction,
                snapshotFile="paused.json",
            )

        self.assertIn("Restore paused", message)
        self.assertNotIn("Restore complete", message)
        defer.assert_awaited_once_with(interaction, ephemeral=True, thinking=False)
        self.assertEqual(cog._logAudit.await_args.kwargs["action"], "server snapshot restore paused")

    async def test_destructive_gate_accepts_narrow_additional_guild(self) -> None:
        config = SimpleNamespace(
            overridingUserIds=[1],
            serverSafetyAllowedUserIds=[],
            runtimeControlAllowedUserIds=[],
            destructiveCommandGuildIds=[10],
            enableDestructiveCommands=True,
            destructiveCommandCooldownSec=30,
        )
        gate = DestructiveActionGate(configModule=config)
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=20),
            user=SimpleNamespace(id=1),
        )

        allowed = await gate.ensureInteractionAllowed(
            interaction,
            source="test",
            actionKey="snapshot-restore",
            actionLabel="snapshot restore",
            additionalGuildIds={20},
        )

        self.assertTrue(allowed)

    async def test_member_counts_only_include_successful_edits_on_copy_target(self) -> None:
        desiredRole = _FakeRole(101)
        oldMappedRole = _FakeRole(202)
        successfulMember = _FakeMember(1, [oldMappedRole])
        failedMember = _FakeMember(2, [oldMappedRole], fail=True)
        members = {1: successfulMember, 2: failedMember}
        guild = SimpleNamespace(
            me=SimpleNamespace(top_role=SimpleNamespace(position=100)),
            get_member=lambda memberId: members.get(memberId),
        )
        snapshot = {
            "members": [
                {"userId": 1, "roleIds": [1]},
                {"userId": 2, "roleIds": [1]},
            ]
        }

        result = await restoreMemberRoles(
            guild,
            snapshot,
            {1: desiredRole, 2: oldMappedRole},
        )

        self.assertEqual(result["membersUpdated"], 1)
        self.assertEqual(result["rolesAdded"], 1)
        self.assertEqual(result["rolesRemoved"], 1)
        self.assertEqual(result["memberFailures"], 1)
        self.assertEqual([role.id for role in successfulMember.editedRoles], [101])

    async def test_stage_topic_uses_stage_instance(self) -> None:
        stageInstance = _FakeStageInstance()
        stageChannel = _FakeStageChannel(50, stageInstance)
        guild = SimpleNamespace(
            id=500,
            channels=[stageChannel],
            roles=[],
            get_channel=lambda channelId: stageChannel if channelId == 50 else None,
            get_role=lambda roleId: None,
            get_member=lambda memberId: None,
        )
        row = {
            "id": 50,
            "type": "stage",
            "name": "stage-room",
            "topic": "Town hall",
            "position": 1,
            "overwrites": [],
        }

        with patch.object(restoreChannels, "_runChannelOperation", new=_runImmediately):
            restored, failure = await restoreChannels.createOrUpdateChannel(
                guild,
                row,
                {},
                {},
            )

        self.assertIs(restored, stageChannel)
        self.assertEqual(failure, "")
        self.assertNotIn("topic", stageChannel.editOptions)
        self.assertEqual(stageInstance.topics, ["Town hall"])

    async def test_existing_category_edit_failure_is_counted(self) -> None:
        category = _FakeCategoryChannel(70)
        guild = SimpleNamespace(
            id=500,
            categories=[category],
            roles=[],
            get_channel=lambda channelId: category if channelId == 70 else None,
            get_role=lambda roleId: None,
            get_member=lambda memberId: None,
        )
        rows = [
            {
                "id": 70,
                "type": "category",
                "name": "staff",
                "position": 1,
                "overwrites": [],
            }
        ]

        with patch.object(restoreChannels, "_runChannelOperation", new=_runImmediately):
            categoryMap, stats = await restoreChannels.upsertCategories(guild, rows, {})

        self.assertIs(categoryMap[70], category)
        self.assertEqual(stats["failedCount"], 1)
        self.assertEqual(stats["failedNames"], ["staff"])


if __name__ == "__main__":
    unittest.main()
