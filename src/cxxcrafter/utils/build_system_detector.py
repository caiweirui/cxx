from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

@dataclass
class BuildSystemDetectionResult:
    build_system: str = "unknown"
    confidence: float = 0.0
    build_root_rel: str = "."
    reason: str = "unknown"
    scanned_files: Optional[Dict[str, List[str]]] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        if payload["scanned_files"] is None:
            payload["scanned_files"] = {
                "cmake": [],
                "make": [],
                "meson": [],
                "autotools": [],
                "python": [],
                "node": [],
            }
        return payload

def normalize_build_system_name(build_system: Any) -> str:
    s = str(build_system or "").strip().lower()
    aliases = {
        "cmake": "cmake",
        "make": "make",
        "makefile": "make",
        "gnu make": "make",
        "gnumake": "make",
        "gnumakefile": "make",
        "meson": "meson",
        "autotools": "autotools",
        "autoconf": "autotools",
        "automake": "autotools",
        "python": "python",
        "py": "python",
        "pip": "python",
        "node": "node",
        "npm": "node",
        "javascript": "node",
        "typescript": "node",
    }
    return aliases.get(s, s or "unknown")

def _safe_relpath(path: str | Path, root: str | Path) -> str:
    try:
        rel = os.path.relpath(str(path), str(root))
        return rel if rel else "."
    except Exception:
        return "."

def _score_rel_dir(rel_dir: str) -> int:
    if rel_dir in ("", "."):
        return 0
    return rel_dir.count(os.sep) + 1

def _root_markers() -> Dict[str, List[str]]:
    return {
        "cmake": ["CMakeLists.txt", "CMakePresets.json", "cmakepresets.json"],
        "make": ["Makefile", "makefile", "GNUmakefile", "gnumakefile"],
        "meson": ["meson.build"],
        "autotools": ["configure.ac", "configure.in", "autogen.sh"],
        "python": ["pyproject.toml", "setup.py", "requirements.txt", "Pipfile"],
        "node": ["package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock"],
    }

def scan_build_files(project_path: str) -> Dict[str, List[str]]:
    """
    扫描项目目录中的构建文件。
    返回：
    {
        "cmake": [...],
        "make": [...],
        "meson": [...],
        "autotools": [...],
        "python": [...],
        "node": [...]
    }
    """
    result: Dict[str, List[str]] = {
        "cmake": [],
        "make": [],
        "meson": [],
        "autotools": [],
        "python": [],
        "node": [],
    }

    root = Path(project_path)
    if not root.is_dir():
        return result

    markers = _root_markers()

    # 只做轻量扫描：优先顶层与少量递归
    for dirpath, dirnames, filenames in os.walk(project_path):
        lower_files = {f.lower() for f in filenames}

        if lower_files & {x.lower() for x in markers["cmake"]}:
            result["cmake"].append(dirpath)
        if lower_files & {x.lower() for x in markers["make"]}:
            result["make"].append(dirpath)
        if lower_files & {x.lower() for x in markers["meson"]}:
            result["meson"].append(dirpath)
        if lower_files & {x.lower() for x in markers["autotools"]}:
            result["autotools"].append(dirpath)
        if lower_files & {x.lower() for x in markers["python"]}:
            result["python"].append(dirpath)
        if lower_files & {x.lower() for x in markers["node"]}:
            result["node"].append(dirpath)

        # 轻量早停：每类最多记录 8 个位置
        if all(len(result[k]) >= 8 for k in result):
            break

    return result

def choose_best_build_root(project_path: str, dirs: List[str]) -> str:
    if not dirs:
        return "."

    rels = [_safe_relpath(d, project_path) for d in dirs]
    rels.sort(key=_score_rel_dir)
    return rels[0] if rels else "."

def _has_top_level_marker(project_path: str, marker_names: List[str]) -> bool:
    root = Path(project_path)
    for name in marker_names:
        if (root / name).exists():
            return True
    return False

