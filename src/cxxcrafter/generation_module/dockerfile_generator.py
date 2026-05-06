# src/cxxcrafter/generation_module/dockerfile_generator.py
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from cxxcrafter.agents.build_agent import BuildPlan
from cxxcrafter.agents.dependency_agent import DependencyAnalysis

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
    规则化 Dockerfile 生成器：
    - 只接受结构化计划
    - 输出稳定、可预测
    - 默认增强 apt 稳定性（重试 + 镜像源）
    - 额外写入项目 family / feature tags，减少“所有 Dockerfile 长得一样”
    """

    DEFAULT_APT_MIRROR = "http://mirrors.tuna.tsinghua.edu.cn/ubuntu"
    DEFAULT_APT_RETRIES = 5
    DEFAULT_APT_HTTP_TIMEOUT = 30

    def __init__(self, project_root: str) -> None:
        self.project_root = Path(project_root)

    def render(self, deps: DependencyAnalysis, plan: BuildPlan, snapshot: Dict[str, Any]) -> str:
        base_image = (plan.base_image or "ubuntu:22.04").strip()
        workdir = self._normalize_workdir(plan.workdir or "/workspace")

        apt_packages = _dedupe(list(deps.apt_packages or []))
        pip_packages = _dedupe(list(deps.pip_packages or []))

        # 如果有 pip 依赖，自动补齐 Python 运行环境
        if pip_packages:
            apt_packages = _dedupe(["python3", "python3-pip", "python3-venv"] + apt_packages)

        # 统一基础工具
        base_tools = ["ca-certificates", "curl", "git", "pkg-config"]
        apt_packages = _dedupe(base_tools + apt_packages)

        project_family = (
            getattr(plan, "project_family", "") or
            getattr(deps, "project_family", "") or
            "generic"
        ).strip()

        feature_tags = _dedupe(
            list(getattr(plan, "feature_tags", []) or [])
            + list(getattr(deps, "feature_tags", []) or [])
        )

        build_system = str(snapshot.get("build_system", "") or "unknown").strip()

        lines: List[str] = []
        lines.append(f"FROM {base_image}")
        lines.append("ENV DEBIAN_FRONTEND=noninteractive")
        lines.append('SHELL ["/bin/bash", "-o", "pipefail", "-c"]')
        lines.append(f"WORKDIR {workdir}")

        # 额外标签：让 Dockerfile 更有“项目特征”
        lines.append(f'LABEL org.cxxcrafter.project_name="{self._escape(str(snapshot.get("project_name", "") or ""))}"')
        lines.append(f'LABEL org.cxxcrafter.project_family="{self._escape(project_family)}"')
        lines.append(f'LABEL org.cxxcrafter.build_system="{self._escape(build_system)}"')
        if feature_tags:
            lines.append(f'LABEL org.cxxcrafter.feature_tags="{self._escape(",".join(feature_tags))}"')

        if deps.notes:
            lines.append(f"# dependency_notes: {self._escape(' | '.join(_dedupe(deps.notes)[:12]))}")
        if plan.notes:
            lines.append(f"# build_notes: {self._escape(' | '.join(_dedupe(plan.notes)[:12]))}")

        # APT 配置：重试 + 镜像源替换
        apt_step = self._render_apt_setup_step(apt_packages)
        if apt_step:
            lines.append(apt_step)

        # 环境变量
        for k, v in (plan.env or {}).items():
            lines.append(f'ENV {k}="{self._escape(v)}"')
        for k, v in (deps.env or {}).items():
            if k not in (plan.env or {}):
                lines.append(f'ENV {k}="{self._escape(v)}"')

        # 复制源代码
        copy_paths = plan.copy_paths or [snapshot.get("source_root_rel", ".") or "."]
        for src in _dedupe(copy_paths):
            lines.append(self._render_copy_line(src, workdir))

        # 预安装命令
        for cmd in _dedupe(plan.preinstall_commands):
            rendered = self._render_run(cmd)
            if rendered:
                lines.append(rendered)

        # Python 包
        if pip_packages:
            lines.append(self._render_run("python3 -m pip install --no-cache-dir --upgrade pip setuptools wheel"))
            lines.append(self._render_run("python3 -m pip install --no-cache-dir " + " ".join(pip_packages)))

        # 构建命令
        for cmd in _dedupe(plan.build_commands):
            rendered = self._render_run(cmd)
            if rendered:
                lines.append(rendered)

        # 测试/验证命令
        for cmd in _dedupe(plan.test_commands):
            rendered = self._render_run(cmd)
            if rendered:
                lines.append(rendered)

        # 默认运行命令（可选）
        if plan.runtime_command:
            lines.append(f'CMD ["sh", "-lc", "{self._escape(plan.runtime_command)}"]')

        return "\n".join(lines) + "\n"

    def save(self, dockerfile_text: str, out_path: str) -> str:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(dockerfile_text, encoding="utf-8")
        return str(out)

    def _render_apt_setup_step(self, apt_packages: List[str]) -> str:
        """
        稳定的 apt 安装步骤：
        - 写入重试配置
        - 尝试替换 Ubuntu 源为镜像源
        - 合并安装基础工具 + 依赖

        关键点：
        1) 使用 http 镜像，避免证书 bootstrap 问题
        2) 最后一行包名也必须带反斜杠，避免 `&&` 被 Docker 误解析成新指令
        """
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
        src = str(src).strip() or "."
        if src == ".":
            return f"COPY . {self._normalize_workdir(workdir)}/"

        normalized_src = src[2:] if src.startswith("./") else src
        normalized_src = normalized_src.lstrip("/")

        dest = f"{self._normalize_workdir(workdir)}/{normalized_src}".rstrip("/")
        return f"COPY {normalized_src} {dest}"

    def _render_run(self, cmd: str) -> str:
        cmd = str(cmd).strip()
        if not cmd:
            return ""
        return f"RUN set -eux; {cmd}"

    def _normalize_workdir(self, workdir: str) -> str:
        workdir = str(workdir).strip()
        if not workdir:
            return "/workspace"
        return workdir.rstrip("/") or "/"

    @staticmethod
    def _escape(s: str) -> str:
        return str(s).replace("\\", "\\\\").replace('"', '\\"')