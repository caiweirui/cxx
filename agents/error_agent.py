from __future__ import annotations

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

def _truncate(text: str, limit: int = 5000) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."

@dataclass
class FailureAnalysis:
    likely_causes: List[str] = field(default_factory=list)
    suggested_actions: List[str] = field(default_factory=list)
    add_apt_packages: List[str] = field(default_factory=list)
    add_pip_packages: List[str] = field(default_factory=list)
    update_build_commands: List[str] = field(default_factory=list)
    change_base_image: str = ""
    confidence: float = 0.0
    raw: Dict[str, Any] = field(default_factory=dict)

class ErrorAgent(BaseAgent):
    """
    错误诊断智能体：
    - 诊断构建失败
    - 使用 RAG 检索历史相似案例
    - 产出结构化修复建议
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

    def analyze(self, build_log: str, dockerfile_text: str, snapshot: Dict[str, Any]) -> FailureAnalysis:
        project_path = str(snapshot.get("project_path", "") or "")
        files_sample = snapshot.get("files_sample", []) or []

        rag_context = ""
        if self.rag_service is not None and build_log:
            try:
                rag_context = self.rag_service.build_error_context(
                    error_text=build_log,
                    project_path=project_path,
                    files_sample=files_sample,
                )
            except Exception:
                rag_context = ""

        prompt = f"""
你是错误诊断智能体，只输出 JSON，不要输出解释文字。

目标：根据构建日志、Dockerfile 和 RAG 检索结果，判断失败原因，并给出修复建议。
要求：
1. 只输出结构化 JSON
2. 不要泛泛而谈，要尽量具体
3. 如果无法确定，保持保守
4. RAG 历史案例可以作为修复参考，但不要盲目照搬

JSON 结构：
{{
  "likely_causes": ["..."],
  "suggested_actions": ["..."],
  "add_apt_packages": ["..."],
  "add_pip_packages": ["..."],
  "update_build_commands": ["..."],
  "change_base_image": "",
  "confidence": 0.0
}}

项目快照：
{snapshot}

Dockerfile：
{_truncate(dockerfile_text, 6000)}

构建日志：
{_truncate(build_log, 8000)}

RAG 增强检索上下文：
{rag_context or "无"}
""".strip()

        default = {
            "likely_causes": [],
            "suggested_actions": [],
            "add_apt_packages": [],
            "add_pip_packages": [],
            "update_build_commands": [],
            "change_base_image": "",
            "confidence": 0.0,
        }

        resp = self.generate_json(prompt, default=default)
        data = resp.data

        try:
            confidence = float(data.get("confidence", 0.0) or 0.0)
        except Exception:
            confidence = 0.0

        raw = dict(data)
        raw["rag_context"] = rag_context
        raw["build_log"] = _truncate(build_log, 12000)
        raw["dockerfile_text"] = _truncate(dockerfile_text, 12000)

        return FailureAnalysis(
            likely_causes=_as_str_list(data.get("likely_causes", [])),
            suggested_actions=_as_str_list(data.get("suggested_actions", [])),
            add_apt_packages=_as_str_list(data.get("add_apt_packages", [])),
            add_pip_packages=_as_str_list(data.get("add_pip_packages", [])),
            update_build_commands=_as_str_list(data.get("update_build_commands", [])),
            change_base_image=str(data.get("change_base_image", "")),
            confidence=confidence,
            raw=raw,
        )