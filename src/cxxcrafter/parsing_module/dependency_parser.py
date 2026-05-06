import os
import re
from typing import Dict, List, Any

def _read_text(path: str, limit: int = 20000) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read(limit)
    except Exception:
        return ""

def _add_dep(deps: Dict[str, Dict[str, Any]], name: str, version: str = "", source: str = "unknown", evidence: str = ""):
    key = name.strip().lower()
    if not key:
        return
    if key not in deps:
        deps[key] = {
            "name": name.strip(),
            "version": version.strip(),
            "source": source,
            "evidence": evidence[:300],
        }
    else:
        if version and not deps[key]["version"]:
            deps[key]["version"] = version.strip()
        if source != "unknown" and deps[key]["source"] == "unknown":
            deps[key]["source"] = source
        if evidence and not deps[key]["evidence"]:
            deps[key]["evidence"] = evidence[:300]

def _parse_from_text(text: str, source_hint: str) -> Dict[str, Dict[str, Any]]:
    deps = {}

    if not text:
        return deps

    # CMake / Meson / Bazel / SCons 常见依赖表达
    patterns = [
        (r"find_package\s*\(\s*([A-Za-z0-9_\-+\.]+)(?:\s+([0-9][^)\s]*))?", "cmake"),
        (r"dependency\s*\(\s*['\"]([^'\"]+)['\"](?:\s*,\s*version\s*:\s*['\"]([^'\"]+)['\"])?", "meson"),
        (r"pkg_check_modules\s*\(\s*([A-Za-z0-9_\-]+)\s+([^)]*)\)", "pkg-config"),
        (r"target_link_libraries\s*\([^)]*?([A-Za-z0-9_\-]+)\s*\)", "cmake"),
        (r"cc_library\s*\(", "bazel"),
    ]

    for pat, source in patterns:
        for m in re.finditer(pat, text, re.I | re.M):
            if pat == r"cc_library\s*\(":
                continue
            name = m.group(1).strip()
            version = m.group(2).strip() if m.lastindex and m.lastindex >= 2 and m.group(2) else ""
            _add_dep(deps, name, version=version, source=source, evidence=m.group(0))

    # README / INSTALL 中的 apt / dnf / yum / pacman / brew
    pkg_patterns = [
        (r"(?:apt(?:-get)?\s+install\s+-y\s+)([a-zA-Z0-9_\-\.\+]+)", "apt"),
        (r"(?:dnf\s+install\s+-y\s+)([a-zA-Z0-9_\-\.\+]+)", "dnf"),
        (r"(?:yum\s+install\s+-y\s+)([a-zA-Z0-9_\-\.\+]+)", "yum"),
        (r"(?:pacman\s+-S\s+)([a-zA-Z0-9_\-\.\+]+)", "pacman"),
        (r"(?:brew\s+install\s+)([a-zA-Z0-9_\-\.\+]+)", "brew"),
        (r"(?:vcpkg\s+install\s+)([a-zA-Z0-9_\-\.\+]+)", "vcpkg"),
        (r"(?:conan\s+install\s+)([a-zA-Z0-9_\-\.\+]+)", "conan"),
    ]

    for pat, source in pkg_patterns:
        for m in re.finditer(pat, text, re.I | re.M):
            _add_dep(deps, m.group(1), source=source, evidence=m.group(0))

    # 一些常见库名的提示词
    common_libs = [
        "opencv", "boost", "fmt", "qt", "gtk", "gtest", "eigen", "protobuf",
        "zlib", "ssl", "openssl", "curl", "sqlite3", "ffmpeg", "sdl2",
        "libpng", "libjpeg", "json", "yaml", "tbb", "pthread"
    ]
    lower = text.lower()
    for name in common_libs:
        if name in lower:
            _add_dep(deps, name, source=source_hint, evidence=f"keyword:{name}")

    return deps

def extract_dependencies(project_dir: str, docs_text: str = "") -> List[Dict[str, Any]]:
    """
    返回统一依赖列表，供 cli / generator 使用
    """
    project_dir = os.path.abspath(project_dir)
    deps_map: Dict[str, Dict[str, Any]] = {}

    # 构建文件
    build_files = [
        "CMakeLists.txt", "meson.build", "configure.ac", "configure.in",
        "Makefile", "GNUmakefile", "build.ninja", "SConstruct", "SConscript",
        "WORKSPACE", "WORKSPACE.bazel", "BUILD", "BUILD.bazel"
    ]

    for root, _, files in os.walk(project_dir):
        for file in files:
            if file in build_files or file.lower().startswith("readme") or file.lower() in ["install", "building", "contributing"]:
                content = _read_text(os.path.join(root, file))
                parsed = _parse_from_text(content, source_hint=file)
                for _, dep in parsed.items():
                    _add_dep(deps_map, dep["name"], dep["version"], dep["source"], dep["evidence"])

    if docs_text:
        parsed = _parse_from_text(docs_text, source_hint="docs")
        for _, dep in parsed.items():
            _add_dep(deps_map, dep["name"], dep["version"], dep["source"], dep["evidence"])

    return list(deps_map.values())