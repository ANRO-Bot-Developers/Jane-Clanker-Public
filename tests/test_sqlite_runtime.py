from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from db import schema as sqliteSchema
from db import sqlite as sqliteDb


_EXPECTED_FRESH_SCHEMA_FINGERPRINTS = {
    30: "79c76e724a06b883f1a55b9eed6cdfabfbc6767ffe37259c829e3e1b7763fa51",
    32: "2f066acdf1bacb3e02639c75e625eb05b42b6fb4394cc1a54f422bf75d067742",
}


class SqliteRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tempDir = tempfile.TemporaryDirectory()
        self._originalDbPath = sqliteDb.dbPath
        await sqliteDb.closeDb()
        sqliteDb.dbPath = str(Path(self._tempDir.name) / "test.db")
        await sqliteDb.initDb()

    async def asyncTearDown(self) -> None:
        await sqliteDb.closeDb()
        sqliteDb.dbPath = self._originalDbPath
        self._tempDir.cleanup()

    async def test_fresh_schema_records_current_version_once(self) -> None:
        versionRow = await sqliteDb.fetchOne("PRAGMA user_version")
        migrationRows = await sqliteDb.fetchAll(
            "SELECT fromVersion, toVersion FROM db_schema_migrations"
        )

        self.assertIsNotNone(versionRow)
        assert versionRow is not None
        self.assertEqual(versionRow["user_version"], sqliteDb._schemaVersionTarget)
        self.assertEqual(
            migrationRows,
            [{"fromVersion": 0, "toVersion": sqliteDb._schemaVersionTarget}],
        )

        await sqliteDb.initDb()
        migrationCount = await sqliteDb.fetchOne(
            "SELECT COUNT(*) AS count FROM db_schema_migrations"
        )
        self.assertEqual(migrationCount, {"count": 1})

    async def test_fresh_schema_matches_versioned_fingerprint(self) -> None:
        rows = await sqliteDb.fetchAll(
            """
            SELECT type, name, tbl_name, COALESCE(sql, '') AS sql
            FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%'
            ORDER BY type, name, tbl_name, sql
            """
        )
        payload = json.dumps(rows, sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()

        self.assertEqual(sqliteDb._schemaVersionTarget, sqliteSchema.SCHEMA_VERSION)
        self.assertIn(
            sqliteSchema.SCHEMA_VERSION,
            _EXPECTED_FRESH_SCHEMA_FINGERPRINTS,
            "Schema changes must bump SCHEMA_VERSION and record a new fingerprint.",
        )
        self.assertEqual(
            fingerprint,
            _EXPECTED_FRESH_SCHEMA_FINGERPRINTS[sqliteSchema.SCHEMA_VERSION],
        )

    async def test_public_write_helpers_commit_and_return_insert_id(self) -> None:
        await sqliteDb.execute(
            "CREATE TABLE jane_runtime_test (id INTEGER PRIMARY KEY AUTOINCREMENT, value TEXT)"
        )
        firstId = await sqliteDb.executeReturnId(
            "INSERT INTO jane_runtime_test (value) VALUES (?)",
            ("first",),
        )
        await sqliteDb.executeMany(
            "INSERT INTO jane_runtime_test (value) VALUES (?)",
            [("second",), ("third",)],
        )

        rows = await sqliteDb.fetchAll(
            "SELECT id, value FROM jane_runtime_test ORDER BY id"
        )
        self.assertEqual(firstId, 1)
        self.assertEqual(
            rows,
            [
                {"id": 1, "value": "first"},
                {"id": 2, "value": "second"},
                {"id": 3, "value": "third"},
            ],
        )

    async def test_write_transaction_rolls_back_all_changes_on_failure(self) -> None:
        await sqliteDb.execute(
            "CREATE TABLE jane_rollback_test (id INTEGER PRIMARY KEY, value TEXT)"
        )

        async def _failingTransaction(db) -> None:
            await db.execute(
                "INSERT INTO jane_rollback_test (id, value) VALUES (?, ?)",
                (1, "must roll back"),
            )
            raise RuntimeError("stop transaction")

        with self.assertRaisesRegex(RuntimeError, "stop transaction"):
            await sqliteDb.runWriteTransaction(_failingTransaction)

        row = await sqliteDb.fetchOne("SELECT id FROM jane_rollback_test WHERE id = 1")
        self.assertIsNone(row)

    async def test_write_transaction_rolls_back_when_cancelled(self) -> None:
        await sqliteDb.execute(
            "CREATE TABLE jane_cancel_test (id INTEGER PRIMARY KEY, value TEXT)"
        )
        insertFinished = asyncio.Event()

        async def _cancelledTransaction(db) -> None:
            await db.execute(
                "INSERT INTO jane_cancel_test (id, value) VALUES (?, ?)",
                (1, "must roll back"),
            )
            insertFinished.set()
            await asyncio.Future()

        transactionTask = asyncio.create_task(
            sqliteDb.runWriteTransaction(_cancelledTransaction)
        )
        await insertFinished.wait()
        transactionTask.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await transactionTask

        await sqliteDb.execute(
            "INSERT INTO jane_cancel_test (id, value) VALUES (?, ?)",
            (2, "connection remains usable"),
        )
        rows = await sqliteDb.fetchAll(
            "SELECT id, value FROM jane_cancel_test ORDER BY id"
        )
        self.assertEqual(
            rows,
            [{"id": 2, "value": "connection remains usable"}],
        )


class SqliteSchemaRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_connection_setup_closes_partial_connection(self) -> None:
        connection = MagicMock()
        connection.close = AsyncMock()
        with (
            patch.object(sqliteDb, "_dbConn", None),
            patch.object(
                sqliteDb.aiosqlite,
                "connect",
                AsyncMock(return_value=connection),
            ),
            patch.object(
                sqliteDb,
                "_prepareConnection",
                AsyncMock(side_effect=RuntimeError("pragma failed")),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "pragma failed"):
                await sqliteDb._getConnection()
            self.assertIsNone(sqliteDb._dbConn)

        connection.close.assert_awaited_once()

    async def test_schema_initialization_retries_locked_database(self) -> None:
        initialize = AsyncMock(
            side_effect=[sqlite3.OperationalError("database is locked"), None]
        )
        with (
            patch.object(sqliteDb, "_initializeSchema", initialize),
            patch.object(sqliteDb, "_dbConn", None),
            patch.object(sqliteDb.asyncio, "sleep", AsyncMock()) as sleepMock,
            patch.object(sqliteDb.log, "warning"),
        ):
            await sqliteDb.initDb()

        self.assertEqual(initialize.await_count, 2)
        sleepMock.assert_awaited_once()


class SqliteSchemaFailureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tempDir = tempfile.TemporaryDirectory()
        self._originalDbPath = sqliteDb.dbPath
        await sqliteDb.closeDb()
        sqliteDb.dbPath = str(Path(self._tempDir.name) / "failed-migration.db")

    async def asyncTearDown(self) -> None:
        await sqliteDb.closeDb()
        sqliteDb.dbPath = self._originalDbPath
        self._tempDir.cleanup()

    async def test_failed_schema_upgrade_rolls_back_all_ddl(self) -> None:
        writeVersion = AsyncMock(side_effect=RuntimeError("version write failed"))
        with patch.object(sqliteSchema, "_writeSchemaVersion", writeVersion):
            with self.assertRaisesRegex(RuntimeError, "version write failed"):
                await sqliteDb.initDb()

        objectCount = await sqliteDb.fetchOne(
            """
            SELECT COUNT(*) AS count
            FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%'
            """
        )
        version = await sqliteDb.fetchOne("PRAGMA user_version")
        self.assertEqual(objectCount, {"count": 0})
        self.assertEqual(version, {"user_version": 0})

    async def test_schema_version_is_read_inside_write_transaction(self) -> None:
        originalReadVersion = sqliteSchema._readSchemaVersion
        transactionStates: list[bool] = []

        async def _readVersion(db) -> int:
            transactionStates.append(bool(db.in_transaction))
            return await originalReadVersion(db)

        with patch.object(sqliteSchema, "_readSchemaVersion", side_effect=_readVersion):
            await sqliteDb.initDb()

        self.assertEqual(transactionStates, [True])

    async def test_version_29_upgrade_adds_training_export_schema(self) -> None:
        connection = sqlite3.connect(sqliteDb.dbPath)
        try:
            connection.execute("PRAGMA user_version=29")
            connection.commit()
        finally:
            connection.close()

        await sqliteDb.initDb()

        table = await sqliteDb.fetchOne(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name = 'training_result_export_state'
            """
        )
        migration = await sqliteDb.fetchOne(
            """
            SELECT fromVersion, toVersion
            FROM db_schema_migrations
            ORDER BY migrationId DESC
            LIMIT 1
            """
        )
        version = await sqliteDb.fetchOne("PRAGMA user_version")

        self.assertEqual(table, {"name": "training_result_export_state"})
        self.assertEqual(
            migration,
            {"fromVersion": 29, "toVersion": sqliteSchema.SCHEMA_VERSION},
        )
        self.assertEqual(version, {"user_version": sqliteSchema.SCHEMA_VERSION})


if __name__ == "__main__":
    unittest.main()
