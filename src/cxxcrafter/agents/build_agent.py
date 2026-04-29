from .base_agent import BaseAgent
from typing import Dict, Any
from cxxcrafter.config import CXXCrafterConfig

class BuildAgent(BaseAgent):
    def __init__(self, config: CXXCrafterConfig):
        super().__init__("BuildAgent", config)
        self.system_prompt = """
        你是专业的C/C++构建系统适配专家。
        任务：根据构建系统类型，生成准确的构建命令序列。
        支持的构建系统：CMake, Makefile, Autotools, Meson, Bazel。
        输出格式：JSON格式，包含commands列表。
        """

    def observe(self, context: Dict[str, Any]):
        self.build_system = context.get("build_system", "")
        self.dependencies = context.get("dependencies", "")

    def think(self) -> str:
        prompt = f"""
        为以下C/C++项目生成构建命令：
        构建系统：{self.build_system}
        依赖信息：{self.dependencies}
        
        请生成：
        1. 环境准备命令
        2. 配置命令
        3. 编译命令
        4. 安装命令（可选）
        
        只返回JSON。
        """
        return self._call_llm(prompt, self.system_prompt)

    def act(self) -> Dict[str, Any]:
        result = self.think()
        return {
            "agent": self.agent_name,
            "model": self.model,
            "commands": result,
            "status": "success"
        }