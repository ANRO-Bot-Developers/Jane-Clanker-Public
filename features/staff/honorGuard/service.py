from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import config
from db.sqlite import execute, executeReturnId, fetchAll, fetchOne
from features.staff.honorGuard.rendering import _mentionUser, _formatPoints
from features.staff.sessions.Roblox import robloxUsers, roverIdentity


@dataclass(slots=True, frozen=True)
class HonorGuardConfig:
    enabled: bool
    reviewChannelId: int
    logChannelId: int
    archiveChannelId: int
    spreadsheetId: str
    memberSheetName: str
    archiveSheetName: str
    eventHostsSheetName: str


@dataclass(slots=True, frozen=True)
class HonorGuardPointDeltas:
    quotaPoints: float = 0
    eventPoints: float = 0
    awardedPoints: float = 0

@dataclass(slots=True, frozen=True)
class HonorGuardScaffoldStatus:
    config: HonorGuardConfig
    plannedDbTables: tuple[str, ...]
    plannedModules: tuple[str, ...]
    nextMilestones: tuple[str, ...]
    sheetProblems: tuple[str, ...] = ()


def _normalizePositiveInt(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _jsonText(value: object) -> str:
    if value is None:
        return "{}"
    if isinstance(value, str):
        text = value.strip()
        return text or "{}"
    try:
        return json.dumps(value, separators=(",", ":"), ensure_ascii=True)
    except Exception:
        return "{}"


def _jsonDict(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}

def format_duration(minutes: int) -> str:
    hours, mins = divmod(minutes, 60)

    parts = []
    if hours:
        parts.append(f"{hours} hour" + ("s" if hours != 1 else ""))
    if mins:
        parts.append(f"{mins} minute" + ("s" if mins != 1 else ""))

    return " ".join(parts) if parts else "0 minutes"

async def _buildUserText(users: list[dict], guildId: int) -> str:
    if not users:
        return ""
    usernames = []
    for user in users:
        try:
            userId = int(user.get("userId") or 0)
            lookup = await robloxUsers.fetchRobloxUser(
                userId,
                guildId
            )
            username = str(lookup.robloxUsername or "").strip()
        except Exception:
            username = ""
        usernames.append(username)
    return ", ".join(usernames)

async def _rememberHonorGuardIdentity(
    *,
    userId: int,
    robloxUsername: str,
    guildId: int,
    source: str,
) -> None:
    if int(userId or 0) <= 0 or not str(robloxUsername or "").strip():
        return
    await roverIdentity.rememberKnownRobloxIdentity(
        int(userId),
        str(robloxUsername or "").strip(),
        source=source,
        guildId=int(guildId or 0),
        confidence=80,
    )


def _normalizeKey(value: object) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _normalizeStatus(value: object, fallback: str = "PENDING") -> str:
    text = str(value or fallback).strip().upper()
    return text or fallback


def _normalizePointType(value: object) -> str:
    text = str(value or "").strip().upper()
    if text in {"QUOTA", "EVENT", "AWARDED"}:
        return text
    return "AWARDED"

def _configuredPointMap(configModule: Any, attrName: str) -> dict[str, float]:
    raw = getattr(configModule, attrName, {}) or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for key, value in raw.items():
        try:
            out[key] = float(value)
        except (TypeError, ValueError):
            continue
    return out

def _configuredComplexPointMap(configModule: Any, attrName: str) -> dict[str, dict[str, float]]:
    raw = getattr(configModule, attrName, {}) or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, float]] = {}
    for key, value in raw.items():
        try:
            out[key] = {
                "base": float(value.get("base", 0)),
                "per_intervall": float(value.get("per_intervall", 0)),
                "minimum": float(value.get("minimum", 0)),
            }
        except (TypeError, ValueError):
            continue
    return out


def _attendanceQuotaPoints(configModule: Any, eventType: str) -> float:
    byType = _configuredPointMap(configModule, "honorGuardAttendanceQuotaPointsByEventType")
    return float(byType.get(eventType, 1))


def _attendanceEventPoints(configModule: Any, eventType: str, durationMinutes: int) -> float:
    byType = _configuredComplexPointMap(configModule, "honorGuardAttendanceEventPointsByEventType")
    intervall = max(1, int(getattr(configModule, "honorGuardAttendanceEventPointsIntervallMinutes", 30) or 30))
    points = byType.get(eventType, {}).get("base", 0) + byType.get(eventType, {}).get("per_intervall", 0) * durationMinutes // intervall
    points = max(points, byType.get(eventType, {}).get("minimum", 0))
    return float(points)

def _supervisorEventPoints(configModule: Any, eventType: str, durationMinutes: int) -> float:
    byType = _configuredComplexPointMap(configModule, "honorGuardSupervisorEventPointsByEventType")
    intervall = max(1, int(getattr(configModule, "honorGuardAttendanceEventPointsIntervallMinutes", 30) or 30))
    points = byType.get(eventType, {}).get("base", 0) + byType.get(eventType, {}).get("per_intervall", 0) * durationMinutes // intervall
    points = max(points, byType.get(eventType, {}).get("minimum", 0))
    return float(points)



def _ceilPoints(value: float) -> int:
    return int(math.ceil(max(0.0, float(value or 0))))


