from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db import sqlite as sqliteDb
from db.sqlite import fetchAll, fetchOne


def _repoRoot() -> Path:
    return Path(__file__).resolve().parent.parent


def _safeName(value: str) -> str:
    cleaned = "".join(ch for ch in str(value or "").strip() if ch.isalnum() or ch in {"-", "_"})
    return cleaned[:40] if cleaned else "manual"


def _backupDir(configModule: Any) -> Path:
    configured = str(getattr(configModule, "dbBackupDir", "") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = _repoRoot() / path
        return path.resolve()
    return (_repoRoot() / "backups").resolve()


def _runtimeSnapshotDir(configModule: Any) -> Path:
    configured = str(getattr(configModule, "dbRuntimeSnapshotDir", "") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = _repoRoot() / path
        return path.resolve()
    return (_repoRoot() / "backups" / "dbSnapshots").resolve()


def _dbPath() -> Path:
    return Path(sqliteDb.dbPath).resolve()


def _nowStamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _makeBackupFilePath(configModule: Any, label: str, *, directory: Path | None = None) -> Path:
    directory = directory or _backupDir(configModule)
    directory.mkdir(parents=True, exist_ok=True)
    fileName = f"bot_{_nowStamp()}_{_safeName(label)}.db"
    return directory / fileName


def _sqliteBackupFile(srcPath: Path, dstPath: Path) -> None:
    srcConn = sqlite3.connect(str(srcPath), timeout=60)
    dstConn = sqlite3.connect(str(dstPath), timeout=60)
    try:
        srcConn.execute("PRAGMA busy_timeout=60000")
        dstConn.execute("PRAGMA busy_timeout=60000")
        srcConn.backup(dstConn)
    finally:
        dstConn.close()
        srcConn.close()


async def _runWithClosedDb(callback: Any) -> Any:
    async with sqliteDb._dbOperationLock:  # type: ignore[attr-defined]
        async with sqliteDb._dbWriteLock:  # type: ignore[attr-defined]
            async with sqliteDb._dbConnInitLock:  # type: ignore[attr-defined]
                conn = getattr(sqliteDb, "_dbConn", None)
                if conn is not None:
                    await conn.close()
                    sqliteDb._dbConn = None  # type: ignore[attr-defined]
                return callback()


async def createBackup(configModule: Any, *, label: str = "manual", directory: Path | None = None) -> Path:
    src = _dbPath()
    dst = _makeBackupFilePath(configModule, label, directory=directory)
    await asyncio.to_thread(_sqliteBackupFile, src, dst)
    return dst


async def createRuntimeSnapshot(configModule: Any, *, label: str) -> Path:
    return await createBackup(
        configModule,
        label=f"runtime_{label}",
        directory=_runtimeSnapshotDir(configModule),
    )


def _runtimeSnapshotRetention(configModule: Any) -> int:
    try:
        value = int(getattr(configModule, "dbRuntimeSnapshotRetention", 20) or 20)
    except (TypeError, ValueError):
        value = 20
    return max(1, min(500, value))


def pruneRuntimeSnapshots(configModule: Any) -> int:
    directory = _runtimeSnapshotDir(configModule)
    if not directory.exists():
        return 0
    retention = _runtimeSnapshotRetention(configModule)
    files = [path for path in directory.glob("*.db") if path.is_file()]
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    removed = 0
    for stalePath in files[retention:]:
        try:
            stalePath.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def _diagnosticReportPath(configModule: Any) -> Path:
    configured = str(getattr(configModule, "dbRuntimeDiagnosticReportPath", "") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = _repoRoot() / path
        return path.resolve()
    return (_repoRoot() / "runtime" / "data" / "db-state" / "latest.json").resolve()


async def writeRuntimeDiagnosticReport(
    configModule: Any,
    *,
    label: str,
    snapshotPath: Path | None = None,
    snapshotError: str = "",
) -> Path:
    dbPath = _dbPath()
    byStatusRows = await fetchAll(
        """
        SELECT status, COUNT(*) AS count
        FROM sessions
        GROUP BY status
        ORDER BY status
        """
    )
    summaryRow = await fetchOne(
        """
        SELECT COUNT(*) AS totalSessions, COALESCE(MAX(sessionId), 0) AS maxSessionId
        FROM sessions
        """
    )
    recentRows = await fetchAll(
        """
        SELECT
            s.sessionId,
            s.guildId,
            s.channelId,
            s.messageId,
            s.sessionType,
            s.hostId,
            s.maxAttendeeLimit,
            s.status,
            s.gradingIndex,
            s.createdAt,
            s.finishedAt,
            COUNT(a.userId) AS attendeeCount
        FROM sessions s
        LEFT JOIN attendees a ON a.sessionId = s.sessionId
        GROUP BY s.sessionId
        ORDER BY s.sessionId DESC
        LIMIT 25
        """
    )
    report = {
        "label": str(label or "runtime"),
        "createdAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "processId": os.getpid(),
        "cwd": str(Path.cwd()),
        "db": {
            "path": str(dbPath),
            "exists": dbPath.exists(),
            "sizeBytes": dbPath.stat().st_size if dbPath.exists() else 0,
            "snapshotPath": str(snapshotPath) if snapshotPath else "",
            "snapshotError": str(snapshotError or ""),
        },
        "sessions": {
            "total": int((summaryRow or {}).get("totalSessions") or 0),
            "maxSessionId": int((summaryRow or {}).get("maxSessionId") or 0),
            "byStatus": {
                str(row.get("status") or "UNKNOWN"): int(row.get("count") or 0)
                for row in byStatusRows
            },
            "recent": [
                {
                    "sessionId": int(row.get("sessionId") or 0),
                    "guildId": int(row.get("guildId") or 0),
                    "channelId": int(row.get("channelId") or 0),
                    "messageId": int(row.get("messageId") or 0),
                    "sessionType": str(row.get("sessionType") or ""),
                    "hostId": int(row.get("hostId") or 0),
                    "maxAttendeeLimit": int(row.get("maxAttendeeLimit") or 0),
                    "status": str(row.get("status") or ""),
                    "gradingIndex": int(row.get("gradingIndex") or 0),
                    "attendeeCount": int(row.get("attendeeCount") or 0),
                    "createdAt": str(row.get("createdAt") or ""),
                    "finishedAt": str(row.get("finishedAt") or ""),
                }
                for row in recentRows
            ],
        },
    }
    path = _diagnosticReportPath(configModule)
    path.parent.mkdir(parents=True, exist_ok=True)
    tempPath = path.with_suffix(path.suffix + ".tmp")
    tempPath.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    tempPath.replace(path)
    return path


async def captureRuntimeDbState(configModule: Any, *, label: str) -> dict[str, str]:
    snapshotPath: Path | None = None
    snapshotError = ""
    if bool(getattr(configModule, "dbRuntimeSnapshotEnabled", True)):
        try:
            snapshotPath = await createRuntimeSnapshot(configModule, label=label)
            pruneRuntimeSnapshots(configModule)
        except Exception as exc:
            snapshotError = f"{exc.__class__.__name__}: {exc}"
    reportPath = await writeRuntimeDiagnosticReport(
        configModule,
        label=label,
        snapshotPath=snapshotPath,
        snapshotError=snapshotError,
    )
    return {
        "snapshotPath": str(snapshotPath) if snapshotPath else "",
        "snapshotError": snapshotError,
        "reportPath": str(reportPath),
    }


async def listBackups(configModule: Any, *, limit: int = 25) -> list[Path]:
    directory = _backupDir(configModule)
    if not directory.exists():
        return []
    files = [path for path in directory.glob("*.db") if path.is_file()]
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return files[: max(1, min(200, int(limit or 25)))]
