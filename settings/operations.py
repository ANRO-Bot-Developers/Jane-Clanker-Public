from __future__ import annotations

from .core import *
from .staff import *
from .env import _envText

# == Internal Link Hub ==
masterLinkHubManagerRoleIds = []
masterLinkHubWebhookName = "Jane Master Directory"
masterLinkHubWebhookAvatarUrl = "https://cdn.discordapp.com/icons/1460113139663114252/1d6aee94fe2c64ac5449eb8d10462610.webp?size=1024"


# == Public Utility / Suggestions ==
welcomeChannelId = 0
welcomeMessageTemplate = "Welcome to **{guild}**, {mention}."
publicRoleMenus = {}
reactionRoleCommandRoleIds = []
reactionRolePolicyRoleIds = []

suggestionChannelId = 0
suggestionForumChannelId = 0
suggestionReviewerRoleIds = []

# Optional Freedcamp task creation when suggestions are approved.
freedcampProjectId = 0
freedcampTaskGroupId = 0


# == Server Safety / Recovery ==
serverSafetySnapshotDir = ""
serverSafetyOffsiteSnapshotDir = ""
serverSafetyOffsiteSnapshotsEnabled = True
serverSafetyWeeklySnapshotKeepCount = 2
serverSafetyManualSnapshotKeepCount = 1
serverSafetyWeeklySnapshotGuildIds = []
serverSafetyIgnoredCategoryIds = []
serverSafetyPreservedChannelIds = []
serverSafetyAllowedUserIds = []


# == Project Workflow ==
# Empty means /project can be used in any guild already allowed above.
projectCommandGuildIds = []
projectAutoCreateThread = True
projectHodRoleIds = []
projectAssistantDirectorRoleIds = []


# == Division Applications ==
divisionApplicationsConfigPath = "configData/divisions.json"
divisionApplicationsCooldownMinutes = 30
divisionApplicationsMaxActivePerDivision = 100

# Optional mapping for app division keys -> Department ORBAT division keys.
divisionOrbatSeedKeyMap = {
    "LO": "LO",
    "LOGISTICS": "LO",
    "LORE": "ANLD",
    "NIRI": "NIRI",
    "A&A": "A&A",
    "AA": "A&A",
    "MSB": "MSB",
}

# Optional mapping for app division keys -> initial ORBAT rank when first seeded.
divisionOrbatSeedStartRankMap = {
    "NIRI": "Researcher",
}

divisionApplicationsAdminRoleIds = []
divisionApplicationsGlobalReviewerRoleIds = []

# Roles allowed to use `!applications <divisionKey> <open|close|status>`.
divisionApplicationsControlRoleIds = []


# == Division Clock-in ==
# If empty, administrators/manage-server can still start sessions.
divisionClockinAllowedRoleIds = []

# Subdepartment -> sheet mapping placeholder for future wiring.
# Example:
# "anrd": {"sheetKey": "dept_anrd", "sheetName": "ANRD"}


# == ORBAT / LOA ==
# Review / audit channels.
orbatReviewChannelId = 0
loaReviewChannelId = 0
orbatAuditChannelId = 0

# Master switch for non-recruitment ORBAT writes.
nonRecruitmentOrbatWritesEnabled = False

# John event-log counter automation. Kept separate from the broader ORBAT
# write switch so event counters can run without enabling unrelated row writes.
johnEventOrbatWritesEnabled = False

# General staff ORBAT workbook.
orbatSpreadsheetId = ""
orbatSheetName = "General Staff"

# Recruitment / department ORBAT workbooks.
recruitmentSpreadsheetId = ""
deptSpreadsheetId = ""

# Department ORBAT layouts live in a separate JSON file.
departmentOrbatLayoutsPath = "features/staff/departmentOrbat/layouts.json"

# ORBAT submit / review access.
orbatSubmitterRoleIds = []
orbatReviewerRoleIds = []
orbatWriteGuildIds = []

# LOA roles (apply based on the submitter's rank role).
orbatLoaRoleMap = {
    middleRankRoleId: 1460770067909185657,  # Middle Rank -> Leave Of Absence [MR]
    highRankRoleId: 1460770421325693116,  # High Rank -> Leave Of Absence [HR]
    # ANROCOM role id -> 1470838628065214534
}

