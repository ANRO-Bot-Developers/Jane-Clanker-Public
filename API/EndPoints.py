from __future__ import annotations

import asyncio
import hmac
import logging
from collections import OrderedDict
from typing import Any

import discord
from quart import Quart, Response, jsonify, request

from features.staff.sessions import bgSpreadsheetRouting
from features.staff.sessions.service import attemptClockIn
from features.staff.sessions.viewRuntime import requestSessionMessageUpdate


log = logging.getLogger(__name__)

_apiBotClient: discord.Client | None = None
_apiToken = ""


def configureApi(*, botClient: discord.Client | None, token: str) -> None:
    global _apiBotClient, _apiToken
    _apiBotClient = botClient
    _apiToken = str(token or "").strip()


def validateToken(requestToken: object) -> bool:
    candidate = str(requestToken or "").strip()
    return bool(_apiToken and candidate and hmac.compare_digest(candidate, _apiToken))


def _positiveInt(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def parseClockInPayload(requestData: object) -> tuple[int, str, int] | None:
    if not isinstance(requestData, dict):
        return None

    sessionId = _positiveInt(requestData.get("sessionId"))
    userId = _positiveInt(requestData.get("sessionUserId"))
    rawPassword = requestData.get("sessionPassword")
    password = rawPassword if isinstance(rawPassword, str) else ""
    if sessionId <= 0 or userId <= 0 or not password:
        return None
    return sessionId, password, userId


async def enterOrientation() -> Response:
    if not validateToken(request.headers.get("X-API-TOKEN")):
        return jsonify({"ok": False, "error": "unauthorized"}), 403

    requestData = await request.get_json(silent=True)
    parsed = parseClockInPayload(requestData)
    if parsed is None:
        return jsonify({"ok": False, "error": "invalid-request"}), 400
    sessionId, password, userId = parsed

    if _apiBotClient is None:
        return jsonify({"ok": False, "error": "discord-client-unavailable"}), 503

    clockInResult = await attemptClockIn(sessionId, userId, password)
    resultText = str(clockInResult.get("status") or "").upper()
    responseBody: dict[str, Any] = {
        "ok": resultText == "ADDED",
        "result": resultText,
        "attendees": clockInResult.get("attendeeCount"),
    }

    if resultText == "ADDED":
        try:
            await requestSessionMessageUpdate(
                bot=_apiBotClient,
                sessionId=sessionId,
                delaySec=0.5,
            )
        except Exception:
            log.exception(
                "Orientation API could not schedule a message refresh for session %s.",
                sessionId,
            )
        return jsonify(responseBody), 200

    # Preserve the legacy endpoint's non-success status for existing clients.
    return jsonify(responseBody), 418


_maxRequestIdLength = 200
_maxPassedUserIds = 250
_maxHostNameLength = 100
_maxRememberedSheetRequests = 500
_recentSheetRequestIds: OrderedDict[str, None] = OrderedDict()
_sheetTasks: set[asyncio.Task] = set()


def resetBgcSpreadsheetRequests() -> None:
    _recentSheetRequestIds.clear()


def _snowflake(value: object) -> int:
    """Discord IDs arrive as strings from JavaScript callers; integers are accepted too."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value if value > 0 else 0
    if isinstance(value, str):
        text = value.strip()
        if text.isascii() and text.isdigit():
            return int(text)
    return 0


def parseBgcSpreadsheetPayload(requestData: object) -> dict[str, Any] | None:
    if not isinstance(requestData, dict):
        return None

    rawRequestId = requestData.get("requestId")
    requestId = rawRequestId.strip() if isinstance(rawRequestId, str) else ""
    if not requestId or len(requestId) > _maxRequestIdLength:
        return None

    guildId = _snowflake(requestData.get("guildId"))
    hostId = _snowflake(requestData.get("hostId"))
    if guildId <= 0 or hostId <= 0:
        return None

    rawPassedUserIds = requestData.get("passedUserIds")
    if not isinstance(rawPassedUserIds, list) or not rawPassedUserIds:
        return None
    if len(rawPassedUserIds) > _maxPassedUserIds:
        return None

    passedUserIds: list[int] = []
    seen: set[int] = set()
    for rawUserId in rawPassedUserIds:
        userId = _snowflake(rawUserId)
        if userId <= 0:
            return None
        if userId in seen:
            continue
        seen.add(userId)
        passedUserIds.append(userId)

    rawHostName = requestData.get("hostName")
    hostName = rawHostName.strip()[:_maxHostNameLength] if isinstance(rawHostName, str) else ""

    return {
        "requestId": requestId,
        "guildId": guildId,
        "channelId": _snowflake(requestData.get("channelId")),
        "messageId": _snowflake(requestData.get("messageId")),
        "hostId": hostId,
        "hostName": hostName,
        "passedUserIds": passedUserIds,
    }


def _rememberSheetRequest(requestId: str) -> bool:
    """Record a request id. Returns False when it was already seen."""
    if requestId in _recentSheetRequestIds:
        return False
    _recentSheetRequestIds[requestId] = None
    while len(_recentSheetRequestIds) > _maxRememberedSheetRequests:
        _recentSheetRequestIds.popitem(last=False)
    return True


async def _runBgcSpreadsheet(botClient: discord.Client, guild: discord.Guild | None, parsed: dict[str, Any]) -> None:
    requestId = parsed["requestId"]
    try:
        result = await bgSpreadsheetRouting.routeExternalOrientationSpreadsheet(
            botClient,
            guild,
            guildId=parsed["guildId"],
            requestId=requestId,
            hostId=parsed["hostId"],
            hostName=parsed["hostName"],
            channelId=parsed["channelId"],
            messageId=parsed["messageId"],
            passedUserIds=parsed["passedUserIds"],
        )
    except Exception:
        log.exception("Orientation API could not create the BGC spreadsheet for request %s.", requestId)
        return

    if not str(getattr(result, "url", "") or "").strip():
        log.error(
            "Orientation API request %s did not produce a BGC spreadsheet: %s",
            requestId,
            str(getattr(result, "skipped_reason", "") or "no reason given"),
        )


async def createBgcSpreadsheet() -> Response:
    if not validateToken(request.headers.get("X-API-TOKEN")):
        return jsonify({"ok": False, "error": "unauthorized"}), 403

    parsed = parseBgcSpreadsheetPayload(await request.get_json(silent=True))
    if parsed is None:
        return jsonify({"ok": False, "error": "invalid-request"}), 400

    getGuild = getattr(_apiBotClient, "get_guild", None)
    if _apiBotClient is None or not callable(getGuild):
        return jsonify({"ok": False, "error": "discord-client-unavailable"}), 503

    # Jane does not have to be in the server the orientation ran in; guild is None then.
    guild = getGuild(parsed["guildId"])

    # Only remember the id once the request is certain to start, so a rejected
    # request can be retried.
    if not _rememberSheetRequest(parsed["requestId"]):
        return jsonify({"ok": True, "status": "duplicate"}), 200

    task = asyncio.create_task(_runBgcSpreadsheet(_apiBotClient, guild, parsed))
    _sheetTasks.add(task)
    task.add_done_callback(_sheetTasks.discard)
    return jsonify({"ok": True, "status": "accepted"}), 202


def registerRoutes(app: Quart) -> None:
    if "enterOrientation" not in app.view_functions:
        app.add_url_rule(
            "/enterOrientation",
            endpoint="enterOrientation",
            view_func=enterOrientation,
            methods=["POST"],
        )
    if "createBgcSpreadsheet" not in app.view_functions:
        app.add_url_rule(
            "/orientation/bgc-spreadsheet",
            endpoint="createBgcSpreadsheet",
            view_func=createBgcSpreadsheet,
            methods=["POST"],
        )