def calculatePointDeltas(
    *,
    configModule: Any,
    memberGroup: str = "",
    eventType: str = "",
    participantRole: str = "ATTENDEE",
    durationMinutes: int = 0,
    attendeeCount: int = 0,
    gradedAttendeeCount: int = 0,
    passed: bool = False,
    screenAssist: bool = False,
) -> HonorGuardPointDeltas:
    normalizedEvent = _normalizeKey(eventType).lower()
    normalizedRole = _normalizeKey(participantRole).upper()
    group = str(memberGroup or "").strip().lower()
    attendeeTotal = max(0, int(attendeeCount or 0))
    gradedTotal = max(0, int(gradedAttendeeCount or 0))

    quotaPoints = 0.0
    eventPoints = 0.0

    attendanceEligible = group in {"enlisted", "nco"}
    officerLike = group in {"officer", "nco", ""}

    if normalizedEvent not in {"jge", "ncoe"}:
        if normalizedRole == "ATTENDEE":
            if attendanceEligible:
                quotaPoints = _attendanceQuotaPoints(configModule, normalizedEvent)
                eventPoints = _attendanceEventPoints(configModule, normalizedEvent, durationMinutes)
            elif normalizedEvent == "inspection":
                eventPoints = _attendanceEventPoints(configModule, normalizedEvent, durationMinutes) or 8

        elif normalizedRole == "HOST":
            if officerLike:
                hostMap = _configuredPointMap(configModule, "honorGuardHostEventPointsByEventType")
                eventPoints = float(hostMap.get(normalizedEvent, 0))
            if group == "nco":
                quotaPoints = _attendanceQuotaPoints(configModule, normalizedEvent)

        elif normalizedRole == "SUPERVISOR":
            if officerLike:
                eventPoints = _supervisorEventPoints(configModule, normalizedEvent, durationMinutes)
            if group == "nco":
                quotaPoints = _attendanceQuotaPoints(configModule, normalizedEvent)

        elif normalizedRole == "COHOST":
            if officerLike:
                eventPoints = _attendanceEventPoints(configModule, normalizedEvent, durationMinutes)
            if group == "nco":
                quotaPoints = _attendanceQuotaPoints(configModule, normalizedEvent)

    if normalizedEvent == "jge":
        if normalizedRole == "HOST":
            rate = float(getattr(configModule, "honorGuardJgePointsPerGradedAttendee", 0.75) or 0.75)
            eventPoints = _ceilPoints(rate * attendeeTotal)
            if group == "nco":
                quotaPoints = _attendanceQuotaPoints(configModule, normalizedEvent)
        elif normalizedRole in {"COHOST", "SUPERVISOR"}:
            rate = float(getattr(configModule, "honorGuardJgePointsPerGradedAttendee", 0.75) or 0.75)
            eventPoints = _ceilPoints(rate * gradedTotal)
            if group == "nco":
                quotaPoints = _attendanceQuotaPoints(configModule, normalizedEvent)
        elif normalizedRole == "ATTENDEE" and passed:
            quotaPoints = _attendanceQuotaPoints(configModule, "jge")
            eventPoints = _attendanceEventPoints(configModule, "jge", durationMinutes)

    if normalizedEvent == "ncoe":
        rate = float(getattr(configModule, "honorGuardNcoExamPointsPerGradedAttendee", 1.5) or 1.5)
        screenAssistPoints = float(getattr(configModule, "honorGuardNcoExamScreenAssistPoints", 2) or 2)
        if normalizedRole == "HOST":
            eventPoints = _ceilPoints(rate * attendeeTotal)
        elif normalizedRole in {"COHOST", "SUPERVISOR"}:
            if screenAssist:
                eventPoints = screenAssistPoints
            if gradedTotal > 0:
                eventPoints += _ceilPoints(rate * gradedTotal)
        elif normalizedRole == "ATTENDEE" and passed:
            quotaPoints = _attendanceQuotaPoints(configModule, "ncoe")
            eventPoints = _attendanceEventPoints(configModule, "ncoe", durationMinutes)

    return HonorGuardPointDeltas(
        quotaPoints=float(quotaPoints),
        eventPoints=float(eventPoints),
    )


def loadHonorGuardConfig(*, configModule: Any) -> HonorGuardConfig:
    return HonorGuardConfig(
        enabled=bool(getattr(configModule, "honorGuardEnabled", False)),
        reviewChannelId=_normalizePositiveInt(getattr(configModule, "honorGuardReviewChannelId", 0)),
        logChannelId=_normalizePositiveInt(getattr(configModule, "honorGuardLogChannelId", 0)),
        archiveChannelId=_normalizePositiveInt(getattr(configModule, "honorGuardArchiveChannelId", 0)),
        spreadsheetId=str(getattr(configModule, "honorGuardSpreadsheetId", "") or "").strip(),
        memberSheetName=str(getattr(configModule, "honorGuardMemberSheetName", "") or "").strip(),
        archiveSheetName=str(getattr(configModule, "honorGuardArchiveSheetName", "") or "").strip(),
        eventHostsSheetName=str(getattr(configModule, "honorGuardEventHostsSheetName", "") or "").strip(),
    )


def buildScaffoldStatus(*, configModule: Any) -> HonorGuardScaffoldStatus:
    try:
        from features.staff.honorGuard import sheets as honorGuardSheets

        sheetProblems = honorGuardSheets.configurationProblems(configModule=configModule)
    except Exception as exc:
        sheetProblems = (f"Sheet adapter check failed: {exc.__class__.__name__}",)

    return HonorGuardScaffoldStatus(
        config=loadHonorGuardConfig(configModule=configModule),
        plannedDbTables=(
            "hg_submissions",
            "hg_submission_events",
            "hg_point_awards",
            "hg_attendance_records",
            "hg_sentry_logs",
            "hg_quota_cycles",
            "hg_event_records",
        ),
        plannedModules=(
            "cogs.staff.honorGuardCog",
            "features.staff.honorGuard.service",
            "features.staff.honorGuard.sheets",
            "features.staff.honorGuard",
        ),
        nextMilestones=(
            "Build the command/view review flow on top of hg_submissions.",
            "Call syncApprovedSubmissionToSheet after reviewer approval.",
            "Live-test member row lookup and archive handling against the HG spreadsheet.",
            "Add bi-weekly quota reset automation after sheet columns are confirmed.",
        ),
        sheetProblems=sheetProblems,
    )