# ORBAT columns (A1 notation).
# The current General Staff workbook has no Discord ID or mic column. Keep
# those blank so Jane does not treat a nearby PMD column as a writable field.
orbatColumnDiscordId = 0
orbatColumnRobloxUser = "B"
orbatColumnRank = "C"
orbatColumnClearance = "D"
orbatColumnStatus = "E"
orbatColumnDepartment = "F"
orbatColumnLoaInfo = "G"
orbatColumnNotes = "H"
orbatColumnMic = ""
orbatColumnShifts = "I"
orbatColumnOtherEvents = "J"
orbatColumnTotal = "K"
orbatColumnAllTimeShifts = "L"
orbatColumnAllTime = "M"
orbatColumnTimezone = "N"
orbatColumnAgeGroup = "O"
orbatColumnStrikes = "P"

# Role mappings.
orbatRoleRankMap = {
    cnoRoleId: "J - Chief Nuclear Officer",
    dooRoleId: "I - Director of Operations",
    ddooRoleId: "H - Deputy DoO",
    sectionChiefRoleId: "G - Section Chief",
    commandStaffRoleId: "F - Command Staff",
    foiRoleId: "E - Field Operations Inspector",
    crsRoleId: "D - Control Room Supervisor",
    shiftSupervisorRoleId: "C - Shift Supervisor",
    juniorSuRoleId: "B - Junior SU",
}

orbatRoleClearanceMap = {
    cnoRoleId: "1IC",  # Chief Nuclear Officer
    dooRoleId: "2IC",  # Director of Operations
    ddooRoleId: "3IC",  # Deputy DoO
    sectionChiefRoleId: "ADMINISTRATIVE",  # Section Chief
    msbRoleId: "MODERATION",  # Moderation Services Bureau
}

# Priority order (highest rank wins).
orbatRolePriority = [
    cnoRoleId,
    dooRoleId,
    ddooRoleId,
    sectionChiefRoleId,
    commandStaffRoleId,
    foiRoleId,
    crsRoleId,
    shiftSupervisorRoleId,
    juniorSuRoleId,
]

# Allowed dropdown values (must match sheet validation lists).
orbatAllowedRanks = [
    "J - Chief Nuclear Officer",
    "I - Director of Operations",
    "H - Deputy DoO",
    "G - Section Chief",
    "F - Command Staff",
    "E - Field Operations Inspector",
    "D - Control Room Supervisor",
    "C - Shift Supervisor",
    "B - Junior SU",
    "A - Retired",
    "0 - Decommisioned",
]

orbatAllowedClearances = [
    "1IC",
    "2IC",
    "3IC",
    "4IC",
    "ANROCOM",
    "ADMINISTRATIVE",
    "MODERATION",
    "NILL",
]

orbatAllowedStatuses = [
    "Active",
    "Inactive",
    "LoA",
    "Retired",
    "Decommisioned",
    "N/A",
    "?",
]

orbatAllowedDepartments = [
    "ANROCOM",
    "INTERNAL AFFAIRS & HR",
    "RECORDS & COMPLIENCE",
    "TRAINING & QUALIFICATION",
    "LOGISTIC & OPERATIONS (LO)",
    "COMMUNITY ENGAGEMENT",
    "GENERAL ADMINISTRATION",
    "MIDDLE RANK MANAGEMENT",
    "MIDDLE RANK OVERSIGHT",
    "MODERATION SERVICES BUREAU",
    "ANROCOM SECRETARY",
    "AUDIT & ASSURANCE (A&A)",
    "ANRO DEVELOPMENT",
    "NILL",
]

orbatAllowedMic = ["Yes", "No", "?"]
orbatAllowedAgeGroups = ["21+", "18-20", "16-17", "13-15", "NO INFO"]
orbatDefaultMic = "?"
orbatDefaultAgeGroup = "NO INFO"

# ORBAT row styling.
orbatBandingPrimaryHex = "#f3f3f3"
orbatBandingSecondaryHex = "#d9d9d9"
orbatRowFontSize = 13
orbatRowBold = True

# Weekly ORBAT organization schedule (UTC). Weekday: Monday=0 ... Sunday=6.
orbatOrganizationUtcHour = 3
orbatOrganizationUtcMinute = 0
orbatOrganizationUtcWeekday = 6

