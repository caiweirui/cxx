from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Dict, List, Optional

from .base_agent import BaseAgent
from .build_agent import BuildPlan

try:
    from cxxcrafter.rag.rag_service import RAGService
except Exception:
    RAGService = None  # type: ignore

def _as_str_list(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, tuple):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, set):
        return [str(x).strip() for x in value if str(x).strip()]
    return [str(value).strip()] if str(value).strip() else []

def _merge_unique(*lists: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for lst in lists:
        if not lst:
            continue
        for item in lst:
            item = str(item).strip()
            if not item or item in seen:
                continue
            seen.add(item)
            out.append(item)
    return out

def _truncate(text: str, limit: int = 5000) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."

def _to_dict(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "__dict__"):
        try:
            return dict(value.__dict__)
        except Exception:
            return {}
    return {}

@dataclass
class RepairPatch:
    add_apt_packages: List[str] = field(default_factory=list)
    add_pip_packages: List[str] = field(default_factory=list)
    add_preinstall_commands: List[str] = field(default_factory=list)
    add_build_commands: List[str] = field(default_factory=list)
    add_test_commands: List[str] = field(default_factory=list)
    remove_build_commands: List[str] = field(default_factory=list)
    remove_test_commands: List[str] = field(default_factory=list)
    replace_base_image: str = ""
    notes: List[str] = field(default_factory=list)
    confidence: float = 0.0
    raw: Dict[str, Any] = field(default_factory=dict)

class DockerfileRepairAgent(BaseAgent):
    """
    根据错误分析给 BuildPlan 打补丁，并使用 RAG 进行历史经验增强。

    重点增强：
    - 识别 COPY / 路径空格 / source_root 子目录问题
    - 识别 autotools 的 CRLF / bad interpreter 问题
    - 识别常见依赖缺失
    - 对“构建失败但验证未开始”的情况更保守地做最小修复
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

    def suggest_patch(
        self,
        snapshot: Dict[str, Any],
        current_plan: BuildPlan,
        failure: Dict[str, Any],
    ) -> RepairPatch:
        snapshot = _to_dict(snapshot)
        failure = _to_dict(failure)

        error_query = self._extract_error_query(failure)
        project_path = str(snapshot.get("project_path", "") or "")
        rag_context = self._extract_rag_context(failure)

        if self.rag_service is not None and not rag_context and error_query:
            try:
                rag_context = self.rag_service.build_error_context(
                    error_text=error_query,
                    project_path=project_path,
                    files_sample=snapshot.get("files_sample", []) or [],
                )
            except Exception:
                rag_context = ""

        prompt = f"""
你是 Dockerfile / BuildPlan 修复智能体，只输出 JSON，不要输出解释文字。

目标：
- 基于当前构建计划、失败诊断和 RAG 历史案例，输出“最小修复补丁”
- 只修正导致失败的点
- 不要重写完整计划
- 不要引入不必要的新依赖
- 如果某个 test/build 命令明显错误，优先移除，而不是强行修复
- 如果失败是 Dockerfile 生成问题（例如 COPY 路径、空格、CRLF、bad interpreter），优先给出最小增量修复建议
- 如果失败是 source_root_rel 子目录问题，优先通过保持相对路径 / 进入子目录解决，不要盲目加包

特别注意本次常见失败模式：
1) COPY 路径空格 / JSON array COPY：
   - 这类问题通常不是缺包
   - 如果看到 failed to calculate checksum / COPY / not found / /workspace/test 之类错误，
     优先在 notes 里说明“需要 JSON array COPY / 正确处理 source_root_rel”
2) autotools CRLF：
   - 如果看到 bad interpreter /bin/sh^M / CRLF / carriage return，
     优先添加预处理命令：
       sed -i 's/\\r$//' ./autogen.sh ./configure 2>/dev/null || true
       chmod +x ./autogen.sh ./configure 2>/dev/null || true
3) build 失败但验证未开始：
   - 只做 build 侧最小修复
4) 如果错误明显来自 cmake/ninja/pkg-config 缺失，再补最少必要依赖