async def listPointAwardPendingStatuses() -> List[Dict]:
    submissions = await fetchAll(
        "SELECT * FROM hg_submissions WHERE submissionType = 'POINT_AWARD' AND status = 'PENDING' ORDER BY createdAt ASC, submissionId ASC",
    )
    enrichedSubmissions = []
    for submission in submissions:
        metadata = _jsonDict(submission.get("metadataJson"))
        enriched = dict(submission)
        enriched["reason"] = str(metadata.get("reason") or "").strip()
        enrichedSubmissions.append(enriched)

    return enrichedSubmissions

async def listSoloSentryPendingStatuses() -> List[Dict]:
    submissions = await fetchAll(
        "SELECT * FROM hg_submissions WHERE submissionType = 'SOLO_SENTRY' AND status = 'PENDING' ORDER BY createdAt ASC, submissionId ASC",
    )
    enrichedSubmissions = []
    for submission in submissions:
        metadata = _jsonDict(submission.get("metadataJson"))
        enriched = dict(submission)
        enriched["minutes"] = int(metadata.get("minutes") or 0)
        enriched["imageUrls"] = [
            str(value).strip()
            for value in metadata.get("imageUrls", [])
            if str(value).strip()
        ] if isinstance(metadata.get("imageUrls"), list) else []
        enrichedSubmissions.append(enriched)

    return enrichedSubmissions

async def listEventPendingStatuses() -> List[Dict]:
    submissions = await fetchAll(
        "SELECT * FROM hg_submissions WHERE submissionType = 'EVENT_RECORD' AND status = 'PENDING' ORDER BY createdAt ASC, submissionId ASC",
    )
    enrichedSubmissions = []
    for submission in submissions:
        metadata = _jsonDict(submission.get("metadataJson"))
        enriched = dict(submission)
        enriched["eventId"] = int(metadata.get("eventRecordId") or 0)
        enriched["imageUrls"] = [
            str(value).strip()
            for value in metadata.get("imageUrls", [])
            if str(value).strip()
        ] if isinstance(metadata.get("imageUrls"), list) else []
        enrichedSubmissions.append(enriched)

    return enrichedSubmissions

async def createPointAwardSubmission(
    *,
    guildId: int,
    channelId: int,
    submitterId: int,
    awardedUserId: int,
    reason: str,
    awardedPoints: float = 0,
    awardedUserDisplayName: str = "",
) -> int:
    awardedDelta = float(awardedPoints or 0)
    return await createSubmission(
        guildId=int(guildId),
        channelId=int(channelId),
        submitterId=int(submitterId),
        submissionType="POINT_AWARD",
        targetUserId=int(awardedUserId or 0),
        targetDisplayName=str(awardedUserDisplayName or "").strip(),
        deltas=HonorGuardPointDeltas(
            awardedPoints=awardedDelta,
        ),
        metadata={
            "reason": str(reason or "").strip(),
            "awardedPoints": awardedDelta,
        },
    )


async def getPointAwardSubmission(submissionId: int) -> Optional[dict[str, Any]]:
    submission = await getSubmission(int(submissionId))
    if submission is None:
        return None
    metadata = _jsonDict(submission.get("metadataJson"))
    enriched = dict(submission)
    enriched["reason"] = str(metadata.get("reason") or "").strip()
    return enriched


async def setPointAwardMessageId(submissionId: int, messageId: int) -> None:
    await setSubmissionMessageId(int(submissionId), int(messageId or 0))


async def updatePointAwardStatus(
    submissionId: int,
    status: str,
    *,
    reviewerId: int,
    note: str | None = None,
    threadId: int | None = None,
) -> None:
    details = {"threadId": int(threadId)} if int(threadId or 0) > 0 else None
    await setSubmissionStatus(
        submissionId=int(submissionId),
        status=str(status or "").strip().upper(),
        reviewerId=int(reviewerId or 0),
        note=str(note or "").strip(),
        details=details,
    )
    if _normalizeStatus(status) == "APPROVED":
        submission = await getSubmission(int(submissionId))
        if submission is not None:
            await ensurePointAwardRecordsForSubmission(
                submission=submission,
                sheetSynced=bool(int(submission.get("sheetSynced") or 0)),
            )


async def _appendSubmissionEvent(
    *,
    submissionId: int,
    actorId: int = 0,
    eventType: str,
    fromStatus: str = "",
    toStatus: str = "",
    note: str = "",
    details: object = None,
) -> int:
    return await executeReturnId(
        """
        INSERT INTO hg_submission_events
            (submissionId, actorId, eventType, fromStatus, toStatus, note, detailsJson)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(submissionId),
            int(actorId or 0),
            str(eventType or "EVENT").strip().upper(),
            str(fromStatus or "").strip().upper(),
            str(toStatus or "").strip().upper(),
            str(note or "").strip(),
            _jsonText(details),
        ),
    )


async def createSubmission(
    *,
    guildId: int,
    channelId: int,
    submitterId: int,
    submissionType: str,
    targetUserId: int = 0,
    targetDisplayName: str = "",
    eventDate: str = "",
    deltas: HonorGuardPointDeltas | None = None,
    metadata: object = None,
) -> int:
    pointDeltas = deltas or HonorGuardPointDeltas()
    submissionId = await executeReturnId(
        """
        INSERT INTO hg_submissions
            (
                guildId, channelId, submitterId, targetUserId,
                targetDisplayName, submissionType, eventDate,
                quotaPoints, eventPoints, awardedPoints,
                metadataJson
            )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(guildId),
            int(channelId),
            int(submitterId),
            int(targetUserId or 0),
            str(targetDisplayName or "").strip(),
            str(submissionType or "").strip().upper(),
            str(eventDate or "").strip(),
            float(pointDeltas.quotaPoints),
            float(pointDeltas.eventPoints),
            float(pointDeltas.awardedPoints),
            _jsonText(metadata),
        ),
    )
    await _appendSubmissionEvent(
        submissionId=submissionId,
        actorId=submitterId,
        eventType="CREATED",
        toStatus="PENDING",
        details=metadata,
    )
    return submissionId