# Role-based ORBAT sync runtime controls.
roleOrbatSyncEnabled = True
roleOrbatSyncMinIntervalSec = 600
roleOrbatSyncMappings = [
    {
        "syncType": "recruitment.anrorsPlacement",
        "enabled": True,
        "memberRoleId": anrorsMemberRoleId,
        "rmPlusRoleId": anrorsRmPlusRoleId,
        "requireAnyRole": True,
        "organizeAfter": True,
    },
    {
        "syncType": "department.anrdRankByRole",
        "enabled": True,
        "divisionKey": "ANRD",
        "roleRankMap": {
            anrdRoleDevelopmentProjectLeadId: "Development Project Lead",
            anrdRoleSeniorDeveloperId: "Senior Developer",
            anrdRoleDeveloperId: "Developer",
            anrdRoleContributorId: "Contributor",
            anrdRoleProbationaryId: "Probationary",
        },
        "rolePriority": [
            anrdRoleDevelopmentProjectLeadId,
            anrdRoleSeniorDeveloperId,
            anrdRoleDeveloperId,
            anrdRoleContributorId,
            anrdRoleProbationaryId,
        ],
        "requireMappedRole": True,
        "organizeAfter": True,
        "fundingRoleId": anrdFundingBenefactorsRoleId,
    },
]

