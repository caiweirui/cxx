import os
import time
from typing import Optional

from openai import OpenAI
from openai import APIError

from cxxcrafter.config import CXXCrafterConfig
from .cache import LLMCache

key_openai = os.getenv("OPENAI_API_KEY")
base_url_openai = os.getenv("OPENAI_BASE_URL", "https://api.jiekou.ai/openai")

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

    def inference(self, message: str = "") -> str:
        if self.auth_failed:
            return "LLM调用失败"

        time.sleep(3.5)
        self.messages.append({"role": "user", "content": message})

        cache_key = None
        if self.cache:
            system_prompt = self.messages[0]["content"] if self.messages and self.messages[0]["role"] == "system" else ""
            cache_key = self.cache.make_key(self.model, system_prompt, message)
            cached = self.cache.get(cache_key)
            if cached:
                return cached

        for retry in range(2):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=self.messages
                )
                content = response.choices[0].message.content.strip()
                self.messages.append({"role": "assistant", "content": content})
                if self.cache and cache_key:
                    self.cache.set(cache_key, content, meta={"model": self.model})
                return content

            except APIError as e:
                if self._is_auth_error(e):
                    print(f"API 认证失败：{e}")
                    self.auth_failed = True
                    return "LLM调用失败"

                if self._is_unsupported_chat_endpoint(e) and not self.endpoint_fallback_used:
                    fallback_model = self._fallback_model()
                    if fallback_model != self.model:
                        print(f"当前模型 {self.model} 不支持 chat/completions，自动切换到 {fallback_model}")
                        self.model = fallback_model
                        self.endpoint_fallback_used = True
                        continue

                print(f"API请求失败，重试 {retry+1}/2: {str(e)}")
                time.sleep(5)

            except Exception as e:
                if self._is_auth_error(e):
                    print(f"API 认证失败：{e}")
                    self.auth_failed = True
                    return "LLM调用失败"

                if self._is_unsupported_chat_endpoint(e) and not self.endpoint_fallback_used:
                    fallback_model = self._fallback_model()
                    if fallback_model != self.model:
                        print(f"当前模型 {self.model} 不支持 chat/completions，自动切换到 {fallback_model}")
                        self.model = fallback_model
                        self.endpoint_fallback_used = True
                        continue

                print(f"网络/超时错误，重试 {retry+1}/2: {str(e)}")
                time.sleep(5)

        print("请求最终失败，跳过当前项目...")
        return "LLM调用失败"

    def inference2(self, context=128000, message=''):
        return self.inference(message)

class TongyiBot: pass
class DeepSeekBot: pass
class GLMBot: pass