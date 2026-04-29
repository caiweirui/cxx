import openai
import os
import time
from openai import APIError
from typing import Optional
from cxxcrafter.config import CXXCrafterConfig

# 旧的环境变量加载（保留向后兼容）
key_openai = os.getenv("OPENAI_API_KEY")
base_url_openai = os.getenv("OPENAI_BASE_URL", "https://api.jiekou.ai/openai")

class GPTBot:
    def __init__(
        self, 
        system_prompt=None, 
        model='gpt-5.4-nano',
        config: Optional[CXXCrafterConfig] = None
    ):
        self.messages = [{"role": "system", "content": system_prompt}]
        
        # ===================== 修复点：兼容新旧配置 =====================
        if config:
            # 新配置结构：优先使用coordinator智能体的配置
            try:
                cred = config.get_agent_credentials("coordinator")
                api_key = cred["api_key"]
                base_url = cred["base_url"]
                model = cred["model"]
            except:
                # 兜底：尝试访问旧属性或全局属性
                api_key = getattr(config, "api_key", None) or getattr(config, "global_api_key", None)
                base_url = getattr(config, "base_url", None) or getattr(config, "global_base_url", base_url_openai)
        else:
            # 旧方式：从环境变量加载
            api_key = key_openai
            base_url = base_url_openai
        # ===================================================================
        
        # 验证API Key
        if not api_key:
            raise ValueError("API Key 未设置！请通过config或环境变量设置")
        
        # 初始化OpenAI客户端
        self.client = openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=60
        )
        self.model = model

    def inference(self, message=''):
        # 限流等待（20次/分钟）
        time.sleep(3.5)
        self.messages.append({"role": "user", "content": message})
        
        # 自动重试2次，兼容网络波动
        for retry in range(2):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=self.messages
                )
                content = response.choices[0].message.content.strip()
                self.messages.append({"role": "assistant", "content": content})
                return content
            except APIError as e:
                print(f"API请求失败，重试 {retry+1}/2: {str(e)}")
                time.sleep(5)
            except Exception as e:
                print(f"网络/超时错误，重试 {retry+1}/2: {str(e)}")
                time.sleep(5)
        
        print("请求最终失败，跳过当前项目...")
        return "无法获取构建信息"

    def inference2(self, context=128000, message=''):
        return self.inference(message)

# 空类占位，保证代码兼容
class TongyiBot: pass
class DeepSeekBot: pass
class GLMBot: pass