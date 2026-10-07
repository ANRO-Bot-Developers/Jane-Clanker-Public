from __future__ import annotations


def formatUserMentions(userIds: list[int]) -> str:
    if not userIds:
        return "(none)"
    return "\n".join(f"<@{int(userId)}>" for userId in userIds)


def buildBgFinalSummaryText(
    *,
    sessionId: int,
    approvedUserIds: list[int],
    rejectedUserIds: list[int],
    pendingUserIds: list[int],
    moderatorStatsLines: list[str],
) -> str:
    lines: list[str] = [
        "### BG Check Final Results",
        f"Session: `{int(sessionId)}`",
        "",
        f"**Approved ({len(approvedUserIds)}):**",
        formatUserMentions(approvedUserIds),
        "",
        f"**Rejected ({len(rejectedUserIds)}):**",
        formatUserMentions(rejectedUserIds),
        "",
        f"**Unresolved/Pending ({len(pendingUserIds)}):**",
        formatUserMentions(pendingUserIds),
    ]
    lines.extend(
        [
            "",
            "**Moderator Stats:**",
            "\n".join(moderatorStatsLines) if moderatorStatsLines else "(none)",
        ]
    )
    return "\n".join(lines)


def normalizeForumPostTitle(value: str, fallback: str) -> str:
    title = str(value or "").strip()
    if not title:
        title = fallback
    if len(title) > 100:
        title = title[:100]
    return title

