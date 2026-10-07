from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import discord

import config
from runtime import interaction as interactionRuntime
from runtime import normalization
from runtime import permissions as runtimePermissions
from runtime import webhooks as runtimeWebhooks
from silly import cookieCog


_skinMentionRegex = re.compile(r"^<@!?(\d+)>$")
_janeGreetingRegex = re.compile(r"\b(hi|hello)\b", re.IGNORECASE)
_americaYaRegex = re.compile(r"\bamerica\W*ya\b", re.IGNORECASE)
_halloEverynyanRegex = re.compile(r"\bhallo\W*everynyan\b", re.IGNORECASE)
_hampterRegex = re.compile(r"\bhampter\b", re.IGNORECASE)
_bumLocatorRegex = re.compile(r"\bwho(?:'s| is)\s+a\s+bum\b", re.IGNORECASE)
_eyesRegex = re.compile(r"^\s*(?::eyes:|👀)\s*$", re.IGNORECASE)
_tanabataTreeRegex = re.compile("^\\s*(?::tanabata_tree:|\U0001F38B)\\s*$", re.IGNORECASE)
_recipePromptRegex = re.compile(r"how\s+do\s+i\s+make\s+(?P<item>[^?\n\r]+)", re.IGNORECASE)
_auraRegex = re.compile(r"\b(how much aura do you have|do you have aura|what amount of aura do you have)\b", re.IGNORECASE)
_furryQueryRegex = re.compile(r"\bis\s+<@!?(\d+)>\s+a\s+furry\b", re.IGNORECASE)
_whoIsFurryRegex = re.compile(r"\bwho(?:'s| is)\s+a\s+furry\b", re.IGNORECASE)
_notAFurryRegex = re.compile(r"\bi\s*'?\s*m\s+not\s+a\s+furry\b", re.IGNORECASE)

_americaYaUserIds = {
    768228862684299265,
    442586254916452353,
    696024277673050203,
}
_auraText = "I have an infinite amount of aura."
_unknownUserId = 1086979130572165231
_hampterUserIds = {
    1086979130572165231,
}
_hampterGifUrl = "https://cdn.discordapp.com/attachments/1233638887532920875/1297368325839654943/togif.gif?ex=69ae1fe8&is=69acce68&hm=fc09e5f5f23c4fcb62019c10979ce3920de078e3aeb03528ed1853a09338707d&"

_halloEverynyanUserIds = {
    1086979130572165231,
}
_halloEverynyanGifUrl = "https://tenor.com/view/hello-cat-gif-3451114628649780924"
_horseUserIds = {
    986748357936553995,
}
_horseGifUrl = "https://tenor.com/view/horse-fat-gif-13461358657664073641"
_eyesUserIds = {
    735528587737694248,
}
_eyesGifUrl = "https://media.discordapp.net/attachments/1430279695303184415/1455423665238839420/eye-side-eye-emoji.gif?ex=69b835aa&is=69b6e42a&hm=cfa65c1d8c1102945bc69894da0233ccadf5d0edf45db05664486ff545a55bbc&="
_tanabataTreeUserIds = {
    1468150318675136634,
}
_tanabataTreeGifUrl = "https://media.discordapp.net/attachments/1374350515617665088/1462696462482935808/image.gif?ex=69e12b7c&is=69dfd9fc&hm=bc65c13dc60b72fbaf8fc8aed05b5b1b27113bd0aec2004e3f8c55de813cad57&="
_perishUserIds = {
    776897552954949683,
}
_perishGifUrl = "https://media.discordapp.net/attachments/1373420224363102208/1471997910379008231/togif.gif?ex=69eb4722&is=69e9f5a2&hm=d43a9358b8f102bd8652947b2a0c17e86b303dd1a9163e8444208dec0f0feadc&="
_stimmerUserId = 641429806382317583
_momUserId = 331660652672319488
_bumUserId = 952282215033745448
_furryUserId = 764302305368735748
_notAFurryUserIds = {_furryUserId}
_janeGreetingBlacklistedUserIds = {
    1220034130805260288,
}
_janeUserId = 1463176057422217348
_skinCooldownBypassRoleIds = normalization.normalizeIntSet(getattr(config, "skinCooldownBypassRoleIds", []))
_skinAllowedUserIds = normalization.normalizeIntSet(getattr(config, "skinAllowedUserIds", []))
_skinOneMinuteCooldownRoleIds = normalization.normalizeIntSet(
    getattr(config, "skinOneMinuteCooldownRoleIds", [1451056189625602341])
)
_skinDoubleCooldownRoleIds = normalization.normalizeIntSet(
    getattr(config, "skinDoubleCooldownRoleIds", [1432967050082385982])
)

