from __future__ import annotations

from .core import *
from .staff import *
from .operations import *
from .env import _envFlag, _envInt, _envText

# == Recruitment / ANRORS ==
recruitmentChannelId = 0
recruitmentTimeLogReviewChannelId = 0
recruitmentPatrolReviewChannelId = 0
recruitmentPatrolEvidenceChannelId = 0
recruitmentCommandGuildIds = []
recruitmentSourceGuildId = serverId

# Optional: roles allowed to host /recruitment-patrol.
# If empty, this falls back to recruiterRoleId permission.
recruitmentPatrolGroupHostRoleIds = []

recruitmentPointsBase = 2
recruitmentPointsOrientationBonus = 3
recruitmentPointsPer15Minutes = 1
recruitmentGroupPatrolPoints = 5
recruitmentPatrolMaxDurationMinutes = 240
recruitmentAutoDetectOrientation = True
recruitmentDivisionKeyAliases = ["recruitment", "anrors"]

# New member defaults.
recruitmentMembersRankOrder = [
    "Recruitment Supervisor",
    "Lead Recruiter",
    "Senior Recruiter",
    "Recruiter",
]
recruitmentNewMemberRank = "Recruiter"
recruitmentNewMemberQuota = 0
recruitmentNewMemberStatus = "Active"
recruitmentManagerQuotaPatrols = 4
recruitmentEmployeeQuotaPoints = 8

# Optional hardcoded cosmetic/footer row. Keep 0 to auto-detect the last non-empty row.
recruitmentFooterRow = 0

# Recruitment ORBAT ranks / sections.
recruitmentAllowedRanks = [
    "Commissioner 1 IC",
    "Comissioner 1 IC",
    "Head Recruiter 1 IC",
    "Head Recruiter 2 IC",
    "Head Recruiter 3 IC",
    "Head Recruiter 4 IC",
    "Recruitment Manager",
    "Recruitment Supervisor",
    "Lead Recruiter",
    "Senior Recruiter",
    "Recruiter",
]

recruitmentSectionHeaders = [
    "High Command",
    "Managers",
    "Employees",
    "Quota not enforced (After 10th)",
    "Dept. Lead",
    "Members",
]

# Section header used for regular recruiter rows.
recruitmentMembersSectionHeaderCandidates = ["Employees", "Members"]

# Quota status values for ANRORS quota column (F).
recruitmentQuotaStatusValues = [
    "Completed",
    "Incomplete",
    "Excused",
    "Failed",
    "Exempt",
]


# == ANRD Payments ==
anrdPaymentReviewChannelId = 0

anrdPaymentSubmitterRoleIds = []
anrdPaymentReviewerRoleIds = []

# ANRD payment manager mapping (ANRD tab, lower section).
anrdMembersStartRow = 3
anrdMembersEndRow = 49
anrdPaymentManagerStartRow = 52
anrdPaymentManagerScanEndRow = 260
anrdDeveloperMonthlyCap = 1200
anrdContributorMonthlyCap = 2000
anrdDeveloperEligibleRanks = [
    "Development Project Lead",
    "Senior Developer",
    "Developer",
]
anrdDeveloperUnlimitedRanks = [
    "Senior Developer",
]
anrdContributorRanks = [
    "Contributer",
    "Contributor",
    "Probationary",
]


# == Ribbon System ==
ribbonRulesPath = "configData/ribbons.json"
ribbonReviewChannelId = 0
ribbonApprovedOutputChannelId = 0

ribbonManagerRoleIds = []
ribbonRequestPingRoleIds = []


# == Best Of ==
bestOfCommandRoleIds = []
bestOfRobloxLookupEnabled = True
bestOfRobloxLookupConcurrency = 8
bestOfRobloxLookupTimeoutSec = 3.0
# Best Of role priority (lowest -> highest):
# Former MR -> MR -> Former HR -> HR -> Former ANROCOM -> Command Staff -> ANROCOM
bestOfFormerMrRoleId = 0
bestOfMrRoleId = middleRankRoleId
bestOfFormerHrRoleId = 0
bestOfHrRoleId = highRankRoleId
bestOfFormerAnrocomRoleId = 0
bestOfFormerAnrocomRoleIds = []
bestOfCommandStaffRoleId = commandStaffRoleId
bestOfAnrocomRoleIds = []


# == Hall of Fame / Shame ==
hallOfFameChannelId = 0
hallOfShameChannelId = 0
hallReactionThreshold = 5
hallUseWebhook = True
hallIgnoreBotMessages = True
hallAllowedCategoryIds = []


# == Cohost ==
cohostAllowedRoleIds = []
cohostSupervisorRoleId = 0
cohostSupervisorRoleName = "supervisor eligible"
cohostSroRoleId = 0
cohostStaRoleId = 0
cohostSlotsSolo = 2
cohostSlotsEmergency = 2
cohostSlotsTurbine = 2
cohostSlotsGrid = 4
cohostSlotsShift = 2


