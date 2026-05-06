# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

# =========================================================
# 模型支持
# =========================================================

SUPPORTED_MODELS: Dict[str, list[str]] = {
    "openai": [
        "gpt-4o-mini",
        "gpt-4.1-mini",
        "gpt-4.1",
    ],
    "anthropic": [
        "claude-3-5-sonnet-latest",
        "claude-3-5-haiku-latest",
    ],
    "google": [
        "gemini-2.0-flash",
        "gemini-2.0-pro",
    ],
    "deepseek": [
        "deepseek-chat",
        "deepseek-reasoner",
    ],
    "qwen": [
        "qwen-plus",
        "qwen-max",
    ],
}

RECOMMENDED_MODELS: Dict[str, str] = {
    "dependency": "gpt-4o-mini",
    "build": "gpt-4o-mini",
    "error": "gpt-4o-mini",
    "coordinator": "gpt-4o-mini",
    "dockerfile_repair": "gpt-4o-mini",
}

# 兼容旧项目里对 MP_POOL_SIZE 的依赖
MP_POOL_SIZE = 10

def _default_model() -> str:
    for models in SUPPORTED_MODELS.values():
        if models:
            return models[0]
    return ""

def _mask_secret(value: str, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}...{value[-keep:]}"

@dataclass
class AgentConfig:
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    enabled: bool = True

    def as_dict(self) -> dict:
        return {
            "model": self.model,
            "api_key": self.api_key,
            "base_url": self.base_url,
            "enabled": self.enabled,
        }

def _build_default_agent_configs() -> Dict[str, AgentConfig]:
    base_model = _default_model()
    return {
        "dependency": AgentConfig(model=RECOMMENDED_MODELS.get("dependency", base_model)),
        "build": AgentConfig(model=RECOMMENDED_MODELS.get("build", base_model)),
        "error": AgentConfig(model=RECOMMENDED_MODELS.get("error", base_model)),
        "coordinator": AgentConfig(model=RECOMMENDED_MODELS.get("coordinator", base_model)),
        "dockerfile_repair": AgentConfig(model=RECOMMENDED_MODELS.get("dockerfile_repair", base_model)),
    }

@dataclass
class CXXCrafterConfig:
    # ===== 全局 API 配置 =====
    global_api_key: str = ""
    global_base_url: str = ""

    # ===== 运行配置 =====
    output_root: str = "./dockerfile_playground"
    logs_root: str = "./data/build_logs"
    base_image: str = "ubuntu:22.04"

    # ===== 运行开关 =====
    enable_build: bool = True
    verify_dockerfile: bool = True
    compatibility_mode: bool = True

    # ===== 缓存/构建优化 =====
    enable_build_cache: bool = True
    skip_rebuild_if_unchanged: bool = True
    use_buildkit: bool = True
    buildkit_progress_plain: bool = True

    # ===== 迭代修复 =====
    enable_iterative_repair: bool = True
    max_rounds: int = 2

    # ===== 运行时上下文（可选） =====
    project_path: str = ""
    source_root: str = ""
    original_project_path: str = ""
    project_output_dir: str = ""
    project_log_dir: str = ""

    # ===== 智能体配置 =====
    agent_configs: Dict[str, AgentConfig] = field(default_factory=_build_default_agent_configs)

    def __post_init__(self):
        if not isinstance(self.agent_configs, dict) or not self.agent_configs:
            self.agent_configs = _build_default_agent_configs()

    # -------------------------
    # 兼容旧字段名
    # -------------------------
    @property
    def enable_verification(self) -> bool:
        return self.verify_dockerfile

    @enable_verification.setter
    def enable_verification(self, value: bool):
        self.verify_dockerfile = bool(value)

    @property
    def logs_dir(self) -> str:
        return self.logs_root

    @logs_dir.setter
    def logs_dir(self, value: str):
        self.logs_root = str(value)

    @property
    def output_dir(self) -> str:
        return self.output_root

    @output_dir.setter
    def output_dir(self, value: str):
        self.output_root = str(value)

    @property
    def dockerfile_repair_config(self) -> Optional[AgentConfig]:
        return self.agent_configs.get("dockerfile_repair")

    @dockerfile_repair_config.setter
    def dockerfile_repair_config(self, cfg: AgentConfig):
        self.agent_configs["dockerfile_repair"] = cfg

    # -------------------------
    # 配置修改方法
    # -------------------------
    def set_global_api_key(self, api_key: str):
        self.global_api_key = (api_key or "").strip()

    def set_global_base_url(self, base_url: str):
        self.global_base_url = (base_url or "").strip()

    def set_agent_config(
        self,
        agent_type: str,
        model: Optional[str] = None,
        api_key: str = "",
        base_url: str = "",
        enabled: bool = True,
    ) -> bool:
        if not agent_type:
            return False

        if agent_type not in self.agent_configs:
            self.agent_configs[agent_type] = AgentConfig()

        cfg = self.agent_configs[agent_type]
        cfg.model = (model or cfg.model or _default_model()).strip()
        cfg.api_key = (api_key or "").strip()
        cfg.base_url = (base_url or "").strip()
        cfg.enabled = bool(enabled)
        return True

    def set_dockerfile_repair_config(
        self,
        model: Optional[str] = None,
        api_key: str = "",
        base_url: str = "",
        enabled: bool = True,
    ) -> bool:
        return self.set_agent_config(
            agent_type="dockerfile_repair",
            model=model,
            api_key=api_key,
            base_url=base_url,
            enabled=enabled,
        )

    def reset_to_recommended(self):
        self.global_api_key = ""
        self.global_base_url = ""
        self.output_root = "./dockerfile_playground"
        self.logs_root = "./data/build_logs"
        self.base_image = "ubuntu:22.04"
        self.enable_build = True
        self.verify_dockerfile = True
        self.compatibility_mode = True
        self.enable_build_cache = True
        self.skip_rebuild_if_unchanged = True
        self.use_buildkit = True
        self.buildkit_progress_plain = True
        self.enable_iterative_repair = True
        self.max_rounds = 2
        self.agent_configs = _build_default_agent_configs()

    # -------------------------
    # 信息展示
    # -------------------------
    def get_config_summary(self) -> str:
        lines = [
            "================= CXXCrafter 配置摘要 =================",
            f"输出目录          : {self.output_root}",
            f"日志目录          : {self.logs_root}",
            f"基础镜像          : {self.base_image}",
            f"启用构建          : {self.enable_build}",
            f"验证 Dockerfile   : {self.verify_dockerfile}",
            f"最小依赖模式      : {not self.compatibility_mode}",
            f"BuildKit          : {self.use_buildkit}",
            f"构建缓存          : {self.enable_build_cache}",
            f"全局 API Key      : {('已设置 ' + _mask_secret(self.global_api_key)) if self.global_api_key else '未设置'}",
            f"全局 Base URL     : {self.global_base_url or '未设置'}",
            "",
            "智能体配置：",
        ]

        for name, cfg in self.agent_configs.items():
            lines.append(
                f"  - {name}: model={cfg.model or 'default'}, "
                f"api_key={('已设置 ' + _mask_secret(cfg.api_key)) if cfg.api_key else '未设置'}, "
                f"base_url={cfg.base_url or '未设置'}, "
                f"enabled={cfg.enabled}"
            )

        lines.append("=====================================================")
        return "\n".join(lines)

__all__ = [
    "SUPPORTED_MODELS",
    "RECOMMENDED_MODELS",
    "AgentConfig",
    "CXXCrafterConfig",
    "MP_POOL_SIZE",
]