from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urljoin

import aiohttp

import config


@dataclass(frozen=True)
class ExternalSourceResult:
    source: str
    status: str
    subjectType: str
    subjectId: int
    matches: list[dict[str, Any]]
    summary: dict[str, Any]
    error: Optional[str] = None


@dataclass(frozen=True)
class ExternalScanResult:
    status: str
    matches: list[dict[str, Any]]
    details: list[dict[str, Any]]
    error: Optional[str] = None


def _cfg(configModule: Any, name: str, default: Any) -> Any:
    return getattr(configModule, name, default)


def _safeInt(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safeFloat(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _asBool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _baseUrl(value: Any, default: str) -> str:
    raw = str(value or default).strip() or default
    return raw.rstrip("/") + "/"


def _bearerHeader(token: str) -> str:
    clean = str(token or "").strip()
    if clean.lower().startswith("bearer "):
        return clean
    return f"Bearer {clean}"


def _sourceDetail(result: ExternalSourceResult) -> dict[str, Any]:
    return {
        "source": result.source,
        "status": result.status,
        "subjectType": result.subjectType,
        "subjectId": int(result.subjectId or 0),
        "matches": result.matches,
        "summary": result.summary,
        "error": result.error,
    }


async def _requestJson(
    session: aiohttp.ClientSession,
    url: str,
    *,
    headers: dict[str, str],
    timeoutSec: int,
) -> tuple[int, Any, Optional[str]]:
    timeout = aiohttp.ClientTimeout(total=max(2, int(timeoutSec or 10)))
    try:
        async with session.get(url, headers=headers, timeout=timeout) as response:
            text = await response.text()
            if response.status in {204, 404}:
                return response.status, None, None
            if response.status >= 400:
                return response.status, None, f"HTTP {response.status}: {text[:300]}"
            try:
                return response.status, await response.json(content_type=None), None
            except Exception as exc:
                return response.status, None, f"Invalid JSON: {exc}"
    except asyncio.TimeoutError:
        return 0, None, "Request timed out."
    except aiohttp.ClientError as exc:
        return 0, None, str(exc)


def _pickTaseUserRecord(payload: Any, discordUserId: int) -> dict[str, Any]:
    if isinstance(payload, dict):
        if isinstance(payload.get("results"), list):
            for row in payload["results"]:
                if isinstance(row, dict) and _safeInt(row.get("userId")) == int(discordUserId):
                    return row
            return {}
        if isinstance(payload.get("data"), dict):
            return _pickTaseUserRecord(payload["data"], discordUserId)
        if isinstance(payload.get("data"), list):
            return _pickTaseUserRecord(payload["data"], discordUserId)
        if _safeInt(payload.get("userId")) == int(discordUserId):
            return payload
        return {}
    if isinstance(payload, list):
        for row in payload:
            if isinstance(row, dict) and _safeInt(row.get("userId")) == int(discordUserId):
                return row
    return {}


def _compactTaseGuild(guild: dict[str, Any]) -> dict[str, Any]:
    types: list[str] = []
    for typeRow in list(guild.get("types") or []):
        if not isinstance(typeRow, dict):
            continue
        name = str(typeRow.get("name") or "").strip()
        summary = str(typeRow.get("summary") or "").strip()
        if name and summary:
            types.append(f"{name}: {summary}")
        elif name:
            types.append(name)
    detail = guild.get("detail") if isinstance(guild.get("detail"), dict) else {}
    return {
        "id": _safeInt(guild.get("id")),
        "name": str(guild.get("name") or "").strip(),
        "score": _safeFloat(guild.get("score")),
        "firstSeen": guild.get("firstSeen"),
        "lastSeen": guild.get("lastSeen"),
        "types": types[:5],
        "detail": {
            "messages": _safeInt(detail.get("messages")),
            "typing": _safeInt(detail.get("typing")),
            "interaction": _safeInt(detail.get("interaction")),
            "indirect": _safeInt(detail.get("indirect")),
            "staff": _safeInt(detail.get("staff")),
            "booster": _safeInt(detail.get("booster")),
        },
    }


def _normalizeTasePayload(payload: Any, discordUserId: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    record = _pickTaseUserRecord(payload, discordUserId)
    if not record:
        return [], {"recordsFound": 0}

    detail = record.get("detail") if isinstance(record.get("detail"), dict) else {}
    guilds = [guild for guild in list(record.get("guilds") or []) if isinstance(guild, dict)]
    topGuilds = sorted((_compactTaseGuild(guild) for guild in guilds), key=lambda row: float(row.get("score") or 0), reverse=True)
    typeNames: list[str] = []
    for guild in topGuilds:
        for typeName in list(guild.get("types") or []):
            clean = str(typeName or "").strip()
            if clean and clean not in typeNames:
                typeNames.append(clean)

    scoreSum = _safeFloat(detail.get("scoreSum"))
    summary = {
        "recordsFound": 1 if guilds or scoreSum > 0 or detail else 0,
        "scoreSum": scoreSum,
        "guildCount": len(guilds),
        "pastOffender": _asBool(detail.get("pastOffender")),
        "appealing": _asBool(detail.get("appealing")),
        "lastSeen": detail.get("lastSeen"),
        "typeNames": typeNames[:10],
    }
    if not summary["recordsFound"]:
        return [], summary

    return [
        {
            "source": "TASE",
            "type": "discord_safety",
            "subjectType": "discord",
            "subjectId": int(discordUserId),
            "scoreSum": scoreSum,
            "guildCount": len(guilds),
            "pastOffender": summary["pastOffender"],
            "appealing": summary["appealing"],
            "lastSeen": summary["lastSeen"],
            "typeNames": typeNames[:10],
            "topGuilds": topGuilds[:5],
        }
    ], summary


async def scanTase(
    *,
    discordUserId: int,
    session: aiohttp.ClientSession,
    configModule: Any = config,
) -> ExternalSourceResult:
    if int(discordUserId or 0) <= 0:
        return ExternalSourceResult("TASE", "SKIPPED", "discord", 0, [], {"reason": "no_discord_user"})
    if not bool(_cfg(configModule, "bgIntelligenceTaseEnabled", True)):
        return ExternalSourceResult("TASE", "SKIPPED", "discord", int(discordUserId), [], {"reason": "disabled"})

    token = str(_cfg(configModule, "bgIntelligenceTaseApiToken", "") or "").strip()
    if not token:
        return ExternalSourceResult("TASE", "SKIPPED", "discord", int(discordUserId), [], {"reason": "missing_token"})

    baseUrl = _baseUrl(_cfg(configModule, "bgIntelligenceTaseApiBaseUrl", "https://api.tasebot.org"), "https://api.tasebot.org")
    url = urljoin(baseUrl, f"v2/check/{int(discordUserId)}")
    timeoutSec = _safeInt(_cfg(configModule, "bgIntelligenceTaseTimeoutSec", 10), 10)
    statusCode, payload, error = await _requestJson(
        session,
        url,
        headers={"Authorization": _bearerHeader(token), "Accept": "application/json"},
        timeoutSec=timeoutSec,
    )
    if error:
        return ExternalSourceResult(
            "TASE",
            "ERROR",
            "discord",
            int(discordUserId),
            [],
            {"httpStatus": statusCode},
            error,
        )

    matches, summary = _normalizeTasePayload(payload, int(discordUserId))
    summary["httpStatus"] = statusCode
    return ExternalSourceResult("TASE", "OK", "discord", int(discordUserId), matches, summary)


def _compactMocoGroup(group: dict[str, Any]) -> dict[str, Any]:
    typeNames: list[str] = []
    for value in list(group.get("types") or group.get("categories") or []):
        clean = str(value.get("name") if isinstance(value, dict) else value or "").strip()
        if clean:
            typeNames.append(clean)
    return {
        "id": _safeInt(group.get("id") or group.get("groupId")),
        "name": str(group.get("name") or group.get("groupName") or "").strip(),
        "lastSeen": group.get("lastSeen") or group.get("last_seen"),
        "types": typeNames[:5],
    }


def _normalizeMocoPayload(payload: Any, robloxUserId: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not isinstance(payload, dict):
        return [], {"recordsFound": 0}

    users = payload.get("users")
    record: dict[str, Any] = payload
    if isinstance(users, list):
        for row in users:
            if isinstance(row, dict) and _safeInt(row.get("userId")) == int(robloxUserId):
                record = row
                break
        else:
            record = payload if _safeInt(payload.get("userId")) == int(robloxUserId) else {}

    found = bool(record.get("found", record.get("success", payload.get("found", payload.get("success", False)))))
    rawGroups = record.get("groups") or record.get("flaggedGroups") or record.get("matches") or []
    topGroups = [
        _compactMocoGroup(group)
        for group in rawGroups
        if isinstance(group, dict)
    ][:5] if isinstance(rawGroups, list) else []
    groupCount = _safeInt(record.get("groupCount") or record.get("groupsCount") or len(topGroups))
    lastSeen = record.get("lastSeen")
    username = str(record.get("username") or "").strip()
    summary = {
        "recordsFound": 1 if found else 0,
        "username": username,
        "groupCount": groupCount,
        "lastSeen": lastSeen,
        "topGroups": topGroups,
    }
    if not found:
        return [], summary
    return [
        {
            "source": "Moco-co",
            "type": "roblox_safety",
            "subjectType": "roblox",
            "subjectId": int(robloxUserId),
            "username": username,
            "groupCount": groupCount,
            "lastSeen": lastSeen,
            "topGroups": topGroups,
        }
    ], summary


async def scanMoco(
    *,
    robloxUserId: int,
    session: aiohttp.ClientSession,
    configModule: Any = config,
) -> ExternalSourceResult:
    if int(robloxUserId or 0) <= 0:
        return ExternalSourceResult("Moco-co", "SKIPPED", "roblox", 0, [], {"reason": "no_roblox_user"})
    if not bool(_cfg(configModule, "bgIntelligenceMocoEnabled", True)):
        return ExternalSourceResult("Moco-co", "SKIPPED", "roblox", int(robloxUserId), [], {"reason": "disabled"})

    apiKey = str(_cfg(configModule, "bgIntelligenceMocoApiKey", "") or "").strip()
    if not apiKey:
        return ExternalSourceResult("Moco-co", "SKIPPED", "roblox", int(robloxUserId), [], {"reason": "missing_api_key"})

    baseUrl = _baseUrl(_cfg(configModule, "bgIntelligenceMocoApiBaseUrl", "https://api.moco-co.org"), "https://api.moco-co.org")
    url = urljoin(baseUrl, f"checkuser/{int(robloxUserId)}")
    timeoutSec = _safeInt(_cfg(configModule, "bgIntelligenceMocoTimeoutSec", 10), 10)
    statusCode, payload, error = await _requestJson(
        session,
        url,
        headers={"x-api-key": apiKey, "Accept": "application/json"},
        timeoutSec=timeoutSec,
    )
    if error:
        return ExternalSourceResult(
            "Moco-co",
            "ERROR",
            "roblox",
            int(robloxUserId),
            [],
            {"httpStatus": statusCode},
            error,
        )

    matches, summary = _normalizeMocoPayload(payload, int(robloxUserId))
    summary["httpStatus"] = statusCode
    return ExternalSourceResult("Moco-co", "OK", "roblox", int(robloxUserId), matches, summary)


# Rotector is served through the Rayward API. Only Flagged (1) and Confirmed (2)
# are findings. Every other non-zero value is a process state, and Unflagged (0)
# is never presented as safe per Rotector's terms of use.
rotectorFlagNames: dict[int, str] = {
    0: "Unflagged",
    1: "Flagged",
    2: "Confirmed",
    3: "Queued",
    4: "Provisional Flag",
    5: "Mixed",
    6: "Past Offender",
    8: "Redacted",
}
rotectorActionableFlagTypes = frozenset({1, 2})
_rotectorMatchFlagTypes = frozenset({1, 2, 4, 5, 6})
_rotectorReasonLimit = 6
_rotectorEvidenceLimit = 6


def _isoDate(epochSeconds: int) -> str:
    try:
        return datetime.fromtimestamp(int(epochSeconds), timezone.utc).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return "unknown"


def _retryAfterText(headers: Any) -> str:
    retryAfter = str((headers or {}).get("Retry-After") or "").strip()
    return f" Retry after {retryAfter}s." if retryAfter else ""


async def _requestRaywardJson(
    session: aiohttp.ClientSession,
    url: str,
    *,
    headers: dict[str, str],
    timeoutSec: int,
) -> tuple[int, Any, Optional[str]]:
    """Fetch a Rayward reply. Unlike other sources, 404 and 503 are errors, not "no record"."""

    timeout = aiohttp.ClientTimeout(total=max(2, int(timeoutSec or 10)))
    try:
        async with session.get(url, headers=headers, timeout=timeout) as response:
            try:
                payload = await response.json(content_type=None)
            except Exception:
                payload = None
            envelope = payload if isinstance(payload, dict) else {}
            if response.status < 400 and envelope.get("success") is True:
                return response.status, envelope.get("data"), None

            message = str(envelope.get("error") or "").strip()[:200]
            code = str(envelope.get("code") or "").strip()
            requestId = str(envelope.get("requestId") or "").strip()
            if response.status == 429:
                label = "Daily lookup quota reached." if code == "DAILY_QUOTA_EXCEEDED" else "Rate limited."
                error = f"{label}{_retryAfterText(response.headers)}"
            elif response.status == 503:
                error = "Rotector did not answer; the account's status is unknown."
            elif response.status == 401:
                error = "Rayward rejected the API key."
            elif response.status == 403:
                error = "Rayward API access is not approved or has been suspended."
            elif response.status < 400:
                error = "Unexpected Rayward response."
            else:
                error = f"HTTP {response.status}"
            if message:
                error += f" {message}"
            if code:
                error += f" [{code}]"
            if requestId:
                error += f" (request {requestId})"
            return response.status, None, error
    except asyncio.TimeoutError:
        return 0, None, "Request timed out."
    except aiohttp.ClientError as exc:
        return 0, None, str(exc)


def _compactRotectorEvidence(item: Any) -> Optional[str]:
    # New evidence kinds can appear without a version bump. Skip unknowns
    if not isinstance(item, dict):
        return None
    kind = str(item.get("kind") or "").strip()
    if kind == "text":
        text = str(item.get("text") or "").strip()
        return text[:240] if text else None
    if kind == "outfit":
        name = str(item.get("name") or "").strip() or "Unnamed outfit"
        if str(item.get("outfitId") or "") == "0":
            name = f"{name} (current avatar)"
        category = str(item.get("category") or "").strip()
        description = str(item.get("description") or "").strip()
        text = f"Outfit {name}"
        if category:
            text += f" [{category}]"
        if description:
            text += f": {description}"
        return text[:240]
    if kind == "discordUser":
        discordId = str(item.get("discordId") or "").strip()
        return f"Linked Discord account {discordId}" if discordId else None
    if kind == "discordGuild":
        # Sanitize the server name
        safeName = str(item.get("safeName") or "").strip() or "Tracked server"
        types = [str(value).strip() for value in list(item.get("types") or []) if str(value).strip()]
        text = f"Server {safeName}"
        if types:
            text += f" [{', '.join(types[:3])}]"
        extras: list[str] = []
        joinedAt = _safeInt(item.get("joinedAt"))
        if joinedAt > 0:
            extras.append(f"joined {_isoDate(joinedAt)}")
        lastSeen = _safeInt(item.get("lastSeen"))
        if lastSeen > 0:
            extras.append(f"last seen {_isoDate(lastSeen)}")
        messages = _safeInt(item.get("messages"))
        if messages > 0:
            extras.append(f"{messages:,} message(s)")
        if _asBool(item.get("staff")):
            extras.append("staff")
        if _asBool(item.get("booster")):
            extras.append("booster")
        if _asBool(item.get("verifiedLeft")):
            extras.append("left")
        if extras:
            text += f" - {', '.join(extras)}"
        return text[:240]
    return None


def _compactRotectorReason(reason: dict[str, Any]) -> dict[str, Any]:
    # Flags sorted worst -> least worst
    detectors: list[str] = []
    summaries: list[str] = []
    for source in list(reason.get("sources") or []):
        if not isinstance(source, dict):
            continue
        label = str(source.get("label") or source.get("id") or "").strip()
        if label and label not in detectors:
            detectors.append(label)
        summary = str(source.get("summary") or "").strip()
        if summary:
            summaries.append(summary[:200])
    evidence: list[str] = []
    for item in list(reason.get("evidence") or []):
        line = _compactRotectorEvidence(item)
        if line:
            evidence.append(line)
        if len(evidence) >= _rotectorEvidenceLimit:
            break
    reasonType = str(reason.get("type") or "").strip()
    return {
        "type": reasonType,
        "title": str(reason.get("title") or reasonType or "Reason").strip(),
        "detectors": detectors[:5],
        "summaries": summaries[:3],
        "evidence": evidence,
    }


def _normalizeRotectorPayload(
    payload: Any,
    *,
    subjectType: str,
    subjectId: int,
    ownRobloxUserId: int = 0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    record = payload if isinstance(payload, dict) else {}
    flagType = _safeInt(record.get("flagType"))
    flagName = rotectorFlagNames.get(flagType, f"Flag type {flagType}")
    linkedAccounts = [
        {
            "robloxUserId": _safeInt(row.get("robloxUserId")),
            "robloxUsername": str(row.get("robloxUsername") or "").strip(),
            "flagType": _safeInt(row.get("flagType")),
            "flagName": rotectorFlagNames.get(_safeInt(row.get("flagType")), "Flagged"),
        }
        for row in list(record.get("linkedRobloxAccounts") or [])
        if isinstance(row, dict) and _safeInt(row.get("robloxUserId")) > 0
    ]
    otherLinkedAccounts = [
        row for row in linkedAccounts if row["robloxUserId"] != int(ownRobloxUserId or 0)
    ]
    summary: dict[str, Any] = {
        "recordsFound": 1 if flagType in _rotectorMatchFlagTypes or otherLinkedAccounts else 0,
        "flagType": flagType,
        "flagName": flagName,
        "statusLabel": str(record.get("statusLabel") or "").strip(),
        "linkedRobloxAccountCount": len(otherLinkedAccounts),
    }
    if not summary["recordsFound"]:
        return [], summary

    reasons = [
        _compactRotectorReason(reason)
        for reason in list(record.get("reasons") or [])
        if isinstance(reason, dict)
    ][:_rotectorReasonLimit]
    provisionalTitles = [
        str(reason.get("title") or reason.get("type") or "").strip()
        for reason in list(record.get("provisionalReasons") or [])
        if isinstance(reason, dict) and str(reason.get("title") or reason.get("type") or "").strip()
    ][:_rotectorReasonLimit]
    reviewer = record.get("reviewer") if isinstance(record.get("reviewer"), dict) else {}
    return [
        {
            "source": "Rotector",
            "type": f"{subjectType}_safety",
            "subjectType": subjectType,
            "subjectId": subjectId,
            "flagType": flagType,
            "flagName": flagName,
            "actionable": flagType in rotectorActionableFlagTypes,
            "statusLabel": summary["statusLabel"],
            "category": str(record.get("category") or "").strip(),
            "categoryLabel": str(record.get("categoryLabel") or "").strip(),
            "reasons": reasons,
            "provisionalReasons": provisionalTitles,
            "reviewed": bool(reviewer),
            "lastUpdated": _safeInt(record.get("lastUpdated")) or None,
            "linkedRobloxAccounts": otherLinkedAccounts[:5],
        }
    ], summary


async def _scanRotector(
    *,
    subjectType: str,
    subjectId: int,
    ownRobloxUserId: int,
    session: aiohttp.ClientSession,
    configModule: Any,
) -> ExternalSourceResult:
    noSubjectReason = "no_roblox_user" if subjectType == "roblox" else "no_discord_user"
    if int(subjectId or 0) <= 0:
        return ExternalSourceResult("Rotector", "SKIPPED", subjectType, 0, [], {"reason": noSubjectReason})
    enabledKey = "bgIntelligenceRotectorEnabled" if subjectType == "roblox" else "bgIntelligenceRotectorDiscordEnabled"
    if not bool(_cfg(configModule, "bgIntelligenceRotectorEnabled", True)) or not bool(_cfg(configModule, enabledKey, True)):
        return ExternalSourceResult("Rotector", "SKIPPED", subjectType, int(subjectId), [], {"reason": "disabled"})

    apiKey = str(_cfg(configModule, "bgIntelligenceRaywardApiKey", "") or "").strip()
    if not apiKey:
        return ExternalSourceResult("Rotector", "SKIPPED", subjectType, int(subjectId), [], {"reason": "missing_api_key"})

    baseUrl = _baseUrl(_cfg(configModule, "bgIntelligenceRaywardApiBaseUrl", "https://roscoe.rayward.app"), "https://roscoe.rayward.app")
    url = urljoin(baseUrl, f"v2/lookup/rotector/{subjectType}/user/{int(subjectId)}")
    timeoutSec = _safeInt(_cfg(configModule, "bgIntelligenceRaywardTimeoutSec", 10), 10)
    statusCode, payload, error = await _requestRaywardJson(
        session,
        url,
        headers={"Authorization": _bearerHeader(apiKey), "Accept": "application/json"},
        timeoutSec=timeoutSec,
    )
    if error:
        return ExternalSourceResult(
            "Rotector",
            "ERROR",
            subjectType,
            int(subjectId),
            [],
            {"httpStatus": statusCode},
            error,
        )

    matches, summary = _normalizeRotectorPayload(
        payload,
        subjectType=subjectType,
        subjectId=int(subjectId),
        ownRobloxUserId=int(ownRobloxUserId or 0),
    )
    summary["httpStatus"] = statusCode
    return ExternalSourceResult("Rotector", "OK", subjectType, int(subjectId), matches, summary)


async def scanRotectorRoblox(
    *,
    robloxUserId: int,
    session: aiohttp.ClientSession,
    configModule: Any = config,
) -> ExternalSourceResult:
    return await _scanRotector(
        subjectType="roblox",
        subjectId=int(robloxUserId or 0),
        ownRobloxUserId=int(robloxUserId or 0),
        session=session,
        configModule=configModule,
    )


async def scanRotectorDiscord(
    *,
    discordUserId: int,
    robloxUserId: int,
    session: aiohttp.ClientSession,
    configModule: Any = config,
) -> ExternalSourceResult:
    return await _scanRotector(
        subjectType="discord",
        subjectId=int(discordUserId or 0),
        ownRobloxUserId=int(robloxUserId or 0),
        session=session,
        configModule=configModule,
    )


async def scanExternalSources(
    *,
    discordUserId: int,
    robloxUserId: int | None,
    configModule: Any = config,
) -> ExternalScanResult:
    if not bool(_cfg(configModule, "bgIntelligenceExternalSourcesEnabled", True)):
        return ExternalScanResult("SKIPPED", [], [], "External sources disabled.")

    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(
            scanTase(discordUserId=int(discordUserId or 0), session=session, configModule=configModule),
            scanMoco(robloxUserId=int(robloxUserId or 0), session=session, configModule=configModule),
            scanRotectorRoblox(robloxUserId=int(robloxUserId or 0), session=session, configModule=configModule),
            scanRotectorDiscord(
                discordUserId=int(discordUserId or 0),
                robloxUserId=int(robloxUserId or 0),
                session=session,
                configModule=configModule,
            ),
            return_exceptions=True,
        )

    normalizedResults: list[ExternalSourceResult] = []
    for result in results:
        if isinstance(result, ExternalSourceResult):
            normalizedResults.append(result)
        elif isinstance(result, Exception):
            normalizedResults.append(
                ExternalSourceResult(
                    "External",
                    "ERROR",
                    "unknown",
                    0,
                    [],
                    {},
                    str(result),
                )
            )

    details = [_sourceDetail(result) for result in normalizedResults]
    matches: list[dict[str, Any]] = []
    for result in normalizedResults:
        matches.extend(result.matches)

    attempted = [result for result in normalizedResults if result.status != "SKIPPED"]
    errors = [result for result in attempted if result.status == "ERROR"]
    if not attempted:
        status = "SKIPPED"
    elif errors and len(errors) == len(attempted):
        status = "ERROR"
    elif errors:
        status = "PARTIAL"
    else:
        status = "OK"

    errorText = "; ".join(
        f"{result.source}: {result.error}"
        for result in errors
        if result.error
    ) or None
    return ExternalScanResult(status, matches, details, errorText)