_skinCommandNextAllowedAtByUser: dict[int, datetime] = {}
_janeGreetingNextAllowedAtByUser: dict[int, datetime] = {}
_recipeMapCache: dict[str, tuple[str, str]] | None = None
_killQuotes = [
    "{target} is being used to weigh down the reactor rods.",
    "{target} is being sent to manually inspect the turbine blades.",
    "{target} has won a trip to the spent fuel pool.",
    "{target} was sent to Turbine Hall with Turbines at 4000 RPM and climbing.",
    "{target} is being reassigned as biological shielding.",
    "{target} was locked in the control room with JRO's during a WN raid.",
    "{target} is being placed inside the control room microwave.",
    "{target} will be converted into a backup coolant system.",
]


def _normalizeText(value: str) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


async def _tryChannelSend(channel: discord.abc.Messageable, content: str) -> bool:
    return await interactionRuntime.safeChannelSend(channel, content=content) is not None


async def _tryChannelSendEmbed(
    channel: discord.abc.Messageable,
    embed: discord.Embed,
    *,
    allowedMentions: discord.AllowedMentions | None = None,
) -> bool:
    return await interactionRuntime.safeChannelSend(
        channel,
        embed=embed,
        allowedMentions=allowedMentions,
    ) is not None


async def _getMemberById(guild: discord.Guild, userId: int) -> discord.Member | None:
    member = guild.get_member(int(userId))
    if member is not None:
        return member
    try:
        return await guild.fetch_member(int(userId))
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return None


def _skinCooldownSec() -> int:
    return max(0, int(getattr(config, "skinCommandCooldownSec", 300) or 300))


def _skinCooldownSecForMember(member: discord.Member) -> int:
    cooldownSec = 60 if any(int(role.id) in _skinOneMinuteCooldownRoleIds for role in member.roles) else _skinCooldownSec()
    if any(int(role.id) in _skinDoubleCooldownRoleIds for role in member.roles):
        cooldownSec *= 2
    return cooldownSec


def _isSkinCooldownBypassed(member: discord.Member) -> bool:
    if int(member.id) == _momUserId:
        return True
    if any(int(role.id) in _skinCooldownBypassRoleIds for role in member.roles):
        return True
    return bool(member.guild_permissions.administrator)


def _janeGreetingCooldownSec() -> int:
    return max(0, int(getattr(config, "janeGreetingCooldownSec", 120) or 120))


def _pruneCooldownMap(cooldownMap: dict[int, datetime], nowUtc: datetime) -> None:
    staleUserIds = [
        userId
        for userId, nextAllowedAt in cooldownMap.items()
        if nextAllowedAt <= nowUtc
    ]
    for userId in staleUserIds:
        cooldownMap.pop(userId, None)


def _resolveRecipeFilePath() -> Path:
    return Path(__file__).with_name("recipes.json")


def _loadRecipeMap() -> dict[str, tuple[str, str]]:
    global _recipeMapCache
    if _recipeMapCache is not None:
        return _recipeMapCache

    out: dict[str, tuple[str, str]] = {}
    path = _resolveRecipeFilePath()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logging.warning("Silly recipes file not found: %s", path)
        _recipeMapCache = out
        return out
    except Exception:
        logging.exception("Failed to load silly recipes file: %s", path)
        _recipeMapCache = out
        return out

    if not isinstance(raw, dict):
        _recipeMapCache = out
        return out

    for key, recipe in raw.items():
        keyText = str(key or "").strip()
        recipeText = str(recipe or "").strip()
        if not keyText or not recipeText:
            continue
        out[_normalizeText(keyText)] = (keyText, recipeText)
    _recipeMapCache = out
    return out


