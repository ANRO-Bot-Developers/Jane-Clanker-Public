from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

_EXCLUDED_PATH_PREFIXES = {
    ".git",
    ".idea",
    ".mypy_cache",
    ".pytest_cache",
    ".python",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "backups",
    "build",
    "dist",
    "env",
    "htmlcov",
    "localOnly",
    "logs",
    "runtime/data",
    "scripts",
    "silly/archives",
    "tmp",
    "temp",
    "venv",
}
_EXCLUDED_PATH_PARTS = {
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}
_EXCLUDED_NAME_GLOBS = {
    "*.avi",
    "*.bak",
    "*.db",
    "*.db-shm",
    "*.db-wal",
    "*.egg-info",
    "*.log",
    "*.mov",
    "*.mp4",
    "*.pyc",
    "*.pyo",
    "*.sqlite",
    "*.sqlite3",
    "*.swp",
    "*.swo",
    ".coverage",
    ".coverage.*",
    ".env",
    ".env.*",
    "Thumbs.db",
    "coverage.xml",
    "htmlcov",
    "client_secret_*.json",
    "google-oauth-token*.json",
    "jane-clanker-*.json",
    "oauth-token*.json",
    "*credentials*.json",
    "*service-account*.json",
}
_ALLOWED_HIDDEN_FILES = {
    ".env.example",
}
_ALLOWED_PRIVATE_PLUGIN_SCAFFOLD_PATHS = {
    "plugins/private/__init__.py",
    "plugins/private/extensionList.py",
}
_PRIVATE_ONLY_PATH_PREFIXES = {
    "cogs/operations/orbatCog.py",
    "cogs/operations/linkHubCog.py",
    "cogs/operations/runtimeControlCog.py",
    "cogs/operations/serverSafetyCog.py",
    "features/operations/linkHub",
    "features/operations/serverSafety",
    "runtime/configMerge.py",
    "runtime/gitUpdate.py",
    "runtime/processControl.py",
    "tests/test_link_hub_service.py",
}
_SCAN_PATTERNS = (
    ("discord_webhook", re.compile(r"https://(?:ptb\.)?discord(?:app)?\.com/api/webhooks/\S+")),
    ("private_key_block", re.compile(r"(?m)^\s*-----BEGIN PRIVATE KEY-----")),
    ("service_account_private_key", re.compile(r'"private_key"\s*:\s*"-----BEGIN PRIVATE KEY-----')),
    ("github_token", re.compile(r"\b(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z\-_]{20,}\b")),
    ("google_form_url", re.compile(r"https://(?:docs\.google\.com/forms|forms\.gle)/\S+")),
    (
        "literal_secret_assignment",
        re.compile(
            r"(?im)^\s*[A-Za-z_][A-Za-z0-9_]*(?:token|api[_-]?key|secret|password|webhook(?:url|_url)?)\s*=\s*['\"][^'\"]{8,}['\"]"
        ),
    ),
)
_GENERIC_CONFIG_REPLACEMENTS = {
    "enablePrivateExtensions": "False",
    "autoGitUpdateEnabled": "False",
    "serverIdTesting": "0",
}
_CONFIG_SANITIZE_PATHS = (
    "config.py",
    "settings/env.py",
    "settings/core.py",
    "settings/staff.py",
    "settings/operations.py",
    "settings/community.py",
)


@dataclass
class ExportStats:
    copiedFiles: int = 0
    copiedDirectories: int = 0
    skippedEntries: int = 0