async def setSubmissionMessageId(submissionId: int, messageId: int) -> None:
    await execute(
        """
        UPDATE hg_submissions
        SET messageId = ?, updatedAt = datetime('now')
        WHERE submissionId = ?
        """,
        (int(messageId or 0), int(submissionId)),
    )


async def getSubmission(submissionId: int) -> Optional[dict[str, Any]]:
    return await fetchOne(
        "SELECT * FROM hg_submissions WHERE submissionId = ?",
        (int(submissionId),),
    )


async def getSubmissionByMessageId(messageId: int, submissionType: str) -> Optional[dict[str, Any]]:
    return await fetchOne(
        "SELECT * FROM hg_submissions WHERE messageId = ? AND submissionType = ? ORDER BY submissionId DESC LIMIT 1",
        (int(messageId), str(submissionType or "").strip().upper()),
    )


async def setSubmissionStatus(
    *,
    submissionId: int,
    status: str,
    reviewerId: int = 0,
    note: str = "",
    details: object = None,
) -> None:
    submission = await getSubmission(int(submissionId))
    if submission is None:
        raise ValueError(f"Honor Guard submission not found: {submissionId}")
    fromStatus = _normalizeStatus(submission.get("status"))
    toStatus = _normalizeStatus(status)
    await execute(
        """
        UPDATE hg_submissions
        SET status = ?,
            reviewerId = ?,
            reviewNote = ?,
            reviewedAt = CASE WHEN ? IN ('APPROVED', 'REJECTED', 'CANCELED') THEN datetime('now') ELSE reviewedAt END,
            updatedAt = datetime('now')
        WHERE submissionId = ?
        """,
        (
            toStatus,
            int(reviewerId or 0),
            str(note or "").strip(),
            toStatus,
            int(submissionId),
        ),
    )
    await _appendSubmissionEvent(
        submissionId=int(submissionId),
        actorId=int(reviewerId or 0),
        eventType=f"STATUS_{toStatus}",
        fromStatus=fromStatus,
        toStatus=toStatus,
        note=note,
        details=details,
    )


async def createPointAward(
    *,
    guildId: int,
    targetUserId: int = 0,
    pointType: str,
    points: float,
    reason: str = "",
    awardedBy: int = 0,
    approvedBy: int = 0,
    submissionId: int = 0,
    sheetSynced: bool = False,
) -> int:
    awardId = await executeReturnId(
        """
        INSERT INTO hg_point_awards
            (
                submissionId, guildId, targetUserId, pointType,
                points, reason, awardedBy, approvedBy, sheetSynced
            )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(submissionId or 0),
            int(guildId),
            int(targetUserId or 0),
            _normalizePointType(pointType),
            float(points or 0),
            str(reason or "").strip(),
            int(awardedBy or 0),
            int(approvedBy or 0),
            1 if sheetSynced else 0,
        ),
    )
    return awardId


async def ensurePointAwardRecordsForSubmission(
    *,
    submission: dict[str, Any],
    sheetSynced: bool,
) -> list[int]:
    if _normalizeStatus(submission.get("status")) != "APPROVED":
        return []
    if str(submission.get("submissionType") or "").strip().upper() != "POINT_AWARD":
        return []

    submissionId = int(submission.get("submissionId") or 0)
    if submissionId <= 0:
        return []

    existingRows = await fetchAll(
        "SELECT awardId, pointType, sheetSynced FROM hg_point_awards WHERE submissionId = ?",
        (submissionId,),
    )
    existingByType = {
        str(row.get("pointType") or "").strip().upper(): row
        for row in existingRows
    }

    metadata = _jsonDict(submission.get("metadataJson"))
    awardedPoints = int(submission.get("awardedPoints") or 0)

    reason = str(metadata.get("reason") or "").strip()

    desiredRows: list[tuple[str, int]] = []
    if awardedPoints > 0:
        desiredRows.append(("AWARDED", awardedPoints))

    createdAwardIds: list[int] = []
    for pointType, points in desiredRows:
        existing = existingByType.get(pointType)
        if existing is None:
            awardId = await createPointAward(
                submissionId=submissionId,
                guildId=int(submission.get("guildId") or 0),
                targetUserId=int(submission.get("targetUserId") or 0),
                pointType=pointType,
                points=points,
                reason=reason,
                awardedBy=int(submission.get("submitterId") or 0),
                approvedBy=int(submission.get("reviewerId") or 0),
                sheetSynced=sheetSynced,
            )
            createdAwardIds.append(int(awardId))
            continue

        if bool(int(existing.get("sheetSynced") or 0)) == bool(sheetSynced):
            continue

        await execute(
            """
            UPDATE hg_point_awards
            SET sheetSynced = ?
            WHERE awardId = ?
            """,
            (1 if sheetSynced else 0, int(existing.get("awardId") or 0)),
        )

    return createdAwardIds


async def createAttendanceRecord(
    *,
    eventId: int,
    guildId: int,
    userId: int,
    memberGroup: str,
    participantRole: str = "ATTENDEE",
    createdBy: int = 0,
) -> int:
    recordId = await executeReturnId(
        """
        INSERT INTO hg_attendance_records
            (
                eventId, guildId, userId,
                participantRole, memberGroup,
                createdBy
            )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            int(eventId),
            int(guildId),
            int(userId),
            participantRole.upper(),
            str(memberGroup or "").strip().upper(),
            int(createdBy or 0),
        ),
    )
    return recordId

