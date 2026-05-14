from __future__ import annotations

import json
import logging
import os
import re
import shlex
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

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
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out

class DockerfileGenerator:
    """
    规则化 Dockerfile 生成器（Bazal 强化修复版）。

    重点增强：
    - 官方 ubuntu/debian 基础镜像自动切换到镜像前缀，避免 Docker Hub 认证/网络失败
    - 识别 CMakeLists.txt 中的 GCC 最低版本要求
    - 当项目要求 GCC >= 14 时，自动切换到 ubuntu:24.04 并安装 gcc-14 / g++-14
    - 识别 FetchContent / ExternalProject 外部依赖
    - 识别 add_subdirectory 指向的本地 thirdparty / submodule 缺失目录
    - 修复 base_image 被 LLM 污染为自然语言的问题
    - 避免重复 COPY 子目录导致 checksum/path not found
    - 处理 autotools 的 CRLF / 可执行权限问题
    - Bazel 项目强制走可靠 bootstrap，且严格过滤噪声依赖
    """

    DEFAULT_APT_MIRROR = "http://mirrors.tuna.tsinghua.edu.cn/ubuntu"
    DEFAULT_APT_RETRIES = 5
    DEFAULT_APT_HTTP_TIMEOUT = 30

    # Docker Hub 代理前缀
    DEFAULT_BASE_IMAGE_MIRROR = os.getenv("CXXCRAFTER_BASE_IMAGE_MIRROR", "")

    def __init__(self, project_root: str, default_base_image: str = "ubuntu:24.04") -> None:
        self.project_root = Path(project_root).resolve()
        self.default_base_image = default_base_image

    # ---------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------
    def render(self, deps: DependencyAnalysis, plan: BuildPlan, snapshot: Dict[str, Any]) -> str:
        project_name = str(snapshot.get("project_name", "") or "").strip()
        project_name_l = project_name.lower()

        build_system = str(snapshot.get("build_system", "unknown") or "unknown").strip().lower()
        build_system = self._apply_project_override(project_name_l, build_system)

        raw_source_root_rel = snapshot.get("source_root_rel", ".")
        source_root_rel = self._resolve_source_root_rel(
            build_system=build_system,
            source_root_rel=self._normalize_rel_path(raw_source_root_rel),
        )

        required_cmake_version = str(snapshot.get("required_cmake_version") or "").strip()

        requires_qt6 = bool(snapshot.get("requires_qt6", False))
        requires_boost = bool(snapshot.get("requires_boost", False))
        requires_x11 = bool(snapshot.get("requires_x11", False))
        requires_opengl = bool(snapshot.get("requires_opengl", False))
        is_gui_project = bool(snapshot.get("is_gui_project", False))

        uses_fetchcontent = bool(snapshot.get("uses_fetchcontent", False))
        uses_externalproject = bool(snapshot.get("uses_externalproject", False))
        requires_network_fetch = bool(snapshot.get("requires_network_fetch", False))

        base_image = self._normalize_base_image(
            getattr(plan, "base_image", None),
            default=self.default_base_image or "ubuntu:24.04",
        )

        # 如果项目明确要求 GCC >= 14，则自动升级基础镜像
        required_gcc_version = self._detect_required_gcc_version(snapshot)
        needs_newer_gcc = bool(required_gcc_version and self._version_tuple(required_gcc_version) >= (14, 0, 0))
        if needs_newer_gcc and base_image.startswith("ubuntu:24.04"):
            base_image = "ubuntu:24.04"

        # Bazel 项目再做一轮兜底：docs/site/examples 绝不能作为主入口
        if build_system == "bazel":
            source_root_rel = self._resolve_bazel_root_fallback(source_root_rel)

        # fetchcontent / submodule 依赖
        fetchcontent_deps = snapshot.get("fetchcontent_deps", []) or []
        if not fetchcontent_deps:
            fetchcontent_deps = self._detect_fetchcontent_deps()

        submodule_deps = snapshot.get("submodule_deps", None)
        if submodule_deps is None:
            submodule_deps = self._discover_missing_submodule_deps()
        submodule_deps = [
            x for x in (submodule_deps or [])
            if isinstance(x, dict) and x.get("path") and x.get("url")
        ]

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
        copy_paths = self._normalize_copy_paths(
            _dedupe(list(getattr(plan, "copy_paths", []) or [])),
            source_root_rel=source_root_rel,
        )
        if not copy_paths:
            copy_paths = [source_root_rel or "."]

        # 项目级兜底规则
        project_extra_apt, project_extra_pre, project_extra_build, project_extra_notes = self._project_specific_rules(
            project_name=project_name_l,
            build_system=build_system,
            source_root_rel=source_root_rel,
        )
        apt_packages = _dedupe(apt_packages + project_extra_apt)
        preinstall_commands = _dedupe(preinstall_commands + project_extra_pre)
        build_commands = _dedupe(build_commands + project_extra_build)

        # Bazel 专用：只保留 bazel / bazelisk 相关命令，过滤掉其他构建系统噪声
        if build_system == "bazel":
            build_commands = self._normalize_bazel_build_commands(build_commands)
            test_commands = self._normalize_bazel_test_commands(test_commands)
            preinstall_commands = self._normalize_bazel_preinstall_commands(preinstall_commands)

        # Bazel / CMake / Make 的默认兜底命令
        if build_system == "bazel":
            if not any("bazel" in c.lower() for c in build_commands):
                build_commands = ["bazel build //..."]
            if not test_commands:
                test_commands = ["bazel test //..."]
        elif build_system == "cmake":
            if not build_commands:
                cmake_cmd = "cmake -S . -B build -DCMAKE_BUILD_TYPE=Release"
                if needs_newer_gcc:
                    cmake_cmd += " -DCMAKE_C_COMPILER=gcc-14 -DCMAKE_CXX_COMPILER=g++-14"
                build_commands = [
                    cmake_cmd,
                    "cmake --build build -j$(nproc)",
                ]
        elif build_system == "make":
            if not build_commands:
                build_commands = ["make -j$(nproc)"]
        elif build_system == "meson":
            if not build_commands:
                build_commands = [
                    "meson setup build --buildtype=release",
                    "meson compile -C build",
                ]
        elif build_system == "autotools":
            if not build_commands:
                build_commands = [
                    "./configure --prefix=/usr",
                    "make -j$(nproc)",
                ]
        elif build_system == "python":
            if not build_commands:
                build_commands = ["python3 -m pip install -e ."]
        elif build_system == "node":
            if not build_commands:
                build_commands = ["npm install"]

        # GCC14 补齐
        if needs_newer_gcc:
            apt_packages = _dedupe(["gcc-14", "g++-14", "cpp-14"] + apt_packages)
            preinstall_commands = _dedupe(
                preinstall_commands
                + [
                    "update-alternatives --install /usr/bin/cc cc /usr/bin/gcc-14 100 || true",
                    "update-alternatives --install /usr/bin/c++ c++ /usr/bin/g++-14 100 || true",
                ]
            )

        # pip 包存在时，补齐 Python 运行环境
        if pip_packages:
            apt_packages = _dedupe(["python3", "python3-pip", "python3-venv"] + apt_packages)

        # 从命令 / 备注 / 项目级规则推断额外依赖
        inferred = self._infer_packages_from_commands(
            build_commands=build_commands,
            test_commands=test_commands,
            preinstall_commands=preinstall_commands,
            notes=list(getattr(plan, "notes", []) or [])
            + list(getattr(deps, "notes", []) or [])
            + project_extra_notes
            + ([f"GCC requirement detected: {required_gcc_version}"] if required_gcc_version else []),
            source_root_rel=source_root_rel,
            build_system=build_system,
            project_name=project_name_l,
        )
        apt_packages = _dedupe(apt_packages + inferred)

        # 过滤掉 Ubuntu 24.04 中不存在的 apt 包（LLM 可能生成不存在的包名）
        apt_packages = self._filter_unavailable_apt_packages(apt_packages, base_image)

        # Bazel 专用：最终强制收口，彻底去掉 cmake / autotools / protobuf / boost 噪声
        if build_system == "bazel":
            apt_packages = self._filter_bazel_apt_packages(apt_packages)
            apt_packages = _dedupe(
                ["ca-certificates", "curl", "git", "pkg-config", "build-essential", "openjdk-17-jdk", "unzip", "zip"]
                + apt_packages
            )
            preinstall_commands = _dedupe(preinstall_commands + [self._bazel_bootstrap_command()])

        # snapshot 级推断
        if requires_qt6:
            apt_packages = _dedupe(apt_packages + ["qt6-base-dev", "qt6-tools-dev", "qt6-tools-dev-tools"])
        if requires_boost:
            apt_packages = _dedupe(apt_packages + ["libboost-all-dev"])
        if requires_x11:
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
                ]
            )
        if requires_opengl:
            apt_packages = _dedupe(
                apt_packages
                + [
                    "libgl1-mesa-dev",
                    "libglu1-mesa-dev",
                    "libglew-dev",
                    "libglfw3-dev",
                ]
            )

        # 如果有 FetchContent / ExternalProject / submodule 时，确保 git 可用
        if fetchcontent_deps or submodule_deps:
            apt_packages = _dedupe(["git", "ca-certificates", "openssh-client"] + apt_packages)

        # CMake 版本升级
        cmake_upgrade_step = ""
        if required_cmake_version and self._version_gt(required_cmake_version, "3.22.1"):
            apt_packages = _dedupe(["python3", "python3-pip", "python3-venv"] + apt_packages)
            cmake_upgrade_step = self._render_run(
                f'python3 -m pip install --no-cache-dir --break-system-packages "cmake>={required_cmake_version}"'
            )

        # 基础工具兜底
        apt_packages = _dedupe(["ca-certificates", "curl", "git", "pkg-config", "build-essential"] + apt_packages)

        # Bazel 再次收口：防止任何恢复逻辑把 cmake/autotools 包塞回来
        if build_system == "bazel":
            apt_packages = self._filter_bazel_apt_packages(apt_packages)
            apt_packages = _dedupe(
                ["ca-certificates", "curl", "git", "pkg-config", "build-essential", "openjdk-17-jdk", "unzip", "zip"]
                + apt_packages
            )

        workdir = self._normalize_workdir(getattr(plan, "workdir", None) or "/workspace")

        lines: List[str] = []
        lines.extend(self._render_from_lines(base_image))

        lines.append("ENV DEBIAN_FRONTEND=noninteractive")
        lines.append('SHELL ["/bin/bash", "-o", "pipefail", "-c"]')
        lines.append(f"WORKDIR {workdir}")

        # labels
        lines.append(f'LABEL org.cxxcrafter.project_name="{self._escape(project_name)}"')
        lines.append(f'LABEL org.cxxcrafter.project_family="{self._escape(project_family)}"')
        lines.append(f'LABEL org.cxxcrafter.build_system="{self._escape(build_system)}"')
        lines.append(f'LABEL org.cxxcrafter.source_root_rel="{self._escape(source_root_rel)}"')
        lines.append(f'LABEL org.cxxcrafter.required_cmake_version="{self._escape(required_cmake_version)}"')
        lines.append(f'LABEL org.cxxcrafter.required_gcc_version="{self._escape(required_gcc_version or "")}"')
        lines.append(f'LABEL org.cxxcrafter.needs_newer_gcc="{str(needs_newer_gcc).lower()}"')
        lines.append(f'LABEL org.cxxcrafter.requires_qt6="{str(requires_qt6).lower()}"')
        lines.append(f'LABEL org.cxxcrafter.requires_boost="{str(requires_boost).lower()}"')
        lines.append(f'LABEL org.cxxcrafter.requires_x11="{str(requires_x11).lower()}"')
        lines.append(f'LABEL org.cxxcrafter.requires_opengl="{str(requires_opengl).lower()}"')
        lines.append(f'LABEL org.cxxcrafter.is_gui_project="{str(is_gui_project).lower()}"')
        lines.append(f'LABEL org.cxxcrafter.uses_fetchcontent="{str(uses_fetchcontent).lower()}"')
        lines.append(f'LABEL org.cxxcrafter.uses_externalproject="{str(uses_externalproject).lower()}"')
        lines.append(f'LABEL org.cxxcrafter.requires_network_fetch="{str(requires_network_fetch).lower()}"')
        lines.append(f'LABEL org.cxxcrafter.submodule_dep_count="{len(submodule_deps)}"')
        if feature_tags:
            lines.append(f'LABEL org.cxxcrafter.feature_tags="{self._escape(",".join(feature_tags))}"')

        if getattr(deps, "notes", None):
            lines.append(f"# dependency_notes: {self._escape(' | '.join(_dedupe(deps.notes)[:12]))}")
        if getattr(plan, "notes", None):
            lines.append(f"# build_notes: {self._escape(' | '.join(_dedupe(plan.notes)[:12]))}")
        if required_gcc_version:
            lines.append(f"# gcc_requirement: >= {required_gcc_version}")
        if needs_newer_gcc:
            lines.append("# gcc_action: switch to gcc-14/g++-14 and force CMake to use them")
        if submodule_deps:
            lines.append("# submodule_action: prefetch missing add_subdirectory dependencies from .gitmodules")

        # APT 安装
        apt_step = self._render_apt_setup_step(apt_packages)
        if apt_step:
            lines.append(apt_step)

        if cmake_upgrade_step:
            lines.append(cmake_upgrade_step)

        # FetchContent / ExternalProject 预拉取
        if fetchcontent_deps:
            lines.append(self._render_fetchcontent_prefetch_step(fetchcontent_deps))

        # 本地缺失 thirdparty / submodule 预拉取
        if submodule_deps:
            lines.append(self._render_submodule_prefetch_step(submodule_deps))

        # 环境变量
        env_map = dict(getattr(plan, "env", {}) or {})
        if needs_newer_gcc:
            env_map.setdefault("CC", "gcc-14")
            env_map.setdefault("CXX", "g++-14")

        for k, v in env_map.items():
            lines.append(f'ENV {k}="{self._escape(v)}"')

        for k, v in (getattr(deps, "env", {}) or {}).items():
            if k not in env_map:
                lines.append(f'ENV {k}="{self._escape(v)}"')

        # COPY
        for src in copy_paths:
            copy_line = self._render_copy_line(src, workdir)
            if copy_line:
                lines.append(copy_line)

        # 预安装命令
        for cmd in preinstall_commands:
            rendered = self._render_run(
                self._sanitize_command(
                    cmd,
                    source_root_rel,
                    build_system,
                    phase="preinstall",
                    fetchcontent_deps=fetchcontent_deps,
                    submodule_deps=submodule_deps,
                    needs_newer_gcc=needs_newer_gcc,
                )
            )
            if rendered:
                lines.append(rendered)

        # autotools 处理
        autotools_has_custom_bootstrap = any(
            ("configure" in c.lower()) or ("autogen" in c.lower())
            for c in build_commands
        )
        if build_system == "autotools" and not autotools_has_custom_bootstrap:
            lines.append(
                self._render_run(
                    'if [ -f ./autogen.sh ] || [ -f ./configure ]; then '
                    'apt-get update >/dev/null 2>&1 || true; '
                    'apt-get install -y --no-install-recommends dos2unix >/dev/null 2>&1 || true; '
                    'dos2unix ./autogen.sh ./configure ./contrib/download_prerequisites 2>/dev/null || true; '
                    'chmod +x ./autogen.sh ./configure ./contrib/download_prerequisites 2>/dev/null || true; '
                    'fi'
                )
            )
            lines.append(self._render_run('if [ -f ./autogen.sh ]; then sh ./autogen.sh; fi'))
            lines.append(self._render_run('if [ -f ./configure ]; then sh ./configure --prefix=/usr; fi'))

        # Python 包
        if pip_packages:
            lines.append(self._render_run("python3 -m pip install --no-cache-dir --break-system-packages --upgrade pip setuptools wheel"))
            lines.append(self._render_run("python3 -m pip install --no-cache-dir --break-system-packages " + " ".join(pip_packages)))

        # build commands
        for cmd in build_commands:
            fixed = self._sanitize_command(
                cmd,
                source_root_rel,
                build_system,
                phase="build",
                fetchcontent_deps=fetchcontent_deps,
                submodule_deps=submodule_deps,
                needs_newer_gcc=needs_newer_gcc,
            )
            rendered = self._render_run(fixed)
            if rendered:
                lines.append(rendered)

        # test commands
        for cmd in test_commands:
            fixed = self._sanitize_command(
                cmd,
                source_root_rel,
                build_system,
                phase="test",
                fetchcontent_deps=fetchcontent_deps,
                submodule_deps=submodule_deps,
                needs_newer_gcc=needs_newer_gcc,
            )
            rendered = self._render_run(fixed)
            if rendered:
                lines.append(rendered)

        # runtime command
        runtime_command = str(getattr(plan, "runtime_command", "") or "").strip()
        if runtime_command:
            runtime_cmd = self._sanitize_command(
                runtime_command,
                source_root_rel,
                build_system,
                phase="runtime",
                fetchcontent_deps=fetchcontent_deps,
                submodule_deps=submodule_deps,
                needs_newer_gcc=needs_newer_gcc,
            )
            lines.append(f'CMD ["sh", "-lc", "{self._escape(runtime_cmd)}"]')

        return "\n".join(lines) + "\n"

    def save(self, dockerfile_text: str, out_path: str) -> str:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(dockerfile_text, encoding="utf-8")
        return str(out)

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
                "uses_fetchcontent": project_info.get("uses_fetchcontent", False),
                "uses_externalproject": project_info.get("uses_externalproject", False),
                "fetchcontent_deps": project_info.get("fetchcontent_deps", []),
                "requires_network_fetch": project_info.get("requires_network_fetch", False),
                "submodule_deps": project_info.get("submodule_deps", []),
            }

        return self.render(deps=deps, plan=plan, snapshot=snapshot)

    def debug_generate(self, deps: DependencyAnalysis, plan: BuildPlan, snapshot: Dict[str, Any]) -> str:
        logger.info(
            "Generating Dockerfile for project=%s source_root_rel=%s",
            snapshot.get("project_name"),
            snapshot.get("source_root_rel", "."),
        )
        dockerfile = self.render(deps=deps, plan=plan, snapshot=snapshot)
        logger.info("Generated Dockerfile preview:\n%s", "\n".join(dockerfile.splitlines()[:30]))
        return dockerfile

    # ---------------------------------------------------------------------
    # Project / build-system correction
    # ---------------------------------------------------------------------
    def _apply_project_override(self, project_name: str, build_system: str) -> str:
        name = (project_name or "").lower()
        bs = (build_system or "unknown").lower()

        if name == "bazel":
            return "bazel"
        if name == "gcc":
            return "autotools"
        if name == "linux":
            return "make"
        if name in {"llvm", "clang", "llvm-project"} and bs == "unknown":
            return "cmake"
        if name in {"scons", "sconstruct"}:
            return "scons"

        return bs

    # ---------------------------------------------------------------------
    # Bazel 专用：收紧命令与依赖
    # ---------------------------------------------------------------------
    def _normalize_bazel_build_commands(self, commands: List[str]) -> List[str]:
        kept = []
        for cmd in commands:
            c = str(cmd).strip()
            if not c:
                continue
            low = c.lower()
            if "bazel" in low or "bazelisk" in low:
                kept.append(c)

        if not kept:
            kept = ["bazel build //..."]

        return _dedupe(kept)

    def _normalize_bazel_test_commands(self, commands: List[str]) -> List[str]:
        kept = []
        for cmd in commands:
            c = str(cmd).strip()
            if not c:
                continue
            low = c.lower()
            if "bazel" in low or "bazelisk" in low:
                kept.append(c)

        if not kept:
            kept = ["bazel test //..."]

        return _dedupe(kept)

    def _normalize_bazel_preinstall_commands(self, commands: List[str]) -> List[str]:
        allowed = []
        for cmd in commands:
            c = str(cmd).strip()
            if not c:
                continue
            low = c.lower()
            # bazel 项目只保留真正必要的 bootstrap/下载命令
            if any(x in low for x in ("bazel", "bazelisk", "curl", "wget", "ghproxy", "openjdk", "java")):
                allowed.append(c)
            elif not any(x in low for x in ("cmake", "autoconf", "automake", "libtool", "meson", "ninja", "configure", "autogen", "protobuf", "boost")):
                allowed.append(c)
        return _dedupe(allowed)

    def _filter_bazel_apt_packages(self, packages: List[str]) -> List[str]:
        """
        Bazel 项目只保留非常有限且稳定的 apt 包，
        避免把 CMake/Autotools/Protobuf/Boost 的噪声依赖加入安装链。
        """
        allow = {
            "ca-certificates",
            "curl",
            "git",
            "pkg-config",
            "build-essential",
            "openjdk-17-jdk",
            "unzip",
            "zip",
            "python3",
            "python3-pip",
            "python3-venv",
            "gnupg",
            "wget",
            "tar",
            "xz-utils",
        }

        filtered = [p for p in _dedupe(packages) if p in allow]
        return _dedupe(filtered)

    def _bazel_bootstrap_command(self) -> str:
        """
        Bazel 专用 bootstrap：
        1. 先尝试 apt 的 bazel-bootstrap（如果可用）
        2. 再下载 bazelisk，并校验 ELF 魔数，避免把 HTML 页面当二进制
        3. 最后生成真正的 shell wrapper，而不是 symlink/HTML 文件
        """
        return (
            'if command -v bazel >/dev/null 2>&1; then exit 0; fi; '
            'echo "[bazel] trying bazel-bootstrap from apt"; '
            'if apt-get update >/dev/null 2>&1 && apt-get install -y --no-install-recommends bazel-bootstrap >/dev/null 2>&1 && command -v bazel >/dev/null 2>&1; then '
            'exit 0; '
            'fi; '
            'echo "[bazel] installing bazelisk"; '
            'tmp="$(mktemp /tmp/bazelisk.XXXXXX)"; '
            'for u in '
            '"https://github.com/bazelbuild/bazelisk/releases/download/v1.20.0/bazelisk-linux-amd64" '
            '"https://ghproxy.com/https://github.com/bazelbuild/bazelisk/releases/download/v1.20.0/bazelisk-linux-amd64" '
            '"https://mirror.ghproxy.com/https://github.com/bazelbuild/bazelisk/releases/download/v1.20.0/bazelisk-linux-amd64"; do '
            'rm -f "$tmp"; '
            'if curl -fL --retry 3 --connect-timeout 10 --max-time 120 "$u" -o "$tmp"; then '
            'sig="$(head -c 4 "$tmp" | od -An -t x1 | tr -d \' \\n\')"; '
            'if [ "$sig" = "7f454c46" ]; then '
            'install -m 0755 "$tmp" /usr/local/bin/bazelisk; '
            'printf \'#!/bin/sh\\nexec /usr/local/bin/bazelisk "$@"\\n\' > /usr/local/bin/bazel; '
            'chmod +x /usr/local/bin/bazel; '
            'exit 0; '
            'fi; '
            'echo "[bazel] invalid bazelisk archive from $u"; '
            'fi; '
            'done; '
            'echo "[bazel] failed to install bazelisk"; '
            'exit 1'
        )

    # ---------------------------------------------------------------------
    # Build root / path correction
    # ---------------------------------------------------------------------
    def _resolve_bazel_root_fallback(self, source_root_rel: str) -> str:
        rel = self._normalize_rel_path(source_root_rel)
        if rel == ".":
            return "."

        low = rel.replace("\\", "/").lower()
        suspicious = any(
            x in low
            for x in (
                "docs",
                "doc",
                "documentation",
                "site",
                "examples",
                "example",
                "sample",
                "tests",
                "test",
                "tutorial",
            )
        )
        if suspicious:
            return "."

        candidate = self.project_root / rel
        if candidate.exists() and self._path_has_build_markers(candidate, build_system="bazel"):
            return rel

        best = self._find_best_source_root(build_system="bazel")
        if best is not None:
            best_rel = self._rel_to_project(best)
            if best_rel and best_rel != ".":
                return best_rel

        return "."

    def _resolve_source_root_rel(self, build_system: str, source_root_rel: str) -> str:
        source_root_rel = self._normalize_rel_path(source_root_rel)
        candidate = self.project_root / source_root_rel

        if build_system == "bazel":
            return self._resolve_bazel_root_fallback(source_root_rel)

        if source_root_rel == ".":
            return "."

        if candidate.exists() and self._path_has_build_markers(candidate, build_system=build_system):
            return source_root_rel

        low = source_root_rel.replace("\\", "/").lower()
        suspicious = any(
            part in low
            for part in (
                "docs",
                "doc",
                "documentation",
                "site",
                "examples",
                "example",
                "sample",
                "tests",
                "test",
                "tutorial",
            )
        )

        best = self._find_best_source_root(build_system=build_system)

        if suspicious and best is not None:
            rel = self._rel_to_project(best)
            return rel or "."

        if not candidate.exists() or not self._path_has_build_markers(candidate, build_system=build_system):
            if best is not None:
                rel = self._rel_to_project(best)
                return rel or "."

            if self._path_has_build_markers(self.project_root, build_system=build_system):
                return "."

        return source_root_rel

    def _find_best_source_root(self, build_system: str) -> Optional[Path]:
        marker_names = self._marker_names(build_system)
        if not marker_names:
            return None

        candidates: List[Path] = []

        for marker in marker_names:
            try:
                for p in self.project_root.rglob(marker):
                    if p.is_file():
                        candidates.append(p.parent)
            except Exception:
                continue

        if not candidates:
            return None

        unique: List[Path] = []
        seen = set()
        for p in candidates:
            try:
                key = str(p.resolve())
            except Exception:
                key = str(p)
            if key not in seen:
                seen.add(key)
                unique.append(p)

        def score(p: Path) -> Tuple[int, int, int]:
            try:
                rel = p.relative_to(self.project_root)
                depth = len(rel.parts)
                rel_text = rel.as_posix().lower()
            except Exception:
                depth = 999
                rel_text = str(p).lower()

            bonus = 0
            if p == self.project_root:
                bonus += 50
            if any(x in rel_text for x in ("docs", "doc", "documentation", "site", "examples", "example", "sample", "tests", "test", "tutorial")):
                bonus -= 40

            if build_system == "bazel":
                if (p / "WORKSPACE").exists() or (p / "MODULE.bazel").exists():
                    bonus += 80
                if (p / "BUILD").exists() or (p / "BUILD.bazel").exists():
                    bonus += 70
            elif build_system == "cmake":
                if (p / "CMakeLists.txt").exists():
                    bonus += 70
            elif build_system == "autotools":
                if (p / "configure.ac").exists() or (p / "configure.in").exists() or (p / "autogen.sh").exists():
                    bonus += 60
            elif build_system == "meson":
                if (p / "meson.build").exists():
                    bonus += 60
            elif build_system == "make":
                if (p / "Makefile").exists() or (p / "makefile").exists():
                    bonus += 50
            elif build_system == "scons":
                if (p / "SConstruct").exists() or (p / "SConscript").exists():
                    bonus += 50

            return (-bonus, depth, len(str(p)))

        unique.sort(key=score)
        return unique[0] if unique else None

    def _marker_names(self, build_system: str) -> List[str]:
        bs = (build_system or "").lower()
        if bs == "bazel":
            return ["BUILD", "BUILD.bazel", "WORKSPACE", "MODULE.bazel"]
        if bs == "cmake":
            return ["CMakeLists.txt"]
        if bs == "autotools":
            return ["configure.ac", "configure.in", "autogen.sh", "configure"]
        if bs == "meson":
            return ["meson.build"]
        if bs == "make":
            return ["Makefile", "makefile"]
        if bs == "scons":
            return ["SConstruct", "SConscript"]
        return ["CMakeLists.txt", "Makefile", "makefile", "configure.ac", "meson.build", "BUILD", "BUILD.bazel", "WORKSPACE", "MODULE.bazel", "SConstruct", "SConscript"]

    def _path_has_build_markers(self, path: Path, build_system: str) -> bool:
        if not path.exists() or not path.is_dir():
            return False

        for marker in self._marker_names(build_system):
            if (path / marker).exists():
                return True
        return False

    def _rel_to_project(self, path: Path) -> str:
        try:
            rel = path.relative_to(self.project_root)
            if not rel.parts:
                return "."
            return rel.as_posix()
        except Exception:
            return "."

    # ---------------------------------------------------------------------
    # Base image / path normalization
    # ---------------------------------------------------------------------
    def _is_official_base_image(self, image: str) -> bool:
        img = (image or "").strip().lower()
        if not img:
            return False

        if re.match(r"^[a-z0-9.-]+(\:[0-9]+)?\/", img) and not img.startswith(("library/ubuntu", "library/debian")):
            return False

        if img.startswith(("ubuntu", "debian", "library/ubuntu", "library/debian")):
            return True

        return False

    def _normalize_base_image(self, value: Any, default: str = "ubuntu:24.04") -> str:
        raw = str(value or "").strip()

        if not raw:
            return default

        low = raw.lower()
        if low.startswith(("consider ", "use ", "try ", "maybe ", "recommend ", "prefer ")):
            return default

        if any(ch.isspace() for ch in raw):
            return default

        if len(raw) > 200:
            return default

        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]*$", raw):
            return default

        # 自动升级过旧的 Ubuntu 基础镜像到 24.04
        # 20.04 已接近 EOL，22.04 的部分依赖包版本较旧
        if re.match(r"^ubuntu:(20\.04|22\.04)$", raw):
            return "ubuntu:24.04"

        return raw

    def _render_from_lines(self, base_image: str) -> List[str]:
        image = self._normalize_base_image(base_image, default=self.default_base_image or "ubuntu:24.04")
        if self._is_official_base_image(image):
            mirror = self.DEFAULT_BASE_IMAGE_MIRROR.strip()
            if mirror:
                # 有镜像代理时，使用 ARG + FROM 组合
                # 用户可通过 --build-arg BASE_IMAGE_MIRROR= 切换回官方源
                return [
                    f"ARG BASE_IMAGE_MIRROR={mirror}",
                    f"FROM ${{BASE_IMAGE_MIRROR}}/{image}",
                ]
            else:
                # 无镜像代理，直接使用 docker.io 官方源
                return [f"FROM {image}"]
        return [f"FROM {image}"]

    def _normalize_copy_paths(self, paths: List[str], source_root_rel: str) -> List[str]:
        normalized = [self._normalize_rel_path(p) for p in paths if str(p).strip()]
        normalized = _dedupe(normalized)

        if "." in normalized:
            return ["."]

        if source_root_rel and source_root_rel != "." and source_root_rel not in normalized:
            normalized.insert(0, source_root_rel)

        return normalized

    def _filter_unavailable_apt_packages(self, packages: List[str], base_image: str) -> List[str]:
        """
        过滤掉在目标 Ubuntu 版本中不存在的 apt 包。
        LLM 可能会生成不存在的包名（如 gcc-15、g++-15 在 Ubuntu 24.04 中不存在）。
        """
        # Ubuntu 24.04 (noble) 中不存在的常见误判包
        noble_blacklist = {
            "gcc-15", "g++-15", "cpp-15",
            "gcc-16", "g++-16", "cpp-16",
            "llvm-19-dev", "llvm-19-tools",
            "clang-19",
        }
        # Ubuntu 22.04 (jammy) 中不存在的常见误判包
        jammy_blacklist = {
            "gcc-14", "g++-14", "cpp-14",
            "gcc-15", "g++-15", "cpp-15",
            "gcc-16", "g++-16", "cpp-16",
            "libavif-dev",
        }
        # Ubuntu 20.04 (focal) 中不存在的常见误判包
        focal_blacklist = {
            "gcc-12", "g++-12", "cpp-12",
            "gcc-13", "g++-13", "cpp-13",
            "gcc-14", "g++-14", "cpp-14",
            "gcc-15", "g++-15", "cpp-15",
            "libavif-dev",
        }

        blacklist = set()
        low = base_image.lower()
        if "24.04" in low or "noble" in low:
            blacklist = noble_blacklist
        elif "22.04" in low or "jammy" in low:
            blacklist = jammy_blacklist
        elif "20.04" in low or "focal" in low:
            blacklist = focal_blacklist

        if not blacklist:
            return packages

        return [p for p in packages if p.lower() not in {b.lower() for b in blacklist}]

    def _normalize_workdir(self, workdir: str) -> str:
        workdir = str(workdir).strip().replace("\\", "/")
        if not workdir:
            return "/workspace"
        if not workdir.startswith("/"):
            workdir = "/" + workdir
        return workdir.rstrip("/") or "/"

    def _normalize_rel_path(self, path: str) -> str:
        path = str(path or "").strip().replace("\\", "/")
        if not path or path == ".":
            return "."
        if len(path) >= 2 and path[1] == ":":
            path = path[2:]
        path = path.lstrip("/").strip("/")
        path = path.replace("//", "/")
        return path or "."

    # ---------------------------------------------------------------------
    # GCC version detection
    # ---------------------------------------------------------------------
    def _detect_required_gcc_version(self, snapshot: Dict[str, Any]) -> Optional[str]:
        source_root_rel = self._normalize_rel_path(snapshot.get("source_root_rel", "."))
        candidates: List[Path] = []

        if source_root_rel != ".":
            candidates.append(self.project_root / source_root_rel / "CMakeLists.txt")
        candidates.append(self.project_root / "CMakeLists.txt")

        try:
            for p in self.project_root.rglob("CMakeLists.txt"):
                candidates.append(p)
        except Exception:
            pass

        seen = set()
        for path in candidates:
            try:
                rp = path.resolve()
            except Exception:
                rp = path
            if str(rp) in seen:
                continue
            seen.add(str(rp))

            if not path.exists() or not path.is_file():
                continue

            text = self._read_text_file(path)
            version = self._extract_required_gcc_version(text)
            if version:
                return version

        return None

    def _extract_required_gcc_version(self, cmake_text: str) -> Optional[str]:
        if not cmake_text:
            return None

        patterns = [
            r"minimum supported version of gcc is\s*([0-9]+(?:\.[0-9]+){0,2})",
            r"minimum supported gcc version\s*[:=]?\s*([0-9]+(?:\.[0-9]+){0,2})",
            r"gcc\s*VERSION_LESS\s*([0-9]+(?:\.[0-9]+){0,2})",
            r"CMAKE_(?:C|CXX)_COMPILER_VERSION\s+VERSION_LESS\s*([0-9]+(?:\.[0-9]+){0,2})",
            r"compiler.*?gcc\s*>=\s*([0-9]+(?:\.[0-9]+){0,2})",
        ]

        low = cmake_text.lower()
        for pat in patterns:
            m = re.search(pat, low, re.I | re.S)
            if m:
                v = m.group(1).strip()
                if v:
                    parts = v.split(".")
                    while len(parts) < 3:
                        parts.append("0")
                    return ".".join(parts[:3])

        return None

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

    # ---------------------------------------------------------------------
    # FetchContent / ExternalProject detection
    # ---------------------------------------------------------------------
    def _extract_cmake_blocks(self, text: str, func_name: str) -> List[Tuple[str, str]]:
        results: List[Tuple[str, str]] = []
        pattern = re.compile(rf"{re.escape(func_name)}\s*\((.*?)\)", re.S | re.I)
        for m in pattern.finditer(text):
            block = m.group(1).strip()
            if not block:
                continue
            try:
                tokens = shlex.split(block.replace("\n", " "))
            except Exception:
                tokens = block.split()

            if not tokens:
                continue
            name = tokens[0].strip().strip('"').strip("'")
            if not name:
                name = "external_dep"
            results.append((name, block))
        return results

    def _extract_git_repository_from_block(self, block: str) -> str:
        if not block:
            return ""
        m = re.search(
            r"GIT_REPOSITORY\s+(?:\"([^\"]+)\"|'([^']+)'|([^\s\)]+))",
            block,
            re.I,
        )
        if not m:
            return ""
        return next((g for g in m.groups() if g), "").strip()

    def _detect_fetchcontent_deps(self) -> List[Dict[str, str]]:
        deps: List[Dict[str, str]] = []
        try:
            cmake_files = list(self.project_root.rglob("CMakeLists.txt"))
        except Exception:
            cmake_files = []

        for cmake_path in cmake_files:
            text = self._read_text_file(cmake_path)
            if not text.strip():
                continue

            blocks: List[Tuple[str, str]] = []
            blocks.extend(self._extract_cmake_blocks(text, "FetchContent_Declare"))
            blocks.extend(self._extract_cmake_blocks(text, "ExternalProject_Add"))

            for name, block in blocks:
                url = self._extract_git_repository_from_block(block)
                if not url:
                    continue
                deps.append(
                    {
                        "name": name,
                        "url": url,
                        "source_cmake": cmake_path.relative_to(self.project_root).as_posix(),
                    }
                )

        return self._unique_dep_dicts(deps)

    def _render_fetchcontent_prefetch_step(self, fetchcontent_deps: List[Dict[str, str]]) -> str:
        deps = []
        for item in fetchcontent_deps:
            name = self._safe_symbol(item.get("name", "external_dep"))
            url = str(item.get("url", "")).strip()
            if not url:
                continue
            local_dir = f"/workspace/.deps/{self._safe_path(item.get('name', 'external_dep'))}"
            deps.append((name, url, local_dir))

        if not deps:
            return ""

        lines = [
            'ARG GIT_MIRROR_PREFIX=https://ghproxy.com/',
            'RUN set -eux; \\',
            '    mkdir -p /workspace/.deps /workspace/.fcache; \\',
            '    export GIT_TERMINAL_PROMPT=0; \\',
        ]

        for sym, url, local_dir in deps:
            lines.extend([
                f'    echo "[FetchContent] prefetch {sym} from {self._escape(url)}"; \\',
                f'    if [ ! -d "{local_dir}" ] || [ ! -f "{local_dir}/CMakeLists.txt" ]; then \\',
                f'        rm -rf "{local_dir}"; \\',
                f'        mkdir -p "$(dirname "{local_dir}")"; \\',
                f'        if ! git clone --depth 1 --recursive "{url}" "{local_dir}"; then \\',
                f'            if echo "{url}" | grep -q "github.com"; then \\',
                f'                mirror_url="${{GIT_MIRROR_PREFIX}}{url}"; \\',
                f'                echo "[FetchContent] retry with mirror: $mirror_url"; \\',
                f'                git clone --depth 1 --recursive "$mirror_url" "{local_dir}"; \\',
                f'            else \\',
                f'                echo "[FetchContent] clone failed and no mirror fallback for {sym}"; \\',
                f'                exit 1; \\',
                f'            fi; \\',
                f'        fi; \\',
                f'    fi; \\',
            ])

        return "\n".join(lines)

    def _fetchcontent_cmake_args(self, fetchcontent_deps: List[Dict[str, str]]) -> List[str]:
        args = [
            "-DFETCHCONTENT_BASE_DIR=/workspace/.fcache",
            "-DFETCHCONTENT_FULLY_DISCONNECTED=OFF",
            "-DFETCHCONTENT_UPDATES_DISCONNECTED=OFF",
        ]
        for item in fetchcontent_deps:
            name = self._safe_symbol(item.get("name", "external_dep"))
            local_dir = f"/workspace/.deps/{self._safe_path(item.get('name', 'external_dep'))}"
            args.append(f"-DFETCHCONTENT_SOURCE_DIR_{name}={local_dir}")
        return args

    # ---------------------------------------------------------------------
    # submodule / add_subdirectory detection
    # ---------------------------------------------------------------------
    def _parse_gitmodules(self) -> Dict[str, Dict[str, str]]:
        gitmodules_path = self.project_root / ".gitmodules"
        if not gitmodules_path.exists() or not gitmodules_path.is_file():
            return {}

        text = self._read_text_file(gitmodules_path)
        if not text.strip():
            return {}

        modules: Dict[str, Dict[str, str]] = {}
        current: Dict[str, str] = {}

        def flush() -> None:
            nonlocal current
            path = current.get("path", "").strip()
            url = current.get("url", "").strip()
            name = current.get("name", "").strip()
            if path and url:
                modules[path.replace("\\", "/").strip("/")] = {
                    "name": name or Path(path).name,
                    "path": path.replace("\\", "/").strip("/"),
                    "url": url,
                }
            current = {}

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            m = re.match(r'\[submodule\s+"(.+?)"\]', line, re.I)
            if m:
                if current:
                    flush()
                current = {"name": m.group(1)}
                continue

            if "=" in line:
                k, v = line.split("=", 1)
                current[k.strip()] = v.strip()

        if current:
            flush()

        return modules

    def _extract_add_subdirectory_paths(self, cmake_text: str) -> List[str]:
        if not cmake_text:
            return []

        paths: List[str] = []
        for match in re.finditer(r"add_subdirectory\s*\((.*?)\)", cmake_text, re.S | re.I):
            block = match.group(1).strip()
            if not block:
                continue

            try:
                tokens = shlex.split(block.replace("\n", " "))
            except Exception:
                tokens = block.split()

            if not tokens:
                continue

            first = tokens[0].strip()
            if not first:
                continue

            if first.upper() in {"EXCLUDE_FROM_ALL", "SYSTEM"}:
                continue
            if first.startswith("${") and first.endswith("}"):
                continue

            paths.append(first)

        return paths

    def _discover_missing_submodule_deps(self) -> List[Dict[str, str]]:
        gitmodules = self._parse_gitmodules()
        if not gitmodules:
            return []

        try:
            cmake_files = list(self.project_root.rglob("CMakeLists.txt"))
        except Exception:
            cmake_files = []

        deps: List[Dict[str, str]] = []
        seen = set()

        for cmake_path in cmake_files:
            cmake_text = self._read_text_file(cmake_path)
            if not cmake_text.strip():
                continue

            for sub_path in self._extract_add_subdirectory_paths(cmake_text):
                try:
                    candidate = (cmake_path.parent / sub_path).resolve()
                    rel = candidate.relative_to(self.project_root.resolve()).as_posix().strip("/")
                except Exception:
                    continue

                existing_cmake = candidate / "CMakeLists.txt"
                if candidate.exists() and existing_cmake.exists():
                    continue

                module = gitmodules.get(rel)
                if module is None:
                    base = Path(rel).name
                    for m in gitmodules.values():
                        if Path(m.get("path", "")).name == base:
                            module = m
                            break

                if module is None:
                    continue

                key = (module.get("path", ""), module.get("url", ""))
                if key in seen:
                    continue
                seen.add(key)

                deps.append(
                    {
                        "name": module.get("name", Path(module.get("path", "external_dep")).name),
                        "path": module.get("path", rel),
                        "url": module.get("url", ""),
                        "source_cmake": cmake_path.relative_to(self.project_root).as_posix(),
                    }
                )

        return deps

    def _render_submodule_prefetch_step(self, submodule_deps: List[Dict[str, str]]) -> str:
        deps = []
        for item in submodule_deps:
            path = str(item.get("path", "")).strip().replace("\\", "/").strip("/")
            url = str(item.get("url", "")).strip()
            name = str(item.get("name", "")).strip() or Path(path).name
            if not path or not url:
                continue
            local_dir = f"/workspace/{path}"
            deps.append((name, path, url, local_dir))

        if not deps:
            return ""

        lines = [
            'ARG GIT_MIRROR_PREFIX=https://ghproxy.com/',
            'RUN set -eux; \\',
            '    export GIT_TERMINAL_PROMPT=0; \\',
        ]

        for name, path, url, local_dir in deps:
            lines.extend([
                f'    echo "[Submodule] prefetch {self._escape(name)} -> {self._escape(path)}"; \\',
                f'    if [ ! -d "{local_dir}" ] || [ ! -f "{local_dir}/CMakeLists.txt" ]; then \\',
                f'        rm -rf "{local_dir}"; \\',
                f'        mkdir -p "$(dirname "{local_dir}")"; \\',
                f'        if ! git clone --depth 1 --recursive "{url}" "{local_dir}"; then \\',
                f'            if echo "{url}" | grep -q "github.com"; then \\',
                f'                mirror_url="${{GIT_MIRROR_PREFIX}}{url}"; \\',
                f'                echo "[Submodule] retry with mirror: $mirror_url"; \\',
                f'                git clone --depth 1 --recursive "$mirror_url" "{local_dir}"; \\',
                f'            else \\',
                f'                echo "[Submodule] clone failed for {self._escape(name)}"; \\',
                f'                exit 1; \\',
                f'            fi; \\',
                f'        fi; \\',
                f'    fi; \\',
            ])

        return "\n".join(lines)

    def _unique_dep_dicts(self, deps: List[Dict[str, str]]) -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []
        seen = set()
        for item in deps:
            key = (item.get("name", ""), item.get("url", ""))
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
        return out

    # ---------------------------------------------------------------------
    # Bazel 专用：收紧命令与依赖
    # ---------------------------------------------------------------------
    def _normalize_bazel_build_commands(self, commands: List[str]) -> List[str]:
        kept = []
        for cmd in commands:
            c = str(cmd).strip()
            if not c:
                continue
            low = c.lower()
            if "bazel" in low or "bazelisk" in low:
                kept.append(c)

        if not kept:
            kept = ["bazel build //..."]

        return _dedupe(kept)

    def _normalize_bazel_test_commands(self, commands: List[str]) -> List[str]:
        kept = []
        for cmd in commands:
            c = str(cmd).strip()
            if not c:
                continue
            low = c.lower()
            if "bazel" in low or "bazelisk" in low:
                kept.append(c)

        if not kept:
            kept = ["bazel test //..."]

        return _dedupe(kept)

    def _normalize_bazel_preinstall_commands(self, commands: List[str]) -> List[str]:
        allowed = []
        for cmd in commands:
            c = str(cmd).strip()
            if not c:
                continue
            low = c.lower()
            # bazel 项目只保留真正必要的 bootstrap/下载命令
            if any(x in low for x in ("bazel", "bazelisk", "curl", "wget", "ghproxy", "openjdk", "java")):
                allowed.append(c)
            elif not any(x in low for x in ("cmake", "autoconf", "automake", "libtool", "meson", "ninja", "configure", "autogen", "protobuf", "boost")):
                allowed.append(c)
        return _dedupe(allowed)

    def _filter_bazel_apt_packages(self, packages: List[str]) -> List[str]:
        """
        Bazel 项目只保留非常有限且稳定的 apt 包，
        避免把 CMake/Autotools/Protobuf/Boost 的噪声依赖加入安装链。
        """
        allow = {
            "ca-certificates",
            "curl",
            "git",
            "pkg-config",
            "build-essential",
            "openjdk-17-jdk",
            "unzip",
            "zip",
            "python3",
            "python3-pip",
            "python3-venv",
            "gnupg",
            "wget",
            "tar",
            "xz-utils",
        }

        filtered = [p for p in _dedupe(packages) if p in allow]
        return _dedupe(filtered)

    def _bazel_bootstrap_command(self) -> str:
        """
        Bazel 专用 bootstrap：
        1. 先尝试 apt 的 bazel-bootstrap（如果可用）
        2. 再下载 bazelisk，并校验 ELF 魔数，避免把 HTML 页面当二进制
        3. 最后生成真正的 shell wrapper，而不是 symlink/HTML 文件
        """
        return (
            'if command -v bazel >/dev/null 2>&1; then exit 0; fi; '
            'echo "[bazel] trying bazel-bootstrap from apt"; '
            'if apt-get update >/dev/null 2>&1 && apt-get install -y --no-install-recommends bazel-bootstrap >/dev/null 2>&1 && command -v bazel >/dev/null 2>&1; then '
            'exit 0; '
            'fi; '
            'echo "[bazel] installing bazelisk"; '
            'tmp="$(mktemp /tmp/bazelisk.XXXXXX)"; '
            'for u in '
            '"https://github.com/bazelbuild/bazelisk/releases/download/v1.20.0/bazelisk-linux-amd64" '
            '"https://ghproxy.com/https://github.com/bazelbuild/bazelisk/releases/download/v1.20.0/bazelisk-linux-amd64" '
            '"https://mirror.ghproxy.com/https://github.com/bazelbuild/bazelisk/releases/download/v1.20.0/bazelisk-linux-amd64"; do '
            'rm -f "$tmp"; '
            'if curl -fL --retry 3 --connect-timeout 10 --max-time 120 "$u" -o "$tmp"; then '
            'sig="$(head -c 4 "$tmp" | od -An -t x1 | tr -d \' \\n\')"; '
            'if [ "$sig" = "7f454c46" ]; then '
            'install -m 0755 "$tmp" /usr/local/bin/bazelisk; '
            'printf \'#!/bin/sh\\nexec /usr/local/bin/bazelisk "$@"\\n\' > /usr/local/bin/bazel; '
            'chmod +x /usr/local/bin/bazel; '
            'exit 0; '
            'fi; '
            'echo "[bazel] invalid bazelisk archive from $u"; '
            'fi; '
            'done; '
            'echo "[bazel] failed to install bazelisk"; '
            'exit 1'
        )

    # ---------------------------------------------------------------------
    # 命令修正
    # ---------------------------------------------------------------------
    def _sanitize_command(
        self,
        cmd: str,
        source_root_rel: str,
        build_system: str,
        phase: str,
        fetchcontent_deps: Optional[List[Dict[str, str]]] = None,
        submodule_deps: Optional[List[Dict[str, str]]] = None,
        needs_newer_gcc: bool = False,
    ) -> str:
        cmd = str(cmd or "").strip()
        source_root_rel = self._normalize_rel_path(source_root_rel)
        fetchcontent_deps = fetchcontent_deps or []

        if not cmd:
            return ""

        if phase == "preinstall":
            return self._sanitize_preinstall_command(cmd)

        if source_root_rel == ".":
            cmd = self._augment_cmake_configure_command(cmd, fetchcontent_deps, needs_newer_gcc)
            return self._sanitize_cmake_command(cmd, source_root_rel, phase, fetchcontent_deps, needs_newer_gcc)

        if self._has_cd_prefix(cmd):
            cmd = self._augment_cmake_configure_command(cmd, fetchcontent_deps, needs_newer_gcc)
            return self._sanitize_cmake_command(cmd, source_root_rel, phase, fetchcontent_deps, needs_newer_gcc)

        wrapped = f'cd {shlex.quote(source_root_rel)} && {cmd}'
        wrapped = self._augment_cmake_configure_command(wrapped, fetchcontent_deps, needs_newer_gcc)
        return self._sanitize_cmake_command(wrapped, source_root_rel, phase, fetchcontent_deps, needs_newer_gcc)

    def _sanitize_preinstall_command(self, cmd: str) -> str:
        cmd = str(cmd or "").strip()
        if not cmd:
            return ""
        return cmd

    def _augment_cmake_configure_command(
        self,
        cmd: str,
        fetchcontent_deps: List[Dict[str, str]],
        needs_newer_gcc: bool,
    ) -> str:
        cmd = str(cmd or "").strip()
        if not cmd or "cmake" not in cmd:
            return cmd

        if "--build" in cmd:
            return cmd

        extra: List[str] = []

        if fetchcontent_deps and "-DFETCHCONTENT_BASE_DIR=" not in cmd and "-DFETCHCONTENT_SOURCE_DIR_" not in cmd:
            extra.extend(self._fetchcontent_cmake_args(fetchcontent_deps))

        if needs_newer_gcc:
            if "-DCMAKE_C_COMPILER=" not in cmd:
                extra.append("-DCMAKE_C_COMPILER=gcc-14")
            if "-DCMAKE_CXX_COMPILER=" not in cmd:
                extra.append("-DCMAKE_CXX_COMPILER=g++-14")

        if not extra:
            return cmd

        return cmd + " " + " ".join(extra)

    def _sanitize_cmake_command(
        self,
        cmd: str,
        source_root_rel: str,
        phase: str,
        fetchcontent_deps: List[Dict[str, str]],
        needs_newer_gcc: bool,
    ) -> str:
        cmd = str(cmd or "").strip()
        if not cmd:
            return ""

        if "cmake" not in cmd:
            return cmd

        if phase not in {"build", "test", "runtime"}:
            return cmd

        if source_root_rel != ".":
            patterns = [
                rf"(-S\s+){re.escape(source_root_rel)}(?=\s|$)",
                rf"(-S\s+['\"]?){re.escape(source_root_rel)}(['\"]?)(?=\s|$)",
                rf"(-S\s+)(?:\./)?{re.escape(source_root_rel)}(?=\s|$)",
            ]
            for pat in patterns:
                if re.search(pat, cmd):
                    cmd = re.sub(pat, r"\1.", cmd)

            win_rel = source_root_rel.replace("/", "\\")
            patterns_win = [
                rf"(-S\s+){re.escape(win_rel)}(?=\s|$)",
                rf"(-S\s+['\"]?){re.escape(win_rel)}(['\"]?)(?=\s|$)",
            ]
            for pat in patterns_win:
                if re.search(pat, cmd):
                    cmd = re.sub(pat, r"\1.", cmd)

        if needs_newer_gcc and "--build" not in cmd:
            if "-DCMAKE_C_COMPILER=" not in cmd:
                cmd += " -DCMAKE_C_COMPILER=gcc-14"
            if "-DCMAKE_CXX_COMPILER=" not in cmd:
                cmd += " -DCMAKE_CXX_COMPILER=g++-14"

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

    def _render_copy_line(self, src: str, workdir: str) -> str:
        src = self._normalize_rel_path(src)
        workdir = self._normalize_workdir(workdir)

        if src != ".":
            candidate = (self.project_root / src).resolve()
            if not candidate.exists():
                return ""

        if src == ".":
            return f'COPY {json.dumps([".", f"{workdir}/"], ensure_ascii=False)}'

        dest = f"{workdir}/{src}".replace("//", "/").rstrip("/")
        return f'COPY {json.dumps([src, dest], ensure_ascii=False)}'

    # ---------------------------------------------------------------------
    # 规则与启发式
    # ---------------------------------------------------------------------
    def _project_specific_rules(
        self,
        *,
        project_name: str,
        build_system: str,
        source_root_rel: str,
    ) -> Tuple[List[str], List[str], List[str], List[str]]:
        """
        少量项目级兜底规则，不追求覆盖全部项目，只补高频失败项目。
        """
        apt_packages: List[str] = []
        preinstall_commands: List[str] = []
        build_commands: List[str] = []
        notes: List[str] = []

        if project_name == "gcc":
            apt_packages.extend([
                "libgmp-dev",
                "libmpfr-dev",
                "libmpc-dev",
                "libisl-dev",
                "texinfo",
                "flex",
                "bison",
                "gawk",
                "perl",
                "patch",
                "m4",
                "tar",
                "xz-utils",
                "dos2unix",
                "libreadline-dev",
                "libncurses5-dev",
                "libncurses-dev",
                "libedit-dev",
            ])
            preinstall_commands.append("if [ -x ./contrib/download_prerequisites ]; then ./contrib/download_prerequisites; fi")
            notes.append("gcc project-specific prerequisites enabled")
            if build_system in {"unknown", "autotools"}:
                build_commands.extend([
                    "./configure --disable-multilib --enable-languages=c,c++",
                    "make -j$(nproc)",
                ])

        elif project_name == "linux":
            apt_packages.extend([
                "bc",
                "bison",
                "flex",
                "libssl-dev",
                "libelf-dev",
                "cpio",
                "rsync",
                "dwarves",
                "perl",
                "tar",
                "xz-utils",
                "dos2unix",
            ])
            notes.append("linux kernel-style build tools enabled")
            if build_system == "unknown":
                notes.append("linux project detected, prefer make-style build")

        elif project_name in {"llvm", "clang", "llvm-project"}:
            apt_packages.extend([
                "cmake",
                "ninja-build",
                "python3",
                "python3-pip",
                "libxml2-dev",
                "libedit-dev",
                "libncurses5-dev",
                "libtinfo-dev",
                "zlib1g-dev",
            ])
            notes.append("llvm/clang project-specific tools enabled")

        elif project_name == "openalpr":
            apt_packages.extend([
                "libopencv-dev",
                "libleptonica-dev",
                "libtesseract-dev",
                "tesseract-ocr",
                "liblog4cplus-dev",
                "libjsoncpp-dev",
            ])
            notes.append("openalpr OCR stack enabled")

        elif project_name == "tensorflow":
            apt_packages.extend([
                "libxml2-dev",
                "libedit-dev",
                "libncurses5-dev",
                "zlib1g-dev",
                "libffi-dev",
            ])
            notes.append("tensorflow/xla/mlir-related tools enabled")

        elif project_name == "luanti":
            apt_packages.extend([
                "libsdl2-dev",
                "libopenal-dev",
                "libfreetype6-dev",
                "libvorbis-dev",
                "libogg-dev",
                "libjpeg-dev",
                "libpng-dev",
                "libsqlite3-dev",
                "libluajit-5.1-dev",
            ])
            notes.append("luanti game engine stack enabled")

        elif project_name == "sqlitebrowser":
            apt_packages.extend([
                "qtbase5-dev",
                "qttools5-dev-tools",
                "qtchooser",
                "qt5-qmake",
                "libsqlite3-dev",
            ])
            notes.append("sqlitebrowser Qt5 stack enabled")

        elif project_name == "bazel":
            # Bazel 项目不要依赖 apt 里的 bazel 包，统一走 bazel bootstrap
            apt_packages.extend([
                "curl",
                "unzip",
                "zip",
                "openjdk-17-jdk",
                "build-essential",
                "pkg-config",
            ])
            preinstall_commands.append(self._bazel_bootstrap_command())
            notes.append("bazel project-specific bootstrap enabled")

        elif project_name in {
            "openpose",
            "paddle",
            "mxnet",
            "scylladb",
            "arangodb",
            "foundationdb",
            "rocksdb",
            "duckdb",
            "libjxl",
            "aseprite",
            "tesseract",
            "xgboost",
            "rethinkdb",
            "elements",
            "blender",
            "dearpygui",
            "godot",
            "gameplay",
            "raylib",
            "simdjson",
            "reactos",
            "rt-thread",
            "seq",
        }:
            notes.append(f"large project hint: {project_name}")

        return _dedupe(apt_packages), _dedupe(preinstall_commands), _dedupe(build_commands), _dedupe(notes)

    def _infer_packages_from_commands(
        self,
        *,
        build_commands: List[str],
        test_commands: List[str],
        preinstall_commands: List[str],
        notes: List[str],
        source_root_rel: str,
        build_system: str,
        project_name: str,
    ) -> List[str]:
        """
        从命令/备注推断依赖。

        Bazel 项目：直接走白名单，绝不把 cmake / boost / protobuf 这类噪声依赖推进去。
        同时收紧音频/视频推断，避免 ffmpeg / libavcodec / libavif 等被过度注入。
        """
        text = "\n".join(build_commands + test_commands + preinstall_commands + notes).lower()

        if build_system == "bazel":
            packages: List[str] = []
            if "python3" in text or "python" in text:
                packages.extend(["python3", "python3-pip", "python3-venv"])
            if "java" in text or "openjdk" in text:
                packages.append("openjdk-17-jdk")
            packages.extend(["curl", "unzip", "zip", "build-essential", "pkg-config"])
            return _dedupe(packages)

        packages: List[str] = []

        # 常见工具
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

        # 构建系统
        if re.search(r"\bcmake\b", text) or build_system == "cmake":
            packages.append("cmake")
            if source_root_rel != ".":
                packages.append("ninja-build")
        if re.search(r"\bmeson\b", text) or build_system == "meson":
            packages.append("meson")
            packages.append("ninja-build")
        if re.search(r"\bninja\b", text):
            packages.append("ninja-build")
        if re.search(r"\bmake\b", text) or build_system == "make":
            packages.append("make")
        if re.search(r"\bconfigure\b", text) or re.search(r"\bautogen\b", text) or build_system == "autotools":
            packages.extend(["autoconf", "automake", "libtool", "gettext", "bison", "flex", "make", "dos2unix"])
        if re.search(r"\bpkg-config\b", text):
            packages.append("pkg-config")
        if re.search(r"\bpython3\b", text) or re.search(r"\bpip\b", text) or re.search(r"\bpython\b", text):
            packages.extend(["python3", "python3-pip", "python3-venv"])
        if re.search(r"\bscons\b", text) or build_system == "scons":
            packages.append("scons")

        # Qt / GUI
        if "qt6" in text:
            packages.extend(["qt6-base-dev", "qt6-tools-dev", "qt6-tools-dev-tools"])
        if "qt5" in text:
            packages.extend(
                [
                    "qtbase5-dev",
                    "qttools5-dev-tools",
                    "qtchooser",
                    "qt5-qmake",
                    "libqt5svg5-dev",
                    "libqt5x11extras5-dev",
                    "libdbus-1-dev",
                ]
            )
        if any(k in text for k in ("gtk", "gdk", "cairo", "pango", "glib", "gdk-pixbuf")):
            packages.extend(
                [
                    "libgtk-3-dev",
                    "libgtk-4-dev",
                    "libglib2.0-dev",
                    "libgdk-pixbuf-2.0-dev",
                    "libcairo2-dev",
                    "libpango1.0-dev",
                    "libdbus-1-dev",
                ]
            )
        if "boost" in text:
            packages.append("libboost-all-dev")

        # 图形 / OpenGL：使用词边界匹配，避免误匹配
        if re.search(r"\bx11\b", text) or re.search(r"\bxrandr\b", text) or re.search(r"\bxrender\b", text) or re.search(r"\bxcb\b", text) or re.search(r"\bwayland\b", text):
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
                ]
            )
        if re.search(r"\bopengl\b", text) or re.search(r"\bglfw\b", text) or re.search(r"\bglew\b", text) or re.search(r"\bglu\b", text):
            packages.extend(["libgl1-mesa-dev", "libglu1-mesa-dev", "libglew-dev", "libglfw3-dev"])

        # 音频：收紧，不再用泛化的 "audio" / "video" 直接拉一大堆包
        if "alsa" in text:
            packages.append("libasound2-dev")
        if "pulse" in text:
            packages.append("libpulse-dev")
        if "sndfile" in text:
            packages.append("libsndfile1-dev")
        if "samplerate" in text:
            packages.append("libsamplerate0-dev")
        if "ogg" in text:
            packages.append("libogg-dev")
        if "vorbis" in text:
            packages.append("libvorbis-dev")
        if "flac" in text:
            packages.append("libflac-dev")
        if "opus" in text:
            packages.append("libopus-dev")

        # 仅在显式出现时才加入 ffmpeg / libav*，避免过度安装
        if "ffmpeg" in text:
            packages.append("ffmpeg")
        if "libavcodec" in text:
            packages.append("libavcodec-dev")
        if "libavformat" in text:
            packages.append("libavformat-dev")
        if "libavutil" in text:
            packages.append("libavutil-dev")
        if "libswresample" in text:
            packages.append("libswresample-dev")
        if "libswscale" in text:
            packages.append("libswscale-dev")

        # 常见库
        if "openssl" in text or "ssl" in text:
            packages.append("libssl-dev")
        if "curl" in text:
            packages.append("libcurl4-openssl-dev")
        if "sqlite" in text:
            packages.append("libsqlite3-dev")
        if "protobuf" in text:
            packages.extend(["protobuf-compiler", "libprotobuf-dev"])
        if "zlib" in text:
            packages.append("zlib1g-dev")
        if "jsoncpp" in text:
            packages.append("libjsoncpp-dev")
        if "yaml" in text:
            packages.append("libyaml-cpp-dev")
        if "tinyxml2" in text:
            packages.append("libtinyxml2-dev")
        if "libusb" in text:
            packages.append("libusb-1.0-0-dev")

        # 项目级补充
        if project_name == "gcc":
            packages.extend(["libgmp-dev", "libmpfr-dev", "libmpc-dev", "libisl-dev", "texinfo", "flex", "bison", "gawk", "perl", "patch", "m4", "tar", "xz-utils"])
        if project_name == "linux":
            packages.extend(["bc", "bison", "flex", "libssl-dev", "libelf-dev", "cpio", "rsync", "dwarves", "perl", "tar", "xz-utils"])
        if project_name == "openalpr":
            packages.extend(["libopencv-dev", "libleptonica-dev", "libtesseract-dev", "tesseract-ocr", "liblog4cplus-dev", "libjsoncpp-dev"])
        if project_name == "tensorflow":
            packages.extend(["libxml2-dev", "libedit-dev", "libncurses5-dev", "zlib1g-dev", "libffi-dev"])
        if project_name == "luanti":
            packages.extend(["libsdl2-dev", "libopenal-dev", "libfreetype6-dev", "libvorbis-dev", "libogg-dev", "libjpeg-dev", "libpng-dev", "libsqlite3-dev", "libluajit-5.1-dev"])
        if project_name == "sqlitebrowser":
            packages.extend(["qtbase5-dev", "qttools5-dev-tools", "qtchooser", "qt5-qmake", "libsqlite3-dev"])

        # 仅在显式出现时加入文档工具
        if "doc" in text or "docs" in text:
            packages.extend(["doxygen", "graphviz"])
        if "fetchcontent" in text or "externalproject" in text:
            packages.append("git")

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
                "    cp -a /etc/apt/sources.list /etc/apt/sources.list.bak 2>/dev/null || true; \\",
                "    cp -a /etc/apt/sources.list.d/ubuntu.sources /etc/apt/sources.list.d/ubuntu.sources.bak 2>/dev/null || true; \\",
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
                "    if ! apt-get update; then \\",
                '        echo "[APT] mirror failed, fallback to official Ubuntu sources"; \\',
                '        if [ -f /etc/apt/sources.list.bak ]; then cp -f /etc/apt/sources.list.bak /etc/apt/sources.list; fi; \\',
                '        if [ -f /etc/apt/sources.list.d/ubuntu.sources.bak ]; then cp -f /etc/apt/sources.list.d/ubuntu.sources.bak /etc/apt/sources.list.d/ubuntu.sources; fi; \\',
                "        apt-get update; \\",
                "    fi; \\",
                "    apt-get install -y --no-install-recommends \\",
                pkg_lines,
                "    && rm -rf /var/lib/apt/lists/*",
            ]
        )

    # ---------------------------------------------------------------------
    # 小工具
    # ---------------------------------------------------------------------
    def _read_text_file(self, path: Any) -> str:
        if not path:
            return ""
        try:
            p = Path(str(path))
            if p.exists() and p.is_file():
                return p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            pass
        return ""

    @staticmethod
    def _escape(s: str) -> str:
        return str(s).replace("\\", "\\\\").replace('"', '\\"')

    @staticmethod
    def _safe_symbol(name: str) -> str:
        name = str(name or "").strip()
        if not name:
            return "EXTERNAL_DEP"
        return re.sub(r"[^A-Za-z0-9]+", "_", name).upper().strip("_") or "EXTERNAL_DEP"

    @staticmethod
    def _safe_path(name: str) -> str:
        name = str(name or "").strip().lower()
        if not name:
            return "external_dep"
        return re.sub(r"[^a-z0-9._-]+", "_", name).strip("_") or "external_dep"