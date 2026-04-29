from .dependency_agent import DependencyAgent
from .build_agent import BuildAgent
from .error_agent import ErrorAgent
from typing import Dict, Any
from cxxcrafter.config import CXXCrafterConfig

class AgentCoordinator:
    def __init__(self, config: CXXCrafterConfig, retriever=None):
        self.config = config
        self.dependency_agent = DependencyAgent(config)
        self.build_agent = BuildAgent(config)
        self.error_agent = ErrorAgent(config, retriever)
        self.global_state = {}

    def run_build_pipeline(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """运行完整的构建流水线"""
        print("="*50)
        print("多智能体协作构建流水线启动")
        print("="*50)
        print(f"使用配置:\n{self.config.get_config_summary()}")
        
        # 阶段1：依赖解析
        print("\n[阶段1] 依赖解析智能体工作中...")
        print(f"  模型: {self.dependency_agent.model}")
        self.dependency_agent.observe(context)
        dep_result = self.dependency_agent.act()
        self.global_state["dependencies"] = dep_result
        
        # 阶段2：构建适配
        print("\n[阶段2] 构建适配智能体工作中...")
        print(f"  模型: {self.build_agent.model}")
        build_context = {**context, **dep_result}
        self.build_agent.observe(build_context)
        build_result = self.build_agent.act()
        self.global_state["build_commands"] = build_result
        
        # 阶段3：错误诊断（预留接口）
        print("\n[阶段3] 错误诊断智能体待命")
        print(f"  模型: {self.error_agent.model}")
        
        print("\n" + "="*50)
        print("多智能体协作完成")
        print("="*50)
        
        return self.global_state