async def removeAttendanceRecord(
    *,
    eventId: int,
    userId: int,
) -> int:
    await execute(
        """
        DELETE FROM hg_attendance_records
        WHERE eventId = ?
          AND userId = ?
        """,
        (
            int(eventId),
            int(userId),
        ),
    )
    return 1

async def updateAttendeePoints(
    *,
    recordId: int,
    points: HonorGuardPointDeltas,
) -> None:
    await execute(
        """
        UPDATE hg_attendance_records
        SET quotaPoints = ?, eventPoints = ?
        WHERE recordId = ?
        """,
        (
            float(points.quotaPoints),
            float(points.eventPoints),
            int(recordId),
        ),
    )

async def createSoloSentryLog(
    *,
    guildId: int,
    userId: int,
    dutyDate: str,
    minutes: int = 0,
    submissionId: int = 0,
    status: str = "PENDING",
    configModule: Any = config,
) -> int:
    eventPoints = float(getattr(configModule, "honorGuardSoloSentryDutyEventPoints", 1) or 1)
    sentryLogId = await executeReturnId(
        """
        INSERT INTO hg_sentry_logs
            (
                submissionId, guildId, userId, dutyDate, minutes,
                eventPoints, status
            )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(submissionId or 0),
            int(guildId),
            int(userId),
            str(dutyDate or "").strip(),
            int(minutes or 0),
            eventPoints,
            _normalizeStatus(status),
        ),
    )
    return sentryLogId


async def findExistingSentryLogForDate(*, userId: int, dutyDate: str) -> Optional[dict[str, Any]]:
    return await fetchOne(
        """
        SELECT *
        FROM hg_sentry_logs
        WHERE userId = ?
          AND dutyDate = ?
          AND status IN ('PENDING', 'APPROVED')
        ORDER BY sentryLogId DESC
        LIMIT 1
        """,
        (int(userId), str(dutyDate or "").strip()),
    )


async def createSoloSentrySubmission(
    *,
    guildId: int,
    channelId: int,
    submitterId: int,
    dutyDate: str,
    targetUserId: int = 0,
    targetDisplayName: str = "",
    minutes: int = 0,
    imageUrls: list[str] | None = None,
    configModule: Any = config,
) -> int:
    userId = int(targetUserId or submitterId)
    existing = await findExistingSentryLogForDate(userId=userId, dutyDate=dutyDate)
    if existing is not None:
        raise ValueError("A pending or approved Honor Guard sentry log already exists for that user/date.")

    deltas = HonorGuardPointDeltas(
        eventPoints=float(getattr(configModule, "honorGuardSoloSentryDutyEventPoints", 1) or 1),
    )
    submissionId = await createSubmission(
        guildId=guildId,
        channelId=channelId,
        submitterId=submitterId,
        submissionType="SOLO_SENTRY",
        targetUserId=userId,
        targetDisplayName=targetDisplayName,
        eventDate=dutyDate,
        deltas=deltas,
        metadata={
            "minutes": int(minutes or 0),
            "imageUrls": list(imageUrls or []),
        },
    )
    await createSoloSentryLog(
        guildId=guildId,
        userId=userId,
        dutyDate=dutyDate,
        minutes=minutes,
        submissionId=submissionId,
        status="PENDING",
        configModule=configModule,
    )
    return submissionId


async def getSoloSentrySubmission(submissionId: int) -> Optional[dict[str, Any]]:
    submission = await getSubmission(int(submissionId))
    if submission is None:
        return None
    if str(submission.get("submissionType") or "").strip().upper() != "SOLO_SENTRY":
        return None

    metadata = _jsonDict(submission.get("metadataJson"))
    enriched = dict(submission)
    enriched["minutes"] = int(
        metadata.get("minutes")
    )
    enriched["imageUrls"] = [
        str(value).strip()
        for value in metadata.get("imageUrls", [])
        if str(value).strip()
    ] if isinstance(metadata.get("imageUrls"), list) else []
    return enriched


async def setSentryLogStatus(
    *,
    sentryLogId: int,
    status: str,
    reviewerId: int = 0,
    note: str = "",
) -> None:
    await execute(
        """
        UPDATE hg_sentry_logs
        SET status = ?,
            reviewerId = ?,
            reviewNote = ?,
            reviewedAt = CASE WHEN ? IN ('APPROVED', 'REJECTED', 'CANCELED') THEN datetime('now') ELSE reviewedAt END
        WHERE sentryLogId = ?
        """,
        (
            _normalizeStatus(status),
            int(reviewerId or 0),
            str(note or "").strip(),
            _normalizeStatus(status),
            int(sentryLogId),
        ),
    )


async def updateSoloSentrySubmissionStatus(
    submissionId: int,
    status: str,
    *,
    reviewerId: int,
    note: str | None = None,
    threadId: int | None = None,
) -> None:
    details = {"threadId": int(threadId)} if int(threadId or 0) > 0 else None
    await setSubmissionStatus(
        submissionId=int(submissionId),
        status=str(status or "").strip().upper(),
        reviewerId=int(reviewerId or 0),
        note=str(note or "").strip(),
        details=details,
    )
    sentryLog = await fetchOne(
        "SELECT sentryLogId FROM hg_sentry_logs WHERE submissionId = ? ORDER BY sentryLogId DESC LIMIT 1",
        (int(submissionId),),
    )
    if sentryLog is None:
        return
    await setSentryLogStatus(
        sentryLogId=int(sentryLog.get("sentryLogId") or 0),
        status=str(status or "").strip().upper(),
        reviewerId=int(reviewerId or 0),
        note=str(note or "").strip(),
    )

async def createEventSubmission(
    *,
    eventId: int,
    event: dict[str, Any],
    submitterId: int,
    imageUrls: list[str] | None,
    evidenceMessageUrl: str
) -> int:
    submissionId = await createSubmission(
        guildId=int(event["guildId"]),
        channelId=int(event["channelId"]),
        submitterId=int(submitterId),
        submissionType="EVENT_RECORD",
        targetUserId=int(event.get("hostId") or 0),
        eventDate=event.get("eventDate") or "",
        metadata={
            "eventRecordId": int(eventId),
            "eventType": str(event.get("eventType") or "").strip(),
            "durationMinutes": int(event.get("durationMinutes") or 0),
            "imageUrls": list(imageUrls or []),
            "evidenceMessageUrl": str(evidenceMessageUrl or "").strip(),
            "platoon": str(event.get("platoon") or "none").strip(),
        }
    )
    await execute("""UPDATE hg_event_records SET submissionId = ? WHERE eventId = ? """, (submissionId, int(eventId)))
    return submissionId

async def createEventRecord(
    *,
    guildId: int,
    eventType: str,
    eventTitle: str = "",
    eventDate: str = "",
    hostId: int = 0,
    attendeeCount: int = 0,
    channelId: int = 0,
    createdById: int = 0,
    platoon: str = "none",
    metadata: object = None,
) -> int:
    timestamp = datetime.now(tz=timezone.utc).isoformat(timespec="minutes")
    return await executeReturnId(
        """
        INSERT INTO hg_event_records
            (
                guildId, eventType, eventTitle, eventDate, hostId, channelId,
                attendeeCount, metadataJson, createdBy, startedAt, platoon
            )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(guildId),
            str(eventType),
            str(eventTitle or "").strip(),
            str(eventDate or "").strip(),
            int(hostId or 0),
            int(channelId or 0),
            int(attendeeCount or 0),
            _jsonText(metadata),
            int(createdById or 0),
            timestamp,
            str(platoon),
        ),
    )


