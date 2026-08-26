"""Check whether the standalone experiment repository is ready to run."""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path
from typing import List, Mapping


REQUIRED_PATHS = (
    "Assets",
    "Assets/Manipulation/G1OP.unity",
    "Assets/Manipulation/main.py",
    "Packages/manifest.json",
    "ProjectSettings/ProjectVersion.txt",
    "environment.yml",
)
PROTOCOL_TOKENS = ("127.0.0.1", "5555", "IMG_READY", "RESET", "STUCK|")


def _check_port() -> List[str]:
    issues: List[str] = []
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", 5555))
    except OSError:
        issues.append("端口 5555 已被占用；如果 Unity 正在运行，这是预期状态")
    finally:
        probe.close()
    return issues


def check_environment(
    root: Path,
    env: Mapping[str, str],
    manual: bool = False,
) -> List[str]:
    """Return setup problems without modifying the project or stopping processes."""
    root = root.resolve()
    issues: List[str] = []

    if sys.version_info < (3, 8):
        issues.append("需要 Python 3.8 或更高版本")

    for relative in REQUIRED_PATHS:
        if not (root / relative).exists():
            issues.append(f"缺少 {relative}")

    if not manual and not env.get("DASHSCOPE_API_KEY", "").strip():
        issues.append("未设置 DASHSCOPE_API_KEY；VLM 模式无法请求模型")

    main_script = root / "Assets" / "Manipulation" / "main.py"
    if main_script.is_file():
        content = main_script.read_text(encoding="utf-8")
        for token in PROTOCOL_TOKENS:
            if token not in content:
                issues.append(f"main.py 缺少通信协议标记 {token}")

    manifest_path = root / "Packages" / "manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            dependencies = manifest["dependencies"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            issues.append(f"Packages/manifest.json 无法解析：{exc}")
        else:
            for name, version in dependencies.items():
                if not isinstance(version, str) or not version.startswith("file:"):
                    continue
                if "/home/" in version or (len(version) > 2 and version[1:3] in (":/", ":\\")):
                    issues.append(f"本地包 {name} 使用了绝对路径：{version}")
                    continue
                package = (manifest_path.parent / version[5:]).resolve()
                if not package.is_dir():
                    issues.append(f"本地包 {name} 不存在：{version}")

    issues.extend(_check_port())
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--manual", action="store_true", help="手动模式不检查 API Key")
    args = parser.parse_args()

    issues = check_environment(args.root, os.environ, manual=args.manual)
    if issues:
        for issue in issues:
            print(f"[检查] {issue}")
        return 1
    print("环境检查通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
