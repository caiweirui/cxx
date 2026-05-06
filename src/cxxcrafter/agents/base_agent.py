# src/cxxcrafter/agents/base_agent.py
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

def _extract_json_text(text: str) -> str:
    """
    尽量从模型输出中提取 JSON。
    支持：
    - ```json ... ```
    - 纯 JSON
    - 文本中夹杂 JSON
    """
    if not text:
        return "{}"

    text = text.strip()

    # fenced code block
    fenced = re.search(r"```json\s*(.*?)\s*```", text, re.S | re.I)
    if fenced:
        return fenced.group(1).strip()

    # first {...} or [...]
    obj_match = re.search(r"(\{.*\})", text, re.S)
    arr_match = re.search(r"(\[.*\])", text, re.S)

    if obj_match and arr_match:
        return obj_match.group(1).strip() if len(obj_match.group(1)) <= len(arr_match.group(1)) else arr_match.group(1).strip()
    if obj_match:
        return obj_match.group(1).strip()
    if arr_match:
        return arr_match.group(1).strip()

    return text

def safe_json_loads(text: str, default: Any = None) -> Any:
    if default is None:
        default = {}
    try:
        return json.loads(_extract_json_text(text))
    except Exception:
        return default

def _normalize_text_result(result: Any) -> str:
    """
    把各种可能的模型返回值统一成字符串。
    支持：
    - str
    - bytes
    - dict / list
    - 带 choices/message/content 的 OpenAI 风格对象
    - 带 model_dump()/dict()/to_dict() 的对象
    - 其他对象
    """
    if result is None:
        return ""

    if isinstance(result, str):
        return result

    if isinstance(result, bytes):
        return result.decode("utf-8", errors="ignore")

    # OpenAI-like dict
    if isinstance(result, dict):
        # choices -> message -> content
        choices = result.get("choices")
        if isinstance(choices, list) and choices:
            c0 = choices[0]
            if isinstance(c0, dict):
                msg = c0.get("message")
                if isinstance(msg, dict) and msg.get("content") is not None:
                    return str(msg["content"])
                if c0.get("text") is not None:
                    return str(c0["text"])
        if result.get("output_text") is not None:
            return str(result["output_text"])
        if result.get("content") is not None and isinstance(result["content"], str):
            return result["content"]
        return json.dumps(result, ensure_ascii=False)

    # common object serializers
    for attr in ("model_dump", "dict", "to_dict"):
        fn = getattr(result, attr, None)
        if callable(fn):
            try:
                obj = fn()
                if isinstance(obj, (dict, list)):
                    return json.dumps(obj, ensure_ascii=False) if not isinstance(obj, str) else obj
                return str(obj)
            except Exception:
                pass

    # common attributes
    for attr in ("content", "text", "output_text", "message"):
        val = getattr(result, attr, None)
        if val is not None:
            if isinstance(val, str):
                return val
            if isinstance(val, dict) and val.get("content") is not None:
                return str(val["content"])
            return str(val)

    # list/tuple fallback
    if isinstance(result, (list, tuple)):
        return json.dumps(result, ensure_ascii=False)

    return str(result)

@dataclass
class AgentResponse:
    raw_text: str
    data: Dict[str, Any]

class BaseAgent:
    """
    所有 Agent 的统一基类：
    - 负责和 LLM 交互
    - 负责把输出强制解析成 JSON
    - 负责输出兜底
    """

    def __init__(
        self,
        bot: Any = None,
        model_name: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: float = 0.2,
    ) -> None:
        self.bot = bot
        self.model_name = model_name
        self.api_key = api_key
        self.base_url = base_url
        self.temperature = temperature

    def _call_llm(self, prompt: str) -> str:
        """
        兼容不同 Bot 实现。
        优先尝试常见方法名，其次尝试直接可调用对象。
        """
        if self.bot is None:
            raise RuntimeError("LLM bot is not configured.")

        candidate_methods = ("chat", "complete", "invoke", "ask", "generate", "call", "run", "predict", "query", "__call__")
        last_exc = None

        # 先尝试显式方法
        for name in candidate_methods:
            method = getattr(self.bot, name, None)
            if not callable(method):
                continue

            try:
                # 先试字符串 prompt
                try:
                    result = method(prompt)
                except TypeError:
                    # 再试 messages 格式
                    messages = [{"role": "user", "content": prompt}]
                    result = method(messages)
                return _normalize_text_result(result)
            except Exception as e:
                last_exc = e

        # 再尝试 bot 本身可调用
        if callable(self.bot):
            try:
                try:
                    return _normalize_text_result(self.bot(prompt))
                except TypeError:
                    messages = [{"role": "user", "content": prompt}]
                    return _normalize_text_result(self.bot(messages))
            except Exception as e:
                last_exc = e

        raise RuntimeError(f"Unable to call LLM bot. Last error: {last_exc!r}")

    def generate_json(self, prompt: str, default: Dict[str, Any]) -> AgentResponse:
        """
        让模型必须输出 JSON；若失败则回退 default。
        """
        try:
            raw = self._call_llm(prompt)
            data = safe_json_loads(raw, default=default)
            if not isinstance(data, dict):
                data = default
            return AgentResponse(raw_text=raw, data=data)
        except Exception:
            return AgentResponse(raw_text="", data=default)