async def setEventRecordMessageId(eventId: int, messageId: int) -> None:
    await execute(
        """
        UPDATE hg_event_records
        SET messageId = ?, updatedAt = datetime('now')
        WHERE eventId = ?
        """,
        (int(messageId or 0), int(eventId)),
    )

async def setEventRecordDuration(eventId: int, duration: int) -> None:
    await execute(
        """
        UPDATE hg_event_records
        SET durationMinutes = ?, updatedAt = datetime('now')
        WHERE eventId = ?
        """,
        (int(duration or 0), int(eventId)),
    )


async def setExamGrade(eventId: int, userId: int, grade: str) -> None:
    await execute(
        """
        UPDATE hg_attendance_records
        SET examGrade = ?, updatedAt = datetime('now')
        WHERE eventId = ?
          AND userId = ?
        """,
        (str(grade or "").strip().upper(), int(eventId), int(userId)),
    )

async def updateEventRecordStatus(eventId: int, status: str) -> None:
    if status in {"CANCELED", "FINISHED", "GRADING"}:
        await execute(
            "UPDATE hg_event_records SET status = ?, finishedAt = datetime('now') WHERE eventId = ?",
            (status, eventId),
        )
    else:
        await execute(
            "UPDATE hg_event_records SET status = ?, finishedAt = NULL WHERE eventId = ?",
            (status, eventId),
        )

async def listOpenEventSessions() -> List[Dict]:
    records = await fetchAll(
        """
        SELECT *
        FROM hg_event_records
        WHERE status = 'OPEN'
        ORDER BY createdAt ASC, eventId ASC
        """,
    )
    return records

async def listHonorGuardAttendees(eventId: int) -> List[Dict]:
    return await fetchAll(
        """
        SELECT *
        FROM hg_attendance_records
        WHERE eventId = ?
        ORDER BY createdAt ASC, recordId ASC
        """,
        (int(eventId),),
    )

async def getEventSubmission(submissionId: int) -> Optional[dict[str, Any]]:
    submission = await getSubmission(int(submissionId))
    if submission is None:
        return None
    if str(submission.get("submissionType") or "").strip().upper() != "EVENT_RECORD":
        return None

    metadata = _jsonDict(submission.get("metadataJson"))
    enriched = dict(submission)
    enriched["eventId"] = int(metadata.get("eventRecordId") or 0)
    enriched["imageUrls"] = [
        str(value).strip()
        for value in metadata.get("imageUrls", [])
        if str(value).strip()
    ] if isinstance(metadata.get("imageUrls"), list) else []
    return enriched

async def _rawEventRecord(eventId: int) -> Optional[dict[str, Any]]:
    return await fetchOne(
        "SELECT * FROM hg_event_records WHERE eventId = ?",
        (int(eventId),),
    )


async def getEventRecord(eventId: int) -> Optional[dict[str, Any]]:
    record = await _rawEventRecord(int(eventId))
    if record is None:
        return None
    enriched = dict(record)
    enriched["startedAt"] = datetime.fromisoformat(str(record.get("startedAt"))).replace(tzinfo=timezone.utc)
    enriched["eventDate"] = datetime.fromisoformat(str(record.get("eventDate"))).replace(tzinfo=timezone.utc)
    return enriched

