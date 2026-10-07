import discord
import json
from typing import Dict


def statusIcon(status: str) -> str:
    if status == "APPROVED":
        return ":white_check_mark: Approved"
    if status == "REJECTED":
        return ":x: Rejected"
    if status == "NEEDS_INFO":
        return ":warning: Needs clarification"
    return ":o: Pending"


recruitLookupFound = "FOUND"
recruitLookupNotFound = "NOT_FOUND"
recruitLookupUnavailable = "UNAVAILABLE"

_recruitLookupText = {
    recruitLookupFound: ":white_check_mark: Jane found this user in the ANRO server.",
    recruitLookupNotFound: ":x: Jane couldn't find this user in the ANRO server.",
    recruitLookupUnavailable: ":warning: Jane isn't in the ANRO server (verify manually).",
}


def buildRecruitmentEmbed(submission: Dict) -> discord.Embed:
    embed = discord.Embed(
        title="Recruitment Log",
    )
    recruitUserId = int(submission["recruitUserId"])
    recruitDisplayName = str(submission.get("recruitDisplayName") or "").strip()
    recruitText = f"<@{recruitUserId}>"
    if recruitDisplayName:
        recruitText = f"{recruitText} ({recruitDisplayName})"
    embed.add_field(name="Recruiter", value=f"<@{submission['submitterId']}>", inline=False)
    embed.add_field(name="Recruited User", value=recruitText, inline=False)
    lookupText = _recruitLookupText.get(str(submission.get("recruitLookupStatus") or "").strip().upper())
    if lookupText:
        embed.add_field(name="Server Check", value=lookupText, inline=False)
    passed = "Yes" if submission["passedOrientation"] else "Pending"
    embed.add_field(name="Passed Orientation", value=passed, inline=True)
    embed.add_field(name="Potential Points", value=str(submission["points"]), inline=True)
    embed.add_field(name="Status", value=statusIcon(submission["status"]), inline=False)
    return embed


def buildRecruitmentTimeEmbed(submission: Dict) -> discord.Embed:
    patrolType = str(submission.get("patrolType") or "solo").strip().lower()
    participantRaw = submission.get("participantUserIds")
    participantIds: list[int] = []
    if participantRaw:
        try:
            data = json.loads(participantRaw)
            if isinstance(data, list):
                for value in data:
                    try:
                        participantIds.append(int(value))
                    except (TypeError, ValueError):
                        continue
        except json.JSONDecodeError:
            participantIds = []

    embed = discord.Embed(
        title="Recruitment Patrol Log",
        description=f"Submission ID: {submission['submissionId']}",
    )
    embed.add_field(name="Recruiter", value=f"<@{submission['submitterId']}>", inline=False)
    embed.add_field(name="Patrol Type", value=patrolType.title(), inline=True)
    embed.add_field(name="Duration (minutes)", value=str(submission["durationMinutes"]), inline=True)
    embed.add_field(name="Potential Points", value=str(submission["points"]), inline=True)
    if patrolType == "group":
        if participantIds:
            mentions = [f"<@{userId}>" for userId in participantIds[:20]]
            if len(participantIds) > 20:
                mentions.append(f"... and {len(participantIds) - 20} more")
            embed.add_field(
                name=f"Attendees ({len(participantIds)})",
                value="\n".join(mentions),
                inline=False,
            )
        else:
            embed.add_field(name="Attendees", value="None recorded", inline=False)
    embed.add_field(name="Status", value=statusIcon(submission["status"]), inline=False)
    return embed
