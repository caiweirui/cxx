from .base_agent import BaseAgent
from typing import Dict, Any
from cxxcrafter.config import CXXCrafterConfig

class DependencyAgent(BaseAgent):
    def __init__(self, config: CXXCrafterConfig):
        super().__init__("DependencyAgent", config)
        self.system_prompt = """
        你是专业的C/C++项目依赖解析专家。
        任务：分析项目的依赖关系，识别缺失依赖、版本约束、安装源。
        输出格式：JSON格式，包含dependencies列表，每个依赖有name, version_constraint, source。
        """

    def observe(self, context: Dict[str, Any]):
        self.project_path = context.get("project_path", "")
        self.build_system = context.get("build_system", "")
        self.docs = context.get("docs", "")

    def think(self) -> str:
        prompt = f"""
        分析以下C/C++项目的依赖：
        构建系统：{self.build_system}
        构建文档：{self.docs[:2000]}
        
        请识别：
        1. 必需的编译时依赖
        2. 版本约束
        3. 推荐的安装源（apt/vcpkg/源码等）
        
        只返回JSON。
        """
        return self._call_llm(prompt, self.system_prompt)

    def act(self) -> Dict[str, Any]:
        result = self.think()
        return {
            "agent": self.agent_name,
            "model": self.model,
            "dependencies": result,
            "status": "success"
        }