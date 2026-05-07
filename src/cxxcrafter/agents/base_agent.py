# src/cxxcrafter/agents/base_agent.py
from __future__ import annotations

import inspect
import json
import os
import re
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

def _extract_json_text(text: str) -> str:
    """
    尽量从模型输出中提取 JSON。
    支持：
    - ```json ... ```
    - ``` ... ```
    - 纯 JSON
    - 文本中夹杂 JSON
    """
    if not text:
        return "{}"

    text = text.strip()

    # fenced code block: ```json ... ```
    fenced = re.search(r"```json\s*(.*?)\s*```", text, re.S | re.I)
    if fenced:
        return fenced.group(1).strip()

    # generic fenced code block: ``` ... ```
    fenced_any = re.search(r"```\s*(.*?)\s*```", text, re.S | re.I)
    if fenced_any:
        candidate = fenced_any.group(1).strip()
        if candidate.startswith("{") or candidate.startswith("["):
            return candidate

    # first {...} or [...]
    obj_match = re.search(r"(\{.*\})", text, re.S)
    arr_match = re.search(r"(\[.*\])", text, re.S)

    if obj_match and arr_match:
        return (
            obj_match.group(1).strip()
            if len(obj_match.group(1)) <= len(arr_match.group(1))
            else arr_match.group(1).strip()
        )
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
                if isinstance(obj, str):
                    return obj
                if isinstance(obj, (dict, list)):
                    return json.dumps(obj, ensure_ascii=False)
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