# Shared multi-ORBAT registry.
multiOrbatSheets = [
    {
        "key": "generalStaff",
        "displayName": "General Staff ORBAT",
        "spreadsheetId": orbatSpreadsheetId,
        "sheetName": orbatSheetName,
        "credentialsPathEnvVar": "ORBAT_GOOGLE_CREDENTIALS_PATH",
        "credentialsPathConfigKey": "orbatGoogleCredentialsPath",
        "rowModel": {
            "identity": {
                "discordIdColumn": orbatColumnDiscordId,
                "robloxUserColumn": orbatColumnRobloxUser,
            },
            "eventColumns": {
                "shifts": orbatColumnShifts,
                "otherEvents": orbatColumnOtherEvents,
                "total": orbatColumnTotal,
                "allTimeShifts": orbatColumnAllTimeShifts,
                "allTime": orbatColumnAllTime,
            },
            "profileColumns": {
                "rank": orbatColumnRank,
                "clearance": orbatColumnClearance,
                "status": orbatColumnStatus,
                "loaInfo": orbatColumnLoaInfo,
                "department": orbatColumnDepartment,
                "notes": orbatColumnNotes,
                "mic": orbatColumnMic,
                "timezone": orbatColumnTimezone,
                "ageGroup": orbatColumnAgeGroup,
                "strikes": orbatColumnStrikes,
            },
        },
        "organization": {
            "enabled": True,
            "supportsSectionHeaders": True,
        },
    },
    {
        "key": "recruitment",
        "displayName": "Recruitment ORBAT",
        "spreadsheetId": recruitmentSpreadsheetId,
        "sheetName": "ANRORS",
        "credentialsPathEnvVar": "ORBAT_GOOGLE_CREDENTIALS_PATH",
        "credentialsPathConfigKey": "orbatGoogleCredentialsPath",
        "rowModel": {
            "identity": {
                "robloxUserColumn": "B",
            },
            "pointColumns": {
                "monthly": "D",
                "allTime": "E",
                "patrols": "F",
            },
            "profileColumns": {
                "rank": "C",
                "quota": "G",
                "status": "H",
                "loaExpiration": "I",
                "notes": "J",
            },
        },
        "organization": {
            "enabled": True,
            "supportsSectionHeaders": True,
        },
    },
    {
        "key": "honorGuard_members",
        "displayName": "Honor Guard ORBAT",
        "spreadsheetId": honorGuardSpreadsheetId,
        "sheetName": honorGuardMemberSheetName,
        "credentialsPathEnvVar": honorGuardCredentialsPathEnvVar,
        "credentialsPathConfigKey": honorGuardCredentialsPathConfigKey,
        "rowModel": {
            "identity": {
                "discordIdColumn": honorGuardDiscordIdColumn,
                "robloxUserColumn": honorGuardRobloxUsernameColumn,
            },
            "pointColumns": {
                "quota": honorGuardQuotaPointsColumn,
                "promotionEvent": honorGuardPromotionEventPointsColumn,
                "promotionAwarded": honorGuardPromotionAwardedPointsColumn,
                "promotionTotal": honorGuardPromotionTotalPointsColumn,
                "hostedEvents": honorGuardHostedEventsColumn,
            },
            "profileColumns": {
                "rank": honorGuardRankColumn,
                "status": honorGuardActivityStatusColumn,
                "quotaStatus": honorGuardQuotaCompleteFormulaColumn,
            },
        },
        "organization": {
            "enabled": honorGuardEnabled,
            "supportsSectionHeaders": True,
        },
    },
    {
        "key": "honorGuard_schedule",
        "displayName": "Honor Guard Event Schedule",
        "spreadsheetId": honorGuardSpreadsheetId,
        "sheetName": honorGuardScheduleSheetName,
        "credentialsPathEnvVar": honorGuardCredentialsPathEnvVar,
        "credentialsPathConfigKey": honorGuardCredentialsPathConfigKey,
        "organization": {
            "enabled": honorGuardEnabled,
            "supportsSectionHeaders": False,
        },
    },
    {
        "key": "honorGuard_platoon_cmp",
        "displayName": "Honor Guard Cavalry Platoon ORBAT",
        "spreadsheetId": honorGuardSpreadsheetId,
        "sheetName": honorGuardCMPSheetName,
        "credentialsPathEnvVar": honorGuardCredentialsPathEnvVar,
        "credentialsPathConfigKey": honorGuardCredentialsPathConfigKey,
        "organization": {
            "enabled": honorGuardEnabled,
            "supportsSectionHeaders": True,
        },
    },
    {
        "key": "honorGuard_archive",
        "displayName": "Honor Guard Event Archive",
        "spreadsheetId": honorGuardSpreadsheetId,
        "sheetName": honorGuardArchiveSheetName,
        "credentialsPathEnvVar": honorGuardCredentialsPathEnvVar,
        "credentialsPathConfigKey": honorGuardCredentialsPathConfigKey,
        "organization": {
            "enabled": honorGuardEnabled,
            "supportsSectionHeaders": False,
        },
    },
    {
        "key": "honorGuard_eventHosts",
        "displayName": "Honor Guard Event Hosts",
        "spreadsheetId": honorGuardSpreadsheetId,
        "sheetName": honorGuardEventHostsSheetName,
        "credentialsPathEnvVar": honorGuardCredentialsPathEnvVar,
        "credentialsPathConfigKey": honorGuardCredentialsPathConfigKey,
        "organization": {
            "enabled": honorGuardEnabled,
            "supportsSectionHeaders": True,
        },
    },
    {
        "key": "dept_anrd",
        "displayName": "Department ORBAT - ANRD",
        "spreadsheetId": deptSpreadsheetId,
        "sheetName": "ANRD",
        "credentialsPathEnvVar": "ORBAT_GOOGLE_CREDENTIALS_PATH",
        "credentialsPathConfigKey": "orbatGoogleCredentialsPath",
        "organization": {
            "enabled": True,
            "supportsSectionHeaders": False,
        },
    },
    {
        "key": "dept_ce",
        "displayName": "Department ORBAT - CE",
        "spreadsheetId": deptSpreadsheetId,
        "sheetName": "CE",
        "credentialsPathEnvVar": "ORBAT_GOOGLE_CREDENTIALS_PATH",
        "credentialsPathConfigKey": "orbatGoogleCredentialsPath",
        "organization": {
            "enabled": True,
            "supportsSectionHeaders": True,
        },
    },
]

# Global Sheets throttling.
googleSheetsMinRequestIntervalSec = 0.05
googleSheetsMaxAttempts = 3
googleSheetsRetryBaseSec = 1.5

# Discord can occasionally 5xx during the first login/application_info call.
# Retry only startup transport/server failures; invalid token/config errors still fail fast.
discordStartupMaxAttempts = 6
discordStartupRetryBaseSec = 15
discordStartupRetryMaxDelaySec = 120

# Temporary identity backfill command: !pairDbNames
pairDbNamesSourceChannelId = 0
pairDbNamesLookbackDays = 5
pairDbNamesLookupConcurrency = 4
pairDbNamesMaxLookups = 500
pairDbNamesHistoryPageSize = 100
pairDbNamesHistoryMaxAttempts = 5
pairDbNamesHistoryRetryBaseSec = 2
pairDbNamesHistoryRetryMaxDelaySec = 20

# Local ORBAT mirror. This is a read-through cache of member rows, not the
# source of truth for sheet edits.
orbatMirrorEnabled = True
orbatMirrorMaxRows = 800
orbatMirrorMaxColumn = "AZ"
orbatMirrorHeaderScanRows = 12
