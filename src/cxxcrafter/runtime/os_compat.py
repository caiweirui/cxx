from __future__ import annotations

import os
import platform
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

@dataclass
class OSCompatResult:
    supported: bool
    os_name: str
    reason: str = ""
    suggestions: List[str] = field(default_factory=list)

def detect_host_os() -> str:
    return platform.system() or "Unknown"

def is_windows() -> bool:
    return detect_host_os() == "Windows"

def is_linux() -> bool:
    return detect_host_os() == "Linux"

def is_macos() -> bool:
    return detect_host_os() == "Darwin"

def normalize_path(path: str | Path | None) -> str:
    if path is None:
        return ""
    return str(Path(path).expanduser().resolve())

def project_root_from_file(file_path: str, levels_up: int = 3) -> Path:
    return Path(file_path).resolve().parents[levels_up]

def default_output_dir(project_root: Optional[str] = None) -> str:
    root = Path(project_root).expanduser().resolve() if project_root else Path.cwd()
    return str((root / "dockerfile_playground").resolve())

def default_log_dir(project_root: Optional[str] = None) -> str:
    root = Path(project_root).expanduser().resolve() if project_root else Path.cwd()
    return str((root / "data" / "build_logs").resolve())

def open_path(path: str) -> bool:
    """
    跨平台打开文件夹/文件：
    - Windows: os.startfile
    - macOS: open
    - Linux: xdg-open
    """
    try:
        path = normalize_path(path)
        if not path:
            return False

        if is_windows():
            os.startfile(path)  # type: ignore[attr-defined]
            return True
        if is_macos():
            subprocess.Popen(["open", path])
            return True
        subprocess.Popen(["xdg-open", path])
        return True
    except Exception:
        return False

def get_os_compat_result() -> OSCompatResult:
    os_name = detect_host_os()

    if os_name == "Windows":
        return OSCompatResult(
            supported=True,
            os_name=os_name,
            reason="Windows 支持 Dockerfile 生成与验证，但需安装并启动 Docker Desktop。",
            suggestions=[
                "确认 Docker Desktop 已启动",
                "确认 Docker Engine 可用",
                "执行 `docker info` 检查 daemon 是否正常",
            ],
        )

    if os_name == "Linux":
        return OSCompatResult(
            supported=True,
            os_name=os_name,
            reason="Linux 支持 Dockerfile 生成与验证，但需安装并启动 Docker Engine。",
            suggestions=[
                "确认 docker 服务已启动",
                "确认当前用户有 docker 权限",
                "执行 `docker info` 检查 daemon 是否正常",
            ],
        )

    if os_name == "Darwin":
        return OSCompatResult(
            supported=True,
            os_name=os_name,
            reason="macOS 理论支持 Dockerfile 生成与验证，但需要 Docker Desktop。",
            suggestions=[
                "确认 Docker Desktop 已启动",
                "确认 Docker 引擎可用",
                "执行 `docker info` 检查 daemon 是否正常",
            ],
        )

    return OSCompatResult(
        supported=False,
        os_name=os_name,
        reason=f"当前操作系统 {os_name} 不在支持范围内，无法执行 Dockerfile 验证。",
        suggestions=[
            "建议切换到 Windows / Linux / macOS",
            "或在支持 Docker 的环境中运行验证",
        ],
    )

def format_os_tip() -> str:
    result = get_os_compat_result()
    if result.supported:
        return f"[OS兼容性] {result.os_name}：支持。{result.reason}"
    return f"[OS兼容性] {result.os_name}：不支持。{result.reason}"