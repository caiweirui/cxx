import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI
from openai import APIError

from cxxcrafter.config import CXXCrafterConfig
from .cache import LLMCache

key_openai = os.getenv("OPENAI_API_KEY")
base_url_openai = os.getenv(
    "OPENAI_BASE_URL",
    os.getenv("POLOAPI_BASE_URL", "https://poloapi.top/v1/chat/completions"),
)
default_provider_mode = os.getenv("LLM_PROVIDER_MODE", "auto").strip().lower() or "auto"
usage_log_default_path = os.getenv("LLM_USAGE_LOG_PATH", "./logs/llm_usage.log")
trace_log_default_path = os.getenv("LLM_TRACE_LOG_PATH", "./logs/llm_trace.jsonl")
claude_api_version = os.getenv("CLAUDE_API_VERSION", "2023-06-01")
claude_max_tokens = int(os.getenv("CLAUDE_MAX_TOKENS", "4096"))
group_header_name_default = os.getenv("LLM_GROUP_HEADER", "X-Group").strip() or "X-Group"

def _normalize_provider_mode(value: Optional[str]) -> str:
    """
    provider_mode 支持：
    - auto
    - openai
    - claude
    - az
    - 以及其它自定义分组名（会原样转小写保留）
    """
    v = (value or "").strip().lower()
    if not v:
        return "auto"
    if v in {"openai", "claude", "auto", "az"}:
        return v
    return v

