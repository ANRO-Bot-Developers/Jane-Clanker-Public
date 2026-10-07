from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from runtime import gitUpdate
from runtime import transientNetwork


class _Dummy:
    pass


class _PauseController:
    def __init__(self):
        self.paused = False

    def isPaused(self):
        return self.paused

    def setPaused(self, value, **_kwargs):
        self.paused = bool(value)


class _RestartProcessControl:
    def __init__(self):
        self.requested = False

    def requestRestart(self):
        self.requested = True

    def restartRequested(self):
        return self.requested


class _ShutdownEvent:
    def __init__(self):
        self.wasSet = False

    def set(self):
        self.wasSet = True


class _RestartBot:
    def __init__(self):
        self.closed = False
        self.shutdown_api_event = _ShutdownEvent()

    async def close(self):
        self.closed = True


class _Config:
    autoGitUpdatePreservePaths = ["custom/runtime"]
    autoGitUpdateGitCommandTimeoutSec = 10


class _UnsafePreserveConfig(_Config):
    autoGitUpdatePreservePaths = [
        "custom/runtime",
        "../outside",
        "/tmp/outside",
        "runtime/../outside",
    ]


class GitUpdateDependencyTests(unittest.TestCase):
    def test_requirements_changed_detects_root_requirements_file(self):
        self.assertTrue(gitUpdate._requirementsChanged(["requirements.txt"]))
        self.assertTrue(gitUpdate._requirementsChanged(["./requirements.txt"]))
        self.assertTrue(gitUpdate._requirementsChanged(["runtime/gitUpdate.py", "requirements.txt"]))

    def test_requirements_changed_ignores_other_paths(self):
        self.assertFalse(gitUpdate._requirementsChanged([]))
        self.assertFalse(gitUpdate._requirementsChanged(["docs/requirements.txt"]))
        self.assertFalse(gitUpdate._requirementsChanged(["requirements-dev.txt"]))