def _resolveRecipe(queryText: str) -> tuple[str, str] | None:
    queryNorm = _normalizeText(queryText)
    if not queryNorm:
        return None

    recipes = _loadRecipeMap()
    exact = recipes.get(queryNorm)
    if exact is not None:
        return exact

    containsMatches: list[tuple[int, tuple[str, str]]] = []
    for keyNorm, value in recipes.items():
        if keyNorm in queryNorm or queryNorm in keyNorm:
            containsMatches.append((abs(len(keyNorm) - len(queryNorm)), value))
    if not containsMatches:
        return None
    containsMatches.sort(key=lambda item: item[0])
    return containsMatches[0][1]


def _isAmericaYaTrigger(content: str) -> bool:
    raw = str(content or "")
    if _americaYaRegex.search(raw):
        return True
    return "americaya" in _normalizeText(raw)



def _isHalloEverynyanTrigger(content: str) -> bool:
    raw = str(content or "")
    if _halloEverynyanRegex.search(raw):
        return True
    return "halloeverynyan" in _normalizeText(raw)

def _isAuraTrigger(content: str) -> bool:
    raw = str(content or "")
    if _auraRegex.search(raw):
        return True
    return "have aura" in _normalizeText(raw)

def _isHampterTrigger(content: str) -> bool:
    raw = str(content or "")
    if _hampterRegex.search(raw):
        return True
    return "hampter" in _normalizeText(raw)


def _isBumLocatorTrigger(content: str) -> bool:
    raw = str(content or "")
    if _bumLocatorRegex.search(raw):
        return True
    normalized = _normalizeText(raw)
    return "whosabum" in normalized or "whoisabum" in normalized

def _isHorseTrigger(content: str) -> bool:
    return _normalizeText(str(content or "")) == "horse"


def _isEyesTrigger(content: str) -> bool:
    return bool(_eyesRegex.match(str(content or "")))


def _isTanabataTreeTrigger(content: str) -> bool:
    return bool(_tanabataTreeRegex.match(str(content or "")))


def _isPerishTrigger(content: str) -> bool:
    return _normalizeText(str(content or "")) == "perish"


def _isNotAFurryTrigger(content: str) -> bool:
    return bool(_notAFurryRegex.search(str(content or "")))


_DirectSillyResponse = tuple[set[int], Callable[[str], bool], str]
_directSillyResponses: tuple[_DirectSillyResponse, ...] = (
    (_americaYaUserIds, _isAmericaYaTrigger, "Hallo!"),
    (_halloEverynyanUserIds, _isHalloEverynyanTrigger, _halloEverynyanGifUrl),
    (_horseUserIds, _isHorseTrigger, _horseGifUrl),
    (_eyesUserIds, _isEyesTrigger, _eyesGifUrl),
    (_tanabataTreeUserIds, _isTanabataTreeTrigger, _tanabataTreeGifUrl),
    (_perishUserIds, _isPerishTrigger, _perishGifUrl),
    (_notAFurryUserIds, _isNotAFurryTrigger, "yeah sure"),
)


async def _maybeSendDirectSillyResponse(message: discord.Message, content: str) -> bool:
    authorId = int(getattr(message.author, "id", 0) or 0)
    for userIds, predicate, response in _directSillyResponses:
        if authorId in userIds and predicate(content):
            return await _tryChannelSend(message.channel, response)
    return False


async def _resolveMemberFromQuery(guild: discord.Guild, query: str) -> discord.Member | None:
    value = (query or "").strip()
    if not value:
        return None

    mentionMatch = _skinMentionRegex.match(value)
    if mentionMatch:
        userId = int(mentionMatch.group(1))
        return await _getMemberById(guild, userId)

    if value.isdigit():
        userId = int(value)
        return await _getMemberById(guild, userId)

    lowered = value.lower()
    if lowered.startswith("@"):
        lowered = lowered[1:].strip()

    exactMatches = [
        member
        for member in guild.members
        if member.display_name.lower() == lowered or member.name.lower() == lowered
    ]
    if len(exactMatches) == 1:
        return exactMatches[0]

    startsWithMatches = [
        member
        for member in guild.members
        if member.display_name.lower().startswith(lowered) or member.name.lower().startswith(lowered)
    ]
    if len(startsWithMatches) == 1:
        return startsWithMatches[0]
    return None


