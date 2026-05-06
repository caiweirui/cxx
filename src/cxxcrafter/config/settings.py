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
    "coordinator": "gpt-5.4-mini",
    "dockerfile_repair": "gpt-5.4-pro",
}

class AgentConfig:
    """单个智能体的配置"""

    def __init__(self, model: str = "gpt-5.4-nano"):
        self.model: str = model
        self.api_key: Optional[str] = None
        self.base_url: Optional[str] = None

    def is_independent(self) -> bool:
        """是否使用独立配置"""
        return self.api_key is not None and len(self.api_key) > 0

class CXXCrafterConfig:
    """
    CXXCrafter 全局配置

    新增内容：
    - enable_build_cache: 是否启用“Dockerfile 不变则复用上次成功结果”
    - skip_rebuild_if_unchanged: 是否在 Dockerfile 未变化时跳过重复构建
    - use_buildkit: 是否启用 BuildKit
    - buildkit_progress_plain: 是否使用 plain 输出，便于日志查看
    - dockerfile_repair_model: Dockerfile 修复智能体模型
    - dockerfile_repair_api_key: Dockerfile 修复智能体独立 API Key
    - dockerfile_repair_base_url: Dockerfile 修复智能体独立 Base URL
    """

    def __init__(self):
        # ===== 兼容旧代码的别名字段 =====
        self.api_key: Optional[str] = None
        self.base_url: str = "https://api.jiekou.ai/openai"

        # ===== 全局配置（兜底） =====
        self.global_api_key: Optional[str] = None
        self.global_base_url: str = "https://api.jiekou.ai/openai"

        # ===== 构建/验证相关开关 =====
        self.enable_build_cache: bool = True
        self.skip_rebuild_if_unchanged: bool = True
        self.use_buildkit: bool = True
        self.buildkit_progress_plain: bool = True

        # ===== 运行行为 =====
        self.max_rounds: int = 2
        self.enable_iterative_repair: bool = True
        self.compatibility_mode: bool = True

        # ===== 缓存/日志目录 =====
        self.cache_dir: str = "./data/cache"
        self.logs_dir: str = "./data/build_logs"
        self.output_root: str = "./dockerfile_playground"

        # ===== 默认基础镜像 =====
        self.base_image: str = "ubuntu:22.04"

        # ===== 每个智能体的独立配置 =====
        self.agent_configs: Dict[str, AgentConfig] = {
            "dependency": AgentConfig(RECOMMENDED_MODELS["dependency"]),
            "build": AgentConfig(RECOMMENDED_MODELS["build"]),
            "error": AgentConfig(RECOMMENDED_MODELS["error"]),
            "coordinator": AgentConfig(RECOMMENDED_MODELS["coordinator"]),
            "dockerfile_repair": AgentConfig(RECOMMENDED_MODELS["dockerfile_repair"]),
        }

        # ===== Dockerfile 修复智能体的直观字段 =====
        self.dockerfile_repair_model: str = RECOMMENDED_MODELS["dockerfile_repair"]
        self.dockerfile_repair_api_key: Optional[str] = None
        self.dockerfile_repair_base_url: Optional[str] = None

        # 尽量从环境变量载入
        self.load_from_env()

        # 同步一次，避免环境变量只写了全局但 repair 侧读取不到
        self._sync_dockerfile_repair_aliases()

    # ------------------------------------------------------------------
    # 内部同步方法
    # ------------------------------------------------------------------
    def _sync_dockerfile_repair_aliases(self):
        """把 dockerfile_repair 的直观字段与 agent_configs 同步。"""
        if "dockerfile_repair" not in self.agent_configs:
            self.agent_configs["dockerfile_repair"] = AgentConfig(
                RECOMMENDED_MODELS["dockerfile_repair"]
            )

        self.agent_configs["dockerfile_repair"].model = self.dockerfile_repair_model
        self.agent_configs["dockerfile_repair"].api_key = self.dockerfile_repair_api_key
        self.agent_configs["dockerfile_repair"].base_url = self.dockerfile_repair_base_url

    # ------------------------------------------------------------------
    # 兼容旧接口
    # ------------------------------------------------------------------
    def set_api_key(self, api_key: str):
        """兼容旧接口：设置全局 API Key"""
        self.set_global_api_key(api_key)

    def set_base_url(self, base_url: str):
        """兼容旧接口：设置全局 Base URL"""
        self.set_global_base_url(base_url)

    def set_agent_model(self, agent_type: str, model: str):
        """兼容旧接口：设置某个智能体模型"""
        ok = self.set_agent_config(agent_type=agent_type, model=model)
        if not ok:
            raise ValueError(f"设置 {agent_type} 模型失败: {model}")

    def set_dockerfile_repair_model(self, model: str):
        """专用接口：设置 Dockerfile 修复智能体模型"""
        if not self._is_model_supported(model):
            raise ValueError(f"模型 '{model}' 不在支持列表中")
        self.dockerfile_repair_model = model
        self._sync_dockerfile_repair_aliases()
        print(f"✅ Dockerfile 修复模型设置成功: {model}")

    def set_dockerfile_repair_config(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None
    ) -> bool:
        """专用接口：设置 Dockerfile 修复智能体配置"""
        if model is not None and model != "":
            if not self._is_model_supported(model):
                print(f"❌ Dockerfile 修复模型 '{model}' 不在支持列表中")
                return False
            self.dockerfile_repair_model = model

        if api_key is not None:
            if api_key and len(api_key) < 10:
                print("❌ Dockerfile 修复智能体的 API Key 无效")
                return False
            self.dockerfile_repair_api_key = api_key if api_key else None

        if base_url is not None:
            self.dockerfile_repair_base_url = base_url if base_url else None

        self._sync_dockerfile_repair_aliases()
        print("✅ Dockerfile 修复智能体配置已更新")
        return True

    # ------------------------------------------------------------------
    # 全局配置
    # ------------------------------------------------------------------
    def set_global_api_key(self, api_key: str):
        """设置全局API Key"""
        if not api_key or len(api_key) < 10:
            raise ValueError("API Key 无效，请输入有效的API Key")
        self.global_api_key = api_key
        self.api_key = api_key
        print("✅ 全局API Key 设置成功")

    def set_global_base_url(self, base_url: str):
        """设置全局Base URL"""
        if not base_url:
            raise ValueError("Base URL 不能为空")
        self.global_base_url = base_url
        self.base_url = base_url
        print(f"✅ 全局API Base URL 设置为: {base_url}")

    # ------------------------------------------------------------------
    # 智能体配置
    # ------------------------------------------------------------------
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

        if agent_type == "dockerfile_repair":
            self.dockerfile_repair_model = agent_cfg.model
            self.dockerfile_repair_api_key = agent_cfg.api_key
            self.dockerfile_repair_base_url = agent_cfg.base_url

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

    def get_dockerfile_repair_credentials(self) -> Dict[str, Any]:
        """获取 Dockerfile 修复智能体的凭证"""
        return self.get_agent_credentials("dockerfile_repair")

    def reset_to_recommended(self):
        """重置为推荐配置"""
        self.agent_configs = {
            "dependency": AgentConfig(RECOMMENDED_MODELS["dependency"]),
            "build": AgentConfig(RECOMMENDED_MODELS["build"]),
            "error": AgentConfig(RECOMMENDED_MODELS["error"]),
            "coordinator": AgentConfig(RECOMMENDED_MODELS["coordinator"]),
            "dockerfile_repair": AgentConfig(RECOMMENDED_MODELS["dockerfile_repair"]),
        }

        self.dockerfile_repair_model = RECOMMENDED_MODELS["dockerfile_repair"]
        self.dockerfile_repair_api_key = None
        self.dockerfile_repair_base_url = None

        print("✅ 已重置为推荐配置")

    def _is_model_supported(self, model: str) -> bool:
        for _, models in SUPPORTED_MODELS.items():
            if model in models:
                return True
        return False

    # ------------------------------------------------------------------
    # 读取环境变量
    # ------------------------------------------------------------------
    def load_from_env(self):
        api_key = os.getenv("OPENAI_API_KEY")
        base_url = os.getenv("OPENAI_BASE_URL")

        if api_key:
            self.global_api_key = api_key
            self.api_key = api_key
        if base_url:
            self.global_base_url = base_url
            self.base_url = base_url

        # Dockerfile 修复智能体专用环境变量（可选）
        repair_api_key = os.getenv("DOCKERFILE_REPAIR_API_KEY")
        repair_base_url = os.getenv("DOCKERFILE_REPAIR_BASE_URL")
        repair_model = os.getenv("DOCKERFILE_REPAIR_MODEL")

        if repair_api_key:
            self.dockerfile_repair_api_key = repair_api_key
        if repair_base_url:
            self.dockerfile_repair_base_url = repair_base_url
        if repair_model and self._is_model_supported(repair_model):
            self.dockerfile_repair_model = repair_model

    # ------------------------------------------------------------------
    # 配置摘要
    # ------------------------------------------------------------------
    def get_config_summary(self) -> str:
        summary = "\n" + "=" * 60 + "\n"
        summary += "CXXCrafter 配置摘要\n"
        summary += "=" * 60 + "\n"
        summary += f"全局API Key: {'已设置' if self.global_api_key else '未设置'}\n"
        summary += f"全局Base URL: {self.global_base_url}\n"
        summary += f"最大修复轮次: {self.max_rounds}\n"
        summary += f"启用迭代修复: {'是' if self.enable_iterative_repair else '否'}\n"
        summary += f"启用构建缓存: {'是' if self.enable_build_cache else '否'}\n"
        summary += f"Dockerfile未变化时跳过重建: {'是' if self.skip_rebuild_if_unchanged else '否'}\n"
        summary += f"启用BuildKit: {'是' if self.use_buildkit else '否'}\n"
        summary += f"BuildKit输出模式: {'plain' if self.buildkit_progress_plain else 'default'}\n"
        summary += f"缓存目录: {self.cache_dir}\n"
        summary += f"默认基础镜像: {self.base_image}\n"

        summary += "\n智能体配置:\n"
        for agent, cfg in self.agent_configs.items():
            cred = self.get_agent_credentials(agent)
            indep = " (独立配置)" if cred["independent"] else " (全局配置)"
            summary += f"  {agent}:\n"
            summary += f"    模型: {cfg.model}{indep}\n"
            summary += f"    API Key: {'已设置' if cred['api_key'] else '未设置'}\n"
            summary += f"    Base URL: {cred['base_url']}\n"

        summary += "\nDockerfile 修复专用字段:\n"
        summary += f"  dockerfile_repair_model: {self.dockerfile_repair_model}\n"
        summary += f"  dockerfile_repair_api_key: {'已设置' if self.dockerfile_repair_api_key else '未设置'}\n"
        summary += f"  dockerfile_repair_base_url: {self.dockerfile_repair_base_url or self.global_base_url}\n"

        summary += "=" * 60 + "\n"
        return summary