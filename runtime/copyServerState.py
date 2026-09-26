from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _repoRoot() -> Path:
    return Path(__file__).resolve().parent.parent


def _stateRoot() -> Path:
    path = _repoRoot() / "runtime" / "data" / "copyserver"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _legacyStatePath() -> Path:
    return _repoRoot() / "logs" / "copyserver-state.json"


def _guildStatePath(guildId: int) -> Path:
    return _stateRoot() / f"guild_{int(guildId or 0)}.json"


def _guildProvenancePath(guildId: int) -> Path:
    return _stateRoot() / "provenance" / f"guild_{int(guildId or 0)}.json"


def _readJson(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _writeJson(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tempPath = path.with_suffix(f"{path.suffix}.tmp")
    tempPath.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tempPath.replace(path)


def _readLegacyGuildState(guildId: int) -> dict[str, Any]:
    rows = _readJson(_legacyStatePath())
    row = rows.get(str(int(guildId or 0)))
    return dict(row) if isinstance(row, dict) else {}


def _deleteLegacyGuildState(guildId: int) -> None:
    path = _legacyStatePath()
    rows = _readJson(path)
    key = str(int(guildId or 0))
    if key not in rows:
        return
    rows.pop(key, None)
    if rows:
        _writeJson(path, rows)
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def loadGuildState(guildId: int) -> dict[str, Any] | None:
    path = _guildStatePath(guildId)
    row = _readJson(path)
    if row:
        return row

    legacyRow = _readLegacyGuildState(guildId)
    if legacyRow:
        _writeJson(path, legacyRow)
        return legacyRow
    return None


def saveGuildState(
    guildId: int,
    *,
    sourceGuildId: int,
    sourceGuildLabel: str,
    snapshotPath: str,
    targetBackupPath: str = "",
    resumeRoleNumber: int = 0,
    resumeRoleName: str = "",
    contiguousCompletedRoles: int = 0,
    totalRoles: int = 0,
) -> None:
    path = _guildStatePath(guildId)
    _writeJson(
        path,
        {
            "schemaVersion": 2,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            "sourceGuildId": int(sourceGuildId or 0),
            "sourceGuildLabel": str(sourceGuildLabel or "").strip(),
            "snapshotPath": str(snapshotPath or "").strip(),
            "targetBackupPath": str(targetBackupPath or "").strip(),
            "resumeRoleNumber": int(resumeRoleNumber or 0),
            "resumeRoleName": str(resumeRoleName or "").strip(),
            "contiguousCompletedRoles": int(contiguousCompletedRoles or 0),
            "totalRoles": int(totalRoles or 0),
        },
    )
    _deleteLegacyGuildState(guildId)


def saveGuildProvenance(
    guildId: int,
    *,
    sourceGuildId: int,
    sourceGuildLabel: str,
    snapshotPath: str,
) -> None:
    _writeJson(
        _guildProvenancePath(guildId),
        {
            "schemaVersion": 1,
            "copiedAt": datetime.now(timezone.utc).isoformat(),
            "sourceGuildId": int(sourceGuildId or 0),
            "sourceGuildLabel": str(sourceGuildLabel or "").strip(),
            "snapshotPath": str(snapshotPath or "").strip(),
        },
    )


def loadGuildProvenance(guildId: int) -> dict[str, Any] | None:
    row = _readJson(_guildProvenancePath(guildId))
    if row:
        return row

    # Older Jane builds only kept resumable copy state. Treating that as
    # provenance keeps an in-progress or previously interrupted test copy usable.
    row = _readJson(_guildStatePath(guildId)) or _readLegacyGuildState(guildId)
    if int(row.get("sourceGuildId") or 0) <= 0:
        return None
    return {
        "schemaVersion": 0,
        "copiedAt": str(row.get("updatedAt") or ""),
        "sourceGuildId": int(row.get("sourceGuildId") or 0),
        "sourceGuildLabel": str(row.get("sourceGuildLabel") or "").strip(),
        "snapshotPath": str(row.get("snapshotPath") or "").strip(),
    }


def clearGuildState(guildId: int) -> None:
    path = _guildStatePath(guildId)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    _deleteLegacyGuildState(guildId)


def clearGuildProvenance(guildId: int) -> None:
    try:
        _guildProvenancePath(guildId).unlink()
    except FileNotFoundError:
        pass
