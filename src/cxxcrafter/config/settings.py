import os
from typing import Dict, Optional, Any

# 支持的模型列表
SUPPORTED_MODELS = {
    "openai": [
        "gpt-5.4-nano", "gpt-5.4-mini", "gpt-5.4-pro", "gpt-5.4",
        "gpt-5.3-chat-latest",
        "gpt-5.2", "gpt-5.2-pro", "gpt-5.2-chat-latest",
        "gpt-5.1-chat-latest", "gpt-5.1", "gpt-5-pro",
        "gpt-5-chat-latest", "gpt-5-nano", "gpt-5-mini", "gpt-5",
        "gpt-4.1-mini", "gpt-4.1-nano", "gpt-4.1",
        "o3", "o3-mini", "o1-mini", "o1",
        "gpt-4o-mini", "gpt-4o"
    ],
    "anthropic": [
        "claude-opus-4-7", "claude-sonnet-4-6", "claude-opus-4-6",
        "claude-opus-4-5-20251101", "claude-haiku-4-5-20251001",
        "claude-sonnet-4-5-20250929", "claude-opus-4-20250514",
        "claude-3-7-sonnet-20250219", "claude-3-5-sonnet-20241022",
        "claude-sonnet-4-20250514", "claude-3-haiku-20240307",
        "claude-3-5-haiku-20241022", "claude-opus-4-1-20250805"
    ],
    "google": [
        "gemini-3.1-flash-lite-preview", "gemini-3.1-pro-preview",
        "gemini-3-flash-preview", "gemini-3-pro-preview",
        "gemini-2.5-flash-lite-preview-09-2025", "gemini-2.0-flash-lite",
        "gemini-2.5-flash-lite", "gemini-2.5-pro", "gemini-2.5-flash",
        "gemini-2.5-flash-lite-preview-06-17", "gemini-2.5-flash-preview-05-20",
        "gemini-2.5-pro-preview-06-05", "gemini-2.0-flash-20250609"
    ]
}

# 推荐的默认模型组合
RECOMMENDED_MODELS = {
    "dependency": "gpt-5.4-mini",
    "build": "gpt-5.4-nano",
    "error": "gpt-5.4-pro",
    "coordinator": "gpt-5.4-mini"
}

class AgentConfig:
    """单个智能体的配置"""
    def __init__(self, model: str = "gpt-5.4-nano"):
        self.model: str = model
        self.api_key: Optional[str] = None  # 独立API Key（可选）
        self.base_url: Optional[str] = None  # 独立Base URL（可选）

    def is_independent(self) -> bool:
        """是否使用独立配置"""
        return self.api_key is not None and len(self.api_key) > 0

class CXXCrafterConfig:
    def __init__(self):
        # 全局配置（兜底）
        self.global_api_key: Optional[str] = None
        self.global_base_url: str = "https://api.jiekou.ai/openai"
        
        # 每个智能体的独立配置
        self.agent_configs: Dict[str, AgentConfig] = {
            "dependency": AgentConfig(RECOMMENDED_MODELS["dependency"]),
            "build": AgentConfig(RECOMMENDED_MODELS["build"]),
            "error": AgentConfig(RECOMMENDED_MODELS["error"]),
            "coordinator": AgentConfig(RECOMMENDED_MODELS["coordinator"])
        }

    def set_global_api_key(self, api_key: str):
        """设置全局API Key"""
        if not api_key or len(api_key) < 10:
            raise ValueError("API Key 无效，请输入有效的API Key")
        self.global_api_key = api_key
        print("✅ 全局API Key 设置成功")

    def set_global_base_url(self, base_url: str):
        """设置全局Base URL"""
        self.global_base_url = base_url
        print(f"✅ 全局API Base URL 设置为: {base_url}")

    def set_agent_config(
        self, 
        agent_type: str, 
        model: str = None,
        api_key: str = None,
        base_url: str = None
    ) -> bool:
        """设置智能体配置"""
        if agent_type not in self.agent_configs:
            print(f"❌ 无效的智能体类型: {agent_type}")
            print(f"   支持的类型: {list(self.agent_configs.keys())}")
            return False

        agent_cfg = self.agent_configs[agent_type]
        
        # 更新模型
        if model:
            if not self._is_model_supported(model):
                print(f"❌ 模型 '{model}' 不在支持列表中")
                return False
            agent_cfg.model = model
        
        # 更新独立API Key
        if api_key is not None:
            if api_key and len(api_key) < 10:
                print(f"❌ {agent_type} 智能体的API Key无效")
                return False
            agent_cfg.api_key = api_key if api_key else None
        
        # 更新独立Base URL
        if base_url is not None:
            agent_cfg.base_url = base_url if base_url else None

        print(f"✅ {agent_type} 智能体配置已更新")
        return True

    def get_agent_credentials(self, agent_type: str) -> Dict[str, Any]:
        """获取智能体的实际凭证（优先使用独立配置，否则使用全局）"""
        agent_cfg = self.agent_configs.get(agent_type)
        if not agent_cfg:
            raise ValueError(f"未知的智能体类型: {agent_type}")

        if agent_cfg.is_independent():
            return {
                "api_key": agent_cfg.api_key,
                "base_url": agent_cfg.base_url or self.global_base_url,
                "model": agent_cfg.model,
                "independent": True
            }
        else:
            return {
                "api_key": self.global_api_key,
                "base_url": self.global_base_url,
                "model": agent_cfg.model,
                "independent": False
            }

    def reset_to_recommended(self):
        """重置为推荐配置"""
        for agent_type in self.agent_configs:
            self.agent_configs[agent_type] = AgentConfig(RECOMMENDED_MODELS[agent_type])
        print("✅ 已重置为推荐配置")

    def _is_model_supported(self, model: str) -> bool:
        for provider, models in SUPPORTED_MODELS.items():
            if model in models:
                return True
        return False

    def get_config_summary(self) -> str:
        summary = "\n" + "="*60 + "\n"
        summary += "CXXCrafter 配置摘要\n"
        summary += "="*60 + "\n"
        summary += f"全局API Key: {'已设置' if self.global_api_key else '未设置'}\n"
        summary += f"全局Base URL: {self.global_base_url}\n"
        summary += "\n智能体配置:\n"
        for agent, cfg in self.agent_configs.items():
            cred = self.get_agent_credentials(agent)
            indep = " (独立配置)" if cred["independent"] else " (全局配置)"
            summary += f"  {agent}:\n"
            summary += f"    模型: {cfg.model}{indep}\n"
            summary += f"    API Key: {'已设置' if cred['api_key'] else '未设置'}\n"
        summary += "="*60 + "\n"
        return summary

    def load_from_env(self):
        api_key = os.getenv("OPENAI_API_KEY")
        base_url = os.getenv("OPENAI_BASE_URL")
        if api_key:
            self.global_api_key = api_key
        if base_url:
            self.global_base_url = base_url