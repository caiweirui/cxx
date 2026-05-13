from __future__ import annotations

import argparse
import inspect
import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, Optional, Type, TypeVar

from cxxcrafter.agents.build_agent import BuildAgent
from cxxcrafter.agents.coordinator import CXXCrafterCoordinator
from cxxcrafter.agents.dependency_agent import DependencyAgent
from cxxcrafter.agents.dockerfile_repair_agent import DockerfileRepairAgent
from cxxcrafter.agents.error_agent import ErrorAgent
from cxxcrafter.llm.bot import GPTBot
from cxxcrafter.execution.executor import DockerExecutor
from cxxcrafter.generation_module.dockerfile_generator import DockerfileGenerator

try:
    from cxxcrafter.rag.rag_service import RAGService
except Exception:
    RAGService = None  # type: ignore

T = TypeVar("T")

def _env_first(*names: str, default: Optional[str] = None) -> Optional[str]:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default

def _normalize_provider_mode(value: Optional[str]) -> str:
    v = (value or "").strip().lower()
    if not v:
        return "auto"
    if v in {"auto", "openai", "claude", "az"}:
        return v
    # 允许自定义分组名
    return v

def _instantiate(cls: Type[T], **kwargs: Any) -> T:
    """
    尽量兼容不同构造函数签名：
    - 先按签名过滤
    - 若不匹配，则退回无参构造
    """
    try:
        sig = inspect.signature(cls)
        params = sig.parameters
        has_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
        if has_kwargs:
            return cls(**kwargs)  # type: ignore[misc]

        filtered = {k: v for k, v in kwargs.items() if k in params}
        try:
            return cls(**filtered)  # type: ignore[misc]
        except TypeError:
            return cls()  # type: ignore[misc]
    except Exception:
        return cls()  # type: ignore[misc]

@dataclass
class AgentRuntimeConfig:
    """
    单个智能体的运行配置：
    - use_separate_config=False：继承全局配置
    - use_separate_config=True ：使用自己的 model/api_key/base_url
    """
    use_separate_config: bool = False
    model_name: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None

@dataclass
class CXXCrafterConfig:
    bot: Any = None
    model_name: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    provider_mode: str = "auto"
    temperature: float = 0.2

    max_repair_rounds: int = 2
    output_dir: str = "./output"
    log_dir: str = "./logs"
    failure_log_path: Optional[str] = "./logs/all_failed_builds.log"

    enable_build: bool = True
    enable_verification: bool = True
    generate_only: bool = False
    use_cache: bool = True

    image_tag: Optional[str] = None
    build_timeout_seconds: Optional[float] = 1800
    verify_timeout_seconds: Optional[float] = 600
    project_timeout_seconds: Optional[float] = None

    enable_rag: bool = True
    rag_service: Any = None
    verification_judge: Any = None
    dockerfile_generator_factory: Any = None

    use_buildkit: bool = True
    buildkit_progress: str = "plain"
    default_base_image: str = "ubuntu:24.04"

    cache_dir: str = "./data/cache"
    llm_usage_log_path: str = "./logs/llm_usage.log"
    llm_trace_log_path: str = "./logs/llm_trace.jsonl"
    agent_trace_log_path: str = "./logs/agent_trace.jsonl"

    # 四个核心 agent 的独立配置
    dependency_agent: AgentRuntimeConfig = field(default_factory=AgentRuntimeConfig)
    build_agent: AgentRuntimeConfig = field(default_factory=AgentRuntimeConfig)
    error_agent: AgentRuntimeConfig = field(default_factory=AgentRuntimeConfig)
    repair_agent: AgentRuntimeConfig = field(default_factory=AgentRuntimeConfig)

