# src/cxxcrafter/agents/dependency_agent.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Set, Tuple

from .base_agent import BaseAgent

def _as_str_list(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(x) for x in value if str(x).strip()]
    if isinstance(value, tuple):
        return [str(x) for x in value if str(x).strip()]
    if isinstance(value, set):
        return [str(x) for x in value if str(x).strip()]
    return [str(value)]

def _project_text(snapshot: Dict[str, Any]) -> str:
    parts: List[str] = []
    project_name = str(snapshot.get("project_name", "") or "")
    build_system = str(snapshot.get("build_system", "") or "")
    files_sample = snapshot.get("files_sample", []) or []
    rule_notes = snapshot.get("rule_notes", []) or []

    parts.append(project_name)
    parts.append(build_system)

    if isinstance(files_sample, list):
        parts.extend([str(x) for x in files_sample[:300]])
    else:
        parts.append(str(files_sample))

    if isinstance(rule_notes, list):
        parts.extend([str(x) for x in rule_notes[:100]])

    return "\n".join(parts).lower()

def _contains_any(haystack: str, keywords: List[str]) -> bool:
    low = haystack.lower()
    return any(k.lower() in low for k in keywords)

def _has_any_file(files_sample: List[str], keywords: List[str]) -> bool:
    low_files = [str(x).lower() for x in files_sample]
    for f in low_files:
        if any(k.lower() in f for k in keywords):
            return True
    return False

def _merge_unique(*lists: List[str]) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for lst in lists:
        if not lst:
            continue
        for item in lst:
            s = str(item).strip()
            if not s or s in seen:
                continue
            seen.add(s)
            out.append(s)
    return out

def _infer_build_flavor(snapshot: Dict[str, Any]) -> str:
    """
    扩展 build_system 识别：
    - cmake
    - make
    - node
    - python
    - meson
    - autotools
    - unknown
    """
    build_system = str(snapshot.get("build_system", "") or "").lower()
    files_sample = snapshot.get("files_sample", []) or []
    text = _project_text(snapshot)

    if build_system in {"cmake", "make", "node", "python"}:
        return build_system

    if _has_any_file(files_sample, ["meson.build"]):
        return "meson"

    if _has_any_file(files_sample, ["configure.ac", "autogen.sh", "Makefile.am", "Makefile.in"]):
        return "autotools"

    # 兜底判断
    if "meson" in text:
        return "meson"
    if "autoconf" in text or "automake" in text:
        return "autotools"

    return "unknown"

