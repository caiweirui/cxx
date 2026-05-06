import os
import re
from typing import Dict, List

BUILD_SYSTEM_RULES = {
    "CMake": {
        "files": ["CMakeLists.txt"],
        "keywords": [r"cmake_minimum_required\s*\(", r"project\s*\(", r"add_executable\s*\(", r"add_library\s*\("],
        "default_command": "cmake -S . -B build && cmake --build build -j$(nproc)",
    },
    "Makefile": {
        "files": ["Makefile", "makefile", "GNUmakefile"],
        "keywords": [r"^all\s*:", r"^install\s*:", r"^\w+\s*:\s*", r"^CC\s*=", r"^CXX\s*="],
        "default_command": "make -j$(nproc)",
    },
    "Meson": {
        "files": ["meson.build"],
        "keywords": [r"project\s*\(", r"dependency\s*\(", r"executable\s*\(", r"library\s*\("],
        "default_command": "meson setup build && meson compile -C build",
    },
    "Autotools": {
        "files": ["configure.ac", "configure.in", "autogen.sh"],
        "keywords": [r"AC_INIT\s*\(", r"AM_INIT_AUTOMAKE\s*\(", r"AC_CONFIG_FILES\s*\("],
        "default_command": "./autogen.sh && ./configure && make -j$(nproc)",
    },
    "Bazel": {
        "files": ["WORKSPACE", "WORKSPACE.bazel", "BUILD", "BUILD.bazel"],
        "keywords": [r"cc_binary\s*\(", r"cc_library\s*\(", r"load\s*\("],
        "default_command": "bazel build //...",
    },
    "SCons": {
        "files": ["SConstruct", "SConscript"],
        "keywords": [r"Environment\s*\(", r"Program\s*\(", r"Library\s*\("],
        "default_command": "scons -j$(nproc)",
    },
    "Ninja": {
        "files": ["build.ninja"],
        "keywords": [r"^rule\s+", r"^build\s+"],
        "default_command": "ninja -C build",
    },
}

def _read_text(path: str, limit: int = 12000) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read(limit)
    except Exception:
        return ""

def _scan_files(project_dir: str) -> List[str]:
    found = []
    for root, _, files in os.walk(project_dir):
        for file in files:
            found.append(os.path.relpath(os.path.join(root, file), project_dir))
    return found

def detect_build_system(project_dir: str) -> Dict:
    """
    返回结构化的构建系统识别结果：
    {
      "primary": "CMake",
      "entry": "CMakeLists.txt",
      "default_command": "...",
      "confidence": 0.92,
      "candidates": [...]
    }
    """
    project_dir = os.path.abspath(project_dir)
    all_files = _scan_files(project_dir)

    candidates = []
    for system, rule in BUILD_SYSTEM_RULES.items():
        score = 0
        hit_files = []
        hit_keywords = []

        # 文件命中
        for marker in rule["files"]:
            for rel_path in all_files:
                if os.path.basename(rel_path).lower() == marker.lower():
                    score += 4
                    hit_files.append(rel_path)

        # 关键字命中
        for rel_path in hit_files[:3]:
            content = _read_text(os.path.join(project_dir, rel_path))
            for kw in rule["keywords"]:
                if re.search(kw, content, re.I | re.M):
                    score += 2
                    hit_keywords.append(kw)

        if hit_files:
            score += min(len(hit_files), 3)

        if score > 0:
            candidates.append({
                "system": system,
                "score": score,
                "entry_files": hit_files[:5],
                "keywords": hit_keywords[:5],
                "default_command": rule["default_command"],
            })

    if not candidates:
        return {
            "primary": "Unknown",
            "entry": "",
            "default_command": "echo 'Unknown build system'",
            "confidence": 0.0,
            "candidates": [],
        }

    candidates.sort(key=lambda x: x["score"], reverse=True)
    top = candidates[0]
    confidence = min(0.98, 0.45 + top["score"] / 12.0)
    entry = top["entry_files"][0] if top["entry_files"] else ""

    return {
        "primary": top["system"],
        "entry": entry,
        "default_command": top["default_command"],
        "confidence": round(confidence, 2),
        "candidates": candidates,
    }

def order_build_system(project_dir: str):
    """
    旧接口兼容别名。
    原项目/旧版本代码可能调用 order_build_system()。
    """
    info = detect_build_system(project_dir)
    return info.get("primary", "Unknown"), info.get("entry", "")