def _relativePosix(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _isAllowedHiddenFile(relativePath: str) -> bool:
    return relativePath in _ALLOWED_HIDDEN_FILES


def _isExcludedPath(path: Path) -> bool:
    relativePath = _relativePosix(path)
    if _isAllowedHiddenFile(relativePath):
        return False
    if any(
        relativePath == prefix or relativePath.startswith(f"{prefix}/")
        for prefix in _PRIVATE_ONLY_PATH_PREFIXES
    ):
        return True
    if relativePath == "plugins/private":
        return False
    if relativePath.startswith("plugins/private/"):
        return relativePath not in _ALLOWED_PRIVATE_PLUGIN_SCAFFOLD_PATHS
    if any(part in _EXCLUDED_PATH_PARTS for part in path.relative_to(REPO_ROOT).parts):
        return True

    if any(
        relativePath == prefix or relativePath.startswith(f"{prefix}/")
        for prefix in _EXCLUDED_PATH_PREFIXES
    ):
        return True

    pathName = path.name
    for pattern in _EXCLUDED_NAME_GLOBS:
        if fnmatch.fnmatch(pathName, pattern) or fnmatch.fnmatch(relativePath, pattern):
            return True
    return False


def _removePath(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path)
        return
    path.unlink()


def _shouldPreserveTargetEntryOnClean(path: Path) -> bool:
    return path.name == ".git"


def _prepareTarget(targetRoot: Path, *, clean: bool) -> None:
    if targetRoot == REPO_ROOT or REPO_ROOT in targetRoot.parents:
        raise ValueError("Target directory must be outside the source repository.")

    if targetRoot.exists() and not targetRoot.is_dir():
        raise ValueError("Target path exists and is not a directory.")

    if clean and targetRoot.exists():
        for child in targetRoot.iterdir():
            if _shouldPreserveTargetEntryOnClean(child):
                continue
            _removePath(child)

    targetRoot.mkdir(parents=True, exist_ok=True)


def _copyTree(sourceRoot: Path, targetRoot: Path, stats: ExportStats) -> None:
    for child in sorted(sourceRoot.iterdir(), key=lambda item: (item.is_file(), item.name.lower())):
        if _isExcludedPath(child):
            stats.skippedEntries += 1
            continue

        destination = targetRoot / child.name
        if child.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            stats.copiedDirectories += 1
            _copyTree(child, destination, stats)
            continue

        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(child, destination)
        stats.copiedFiles += 1


def _isProbablyTextFile(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            sample = handle.read(8192)
    except OSError:
        return False
    return b"\x00" not in sample


def _scanForSecrets(targetRoot: Path) -> list[str]:
    findings: list[str] = []
    for filePath in sorted(path for path in targetRoot.rglob("*") if path.is_file()):
        if not _isProbablyTextFile(filePath):
            continue

        try:
            text = filePath.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        relativePath = filePath.relative_to(targetRoot).as_posix()
        for label, pattern in _SCAN_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            findings.append(f"{relativePath}: {label}")
            break
    return findings


def _stripFunctionBlock(source: str, functionName: str, *, indent: str = "") -> str:
    escapedIndent = re.escape(indent)
    pattern = re.compile(
        rf"(?ms)^(?:{escapedIndent}@[^\n]+\n)*"
        rf"{escapedIndent}(?:async\s+def|def)\s+{re.escape(functionName)}\b[^\n]*:\n"
        rf".*?(?=^(?:{escapedIndent}@[^\n]+\n)*{escapedIndent}(?:async\s+def|def)\s+\w+\b|^class\s+\w+\b|\Z)"
    )
    return pattern.sub("", source)


def _clearTopLevelIdLists(source: str) -> str:
    tree = ast.parse(source)
    replacements: list[tuple[int, int, str]] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        if not target.id.endswith("Ids") or not isinstance(node.value, ast.List):
            continue
        replacements.append((node.lineno, node.end_lineno or node.lineno, f"{target.id} = []\n"))

    if not replacements:
        return source

    lines = source.splitlines(keepends=True)
    for startLine, endLine, replacement in sorted(replacements, reverse=True):
        lines[startLine - 1 : endLine] = [replacement]
    return "".join(lines)


def _iterConfigSanitizePaths(targetRoot: Path) -> list[Path]:
    return [
        targetRoot / relPath
        for relPath in _CONFIG_SANITIZE_PATHS
        if (targetRoot / relPath).exists()
    ]


def _sanitizeExportedConfig(targetRoot: Path) -> None:
    for configPath in _iterConfigSanitizePaths(targetRoot):
        source = configPath.read_text(encoding="utf-8")
        for envVar in ("BGC_SPREADSHEET_TEMPLATE_ID", "BGC_SPREADSHEET_FOLDER_ID"):
            source = re.sub(
                rf'("{re.escape(envVar)}",\s*\n\s*)"(?:[^"\\]|\\.)*"',
                r'\1""',
                source,
            )
        source = re.sub(
            r'(?m)^load_dotenv\(Path\(__file__\)\.resolve\(\)\.parent / "localOnly" / "credentials" / "jane-runtime-secrets\.env", override=True\)\n',
            "",
            source,
        )
        source = re.sub(
            r'(?m)^load_dotenv\(_REPO_ROOT / "localOnly" / "credentials" / "jane-runtime-secrets\.env", override=True\)\n',
            "",
            source,
        )
        if "Path(" not in source:
            source = re.sub(r"(?m)^from pathlib import Path\n", "", source)
        source = _clearTopLevelIdLists(source)
        source = re.sub(
            r"(?m)^([A-Za-z_][A-Za-z0-9_]*(?:SpreadsheetId|SheetId))\s*=\s*(?:r?\"[^\"]*\"|r?'[^']*'|\d+)",
            lambda match: f'{match.group(1)} = ""',
            source,
        )
        source = re.sub(r'("spreadsheetId"\s*:\s*)"(.*?)"', r'\1""', source)
        source = re.sub(r'("sheetId"\s*:\s*)"(.*?)"', r'\1""', source)
        source = re.sub(
            r"(?m)^([A-Za-z_][A-Za-z0-9_]*Id)\s*=\s*(?:r?\"[^\"]*\"|r?'[^']*'|\d+)",
            lambda match: match.group(0)
            if match.group(1).endswith(("SpreadsheetId", "SheetId"))
            else f"{match.group(1)} = 0",
            source,
        )
        for name, replacement in _GENERIC_CONFIG_REPLACEMENTS.items():
            source = re.sub(
                rf"(?m)^({re.escape(name)})\s*=\s*.+$",
                lambda match: f"{match.group(1)} = {replacement}",
                source,
            )
        configPath.write_text(source, encoding="utf-8")


def _sanitizeApplicationConfig(targetRoot: Path) -> None:
    divisionsPath = targetRoot / "configData" / "divisions.json"
    if not divisionsPath.exists():
        return

    try:
        payload = json.loads(divisionsPath.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return

    changed = False
    for division in list(payload.get("divisions") or []):
        if not isinstance(division, dict):
            continue
        for question in list(division.get("questions") or []):
            if not isinstance(question, dict):
                continue
            link = str(question.get("link") or "").strip()
            if not link:
                continue
            if "docs.google.com/forms" not in link and "forms.gle/" not in link:
                continue
            question["link"] = ""
            changed = True

    if changed:
        divisionsPath.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )




def _writePrivateScaffoldFiles(targetRoot: Path) -> None:
    privateExtensionListPath = targetRoot / "plugins" / "private" / "extensionList.py"
    if privateExtensionListPath.exists():
        privateExtensionListPath.write_text(
            "from __future__ import annotations\n\n\n# Private-only optional extensions can be listed here on private deployments.\nextensionNames: list[str] = []\n",
            encoding="utf-8",
        )


def _runPublicSmokeTest(targetRoot: Path) -> None:
    compileCommand = [
        sys.executable,
        "-m",
        "compileall",
        "-q",
        str(targetRoot),
    ]
    compileResult = subprocess.run(
        compileCommand,
        cwd=targetRoot,
        capture_output=True,
        text=True,
        check=False,
    )
    if compileResult.returncode != 0:
        detail = (compileResult.stderr or compileResult.stdout or "").strip()
        raise RuntimeError(f"compileall failed for exported repo.{f' {detail}' if detail else ''}")

    importScript = """
import importlib
import os
import sys

targetRoot = sys.argv[1]
sys.path.insert(0, targetRoot)
os.environ["DISCORD_BOT_TOKEN"] = "public-export-smoke-token"
os.environ["ROBLOX_OPEN_CLOUD_API_KEY"] = "public-export-smoke-key"
os.environ["ROVER_API_KEY"] = "public-export-smoke-key"
os.environ["ORBAT_GOOGLE_CREDENTIALS_PATH"] = "public-export-service-account.json"
os.environ["JANE_GAMBLING_API_TOKEN"] = "public-export-smoke-token"
os.environ["JANE_ENABLE_PRIVATE_EXTENSIONS"] = "0"
os.environ["JANE_ENABLE_AUTO_GIT_UPDATE"] = "0"

moduleNames = [
    "config",
    "runtime.extensionLayout",
    "runtime.privateServices",
    "runtime.pluginRegistry",
    "runtime.maintenance",
    "bot",
]

failed = []
for moduleName in moduleNames:
    try:
        importlib.import_module(moduleName)
    except Exception as exc:
        failed.append(f"{moduleName}: {exc.__class__.__name__}: {exc}")

if not failed:
    import config
    from runtime import extensionLayout

    for extensionName in extensionLayout.buildExtensionNames(configModule=config):
        try:
            importlib.import_module(extensionName)
        except Exception as exc:
            failed.append(f"{extensionName}: {exc.__class__.__name__}: {exc}")

if failed:
    print("Import smoke test failed:")
    for line in failed:
        print(f" - {line}")
    raise SystemExit(1)

print("Import smoke test passed.")
"""
    env = os.environ.copy()
    env["DISCORD_BOT_TOKEN"] = "public-export-smoke-token"
    env["ROBLOX_OPEN_CLOUD_API_KEY"] = "public-export-smoke-key"
    env["ROVER_API_KEY"] = "public-export-smoke-key"
    env["ORBAT_GOOGLE_CREDENTIALS_PATH"] = "public-export-service-account.json"
    env["JANE_GAMBLING_API_TOKEN"] = "public-export-smoke-token"
    env["JANE_ENABLE_PRIVATE_EXTENSIONS"] = "0"
    env["JANE_ENABLE_AUTO_GIT_UPDATE"] = "0"
    importResult = subprocess.run(
        [sys.executable, "-c", importScript, str(targetRoot)],
        cwd=targetRoot,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if importResult.returncode != 0:
        detail = (importResult.stdout or importResult.stderr or "").strip()
        raise RuntimeError(f"import smoke test failed for exported repo.{f' {detail}' if detail else ''}")


def _cleanupSmokeArtifacts(targetRoot: Path) -> None:
    for path in sorted(targetRoot.rglob("__pycache__"), reverse=True):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
    for pattern in ("*.pyc", "*.pyo"):
        for path in targetRoot.rglob(pattern):
            try:
                path.unlink()
            except OSError:
                pass


def _buildParser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export a sanitized public copy of the Jane repo.",
    )
    parser.add_argument(
        "target",
        help="Directory to receive the sanitized export. This can be a plain folder or a cloned public repo.",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Delete existing contents in the target directory before export while preserving the target repo's .git directory.",
    )
    parser.add_argument(
        "--allow-findings",
        action="store_true",
        help="Return success even if the post-copy secret scan reports findings.",
    )
    parser.add_argument(
        "--skip-smoke-test",
        action="store_true",
        help="Skip the post-export compile/import smoke test.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _buildParser().parse_args(argv)
    targetRoot = Path(args.target).expanduser().resolve()

    try:
        _prepareTarget(targetRoot, clean=args.clean)
    except ValueError as exc:
        print(f"Export aborted: {exc}", file=sys.stderr)
        return 2

    stats = ExportStats()
    _copyTree(REPO_ROOT, targetRoot, stats)
    _writePrivateScaffoldFiles(targetRoot)
    _sanitizeExportedConfig(targetRoot)
    _sanitizeApplicationConfig(targetRoot)
    findings = _scanForSecrets(targetRoot)

    print(f"Exported to: {targetRoot}")
    print(f"Copied files: {stats.copiedFiles}")
    print(f"Copied directories: {stats.copiedDirectories}")
    print(f"Skipped entries: {stats.skippedEntries}")

    if findings:
        print("Potential secret findings:")
        for finding in findings:
            print(f" - {finding}")
        if not args.allow_findings:
            print("Export finished with findings. Review the copied tree before publishing.", file=sys.stderr)
            return 1
    else:
        print("Secret scan: clean")

    if not args.skip_smoke_test:
        try:
            _runPublicSmokeTest(targetRoot)
        except RuntimeError as exc:
            print(f"Public smoke test failed: {exc}", file=sys.stderr)
            return 1
        _cleanupSmokeArtifacts(targetRoot)
        print("Public smoke test: passed")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