def _infer_project_family(snapshot: Dict[str, Any]) -> Tuple[str, List[str]]:
    """
    输出：
    - family
    - feature tags
    """
    text = _project_text(snapshot)
    files_sample = snapshot.get("files_sample", []) or []

    tags: Set[str] = set()

    # GUI / Desktop
    qt_keywords = [
        "qt", "qwidget", "qmainwindow", "qapplication", "qt5", "qt6",
        ".ui", ".qrc", "qml", "moc_", "uic", "flameshot", "qtcore",
    ]
    gtk_keywords = [
        "gtk", "gtkmm", "gdk", "glib", "gobject", "cairo", "pango",
        "geany", "libadwaita", "gtk3", "gtk4",
    ]
    x11_keywords = [
        "x11", "xcb", "xlib", "xext", "xrandr", "xcursor", "xi",
        "xinerama", "xkbcommon", "wayland", "dbus", "org.freedesktop",
    ]

    # Media / Audio
    audio_keywords = [
        "audio", "sound", "alsa", "pulse", "puls", "sndfile", "vorbis",
        "flac", "ogg", "opus", "samplerate", "rnnoise", "aubio",
        "ffmpeg", "libavcodec", "libavformat", "libavutil", "libswresample",
    ]

    # Image / CV
    image_keywords = [
        "jpeg", "png", "tiff", "webp", "avif", "opencv", "image",
        "stb_image", "simd", "turbojpeg", "libjpeg",
    ]

    # Network / server
    network_keywords = [
        "http", "web", "server", "socket", "ssl", "tls", "curl", "asio",
        "oatpp", "boost", "grpc", "protobuf", "rest", "websocket",
        "openssl",
    ]

    # Graphics / rendering
    graphics_keywords = [
        "raylib", "glfw", "opengl", "glad", "vulkan", "sdl",
        "sfml", "render", "shader", "graphics", "imgui",
    ]

    # Utility / CLI
    cli_keywords = [
        "cli", "command line", "terminal", "tool", "utility", "mold",
        "8cc", "guetzli", "polybar",
    ]

    # Feature tags
    if _contains_any(text, qt_keywords):
        tags.update(["qt", "gui", "desktop"])
    if _contains_any(text, gtk_keywords):
        tags.update(["gtk", "gui", "desktop"])
    if _contains_any(text, x11_keywords):
        tags.add("x11")
    if _contains_any(text, audio_keywords):
        tags.update(["audio", "media"])
    if _contains_any(text, image_keywords):
        tags.update(["image"])
    if _contains_any(text, network_keywords):
        tags.update(["network"])
    if _contains_any(text, graphics_keywords):
        tags.update(["graphics"])
    if _contains_any(text, cli_keywords):
        tags.update(["cli"])

    # 文件级别增强
    if _has_any_file(files_sample, ["qt", ".ui", ".qrc"]):
        tags.update(["qt", "gui", "desktop"])
    if _has_any_file(files_sample, ["gtk", "gdk", "glib", "pango", "cairo"]):
        tags.update(["gtk", "gui", "desktop"])
    if _has_any_file(files_sample, ["tests/", "test/", "/test", "gtest", "catch2", "doctest"]):
        tags.add("tests")

    # family 判定顺序
    if "qt" in tags and "gui" in tags:
        return "qt_gui", sorted(tags)
    if "gtk" in tags and "gui" in tags:
        return "gtk_gui", sorted(tags)
    if "audio" in tags or "media" in tags:
        return "audio_media", sorted(tags)
    if "image" in tags:
        return "image_processing", sorted(tags)
    if "network" in tags:
        return "network_server", sorted(tags)
    if "graphics" in tags:
        return "graphics", sorted(tags)
    if "cli" in tags:
        return "cli_tool", sorted(tags)

    build_flavor = _infer_build_flavor(snapshot)
    if build_flavor == "python":
        return "python_app", sorted(tags)
    if build_flavor == "node":
        return "node_app", sorted(tags)
    if build_flavor == "meson":
        return "native_meson", sorted(tags)
    if build_flavor == "autotools":
        return "native_autotools", sorted(tags)

    if str(snapshot.get("project_name", "")).lower().startswith("lib"):
        tags.add("library")
        return "library", sorted(tags)

    return "generic", sorted(tags)

