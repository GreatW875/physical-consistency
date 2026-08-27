"""Detect files and machine-specific values that must not enter the repository."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable, List


FORBIDDEN_DIRECTORIES = {
    ".agents",
    ".claude",
    ".codex",
    ".workbuddy",
    "Library",
    "Logs",
    "Temp",
    "UserSettings",
    "__pycache__",
}
TEXT_SUFFIXES = {
    ".anim",
    ".asmdef",
    ".asmref",
    ".asset",
    ".cs",
    ".csv",
    ".controller",
    ".env",
    ".hlsl",
    ".json",
    ".mat",
    ".md",
    ".meta",
    ".overridecontroller",
    ".playable",
    ".prefab",
    ".py",
    ".shader",
    ".tsv",
    ".txt",
    ".unity",
    ".uss",
    ".uxml",
    ".xml",
    ".yaml",
    ".yml",
}
SENSITIVE_PATTERNS = (
    (
        "DASHSCOPE_API_KEY",
        re.compile(r"DASHSCOPE_API_KEY\s*=\s*['\"]?(?!your-|replace-|example|test)[A-Za-z0-9_-]{20,}"),
    ),
    (
        "possible API token",
        re.compile(r"\bsk-(?!test|example|replace)[A-Za-z0-9_-]{20,}\b"),
    ),
    ("Linux user path", re.compile(r"/home/[^/\s]+/")),
    ("Windows user path", re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+\\")),
)


def _files(root: Path) -> Iterable[Path]:
    audit_script = Path(__file__).resolve()
    for path in root.rglob("*"):
        if ".git" in path.parts:
            continue
        if path.resolve() == audit_script:
            continue
        if path.is_file() and (path.suffix.lower() in TEXT_SUFFIXES or path.name.startswith(".env")):
            yield path


def audit_repository(root: Path) -> List[str]:
    """Return human-readable problems found below *root*."""
    root = root.resolve()
    issues: List[str] = []

    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part in FORBIDDEN_DIRECTORIES for part in relative.parts):
            issues.append(f"forbidden path: {relative.as_posix()}")

    for path in _files(root):
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        relative = path.relative_to(root).as_posix()
        for label, pattern in SENSITIVE_PATTERNS:
            if pattern.search(content):
                issues.append(f"{label}: {relative}")

    return sorted(set(issues))


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
    issues = audit_repository(root)
    if issues:
        for issue in issues:
            print(issue)
        return 1
    print(f"Repository audit passed: {root.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
