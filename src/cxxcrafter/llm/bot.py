import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from openai import OpenAI
from openai import APIError

from cxxcrafter.config import CXXCrafterConfig
from .cache import LLMCache

key_openai = os.getenv("OPENAI_API_KEY")
base_url_openai = os.getenv("OPENAI_BASE_URL", "https://api.jiekou.ai/openai")
usage_log_default_path = os.getenv("LLM_USAGE_LOG_PATH", "./logs/llm_usage.log")
trace_log_default_path = os.getenv("LLM_TRACE_LOG_PATH", "./logs/llm_trace.jsonl")

class GPTBot:
    def __init__(
        self,
        system_prompt: Optional[str] = None,
        model: str = "gpt-5.4-nano",
        config: Optional[CXXCrafterConfig] = None
    ):
        self.messages = []
        if system_prompt:
            self.messages.append({"role": "system", "content": system_prompt})

        self.cache = None
        self.model = model
        self.auth_failed = False
        self.endpoint_fallback_used = False
        self.usage_log_path = usage_log_default_path
        self.trace_log_path = trace_log_default_path

        if config:
            try:
                cred = config.get_agent_credentials("coordinator")
                api_key = cred["api_key"] or config.global_api_key
                base_url = cred["base_url"] or config.global_base_url
                self.model = cred["model"] or model
                self.cache = LLMCache(getattr(config, "cache_dir", "./data/cache"))
            except Exception:
                api_key = getattr(config, "global_api_key", None)
                base_url = getattr(config, "global_base_url", base_url_openai)
                self.cache = LLMCache(getattr(config, "cache_dir", "./data/cache"))

            # 如果 config 里有日志路径配置，则优先使用
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
            self.cache = LLMCache("./data/cache")

        if not api_key:
            raise ValueError("API Key 未设置！请通过config或环境变量设置")

        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=60
        )

    @staticmethod
    def _is_auth_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        return (
            "401" in str(exc)
            or "failed_to_auth" in msg
            or "failed to authenticate api key" in msg
        )

    @staticmethod
    def _is_unsupported_chat_endpoint(exc: Exception) -> bool:
        msg = str(exc).lower()
        return (
            "does not support endpoint: chat/completions" in msg
            or "invalid_request_body" in msg
            or "unsupported endpoint" in msg
        )

    def _fallback_model(self) -> str:
        if self.model != "gpt-5.4-mini":
            return "gpt-5.4-mini"
        return "gpt-5.4"

    def _extract_usage(self, response: Any) -> Dict[str, Any]:
        """
        兼容 openai response.usage 是对象或 dict 的情况。
        """
        usage = getattr(response, "usage", None)
        if usage is None:
            return {
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
                "cached": False,
            }

        # 对象形式
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

        # dict 形式
        if isinstance(usage, dict):
            return {
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
                "cached": False,
            }

        # 兜底
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

    def _append_jsonl(self, path: str, record: Dict[str, Any]) -> None:
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[LLM trace] 写入日志失败: {e}")

    def _append_usage_log(self, record: Dict[str, Any], usage_log_path: Optional[str] = None) -> None:
        """
        追加写入 JSONL 日志，便于后续统计。
        """
        try:
            path = Path(usage_log_path or self.usage_log_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            # 日志写入失败不影响主流程
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

        # 控制台也输出一份，便于实时观察
        console = [
            f"[LLM][{event}]",
            f"model={record['model']}",
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
                # 缓存命中：没有新的 token 消耗
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

                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=self.messages
                )
                content = response.choices[0].message.content.strip()
                self.messages.append({"role": "assistant", "content": content})

                usage = self._extract_usage(response)

                # 打印并记录 token usage
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
                            "prompt_tokens": usage.get("prompt_tokens"),
                            "completion_tokens": usage.get("completion_tokens"),
                            "total_tokens": usage.get("total_tokens"),
                        }
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
                    fallback_model = self._fallback_model()
                    if fallback_model != self.model:
                        print(f"当前模型 {self.model} 不支持 chat/completions，自动切换到 {fallback_model}")
                        self.model = fallback_model
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
                    fallback_model = self._fallback_model()
                    if fallback_model != self.model:
                        print(f"当前模型 {self.model} 不支持 chat/completions，自动切换到 {fallback_model}")
                        self.model = fallback_model
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

class TongyiBot: pass
class DeepSeekBot: pass
class GLMBot: pass