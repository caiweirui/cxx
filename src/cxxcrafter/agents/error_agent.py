from .base_agent import BaseAgent
from typing import Dict, Any
from cxxcrafter.config import CXXCrafterConfig
from cxxcrafter.rag import Retriever

class ErrorAgent(BaseAgent):
    def __init__(self, config: CXXCrafterConfig, retriever: Retriever = None):
        super().__init__("ErrorAgent", config)
        self.retriever = retriever
        self.system_prompt = """
        你是专业的C/C++构建错误诊断专家。
        任务：分析构建错误日志，定位根因，生成修复方案。
        输出格式：JSON格式，包含error_type, root_cause, fix_commands。
        """

    def observe(self, context: Dict[str, Any]):
        self.error_log = context.get("error_log", "")
        self.build_context = context.get("build_context", "")

    def think(self) -> str:
        # 如果有RAG检索器，先检索历史解决方案
        rag_context = ""
        if self.retriever:
            rag_context = self.retriever.format_prompt(self.error_log)
        
        prompt = f"""
        分析以下C/C++构建错误：
        错误日志：{self.error_log[:3000]}
        构建上下文：{self.build_context}
        历史解决方案参考：{rag_context}
        
        请诊断：
        1. 错误类型
        2. 根本原因
        3. 具体的修复命令
        
        只返回JSON。
        """
        return self._call_llm(prompt, self.system_prompt)

    def act(self) -> Dict[str, Any]:
        result = self.think()
        return {
            "agent": self.agent_name,
            "model": self.model,
            "diagnosis": result,
            "status": "success"
        }