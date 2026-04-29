import os
import time
from openai import OpenAI
from typing import Dict, Any
from cxxcrafter.config import CXXCrafterConfig

class BaseAgent:
    def __init__(self, agent_name: str, config: CXXCrafterConfig):
        self.agent_name = agent_name
        self.config = config
        
        # 🔥 获取该智能体的专属配置
        agent_type = agent_name.lower().replace("agent", "")
        self.agent_cred = config.get_agent_credentials(agent_type)
        self.model = self.agent_cred["model"]
        
        self.messages = []
        
        # 验证API Key
        if not self.agent_cred["api_key"]:
            raise ValueError(f"请先设置 {agent_name} 的API Key！")
        
        # 初始化OpenAI客户端（使用该智能体的专属配置）
        self.client = OpenAI(
            api_key=self.agent_cred["api_key"],
            base_url=self.agent_cred["base_url"],
            timeout=60
        )

    def observe(self, context: Dict[str, Any]):
        self.context = context

    def think(self) -> str:
        raise NotImplementedError

    def act(self) -> Dict[str, Any]:
        raise NotImplementedError

    def update(self, feedback: Dict[str, Any]):
        pass

    def _call_llm(self, prompt: str, system_prompt: str = None) -> str:
        time.sleep(3.5)
        
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        
        for retry in range(2):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages
                )
                return response.choices[0].message.content.strip()
            except Exception as e:
                print(f"[{self.agent_name}] LLM调用失败，重试 {retry+1}/2: {e}")
                time.sleep(5)
        
        return "LLM调用失败"