def _ensure_https(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if not u.startswith(("http://", "https://")):
        u = "https://" + u.lstrip("/")
    return u

def _normalize_base_url(url: Optional[str]) -> str:
    """
    统一把各种 PoloAPI / OpenAI 风格地址归一成“客户端 base_url”。
    目标：
    - https://poloapi.top          -> https://poloapi.top/v1
    - https://poloapi.top/v1       -> https://poloapi.top/v1
    - https://poloapi.top/v1/...   -> https://poloapi.top/v1
    - https://xxx/chat/completions  -> 退回到父级 API 根路径
    """
    raw = _ensure_https(str(url or "").strip())
    if not raw:
        return "https://poloapi.top/v1/chat/completions"

    parsed = urllib.parse.urlparse(raw)
    path = (parsed.path or "").rstrip("/")

    # 如果直接传了 endpoint，就截断到 /v1
    if path.endswith("/chat/completions"):
        path = path[: -len("/chat/completions")]
    elif path.endswith("/messages"):
        path = path[: -len("/messages")]
    elif path.endswith("/completions"):
        path = path[: -len("/completions")]

    # 如果没有 /v1，默认补 /v1
    if not path:
        path = "/v1"
    elif not path.endswith("/v1") and "/v1/" not in path:
        if path.count("/") <= 1:
            path = path + "/v1"

    normalized = urllib.parse.urlunparse(
        (
            parsed.scheme or "https",
            parsed.netloc,
            path,
            "",
            "",
            "",
        )
    )
    return normalized.rstrip("/")

def _strip_system_messages(messages: List[Dict[str, Any]]) -> Tuple[str, List[Dict[str, Any]]]:
    system_parts: List[str] = []
    rest: List[Dict[str, Any]] = []
    for m in messages or []:
        role = str(m.get("role", "") or "").lower()
        content = m.get("content", "")
        if role == "system":
            if content:
                system_parts.append(str(content))
        else:
            rest.append({"role": role or "user", "content": content})
    return "\n".join(system_parts).strip(), rest

class GPTBot:
    def __init__(
        self,
        system_prompt: Optional[str] = None,
        model: str = "gpt-4o",
        config: Optional[CXXCrafterConfig] = None,
    ):
        self.messages: List[Dict[str, Any]] = []
        if system_prompt:
            self.messages.append({"role": "system", "content": system_prompt})

        self.cache = None
        self.model = model
        self.auth_failed = False
        self.endpoint_fallback_used = False
        self.usage_log_path = usage_log_default_path
        self.trace_log_path = trace_log_default_path
        self.provider_mode = default_provider_mode
        self.api_key = None
        self.base_url = _normalize_base_url(base_url_openai)
        self.client = None
        self.group_header_name = group_header_name_default
        self.group_headers: Dict[str, str] = {}

        if config:
            try:
                cred = config.get_agent_credentials("coordinator")
                api_key = cred.get("api_key") or config.global_api_key
                base_url = cred.get("base_url") or config.global_base_url
                self.model = cred.get("model") or model
                self.provider_mode = _normalize_provider_mode(
                    cred.get("provider_mode")
                    or getattr(config, "provider_mode", None)
                    or default_provider_mode
                )
                self.cache = LLMCache(getattr(config, "cache_dir", "./data/cache"))
            except Exception:
                api_key = getattr(config, "global_api_key", None)
                base_url = getattr(config, "global_base_url", base_url_openai)
                self.provider_mode = _normalize_provider_mode(
                    getattr(config, "provider_mode", None) or default_provider_mode
                )
                self.cache = LLMCache(getattr(config, "cache_dir", "./data/cache"))

            for attr in ("llm_usage_log_path", "usage_log_path", "token_usage_log_path"):
                try:
                    v = getattr(config, attr, None)
                    if v:
                        self.usage_log_path = str(v)
                        break
                except Exception:
                    pass

            for attr in ("llm_trace_log_path", "trace_log_path", "agent_trace_log_path"):
                try:
                    v = getattr(config, attr, None)
                    if v:
                        self.trace_log_path = str(v)
                        break
                except Exception:
                    pass
        else:
            api_key = key_openai
            base_url = base_url_openai
            self.provider_mode = default_provider_mode
            self.cache = LLMCache("./data/cache")

        if not api_key:
            raise ValueError("API Key 未设置！请通过 config 或环境变量设置")

        self.api_key = str(api_key).strip()
        self.base_url = _normalize_base_url(base_url)

        # 如果是 az / 其他自定义分组，则附加一个分组头
        if self.provider_mode not in {"auto", "openai", "claude"}:
            self.group_headers = {
                self.group_header_name: self.provider_mode,
            }

        client_kwargs = {
            "api_key": self.api_key,
            "base_url": self.base_url,
            "timeout": 60,
        }
        if self.group_headers:
            client_kwargs["default_headers"] = self.group_headers

        try:
            self.client = OpenAI(**client_kwargs)
        except TypeError:
            # 兼容旧版 openai 包不支持 default_headers 的情况
            client_kwargs.pop("default_headers", None)
            self.client = OpenAI(**client_kwargs)

    @staticmethod
    def _is_auth_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        return (
            "401" in str(exc)
            or "failed_to_auth" in msg
            or "failed to authenticate api key" in msg
            or "unauthorized" in msg
            or "invalid api key" in msg
        )

    @staticmethod
    def _is_unsupported_chat_endpoint(exc: Exception) -> bool:
        msg = str(exc).lower()
        return (
            "does not support endpoint: chat/completions" in msg
            or "invalid_request_body" in msg
            or "unsupported endpoint" in msg
            or "not found" in msg
            or "404" in str(exc)
        )

    def _fallback_model(self) -> str:
        if self.model != "gpt-4o":
            return "gpt-4o"
        return "gpt-4o-mini"

    def _looks_like_claude(self) -> bool:
        low = (self.model or "").lower()
        return "claude" in low

    def _use_claude_native(self) -> bool:
        if self.provider_mode == "claude":
            return True
        if self.provider_mode == "openai":
            return False
        # auto / az / 自定义分组：默认走 OpenAI-compatible
        if self.provider_mode == "auto":
            return self._looks_like_claude()
        return False

    def _extract_usage(self, response: Any) -> Dict[str, Any]:
        """
        兼容 openai response.usage 是对象或 dict 的情况。
        """
        usage = getattr(response, "usage", None)
        if usage is None and isinstance(response, dict):
            usage = response.get("usage")

        if usage is None:
            return {
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
                "cached": False,
            }

        try:
            prompt_tokens = getattr(usage, "prompt_tokens", None)
            completion_tokens = getattr(usage, "completion_tokens", None)
            total_tokens = getattr(usage, "total_tokens", None)
            if prompt_tokens is not None or completion_tokens is not None or total_tokens is not None:
                return {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                    "cached": False,
                }
        except Exception:
            pass

        if isinstance(usage, dict):
            return {
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
                "cached": False,
            }

        try:
            usage_dict = dict(usage)
            return {
                "prompt_tokens": usage_dict.get("prompt_tokens"),
                "completion_tokens": usage_dict.get("completion_tokens"),
                "total_tokens": usage_dict.get("total_tokens"),
                "cached": False,
            }
        except Exception:
            return {
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
                "cached": False,
            }

    def _extract_content(self, response: Any) -> str:
        """
        兼容多种 LLM 返回值格式：
        - str
        - dict
        - OpenAI 标准 response
        - Claude messages / Anthropic 风格 response
        - 带 content/text/output_text 的对象
        """
        if response is None:
            return ""

        if isinstance(response, str):
            return response.strip()

        if isinstance(response, bytes):
            return response.decode("utf-8", errors="ignore").strip()

        if isinstance(response, dict):
            choices = response.get("choices")
            if isinstance(choices, list) and choices:
                c0 = choices[0]
                if isinstance(c0, dict):
                    msg = c0.get("message")
                    if isinstance(msg, dict) and msg.get("content") is not None:
                        return str(msg["content"]).strip()
                    if c0.get("text") is not None:
                        return str(c0["text"]).strip()

            # Claude / Anthropic 风格：content = [{"type":"text","text":"..."}]
            if isinstance(response.get("content"), list):
                texts = []
                for block in response["content"]:
                    if isinstance(block, dict) and block.get("text"):
                        texts.append(str(block["text"]))
                if texts:
                    return "\n".join(texts).strip()

            if response.get("output_text") is not None:
                return str(response["output_text"]).strip()

            if response.get("content") is not None:
                return str(response["content"]).strip()

            return json.dumps(response, ensure_ascii=False)

        # OpenAI-like object
        choices = getattr(response, "choices", None)
        if isinstance(choices, list) and choices:
            c0 = choices[0]
            try:
                msg = getattr(c0, "message", None)
                if msg is not None:
                    content = getattr(msg, "content", None)
                    if content is not None:
                        return str(content).strip()
            except Exception:
                pass

            try:
                text = getattr(c0, "text", None)
                if text is not None:
                    return str(text).strip()
            except Exception:
                pass

        # Claude / Anthropic object style
        try:
            content_blocks = getattr(response, "content", None)
            if isinstance(content_blocks, list):
                texts = []
                for block in content_blocks:
                    if isinstance(block, dict) and block.get("text"):
                        texts.append(str(block["text"]))
                    else:
                        txt = getattr(block, "text", None)
                        if txt:
                            texts.append(str(txt))
                if texts:
                    return "\n".join(texts).strip()
        except Exception:
            pass

        for attr in ("output_text", "content", "text"):
            try:
                val = getattr(response, attr, None)
                if val is not None:
                    return str(val).strip()
            except Exception:
                pass

        for method_name in ("model_dump", "dict", "to_dict"):
            fn = getattr(response, method_name, None)
            if callable(fn):
                try:
                    obj = fn()
                    if isinstance(obj, str):
                        return obj.strip()
                    if isinstance(obj, dict):
                        return self._extract_content(obj)
                    return str(obj).strip()
                except Exception:
                    pass

        return str(response).strip()

    def _append_jsonl(self, path: str, record: Dict[str, Any]) -> None:
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[LLM trace] 写入日志失败: {e}")

    def _append_usage_log(self, record: Dict[str, Any], usage_log_path: Optional[str] = None) -> None:
        try:
            path = Path(usage_log_path or self.usage_log_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[LLM usage] 写入日志失败: {e}")

    def _emit_trace(
        self,
        event: str,
        *,
        trace_id: str = "",
        agent_name: str = "",
        stage: str = "",
        usage_log_path: Optional[str] = None,
        prompt: str = "",
        response: str = "",
        cache_hit: Optional[bool] = None,
        error: Optional[str] = None,
        model: Optional[str] = None,
        usage: Optional[Dict[str, Any]] = None,
    ) -> None:
        record = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "event": event,
            "trace_id": trace_id or "",
            "agent": agent_name or "",
            "stage": stage or "",
            "model": model or self.model,
            "provider_mode": self.provider_mode,
            "cache_hit": cache_hit,
            "prompt_chars": len(prompt or ""),
            "response_chars": len(response or ""),
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "error": error,
        }
        if usage:
            record["prompt_tokens"] = usage.get("prompt_tokens")
            record["completion_tokens"] = usage.get("completion_tokens")
            record["total_tokens"] = usage.get("total_tokens")

        self._append_jsonl(self.trace_log_path, record)

        console = [
            f"[LLM][{event}]",
            f"model={record['model']}",
            f"provider_mode={record['provider_mode']}",
        ]
        if trace_id:
            console.append(f"trace_id={trace_id}")
        if agent_name:
            console.append(f"agent={agent_name}")
        if stage:
            console.append(f"stage={stage}")
        if cache_hit is not None:
            console.append(f"cache_hit={cache_hit}")
        if record["prompt_tokens"] is not None or record["completion_tokens"] is not None or record["total_tokens"] is not None:
            console.append(
                f"prompt_tokens={record['prompt_tokens']} completion_tokens={record['completion_tokens']} total_tokens={record['total_tokens']}"
            )
        if error:
            console.append(f"error={error}")
        print(" | ".join(console))

    def _openai_chat_completion(self, prompt: str) -> Tuple[str, Dict[str, Any]]:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=self.messages,
        )
        content = self._extract_content(response)
        usage = self._extract_usage(response)
        return content, usage

    def _claude_messages_completion(self, prompt: str) -> Tuple[str, Dict[str, Any]]:
        """
        PoloAPI / Claude 原生 messages 接口（尽量兼容 Anthropic 风格）。
        """
        system_prompt, filtered_messages = _strip_system_messages(self.messages)

        payload = {
            "model": self.model,
            "max_tokens": claude_max_tokens,
            "messages": filtered_messages,
        }
        if system_prompt:
            payload["system"] = system_prompt

        url = self.base_url.rstrip("/") + "/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": claude_api_version,
            "Authorization": f"Bearer {self.api_key}",
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
                data = json.loads(raw) if raw else {}
                return self._extract_content(data), self._extract_usage(data)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore") if hasattr(e, "read") else ""
            msg = body or str(e)
            raise RuntimeError(msg) from e
        except urllib.error.URLError as e:
            raise RuntimeError(str(e)) from e

    def inference(
        self,
        message: str = "",
        *,
        trace_id: str = "",
        agent_name: str = "",
        stage: str = "",
        use_cache: bool = True,
        usage_log_path: Optional[str] = None,
    ) -> str:
        if self.auth_failed:
            self._emit_trace(
                "AUTH_BLOCKED",
                trace_id=trace_id,
                agent_name=agent_name,
                stage=stage,
                prompt=message,
                error="auth_failed_cached_state",
            )
            return "LLM调用失败"

        time.sleep(3.5)
        self.messages.append({"role": "user", "content": message})

        cache_key = None
        if self.cache and use_cache:
            system_prompt = self.messages[0]["content"] if self.messages and self.messages[0]["role"] == "system" else ""
            cache_key = self.cache.make_key(self.model, system_prompt, message)
            cached = self.cache.get(cache_key)
            if cached:
                self._emit_trace(
                    "CACHE_HIT",
                    trace_id=trace_id,
                    agent_name=agent_name,
                    stage=stage,
                    prompt=message,
                    response=cached,
                    cache_hit=True,
                    model=self.model,
                )
                self._append_usage_log(
                    {
                        "time": datetime.now().isoformat(timespec="seconds"),
                        "trace_id": trace_id,
                        "agent": agent_name,
                        "stage": stage,
                        "model": self.model,
                        "provider_mode": self.provider_mode,
                        "cached": True,
                        "prompt_tokens": None,
                        "completion_tokens": None,
                        "total_tokens": None,
                        "message": message,
                        "error": None,
                    },
                    usage_log_path=usage_log_path,
                )
                self.messages.append({"role": "assistant", "content": cached})
                return cached

        for retry in range(2):
            try:
                self._emit_trace(
                    "START",
                    trace_id=trace_id,
                    agent_name=agent_name,
                    stage=stage,
                    prompt=message,
                    cache_hit=False,
                    model=self.model,
                )

                if self._use_claude_native():
                    content, usage = self._claude_messages_completion(message)
                else:
                    content, usage = self._openai_chat_completion(message)

                content = (content or "").strip()
                self.messages.append({"role": "assistant", "content": content})

                self._emit_trace(
                    "OK",
                    trace_id=trace_id,
                    agent_name=agent_name,
                    stage=stage,
                    prompt=message,
                    response=content,
                    cache_hit=False,
                    model=self.model,
                    usage=usage,
                )

                self._append_usage_log(
                    {
                        "time": datetime.now().isoformat(timespec="seconds"),
                        "trace_id": trace_id,
                        "agent": agent_name,
                        "stage": stage,
                        "model": self.model,
                        "provider_mode": self.provider_mode,
                        "cached": False,
                        "prompt_tokens": usage.get("prompt_tokens"),
                        "completion_tokens": usage.get("completion_tokens"),
                        "total_tokens": usage.get("total_tokens"),
                        "message": message,
                        "error": None,
                    },
                    usage_log_path=usage_log_path,
                )

                if self.cache and cache_key:
                    self.cache.set(
                        cache_key,
                        content,
                        meta={
                            "model": self.model,
                            "provider_mode": self.provider_mode,
                            "prompt_tokens": usage.get("prompt_tokens"),
                            "completion_tokens": usage.get("completion_tokens"),
                            "total_tokens": usage.get("total_tokens"),
                        },
                    )
                return content

            except APIError as e:
                if self._is_auth_error(e):
                    print(f"API 认证失败：{e}")
                    self.auth_failed = True
                    self._emit_trace(
                        "ERROR",
                        trace_id=trace_id,
                        agent_name=agent_name,
                        stage=stage,
                        prompt=message,
                        error=f"auth_error: {e}",
                    )
                    self._append_usage_log(
                        {
                            "time": datetime.now().isoformat(timespec="seconds"),
                            "trace_id": trace_id,
                            "agent": agent_name,
                            "stage": stage,
                            "model": self.model,
                            "provider_mode": self.provider_mode,
                            "cached": False,
                            "prompt_tokens": None,
                            "completion_tokens": None,
                            "total_tokens": None,
                            "message": message,
                            "error": f"auth_error: {e}",
                        },
                        usage_log_path=usage_log_path,
                    )
                    return "LLM调用失败"

                if self._is_unsupported_chat_endpoint(e) and not self.endpoint_fallback_used:
                    if self.provider_mode == "auto" and self._looks_like_claude():
                        print(f"当前模型 {self.model} 更像 Claude 原生模型，自动切换到 messages 接口")
                        self.provider_mode = "claude"
                        self.endpoint_fallback_used = True
                        continue

                print(f"API请求失败，重试 {retry+1}/2: {str(e)}")
                self._emit_trace(
                    "ERROR",
                    trace_id=trace_id,
                    agent_name=agent_name,
                    stage=stage,
                    prompt=message,
                    error=str(e),
                )
                time.sleep(5)

            except Exception as e:
                if self._is_auth_error(e):
                    print(f"API 认证失败：{e}")
                    self.auth_failed = True
                    self._emit_trace(
                        "ERROR",
                        trace_id=trace_id,
                        agent_name=agent_name,
                        stage=stage,
                        prompt=message,
                        error=f"auth_error: {e}",
                    )
                    self._append_usage_log(
                        {
                            "time": datetime.now().isoformat(timespec="seconds"),
                            "trace_id": trace_id,
                            "agent": agent_name,
                            "stage": stage,
                            "model": self.model,
                            "provider_mode": self.provider_mode,
                            "cached": False,
                            "prompt_tokens": None,
                            "completion_tokens": None,
                            "total_tokens": None,
                            "message": message,
                            "error": f"auth_error: {e}",
                        },
                        usage_log_path=usage_log_path,
                    )
                    return "LLM调用失败"

                if self._is_unsupported_chat_endpoint(e) and not self.endpoint_fallback_used:
                    if self.provider_mode == "auto" and self._looks_like_claude():
                        print(f"当前模型 {self.model} 可能需要 Claude messages 接口，自动切换 provider_mode=claude")
                        self.provider_mode = "claude"
                        self.endpoint_fallback_used = True
                        continue

                print(f"网络/超时错误，重试 {retry+1}/2: {str(e)}")
                self._emit_trace(
                    "ERROR",
                    trace_id=trace_id,
                    agent_name=agent_name,
                    stage=stage,
                    prompt=message,
                    error=str(e),
                )
                time.sleep(5)

        print("请求最终失败，跳过当前项目...")
        self._emit_trace(
            "FINAL_FAILED",
            trace_id=trace_id,
            agent_name=agent_name,
            stage=stage,
            prompt=message,
            error="final_failed",
        )
        self._append_usage_log(
            {
                "time": datetime.now().isoformat(timespec="seconds"),
                "trace_id": trace_id,
                "agent": agent_name,
                "stage": stage,
                "model": self.model,
                "provider_mode": self.provider_mode,
                "cached": False,
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
                "message": message,
                "error": "final_failed",
            },
            usage_log_path=usage_log_path,
        )
        return "LLM调用失败"

    def inference2(
        self,
        context=128000,
        message='',
        **kwargs,
    ):
        return self.inference(message, **kwargs)

    def __call__(self, message: str = "", **kwargs) -> str:
        return self.inference(message, **kwargs)

    def chat(self, message: str = "", **kwargs) -> str:
        return self.inference(message, **kwargs)

class TongyiBot:
    pass

class DeepSeekBot:
    pass

class GLMBot:
    pass