async def sendSkinEmbed(channel, botClient: discord.Client, embed: discord.Embed) -> bool:
    if getattr(channel, "guild", None) is not None and botClient.user:
        sentMessage = await runtimeWebhooks.sendOwnedWebhookMessageDetailed(
            botClient=botClient,
            channel=channel,
            webhookName="Jane Skinner",
            embed=embed,
            username="Jane Skinner",
            avatarUrl=botClient.user.display_avatar.url,
            reason="Skin command output",
        )
        if sentMessage is not None:
            return True
    return await _tryChannelSendEmbed(channel, embed)


async def sendKillEmbed(channel, botClient: discord.Client, embed: discord.Embed) -> bool:
    if getattr(channel, "guild", None) is not None and botClient.user:
        sentMessage = await runtimeWebhooks.sendOwnedWebhookMessageDetailed(
            botClient=botClient,
            channel=channel,
            webhookName="Jane Clanker",
            embed=embed,
            username=str(botClient.user.display_name or botClient.user.name or "Jane Clanker"),
            avatarUrl=botClient.user.display_avatar.url,
            reason="Kill command output",
        )
        if sentMessage is not None:
            return True
    return await _tryChannelSendEmbed(
        channel,
        embed,
        allowedMentions=discord.AllowedMentions(users=False, roles=False, everyone=False),
    )


@dataclass(frozen=True, slots=True)
class SillyOutcome:
    """What a silly command wants said: plain text (refusal/joke) or an embed (success)."""

    text: str | None = None
    embed: discord.Embed | None = None
    ephemeral: bool = False


def _botMentionRegex(botUserId: int) -> re.Pattern[str]:
    return re.compile(rf"<@!?{int(botUserId)}>")


def stripBotMention(content: str, botUserId: int) -> str:
    return _botMentionRegex(botUserId).sub("", str(content or "")).strip()


def parseMentionCommand(content: str, botUserId: int) -> tuple[str, str]:
    """Return (word, rest) for '@Jane word rest'; ('', '') if Jane's mention is not first."""
    stripped = str(content or "").strip()
    match = _botMentionRegex(botUserId).match(stripped)
    if match is None:
        return "", ""
    word, rest = normalization.commandParts(stripped[match.end():])
    return word.lstrip("!"), rest


async def _referencedAuthorId(message: discord.Message) -> int:
    reference = getattr(message, "reference", None)
    if reference is None:
        return 0
    resolved = getattr(reference, "resolved", None)
    if isinstance(resolved, discord.Message):
        return int(getattr(getattr(resolved, "author", None), "id", 0) or 0)
    messageId = int(getattr(reference, "message_id", 0) or 0)
    if messageId <= 0:
        return 0
    try:
        referencedMessage = await message.channel.fetch_message(messageId)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException, AttributeError):
        return 0
    return int(getattr(getattr(referencedMessage, "author", None), "id", 0) or 0)


async def resolveTextTarget(
    message: discord.Message,
    rest: str,
    *,
    botUserId: int,
) -> discord.Member | None:
    if not message.guild:
        return None
    query = str(rest or "").strip()
    if query:
        try:
            return await _resolveMemberFromQuery(message.guild, query)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None
    # Jane is always mentioned by the trigger itself, so she is never the target.
    for mentioned in list(message.mentions):
        mentionedId = int(getattr(mentioned, "id", 0) or 0)
        if mentionedId == int(botUserId):
            continue
        member = message.guild.get_member(mentionedId)
        if member is not None:
            return member
    authorId = await _referencedAuthorId(message)
    if authorId <= 0 or authorId == int(botUserId):
        return None
    return await _getMemberById(message.guild, authorId)