async def updateEventSubmissionStatus(
    submissionId: int,
    eventId: int,
    status: str,
    *,
    reviewerId: int,
    note: str | None = None,
    threadId: int | None = None,
) -> None:
    details = {"threadId": int(threadId)} if int(threadId or 0) > 0 else None
    await setSubmissionStatus(
        submissionId=int(submissionId),
        status=str(status or "").strip().upper(),
        reviewerId=int(reviewerId or 0),
        note=str(note or "").strip(),
        details=details,
    )
    await updateEventRecordStatus(
        eventId=int(eventId),
        status=str(status or "").strip().upper(),
    )


async def syncEventRecordToSheets(eventId: int) -> dict[str, Any]:
    record = await getEventRecord(int(eventId))
    if record is None:
        raise ValueError(f"Honor Guard event record not found: {eventId}")

    if str(record.get("eventType") or "").strip() == "orientation":
        return {"eventId": int(eventId), "archiveSynced": False, "eventHostUpdate": None}

    from features.staff.honorGuard import sheets as honorGuardSheets

    attendees = await listHonorGuardAttendees(int(record.get("eventId") or 0))
    supervisors = [attendee for attendee in attendees if str(attendee.get("participantRole") or "").strip().upper() == "SUPERVISOR"]
    cohosts = [attendee for attendee in attendees if str(attendee.get("participantRole") or "").strip().upper() == "COHOST"]

    hostLookup = await robloxUsers.fetchRobloxUser(
        int(record.get("hostId") or 0),
        int(record.get("guildId") or 0)
    )
    hostText = str(hostLookup.robloxUsername or "").strip()
    supervisorText = await _buildUserText(supervisors, int(record.get("guildId") or 0))
    coHostText = await _buildUserText(cohosts, int(record.get("guildId") or 0))
    metadata = _jsonDict(record.get("metadataJson"))
    scheduleEventId = str(metadata.get("scheduleEventId") or metadata.get("eventId") or "").strip()
    eventType = str(record.get("eventType") or "").strip()
    eventTitle = str(record.get("eventTitle") or "").strip()
    eventDetail = str(metadata.get("eventDetail") or eventTitle).strip()
    eventDate: datetime = record.get("eventDate", datetime.now(tz=timezone.utc))
    await asyncio.to_thread(
        honorGuardSheets.archiveEvent,
        honorGuardSheets.HonorGuardArchiveRecord(
            eventType=eventType.title(),
            eventTimeUtc=str(eventDate.strftime("%Y/%m/%d %I:%M %p")),
            eventTitle=eventTitle,
            host=hostText,
            coHosts=coHostText,
            supervisors=supervisorText,
            eventDuration=format_duration(int(record.get("durationMinutes") or 0)),
            eventDetail=eventDetail,
            attendeeCount=int(record.get("attendeeCount") or 0),
            notes=str(metadata.get("notes") or "").strip(),
            eventId=scheduleEventId,
        ),
    )
    eventHostUpdate = None
    if hostText:
        eventHostUpdate = await asyncio.to_thread(
            honorGuardSheets.incrementEventHostStats,
            host=hostText,
            eventType=eventType,
        )
    return {
        "eventId": int(eventId),
        "archiveSynced": True,
        "eventHostUpdate": eventHostUpdate,
    }