class GitUpdateBackupLocationTests(unittest.TestCase):
    def _coordinator(self, repoRoot: Path, configModule: object | None = None) -> gitUpdate.GitUpdateCoordinator:
        return gitUpdate.GitUpdateCoordinator(
            botClient=_Dummy(),
            configModule=configModule or _Config(),
            pauseController=_Dummy(),
            processControlModule=_Dummy(),
            repoRoot=str(repoRoot),
            auditStream=None,
        )

    def _workspace(self) -> tuple[Path, Path]:
        workspaceContext = tempfile.TemporaryDirectory()
        self.addCleanup(workspaceContext.cleanup)
        workspace = Path(workspaceContext.name).resolve()
        repoRoot = workspace / "Jane"
        (repoRoot / "backups" / "serverSnapshots").mkdir(parents=True)
        (repoRoot / "bot.db").write_bytes(b"database")
        (repoRoot / "backups" / "serverSnapshots" / "guild.json").write_bytes(b"x" * 2048)
        return workspace, repoRoot

    def test_backup_is_written_beside_the_repo_not_in_the_system_temp_dir(self):
        workspace, repoRoot = self._workspace()
        systemTemp = workspace / "system-temp"
        systemTemp.mkdir()
        coordinator = self._coordinator(repoRoot)

        with patch.object(gitUpdate.tempfile, "gettempdir", return_value=str(systemTemp)):
            tempRoot, manifest = coordinator._backupPreservedPathsSync()

        self.assertEqual(tempRoot.parent, workspace / ".jane-git-update")
        self.assertTrue(tempRoot.name.startswith("jane-git-update-"))
        self.assertEqual((tempRoot / "bot.db").read_bytes(), b"database")
        self.assertTrue((tempRoot / "backups" / "serverSnapshots" / "guild.json").is_file())
        self.assertTrue(manifest["bot.db"])
        self.assertEqual(list(systemTemp.iterdir()), [])

    def test_configured_backup_dir_is_used(self):
        workspace, repoRoot = self._workspace()
        configuredDir = workspace / "disk" / "updater"

        class _BackupDirConfig(_Config):
            autoGitUpdateBackupDir = str(configuredDir)

        tempRoot, _manifest = self._coordinator(repoRoot, _BackupDirConfig())._backupPreservedPathsSync()

        self.assertEqual(tempRoot.parent, configuredDir)

    def test_backup_dir_inside_the_repo_is_rejected(self):
        _workspace, repoRoot = self._workspace()

        class _InsideRepoConfig(_Config):
            autoGitUpdateBackupDir = str(repoRoot / "runtime" / "data" / "updater")

        coordinator = self._coordinator(repoRoot, _InsideRepoConfig())

        with self.assertRaisesRegex(RuntimeError, "inside the repository"):
            coordinator._backupPreservedPathsSync()
        self.assertFalse((repoRoot / "runtime" / "data" / "updater").exists())

    def test_backup_is_refused_when_the_disk_lacks_room_for_it(self):
        workspace, repoRoot = self._workspace()
        coordinator = self._coordinator(repoRoot)
        tooSmall = shutil.disk_usage(workspace)._replace(free=1024)

        with patch.object(gitUpdate.shutil, "disk_usage", return_value=tooSmall):
            with self.assertRaisesRegex(RuntimeError, "Not enough free space"):
                coordinator._backupPreservedPathsSync()

        backupParent = workspace / ".jane-git-update"
        leftovers = list(backupParent.iterdir()) if backupParent.exists() else []
        self.assertEqual(leftovers, [])

    def test_failed_copy_does_not_leave_a_partial_backup_behind(self):
        workspace, repoRoot = self._workspace()
        coordinator = self._coordinator(repoRoot)

        with patch.object(gitUpdate.shutil, "copytree", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                coordinator._backupPreservedPathsSync()

        self.assertEqual(list((workspace / ".jane-git-update").iterdir()), [])

    def test_leftover_backups_are_found_in_the_backup_dir_and_the_system_temp_dir(self):
        workspace, repoRoot = self._workspace()
        systemTemp = workspace / "system-temp"
        (systemTemp / "jane-git-update-oldtmp").mkdir(parents=True)
        (systemTemp / "unrelated").mkdir()
        (workspace / ".jane-git-update" / "jane-git-update-olddisk").mkdir(parents=True)
        coordinator = self._coordinator(repoRoot)

        with patch.object(gitUpdate.tempfile, "gettempdir", return_value=str(systemTemp)):
            leftovers = coordinator._leftoverBackupDirs()

        self.assertEqual(
            sorted(path.name for path in leftovers),
            ["jane-git-update-olddisk", "jane-git-update-oldtmp"],
        )

    def test_start_warns_about_leftover_backups_even_when_auto_update_is_disabled(self):
        workspace, repoRoot = self._workspace()
        leftover = workspace / ".jane-git-update" / "jane-git-update-olddisk"
        leftover.mkdir(parents=True)
        coordinator = self._coordinator(repoRoot)

        with self.assertLogs(gitUpdate.log, level="WARNING") as captured:
            coordinator.start()

        self.assertIsNone(coordinator.workerTask)
        self.assertTrue(any(str(leftover) in line for line in captured.output))
        self.assertTrue(leftover.is_dir())


class GitUpdatePreservePathTests(unittest.TestCase):
    def test_configured_preserve_paths_extend_defaults(self):
        coordinator = gitUpdate.GitUpdateCoordinator(
            botClient=_Dummy(),
            configModule=_Config(),
            pauseController=_Dummy(),
            processControlModule=_Dummy(),
            repoRoot=".",
            auditStream=None,
        )

        paths = coordinator._preservePaths()

        self.assertIn("custom/runtime", paths)
        self.assertIn("backups/serverSnapshots", paths)
        self.assertIn("bot.db", paths)

    def test_configured_preserve_paths_reject_paths_outside_repo(self):
        coordinator = gitUpdate.GitUpdateCoordinator(
            botClient=_Dummy(),
            configModule=_UnsafePreserveConfig(),
            pauseController=_Dummy(),
            processControlModule=_Dummy(),
            repoRoot=".",
            auditStream=None,
        )

        paths = coordinator._preservePaths()

        self.assertIn("custom/runtime", paths)
        self.assertNotIn("../outside", paths)
        self.assertNotIn("tmp/outside", paths)
        self.assertNotIn("runtime/../outside", paths)

    def test_requirements_install_is_opt_in(self):
        coordinator = gitUpdate.GitUpdateCoordinator(
            botClient=_Dummy(),
            configModule=_Dummy(),
            pauseController=_Dummy(),
            processControlModule=_Dummy(),
            repoRoot=".",
            auditStream=None,
        )

        self.assertFalse(coordinator._installRequirementsOnUpdate())

    def test_requirements_install_command_uses_normal_venv_pip(self):
        coordinator = gitUpdate.GitUpdateCoordinator(
            botClient=_Dummy(),
            configModule=_Dummy(),
            pauseController=_Dummy(),
            processControlModule=_Dummy(),
            repoRoot=".",
            auditStream=None,
        )

        with patch.object(coordinator, "_pipNeedsSystemPackageOverride", return_value=False):
            command = coordinator._pipInstallRequirementsCommand(Path("requirements.txt"))

        self.assertNotIn("--break-system-packages", command)
        self.assertEqual(command[-2:], ["-r", "requirements.txt"])

    def test_requirements_install_supports_externally_managed_linux_python(self):
        coordinator = gitUpdate.GitUpdateCoordinator(
            botClient=_Dummy(),
            configModule=_Dummy(),
            pauseController=_Dummy(),
            processControlModule=_Dummy(),
            repoRoot=".",
            auditStream=None,
        )

        with patch.object(coordinator, "_pipNeedsSystemPackageOverride", return_value=True):
            command = coordinator._pipInstallRequirementsCommand(Path("requirements.txt"))
            environment = coordinator._pipInstallEnvironment()

        self.assertIn("--break-system-packages", command)
        self.assertEqual(environment["PIP_BREAK_SYSTEM_PACKAGES"], "1")


class GitUpdateApplyTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_code_update_signals_api_shutdown_before_closing_bot(self):
        class SuccessfulApplyCoordinator(gitUpdate.GitUpdateCoordinator):
            async def _buildMergePlans(self, relPaths: list[str]) -> dict:
                return {}

            async def _backupPreservedPaths(self):
                tempRoot = Path(tempfile.mkdtemp(prefix="jane-git-update-test-"))
                return tempRoot, {}

            async def _stashDirtyPaths(self, dirtyPaths: list[str]) -> str:
                return ""

            async def _runGit(self, *args: str, check: bool = True):
                return subprocess.CompletedProcess(["git", *args], 0, "", "")

            async def _applyMergePlans(self, mergePlans: dict) -> dict:
                return {}

            async def _restorePreservedPaths(self, tempRoot: Path, manifest: dict[str, bool]) -> None:
                return None

            async def _syncRequirementsIfNeeded(self, state: dict) -> str:
                return ""

            async def _dropStash(self, stashRef: str) -> None:
                return None

            async def _currentHeadCommit(self) -> str:
                return "abc123"

        with tempfile.TemporaryDirectory() as tempDir:
            bot = _RestartBot()
            processControl = _RestartProcessControl()
            coordinator = SuccessfulApplyCoordinator(
                botClient=bot,
                configModule=_Config(),
                pauseController=_PauseController(),
                processControlModule=processControl,
                repoRoot=tempDir,
                auditStream=None,
            )

            message = await coordinator._applyAvailableUpdate(
                {
                    "remote": "origin",
                    "branch": "main",
                    "behindCount": 1,
                    "upstreamCodePaths": ["bot.py"],
                    "preservedDirtyPaths": [],
                    "mergeManagedDirtyPaths": [],
                },
                triggeredBy="scheduled",
            )

            self.assertIn("requested restart", message)
            self.assertTrue(processControl.restartRequested())
            self.assertTrue(bot.shutdown_api_event.wasSet)
            self.assertTrue(bot.closed)


class GitUpdateStateTests(unittest.TestCase):
    def test_last_successful_pull_loads_from_persisted_state(self):
        with tempfile.TemporaryDirectory() as tempDir:
            repoRoot = Path(tempDir)
            statePath = repoRoot / "logs" / "git-update-state.json"
            statePath.parent.mkdir(parents=True)
            statePath.write_text(
                json.dumps(
                    {
                        "lastSuccessfulPullAt": "2026-06-02T14:30:00+00:00",
                        "lastUpdatedCommit": "def456",
                    }
                ),
                encoding="utf-8",
            )

            coordinator = gitUpdate.GitUpdateCoordinator(
                botClient=_Dummy(),
                configModule=_Config(),
                pauseController=_Dummy(),
                processControlModule=_Dummy(),
                repoRoot=str(repoRoot),
                auditStream=None,
            )

            self.assertEqual(
                coordinator.getStats()["lastUpdateAt"],
                "2026-06-02T14:30:00+00:00",
            )
            self.assertEqual(coordinator.getStats()["lastUpdatedCommit"], "def456")

    def test_record_successful_pull_preserves_existing_state_keys(self):
        with tempfile.TemporaryDirectory() as tempDir:
            repoRoot = Path(tempDir)
            statePath = repoRoot / "logs" / "git-update-state.json"
            statePath.parent.mkdir(parents=True)
            statePath.write_text(
                json.dumps({"configPullOverrideEnabled": False}),
                encoding="utf-8",
            )
            coordinator = gitUpdate.GitUpdateCoordinator(
                botClient=_Dummy(),
                configModule=_Config(),
                pauseController=_Dummy(),
                processControlModule=_Dummy(),
                repoRoot=str(repoRoot),
                auditStream=None,
            )

            coordinator._recordSuccessfulPull(
                at=datetime(2026, 6, 2, 14, 30, 15, 123456, tzinfo=timezone.utc),
                commit="abc123",
            )

            payload = json.loads(statePath.read_text(encoding="utf-8"))
            self.assertFalse(payload["configPullOverrideEnabled"])
            self.assertEqual(payload["lastSuccessfulPullAt"], "2026-06-02T14:30:15+00:00")
            self.assertEqual(payload["lastUpdatedCommit"], "abc123")
            self.assertEqual(coordinator.getStats()["lastUpdateAt"], "2026-06-02T14:30:15+00:00")


class GitUpdateInspectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_transient_connect_failure_is_detected(self):
        exc = RuntimeError(
            "fatal: unable to access 'https://github.com/aVeryTiredPotato/Jane-Clanker.git/': "
            "Failed to connect to github.com port 443 after 9417 ms: Could not connect to server"
        )

        self.assertTrue(transientNetwork.isLikelyTransientNetworkError(exc))

    async def test_failed_fetch_still_records_check_time(self):
        class FailingFetchCoordinator(gitUpdate.GitUpdateCoordinator):
            async def _branchName(self) -> str:
                return "main"

            async def _runGit(self, *args: str, check: bool = True):
                if args and args[0] == "fetch":
                    raise RuntimeError("fatal: unable to access remote: Could not resolve host: github.com")
                return await super()._runGit(*args, check=check)

        coordinator = FailingFetchCoordinator(
            botClient=_Dummy(),
            configModule=_Config(),
            pauseController=_Dummy(),
            processControlModule=_Dummy(),
            repoRoot=".",
            auditStream=None,
        )

        with self.assertRaisesRegex(RuntimeError, "Could not resolve host"):
            await coordinator._inspectUpdateState()

        self.assertTrue(coordinator.getStats().get("lastCheckAt"))

    async def test_pull_connect_failure_is_network_skip_without_exception_log(self):
        with tempfile.TemporaryDirectory() as tempDir:
            repoRoot = Path(tempDir)
            (repoRoot / "README.md").write_text("base\n", encoding="utf-8")

            class FailingPullCoordinator(gitUpdate.GitUpdateCoordinator):
                async def _runGit(self, *args: str, check: bool = True):
                    if args and args[0] == "pull":
                        raise RuntimeError(
                            "fatal: unable to access 'https://github.com/aVeryTiredPotato/Jane-Clanker.git/': "
                            "Failed to connect to github.com port 443 after 9417 ms: Could not connect to server"
                        )
                    return await super()._runGit(*args, check=check)

            coordinator = FailingPullCoordinator(
                botClient=_Dummy(),
                configModule=_Config(),
                pauseController=_PauseController(),
                processControlModule=_Dummy(),
                repoRoot=str(repoRoot),
                auditStream=None,
            )

            with patch.object(gitUpdate.log, "exception") as exceptionLog:
                result = await coordinator._applyAvailableUpdate(
                    {
                        "remote": "origin",
                        "branch": "main",
                        "behindCount": 1,
                        "upstreamCodePaths": ["bot.py"],
                        "preservedDirtyPaths": [],
                        "mergeManagedDirtyPaths": [],
                    },
                    triggeredBy="scheduled",
                )

            self.assertIn("cannot reach GitHub", result)
            self.assertEqual(coordinator.getStats()["lastResult"], "network-unavailable")
            exceptionLog.assert_not_called()


class GitUpdateStashTests(unittest.IsolatedAsyncioTestCase):
    async def test_stash_dirty_paths_ignores_stale_expected_paths(self):
        with tempfile.TemporaryDirectory() as tempDir:
            repoRoot = Path(tempDir)
            self._runGit(repoRoot, "init")
            self._runGit(repoRoot, "config", "user.email", "test@example.com")
            self._runGit(repoRoot, "config", "user.name", "Test User")
            (repoRoot / "README.md").write_text("base\n", encoding="utf-8")
            self._runGit(repoRoot, "add", "README.md")
            self._runGit(repoRoot, "commit", "-m", "base")
            trackedPath = repoRoot / "backups" / "serverSnapshots" / "tracked.json"
            trackedPath.parent.mkdir(parents=True)
            trackedPath.write_text("{\"tracked\": true}\n", encoding="utf-8")
            self._runGit(repoRoot, "add", "backups/serverSnapshots/tracked.json")
            self._runGit(repoRoot, "commit", "-m", "tracked runtime file")
            trackedPath.unlink()

            livePath = repoRoot / "backups" / "serverSnapshots" / "live.json"
            livePath.write_text("{}\n", encoding="utf-8")

            coordinator = gitUpdate.GitUpdateCoordinator(
                botClient=_Dummy(),
                configModule=_Config(),
                pauseController=_Dummy(),
                processControlModule=_Dummy(),
                repoRoot=str(repoRoot),
                auditStream=None,
            )

            stashRef = await coordinator._stashDirtyPaths(
                [
                    "backups/serverSnapshots/live.json",
                    "backups/serverSnapshots/tracked.json",
                    "backups/seversnapshots/missing.json",
                ]
            )

            self.assertTrue(stashRef)
            self.assertFalse(livePath.exists())
            self.assertTrue(trackedPath.exists())
            await coordinator._dropStash(stashRef)

    async def test_stash_dirty_paths_survives_untracked_path_disappearing_during_stash(self):
        with tempfile.TemporaryDirectory() as tempDir:
            repoRoot = Path(tempDir)
            self._runGit(repoRoot, "init")
            self._runGit(repoRoot, "config", "user.email", "test@example.com")
            self._runGit(repoRoot, "config", "user.name", "Test User")
            (repoRoot / "README.md").write_text("base\n", encoding="utf-8")
            self._runGit(repoRoot, "add", "README.md")
            self._runGit(repoRoot, "commit", "-m", "base")

            trackedPath = repoRoot / "backups" / "serverSnapshots" / "tracked.json"
            trackedPath.parent.mkdir(parents=True)
            trackedPath.write_text("{\"tracked\": true}\n", encoding="utf-8")
            self._runGit(repoRoot, "add", "backups/serverSnapshots/tracked.json")
            self._runGit(repoRoot, "commit", "-m", "tracked runtime file")
            trackedPath.unlink()

            vanishingPath = repoRoot / "backups" / "serverSnapshots" / "guild_1373417102115078215_20260317_215359_manual.json"
            vanishingPath.write_text("{}\n", encoding="utf-8")

            class RacyCoordinator(gitUpdate.GitUpdateCoordinator):
                def __init__(self, *args, pathToDelete: Path, **kwargs):
                    super().__init__(*args, **kwargs)
                    self.pathToDelete = pathToDelete
                    self.deletedBeforeStash = False

                async def _runGit(self, *args: str, check: bool = True):
                    if (
                        len(args) >= 2
                        and args[0] == "stash"
                        and args[1] == "push"
                        and not self.deletedBeforeStash
                    ):
                        self.pathToDelete.unlink(missing_ok=True)
                        self.deletedBeforeStash = True
                    return await super()._runGit(*args, check=check)

            coordinator = RacyCoordinator(
                botClient=_Dummy(),
                configModule=_Config(),
                pauseController=_Dummy(),
                processControlModule=_Dummy(),
                repoRoot=str(repoRoot),
                auditStream=None,
                pathToDelete=vanishingPath,
            )

            stashRef = await coordinator._stashDirtyPaths(
                [
                    "backups/serverSnapshots/tracked.json",
                    "backups/serverSnapshots/guild_1373417102115078215_20260317_215359_manual.json",
                ]
            )

            self.assertTrue(stashRef)
            self.assertFalse(vanishingPath.exists())
            self.assertTrue(trackedPath.exists())
            await coordinator._dropStash(stashRef)

    async def test_stash_dirty_paths_blocks_new_dirty_code_paths(self):
        with tempfile.TemporaryDirectory() as tempDir:
            repoRoot = Path(tempDir)
            self._runGit(repoRoot, "init")
            self._runGit(repoRoot, "config", "user.email", "test@example.com")
            self._runGit(repoRoot, "config", "user.name", "Test User")
            (repoRoot / "README.md").write_text("base\n", encoding="utf-8")
            self._runGit(repoRoot, "add", "README.md")
            self._runGit(repoRoot, "commit", "-m", "base")

            preservedPath = repoRoot / "backups" / "serverSnapshots" / "live.json"
            preservedPath.parent.mkdir(parents=True)
            preservedPath.write_text("{}\n", encoding="utf-8")
            (repoRoot / "local_code.py").write_text("print('local')\n", encoding="utf-8")

            coordinator = gitUpdate.GitUpdateCoordinator(
                botClient=_Dummy(),
                configModule=_Config(),
                pauseController=_Dummy(),
                processControlModule=_Dummy(),
                repoRoot=str(repoRoot),
                auditStream=None,
            )

            with self.assertRaisesRegex(RuntimeError, "outside the auto-update allowlist"):
                await coordinator._stashDirtyPaths(["backups/serverSnapshots/live.json"])

    @staticmethod
    def _runGit(repoRoot: Path, *args: str) -> None:
        result = subprocess.run(
            ["git", *args],
            cwd=str(repoRoot),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            raise AssertionError(result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