@dataclass
class _GPTBotConfigShim:
    """
    最小 GPTBot 配置适配器：
    - 兼容 GPTBot(config=...) 的读取方式
    - 提供 get_agent_credentials("coordinator")
    - 提供 cache_dir / llm_usage_log_path / llm_trace_log_path
    """
    global_api_key: Optional[str]
    global_base_url: Optional[str]
    global_model: Optional[str]
    global_provider_mode: str
    cache_dir: str
    llm_usage_log_path: str
    llm_trace_log_path: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    provider_mode: Optional[str] = None

    def get_agent_credentials(self, _agent_name: str) -> Dict[str, Any]:
        return {
            "api_key": self.api_key or self.global_api_key,
            "base_url": self.base_url or self.global_base_url,
            "model": self.model or self.global_model,
            "provider_mode": self.provider_mode or self.global_provider_mode,
        }

class CXXCrafterCLI:
    """
    CLI / GUI 统一入口：
    - GUI 会从这里导入 CXXCrafterCLI
    - 负责实例化各个 agent + coordinator
    - 支持单项目处理
    """

    def __init__(self, config: Optional[CXXCrafterConfig] = None, **overrides: Any) -> None:
        base = config or CXXCrafterConfig()

        # 只采纳 dataclass 中存在的字段
        filtered = {k: v for k, v in overrides.items() if hasattr(base, k)}
        self.config = self._normalize_config(replace(base, **filtered))
        self._rag_service = self._build_rag_service(self.config)

    @staticmethod
    def _normalize_config(config: CXXCrafterConfig) -> CXXCrafterConfig:
        api_key = config.api_key or _env_first("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY")
        base_url = config.base_url or _env_first("OPENAI_BASE_URL", "BASE_URL")
        model_name = config.model_name or _env_first("OPENAI_MODEL", "MODEL_NAME")
        provider_mode = _normalize_provider_mode(
            config.provider_mode or _env_first("LLM_PROVIDER_MODE", default="auto")
        )

        return replace(
            config,
            api_key=api_key,
            base_url=base_url,
            model_name=model_name,
            provider_mode=provider_mode,
            temperature=float(config.temperature if config.temperature is not None else 0.2),
            max_repair_rounds=max(0, int(config.max_repair_rounds)),
            enable_build=bool(config.enable_build),
            enable_verification=bool(config.enable_verification),
            generate_only=bool(config.generate_only),
            use_cache=bool(config.use_cache),
            enable_rag=bool(config.enable_rag),
            use_buildkit=bool(config.use_buildkit),
            buildkit_progress=str(config.buildkit_progress or "plain"),
            default_base_image=str(config.default_base_image or "ubuntu:24.04"),
            failure_log_path=str(config.failure_log_path) if config.failure_log_path else None,
            cache_dir=str(config.cache_dir or "./data/cache"),
            llm_usage_log_path=str(config.llm_usage_log_path or "./logs/llm_usage.log"),
            llm_trace_log_path=str(config.llm_trace_log_path or "./logs/llm_trace.jsonl"),
            agent_trace_log_path=str(config.agent_trace_log_path or "./logs/agent_trace.jsonl"),
        )

    def _build_rag_service(self, config: CXXCrafterConfig) -> Any:
        if config.rag_service is not None:
            return config.rag_service

        if not config.enable_rag or RAGService is None:
            return None

        try:
            return RAGService()
        except Exception:
            pass

        try:
            return RAGService(base_dir=str(Path(config.output_dir).resolve() / "rag"))
        except Exception:
            return None

    def _resolve_config(self, **overrides: Any) -> CXXCrafterConfig:
        base = self.config
        filtered = {k: v for k, v in overrides.items() if hasattr(base, k) and v is not None}
        if filtered:
            base = replace(base, **filtered)
        return self._normalize_config(base)

    def _build_dockerfile_generator_factory(self, cfg: CXXCrafterConfig):
        if cfg.dockerfile_generator_factory is not None:
            return cfg.dockerfile_generator_factory

        def factory(project_path: str):
            try:
                return DockerfileGenerator(project_path, default_base_image=cfg.default_base_image)
            except TypeError:
                pass
            try:
                return DockerfileGenerator(project_path, base_image=cfg.default_base_image)
            except TypeError:
                pass
            return DockerfileGenerator(project_path)

        return factory

    def _resolve_agent_runtime(self, agent_cfg: AgentRuntimeConfig, global_cfg: CXXCrafterConfig) -> AgentRuntimeConfig:
        """
        统一解析某个 agent 的最终配置：
        - 不使用独立配置：完全继承全局
        - 使用独立配置：局部字段为空时仍回退到全局
        """
        if not agent_cfg or not agent_cfg.use_separate_config:
            return AgentRuntimeConfig(
                use_separate_config=False,
                model_name=global_cfg.model_name,
                api_key=global_cfg.api_key,
                base_url=global_cfg.base_url,
            )

        return AgentRuntimeConfig(
            use_separate_config=True,
            model_name=agent_cfg.model_name or global_cfg.model_name,
            api_key=agent_cfg.api_key or global_cfg.api_key,
            base_url=agent_cfg.base_url or global_cfg.base_url,
        )

    def _build_gptbot(self, cfg: CXXCrafterConfig, agent_cfg: AgentRuntimeConfig) -> GPTBot:
        """
        给某个 Agent 构造一个独立 GPTBot 实例：
        - 共享同一套 OpenAI 账号配置
        - 但每个 Agent 保留独立 messages / cache / 日志路径
        """
        effective = self._resolve_agent_runtime(agent_cfg, cfg)

        shim = _GPTBotConfigShim(
            global_api_key=cfg.api_key,
            global_base_url=cfg.base_url,
            global_model=cfg.model_name,
            global_provider_mode=cfg.provider_mode,
            cache_dir=cfg.cache_dir,
            llm_usage_log_path=cfg.llm_usage_log_path,
            llm_trace_log_path=cfg.llm_trace_log_path,
            api_key=effective.api_key,
            base_url=effective.base_url,
            model=effective.model_name,
            provider_mode=cfg.provider_mode,
        )

        return GPTBot(
            model=effective.model_name or cfg.model_name or "gpt-4o",
            config=shim,
        )

    def _prime_agent_runtime(self, agent: Any, cfg: CXXCrafterConfig) -> None:
        """
        给 BaseAgent 注入运行时上下文：
        - use_cache 真正传到 LLM 调用
        - 统一写日志路径
        """
        if agent is None:
            return

        try:
            if hasattr(agent, "set_runtime_context") and callable(getattr(agent, "set_runtime_context")):
                agent.set_runtime_context(
                    use_cache=cfg.use_cache,
                    usage_log_path=cfg.llm_usage_log_path,
                    trace_log_path=cfg.agent_trace_log_path,
                )
        except Exception:
            pass

    def _agent_kwargs(self, cfg: CXXCrafterConfig, agent_cfg: AgentRuntimeConfig, rag_service: Any, bot: Any) -> Dict[str, Any]:
        effective = self._resolve_agent_runtime(agent_cfg, cfg)
        return {
            "bot": bot,
            "model_name": effective.model_name,
            "api_key": effective.api_key,
            "base_url": effective.base_url,
            "temperature": cfg.temperature,
            "rag_service": rag_service,
        }

    def create_agents(self, config: Optional[CXXCrafterConfig] = None):
        cfg = config or self.config
        rag_service = cfg.rag_service if cfg.rag_service is not None else self._rag_service

        dependency_bot = cfg.bot if cfg.bot is not None else self._build_gptbot(cfg, cfg.dependency_agent)
        build_bot = cfg.bot if cfg.bot is not None else self._build_gptbot(cfg, cfg.build_agent)
        error_bot = cfg.bot if cfg.bot is not None else self._build_gptbot(cfg, cfg.error_agent)
        repair_bot = cfg.bot if cfg.bot is not None else self._build_gptbot(cfg, cfg.repair_agent)

        dependency_agent = _instantiate(
            DependencyAgent,
            **self._agent_kwargs(cfg, cfg.dependency_agent, rag_service, dependency_bot),
        )
        build_agent = _instantiate(
            BuildAgent,
            **self._agent_kwargs(cfg, cfg.build_agent, rag_service, build_bot),
        )
        error_agent = _instantiate(
            ErrorAgent,
            **self._agent_kwargs(cfg, cfg.error_agent, rag_service, error_bot),
        )
        repair_agent = _instantiate(
            DockerfileRepairAgent,
            **self._agent_kwargs(cfg, cfg.repair_agent, rag_service, repair_bot),
        )

        for agent in (dependency_agent, build_agent, error_agent, repair_agent):
            self._prime_agent_runtime(agent, cfg)

        docker_executor = DockerExecutor(
            use_buildkit=cfg.use_buildkit,
            buildkit_progress=cfg.buildkit_progress,
        )

        return {
            "dependency_agent": dependency_agent,
            "build_agent": build_agent,
            "error_agent": error_agent,
            "repair_agent": repair_agent,
            "docker_executor": docker_executor,
            "rag_service": rag_service,
        }

    def create_coordinator(self, config: Optional[CXXCrafterConfig] = None) -> CXXCrafterCoordinator:
        cfg = config or self.config
        agents = self.create_agents(cfg)

        return CXXCrafterCoordinator(
            dependency_agent=agents["dependency_agent"],
            build_agent=agents["build_agent"],
            error_agent=agents["error_agent"],
            repair_agent=agents["repair_agent"],
            docker_executor=agents["docker_executor"],
            dockerfile_generator_factory=self._build_dockerfile_generator_factory(cfg),
            max_repair_rounds=cfg.max_repair_rounds,
            rag_service=agents["rag_service"],
            verification_judge=cfg.verification_judge,
        )

    def process_project(
        self,
        project_path: str,
        output_dir: Optional[str] = None,
        log_dir: Optional[str] = None,
        failure_log_path: Optional[str] = None,
        **overrides: Any,
    ) -> Dict[str, Any]:
        cfg = self._resolve_config(
            **{
                **overrides,
                "output_dir": output_dir if output_dir is not None else self.config.output_dir,
                "log_dir": log_dir if log_dir is not None else self.config.log_dir,
                "failure_log_path": failure_log_path if failure_log_path is not None else self.config.failure_log_path,
            }
        )

        output_dir_path = str(Path(cfg.output_dir).expanduser().resolve())
        log_dir_path = str(Path(cfg.log_dir).expanduser().resolve())
        failure_log_path_path = str(Path(cfg.failure_log_path).expanduser().resolve()) if cfg.failure_log_path else None

        Path(output_dir_path).mkdir(parents=True, exist_ok=True)
        Path(log_dir_path).mkdir(parents=True, exist_ok=True)
        if failure_log_path_path:
            Path(failure_log_path_path).parent.mkdir(parents=True, exist_ok=True)

        coordinator = self.create_coordinator(cfg)
        return coordinator.process_project(
            project_path=project_path,
            output_dir=output_dir_path,
            log_dir=log_dir_path,
            enable_build=cfg.enable_build,
            enable_verification=cfg.enable_verification,
            generate_only=cfg.generate_only,
            use_cache=cfg.use_cache,
            image_tag=cfg.image_tag,
            build_timeout_seconds=cfg.build_timeout_seconds,
            verify_timeout_seconds=cfg.verify_timeout_seconds,
            project_timeout_seconds=cfg.project_timeout_seconds,
            failure_log_path=failure_log_path_path,
        )

    # 兼容旧调用名
    run_project = process_project
    process_single_project = process_project
    execute_project = process_project

    def build_arg_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            prog="cxxcrafter",
            description="CXXCrafter - 自动生成并验证 Dockerfile 的 CLI",
            formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        )
        parser.add_argument("project_path", help="项目路径")
        parser.add_argument("--output-dir", default=self.config.output_dir, help="输出目录")
        parser.add_argument("--log-dir", default=self.config.log_dir, help="日志目录")
        parser.add_argument("--failure-log", default=self.config.failure_log_path, help="所有失败项目统一追加的日志文件")
        parser.add_argument("--image-tag", default=self.config.image_tag, help="Docker 镜像标签")
        parser.add_argument("--model-name", default=self.config.model_name, help="模型名称")
        parser.add_argument("--api-key", default=self.config.api_key, help="API Key")
        parser.add_argument("--base-url", default=self.config.base_url, help="API Base URL")
        parser.add_argument("--provider-mode", default=self.config.provider_mode, help="分组/Provider 模式（auto/openai/claude/az/自定义）")
        parser.add_argument("--temperature", type=float, default=self.config.temperature, help="模型温度")
        parser.add_argument("--max-repair-rounds", type=int, default=self.config.max_repair_rounds, help="最大修复轮次")
        parser.add_argument("--build-timeout", type=float, default=self.config.build_timeout_seconds, help="构建超时秒数")
        parser.add_argument("--verify-timeout", type=float, default=self.config.verify_timeout_seconds, help="验证超时秒数")
        parser.add_argument("--project-timeout", type=float, default=self.config.project_timeout_seconds, help="项目总超时秒数")
        parser.add_argument("--default-base-image", default=self.config.default_base_image, help="默认基础镜像")
        parser.add_argument("--buildkit-progress", default=self.config.buildkit_progress, help="BuildKit 输出模式")

        # 全局控制项
        parser.add_argument("--disable-build", action="store_true", help="禁用构建")
        parser.add_argument("--disable-verification", action="store_true", help="禁用验证")
        parser.add_argument("--generate-only", action="store_true", help="只生成，不构建")
        parser.add_argument("--no-cache", action="store_true", help="关闭缓存")
        parser.add_argument("--disable-rag", action="store_true", help="关闭 RAG")
        parser.add_argument("--no-buildkit", action="store_true", help="关闭 BuildKit")
        return parser

    def run(self, argv: Optional[list[str]] = None) -> int:
        parser = self.build_arg_parser()
        args = parser.parse_args(argv)

        cfg = self._resolve_config(
            output_dir=args.output_dir,
            log_dir=args.log_dir,
            failure_log_path=args.failure_log,
            image_tag=args.image_tag,
            model_name=args.model_name,
            api_key=args.api_key,
            base_url=args.base_url,
            provider_mode=_normalize_provider_mode(args.provider_mode),
            temperature=args.temperature,
            max_repair_rounds=args.max_repair_rounds,
            build_timeout_seconds=args.build_timeout,
            verify_timeout_seconds=args.verify_timeout,
            project_timeout_seconds=args.project_timeout,
            enable_build=not args.disable_build,
            enable_verification=not args.disable_verification,
            generate_only=args.generate_only,
            use_cache=not args.no_cache,
            enable_rag=not args.disable_rag,
            use_buildkit=not args.no_buildkit,
            buildkit_progress=args.buildkit_progress,
            default_base_image=args.default_base_image,
        )

        summary = self.process_project(
            project_path=args.project_path,
            output_dir=cfg.output_dir,
            log_dir=cfg.log_dir,
            failure_log_path=cfg.failure_log_path,
            model_name=cfg.model_name,
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            provider_mode=cfg.provider_mode,
            temperature=cfg.temperature,
            max_repair_rounds=cfg.max_repair_rounds,
            enable_build=cfg.enable_build,
            enable_verification=cfg.enable_verification,
            generate_only=cfg.generate_only,
            use_cache=cfg.use_cache,
            image_tag=cfg.image_tag,
            build_timeout_seconds=cfg.build_timeout_seconds,
            verify_timeout_seconds=cfg.verify_timeout_seconds,
            project_timeout_seconds=cfg.project_timeout_seconds,
            enable_rag=cfg.enable_rag,
            rag_service=cfg.rag_service,
            verification_judge=cfg.verification_judge,
            dockerfile_generator_factory=cfg.dockerfile_generator_factory,
            use_buildkit=cfg.use_buildkit,
            buildkit_progress=cfg.buildkit_progress,
            default_base_image=cfg.default_base_image,
        )

        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary.get("success", False) else 1

def main(argv: Optional[list[str]] = None) -> int:
    cli = CXXCrafterCLI()
    return cli.run(argv)

if __name__ == "__main__":
    raise SystemExit(main())