async def syncApprovedSubmissionToSheet(submissionId: int, *, configModule: Any) -> dict[str, Any]:
    count = 0
    auditLogs = []
    submission = await getSubmission(int(submissionId))
    if submission is None:
        raise ValueError(f"Honor Guard submission not found: {submissionId}")
    if _normalizeStatus(submission.get("status")) != "APPROVED":
        raise ValueError("Only approved Honor Guard submissions can be synced.")
    if int(submission.get("sheetSynced") or 0):
        await ensurePointAwardRecordsForSubmission(
            submission=submission,
            sheetSynced=True,
        )
        if str(submission.get("submissionType") or "").strip().upper() == "SOLO_SENTRY":
            await execute(
                """
                UPDATE hg_sentry_logs
                SET sheetSynced = 1
                WHERE submissionId = ?
                """,
                (int(submissionId),),
            )
        return {"alreadySynced": True, "submissionId": int(submissionId)}

    from features.staff.honorGuard import sheets as honorGuardSheets

    if str(submission.get("submissionType") or "").strip().upper() in {"POINT_AWARD", "SOLO_SENTRY"}:
        lookup = await robloxUsers.fetchRobloxUser(
            int(submission.get("targetUserId") or 0),
            int(submission.get("guildId") or 0)
        )
        targetRobloxUsername = str(lookup.robloxUsername or "").strip()

        updateResult = await asyncio.to_thread(
            honorGuardSheets.applyMemberPointDeltas,
            discordId=int(submission.get("targetUserId") or 0),
            robloxUsername=targetRobloxUsername,
            quotaDelta=float(submission.get("quotaPoints") or 0),
            eventDelta=float(submission.get("eventPoints") or 0),
            awardedDelta=float(submission.get("awardedPoints") or 0),
        )
    else:
        eventId = int(_jsonDict(submission.get("metadataJson")).get("eventRecordId"))
        eventPlatoon = str(_jsonDict(submission.get("metadataJson")).get("platoon") or "").strip().upper()
        eventType = str(_jsonDict(submission.get("metadataJson")).get("eventType") or "").strip().upper()
        activePlatoons = getattr(configModule, "honorGuardActivePlatoons", [""])
        activePlatoons.append("NONE")

        attendees = await listHonorGuardAttendees(eventId)
        updates: list[dict] = []
        
        if eventPlatoon not in activePlatoons:
            raise ValueError(f"Invalid platoon for Honor Guard event record: {eventPlatoon}")
        for record in attendees:
            passedExam = str(record.get("examGrade")).upper() == "PASS"
            lookup = await robloxUsers.fetchRobloxUser(
                int(record.get("userId") or 0),
                int(submission.get("guildId") or 0),
            )

            updates.append({
                "userId": record["userId"],
                "robloxUsername": str(lookup.robloxUsername or "").strip(),
                "quotaDelta": record.get("quotaPoints", 0),
                "eventDelta": record.get("eventPoints", 0),
                "juniorExamPassed": eventType == "JGE" and passedExam,
                "ncoExamPassed": eventType == "NCOE" and passedExam,
            })
            count += 1

        updateResult = await asyncio.to_thread(
            honorGuardSheets.applyApprovedLogsBatch,
            updates=updates,
            eventPlatoon=eventPlatoon,
        )

        errors = [entry["error"] for entry in updateResult if entry.get("error")]
        skippedNotes = [entry["note"] for entry in updateResult if entry.get("skipped")]
        skippedCount = len(skippedNotes)
        auditLogs.extend(errors)
        auditLogs.extend(skippedNotes)

        appliedAttendees = [entry for entry in updateResult if entry.get("pointUpdate")]
        if eventPlatoon == "NONE":
            if eventType == "JGE":
                for attendee in appliedAttendees:
                    pointUpdate = attendee["pointUpdate"]
                    auditLogs.append(f"{_mentionUser(attendee['userId'])} ({pointUpdate.robloxUsername}): {_formatPoints(pointUpdate.previousQuotaPoints)} -> {_formatPoints(pointUpdate.quotaPoints)} quota, {_formatPoints(pointUpdate.previousEventPoints)} -> {_formatPoints(pointUpdate.eventPoints)} event points, Passed JGE: {pointUpdate.passedJGE}, Promotion: {attendee['promotion']}")
            elif eventType == "NCOE":
                for attendee in appliedAttendees:
                    pointUpdate = attendee["pointUpdate"]
                    auditLogs.append(f"{_mentionUser(attendee['userId'])} ({pointUpdate.robloxUsername}): {_formatPoints(pointUpdate.previousQuotaPoints)} -> {_formatPoints(pointUpdate.quotaPoints)} quota, {_formatPoints(pointUpdate.previousEventPoints)} -> {_formatPoints(pointUpdate.eventPoints)} event points, Passed NCOE: {pointUpdate.passedNCOE}, Promotion: {attendee['promotion']}")
            else:
                for attendee in appliedAttendees:
                    pointUpdate = attendee["pointUpdate"]
                    auditLogs.append(f"{_mentionUser(attendee['userId'])} ({pointUpdate.robloxUsername}): {_formatPoints(pointUpdate.previousQuotaPoints)} -> {_formatPoints(pointUpdate.quotaPoints)} quota, {_formatPoints(pointUpdate.previousEventPoints)} -> {_formatPoints(pointUpdate.eventPoints)} event points, Promotion: {attendee['promotion']}")
        else:
            for attendee in appliedAttendees:
                pointUpdate = attendee["pointUpdate"]
                auditLogs.append(f"{_mentionUser(attendee['userId'])} ({pointUpdate.robloxUsername}): {_formatPoints(pointUpdate.previousQuotaPoints)} -> {_formatPoints(pointUpdate.quotaPoints)} quota, {_formatPoints(pointUpdate.previousEventPoints)} -> {_formatPoints(pointUpdate.eventPoints)} event points, {_formatPoints(pointUpdate.previousPlatoonPoints)} -> {_formatPoints(pointUpdate.platoonPoints)} platoon points, Promotion: {attendee['promotion']}")
            
            
    await execute(
        """
        UPDATE hg_submissions
        SET sheetSynced = 1,
            appliedAt = datetime('now'),
            updatedAt = datetime('now')
        WHERE submissionId = ?
        """,
        (int(submissionId),),
    )
    await ensurePointAwardRecordsForSubmission(
        submission={**submission, "sheetSynced": 1},
        sheetSynced=True,
    )
    if str(submission.get("submissionType") or "").strip().upper() == "SOLO_SENTRY":
        await execute(
            """
            UPDATE hg_sentry_logs
            SET sheetSynced = 1
            WHERE submissionId = ?
            """,
            (int(submissionId),),
        )
    if str(submission.get("submissionType") or "").strip().upper() == "EVENT_RECORD":
        await _appendSubmissionEvent(
            submissionId=int(submissionId),
            actorId=int(submission.get("reviewerId") or 0),
            eventType="SHEET_SYNCED",
            fromStatus="APPROVED",
            toStatus="APPROVED",
            details={
                "count": count,
            },
        )
        return {
            "alreadySynced": False,
            "submissionId": int(submissionId),
            "auditLogs": auditLogs,
            "skippedCount": skippedCount,
        }
    else:
        await _appendSubmissionEvent(
            submissionId=int(submissionId),
            actorId=int(submission.get("reviewerId") or 0),
            eventType="SHEET_SYNCED",
            fromStatus="APPROVED",
            toStatus="APPROVED",
            details={
                "row": updateResult.row,
                "quotaPoints": updateResult.quotaPoints,
                "eventPoints": updateResult.eventPoints,
            },
        )
        return {
            "alreadySynced": False,
            "submissionId": int(submissionId),
            "auditLogs": auditLogs,
            "row": updateResult.row,
            "robloxUsername": updateResult.robloxUsername,
            "quotaPoints": updateResult.quotaPoints,
            "eventPoints": updateResult.eventPoints,
            "awardedPoints": updateResult.awardedPoints,
            "totalPoints": updateResult.totalPoints,
            "activityStatus": updateResult.activityStatus,
        }

