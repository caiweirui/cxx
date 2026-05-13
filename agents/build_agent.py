from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .base_agent import BaseAgent

try:
    from cxxcrafter.rag.rag_service import RAGService
except Exception:
    RAGService = None  # type: ignore

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

    parts.append(project_name)
    parts.append(build_system)
    if isinstance(files_sample, list):
        parts.extend([str(x) for x in files_sample[:200]])
    else:
        parts.append(str(files_sample))

    return "\n".join(parts).lower()

def _contains_any(haystack: str, keywords: List[str]) -> bool:
    low = haystack.lower()
    return any(k.lower() in low for k in keywords)

@dataclass
class BuildPlan:
    base_image: str = "ubuntu:24.04"
    workdir: str = "/workspace"
    copy_paths: List[str] = field(default_factory=lambda: ["."])
    preinstall_commands: List[str] = field(default_factory=list)
    build_commands: List[str] = field(default_factory=list)
    test_commands: List[str] = field(default_factory=list)
    runtime_command: str = ""
    env: Dict[str, str] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    confidence: float = 0.0
    raw: Dict[str, Any] = field(default_factory=dict)

class BuildAgent(BaseAgent):
    """
    输入：项目快照 + 依赖分析 + RAG 文档上下文
    输出：构建计划（不是 Dockerfile）
    """

    def __init__(
        self,
        bot: Any = None,
        model_name: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: float = 0.2,
        rag_service: Any = None,
    ) -> None:
        super().__init__(
            bot=bot,
            model_name=model_name,
            api_key=api_key,
            base_url=base_url,
            temperature=temperature,
        )
        self.rag_service = rag_service

    def plan(self, snapshot: Dict[str, Any], deps: Dict[str, Any]) -> BuildPlan:
        project_path = str(snapshot.get("project_path", "") or "")
        files_sample = snapshot.get("files_sample", []) or []

        docs_context = ""
        if self.rag_service is not None and project_path:
            try:
                docs_context = self.rag_service.build_project_context(
                    project_path=project_path,
                    files_sample=files_sample,
                )
            except Exception:
                docs_context = ""

        prompt = f"""
你是构建策略智能体，只输出 JSON，不要输出解释文字。

目标：根据项目快照、依赖分析和文档检索结果生成构建计划。
约束：
1. 只输出计划，不要直接输出 Dockerfile
2. build_commands 只放关键构建命令
3. 如果是 cmake 项目，优先使用 cmake -S ... -B build / cmake --build build
4. 如果信息不足，保持保守，不要乱猜
5. 文档检索结果可作为强参考，但不要违背构建系统类型

JSON 结构：
{{
  "base_image": "ubuntu:24.04",
  "workdir": "/workspace",
  "copy_paths": ["."],
  "preinstall_commands": ["..."],
  "build_commands": ["..."],
  "test_commands": ["..."],
  "runtime_command": "xxx",
  "env": {{"KEY":"VALUE"}},
  "notes": ["..."],
  "confidence": 0.0
}}

项目快照：
{snapshot}

依赖分析：
{deps}

RAG 文档上下文：
{docs_context or "无"}
""".strip()

        default = {
            "base_image": "ubuntu:24.04",
            "workdir": "/workspace",
            "copy_paths": [snapshot.get("source_root_rel", ".")],
            "preinstall_commands": [],
            "build_commands": [],
            "test_commands": [],
            "runtime_command": "",
            "env": {},
            "notes": [],
            "confidence": 0.0,
        }

        resp = self.generate_json(prompt, default=default)
        data = resp.data or {}

        try:
            confidence = float(data.get("confidence", 0.0) or 0.0)
        except Exception:
            confidence = 0.0

        build_system = str(snapshot.get("build_system", "") or "").lower()
        source_root_rel = str(snapshot.get("source_root_rel", ".") or ".").strip() or "."
        text = _project_text(snapshot)

        qt_hint = _contains_any(
            text,
            [
                "qt",
                "qwidget",
                "qmainwindow",
                "qapplication",
                "qt5",
                "qt6",
                "mainwindow",
                ".ui",
                ".qrc",
                "qml",
                "flameshot",
                "dbus",
                "x11",
                "xcb",
                "wayland",
            ],
        )

        library_like = _contains_any(
            text,
            [
                "entt",
                "header-only",
                "library",
                "include/",
            ],
        )

        has_tests = _contains_any(
            text,
            [
                "tests/",
                "/tests",
                "test/",
                "/test",
                "gtest",
                "catch2",
                "doctest",
            ],
        )

        copy_paths = _as_str_list(data.get("copy_paths", [source_root_rel]))
        if not copy_paths:
            copy_paths = [source_root_rel]

        env = dict(data.get("env", {}) or {})
        notes = _as_str_list(data.get("notes", []))

        base_image = str(data.get("base_image", "ubuntu:24.04") or "ubuntu:24.04").strip()
        workdir = str(data.get("workdir", "/workspace") or "/workspace").strip()

        preinstall_commands = _as_str_list(data.get("preinstall_commands", []))
        build_commands = _as_str_list(data.get("build_commands", []))
        test_commands = _as_str_list(data.get("test_commands", []))
        runtime_command = str(data.get("runtime_command", "") or "").strip()

        if build_system == "cmake":
            cmake_args = _as_str_list(deps.get("cmake_args", []))
            if not cmake_args:
                cmake_args = ["-DCMAKE_BUILD_TYPE=Release"]

            quoted_src = shlex.quote(source_root_rel)
            configure_cmd = f"cmake -S {quoted_src} -B build " + " ".join(cmake_args)
            compile_cmd = "cmake --build build -j$(nproc)"

            if not build_commands:
                build_commands = [configure_cmd, compile_cmd]
            else:
                if not any("cmake -s" in c.lower() and "-b build" in c.lower() for c in build_commands):
                    build_commands.insert(0, configure_cmd)
                if not any("cmake --build build" in c.lower() for c in build_commands):
                    build_commands.append(compile_cmd)

            if has_tests and not test_commands:
                test_commands = ["ctest --test-dir build --output-on-failure"]

            notes.append(f"CMake source root: {source_root_rel}")

            if qt_hint:
                notes.append("Qt/GUI project detected; keep runtime command empty unless an executable target is known.")
                runtime_command = runtime_command or ""

            if library_like and not qt_hint:
                notes.append("Library/header-only-like project; keep build plan minimal.")
                runtime_command = runtime_command or ""

        elif build_system == "make":
            if not build_commands:
                build_commands = ["make -j$(nproc)"]
            if has_tests and not test_commands:
                test_commands = ["make test"]

        elif build_system == "node":
            if not build_commands:
                build_commands = ["npm install"]
            if has_tests and not test_commands:
                test_commands = ["npm test"]

        elif build_system == "python":
            if not build_commands:
                build_commands = ["python3 -m pip install --no-cache-dir -r requirements.txt"]
            if has_tests and not test_commands:
                test_commands = ["pytest -q"]

        if qt_hint:
            notes.append("Detected Qt/desktop GUI hints from project tree.")
        if library_like and not qt_hint:
            notes.append("Detected library-oriented project structure.")

        if docs_context:
            notes.append("RAG document context injected into planning stage.")

        return BuildPlan(
            base_image=base_image,
            workdir=workdir,
            copy_paths=copy_paths,
            preinstall_commands=preinstall_commands,
            build_commands=build_commands,
            test_commands=test_commands,
            runtime_command=runtime_command,
            env=env,
            notes=notes,
            confidence=confidence,
            raw={**data, "rag_docs_context": docs_context},
        )