def buildKillOutcome(actor: discord.Member, target: discord.Member | None) -> SillyOutcome:
    if not runtimePermissions.hasMiddleHighRankRole(actor):
        return SillyOutcome(text="Only MR/HR roles can use kill.", ephemeral=True)
    if target is None:
        return SillyOutcome(text="Usage: `/kill` or `@Jane kill @user`", ephemeral=True)

    nowUtc = datetime.now(timezone.utc)
    scheduledAt = nowUtc + timedelta(seconds=random.randint(60, 3600))
    timestampText = f"<t:{int(scheduledAt.timestamp())}:R>"
    quote = random.choice(_killQuotes).format(target=target.mention)
    embed = discord.Embed(
        title="Execution Scheduled",
        description=f"**{quote}**\n\n-# {target.mention}'s execution takes place {timestampText}.",
        color=discord.Color.orange(),
        timestamp=nowUtc,
    )
    embed.set_footer(text=f"Command used by {actor.display_name}")
    return SillyOutcome(embed=embed)


def buildSkinOutcome(
    actor: discord.Member,
    target: discord.Member | None,
    *,
    hasSkinPermission: Callable[[discord.Member], bool],
) -> SillyOutcome:
    if int(actor.id) not in _skinAllowedUserIds and not hasSkinPermission(actor):
        return SillyOutcome(text="You do not have permission to skin users.", ephemeral=True)

    if not _isSkinCooldownBypassed(actor):
        nowUtc = datetime.now(timezone.utc)
        _pruneCooldownMap(_skinCommandNextAllowedAtByUser, nowUtc)
        nextAllowedAt = _skinCommandNextAllowedAtByUser.get(int(actor.id))
        if nextAllowedAt and nextAllowedAt > nowUtc:
            remainingSec = max(1, int((nextAllowedAt - nowUtc).total_seconds()))
            mins, secs = divmod(remainingSec, 60)
            waitText = f"{mins}m {secs:02d}s" if mins > 0 else f"{secs}s"
            return SillyOutcome(text=f"You're on cooldown for skin. Try again in {waitText}.", ephemeral=True)
        cooldownSec = _skinCooldownSecForMember(actor)
        if cooldownSec > 0:
            _skinCommandNextAllowedAtByUser[int(actor.id)] = nowUtc + timedelta(seconds=cooldownSec)

    if target is None:
        return SillyOutcome(text="Usage: `/skin` or `@Jane skin username`", ephemeral=True)
    if bool(getattr(target, "bot", False)) or bool(getattr(target, "system", False)):
        return SillyOutcome(text="I can't skin bots or app accounts.")
    if int(target.id) == _janeUserId:
        return SillyOutcome(text="why would i skin myself????")
    if int(target.id) == _momUserId:
        return SillyOutcome(text="That's my mom, dude :woman_standing:")
    if int(target.id) == _unknownUserId:
        return SillyOutcome(text="Sorry, but no. Get skinned. heh.")

    jokes = [
        f"{target.mention} has been skinned. What a bum.",
        f"{target.mention} got skinned by {actor.mention}. Tragic.",
        f"Skinning complete: {target.mention} has entered the leather era.",
        f"{target.mention} has been skinned. Somebody alert ANRO dermatology.",
        f"{actor.mention} has claimed another victim, {target.mention} will never recover :pensive:",
    ]
    embed = discord.Embed(
        title="Skinning has been completed",
        description=f"\n\n{random.choice(jokes)}",
        color=discord.Color.orange(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text=f"Command used by {actor.display_name}")
    return SillyOutcome(embed=embed)


def _isGuildMember(author: object) -> bool:
    return isinstance(author, discord.Member)


async def handleKillMention(message: discord.Message, botClient: discord.Client, rest: str) -> None:
    if not message.guild or botClient.user is None or not _isGuildMember(message.author):
        return
    target = await resolveTextTarget(message, rest, botUserId=int(botClient.user.id))
    outcome = buildKillOutcome(message.author, target)
    if outcome.embed is not None:
        await sendKillEmbed(message.channel, botClient, outcome.embed)
    elif outcome.text:
        await _tryChannelSend(message.channel, outcome.text)


async def handleSkinMention(
    message: discord.Message,
    botClient: discord.Client,
    rest: str,
    *,
    hasSkinPermission: Callable[[discord.Member], bool],
) -> None:
    if not message.guild or botClient.user is None or not _isGuildMember(message.author):
        return
    target = await resolveTextTarget(message, rest, botUserId=int(botClient.user.id))
    outcome = buildSkinOutcome(message.author, target, hasSkinPermission=hasSkinPermission)
    if outcome.embed is not None:
        await sendSkinEmbed(message.channel, botClient, outcome.embed)
    elif outcome.text:
        await _tryChannelSend(message.channel, outcome.text)


async def maybeHandleSillyMentions(message: discord.Message, botClient: discord.Client) -> None:
    if message.author.bot:
        return
    botUser = botClient.user
    if botUser is None:
        return

    # Without the Message Content intent Discord only delivers text for
    # messages that mention Jane, so every reply below is mention-gated.
    isMentioningJane = any(int(user.id) == int(botUser.id) for user in message.mentions)
    if not isMentioningJane:
        return

    content = str(message.content or "")
    if await _maybeSendDirectSillyResponse(message, stripBotMention(content, int(botUser.id))):
        return

    #hampter
    if int(message.author.id) in _hampterUserIds and _isHampterTrigger(content):
        if await _tryChannelSend(message.channel, _hampterGifUrl):
            await interactionRuntime.safeMessageDelete(message)
        return

    furryQueryMatch = _furryQueryRegex.search(content)
    if furryQueryMatch:
        queriedUserId = int(furryQueryMatch.group(1))
        reply = "Yes" if queriedUserId == _furryUserId else "unknown"
        await _tryChannelSend(message.channel, reply)
        return

    if _whoIsFurryRegex.search(content):
        await interactionRuntime.safeChannelSend(
            message.channel,
            content=f"<@{_furryUserId}> is a furry",
            allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False),
        )
        return

    if _isBumLocatorTrigger(content):
        locatingMessage = await interactionRuntime.safeChannelSend(
            message.channel,
            content=":compass: Locating...",
        )
        if locatingMessage is None:
            return

        async def _revealBum() -> None:
            await asyncio.sleep(5)
            await interactionRuntime.safeMessageEdit(
                locatingMessage,
                content=f"Bum located! <@{_bumUserId}>",
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )

        asyncio.create_task(_revealBum())
        return

    if cookieCog.cookieRequestRegex.search(content):
        await interactionRuntime.safeChannelSend(
            message.channel,
            content=cookieCog.buildCookieMessage(message.author.mention),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return

    if _isAuraTrigger(content):
        await _tryChannelSend(message.channel, _auraText)
        return

    recipeMatch = _recipePromptRegex.search(content)
    if recipeMatch:
        requestedItem = str(recipeMatch.group("item") or "").strip()
        resolved = _resolveRecipe(requestedItem)
        if resolved is None:
            await _tryChannelSend(
                message.channel,
                "I don't have that recipe yet. Try one from my recipe list.",
            )
            return
        recipeName, recipeText = resolved
        await _tryChannelSend(message.channel, f"**{recipeName.title()} Recipe**\n{recipeText}")
        return

    if not _janeGreetingRegex.search(content):
        return

    if int(message.author.id) in _janeGreetingBlacklistedUserIds:
        return

    nowUtc = datetime.now(timezone.utc)
    _pruneCooldownMap(_janeGreetingNextAllowedAtByUser, nowUtc)
    nextAllowedAt = _janeGreetingNextAllowedAtByUser.get(int(message.author.id))
    if nextAllowedAt and nextAllowedAt > nowUtc:
        return
    cooldownSec = _janeGreetingCooldownSec()
    if cooldownSec > 0:
        _janeGreetingNextAllowedAtByUser[int(message.author.id)] = nowUtc + timedelta(seconds=cooldownSec)

    if int(message.author.id) == _momUserId:
        responseText = "Hi mom!"
    elif int(message.author.id) == _stimmerUserId:
        responseText = "Hey cash! Good to see you :D"
    else:
        responseText = f"Hi {message.author.mention}!"
    await _tryChannelSend(message.channel, responseText)