def detect_build_system(
    project_path: str,
    parsed_build_system: Optional[str] = None,
    build_root_info: Optional[Dict[str, Any]] = None,
) -> BuildSystemDetectionResult:
    """
    综合解析结果 + 目录扫描结果，判断真实构建系统。

    优先级：
    1. CMake
    2. Meson
    3. Autotools
    4. Make
    5. Python
    6. Node

    说明：
    - 以“真实文件结构”为准
    - 解析结果只作为 fallback
    """
    project_path = os.path.abspath(project_path)
    scanned = scan_build_files(project_path)

    has_cmake = len(scanned["cmake"]) > 0 or _has_top_level_marker(project_path, _root_markers()["cmake"])
    has_meson = len(scanned["meson"]) > 0 or _has_top_level_marker(project_path, _root_markers()["meson"])
    has_autotools = len(scanned["autotools"]) > 0 or _has_top_level_marker(project_path, _root_markers()["autotools"])
    has_make = len(scanned["make"]) > 0 or _has_top_level_marker(project_path, _root_markers()["make"])
    has_python = len(scanned["python"]) > 0 or _has_top_level_marker(project_path, _root_markers()["python"])
    has_node = len(scanned["node"]) > 0 or _has_top_level_marker(project_path, _root_markers()["node"])

    parsed = normalize_build_system_name(parsed_build_system)
    info = build_root_info or {}
    if not isinstance(info, dict):
        info = {}

    # CMake
    if has_cmake:
        rel = choose_best_build_root(project_path, scanned["cmake"]) if scanned["cmake"] else "."
        return BuildSystemDetectionResult(
            build_system="cmake",
            confidence=0.99 if parsed == "cmake" else 0.98,
            build_root_rel=rel,
            reason="Detected CMakeLists.txt / CMakePresets.json in project tree",
            scanned_files=scanned,
        )

    # Meson
    if has_meson:
        rel = choose_best_build_root(project_path, scanned["meson"]) if scanned["meson"] else "."
        return BuildSystemDetectionResult(
            build_system="meson",
            confidence=0.99 if parsed == "meson" else 0.95,
            build_root_rel=rel,
            reason="Detected meson.build in project tree",
            scanned_files=scanned,
        )

    # Autotools
    if has_autotools:
        rel = choose_best_build_root(project_path, scanned["autotools"]) if scanned["autotools"] else "."
        return BuildSystemDetectionResult(
            build_system="autotools",
            confidence=0.98 if parsed == "autotools" else 0.92,
            build_root_rel=rel,
            reason="Detected Autotools files in project tree",
            scanned_files=scanned,
        )

    # Make
    if has_make:
        rel = choose_best_build_root(project_path, scanned["make"]) if scanned["make"] else "."
        return BuildSystemDetectionResult(
            build_system="make",
            confidence=0.97 if parsed == "make" else 0.90,
            build_root_rel=rel,
            reason="Detected Makefile / GNUmakefile in project tree",
            scanned_files=scanned,
        )

    # Python
    if has_python:
        rel = choose_best_build_root(project_path, scanned["python"]) if scanned["python"] else "."
        return BuildSystemDetectionResult(
            build_system="python",
            confidence=0.97 if parsed == "python" else 0.88,
            build_root_rel=rel,
            reason="Detected Python project files in project tree",
            scanned_files=scanned,
        )

    # Node
    if has_node:
        rel = choose_best_build_root(project_path, scanned["node"]) if scanned["node"] else "."
        return BuildSystemDetectionResult(
            build_system="node",
            confidence=0.97 if parsed == "node" else 0.88,
            build_root_rel=rel,
            reason="Detected Node.js project files in project tree",
            scanned_files=scanned,
        )

    # fallback
    if parsed in {"cmake", "meson", "autotools", "make", "python", "node"}:
        return BuildSystemDetectionResult(
            build_system=parsed,
            confidence=0.55,
            build_root_rel=str(info.get("source_dir_rel", ".") or "."),
            reason="Using parsed build_system as fallback",
            scanned_files=scanned,
        )

    return BuildSystemDetectionResult(
        build_system="unknown",
        confidence=0.0,
        build_root_rel=str(info.get("source_dir_rel", ".") or "."),
        reason="No supported build system files found",
        scanned_files=scanned,
    )

def normalize_parsed_project(parsed_project: Dict[str, Any], project_path: str) -> Dict[str, Any]:
    """
    对 parse_project() 的结果做二次修正，避免误判。
    """
    if not isinstance(parsed_project, dict):
        return parsed_project

    detected = detect_build_system(
        project_path=project_path,
        parsed_build_system=parsed_project.get("build_system"),
        build_root_info=parsed_project.get("build_root_info", {}),
    )

    parsed_project["build_system"] = detected.build_system
    parsed_project["build_root_info"] = {
        **(parsed_project.get("build_root_info", {}) or {}),
        "build_system": detected.build_system,
        "source_dir_rel": detected.build_root_rel,
        "confidence": detected.confidence,
        "reason": detected.reason,
        "scanned_files": detected.scanned_files,
    }

    return parsed_project