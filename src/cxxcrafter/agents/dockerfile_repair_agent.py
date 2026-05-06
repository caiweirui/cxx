from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
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
        return [str(x) for x in value if str(x).strip()]
    if isinstance(value, tuple):
        return [str(x) for x in value if str(x).strip()]
    if isinstance(value, set):
        return [str(x) for x in value if str(x).strip()]
    return [str(value)]

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
你是 Dockerfile 修复智能体，只输出 JSON，不要输出解释文字。

目标：基于当前构建计划、失败诊断和 RAG 历史案例，输出最小修复补丁。
约束：
1. 只输出补丁，不要重写完整计划
2. 修复动作必须尽量小
3. 不要添加无关依赖
4. 如果某个 test 命令明显错误，优先移除它，而不是强行修复
5. 如果需要补依赖，尽量补最少必要依赖
6. RAG 历史案例只能作为参考，不能违反失败诊断

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
{snapshot}

当前构建计划：
{current_plan}

失败分析：
{_truncate(json.dumps(failure, ensure_ascii=False, indent=2), 10000)}

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
            replace_base_image=str(data.get("replace_base_image", "")),
            notes=_as_str_list(data.get("notes", [])),
            confidence=confidence,
            raw=data,
        )

        final_patch = self._merge_patches(llm_patch, heuristic)

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

        raw = failure.get("raw", {})
        if isinstance(raw, dict):
            for key in ("build_log", "message", "rag_context"):
                v = raw.get(key)
                if v:
                    parts.append(str(v))

        for key in (
            "likely_causes",
            "suggested_actions",
            "notes",
            "raw",
            "message",
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

        for key in ("likely_causes", "suggested_actions", "notes", "raw", "message"):
            v = failure.get(key)
            if isinstance(v, (list, tuple, set)):
                text_parts.extend([str(x) for x in v])
            elif v is not None:
                text_parts.append(str(v))

        raw = failure.get("raw", {})
        if isinstance(raw, dict):
            for key in ("build_log", "rag_context"):
                v = raw.get(key)
                if v:
                    text_parts.append(str(v))

        text = "\n".join(text_parts).lower()

        patch = RepairPatch()

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

        inferred_packages: List[str] = []
        for pattern, pkg in tool_pkg_map:
            if re.search(pattern, text, re.I):
                inferred_packages.append(pkg)

        patch.add_apt_packages = _merge_unique(patch.add_apt_packages, inferred_packages)

        if "module not found" in text and ("python" in text or "pip" in text):
            patch.add_apt_packages = _merge_unique(
                patch.add_apt_packages,
                ["python3", "python3-pip", "python3-venv"],
            )

        if "no rule to make target 'test'" in text or 'no rule to make target "test"' in text or "target 'test' not found" in text:
            patch.remove_test_commands = _merge_unique(
                patch.remove_test_commands,
                [cmd for cmd in current_plan.test_commands if self._looks_like_test_target(cmd)],
            )
            patch.notes = _merge_unique(
                patch.notes,
                ["Remove invalid `test` target or replace it with `ctest --test-dir build --output-on-failure` if tests exist."],
            )

        if "ctest" in text and ("failed" in text or "error" in text):
            patch.notes = _merge_unique(
                patch.notes,
                ["CTest failed; verify whether tests are configured and whether runtime dependencies are installed."],
            )

        if "undefined reference" in text or "ld:" in text or "linker command failed" in text:
            patch.notes = _merge_unique(
                patch.notes,
                ["Linker failure detected; check missing system libraries or link order."],
            )

        if any(x in text for x in ["502 bad gateway", "failed to fetch", "repository", "inrelease", "not signed", "certificate verification failed"]):
            patch.notes = _merge_unique(
                patch.notes,
                [
                    "APT repository/network failure detected.",
                    "If a mirror is used, prefer HTTP mirror during bootstrap to avoid certificate deadlock.",
                    "Keep apt steps minimal and re-render Dockerfile instead of adding more packages.",
                ],
            )

        if "unknown instruction: &&" in text or "dockerfile parse error" in text:
            patch.notes = _merge_unique(
                patch.notes,
                [
                    "Dockerfile syntax error detected; fix generator line continuation and keep `&&` inside the RUN shell.",
                ],
            )

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

        new_deps = replace(
            deps,
            apt_packages=self._merge_str_lists(deps.apt_packages, patch.add_apt_packages, failure.add_apt_packages),
            pip_packages=self._merge_str_lists(deps.pip_packages, patch.add_pip_packages, failure.add_pip_packages),
            notes=self._merge_str_lists(deps.notes, patch.notes, failure.suggested_actions, failure.likely_causes),
            confidence=max(deps.confidence, patch.confidence, failure.confidence),
            raw={**deps.raw, **patch.raw, **failure.raw},
        )

        new_preinstall = list(plan.preinstall_commands)
        new_build = list(plan.build_commands)
        new_test = list(plan.test_commands)
        new_notes = list(plan.notes)

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

        new_base_image = patch.replace_base_image or failure.change_base_image or plan.base_image

        for pkg in patch.add_apt_packages + failure.add_apt_packages:
            cmd = f"apt-get install -y {pkg}"
            if cmd not in new_preinstall:
                new_preinstall.append(cmd)

        for pkg in patch.add_pip_packages + failure.add_pip_packages:
            cmd = f"python3 -m pip install --no-cache-dir {pkg}"
            if cmd not in new_preinstall:
                new_preinstall.append(cmd)

        add_unique(new_preinstall, patch.add_preinstall_commands)
        add_unique(new_build, patch.add_build_commands)
        add_unique(new_build, failure.update_build_commands)
        add_unique(new_test, patch.add_test_commands)

        new_build = remove_matching(new_build, patch.remove_build_commands)
        new_test = remove_matching(new_test, patch.remove_test_commands)

        add_unique(new_notes, patch.notes)
        add_unique(new_notes, failure.suggested_actions)
        add_unique(new_notes, failure.likely_causes)

        new_plan = replace(
            plan,
            base_image=new_base_image,
            preinstall_commands=new_preinstall,
            build_commands=new_build,
            test_commands=new_test,
            notes=new_notes,
            confidence=max(plan.confidence, patch.confidence, failure.confidence),
            raw={**plan.raw, **patch.raw, **failure.raw},
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
                if not s:
                    continue
                if s in seen:
                    continue
                seen.add(s)
                out.append(s)

        return out