# == Voice Chat ==
_canCreateVoiceChatAll = [
    1376949919100698814,
    1376949984750206986,
    1383522027251699772,
    1470914397424717825,
]

_canCreateVoiceChatBasic = [
    1376949919100698814,
    1376949984750206986,
    1383522027251699772,
    1456604223407001601,
]

voiceChannelCreationCategory = 1481169790990029002
voiceChatRebalanceMaxPositionEdits = 4
voiceChatRebalanceEditDelaySec = 1.25
permanentVoiceChatChannelIds = []


# == Roblox / Identity ==
robloxGroupId = 0
robloxGroupUrl = "https://www.roblox.com/communities/36000077/ANRO-Advanced-Noobic-Reactor-Operations#!/about"

# Jane Identity links Discord users to Roblox accounts through a Discord-started
# Roblox OAuth flow. The web server only handles the OAuth callback.
janeIdentityEnabled = True
janeIdentityWebEnabled = _envFlag("JANE_IDENTITY_WEB_ENABLED", False)
janeIdentityWebHost = _envText("JANE_IDENTITY_WEB_HOST", "127.0.0.1")
janeIdentityWebPort = _envInt("JANE_IDENTITY_WEB_PORT", 8791)
janeIdentityPublicBaseUrl = _envText("JANE_IDENTITY_PUBLIC_BASE_URL")
janeIdentityRedirectPath = "/identity/roblox/callback"
janeIdentityRedirectUri = _envText("JANE_IDENTITY_REDIRECT_URI")
janeIdentityApiToken = _envText("JANE_IDENTITY_API_TOKEN")
janeIdentityLinkTtlSec = 600
janeIdentityRelayEnabled = _envFlag("JANE_IDENTITY_RELAY_ENABLED", False)
janeIdentityRelayApiBaseUrl = _envText("JANE_IDENTITY_RELAY_API_BASE_URL")
janeIdentityRelayApiToken = _envText("JANE_IDENTITY_RELAY_API_TOKEN")
janeIdentityRelayPollIntervalSec = 5
janeIdentityRelayBatchSize = 10
janeIdentityPreferInternalLinks = True
janeIdentityUpdateNickname = True
janeIdentityVerifiedRoleIds = []
janeIdentityUnverifiedRoleIds = []
janeIdentityGroupRoleRules = []
janeIdentityAutoApplyOnJoin = True
janeIdentityBulkUpdateFetchMembers = True
janeIdentityBulkUpdateMaxMembers = 5000
janeIdentityBulkUpdateDelaySec = 0.20
janeIdentityScheduledRefreshEnabled = True
janeIdentityScheduledRefreshShardCount = 7
janeIdentityScheduledRefreshHourCentral = 0
janeIdentityScheduledRefreshMinuteCentral = 0
janeIdentityScheduledRefreshFetchMembers = False
janeIdentityScheduledRefreshMaxMembers = 5000
janeIdentityScheduledRefreshDelaySec = 0.50
janeIdentityScheduledRefreshPauseWhenBusy = True
janeIdentityScheduledRefreshBusyPollSec = 30
robloxOAuthClientId = _envText("ROBLOX_OAUTH_CLIENT_ID")
robloxOAuthClientSecret = _envText("ROBLOX_OAUTH_CLIENT_SECRET")

# RoVer lookup (Discord -> Roblox). Uses the official RoVer API.
roverApiBaseUrl = "https://registry.rover.link/api/guilds/{guildId}/discord-to-roblox/{discordId}"
roverApiKeyHeader = "Authorization"
roverApiKeyUseBearer = True
roverVerifyUrl = "https://rover.link/verify"
roverCacheTtlSec = 120
roverCacheMaxEntries = 2000
roverIdentityDbTimeoutSec = 1.5
robloxHttpTimeoutSec = 10
robloxGroupRolesCacheTtlSec = 3600
recruitmentRoverLookupConcurrency = 8


# == Roblox Flagging / Scanning ==
# If a passing attendee is in any of these groups, they are marked FLAGGED.
robloxFlagGroupIds = []

# Flag Roblox accounts younger than this many days (0 to disable).
robloxAccountAgeFlagDays = 100

# Group scan cache.
robloxGroupScanCacheDays = 7

# Badge scan.
robloxBadgeScanEnabled = True
robloxBadgeScanCacheDays = 7
robloxBadgeScanBatchSize = 100
robloxBadgeImportMax = 200

# Outfit viewer.
robloxOutfitScanEnabled = True
robloxOutfitScanCacheDays = 7
robloxOutfitMax = 0
robloxOutfitMaxPages = 20
robloxOutfitThumbSize = "420x420"

# Inventory scanning.
robloxInventoryScanEnabled = True
robloxInventoryScanCacheDays = 7
robloxInventoryScanMaxPages = 5


