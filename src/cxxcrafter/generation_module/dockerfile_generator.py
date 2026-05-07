# src/cxxcrafter/generation_module/dockerfile_generator.py
from __future__ import annotations

import json
import logging
import re
import shlex
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from cxxcrafter.agents.build_agent import BuildPlan
from cxxcrafter.agents.dependency_agent import DependencyAnalysis

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def _dedupe(seq: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in seq:
        item = str(item).strip()
        if not item:
            continue
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


class DockerfileGenerator:
    """
    规则化 Dockerfile 生成器。

    这版针对批量测试中暴露的问题做了修正：
    - 预安装依赖永远在 WORKDIR 根目录执行，避免 cd 到子目录后执行 apt-get
    - source_root_rel 仅用于 COPY 与构建命令定位，不污染依赖安装步骤
    - COPY 使用 JSON array 形式，兼容空格路径、反斜杠、Windows 路径
    - CMake 项目在子目录下执行时，自动修正 -S 参数
    - Autotools 项目自动规避 CRLF / bad interpreter
    - 识别常见缺失工具包，例如 sphinx-build -> python3-sphinx
    """

    DEFAULT_APT_MIRROR = "http://mirrors.tuna.tsinghua.edu.cn/ubuntu"
    DEFAULT_APT_RETRIES = 5
    DEFAULT_APT_HTTP_TIMEOUT = 30

    def __init__(self, project_root: str, default_base_image: str = "ubuntu:22.04") -> None:
        self.project_root = Path(project_root).resolve()
        self.default_base_image = default_base_image

    def render(self, deps: DependencyAnalysis, plan: BuildPlan, snapshot: Dict[str, Any]) -> str:
        base_image = (getattr(plan, "base_image", None) or self.default_base_image or "ubuntu:22.04").strip()
        source_root_rel = self._normalize_rel_path(snapshot.get("source_root_rel", "."))
        build_system = str(snapshot.get("build_system", "unknown") or "unknown").strip().lower()
        required_cmake_version = str(snapshot.get("required_cmake_version") or "").strip()

        requires_qt6 = bool(snapshot.get("requires_qt6", False))
        requires_boost = bool(snapshot.get("requires_boost", False))
        requires_x11 = bool(snapshot.get("requires_x11", False))
        requires_opengl = bool(snapshot.get("requires_opengl", False))
        is_gui_project = bool(snapshot.get("is_gui_project", False))

        project_family = (
            getattr(plan, "project_family", "")
            or getattr(deps, "project_family", "")
            or "generic"
        ).strip()

        feature_tags = _dedupe(
            list(getattr(plan, "feature_tags", []) or [])
            + list(getattr(deps, "feature_tags", []) or [])
        )

        apt_packages = _dedupe(list(getattr(deps, "apt_packages", []) or []))
        pip_packages = _dedupe(list(getattr(deps, "pip_packages", []) or []))
        preinstall_commands = _dedupe(list(getattr(plan, "preinstall_commands", []) or []))
        build_commands = _dedupe(list(getattr(plan, "build_commands", []) or []))
        test_commands = _dedupe(list(getattr(plan, "test_commands", []) or []))
        copy_paths = _dedupe(list(getattr(plan, "copy_paths", []) or []))
        if not copy_paths:
            copy_paths = [source_root_rel or "."]

        # pip 包存在时，补齐 Python 运行环境
        if pip_packages:
            apt_packages = _dedupe(["python3", "python3-pip", "python3-venv"] + apt_packages)

        # 从命令和备注中推断额外依赖
        inferred = self._infer_packages_from_commands(
            build_commands=build_commands,
            test_commands=test_commands,
            preinstall_commands=preinstall_commands,
            notes=list(getattr(plan, "notes", []) or []) + list(getattr(deps, "notes", []) or []),
            source_root_rel=source_root_rel,
        )
        apt_packages = _dedupe(apt_packages + inferred)

        # snapshot 推断出的额外依赖
        if requires_qt6:
            apt_packages = _dedupe(apt_packages + ["qt6-base-dev", "qt6-tools-dev", "qt6-tools-dev-tools"])
        if requires_boost:
            apt_packages = _dedupe(apt_packages + ["libboost-all-dev"])
        if requires_x11 or is_gui_project:
            apt_packages = _dedupe(
                apt_packages
                + [
                    "libx11-dev",
                    "libxext-dev",
                    "libxrender-dev",
                    "libxrandr-dev",
                    "libxcursor-dev",
                    "libxi-dev",
                    "libxkbcommon-x11-dev",
                    "libxinerama-dev",
                    "libwayland-dev",
                    "xorg-dev",
                ]
            )
        if requires_opengl or is_gui_project:
            apt_packages = _dedupe(
                apt_packages
                + [
                    "libgl1-mesa-dev",
                    "libglu1-mesa-dev",
                    "libglew-dev",
                    "libglfw3-dev",
                ]
            )

        # CMake 版本要求高于 Ubuntu 22.04 自带版本时，先升级 cmake
        cmake_upgrade_step = ""
        if required_cmake_version and self._version_gt(required_cmake_version, "3.22.1"):
            apt_packages = _dedupe(["python3", "python3-pip", "python3-venv"] + apt_packages)
            cmake_upgrade_step = self._render_run(
                f'python3 -m pip install --no-cache-dir "cmake>={required_cmake_version}"'
            )

        # 基础工具兜底
        apt_packages = _dedupe(["ca-certificates", "curl", "git", "pkg-config"] + apt_packages)

        workdir = self._normalize_workdir(getattr(plan, "workdir", None) or "/workspace")

        lines: List[str] = []
        lines.append(f"FROM {base_image}")
        lines.append("ENV DEBIAN_FRONTEND=noninteractive")
        lines.append('SHELL ["/bin/bash", "-o", "pipefail", "-c"]')
        lines.append(f"WORKDIR {workdir}")

        lines.append(f'LABEL org.cxxcrafter.project_name="{self._escape(str(snapshot.get("project_name", "") or ""))}"')
        lines.append(f'LABEL org.cxxcrafter.project_family="{self._escape(project_family)}"')
        lines.append(f'LABEL org.cxxcrafter.build_system="{self._escape(build_system)}"')
        lines.append(f'LABEL org.cxxcrafter.source_root_rel="{self._escape(source_root_rel)}"')
        lines.append(f'LABEL org.cxxcrafter.required_cmake_version="{self._escape(required_cmake_version)}"')
        lines.append(f'LABEL org.cxxcrafter.requires_qt6="{str(requires_qt6).lower()}"')
        lines.append(f'LABEL org.cxxcrafter.requires_boost="{str(requires_boost).lower()}"')
        lines.append(f'LABEL org.cxxcrafter.requires_x11="{str(requires_x11).lower()}"')
        lines.append(f'LABEL org.cxxcrafter.requires_opengl="{str(requires_opengl).lower()}"')
        lines.append(f'LABEL org.cxxcrafter.is_gui_project="{str(is_gui_project).lower()}"')
        if feature_tags:
            lines.append(f'LABEL org.cxxcrafter.feature_tags="{self._escape(",".join(feature_tags))}"')

        if getattr(deps, "notes", None):
            lines.append(f"# dependency_notes: {self._escape(' | '.join(_dedupe(deps.notes)[:12]))}")
        if getattr(plan, "notes", None):
            lines.append(f"# build_notes: {self._escape(' | '.join(_dedupe(plan.notes)[:12]))}")

        apt_step = self._render_apt_setup_step(apt_packages)
        if apt_step:
            lines.append(apt_step)

        if cmake_upgrade_step:
            lines.append(cmake_upgrade_step)

        # 环境变量先写入，便于后续命令读取
        for k, v in (getattr(plan, "env", {}) or {}).items():
            lines.append(f'ENV {k}="{self._escape(v)}"')
        for k, v in (getattr(deps, "env", {}) or {}).items():
            if k not in (getattr(plan, "env", {}) or {}):
                lines.append(f'ENV {k}="{self._escape(v)}"')

        # COPY 使用 JSON array 形式，避免空格 / 反斜杠 / 解析错误
        for src in copy_paths:
            copy_line = self._render_copy_line(src, workdir)
            if copy_line:
                lines.append(copy_line)

        # Autotools 项目：先处理 CRLF，再执行 autogen/configure
        if build_system == "autotools":
            lines.append(
                self._render_run(
                    'if [ -f ./autogen.sh ]; then sed -i "s/\\r$//" ./autogen.sh 2>/dev/null || true; sh ./autogen.sh; fi'
                )
            )
            lines.append(
                self._render_run(
                    'if [ -f ./configure ]; then sed -i "s/\\r$//" ./configure 2>/dev/null || true; sh ./configure --prefix=/usr; fi'
                )
            )

        # 预安装命令：绝不包裹 source_root_rel，确保 apt-get 在 WORKDIR 根目录执行
        for cmd in preinstall_commands:
            rendered = self._render_run(self._sanitize_command(cmd, source_root_rel, build_system, phase="preinstall"))
            if rendered:
                lines.append(rendered)

        # Python 包
        if pip_packages:
            lines.append(self._render_run("python3 -m pip install --no-cache-dir --upgrade pip setuptools wheel"))
            lines.append(self._render_run("python3 -m pip install --no-cache-dir " + " ".join(pip_packages)))

        # Meson 保底命令
        if build_system == "meson" and not build_commands:
            lines.append(self._render_run("meson setup build --buildtype=release"))
            lines.append(self._render_run("meson compile -C build"))

        # 构建命令：在 source_root_rel 下执行
        if not build_commands and build_system == "autotools":
            build_commands = ["make -j$(nproc)"]

        for cmd in build_commands:
            fixed = self._sanitize_command(cmd, source_root_rel, build_system, phase="build")
            rendered = self._render_run(fixed)
            if rendered:
                lines.append(rendered)

        # 测试命令：同样在 source_root_rel 下执行
        for cmd in test_commands:
            fixed = self._sanitize_command(cmd, source_root_rel, build_system, phase="test")
            rendered = self._render_run(fixed)
            if rendered:
                lines.append(rendered)

        runtime_command = str(getattr(plan, "runtime_command", "") or "").strip()
        if runtime_command:
            runtime_cmd = self._sanitize_command(runtime_command, source_root_rel, build_system, phase="runtime")
            lines.append(f'CMD ["sh", "-lc", "{self._escape(runtime_cmd)}"]')

        return "\n".join(lines) + "\n"

    def save(self, dockerfile_text: str, out_path: str) -> str:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(dockerfile_text, encoding="utf-8")
        return str(out)

    def _infer_packages_from_commands(
        self,
        *,
        build_commands: List[str],
        test_commands: List[str],
        preinstall_commands: List[str],
        notes: List[str],
        source_root_rel: str,
    ) -> List[str]:
        text = "\n".join(build_commands + test_commands + preinstall_commands + notes).lower()
        packages: List[str] = []

        # 常见工具缺失
        if "sphinx-build" in text or "python3 -m sphinx" in text:
            packages.append("python3-sphinx")
        if re.search(r"\bnasm\b", text):
            packages.append("nasm")
        if re.search(r"\byasm\b", text):
            packages.append("yasm")
        if re.search(r"\bdoxygen\b", text):
            packages.append("doxygen")
        if re.search(r"\bgraphviz\b", text):
            packages.append("graphviz")
        if re.search(r"\bccache\b", text):
            packages.append("ccache")

        # CMake / Ninja / Make / pkg-config / Python
        if re.search(r"\bcmake\b", text):
            packages.append("cmake")
            if source_root_rel != ".":
                packages.append("ninja-build")
        if re.search(r"\bninja\b", text):
            packages.append("ninja-build")
        if re.search(r"\bmake\b", text):
            packages.append("make")
        if re.search(r"\bpkg-config\b", text):
            packages.append("pkg-config")
        if re.search(r"\bpython3\b", text) or re.search(r"\bpip\b", text):
            packages.extend(["python3", "python3-pip", "python3-venv"])

        # Qt / Boost / X11 / OpenGL 兜底
        if "qt6" in text:
            packages.extend(["qt6-base-dev", "qt6-tools-dev", "qt6-tools-dev-tools"])
        if "qt5" in text:
            packages.extend(["qtbase5-dev", "qttools5-dev-tools", "qtchooser", "qt5-qmake", "libqt5svg5-dev", "libqt5x11extras5-dev"])
        if "boost" in text:
            packages.append("libboost-all-dev")
        if "x11" in text or "xrandr" in text or "xrender" in text or "xcb" in text:
            packages.extend(
                [
                    "libx11-dev",
                    "libxext-dev",
                    "libxrender-dev",
                    "libxrandr-dev",
                    "libxcursor-dev",
                    "libxi-dev",
                    "libxkbcommon-x11-dev",
                    "libxinerama-dev",
                    "libwayland-dev",
                    "xorg-dev",
                ]
            )
        if "opengl" in text or "glfw" in text or "glew" in text or "glu" in text:
            packages.extend(["libgl1-mesa-dev", "libglu1-mesa-dev", "libglew-dev", "libglfw3-dev"])

        return _dedupe(packages)

    def _render_apt_setup_step(self, apt_packages: List[str]) -> str:
        all_packages = _dedupe(apt_packages or [])
        if not all_packages:
            return ""

        pkg_lines = "\n".join([f"        {pkg} \\" for pkg in all_packages])

        return "\n".join(
            [
                f"ARG APT_SOURCE_MIRROR={self.DEFAULT_APT_MIRROR}",
                f"ARG APT_RETRIES={self.DEFAULT_APT_RETRIES}",
                f"ARG APT_HTTP_TIMEOUT={self.DEFAULT_APT_HTTP_TIMEOUT}",
                "RUN set -eux; \\",
                '    printf \'Acquire::Retries "%s";\\nAcquire::http::Timeout "%s";\\nAcquire::https::Timeout "%s";\\n\' "${APT_RETRIES}" "${APT_HTTP_TIMEOUT}" "${APT_HTTP_TIMEOUT}" > /etc/apt/apt.conf.d/80retry; \\',
                '    if [ -n "${APT_SOURCE_MIRROR:-}" ]; then \\',
                '        if [ -f /etc/apt/sources.list ]; then \\',
                '            sed -i -e "s|http://archive.ubuntu.com/ubuntu|${APT_SOURCE_MIRROR}|g" \\',
                '                   -e "s|https://archive.ubuntu.com/ubuntu|${APT_SOURCE_MIRROR}|g" \\',
                '                   -e "s|http://security.ubuntu.com/ubuntu|${APT_SOURCE_MIRROR}|g" \\',
                '                   -e "s|https://security.ubuntu.com/ubuntu|${APT_SOURCE_MIRROR}|g" /etc/apt/sources.list; \\',
                "        fi; \\",
                '        if [ -f /etc/apt/sources.list.d/ubuntu.sources ]; then \\',
                '            sed -i -e "s|http://archive.ubuntu.com/ubuntu|${APT_SOURCE_MIRROR}|g" \\',
                '                   -e "s|https://archive.ubuntu.com/ubuntu|${APT_SOURCE_MIRROR}|g" \\',
                '                   -e "s|http://security.ubuntu.com/ubuntu|${APT_SOURCE_MIRROR}|g" \\',
                '                   -e "s|https://security.ubuntu.com/ubuntu|${APT_SOURCE_MIRROR}|g" /etc/apt/sources.list.d/ubuntu.sources; \\',
                "        fi; \\",
                "    fi; \\",
                "    apt-get update; \\",
                "    apt-get install -y --no-install-recommends \\",
                pkg_lines,
                "    && rm -rf /var/lib/apt/lists/*",
            ]
        )

    def _render_copy_line(self, src: str, workdir: str) -> str:
        src = self._normalize_rel_path(src)
        workdir = self._normalize_workdir(workdir)

        if src == ".":
            return f'COPY {json.dumps([".", f"{workdir}/"], ensure_ascii=False)}'

        dest = f"{workdir}/{src}".replace("//", "/").rstrip("/")
        return f'COPY {json.dumps([src, dest], ensure_ascii=False)}'

    def _sanitize_command(self, cmd: str, source_root_rel: str, build_system: str, phase: str) -> str:
        """
        修正命令，避免常见失败模式：
        - preinstall 阶段绝不包裹 source_root_rel
        - build/test/runtime 阶段若 source_root_rel 非 .，在子目录执行
        - cmake -S 路径在子目录场景下尽量修正为 . 或绝对路径
        - 已经存在 cd 的命令不重复包裹
        """
        cmd = str(cmd or "").strip()
        source_root_rel = self._normalize_rel_path(source_root_rel)

        if not cmd:
            return ""

        if phase == "preinstall":
            return self._sanitize_preinstall_command(cmd)

        # 运行在 source_root_rel 下的命令
        if source_root_rel == ".":
            return self._sanitize_cmake_command(cmd, source_root_rel, phase)

        # 如果命令已经显式 cd 到目标目录，避免重复包裹
        if self._has_cd_prefix(cmd):
            return self._sanitize_cmake_command(cmd, source_root_rel, phase)

        wrapped = f'cd {shlex.quote(source_root_rel)} && {cmd}'
        return self._sanitize_cmake_command(wrapped, source_root_rel, phase)

    def _sanitize_preinstall_command(self, cmd: str) -> str:
        """
        预安装命令不应进入 source_root_rel。
        仅对极少数需要在工作目录内执行的命令做保守处理。
        """
        cmd = str(cmd or "").strip()
        if not cmd:
            return ""

        # 预安装阶段不允许把 apt-get 放进子目录
        if "apt-get " in cmd or cmd.startswith("apt-get"):
            return cmd

        # 允许个别命令携带工作目录，但不强制修改
        return cmd

    def _sanitize_cmake_command(self, cmd: str, source_root_rel: str, phase: str) -> str:
        """
        针对 CMake 子目录工程修正 -S 参数。
        例如日志里出现：
            cd platform/android/java/nativeSrcsConfigs && cmake -S 'platform\\android\\java\\nativeSrcsConfigs' -B build ...
        这会导致 -S 指向错误的嵌套目录，因此应改为：
            cd platform/android/java/nativeSrcsConfigs && cmake -S . -B build ...
        """
        cmd = str(cmd or "").strip()
        if not cmd:
            return ""

        if "cmake" not in cmd:
            return cmd

        # 仅在 build/test/runtime 阶段处理 cmake -S
        if phase not in {"build", "test", "runtime"}:
            return cmd

        # 如果命令里已经有 cd source_root_rel，则把 -S source_root_rel 替换成 .
        if source_root_rel != ".":
            patterns = [
                rf"(-S\s+){re.escape(source_root_rel)}(?=\s|$)",
                rf"(-S\s+['\"]?){re.escape(source_root_rel)}(['\"]?)(?=\s|$)",
                rf"(-S\s+)(?:\./)?{re.escape(source_root_rel)}(?=\s|$)",
            ]
            for pat in patterns:
                if re.search(pat, cmd):
                    cmd = re.sub(pat, r"\1.", cmd)

            # 处理 Windows 风格反斜杠
            win_rel = source_root_rel.replace("/", "\\")
            patterns_win = [
                rf"(-S\s+){re.escape(win_rel)}(?=\s|$)",
                rf"(-S\s+['\"]?){re.escape(win_rel)}(['\"]?)(?=\s|$)",
            ]
            for pat in patterns_win:
                if re.search(pat, cmd):
                    cmd = re.sub(pat, r"\1.", cmd)

        # 如果 cmake 命令带 -S . 但前面已经 cd 到 source_root_rel，保持不变即可
        return cmd

    def _has_cd_prefix(self, cmd: str) -> bool:
        cmd = str(cmd or "").strip()
        lowered = cmd.lower()
        return lowered.startswith("cd ") or " && cd " in lowered

    def _render_run(self, cmd: str) -> str:
        cmd = str(cmd).strip()
        if not cmd:
            return ""
        return f"RUN set -eux; {cmd}"

    def _normalize_workdir(self, workdir: str) -> str:
        workdir = str(workdir).strip().replace("\\", "/")
        if not workdir:
            return "/workspace"
        if not workdir.startswith("/"):
            workdir = "/" + workdir
        return workdir.rstrip("/") or "/"

    def _normalize_rel_path(self, path: str) -> str:
        """
        统一处理相对路径：
        - Windows 反斜杠转 /
        - 去掉盘符
        - 去掉首尾 /
        - 保留空格（COPY 使用 JSON array）
        """
        path = str(path or "").strip().replace("\\", "/")
        if not path or path == ".":
            return "."

        if len(path) >= 2 and path[1] == ":":
            path = path[2:]

        path = path.lstrip("/")
        path = path.strip("/")
        path = path.replace("//", "/")
        return path or "."

    @staticmethod
    def _escape(s: str) -> str:
        return str(s).replace("\\", "\\\\").replace('"', '\\"')

    @staticmethod
    def _version_tuple(v: str) -> tuple:
        parts = []
        for token in str(v).split("."):
            try:
                parts.append(int(token))
            except Exception:
                break
        while len(parts) < 3:
            parts.append(0)
        return tuple(parts[:3])

    def _version_gt(self, a: str, b: str) -> bool:
        return self._version_tuple(a) > self._version_tuple(b)

    # 兼容旧接口
    def generate_dockerfile(self, project_info: Dict[str, Any]) -> str:
        deps = project_info.get("deps")
        if deps is None:
            deps = project_info.get("dependencies", [])

        plan = project_info.get("plan")
        if plan is None:
            plan = project_info.get("build_plan", {})

        snapshot = project_info.get("snapshot")
        if snapshot is None:
            snapshot = {
                "project_name": project_info.get("project_name", "unknown"),
                "build_system": project_info.get("build_system", "unknown"),
                "source_root_rel": project_info.get("source_root_rel", "."),
                "required_cmake_version": project_info.get("required_cmake_version", ""),
                "requires_qt6": project_info.get("requires_qt6", False),
                "requires_boost": project_info.get("requires_boost", False),
                "requires_x11": project_info.get("requires_x11", False),
                "requires_opengl": project_info.get("requires_opengl", False),
                "is_gui_project": project_info.get("is_gui_project", False),
            }

        return self.render(deps=deps, plan=plan, snapshot=snapshot)

    def debug_generate(self, deps: DependencyAnalysis, plan: BuildPlan, snapshot: Dict[str, Any]) -> str:
        logger.info("Generating Dockerfile for project=%s source_root_rel=%s", snapshot.get("project_name"), snapshot.get("source_root_rel", "."))
        dockerfile = self.render(deps=deps, plan=plan, snapshot=snapshot)
        logger.info("Generated Dockerfile preview:\n%s", "\n".join(dockerfile.splitlines()[:30]))
        return dockerfile