def _shorten(text: Any, limit: int = 240) -> str:
    s = "" if text is None else str(text)
    s = s.replace("\n", " ").replace("\r", " ").strip()
    if len(s) <= limit:
        return s
    return s[: limit - 3] + "..."

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
    - 负责记录 agent / LLM 调用轨迹
    """

    def __init__(
        self,
        bot: Any = None,
        model_name: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: float = 0.2,
        trace_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        strict_llm: Optional[bool] = None,
        trace_log_path: Optional[str] = None,
        usage_log_path: Optional[str] = None,
    ) -> None:
        self.bot = bot
        self.model_name = model_name
        self.api_key = api_key
        self.base_url = base_url
        self.temperature = temperature

        self.agent_name = agent_name or self.__class__.__name__
        self.trace_id = trace_id or os.getenv("CXXCRAFT_RUN_ID") or datetime.now().strftime("run-%Y%m%d-%H%M%S")

        if strict_llm is None:
            env_strict = os.getenv("CXXCRAFT_STRICT_LLM", os.getenv("LLM_STRICT", "0"))
            strict_llm = str(env_strict).strip().lower() in {"1", "true", "yes", "on"}
        self.strict_llm = bool(strict_llm)

        self.trace_log_path = trace_log_path or os.getenv("AGENT_TRACE_LOG_PATH", "./logs/agent_trace.jsonl")
        self.usage_log_path = usage_log_path or os.getenv("LLM_USAGE_LOG_PATH", "./logs/llm_usage.log")

        # 运行时上下文：coordinator / agent 之间可以临时注入
        self.runtime_context: Dict[str, Any] = {}

    def set_runtime_context(self, **kwargs: Any) -> None:
        """
        注入运行时上下文，例如：
        - run_id
        - project_name
        - project_path
        - stage
        - use_cache
        - usage_log_path
        - trace_log_path
        """
        if not kwargs:
            return

        self.runtime_context.update(kwargs)

        if kwargs.get("trace_id"):
            self.trace_id = str(kwargs["trace_id"])
        if kwargs.get("agent_name"):
            self.agent_name = str(kwargs["agent_name"])
        if kwargs.get("trace_log_path"):
            self.trace_log_path = str(kwargs["trace_log_path"])
        if kwargs.get("usage_log_path"):
            self.usage_log_path = str(kwargs["usage_log_path"])

    def _append_jsonl(self, path: str, record: Dict[str, Any]) -> None:
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            # 日志失败不影响主流程
            pass

    def _emit_trace(self, event: str, **payload: Any) -> None:
        record = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "run_id": self.trace_id,
            "agent": self.agent_name,
            "event": event,
            **payload,
        }
        self._append_jsonl(self.trace_log_path, record)

        # 控制台尽量简洁
        stage = payload.get("stage", "")
        msg = payload.get("message", "")
        extra = []
        if stage:
            extra.append(f"stage={stage}")
        if msg:
            extra.append(f"msg={_shorten(msg, 120)}")
        if payload.get("raw_chars") is not None:
            extra.append(f"raw_chars={payload.get('raw_chars')}")
        if payload.get("prompt_chars") is not None:
            extra.append(f"prompt_chars={payload.get('prompt_chars')}")
        if payload.get("cache_hit") is not None:
            extra.append(f"cache_hit={payload.get('cache_hit')}")
        if payload.get("success") is not None:
            extra.append(f"success={payload.get('success')}")
        if payload.get("error"):
            extra.append(f"error={_shorten(payload.get('error'), 120)}")

        print(f"[AGENT][{self.agent_name}][{event}] run_id={self.trace_id}" + (f" | {' | '.join(extra)}" if extra else ""))

    def _build_call_meta(self) -> Dict[str, Any]:
        meta = {
            "trace_id": self.runtime_context.get("trace_id", self.trace_id),
            "agent_name": self.runtime_context.get("agent_name", self.agent_name),
            "stage": self.runtime_context.get("stage", ""),
            "use_cache": self.runtime_context.get("use_cache", True),
            "usage_log_path": self.runtime_context.get("usage_log_path", self.usage_log_path),
            "trace_log_path": self.runtime_context.get("trace_log_path", self.trace_log_path),
        }
        # 去掉 None / 空字符串
        return {k: v for k, v in meta.items() if v is not None and str(v) != ""}

    def _call_llm(self, prompt: str) -> str:
        """
        兼容不同 Bot 实现。
        优先尝试常见方法名，其次尝试直接可调用对象。
        """
        if self.bot is None:
            raise RuntimeError("LLM bot is not configured.")

        candidate_methods = (
            "inference",
            "inference2",
            "chat",
            "complete",
            "invoke",
            "ask",
            "generate",
            "call",
            "run",
            "predict",
            "query",
            "__call__",
        )

        meta_kwargs = self._build_call_meta()
        last_exc: Optional[Exception] = None

        self._emit_trace(
            "llm_call_start",
            stage=meta_kwargs.get("stage", ""),
            prompt_chars=len(prompt or ""),
            prompt_preview=_shorten(prompt, 180),
            model_name=self.model_name,
        )

        # 先尝试显式方法
        for name in candidate_methods:
            method = getattr(self.bot, name, None)
            if not callable(method):
                continue

            try:
                # 尝试携带上下文参数
                try:
                    result = method(prompt, **meta_kwargs)
                except TypeError:
                    # 再试普通调用
                    result = method(prompt)

                text = _normalize_text_result(result)
                self._emit_trace(
                    "llm_call_ok",
                    stage=meta_kwargs.get("stage", ""),
                    method=name,
                    raw_chars=len(text or ""),
                    response_preview=_shorten(text, 180),
                )
                return text
            except Exception as e:
                last_exc = e
                continue

        # 再尝试 bot 本身可调用
        if callable(self.bot):
            try:
                try:
                    text = _normalize_text_result(self.bot(prompt, **meta_kwargs))
                except TypeError:
                    text = _normalize_text_result(self.bot(prompt))

                self._emit_trace(
                    "llm_call_ok",
                    stage=meta_kwargs.get("stage", ""),
                    method="__call__",
                    raw_chars=len(text or ""),
                    response_preview=_shorten(text, 180),
                )
                return text
            except Exception as e:
                last_exc = e

        self._emit_trace(
            "llm_call_failed",
            stage=meta_kwargs.get("stage", ""),
            error=repr(last_exc) if last_exc is not None else "unknown_error",
        )
        raise RuntimeError(f"Unable to call LLM bot. Last error: {last_exc!r}")

    def generate_json(self, prompt: str, default: Dict[str, Any]) -> AgentResponse:
        """
        让模型必须输出 JSON；若失败则回退 default。
        同时记录完整 trace，方便确认是否真的调用了模型。
        """
        caller_stage = "generate_json"
        try:
            # 调用方通常是 analyze / plan / suggest_patch
            frame = inspect.currentframe()
            if frame and frame.f_back:
                caller_stage = frame.f_back.f_code.co_name
        except Exception:
            pass

        old_stage = self.runtime_context.get("stage")
        self.runtime_context["stage"] = caller_stage

        self._emit_trace(
            "agent_generate_json_start",
            stage=caller_stage,
            prompt_chars=len(prompt or ""),
            prompt_preview=_shorten(prompt, 260),
        )

        try:
            raw = self._call_llm(prompt)
            data = safe_json_loads(raw, default=default)

            if not isinstance(data, dict):
                self._emit_trace(
                    "agent_generate_json_parse_fallback",
                    stage=caller_stage,
                    raw_chars=len(raw or ""),
                    reason="parsed_output_is_not_dict",
                )
                data = default
            else:
                self._emit_trace(
                    "agent_generate_json_ok",
                    stage=caller_stage,
                    raw_chars=len(raw or ""),
                    parsed_keys=list(data.keys())[:30],
                    parsed_ok=True,
                )

            return AgentResponse(raw_text=raw, data=data)

        except Exception as exc:
            self._emit_trace(
                "agent_generate_json_error",
                stage=caller_stage,
                error=repr(exc),
                traceback=_shorten(traceback.format_exc(limit=5), 1200),
            )
            if self.strict_llm:
                raise
            return AgentResponse(raw_text="", data=default)

        finally:
            if old_stage is None:
                self.runtime_context.pop("stage", None)
            else:
                self.runtime_context["stage"] = old_stage