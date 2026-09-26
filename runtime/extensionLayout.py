from __future__ import annotations

import importlib
import logging
from typing import Any

from runtime.optionalImports import isRequestedModuleMissing


log = logging.getLogger(__name__)

_coreExtensionNames = [
    "cogs.staff.sessionCog",
    "cogs.staff.divisionClockinCog",
    "cogs.staff.cohostCog",
    "cogs.staff.recruitmentCog",
    "cogs.staff.bgFlagCog",
    "cogs.staff.bgIntelligenceCog",
    "cogs.applicationsCog",
    "cogs.staff.ribbonCog",
    "cogs.staff.anrdPaymentCog",
    "cogs.operations.federationCog",
    "cogs.staff.projectCog",
    "cogs.community.publicUtilityCog",
    "cogs.community.eventCog",
    "cogs.community.bestOfCog",
    "cogs.community.archiveCog",
    "cogs.community.infoCog",
    "cogs.community.identityCog",
    "cogs.community.pollCog",
    "cogs.community.reminderCog",
    "cogs.community.suggestionCog",
    "cogs.community.minecraftCog",
    "cogs.operations.opsCog",
    "cogs.operations.curfewCog",
    "cogs.operations.jailCog",
    "cogs.staff.voiceChatCog",
    "silly.hallCog",
    "silly.gamblingCog",
    "silly.cookieCog",
]
_optionalExtensionListModules = [
    "plugins.public.extensionList",
    "plugins.private.extensionList",
]


def _dedupeExtensionNames(extensionNames: list[str]) -> list[str]:
    orderedNames: list[str] = []
    seenNames: set[str] = set()
    for rawName in extensionNames:
        name = str(rawName or "").strip()
        if not name or name in seenNames:
            continue
        seenNames.add(name)
        orderedNames.append(name)
    return orderedNames


def _loadOptionalExtensionNames(moduleName: str) -> list[str]:
    try:
        module = importlib.import_module(moduleName)
    except ModuleNotFoundError as exc:
        if isRequestedModuleMissing(exc, moduleName):
            return []
        log.exception(
            "Optional extension list module %s has a missing dependency %s.",
            moduleName,
            exc.name,
        )
        raise
    except Exception:
        log.exception("Failed to import optional extension list module %s.", moduleName)
        raise

    rawExtensionNames = getattr(module, "extensionNames", [])
    if not isinstance(rawExtensionNames, (list, tuple, set)):
        log.warning(
            "Optional extension list module %s exposed a non-list extensionNames value.",
            moduleName,
        )
        return []
    return [str(name).strip() for name in rawExtensionNames if str(name).strip()]


def buildExtensionNames(*, configModule: Any | None = None) -> list[str]:
    extensionNames = list(_coreExtensionNames)
    privateExtensionsEnabled = True

    if configModule is not None:
        rawExtraExtensionNames = getattr(configModule, "extraExtensionNames", [])
        if isinstance(rawExtraExtensionNames, (list, tuple, set)):
            extensionNames.extend(str(name).strip() for name in rawExtraExtensionNames if str(name).strip())
        privateExtensionsEnabled = bool(getattr(configModule, "enablePrivateExtensions", True))

    for moduleName in _optionalExtensionListModules:
        if moduleName.startswith("plugins.private.") and not privateExtensionsEnabled:
            continue
        extensionNames.extend(_loadOptionalExtensionNames(moduleName))

    return _dedupeExtensionNames(extensionNames)


def classifyExtensionLayer(extensionName: str) -> str:
    name = str(extensionName or "").strip()
    if name.startswith("plugins.private."):
        return "private"
    if name.startswith("plugins.public."):
        return "public"
    return "core"