def _family_packages(family: str) -> Tuple[List[str], List[str]]:
    """
    返回：
    - apt packages
    - notes
    """
    apt: List[str] = []
    notes: List[str] = []

    if family == "qt_gui":
        apt += [
            "qtbase5-dev",
            "qttools5-dev-tools",
            "qtchooser",
            "qt5-qmake",
            "qtbase5-private-dev",
            "libqt5svg5-dev",
            "libqt5x11extras5-dev",
            "libx11-dev",
            "libxext-dev",
            "libxrender-dev",
            "libxrandr-dev",
            "libxcursor-dev",
            "libxi-dev",
            "libxkbcommon-x11-dev",
            "libdbus-1-dev",
            "libwayland-dev",
        ]
        notes.append("Detected Qt desktop GUI project")

    elif family == "gtk_gui":
        apt += [
            "libgtk-3-dev",
            "libgtk-4-dev",
            "libglib2.0-dev",
            "libgdk-pixbuf-2.0-dev",
            "libcairo2-dev",
            "libpango1.0-dev",
            "libdbus-1-dev",
            "libx11-dev",
            "libxext-dev",
            "libxrender-dev",
            "libxrandr-dev",
            "libxcursor-dev",
            "libxi-dev",
            "libxkbcommon-x11-dev",
            "libwayland-dev",
        ]
        notes.append("Detected GTK desktop GUI project")

    elif family == "audio_media":
        apt += [
            "libasound2-dev",
            "libpulse-dev",
            "libsndfile1-dev",
            "libsamplerate0-dev",
            "libogg-dev",
            "libvorbis-dev",
            "libflac-dev",
            "libopus-dev",
            "ffmpeg",
            "libavcodec-dev",
            "libavformat-dev",
            "libavutil-dev",
            "libswresample-dev",
        ]
        notes.append("Detected audio/media project")

    elif family == "image_processing":
        apt += [
            "libjpeg-dev",
            "libpng-dev",
            "libtiff-dev",
            "libwebp-dev",
            "libopenjp2-7-dev",
            "libavif-dev",
            "zlib1g-dev",
        ]
        notes.append("Detected image-processing project")

    elif family == "network_server":
        apt += [
            "libssl-dev",
            "libcurl4-openssl-dev",
            "zlib1g-dev",
            "libsqlite3-dev",
            "libboost-all-dev",
            "protobuf-compiler",
            "libprotobuf-dev",
        ]
        notes.append("Detected network/server project")

    elif family == "graphics":
        apt += [
            "libgl1-mesa-dev",
            "libglu1-mesa-dev",
            "libglew-dev",
            "libglfw3-dev",
            "libx11-dev",
            "libxrandr-dev",
            "libxcursor-dev",
            "libxi-dev",
            "libxinerama-dev",
            "libxxf86vm-dev",
            "libasound2-dev",
        ]
        notes.append("Detected graphics/rendering project")

    elif family == "cli_tool":
        apt += [
            "libreadline-dev",
            "libncurses5-dev",
            "bison",
            "flex",
            "gettext",
            "zlib1g-dev",
        ]
        notes.append("Detected CLI/tooling project")

    elif family == "library":
        notes.append("Detected library-oriented project")

    elif family == "python_app":
        notes.append("Detected Python project")

    elif family == "node_app":
        notes.append("Detected Node project")

    elif family == "native_meson":
        notes.append("Detected Meson-based native project")

    elif family == "native_autotools":
        notes.append("Detected Autotools-based native project")

    else:
        notes.append("Generic native project")

    return apt, notes

def _buildsystem_packages(build_flavor: str) -> Tuple[List[str], List[str]]:
    apt: List[str] = []
    notes: List[str] = []

    if build_flavor == "cmake":
        apt += ["cmake", "build-essential", "ninja-build", "pkg-config"]
        notes.append("Detected CMake project")
    elif build_flavor == "make":
        apt += ["build-essential", "make", "pkg-config"]
        notes.append("Detected Makefile project")
    elif build_flavor == "node":
        apt += ["nodejs", "npm"]
        notes.append("Detected Node project")
    elif build_flavor == "python":
        apt += ["python3", "python3-pip", "python3-venv"]
        notes.append("Detected Python project")
    elif build_flavor == "meson":
        apt += ["meson", "ninja-build", "build-essential", "pkg-config"]
        notes.append("Detected Meson project")
    elif build_flavor == "autotools":
        apt += ["build-essential", "autoconf", "automake", "libtool", "pkg-config", "make"]
        notes.append("Detected Autotools project")
    else:
        apt += ["build-essential", "pkg-config"]
        notes.append("Using generic native build toolchain")

    return apt, notes

@dataclass
class DependencyAnalysis:
    apt_packages: List[str] = field(default_factory=list)
    pip_packages: List[str] = field(default_factory=list)
    project_packages: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    cmake_args: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    confidence: float = 0.0
    raw: Dict[str, Any] = field(default_factory=dict)

    # 新增字段：不影响旧代码
    project_family: str = "generic"
    feature_tags: List[str] = field(default_factory=list)