JSON 结构：
{{
  "add_apt_packages": ["..."],
  "add_pip_packages": ["..."],
  "add_preinstall_commands": ["..."],
  "add_build_commands": ["..."],
  "add_test_commands": ["..."],
  "remove_build_commands": ["..."],
  "remove_test_commands": ["..."],
  "replace_base_image": "",
  "notes": ["..."],
  "confidence": 0.0
}}

项目快照：
{json.dumps(snapshot, ensure_ascii=False, indent=2)}

当前构建计划：
{current_plan}

失败分析：
{_truncate(json.dumps(failure, ensure_ascii=False, indent=2), 12000)}

RAG 历史案例参考：
{rag_context or "无"}
""".strip()

        default = {
            "add_apt_packages": [],
            "add_pip_packages": [],
            "add_preinstall_commands": [],
            "add_build_commands": [],
            "add_test_commands": [],
            "remove_build_commands": [],
            "remove_test_commands": [],
            "replace_base_image": "",
            "notes": [],
            "confidence": 0.0,
        }

        resp = self.generate_json(prompt, default=default)
        data = resp.data or {}

        heuristic = self._heuristic_patch_from_failure(current_plan=current_plan, failure=failure)

        try:
            confidence = float(data.get("confidence", 0.0) or 0.0)
        except Exception:
            confidence = 0.0

        llm_patch = RepairPatch(
            add_apt_packages=_as_str_list(data.get("add_apt_packages", [])),
            add_pip_packages=_as_str_list(data.get("add_pip_packages", [])),
            add_preinstall_commands=_as_str_list(data.get("add_preinstall_commands", [])),
            add_build_commands=_as_str_list(data.get("add_build_commands", [])),
            add_test_commands=_as_str_list(data.get("add_test_commands", [])),
            remove_build_commands=_as_str_list(data.get("remove_build_commands", [])),
            remove_test_commands=_as_str_list(data.get("remove_test_commands", [])),
            replace_base_image=str(data.get("replace_base_image", "") or ""),
            notes=_as_str_list(data.get("notes", [])),
            confidence=confidence,
            raw=data,
        )

        final_patch = self._merge_patches(llm_patch, heuristic)

        # 回写 RAG 成功/失败经验
        if self.rag_service is not None and error_query:
            try:
                project_name = str(snapshot.get("project_name") or snapshot.get("project_path") or "unknown")
                solution_text = json.dumps(
                    {
                        "add_apt_packages": final_patch.add_apt_packages,
                        "add_pip_packages": final_patch.add_pip_packages,
                        "add_preinstall_commands": final_patch.add_preinstall_commands,
                        "add_build_commands": final_patch.add_build_commands,
                        "add_test_commands": final_patch.add_test_commands,
                        "remove_build_commands": final_patch.remove_build_commands,
                        "remove_test_commands": final_patch.remove_test_commands,
                        "replace_base_image": final_patch.replace_base_image,
                        "notes": final_patch.notes,
                        "confidence": final_patch.confidence,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                self.rag_service.record_case(
                    error_text=error_query,
                    solution=solution_text,
                    project=project_name,
                )
            except Exception:
                pass

        return final_patch

    def _extract_error_query(self, failure: Dict[str, Any]) -> str:
        parts: List[str] = []

        # 优先抓 raw 内的 build_log / dockerfile_text / rag_context
        raw = failure.get("raw", {})
        if isinstance(raw, dict):
            for key in ("build_log", "build_log_excerpt", "dockerfile_text", "message", "rag_context"):
                v = raw.get(key)
                if v:
                    parts.append(str(v))

        # 兼容 flat 字段
        for key in (
            "likely_causes",
            "suggested_actions",
            "notes",
            "raw",
            "message",
            "build_log",
            "build_log_excerpt",
            "dockerfile_text",
        ):
            v = failure.get(key)
            if isinstance(v, (list, tuple, set)):
                parts.extend([str(x) for x in v])
            elif v is not None and not isinstance(v, dict):
                parts.append(str(v))

        text = "\n".join(parts).strip()
        return _truncate(text, 4000)

    def _extract_rag_context(self, failure: Dict[str, Any]) -> str:
        raw = failure.get("raw", {})
        if isinstance(raw, dict):
            rag_context = raw.get("rag_context", "")
            if rag_context:
                return str(rag_context)

        rag_context = failure.get("rag_context", "")
        return str(rag_context or "")

    def _heuristic_patch_from_failure(self, current_plan: BuildPlan, failure: Dict[str, Any]) -> RepairPatch:
        text_parts: List[str] = []

        for key in (
            "likely_causes",
            "suggested_actions",
            "notes",
            "raw",
            "message",
            "build_log",
            "build_log_excerpt",
            "dockerfile_text",
            "verification_log",
        ):
            v = failure.get(key)
            if isinstance(v, (list, tuple, set)):
                text_parts.extend([str(x) for x in v])
            elif v is not None:
                text_parts.append(str(v))

        raw = failure.get("raw", {})
        if isinstance(raw, dict):
            for key in ("build_log", "build_log_excerpt", "dockerfile_text", "rag_context"):
                v = raw.get(key)
                if v:
                    text_parts.append(str(v))

        text = "\n".join(text_parts).lower()

        patch = RepairPatch()

        def add_pkg(pkg: str) -> None:
            if pkg and pkg not in patch.add_apt_packages:
                patch.add_apt_packages.append(pkg)

        def add_pre(cmd: str) -> None:
            if cmd and cmd not in patch.add_preinstall_commands:
                patch.add_preinstall_commands.append(cmd)

        def add_note(note: str) -> None:
            if note and note not in patch.notes:
                patch.notes.append(note)

        # ---------- 常见工具缺失 ----------
        tool_pkg_map = [
            (r"\bcmake\b.*(not found|command not found|missing)", "cmake"),
            (r"\bninja\b.*(not found|command not found|missing)", "ninja-build"),
            (r"\bpkg-config\b.*(not found|command not found|missing)", "pkg-config"),
            (r"\bmake\b.*(not found|command not found|missing)", "make"),
            (r"\bg\+\+\b.*(not found|command not found|missing)", "build-essential"),
            (r"\bgcc\b.*(not found|command not found|missing)", "build-essential"),
            (r"\bpython3\b.*(not found|command not found|missing)", "python3"),
            (r"\bpip\b.*(not found|command not found|missing)", "python3-pip"),
        ]
        for pattern, pkg in tool_pkg_map:
            if re.search(pattern, text, re.I):
                add_pkg(pkg)

        if "module not found" in text and ("python" in text or "pip" in text):
            add_pkg("python3")
            add_pkg("python3-pip")
            add_pkg("python3-venv")

        # ---------- COPY / 路径 / source_root 问题 ----------
        if any(
            s in text
            for s in [
                "failed to calculate checksum",
                "copy failed",
                "no such file or directory",
                "/workspace/test",
                "not found",
                "can't stat",
            ]
        ) and ("copy" in text or "dockerfile" in text or "/workspace/" in text):
            add_note("Detected Docker COPY/path failure: use JSON-array COPY and keep source_root_rel as a quoted relative path.")
            add_note("If source_root_rel contains spaces, COPY must not be rendered as `COPY a b`; use `COPY [\"a\", \"b\"]`.")

        # ---------- autotools / CRLF / bad interpreter ----------
        if any(
            s in text
            for s in [
                "bad interpreter",
                "/bin/sh^m",
                "carriage return",
                "crlf",
                "dos line endings",
                "mismatched shebang",
            ]
        ):
            add_pre(r"sed -i 's/\r$//' ./autogen.sh ./configure 2>/dev/null || true")
            add_pre(r"chmod +x ./autogen.sh ./configure 2>/dev/null || true")
            add_note("CRLF / bad interpreter detected; normalize autogen.sh/configure line endings before running autotools scripts.")

        # ---------- autotools / invalid test target ----------
        if "no rule to make target 'test'" in text or 'no rule to make target "test"' in text or "target 'test' not found" in text:
            patch.remove_test_commands = _merge_unique(
                patch.remove_test_commands,
                [cmd for cmd in current_plan.test_commands if self._looks_like_test_target(cmd)],
            )
            add_note("Remove invalid `test` target or replace it with `ctest --output-on-failure` only when tests are configured.")

        if "ctest" in text and ("failed" in text or "error" in text):
            add_note("CTest failed; verify that tests are actually enabled and runtime dependencies are present.")

        # ---------- linker ----------
        if "undefined reference" in text or "ld:" in text or "linker command failed" in text:
            add_note("Linker failure detected; check missing system libraries or link order.")

        # ---------- APT 网络 ----------
        if any(
            x in text
            for x in [
                "502 bad gateway",
                "failed to fetch",
                "repository",
                "inrelease",
                "not signed",
                "certificate verification failed",
                "temporary failure resolving",
                "could not resolve",
            ]
        ):
            add_note("APT repository/network failure detected.")
            add_note("Prefer HTTP mirror during bootstrap and keep apt steps minimal.")
            add_note("Re-render Dockerfile with retry settings instead of adding more packages.")

        # ---------- Qt / Boost / X11 / OpenGL ----------
        if "could not find boost" in text or "boost_system" in text or "boost_thread" in text or "findpackage(boost" in text:
            add_pkg("libboost-all-dev")

        if "could not find x11" in text or "findx11.cmake" in text or "missing: x11_x11_include_path" in text or "missing: x11_x11_lib" in text:
            add_pkg("libx11-dev")
            add_pkg("libxext-dev")
            add_pkg("libxrender-dev")
            add_pkg("libxrandr-dev")
            add_pkg("libxcursor-dev")
            add_pkg("libxi-dev")
            add_pkg("libxkbcommon-x11-dev")
            add_pkg("libxinerama-dev")
            add_pkg("libwayland-dev")
            add_pkg("xorg-dev")

        if "could not find opengl" in text or "could not find glu" in text or "could not find glfw" in text or "could not find glew" in text:
            add_pkg("libgl1-mesa-dev")
            add_pkg("libglu1-mesa-dev")
            add_pkg("libglew-dev")
            add_pkg("libglfw3-dev")

        if "could not find qt6" in text or "qt6config.cmake" in text or "qt6-config.cmake" in text or "qt6::" in text:
            add_pkg("qt6-base-dev")
            add_pkg("qt6-tools-dev")
            add_pkg("qt6-tools-dev-tools")

        if "could not find qt5" in text or "qt5config.cmake" in text or "qt5-config.cmake" in text:
            add_pkg("qtbase5-dev")
            add_pkg("qttools5-dev-tools")
            add_pkg("qtchooser")
            add_pkg("qt5-qmake")
            add_pkg("libqt5svg5-dev")
            add_pkg("libqt5x11extras5-dev")

        # ---------- 生成器层面的提示 ----------
        if "copy [" in text or "copy " in text and " /workspace/" in text:
            add_note("The generator should emit COPY in JSON array form for paths with spaces and subdirectories.")
        if "source_root_rel" in text and "not found" in text:
            add_note("Ensure commands are run under the correct source_root_rel and paths are quoted when entering subdirectories.")

        return patch

    @staticmethod
    def _looks_like_test_target(cmd: str) -> bool:
        c = (cmd or "").lower().strip()
        if "ctest" in c:
            return True
        if re.search(r"\btest\b", c):
            return True
        return False

    def _merge_patches(self, a: RepairPatch, b: RepairPatch) -> RepairPatch:
        return RepairPatch(
            add_apt_packages=_merge_unique(a.add_apt_packages, b.add_apt_packages),
            add_pip_packages=_merge_unique(a.add_pip_packages, b.add_pip_packages),
            add_preinstall_commands=_merge_unique(a.add_preinstall_commands, b.add_preinstall_commands),
            add_build_commands=_merge_unique(a.add_build_commands, b.add_build_commands),
            add_test_commands=_merge_unique(a.add_test_commands, b.add_test_commands),
            remove_build_commands=_merge_unique(a.remove_build_commands, b.remove_build_commands),
            remove_test_commands=_merge_unique(a.remove_test_commands, b.remove_test_commands),
            replace_base_image=a.replace_base_image or b.replace_base_image,
            notes=_merge_unique(a.notes, b.notes),
            confidence=max(a.confidence, b.confidence),
            raw={**b.raw, **a.raw},
        )

    def _apply_patch(
        self,
        plan: BuildPlan,
        deps: Any,
        patch: RepairPatch,
        failure: Any,
    ):
        from dataclasses import replace

        failure_dict = _to_dict(failure)
        deps_dict = _to_dict(deps)

        def get_list(d: Dict[str, Any], key: str) -> List[str]:
            return _as_str_list(d.get(key, []))

        new_deps = replace(
            deps,
            apt_packages=self._merge_str_lists(
                get_list(deps_dict, "apt_packages"),
                patch.add_apt_packages,
                get_list(failure_dict, "add_apt_packages"),
            ),
            pip_packages=self._merge_str_lists(
                get_list(deps_dict, "pip_packages"),
                patch.add_pip_packages,
                get_list(failure_dict, "add_pip_packages"),
            ),
            notes=self._merge_str_lists(
                get_list(deps_dict, "notes"),
                patch.notes,
                _as_str_list(failure_dict.get("suggested_actions", [])),
                _as_str_list(failure_dict.get("likely_causes", [])),
            ),
            confidence=max(
                float(getattr(deps, "confidence", 0.0) or 0.0),
                patch.confidence,
                float(failure_dict.get("confidence", 0.0) or 0.0),
            ),
            raw={**(getattr(deps, "raw", {}) or {}), **patch.raw, **failure_dict},
        )

        new_preinstall = list(getattr(plan, "preinstall_commands", []) or [])
        new_build = list(getattr(plan, "build_commands", []) or [])
        new_test = list(getattr(plan, "test_commands", []) or [])
        new_notes = list(getattr(plan, "notes", []) or [])

        def add_unique(target: List[str], items: List[str]) -> None:
            for item in items:
                item = str(item).strip()
                if item and item not in target:
                    target.append(item)

        def remove_matching(target: List[str], patterns: List[str]) -> List[str]:
            if not patterns:
                return target
            out = []
            for cmd in target:
                matched = False
                for pat in patterns:
                    p = str(pat).strip()
                    if not p:
                        continue
                    if cmd == p or p in cmd:
                        matched = True
                        break
                    try:
                        if re.search(p, cmd, re.I):
                            matched = True
                            break
                    except re.error:
                        pass
                if not matched:
                    out.append(cmd)
            return out

        new_base_image = patch.replace_base_image or str(failure_dict.get("change_base_image", "") or "") or getattr(plan, "base_image", "")

        for pkg in patch.add_apt_packages + _as_str_list(failure_dict.get("add_apt_packages", [])):
            cmd = f"apt-get install -y {pkg}"
            if cmd not in new_preinstall:
                new_preinstall.append(cmd)

        for pkg in patch.add_pip_packages + _as_str_list(failure_dict.get("add_pip_packages", [])):
            cmd = f"python3 -m pip install --no-cache-dir {pkg}"
            if cmd not in new_preinstall:
                new_preinstall.append(cmd)

        add_unique(new_preinstall, patch.add_preinstall_commands)
        add_unique(new_build, patch.add_build_commands)
        add_unique(new_build, _as_str_list(failure_dict.get("update_build_commands", [])))
        add_unique(new_test, patch.add_test_commands)

        new_build = remove_matching(new_build, patch.remove_build_commands)
        new_test = remove_matching(new_test, patch.remove_test_commands)

        add_unique(new_notes, patch.notes)
        add_unique(new_notes, _as_str_list(failure_dict.get("suggested_actions", [])))
        add_unique(new_notes, _as_str_list(failure_dict.get("likely_causes", [])))

        new_plan = replace(
            plan,
            base_image=new_base_image,
            preinstall_commands=new_preinstall,
            build_commands=new_build,
            test_commands=new_test,
            notes=new_notes,
            confidence=max(
                float(getattr(plan, "confidence", 0.0) or 0.0),
                patch.confidence,
                float(failure_dict.get("confidence", 0.0) or 0.0),
            ),
            raw={**(getattr(plan, "raw", {}) or {}), **patch.raw, **failure_dict},
        )

        return new_plan, new_deps

    def _merge_str_lists(self, *lists: Any) -> List[str]:
        out: List[str] = []
        seen = set()

        for seq in lists:
            if not seq:
                continue

            if isinstance(seq, (str, bytes)):
                seq = [seq]

            for item in seq:
                s = str(item).strip()
                if not s or s in seen:
                    continue
                seen.add(s)
                out.append(s)

        return out