# == Gambling API ==
# Use 0.0.0.0 to allow external callers via token auth.
gamblingApiEnabled = True
gamblingApiHost = "0.0.0.0"
gamblingApiPort = 8787
gamblingApiMaxConcurrency = 8
gamblingPointsToDollarRate = 5  # 1 point => 5 anrobucks


# == Hidden / Misc ==
skinAllowedUserIds = []
skinCooldownBypassRoleIds = []


# == Organization Profiles ==
# Jane is still a single bot process, but org-specific settings now live behind
# profile keys so other groups can be added without turning config.py into a
# bigger singleton mess than it already is.
defaultOrganizationKey = "ANRO"
organizationCommandFeatureMap = {
    "orientation": "anro-sessions",
    "bg-add": "anro-bgc",
    "bgcheck": "anro-bgc",
    "bg-intel": "anro-bgc",
    "trainingstats": "anro-training-logs",
    "hoststats": "anro-training-logs",
    "mirrortraininghistory": "anro-training-logs",
    "bgleaderboard": "anro-bgc",
    "bg-leaderboard": "anro-bgc",
    "recruitment": "anro-recruitment",
    "recruitment-time-log": "anro-recruitment",
    "recruitment-patrol": "anro-recruitment",
    "orbat": "anro-orbat",
    "orbat-request": "anro-orbat",
    "orbat-pending": "anro-orbat",
    "loa-request": "anro-orbat",
}
_anroOrganizationGuildIds = sorted(
    {
        int(guildId)
        for guildId in (
            list(allowedCommandGuildIds)
            + [
                serverId,
                serverIdTesting,
                bgCheckAdultReviewGuildId,
                bgCheckMinorReviewGuildId,
            ]
        )
        if int(guildId) > 0
    }
)
organizationProfiles = {
    "ANRO": {
        "label": "ANRO",
        "primaryGuildId": serverId,
        "guildIds": list(_anroOrganizationGuildIds),
        "enabledFeatures": [
            "anro-sessions",
            "anro-bgc",
            "anro-training-logs",
            "anro-recruitment",
            "anro-orbat",
        ],
        "trainingResultsChannelId": trainingResultsChannelId,
        "trainingArchiveChannelId": trainingArchiveChannelId,
        "trainingLogBackfillDays": trainingLogBackfillDays,
        "trainingLogStartupMirrorNewRows": trainingLogStartupMirrorNewRows,
        "trainingSummaryWebhookName": trainingSummaryWebhookName,
        "trainingMirrorWebhookName": trainingMirrorWebhookName,
        "startupGreetingChannelId": startupGreetingChannelId,
        "bgCheckChannelId": bgCheckChannelId,
        "bgCheckAdultReviewGuildId": bgCheckAdultReviewGuildId,
        "bgCheckAdultReviewChannelId": bgCheckAdultReviewChannelId,
        "bgCheckMinorReviewGuildId": bgCheckMinorReviewGuildId,
        "bgCheckMinorReviewChannelId": bgCheckMinorReviewChannelId,
        "bgCheckMinorReviewRoleId": bgCheckMinorReviewRoleId,
        "bgCheckMinorReviewRoleIds": list(bgCheckMinorReviewRoleIds),
        "bgCheckSourceGuildId": bgCheckSourceGuildId,
        "bgCheckSpreadsheetTemplateId": bgCheckSpreadsheetTemplateId,
        "bgCheckSpreadsheetFolderId": bgCheckSpreadsheetFolderId,
        "bgCheckSpreadsheetSheetName": bgCheckSpreadsheetSheetName,
        "bgMinorAgeRoleIds": list(bgMinorAgeRoleIds),
        "bgMajorAgeRoleIds": list(bgMajorAgeRoleIds),
        "bgMinorAgeGroups": list(bgMinorAgeGroups),
        "bgAdultAgeGroups": list(bgAdultAgeGroups),
        "bgUnknownDefaultsToMinor": bool(bgUnknownDefaultsToMinor),
        "moderatorRoleId": moderatorRoleId,
        "bgReviewModeratorRoleId": bgReviewModeratorRoleId,
        "newApplicantRoleId": newApplicantRoleId,
        "pendingBgRoleId": pendingBgRoleId,
        "robloxGroupId": robloxGroupId,
        "robloxGroupUrl": robloxGroupUrl,
    },
}
guildOrganizationKeys = {
    int(guildId): defaultOrganizationKey
    for guildId in list(_anroOrganizationGuildIds)
    if int(guildId) > 0
}

minecraftAuthenticationToken = _envText("MINECRAFT_RCON_TOKEN")
minecraftRCONAddress = "unease-year.with.playit.plus"
minecraftRCONPort = 1395
minecraftRCONTimeoutSeconds = 5
minecraftServerMaxPlayersFallback = 60
minecraftAllowedRoleIds = []
minecraftCheckCooldownSeconds = 30