class DependencyAgent(BaseAgent):
    """
    输入：项目快照（规则层已经提取出来的信息）
    输出：依赖分析 JSON

    这一版增强点：
    - 更细的项目类型识别
    - 针对 Qt / GTK / 图形 / 音频 / 网络 / 图像 / Autotools / Meson 补依赖
    - 结果会明显比“统一模板”更有差异
    """

    def analyze(self, snapshot: Dict[str, Any]) -> DependencyAnalysis:
        # 1) 规则优先：基础依赖
        base_apt = set(_as_str_list(snapshot.get("rule_apt_packages", [])))
        base_pip = set(_as_str_list(snapshot.get("rule_pip_packages", [])))
        base_env = dict(snapshot.get("rule_env", {}) or {})
        base_args = list(_as_str_list(snapshot.get("rule_cmake_args", [])))
        base_notes = list(_as_str_list(snapshot.get("rule_notes", [])))

        # 2) 识别项目 family / tags / build flavor
        family, tags = _infer_project_family(snapshot)
        build_flavor = _infer_build_flavor(snapshot)

        family_apt, family_notes = _family_packages(family)
        flavor_apt, flavor_notes = _buildsystem_packages(build_flavor)

        # 3) 让 LLM 补充少量依赖，但不要覆盖规则结果
        prompt = f"""
你是依赖分析智能体，只输出 JSON，不要输出解释文字。

目标：根据项目快照补充依赖列表。
要求：
1. 只补充必要依赖，避免过度安装
2. 优先服从项目类型、build system 和现有规则结果
3. 如果项目明显是 GUI / 图形 / 音频 / 网络 / 图像项目，请补充对应系统库
4. 如果信息不足，宁可保守，不要乱猜

JSON 结构：
{{
  "apt_packages": ["..."],
  "pip_packages": ["..."],
  "project_packages": ["..."],
  "env": {{"KEY": "VALUE"}},
  "cmake_args": ["..."],
  "notes": ["..."],
  "confidence": 0.0
}}

项目 family：
{family}

项目 tags：
{tags}

build flavor：
{build_flavor}

项目快照：
{snapshot}
""".strip()

        default = {
            "apt_packages": [],
            "pip_packages": [],
            "project_packages": [],
            "env": {},
            "cmake_args": [],
            "notes": [],
            "confidence": 0.0,
        }

        resp = self.generate_json(prompt, default=default)
        data = resp.data or {}

        # 4) 合并
        apt_packages = _merge_unique(
            sorted(base_apt),
            family_apt,
            flavor_apt,
            _as_str_list(data.get("apt_packages", [])),
        )

        pip_packages = _merge_unique(
            sorted(base_pip),
            _as_str_list(data.get("pip_packages", [])),
        )

        project_packages = _merge_unique(
            _as_str_list(data.get("project_packages", [])),
        )

        env = {**base_env, **(data.get("env", {}) or {})}

        cmake_args = list(dict.fromkeys(
            base_args + _as_str_list(data.get("cmake_args", []))
        ))

        notes = _merge_unique(
            base_notes,
            family_notes,
            flavor_notes,
            _as_str_list(data.get("notes", [])),
        )

        # 5) 置信度
        try:
            confidence = float(data.get("confidence", 0.0) or 0.0)
        except Exception:
            confidence = 0.0

        # 6) 对某些 family 给一点更明确的提示
        if family in {"qt_gui", "gtk_gui"}:
            env.setdefault("QT_QPA_PLATFORM", "offscreen") if family == "qt_gui" else None
            notes.append("GUI project detected; verification may need a non-interactive runtime command.")
        if family == "library":
            notes.append("Library-like project; keep runtime verification conservative.")
        if build_flavor in {"meson", "autotools"}:
            notes.append(f"{build_flavor.capitalize()} build flow enabled.")

        return DependencyAnalysis(
            apt_packages=apt_packages,
            pip_packages=pip_packages,
            project_packages=project_packages,
            env=env,
            cmake_args=cmake_args,
            notes=_merge_unique(notes),
            confidence=confidence,
            raw=data,
            project_family=family,
            feature_tags=tags,
        )